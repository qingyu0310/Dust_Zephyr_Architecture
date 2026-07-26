# 双遥控器架构设计

## 1. 概述

支持两个 UART 串口各自连接一个遥控器（如 DR16 + SBUS），自动探测协议，按优先级切换。用户只需管理遥控器开关，系统自动跟随有数据的串口。

### 1.1 核心设计原则

- **热路径必须笔直**：锁定解码路径无分支——`Read → Decode → Publish`，不掺入切换逻辑
- **冷路径隔离**：串口切换、协议探测只在初始化或超时等罕见路径中执行
- **detect_[2] 是状态副本，不是两个解析线程**：两个 UART 各自保存探测状态，但同一时间只有一个串口被当前线程解析
- **Priority 描述的是 UART 输入源优先级**：协议本身的探测仍由当前 UART 内部按注册表顺序执行

## 2. 数据结构

```cpp
class Remote final
{
    // ...

    UartDma *uart_[2] {};       // [0]=高优先级串口, [1]=低优先级串口
    uint8_t  uart_idx_ = 0;     // 当前激活的串口索引

    struct Detect {
        DetectState         state           = DetectState::Detecting;
        uint8_t             fail_count      = 0;
        uint32_t            last_valid_ms   = 0;   // 必须用 uint32_t
        uint8_t             min_frame_size  = 0;
        uint8_t             max_frame_size  = 0;
        const RemoteEntry  *locked          = nullptr;
        Probe               probe           {};
        bool                ready           = false;   // 串口是否已初始化
    } detect_[2] {};
};
```

- `uart_[0]` — 高优先级串口
- `uart_[1]` — 低优先级串口
- `uart_idx_` — 当前激活的引用索引，所有代码统一用 `uart_[uart_idx_]` 和 `detect_[uart_idx_]`
- `detect_[i].ready` — 该串口初始化结果，来自各自的 `UartDma::Init()`，失败直接跳过
- `last_valid_ms` 必须用 `uint32_t`，`uint16_t` 存不下 `k_uptime_get_32()`

## 3. 多层次状态机

### 3.1 串口级状态

每个串口独立维护自身的探测/锁定状态。

```
uart_[0] (High):  Detecting ↔ Locked
uart_[1] (Low):   Detecting ↔ Locked
```

### 3.2 Remote 级状态

`uart_idx_` 决定当前操作哪个串口。

```
无数据 → 有数据 → 选择有数据的串口探测
有数据 → 锁定 → 固定串口解码
锁定 → 超时 → 检查另一串口有无数据
超时 → 另一串口有数据 → 切换串口（完整事务）
超时 → 另一串口无数据 → 保持当前，周期性发归零
```

## 4. 任务循环

```cpp
void Remote::Task()
{
    for (;;)
    {
        // 检查当前串口是否有数据
        if (k_sem_take(&uart_[uart_idx_]->sem_, K_MSEC(50)) == 0)
        {
            uint8_t tmp[32];
            while (true)
            {
                uint16_t n = uart_[uart_idx_]->Read(tmp, sizeof(tmp));
                if (n == 0) break;
                ProcessChunk(tmp, n);
            }
            continue;
        }

        // 当前串口无数据 → 检查是否超时
        uint32_t now = k_uptime_get_32();
        auto &det = detect_[uart_idx_];

        if (det.locked != nullptr)
        {
            if (now - det.last_valid_ms >= kRemoteTimeoutMs)
            {
                if (det.last_valid_ms != 0) {
                    LOG_ERR("lost %s", det.locked->name);
                    det.last_valid_ms = 0;
                }
                // 周期性发归零（避免高频重复发布）
                pub_ = {};
                zbus_chan_pub(&pub_remote_to, &pub_, K_MSEC(1));

                TrySwitchUart();
            }
        }
        else
        {
            // 探测状态也无数据 → 看另一个串口
            TrySwitchUart();
        }
    }
}
```

### 4.1 串口切换 — 完整事务

切换是一个原子操作，必须保证一致性：

