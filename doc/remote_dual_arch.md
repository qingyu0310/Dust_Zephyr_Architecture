# 双遥控器架构设计

## 1. 概述

支持两个 UART 串口各自连接一个遥控器（如 DR16 + SBUS），自动探测协议，按优先级切换。用户只需管理遥控器开关，系统自动跟随有数据的串口。

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
        uint16_t            last_valid_ms   = 0;
        uint8_t             min_frame_size  = 0;
        uint8_t             max_frame_size  = 0;
        const RemoteEntry  *locked          = nullptr;
        Probe               probe           {};
        bool                ready           = false;   // 串口已初始化
    } detect_[2] {};
};
```

- `uart_[0]` — 高优先级串口
- `uart_[1]` — 低优先级串口
- `uart_idx_` — 当前激活的引用索引，所有代码统一用 `uart_[uart_idx_]` 和 `detect_[uart_idx_]`
- `detect_[i].ready` — 该串口是否已初始化（有数据可用）

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
超时 → 另一串口有数据 → 切换串口
超时 → 另一串口无数据 → 保持当前，发归零
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
            // 有数据 → ProcessChunk → Dispatch
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
                // 发归零数据
                pub_ = {};
                zbus_chan_pub(&pub_remote_to, &pub_, K_MSEC(1));

                // 尝试切换串口
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

### 4.1 串口切换

```cpp
void Remote::TrySwitchUart()
{
    uint8_t other = uart_idx_ ^ 1;

    // 另一种串口未初始化 or 无数据 → 不动
    if (!detect_[other].ready) return;
    if (!HasData(uart_[other])) return;

    // 切到另一个串口
    LOG_INF("switch to uart[%d]", other);
    uart_idx_ = other;
    frame_.frame_pos_ = 0;
}
```

`HasData()` 检查另一个串口的信号量或 FIFO，不做数据搬迁。

## 5. 初始探测

### 5.1 初始化

```cpp
bool Remote::Init(UartDma &uart_high, UartDma &uart_low)
{
    uart_[0] = &uart_high;
    uart_[1] = &uart_low;
    detect_[0].ready = true;
    detect_[1].ready = true;
    uart_idx_ = 0;

    InitRange();
    ResetDetect();
    ready_ = true;
    return true;
}
```

### 5.2 初始串口选择

Task 首次运行时，优先探测有数据的串口：

```cpp
// Task 第一次进入数据检查
if (!HasData(uart_[0]) && HasData(uart_[1])) {
    uart_idx_ = 1;  // 高优先级无数据，切到低优先级
}
```

### 5.3 探测流程（各串口独立）

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
            // 解码成功
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

**关键：** 锁定状态不主动探查另一个串口。只有当前串口超时（无数据）时才去检查另一个。

## 7. 数据传输流

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

## 8. 边界情况

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

## 9. 关键规则

1. **串口优先级只在初始选择时生效。** 锁定后不主动切回高优先级
2. **锁定状态下不探查另一个串口。** 只有当前串口超时才检查
3. **两个串口各自维护独立 detect_** ，探测进度互不干扰
4. **用户视角**：开哪个遥控器，系统就锁哪个。关掉当前 → 自动切到另一个
5. **不需要在运行时关心"哪个是主哪个是备"**——代码透明，索引切换

## 10. 与当前架构的差异

| 当前（单串口） | 双串口 |
|------|------|
| `UartDma *uart_` | `UartDma *uart_[2]` + `uart_idx_` |
| `detect_ {}` | `detect_[2] {}`（各串口独立） |
| 锁定后只有一个串口 | 锁定后当前串口超时才检查另一个 |
| 超时 → ResetDetect | 超时 → 不 ResetDetect，尝试切串口 |
| 没有串口切换 | `TrySwitchUart()` 切换索引 |
