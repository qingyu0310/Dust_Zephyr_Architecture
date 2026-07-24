# IMU 注册宏与自动探测 — 设计规划

## 1. 动机

当前 IMU 设备的选择通过编译期 `CONFIG_MOD_DEV_IMU_BMI088` / `CONFIG_MOD_DEV_IMU_ICM42688P` 宏硬编码，`SelectSource()` 中 if-else 决定实例化哪个设备。这种方式：

- 增删设备需要改 `SelectSource()` 函数
- 无法运行时自动探测实际连接的 IMU
- 每个设备工厂逻辑分散在 if-else 分支中

借鉴 `REGISTER_REMOTE` 的成功模式，用链接器段 + 注册宏替代编译期硬编码。

## 2. 核心设计

### 2.1 `ImuDevice` — 设备抽象基类

```cpp
// imu/devices/imu_device_layer.hpp 已有 Source 抽象类，扩展为：

struct ImuEntry {
    const char  *name;              // 设备名称（BMI088 / ICM42688P）
    uint16_t    priority;           // 探测优先级（越高越优先）
    Source*     (*factory)();       // 工厂函数，创建设备实例
};

// 现有 Source 接口不变：
class Source {
public:
    virtual bool    Probe();         // 新增：探测硬件是否存在
    virtual bool    Init() = 0;      // 现有
    virtual bool    Read(Sample &s) = 0; // 现有
    virtual void    Reset() = 0;     // 现有
    virtual uint8_t GetAddr() = 0;   // 现有
};
```

`Probe()` 是新增的纯虚函数（或默认返回 false），每个设备实现自己的硬件探测逻辑（如读取 WHO_AM_I 寄存器）。

### 2.2 `REGISTER_IMU` — 注册宏

```cpp
#define REGISTER_IMU(device_class, priority_, name_)                               \
    static Source* kFactory_##name_() { return new device_class(); }               \
    static const ImuEntry kImuEntry_##name_                                         \
    __attribute__((used, __section__(".imu"))) = { #name_, priority_, kFactory_##name_ }
```

用法（在设备 .cpp 末尾）：

```cpp
// bmi088.cpp
REGISTER_IMU(Bmi088, 100, bmi088);

// icm42688p.cpp
REGISTER_IMU(Icm42688p, 90, icm42688p);
```

### 2.3 链接器段声明

```cpp
// imu.hpp 或单独的 linker_defs.hpp
extern const ImuEntry __imu_start[];
extern const ImuEntry __imu_end[];
```

链接器脚本（`project/linker.ld` 或通过 `zephyr/linker.ld` 扩展）：

```ld
. = ALIGN(4);
__imu_start = .;
KEEP(*(SORT_BY_NAME(.imu)));
__imu_end = .;
```

### 2.4 `ImuManager` 自动探测

替换当前的 `SelectSource()`：

```cpp
/**
 * @brief 自动探测 IMU 设备 — 遍历注册表，Probe 成功即锁定。
 *        全部失败 → 返回 false。
 */
bool ImuManager::SelectSource()
{
    for (const ImuEntry *e = __imu_start; e < __imu_end; e++)
    {
        LOG_INF("probing %s ...", e->name);

        Source *dev = e->factory();
        if (dev == nullptr) continue;

        if (dev->Probe())
        {
            source_ = dev;
            LOG_INF("found %s", e->name);
            return source_->Init();
        }

        delete dev;
    }

    LOG_ERR("no imu found");
    return false;
}
```

### 2.5 探测顺序

`__attribute__((used, __section__(".imu")))` 默认按链接顺序遍历。如果需要按 `priority` 排序：

**方案 A：编译期排序**
```ld
__imu_start = .;
KEEP(*(SORT_BY_NAME(.imu.priority_100)));
KEEP(*(SORT_BY_NAME(.imu.priority_90)));
__imu_end = .;
```

宏内根据 priority 选择段名：
```cpp
#define REGISTER_IMU(device_class, priority_, name_) \
    // ... __section__(".imu.priority_" #priority_)
```

**方案 B：运行时排序**
遍历时按 priority 降序排序后再探测（冷路径，代价可忽略）。

推荐方案 A——一个链接段一个优先级，零运行时开销。

## 3. ImuManager 改造

### 3.1 状态机

```
Init → SelectSource
          │
     ┌────┴────┐
     │  Probe   │
     └────┬────┘
    通过 ←┴→ 失败
     │         │
   Init()   下一个设备
     │         │
     ▼         └── 全部失败 → return false
  正常解码
```

### 3.2 改造后的类结构

```cpp
class ImuManager final
{
public:
    bool Init(ImuStartMode mode = ImuStartMode::Normal);
    bool Start(ThreadPrio prio = ThreadPrio::Normal);

private:
    Source              *source_    = nullptr;
    Sample              sample_     {};
    attitude::Processor attitude_   {};
    heater::Heater      heater_     {};

    Timer               log_timer_  {10};
    topic::imu_to::Message pub_    {};
    Thread<4096>        thread_    {};
    bool                ready_     = false;

    bool SelectSource();
    bool Preheat();
    void Task();

    static void TaskEntry(void *p1, void *p2, void *p3);
};
```

与当前的区别：
- 删除 `AutoIdent` 相关（归 heater 管）
- `SelectSource()` 改为自动探测
- `Preheat()` 只调 `heater_.Preheat()`，不关心内部细节