```cpp
void Remote::TrySwitchUart()
{
    uint8_t other = uart_idx_ ^ 1;

    // 另一串口未初始化 → 跳过
    if (!detect_[other].ready) return;

    // 切换冷却：防止两个串口都有噪声时来回跳
    if (!switch_cooldown_elapsed()) return;

    if (!HasValidData(uart_[other])) return;

    // === 切换事务开始 ===
    uint8_t prev = uart_idx_;
    uart_idx_ = other;

    // 清空共享帧缓冲区
    frame_.frame_pos_ = 0;

    // 丢弃目标 UART 的残留旧数据，避免切过去先解析到延迟的旧帧
    FlushUartFifo(uart_[other]);

    // 使用目标 UART 自己的探测状态继续（detect_[other] 已独立维护）
    // 不重置 detect_[other] 的 probe/hits/retry

    // === 切换事务完成 ===

    LOG_INF("switch uart[%d] → [%d]", prev, other);
}
```

**关键规则：**
- 切换时先保存旧索引，再设新索引，最后清理，不允许中间状态
- `FlushUartFifo()` 丢弃目标 UART 软件 FIFO 中的历史缓存
- 切换后 frame_ 是空的，从下一次新接收帧开始解析
- 切换后不重置 `detect_[other]` 的探测状态（已有探测进度保留）
- 防止故障状态来回切换：用冷却时间避免两个串口都有噪声时高频跳转
- `HasValidData()` 检查信号量或 FIFO 非空——只说明"有数据"，不说明"是有效遥控器"

## 5. 初始探测

### 5.1 初始化

```cpp
bool Remote::Init(UartDma &uart_high, UartDma &uart_low)
{
    uart_[0] = &uart_high;
    uart_[1] = &uart_low;

    // 各自独立保存初始化结果
    if (!uart_high.IsReady()) {
        LOG_WRN("uart_high not ready");
        detect_[0].ready = false;
    } else {
        detect_[0].ready = true;
    }
    if (!uart_low.IsReady()) {
        LOG_WRN("uart_low not ready");
        detect_[1].ready = false;
    } else {
        detect_[1].ready = true;
    }

    // 一个串口都不能用 → 失败
    if (!detect_[0].ready && !detect_[1].ready) return false;

    // 初始串口选择：高优先级优先，无数据才切到低优先级
    uart_idx_ = (detect_[0].ready && HasData(uart_[0])) ? 0
              : (detect_[1].ready && HasData(uart_[1])) ? 1
              : detect_[0].ready ? 0 : 1;

    InitRange();
    ResetDetect();
    ready_ = true;
    return true;
}
```

- `detect_[i].ready` 来自各自的初始化结果，不能假定另一个也能用
- 初始化失败的 UART 直接跳过，不参与切换

### 5.2 探测流程（各串口独立）

```
选择第一个协议 → SwitchProto(uart, e)
        │
   收到完整一帧
        │
   ┌────┴────┐
   │ Validate │
   └────┬────┘
  通过 ←┴→ 失败
   │         │
 hits++   retry++
   │         │
   ├ hits >= need_hits ──→ Locked
   │
   ├ retry >= need_hits ──→ 切下一协议
   │
   └ 不够 → 继续驻留
```

每个串口的 `detect_[i].probe` 独立维护 hit/retry，互不干扰。

## 6. 锁定后解码

```cpp
void Remote::HandleLocked()
{
    auto &det = detect_[uart_idx_];
    if (det.locked == nullptr) { ResetDetect(uart_idx_); return; }

    const auto *entry = det.locked;

    while (frame_.frame_pos_ >= entry->frame_size)
    {
        if (entry->protocol->Decode(frame_.frame_buf_, entry->frame_size, pub_))
        {
            if (det.last_valid_ms == 0) {
                LOG_INF("reconnect %s", entry->name);
            }
            det.last_valid_ms = k_uptime_get_32();
            det.fail_count = 0;
            Consume(entry->frame_size);
        } else {
            det.fail_count++;
            Consume(entry->frame_size);
        }

        if (det.fail_count >= kUnlockFailLimit) {
            ResetDetect(uart_idx_);
            break;
        }
    }
}
```

**关键：** 锁定状态不探查另一个串口。热路径笔直——只有当前串口超时才进入冷路径处理切换。

## 7. 非活动 UART 数据处理

非活动 UART 的 DMA 仍在持续接收，软件 FIFO 可能积累大量旧帧。切换过去时如果不处理历史缓存，可能解析到延迟很久的旧数据。

