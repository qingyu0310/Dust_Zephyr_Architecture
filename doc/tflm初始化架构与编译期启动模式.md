# tflm 初始化架构与编译期启动模式

## 目录

- [1. 当前问题](#1-当前问题)
- [2. 能否像 Zephyr 一样做](#2-能否像-zephyr-一样做)
- [3. Zephyr 的关键思想](#3-zephyr-的关键思想)
- [4. tflm 当前初始化链](#4-tflm-当前初始化链)
- [5. 建议的目标模型](#5-建议的目标模型)
- [6. 初始化项应该包含什么](#6-初始化项应该包含什么)
- [7. 编译期如何决定最终启动形态](#7-编译期如何决定最终启动形态)
- [8. 推荐分阶段演进](#8-推荐分阶段演进)
- [9. 不建议直接照搬的部分](#9-不建议直接照搬的部分)
- [10. 最终判断](#10-最终判断)

## 1. 当前问题

当前系统入口是：

```text
src/main.c
  → System_Bsp_Init()
  → System_Modules_Init()
  → System_Thread_Start()
```

三个函数的调用关系是清楚的，但每增加一个线程，通常都要手动修改：

1. `project/thread/Kconfig`；
2. `project/thread/CMakeLists.txt`；
3. `project/apps/System_startup.cpp`；
4. 线程实现文件。

其中真正需要减少人工维护的是第 3 步。

当前已经存在的编译期能力是：

```text
CONFIG_TRD_XXX
  → CMake 是否编译 trd_xxx.cpp
  → C++ 中是否包含对应接口
  → System_startup.cpp 中是否调用对应函数
```

因此当前属于：

> 编译期决定“有哪些组件”，运行期由 `System_startup.cpp` 手工决定“如何调用这些组件”。

## 2. 能否像 Zephyr 一样做

可以，而且非常适合当前架构。

但要准确理解“像 Zephyr”包含两层意思：

### 第一层：编译期裁剪

这一层你已经在做：

- Kconfig 决定功能开关；
- CMake 决定源文件是否进入编译；
- 依赖通过 `select` 传播；
- 未启用的模块不产生目标代码。

### 第二层：编译期注册初始化项

这一层目前还没有完成。

目标是让每个模块或线程自己声明：

```cpp
注册一个初始化函数
注册所属初始化等级
注册同等级内的优先级
```

最终链接产物中形成一张初始化表，系统启动器只需要遍历这张表。

从效果上看：

```text
当前：
System_startup.cpp 知道所有线程

目标：
每个线程自己注册，启动器只遍历注册表
```

这正是 Zephyr `SYS_INIT()`、`struct init_entry` 和链接段初始化机制背后的核心思想。

## 3. Zephyr 的关键思想

Zephyr 并不是让 `main()` 手工调用所有驱动初始化函数，而是把初始化函数包装成初始化项：

```text
初始化函数
  + 初始化等级
  + 等级内优先级
  + 参数
        ↓
编译器/链接器放入指定 section
        ↓
启动阶段按 section 顺序遍历
        ↓
执行初始化函数
```

典型初始化等级可以抽象成：

```text
EARLY
  → PRE_KERNEL_1
  → PRE_KERNEL_2
  → POST_KERNEL
  → APPLICATION
  → main thread
```

要点不是宏本身，而是三个设计：

1. 初始化项由组件自己声明；
2. 初始化顺序由等级和优先级表达；
3. 启动器不需要知道所有具体组件的名字。

## 4. tflm 当前初始化链

### 4.1 当前代码的职责

当前 [project/apps/System_startup.cpp](/D:/Zephyr/projects/tflm/project/apps/System_startup.cpp) 同时承担：

- 线程头文件汇总；
- 编译开关判断；
- 模块初始化调用；
- 线程启动调用；
- 线程优先级指定；
- 启动顺序维护。

这在项目规模较小时很直观，但它把多个变化原因集中到了一个文件：

```text
新增线程
修改优先级
调整依赖顺序
切换功能模式
修改启动失败策略
```

### 4.2 当前启动顺序表达的真实依赖

当前顺序大致是：

```text
CAN / GPIO 等基础能力
        ↓
遥控、IMU 等数据源
        ↓
CAN 发送
        ↓
底盘、云台等控制线程
        ↓
测试、PC、TFLM 等附加线程
```

这个顺序本身是有意义的，但目前通过代码位置表达，而不是通过初始化元数据表达。

### 4.3 `thread_init()` 和 `thread_start()` 的区别

当前接口已经把两个概念分开：

```cpp
void thread_init();
void thread_start(uint8_t prio);
```

这比单一的 `Start()` 更适合继续演进：

- `thread_init()`：检查设备、创建对象、配置参数、建立资源；
- `thread_start()`：创建或启动 Zephyr 线程；
- 初始化失败时，可以阻止该线程启动；
- 未来可以把两个函数注册到不同初始化等级。

因此当前接口不需要推倒重来，适合直接作为编译期注册机制的过渡基础。

## 5. 建议的目标模型

建议把初始化划分为三类，而不是继续把所有事情都叫作“线程启动”。

### 5.1 BSP 初始化

负责板级和底层资源准备：

```text
时钟
GPIO
总线控制器
中断基础配置
板级外设绑定
```

这一层通常应早于模块初始化。

### 5.2 Module 初始化

负责设备能力和算法对象准备：

```text
IMU 驱动实例
遥控器解码器
电机对象
功率控制器
消息通道依赖
```

这一层不一定创建线程，但必须让上层知道模块是否 ready。

### 5.3 Thread 启动

负责：

```text
创建线程
配置线程优先级
进入线程调度
```

它应该是初始化链的最后阶段之一。

推荐的目标运行链：

```text
EARLY
  → BSP
  → MODULE
  → TOPIC / RESOURCE
  → THREAD
  → APPLICATION
```

这里的 `TOPIC / RESOURCE` 不一定需要单独写很多初始化代码。它更多表示：消息队列、zbus 通道和共享资源必须在业务线程使用前完成定义和准备。

## 6. 初始化项应该包含什么

可以先设计一个最小初始化项：

```cpp
enum class InitLevel : uint8_t {
    Bsp = 0,
    Module,
    Thread,
};

using InitFunc = bool (*)();

struct InitEntry {
    InitFunc func;
    InitLevel level;
    uint16_t priority;
    const char* name;
};
```

实际项目中可以先不保存 `name`，但调试阶段保留名字很有价值。

每个组件只需要提供一个注册入口：

```cpp
bool ImuInit()
{
    return imu::Init();
}

REGISTER_INIT(ImuInit, InitLevel::Module, 20);
```

线程可以拆成两个入口：

```cpp
bool ImuModuleInit();
bool ImuThreadStart();

REGISTER_INIT(ImuModuleInit, InitLevel::Module, 20);
REGISTER_INIT(ImuThreadStart, InitLevel::Thread, 40);
```

启动器只负责：

```cpp
for (level : levels) {
    for (entry : entries_in_priority_order) {
        if (!entry.func()) {
            handle_init_failure(entry);
        }
    }
}
```

重要的是：启动器不再直接写 `remote::thread_init()`、`imu::thread_init()`、`chassis::thread_start()` 这些具体名字。

## 7. 编译期如何决定最终启动形态

### 7.1 Kconfig 决定组件是否存在

例如：

```text
CONFIG_TRD_IMU=y
  → 编译 trd_imu.cpp
  → 编译 IMU module
  → 编译 imu_to topic
  → 生成 IMU 初始化项
```

如果：

```text
CONFIG_TRD_IMU=n
```

那么对应源文件和初始化注册项都不应进入最终镜像。

### 7.2 CMake 决定源文件是否进入目标

当前 `project/thread/CMakeLists.txt` 已经按：

```cmake
if(CONFIG_TRD_IMU)
    target_sources(app PRIVATE imu/trd_imu.cpp)
endif()
```

裁剪线程实现。

未来注册机制也应遵守同一原则：

- 关闭组件时不编译注册函数；
- 不产生无效初始化项；
- 启动器不需要运行时判断大量 feature flag。

### 7.3 链接段保存初始化表

有两种实现方向。

#### 方向 A：链接段注册

每个 `REGISTER_INIT()` 生成一个静态 `InitEntry`，并放入指定 section：

```text
.tflm_init_bsp
.tflm_init_module
.tflm_init_thread
```

启动器通过 linker symbol 获取每个 section 的首尾地址：

```cpp
extern const InitEntry __tflm_init_module_start[];
extern const InitEntry __tflm_init_module_end[];
```

然后遍历范围。

优点：

- 接近 Zephyr 的设计；
- 各组件自注册；
- 启动器不需要 include 所有线程头文件；
- 适合后续扩展初始化等级。

代价：

- 需要处理 linker script；
- 需要保证 section 排序；
- 需要考虑 C++ 静态对象和链接器垃圾回收；
- 调试时要检查 map 文件中的初始化项。

#### 方向 B：Kconfig/CMake 生成注册列表

由 CMake 或脚本根据启用配置生成一个 `generated_init.cpp`：

```cpp
const InitEntry g_init_table[] = {
    {imu_init, InitLevel::Module, 20, "imu"},
    {remote_init, InitLevel::Module, 30, "remote"},
    {can_start, InitLevel::Thread, 40, "can"},
};
```

优点：

- 链接器机制更简单；
- 表内容直观；
- 初期更容易调试。

代价：

- 需要维护生成脚本；
- 组件注册信息可能同时存在于 Kconfig、CMake 和脚本中；
- 不如链接段方案自然。

### 7.4 推荐选择

建议分两步：

```text
第一阶段：生成表或显式注册表，先验证初始化模型
第二阶段：迁移到链接段注册，减少集中式启动文件
```

如果当前主要目标是学习 Zephyr 内核机制并建立自己的框架，最终可以采用链接段方案；如果主要目标是快速稳定落地，先采用生成表会更稳妥。

## 8. 推荐分阶段演进

### 阶段 1：统一初始化描述

先不改调用方式，只统一每个线程的元数据：

```cpp
namespace thread::imu {
    constexpr uint16_t kInitPriority = 20;
    constexpr uint8_t kThreadPriority = 4;
    bool thread_init();
    bool thread_start();
}
```

把当前 `void` 返回值逐步改成可以表达成功/失败的形式。

### 阶段 2：把启动参数移入 Kconfig

例如：

```text
CONFIG_TRD_IMU_INIT_PRIORITY=20
CONFIG_TRD_IMU_THREAD_PRIORITY=4
CONFIG_TRD_REMOTE_INIT_PRIORITY=30
CONFIG_TRD_REMOTE_THREAD_PRIORITY=4
```

这样不同板卡或不同项目可以改变启动策略，而不需要修改 `System_startup.cpp`。

### 阶段 3：引入统一注册宏

注册宏先可以生成普通数组项：

```cpp
REGISTER_MODULE_INIT(imu, imu::thread_init, 20);
REGISTER_THREAD_START(imu, imu::thread_start, 4);
```

这一阶段的目标是验证：

- 初始化项是否完整；
- 顺序是否正确；
- 失败处理是否明确；
- 同一组件是否被重复注册。

### 阶段 4：迁移到链接段

当初始化项模型稳定后，再把注册宏的实现替换成链接段：

```text
组件源码
  → REGISTER_INIT()
  → .tflm_init_xxx section
  → linker 排序
  → startup 遍历
```

这时 `System_startup.cpp` 可以缩减为：

```cpp
void System_Startup()
{
    RunInitLevel(InitLevel::Bsp);
    RunInitLevel(InitLevel::Module);
    RunInitLevel(InitLevel::Thread);
}
```

### 阶段 5：保留项目级显式模式

并不是所有启动逻辑都应该自动注册。

建议保留两种模式：

```text
自动模式：
  框架组件通过注册表启动

显式模式：
  特殊项目在 System_startup.cpp 中手工编排
```

例如：

- 需要临时实验的测试线程；
- 需要特殊顺序的标定流程；
- 需要根据运行条件决定是否启动的任务；
- 多板卡之间差异很大的项目逻辑。

这样既获得 Zephyr 风格的可扩展性，也不会失去项目级控制力。

## 9. 不建议直接照搬的部分

### 9.1 不要一开始就复制完整 Zephyr init 子系统

Zephyr 的初始化机制还涉及：

- 多个内核初始化等级；
- 架构和 SoC 初始化；
- linker section；
- SMP；
- 驱动设备模型；
- `device_is_ready()`；
- 静态对象和电源管理。

当前 tflm 只需要先解决自己的组件初始化，不需要立刻复制全部机制。

### 9.2 不要让每个模块都自动创建线程

模块和线程仍应保持边界：

```text
module：提供能力和 ready 状态
thread：决定是否创建线程、使用什么优先级
```

自动注册的是“初始化入口”，不是把所有模块都强行变成线程。

### 9.3 不要用初始化等级掩盖真实依赖

如果底盘依赖遥控 topic、CAN topic 和电机模块，就应该在 Kconfig 和文档中表达依赖。

不能只把底盘优先级写成 50，就认为依赖自然成立。

初始化顺序解决“什么时候执行”，依赖关系解决“为什么允许执行”，两者不是同一个问题。

## 10. 最终判断

当前 tflm 完全可以发展出类似 Zephyr 的编译期初始化模式，而且现有架构已经具备较好的基础：

- Kconfig 已经能裁剪功能；
- CMake 已经能裁剪源文件；
- `thread_init()` 和 `thread_start()` 已经分离；
- 模块、topic、线程边界已经建立；
- 启动顺序目前已经被明确写出来。

真正还缺少的是：

```text
初始化项描述
  + 等级
  + 优先级
  + 函数指针
  + 编译期注册
  + 启动器遍历
```

因此最准确的结论是：

> 你现在已经完成了“编译期选择组件”，下一步可以继续完成“编译期生成初始化表”，最终让启动过程从手工调用所有线程，演进为框架自动遍历已注册组件。

这不是脱离当前架构的重写，而是对现有 `Kconfig → CMake → thread_init/thread_start` 链路的自然延伸。