### 3.3 任务循环

```cpp
void ImuManager::Task()
{
    while (!Preheat()) { /* 预热失败则重试？或直接挂起 */ }

    for (;;)
    {
        log_timer_.Update();

        if (!source_->Read(sample_)) {
            k_busy_wait(1000);
            continue;
        }

        attitude_.Update(sample_);
        heater_.Update(sample_.temp);

        pub_ = {};
        pub_.quat    = attitude_.GetQuaternion();
        pub_.gyro    = sample_.gyro;
        pub_.accel   = sample_.accel;
        pub_.temp    = sample_.temp;
        zbus_chan_pub(&pub_imu_to, &pub_, K_MSEC(1));

        log_timer_.Clock([&](){
            LOG_INF("q: %.3f, %.3f, %.3f, %.3f",
                    (double)pub_.quat.w, (double)pub_.quat.x,
                    (double)pub_.quat.y, (double)pub_.quat.z);
        });
    }
}
```

## 4. 与 `REGISTER_REMOTE` 的同与异

| 方面 | REGISTER_REMOTE | REGISTER_IMU |
|------|----------------|--------------|
| 注册对象 | `RemoteEntry`（协议 + UART 参数） | `ImuEntry`（工厂函数 + 设备名） |
| 探测方式 | 接收实际数据帧进行 Validate | 主动 Probe（读 WHO_AM_I / 握手） |
| 锁定 | 连续命中 need_hits 帧才锁 | Probe 一次返回 true 即锁定 |
| 切换 | 失败 retry++ → 下一协议 | Probe 失败 → 下一设备 |
| 多个同时 | 不支持双协议同时锁定（串口决定） | 不支持双 IMU 同时工作 |
| 数据来源 | UART 空闲中断 | SPI/I2C 主动读取 |
| 运行时切换 | 支持（超时 → 重探测 / 双串口切换） | 不支持（IMU 焊在板上，运行时不会换） |

## 5. 设备层改造

### 5.1 现有 Source 接口扩展

```cpp
class Source {
public:
    virtual ~Source() = default;

    /**
     * @brief 探测 IMU 硬件是否存在
     * @return true  硬件可用
     * @note 默认返回 false，设备需实现
     */
    virtual bool Probe() { return false; }

    virtual bool Init() = 0;
    virtual bool Read(Sample &s) = 0;
    virtual void Reset() = 0;
    virtual uint8_t GetAddr() = 0;
};
```

### 5.2 BMI088 Probe 示例

```cpp
bool Bmi088::Probe()
{
    // 尝试读取 BMI088 加速计 WHO_AM_I（0x00）
    uint8_t whoami = 0;
    if (!ReadReg(ACCEL_WHO_AM_I, &whoami, 1)) return false;
    if (whoami != 0x1E) return false;

    // 尝试读取 BMI088 陀螺仪 WHO_AM_I（0x0F）
    if (!ReadReg(GYRO_WHO_AM_I, &whoami, 1)) return false;
    if (whoami != 0x0F) return false;

    LOG_INF("BMI088 whoami OK");
    return true;
}
```

### 5.3 ICM42688P Probe 示例

```cpp
bool Icm42688p::Probe()
{
    uint8_t whoami = 0;
    if (!ReadReg(REG_WHO_AM_I, &whoami, 1)) return false;
    if (whoami != 0x47) return false;

    LOG_INF("ICM42688P whoami OK");
    return true;
}
```

## 6. 启动流程对比

### 当前

```
ImuManager::Init()
  → SelectSource()
    → CONFIG_MOD_DEV_IMU_BMI088 → new Bmi088 → Init()
    → CONFIG_MOD_DEV_IMU_ICM42688P → new Icm42688p → Init()
    → CONFIG_MOD_DEV_IMU_IDENT → ident 初始化
```

### 改造后

```
ImuManager::Init()
  → SelectSource()
    → 遍历 __imu_start → __imu_end
    → BMI088: factory → Probe() → true → Init() → locked
    → ICM42688p: factory → Probe() → false → delete → next
```

## 7. 文件变更清单

| 文件 | 变更 |
|------|------|
| `imu/devices/imu_device_layer.hpp` | Source 加 `virtual bool Probe()`，`virtual ~Source()` |
| `imu/devices/bmi088/bmi088.cpp` | 末尾加 `REGISTER_IMU(Bmi088, 100, bmi088)`，实现 `Probe()` |
| `imu/devices/icm42688p/icm42688p.cpp` | 末尾加 `REGISTER_IMU(Icm42688p, 90, icm42688p)`，实现 `Probe()` |
| `imu/drivers/imu.hpp` | 加 `ImuEntry` struct、`extern __imu_start/__imu_end`、`REGISTER_IMU` 宏 |
| `imu/drivers/imu.cpp` | `SelectSource()` 重写为遍历注册表 |
| `project/linker.ld` 或 `modules/CMakeLists.txt` | 加 `.imu` linker section |
| `doc/imu_register_arch.md` | 本文档 |

## 8. 收益

- 新增 IMU 设备只需新建文件 + 实现 `Source` 接口 + 末尾一行 `REGISTER_IMU`，不动核心代码
- 运行时自动探测，board overlay 不需要明确指定 IMU 型号
- SelectSource 从 if-else 链变成统一遍历逻辑，可读性大幅提升
- 与 REGISTER_REMOTE 共享同一个设计模式，降低学习成本