**处理策略：**
1. 切换时 `FlushUartFifo()` 丢弃目标 UART 的软件 FIFO 全部内容
2. 清空共享 `frame_`（`frame_pos_ = 0`）
3. 切换后从**下一次新接收帧**开始探测/解码
4. 永远不会在切换瞬间去解析历史缓存

## 8. 超时与归零语义

| 规则 | 说明 |
|------|------|
| 何时发归零 | 锁定状态下 `last_valid_ms` 超时 |
| 发布频率 | 每 50ms 一次（随着 Task 循环自然触发） |
| 重复发布 | 锁定超时期间周期性发零，避免高频无意义重复 |
| 数据类型 | `pub_ = {}` 全零，包括 `version++` 归零 |
| `last_valid_ms` | `uint32_t`，匹配 `k_uptime_get_32()` 返回类型 |
| 回到探测 | 切换串口后由新串口的 Locked/Detecting 决定 |

## 9. 数据传输流

```
UART-A (High)     UART-B (Low)
    │                  │
    │ 空闲中断         │
    ▼                  │
 k_sem_give            │
    │                  │ 空闲中断
    │                  ▼
    │               k_sem_give
    │                  │
    └────────┬─────────┘
             │ Task 轮询当前 uart_idx_
             ▼
       uart_[idx_]->Read()
             │
             ▼
       ProcessChunk → frame_buf_
             │ Dispatch()
             ▼
       HandleDetecting / HandleLocked
             │
             ▼
       zbus_chan_pub → topic::remote_to::Message
```

## 10. 边界情况

| 场景 | 行为 |
|------|------|
| 仅 UART-A 有遥控器 | 探测 A → 锁定 A → 解码 A |
| 仅 UART-B 有遥控器 | A 无数据 → 切到 B → 探测 B → 锁定 B → 解码 B |
| 两个都有遥控器 | A 优先 → 探测 A → 锁定 A → 解码 A。B 的数据被忽略 |
| A 锁定后关机 | A 超时 → 切到 B → 探测 B → 锁定 B |
| B 锁定后，A 开机 | 保持 B，不切。除非 B 也超时 |
| 两个都关机 | 当前串口超时 → 发归零 → 另一串口也没数据 → 发归零，等 |
| 两个都开机，A 先锁，B 关 | 保持 A，正常解码。B 无关 |
| A 锁定后 A 的 UART 断线 | A 超时 → 切到 B → B 有数据则探测，无则等 |
| 两个 UART 都有噪声干扰 | 切换冷却时间防止高频来回跳，验证成功后才认为接管 |
| 一个 UART 初始化失败 | `ready = false`，直接跳过，不参与切换 |
| 切换到有旧缓存的 UART | `FlushUartFifo()` + `frame_pos_ = 0`，从新帧开始 |

## 11. 关键规则

1. **串口优先级只在初始选择时生效。** 锁定后不主动切回高优先级
2. **锁定状态下不探查另一个串口。** 只有当前串口超时才检查
3. **两个串口各自维护独立 detect_** ，探测进度互不干扰
4. **用户视角**：开哪个遥控器，系统就锁哪个。关掉当前 → 自动切到另一个
5. **不需要在运行时关心"哪个是主哪个是备"**——代码透明，索引切换
6. **切换是一个完整事务**：先保存旧索引 → 设新索引 → 清 frame_ → 清残留数据 → 用新 detect_ 状态
7. **detect_[2] 是状态副本，不是两个解析线程**——同一时间只有一个串口被解析
8. **Priority 描述的是 UART 输入源优先级**，协议自身仍由注册表顺序探测

## 12. 与当前架构的差异

| 当前（单串口） | 双串口 |
|------|------|
| `UartDma *uart_` | `UartDma *uart_[2]` + `uart_idx_` |
| `detect_ {}` | `detect_[2] {}`（各串口独立） |
| `uint16_t last_valid_ms` | `uint32_t last_valid_ms` |
| 锁定后只有一个串口 | 锁定后当前串口超时才检查另一个 |
| 超时 → ResetDetect | 超时 → 不 ResetDetect，尝试切串口 |
| 没有串口切换 | `TrySwitchUart()` 完整事务切换 |
| 无残留数据处理 | `FlushUartFifo()` 丢弃历史缓存 |
| 无故障保护 | 切换冷却时间 + 初始化失败跳过 |
