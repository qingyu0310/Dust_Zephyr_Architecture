# tflm - Zephyr 模块化嵌入式框架

## 目录

- [1. 这是什么](#1-这是什么)
- [2. 一句话理解这套架构](#2-一句话理解这套架构)
- [3. 先分清两种顺序](#3-先分清两种顺序)
- [4. 顶层目录总览](#4-顶层目录总览)
- [5. 整体分层关系](#5-整体分层关系)
- [6. 真正的运行时启动链](#6-真正的运行时启动链)
- [7. 当前初始化表机制](#7-当前初始化表机制)
- [8. 编译期配置和裁剪链](#8-编译期配置和裁剪链)
- [9. `project/`：用户项目单元](#9-project用户项目单元)
- [10. `drivers/`：底层外设接口层](#10-drivers底层外设接口层)
- [11. `modules/`：设备能力与管理层](#11-modules设备能力与管理层)
- [12. `algorithm/`：纯计算与控制层](#12-algorithm纯计算与控制层)
- [13. `topic/`：线程间数据契约层](#13-topic线程间数据契约层)
- [14. `cmd/`：运行时命令入口](#14-cmd运行时命令入口)
- [15. 当前线程版图](#15-当前线程版图)
- [16. 当前数据流示例](#16-当前数据流示例)
- [17. 板级配置与构建入口](#17-板级配置与构建入口)
- [18. 新增功能时应该改哪里](#18-新增功能时应该改哪里)
- [19. 当前架构已经完成了什么](#19-当前架构已经完成了什么)
- [20. 当前还在收敛的地方](#20-当前还在收敛的地方)
- [21. 建议阅读顺序](#21-建议阅读顺序)
- [22. 关键文件索引](#22-关键文件索引)

## 1. 这是什么

这不是一个“把几个目录堆在一起”的普通工程。

这套仓库的目标，是在 Zephyr 之上，把：

- 板级配置
- 外设驱动
- 设备模块
- 控制/滤波/辨识算法
- 线程间消息
- 具体项目线程

拆成边界清楚、可编译期裁剪、可以逐步复用的结构。

它不是单纯为了“把一台机器人先跑起来”。

它更像是把真实机器人项目里反复出现的东西，重新拆成一套自己能长期掌控的框架语言。

所以这个仓库最重要的不是“某一个线程能不能跑”，而是：

1. 不同职责到底放在哪一层。
2. 这些层之间到底怎么连接。
3. 启动顺序、编译选择、消息通道、板级配置，是否已经形成稳定规则。

## 2. 一句话理解这套架构

一句话讲，这个仓库可以理解成：

> 以 Zephyr 为运行时基础，以 Kconfig/CMake 为编译期裁剪手段，以 `drivers / modules / algorithm / topic / project` 为主要职责边界的模块化嵌入式框架。

再说得更直白一点：

- `project/` 决定“这次我要装哪台机器、起哪些线程、绑哪块板子”。
- `topic/` 决定“线程之间传什么数据”。
- `modules/` 决定“设备能力怎么封装成可用对象”。
- `drivers/` 决定“怎么和 UART/CAN/SPI/USB/PWM/GPIO 这些底层外设打交道”。
- `algorithm/` 决定“控制器、滤波器、辨识器、TFLM 这些纯计算能力怎么组织”。

它不是传统 STM32 工程里那种“所有东西都往 app 或 bsp 里塞”的路数。

它也不是 Zephyr 官方那种完全通用中间件仓库。

它是一个明显带有真实机器人业务经验的、但又在主动做边界抽象的工程。

## 3. 先分清两种顺序

读这套架构时，最容易混掉的是下面两种顺序：

### 3.1 运行顺序

运行顺序是系统上电后必须遵守的依赖顺序。

比如：

- 哪些底层硬件先起来；
- 哪些线程必须先启动；
- 哪些模块必须等总线就绪后才能启动；
- 哪些业务线程应该晚一点开始跑。

这个顺序是硬约束。

### 3.2 开发顺序

开发顺序不是固定的。

你完全可以：

- 先写模块再补线程；
- 先写 topic 再补生产者和消费者；
- 先把某个驱动和模块打通，再回头补板级 overlay；
- 先做线程骨架，再把算法一点点填进去。

也就是说，这个仓库的设计思想不是“所有代码必须按一条流水线写出来”。

它强调的是：

> 先把职责边界和依赖方向定清楚，具体写代码时按问题驱动、按依赖收敛。

这点非常重要。

因为很多人一看启动链，就误以为整个项目也只能按那条顺序开发。

不是。

这里要分开：

- 运行时顺序是固定的；
- 开发时顺序是弹性的。

## 4. 顶层目录总览

当前仓库顶层关键目录如下：

```text
tflm/
├── algorithm/
├── cmd/
├── drivers/
├── modules/
├── topic/
├── project/
├── src/
├── doc/
├── scripts/
├── include/
├── third_party/
├── CMakeLists.txt
├── Kconfig
└── prj.conf
```

各目录的角色可以先粗看成下面这样：

| 目录 | 作用 | 备注 |
|---|---|---|
| `algorithm/` | 纯计算层 | 控制、滤波、辨识、TFLM |
| `cmd/` | 命令层 | 运行时 shell 入口，当前骨架为主 |
| `drivers/` | 驱动层 | 对 Zephyr 外设 API 的 C++ 封装 |
| `modules/` | 模块层 | 面向设备能力的对象封装 |
| `topic/` | 消息层 | zbus / msgq 数据契约 |
| `project/` | 项目层 | apps / boards / thread 三部分 |
| `src/` | 根入口 | `main.c` 在这里 |
| `doc/` | 说明文档 | 深入分析、记录、路线图 |
| `scripts/` | 配套脚本 | 当前主要是 IMU 辨识和分析脚本 |
| `include/` | 公共头 | 补充头文件 |
| `third_party/` | 三方代码 | Eigen、FlatBuffers、gemmlowp、ruy 等 |

## 5. 整体分层关系

如果只看“谁依赖谁”，当前整体关系可以先理解成：

```text
           project/
        (apps / boards / thread)
                  │
      ┌───────────┼───────────┐
      │           │           │
   modules/   algorithm/   topic/
      │           │           │
      └───────┬───┴───────┬───┘
              │           │
           drivers/    Zephyr API
              │
     Zephyr HAL + SDK Glue + Devicetree
```

但如果只用这一张图，会丢掉一个核心事实：

`project/` 不是简单地“在最上层调下面所有东西”。

它实际上承担了三件非常不一样的工作：

1. 在 `apps/` 里定义启动入口和初始化编排。
2. 在 `boards/` 里定义板级绑定和烧录规则。
3. 在 `thread/` 里把模块、算法、topic 装配成真实业务线程。

所以 `project/` 不是薄薄的一层。

它是“把框架装成具体系统”的那一层。

## 6. 真正的运行时启动链

当前 live tree 里的真实入口，不是某个庞大的系统总控类。

最外层入口很直接：

```text
src/main.c
    └── System_Startup()
```

`src/main.c` 的职责非常窄：

- 进入 `main()`
- 调用 `System_Startup()`
- 然后留在主循环里睡眠

真正的启动组织在 `project/apps/Init_entry.cpp`。

当前启动阶段顺序是：

```text
Bsp -> ThreadEarly -> Module -> ThreadMid -> ThreadLate
```

这里有两个重要点：

### 6.1 为什么先 `Bsp`

`Bsp` 阶段放的是最基础的板级能力和底层总线初始化。

当前最典型的就是 CAN 发送线程相关的底层设备准备。

### 6.2 为什么 `ThreadEarly` 在 `Module` 之前

这不是随便排的。

当前代码里的注释已经说明了意图：

- 某些线程必须比模块层更早就绪；
- 例如 CAN 这类总线能力，需要先能工作，后面模块或业务线程再往上挂。

所以它不是“先模块，后线程”的死板套路。

而是：

> 哪些能力必须先就绪，就先把对应线程或基础设施拉起来。

这正是这套架构比传统“统一 `InitAll()` + `StartAll()`”更进了一步的地方。

## 7. 当前初始化表机制

当前仓库已经不再完全依赖一个大号 `#ifdef` 启动表去手写所有调用。

它现在有了自己的初始化项注册机制。

### 7.1 注册方式

核心宏是：

```cpp
REGISTER_INIT(fn, stage_, level_, name_)
```

每个线程或组件可以把自己的初始化函数、启动函数，注册到 `.user_init` 链接段。

这意味着：

- 启动项不再必须都集中写在一个巨大总控函数里；
- 每个线程文件可以在本地声明自己的启动条目；
- 启动器统一遍历这些条目，而不是手工知道所有组件。

### 7.2 初始化阶段

`project/apps/Init_entry.hpp` 当前定义了五个阶段：

| 阶段 | 含义 |
|---|---|
| `Bsp` | 板级基础能力：时钟、GPIO、总线、中断等 |
| `ThreadEarly` | 早期线程 |
| `Module` | 设备/算法模块初始化 |
| `ThreadMid` | 中期线程 |
| `ThreadLate` | 后期线程 |

### 7.3 初始化等级

除了阶段，还有等级：

| 等级 | 行为 |
|---|---|
| `High` | 失败后停机 |
| `Mid` | 报错后继续 |
| `Low` | 告警后继续 |

这说明当前启动系统已经开始表达“失败是否致命”这类架构语义。

这不是简单的 `if (!init()) return false;`。

它已经是在描述：

- 哪些组件是必须起来的；
- 哪些组件可以带病运行；
- 哪些只是可选能力。

### 7.4 链接段

`.user_init` 段由 `cmd/linker/tflm_init.ld` 定义边界：

```text
__user_init_start
...
KEEP(*(.user_init))
...
__user_init_end
```

然后 `Init_entry.cpp` 在运行时遍历这段内的所有 `InitEntry`。

这条链是当前 README 最值得写清楚的地方之一。

因为它已经不是“普通项目启动函数”了。

它实际上是：

> 一个小型的、面向当前项目的编译期注册 + 运行时遍历启动框架。

### 7.5 当前它和 Zephyr 的关系

这套东西不是 Zephyr 官方 `SYS_INIT()` 的直接照搬。

但思路已经明显往那边靠：

- 编译期收集初始化项；
- 链接期形成表；
- 运行时按阶段遍历。

所以它不是一个随意的技巧。

它已经是当前仓库里最有“框架味”的部分之一。

## 8. 编译期配置和裁剪链

当前仓库的另一条主线，不在运行时，而在编译期。

这条线主要靠：

- 根 `Kconfig`
- 根 `CMakeLists.txt`
- `prj.conf`
- `project/thread/Kconfig`
- 各层自己的 `Kconfig`
- 各层自己的 `CMakeLists.txt`

共同完成。

### 8.1 根 `Kconfig`

根 `Kconfig` 负责项目入口门禁。

当前最关键的是：

```text
CONFIG_PRJ_TEST
```

它控制项目层是否参与编译，并在打开后 `rsource "project/thread/Kconfig"`。

也就是说，根 Kconfig 不直接把所有功能平铺出来。

它先决定：

- 当前有没有项目层；
- 当前项目层要不要暴露线程/功能开关。

### 8.2 `prj.conf`

`prj.conf` 是当前所有项目共享的公共配置基线。

当前里面比较关键的内容包括：

- `CONFIG_CPP=y`
- `CONFIG_STD_CPP17=y`
- `CONFIG_STATIC_INIT_GNU=y`
- `CONFIG_THREAD_NAME=y`
- `CONFIG_SYS_CLOCK_TICKS_PER_SEC=2000`
- `CONFIG_HW_STACK_PROTECTION=y`
- `CONFIG_SERIAL=y`
- `CONFIG_CONSOLE=y`
- `CONFIG_UART_CONSOLE=y`
- `CONFIG_LOG_MODE_IMMEDIATE=y`
- `CONFIG_SPEED_OPTIMIZATIONS=y`
- `CONFIG_PRJ_TEST=y`

这说明当前仓库的公共默认环境已经不是“只开最小 Zephyr 基线”。

它是为：

- C++17
- 线程化业务
- 即时日志
- 实时控制
- 固件性能优化

这一类应用预先做了偏向性配置。

### 8.3 根 `CMakeLists.txt`

根 `CMakeLists.txt` 当前做了几件架构上很关键的事：

1. 设定当前项目选择：
   - `ACTIVE_PRJ "test"`
   - `PROJ_DIR project`
   - `CONFIG_SYM PRJ_TEST`
2. 设置 HPMicro 的 `SDK_GLUE_DIR`
3. 把 `BOARD_ROOT` / `SOC_ROOT` / `DTS_ROOT` 指向 SDK glue
4. 根据 `BOARD_CFG` 和 `BOARD` 自动加载：
   - board overlay
   - 板级 `.conf`
   - `board.cmake`
5. 在 `CONFIG_${CONFIG_SYM}` 为真时才把：
   - `drivers/`
   - `algorithm/`
   - `modules/`
   - `topic/`
   - `cmd/`
   - `project/`
   这些目录接进来

这意味着当前仓库的“项目装配”首先发生在 CMake 层。

不是先写死在代码里。

### 8.4 `project/thread/Kconfig`

`project/thread/Kconfig` 是当前最直接的功能裁剪面板。

它一方面定义线程开关，另一方面通过 `select` 自动把下层依赖拉进来。

例如：

| 线程开关 | 自动拉起的依赖 |
|---|---|
| `TRD_CHASSIS` | `MOD_CTL_POWER`、`MOD_DEV_MOTOR_DJI`、`TPC_TO_CAN_TX`、`TPC_REMOTE_TO` |
| `TRD_GIMBAL` | `CTL_PID`、`MOD_DEV_MOTOR_DM`、`TPC_TO_CAN_TX`、`TPC_REMOTE_TO` |
| `TRD_CAN_TX` | `COM_CAN`、`TPC_TO_CAN_TX` |
| `TRD_REMOTE` | `MOD_DEV_REMOTE` |
| `TRD_IMU` | `MOD_DEV_IMU`、`TPC_IMU_TO` |
| `TRD_TFLM` | `TFLM` |
| `TRD_PC` | `COM_USB` |
| `TRD_TEST` | `COM_UART_DMA`、`MOD_DEV_MOTOR_DJI`、`CTL_PID`、`TPC_TO_CAN_TX`、`ID_MOTOR_PLANT` |

这个机制有两个好处：

1. 用户打开的是“业务线程”，而不是一堆零碎底层开关。
2. 依赖关系被编译期表达出来，不需要人工记忆。

### 8.5 `project/thread/CMakeLists.txt`

线程目录的 `CMakeLists.txt` 与 `Kconfig` 配套。

Kconfig 决定“逻辑上选了什么”。

CMake 决定“真正把哪些源文件编译进去”。

例如：

- 开了 `CONFIG_TRD_IMU`，就编译 `project/thread/imu/trd_imu.cpp`
- 开了 `CONFIG_TRD_PC`，就编译 `project/thread/pc/trd_pc.cpp`
- 开了 `CONFIG_TRD_TEST`，就编译 `project/thread/test/trd_test.cpp`

因此当前仓库的裁剪链不是单点机制。

而是：

```text
Kconfig 负责选择
    ↓
CMake 负责纳入编译
    ↓
链接阶段形成最终镜像
```

## 9. `project/`：用户项目单元

`project/` 是整套架构里最关键、也最容易被误解的一层。

它既不是纯框架底层，也不是随便塞业务代码的地方。

它承担的是“把框架装配成一台具体机器”的职责。

当前 `project/` 主要由三部分组成：

```text
project/
├── apps/
├── boards/
└── thread/
```

### 9.1 `project/` 最灵活的地方

`project/` 被单独抽出来，不只是为了让目录更好看。

它最灵活、也最有架构价值的一点是：

> 当 `drivers / modules / algorithm / topic` 这些框架层能力已经覆盖需求后，新建一个项目，或者把一个旧项目移植到这套框架里，原则上只需要改 `project/`。

也就是说，项目切换时真正应该变化的，是：

- `apps/`：启动组织怎么排
- `boards/`：这次跑在哪块板子上
- `thread/`：这次有哪些业务线程、怎么装配模块和 topic

而下面这些层，理想上不应该跟着每个项目一起改：

- `drivers/`
- `modules/`
- `algorithm/`
- `topic/`

这样 `project/` 才真的像“项目单元”，而不是“整个仓库的一层皮”。

换句话说，这套架构真正想达到的不是“每次来个新项目就再改一遍全仓库”，而是：

> 把项目差异尽量收敛到 `project/`，把可复用能力尽量沉到框架层。

当然，如果新项目引入了全新的硬件、全新的设备能力、或者全新的算法需求，那就应该往下扩展对应层。

但在框架能力已经具备的前提下，项目迁移的主要修改面就应该是 `project/`。

### 9.2 `apps/`

`apps/` 管的是启动入口和启动规则。

当前你应该重点看这几个文件：

| 文件 | 作用 |
|---|---|
| `project/apps/System_startup.h` | 启动入口声明 |
| `project/apps/Init_entry.hpp` | 初始化项定义、阶段、等级、注册宏 |
| `project/apps/Init_entry.cpp` | 启动遍历器、阶段执行器、失败策略 |
| `project/apps/Irq_handlers.cpp` | 中断处理 |
| `project/apps/Irq_handlers.h` | 中断接口声明 |

也就是说，当前 `apps/` 的核心不是某个巨大流程函数。

核心是：

- 启动注册机制；
- 启动阶段组织；
- 中断入口约束。

### 9.3 `boards/`

`boards/` 管的是板级绑定。

当前目录结构是：

```text
project/boards/
├── hpm/
│   ├── hpm5361icb/
│   └── hpm6e00evk/
└── st/
    ├── board_rm_c/
    └── puzhong/
```

每个板型目录里一般包含三类文件：

| 文件 | 作用 |
|---|---|
| `<board>.overlay` | 设备树 overlay，做 pin/alias 绑定 |
| `<board>.conf` | 板级或 SoC 相关 Kconfig 覆写 |
| `board.cmake` | 烧录脚本或板级构建辅助 |

这一层解决的问题是：

- 当前设备树别名怎么取；
- UART/CAN/SPI/USB 到底绑到哪组硬件上；
- 某块板子需要打开哪些驱动和外设能力；
- 某块板子的烧录工具怎么接。

### 9.4 `thread/`

`thread/` 是把系统真正装成业务行为的地方。

这里不是简单地“放几个任务”。

它是：

- 模块对象的装配点；
- topic 的生产者/消费者挂接点；
- 控制循环和业务逻辑的入口；
- 当前整机行为最集中的实现层。

当前子目录包括：

```text
project/thread/
├── can/
├── chassis/
├── gimbal/
├── gpio/
├── imu/
├── pc/
├── remote/
├── test/
├── tflm/
└── thread.hpp
```

其中 `thread.hpp` 提供了统一的线程包装模板：

- `Thread<StackSize>`
- `Start(k_thread_entry_t entry, ThreadPrio prio, ...)`

因此线程文件自己更关注：

- 初始化逻辑；
- 业务循环；
- 优先级；
- 启动阶段；

而不是重复写大量 Zephyr 样板代码。

## 10. `drivers/`：底层外设接口层

`drivers/` 负责把 Zephyr 的外设能力封装成当前项目直接可用的 C++ 接口。

它的职责很明确：

- 操作 `struct device`
- 调 Zephyr 驱动 API
- 提供收发和控制接口
- 不带业务语义

当前目录大体是：

```text
drivers/
├── communication/
│   ├── can/
│   ├── rs485/
│   ├── spi/
│   ├── uart/
│   └── usb/
└── device/
    ├── gpio/
    └── pwm/
```

### 10.1 这一层管什么

这一层主要管：

- UART 中断/DMA 接收
- CAN 收发
- SPI 外设通信
- USB CDC ACM
- RS485
- GPIO 输入输出
- PWM 输出

### 10.2 这一层不管什么

这一层不应该直接管：

- 底盘控制策略
- 遥控语义解析
- IMU 姿态融合
- 电机功率控制
- 线程生命周期

这些都属于上层。

### 10.3 `RxStream` 抽象

当前驱动层里一个很值得写进 README 的点，是 `RxStream`。

它是串行接收类设备的统一接口。

当前明确实现这个接口的有：

- `Uart`
- `UartDma`
- `Rs485`
- `Usb`

这意味着上层在很多情况下不用关心“底下到底是 UART DMA 还是 USB CDC”。

它只需要关心：

- 初始化参数；
- 接收通知；
- 读取缓冲。

这比把每种外设都写成完全不同接口要干净得多。

### 10.4 驱动层当前的架构价值

对这套仓库来说，`drivers/` 的意义不只是“把底层封起来”。

它更重要的作用是：

> 把 Zephyr/HAL 世界，翻译成当前项目自己的稳定接口世界。

这样 `modules/` 和 `project/thread/` 就不必直接到处碰 Zephyr 的底层细节。

## 11. `modules/`：设备能力与管理层

`modules/` 是“设备对象”真正出现的地方。

这里的重点不是某条总线，而是“一个设备模块对外到底提供什么能力”。

当前主要子目录是：

```text
modules/
├── imu/
├── motors/
├── powermeter/
└── remotes/
```

### 11.1 模块层的职责

模块层负责：

- 封装具体设备；
- 组合驱动与算法；
- 管理配置；
- 维护 `ready_` 或可用状态；
- 给线程层提供相对完整的设备能力。

模块层不应该演化成“大一统业务总控”。

需要多个模块协作时，最终装配应回到 `project/thread/`。

### 11.2 `modules/imu/`

当前 IMU 模块的角色，不是只读一份原始加速度和角速度。

它已经明显承担：

- 采样；
- 控温；
- 姿态解算；
- 数据发布；
- 识别/调试相关能力。

从 `modules/Kconfig` 可以看出，`MOD_DEV_IMU` 会自动拉起：

- `FLT_QUATERNION`
- `TPC_IMU_TO`
- `DEV_PWM`
- `CTL_PID`
- `CTL_TIMER`
- `ID_STABILITY`

这说明当前 IMU 模块已经不是“一个 SPI 传感器驱动”。

它是驱动、控制和消息层的组合点。

而具体 IMU 数据源还能继续分成：

- `MOD_DEV_IMU_BMI088`
- `MOD_DEV_IMU_ICM42688P`

这也体现了“模块层负责设备能力抽象，具体芯片在模块内部切换”的方向。

### 11.3 `modules/remotes/`

遥控模块当前不是单一协议写死。

从目录上能看到：

- `dr16/`
- `vt12/`
- `vt13/`
- `remote.cpp/.hpp`
- `protocol_base.hpp`

这说明当前遥控器这条链，已经开始从“某个具体接收机实现”抽成：

- 协议基类
- 多协议实现
- 自动识别或统一上层接口

再结合 `TRD_REMOTE -> MOD_DEV_REMOTE -> COM_UART_DMA + TPC_REMOTE_TO`

能看出这一层的角色很清楚：

- 底层靠串口 DMA 接收；
- 模块层把协议和设备语义包起来；
- 最终对外发布遥控消息。

### 11.4 `modules/motors/`

电机模块当前主要包括：

- DJI 电机
- DM 电机

像你现在打开的 `modules/motors/dji/dji_c6xx.hpp`，本身就已经不是“裸 CAN 帧工具函数”。

它在做的事情包括：

- 编码器累计；
- 角度/角速度/电流/力矩/线速度换算；
- 快照读取；
- 面向控制层的数据接口暴露。

这说明模块层不是只负责初始化。

它还负责把底层离散、原始、协议相关的数据，变成上层控制算法可直接消费的状态量。

### 11.5 `modules/powermeter/`

功率计模块目前的职责更单纯一些：

- 读取功率相关数据；
- 为底盘功率控制分配提供输入。

它本身不负责整车的功率策略。

策略属于更上层的算法/线程装配。

### 11.6 模块层当前的价值

如果没有 `modules/`，上层线程会直接面对：

- UART/SPI/CAN 的驱动细节
- 原始传感器数据
- 电机协议解析
- 设备就绪与错误状态

这样 `project/thread/` 很快就会变脏。

所以模块层的意义不是“多套一层”。

而是把“设备能力”和“业务线程”隔开。

## 12. `algorithm/`：纯计算与控制层

`algorithm/` 是当前仓库最像“可脱离硬件复用”的那一层。

它的基本原则是：

- 只收数据
- 只算结果
- 不拿外设句柄
- 不创建线程

当前目录可以分成几大块：

```text
algorithm/
├── buffer/
├── controller/
├── filter/
├── identify/
├── math/
└── tflm/
```

### 12.1 `buffer/`

这一层当前有：

- `bipbuf/`
- `ringbuf/`

它服务的是“数据进出过程中的缓存组织”，不是业务控制。

典型价值在：

- DMA 友好；
- 零拷贝倾向；
- 收发链路缓冲。

### 12.2 `controller/`

这一层当前包括：

- `pid/`
- `power_ctrl/`
- `timer/`

对应 `algorithm/Kconfig` 里的：

- `CTL_PID`
- `CTL_TIMER`
- `CTL_EXECTIMER`
- `MOD_CTL_POWER`

这说明“功率控制器”虽然名字像模块，但它在架构上被放在算法层。

因为它本质还是控制计算能力。

### 12.3 `filter/`

当前滤波层包括：

- `hpf/`
- `lpf/`
- `kalman/`
- `quaternion/`

这层的架构意义很大。

因为它已经不只是几个一阶滤波器。

它包含了：

- 线性 Kalman
- 扩展卡尔曼滤波器模板
- 四元数姿态解算

尤其 `FLT_QUATERNION` 在 Kconfig 里会自动 `select FLT_KALMAN_EKF`。

这说明四元数姿态解算并不是孤立实现。

它是建立在更底层通用 EKF 能力之上的。

### 12.4 `identify/`

辨识层当前包括：

- `rls/`
- `motor/`
- `stability.hpp`

对应能力包括：

- RLS 递推最小二乘
- 电机本体辨识
- 稳定判据与波形发生器

这意味着当前架构并不只关心“控制器上线能跑”。

它也开始关心：

- 如何识别模型；
- 如何离线/在线分析系统；
- 如何把实验能力组织进代码体系。

### 12.5 `math/`

这里当前核心是 Eigen。

也就是：

- 提供线性代数基础；
- 为 Kalman、EKF、RLS 这一类算法服务。

### 12.6 `tflm/`

这一层是 TensorFlow Lite Micro 底座。

它不是单个模型文件。

而是把：

- TFLM runtime
- Micro interpreter
- signal kernels
- model data

放进了当前工程体系。

这说明这个仓库不是只做传统控制。

它也在给“把小模型拉进固件”预留位置。

### 12.7 算法层的依赖方向

理想依赖方向应该始终是：

```text
algorithm -> algorithm
algorithm ↛ drivers
algorithm ↛ modules
algorithm ↛ topic
algorithm ↛ project/thread
```

也就是说，算法层可以被上层装配，但不应该反向知道上层存在。

这是当前仓库里最需要守住的一条边界。

## 13. `topic/`：线程间数据契约层

`topic/` 的作用，不是“放几个结构体方便传参”。

它的作用是：

> 明确线程、模块、业务单元之间，交换的到底是什么数据。

当前已有三个主要通道：

```text
topic/
├── imu_to/
├── remote_to/
└── to_can_tx/
```

### 13.1 `remote_to`

`remote_to::Message` 当前表达的是遥控语义，而不是原始串口字节流。

当前主要字段包括：

- `version`
- `chassisx`
- `chassisy`
- `yaw`
- `pitch`
- `chassis_mode`
- `shoot_ctrl`
- `reload_ctrl`
- `autoaim_ctrl`
- `supercap_ctrl`

这说明遥控器模块已经把协议、归一化和开关语义消化掉了。

下游线程接收到的是“控制语义”，不是协议细节。

### 13.2 `imu_to`

`imu_to::Message` 当前表达的是 IMU 姿态结果。

字段包括：

- `quaternion[4]`
- `gyro[3]`
- `temperature`
- `roll`
- `pitch`
- `yaw`
- `yaw_total`

也就是说，这个 topic 已经是“姿态与角速度结果”层，而不是原始加速度计寄存器层。

### 13.3 `to_can_tx`

`to_can_tx::Message` 很简单：

- `tx_id`
- `data[8]`

但它的机制和前两个不一样。

前两个主要基于 zbus。

`to_can_tx` 当前走的是 `k_msgq`。

### 13.4 为什么 `to_can_tx` 不是 zbus

当前 `project/thread/can/trd_can_tx.cpp` 里的注释已经说清楚了：

- 多个发布者共用一个 zbus channel 时可能相互覆盖；
- `k_msgq` 天然适合多 put 一 get 的逐条消费；
- CAN 发送更像帧队列，而不是“最新状态广播”。

所以 `topic/` 里并不是“一律 zbus”。

这里已经开始根据数据语义选择机制：

- 广播型、状态型数据：zbus
- 帧队列型、逐条消费数据：`k_msgq`

这恰恰说明 topic 层不是形式主义。

它已经在表达数据语义。

## 14. `cmd/`：运行时命令入口

`cmd/` 当前还处在骨架阶段。

这一层现在的主要意义，是预留一个运行时参数和状态查看入口。

它未来要做的事应该是：

- 注册 shell 命令；
- 解析命令参数；
- 调用下层模块或算法接口；
- 查看状态、改参数、做调试。

但它当前还不是系统主线。

所以在这套架构里，`cmd/` 更像：

- 已经预留了位置；
- 规则已经想清楚；
- 具体命令实现还没大面积展开。

这也是一种正常的成熟过程。

## 15. 当前线程版图

当前线程不是靠 README 想象出来的。

它们已经在 `project/thread/` 下各自注册启动项。

### 15.1 当前线程目录

当前主要线程包括：

- `gpio`
- `can`
- `remote`
- `imu`
- `chassis`
- `gimbal`
- `pc`
- `tflm`
- `test`

### 15.2 当前注册阶段与优先级

按当前 live tree，可整理成下面这张表：

| 线程/功能 | 初始化注册 | 启动注册 | 线程优先级 / 启动参数 |
|---|---|---|---|
| `can` | `Bsp / High` | `ThreadEarly / High` | `ThreadPrio::High` |
| `remote` | `Module / High` | `ThreadLate / High` | `remote_.Start(5)` |
| `imu` | `Module / High` | `ThreadLate / High` | `imu_.Start(5)` |
| `chassis` | `Module / Mid` | `ThreadLate / Mid` | `ThreadPrio::Normal` |
| `gimbal` | `Module / Mid` | `ThreadLate / Mid` | `ThreadPrio::Normal` |
| `pc` | `Module / Low` | `ThreadLate / Low` | `ThreadPrio::Low` |
| `output` | `Module / Low` | `ThreadLate / Low` | `ThreadPrio::Low` |
| `input` | `Module / Low` | `ThreadLate / Low` | `ThreadPrio::Low` |
| `tflm` | `Module / Low` | `ThreadLate / Low` | `ThreadPrio::Lowest` |
| `test` | `Module / Low` | `ThreadLate / Low` | `ThreadPrio::Low` |

### 15.3 这张表说明了什么

这张表里最值得注意的是：

1. `can` 被提前到了 `Bsp + ThreadEarly`
2. `remote` 和 `imu` 被视为较高优先级能力
3. `chassis` / `gimbal` 被放在 `Mid`
4. `test` / `pc` / `tflm` 被明显放在更后、更弱的等级

这其实已经把“系统里谁更基础、谁更核心、谁更像附加能力”表达出来了。

换句话说，线程表本身就是架构说明。

### 15.4 `trd_test`：专门跑 Demo 和验证的线程

`project/thread/test/trd_test.cpp` 是这套架构里另一个很灵活的入口。

它不需要承担正式业务线程的长期职责。

它更像一个专门留出来的：

- Demo 运行区；
- 新模块验证区；
- 参数实验区；
- 临时功能试验区；
- 设备辨识入口。

当前的 `trd_test` 就是一个很具体的例子：

- 复用 DJI C610 电机模块；
- 复用 `to_can_tx` 的 CAN 发送队列；
- 复用 `MotorPlant` 电机本体辨识算法；
- 通过 CAN 回调接收反馈；
- 在线注入转矩并记录速度；
- 输出辨识得到的 `tau`、`K`、`p1`、`p2`、`p4`。

它没有把这段实验逻辑硬塞进：

- `modules/motors/`
- `algorithm/identify/motor/`
- `project/thread/chassis/`

而是把“这次我要验证什么”留在 `trd_test` 里。

这正是它方便的地方。

### 15.5 `trd_test` 的使用方式

如果只是想快速跑一个 Demo，通常只需要：

1. 在 `project/thread/test/trd_test.cpp` 里写实验逻辑；
2. 在 `project/thread/Kconfig` 里选择已有模块；
3. 在 `project/thread/CMakeLists.txt` 里确认测试线程进入编译链；
4. 复用已有的驱动、模块、算法、topic；
5. 给测试线程注册 `thread_init()` 和 `thread_start()`；
6. 用 `REGISTER_INIT()` 把它挂进启动阶段。

它的好处是：

- 不需要为了一个 Demo 新建一整套框架层；
- 不需要把临时实验逻辑污染到正式业务线程；
- 不需要重复实现已经存在的电机、CAN、辨识或控制能力；
- 需要什么就选择什么，写完就能进入现有编译和启动链。

所以 `trd_test` 不是“随便放临时代码的垃圾桶”。

它是一个有边界的快速验证槽。

如果某个 Demo 最终变成正式能力，再把它从 `trd_test` 里沉淀到合适的：

- `algorithm/`
- `modules/`
- `topic/`
- 正式业务线程

这样既保留了实验速度，也保留了后续整理的路径。

## 16. 当前数据流示例

如果只看目录，你不一定能感受到整套东西怎么流动。

下面给几条当前比较有代表性的链。

### 16.1 遥控链

```text
UART DMA / RxStream
    ↓
modules/remotes
    ↓
topic::remote_to
    ↓
chassis / gimbal
```

这条链体现的是：

- 驱动层负责接收；
- 模块层负责协议和语义；
- topic 层负责契约；
- 业务线程负责消费。

### 16.2 IMU 链

```text
SPI / PWM / timer / identify
    ↓
modules/imu
    ↓
algorithm/filter/quaternion
    ↓
topic::imu_to
    ↓
其他消费者
```

这条链说明 IMU 已经不是“一个传感器驱动文件”。

它是跨多层的综合路径。

### 16.3 CAN 发送链

```text
chassis / gimbal / other producers
    ↓
topic::to_can_tx (k_msgq)
    ↓
thread::can
    ↓
drivers/communication/can
```

这条链体现的是：

- 业务线程不直接拿底层 CAN 发；
- 它们只负责组帧或生成目标帧；
- 真正的发送线程集中处理队列与发出动作。

### 16.4 TFLM 链

```text
algorithm/tflm
    ↓
project/thread/tflm
```

当前它更偏测试和推理入口，而不是已经完全并入主业务闭环。

但位置已经给出来了。

## 17. 板级配置与构建入口

当前仓库的构建链，不只是 `west build` 一条命令。

它还涉及：

- `BOARD`
- `BOARD_CFG`
- `SDK_GLUE_DIR`
- overlay
- board `.conf`
- `board.cmake`

### 17.1 `BOARD_CFG`

当前根 `CMakeLists.txt` 会根据：

- `BOARD_CFG`
- `BOARD`

自动去找：

- `${PROJ_DIR}/boards/*/${BOARD_CFG}/${BOARD}.overlay`
- `${PROJ_DIR}/boards/*/${BOARD_CFG}/${BOARD}.conf`
- `${PROJ_DIR}/boards/*/${BOARD_CFG}/board.cmake`

这意味着当前板级配置的组织方式不是乱放。

它是“按板型分组，再按实际 board 名取具体文件”。

### 17.2 `SDK_GLUE_DIR`

当前 HPMicro 相关板级/SOC/DTS 补充，默认从：

```text
D:/Zephyr_HPMicro/sdk_glue
```

引入。

也可以通过环境变量覆盖。

这个设计说明当前仓库默认并不想把所有 HPMicro glue 都直接塞进本仓库。

它允许外部 glue 仓库参与装配。

### 17.3 常用构建命令

当前常见命令是：

```bash
west build -b hpm5361icb -- -DBOARD_CFG=hpm5361icb
west build -b stm32f4_disco -- -DBOARD_CFG=puzhong
west build -b stm32f407igh6 -- -DBOARD_CFG=board_rm_c
west build -b hpm6e00evk -- -DBOARD_CFG=hpm6e00evk
```

### 17.4 当前默认项目基线

`prj.conf` 当前直接打开了：

```text
CONFIG_PRJ_TEST=y
```

也就是默认以当前 `project/` 里的 test 项目基线来装配整个系统。

## 18. 新增功能时应该改哪里

这部分是 README 最应该保留的内容之一。

因为它直接回答“这套架构到底怎么用”。

### 18.1 新增一个线程

典型路径是：

1. 在 `project/thread/<name>/` 下新增 `trd_<name>.cpp`
2. 在 `project/thread/Kconfig` 里加 `CONFIG_TRD_<NAME>`
3. 在 `project/thread/CMakeLists.txt` 里加对应编译段
4. 在 `trd_<name>.cpp` 本地写：
   - `thread_init()`
   - `thread_start()`
   - `REGISTER_INIT(...)`

当前架构下，新增线程已经不再要求你回到一个中央大总控里手工登记所有调用。

这比老式写法清爽很多。

### 18.2 新增一个模块

典型路径是：

1. 在 `modules/<name>/` 下建目录
2. 放 `xxx.hpp` + `xxx.cpp`
3. 在 `modules/Kconfig` 里定义模块开关
4. 在 `modules/CMakeLists.txt` 里加入编译条件
5. 在某个线程里实例化并接入

如果这个模块需要和别的线程交换数据，再补对应 `topic/`。

### 18.3 新增一个 topic

典型路径是：

1. 在 `topic/<name>/` 下建 `xxx.hpp` + `xxx.cpp`
2. 明确它是：
   - zbus channel
   - 还是 `k_msgq`
3. 在 `topic/Kconfig` 和 `topic/CMakeLists.txt` 注册
4. 在生产者和消费者中接入

新增 topic 时最重要的，不是先想“宏怎么写”。

而是先想：

- 这是最新状态广播，还是逐条消费队列；
- 这是模块内部细节，还是跨线程契约；
- 这个结构体里放的是原始数据，还是语义数据。

### 18.4 新增一个板子

典型路径是：

1. 在 `project/boards/<vendor>/<board_cfg>/` 下建目录
2. 放 `<board>.overlay`
3. 放 `<board>.conf`
4. 需要的话补 `board.cmake`
5. 构建时传 `-DBOARD_CFG=<board_cfg>`

### 18.5 新增一个驱动

典型路径是：

1. 在 `drivers/communication/` 或 `drivers/device/` 下建目录
2. 只做底层接口封装
3. 在 `drivers/Kconfig` 和 `drivers/CMakeLists.txt` 注册
4. 由模块层或线程层来使用

别在驱动层直接塞业务规则。

这条边界要守住。

## 19. 当前架构已经完成了什么

这套仓库现在最可贵的一点，是很多边界已经不是纸上谈兵。

### 19.1 分层不是空架子

`drivers/`

`modules/`

`algorithm/`

`topic/`

`project/thread/`

都已经有真实代码和真实调用关系。

这不是只有 README 好看、目录漂亮、实现却全空着的那种工程。

### 19.2 编译期裁剪已经成立

现在已经有完整链条：

- 根 Kconfig 门禁
- 线程 Kconfig 选择
- 下层依赖 `select`
- 各层 CMake 条件编译
- 板级 overlay / `.conf` 自动装配

这说明“按功能裁剪固件”已经不是想法，而是当前代码正在做的事。

### 19.3 启动系统已经开始平台化

当前 `REGISTER_INIT + .user_init + stage walker` 这套机制，是非常明确的平台化信号。

因为它把：

- 启动阶段
- 启动等级
- 组件自注册

这些规则从具体业务代码里提了出来。

### 19.4 Topic 契约已经有代表性样本

现在至少已经有：

- 遥控语义
- IMU 姿态结果
- CAN 发送帧

三类很不同的数据契约。

这足以说明 topic 层已经进入真实使用期。

### 19.5 真实机器人业务已经在里面

当前仓库里不是只有测试线程。

它已经包含：

- 遥控器
- IMU
- CAN
- 底盘
- 云台
- 电机
- 功率控制
- TFLM 入口

所以这不是“未来也许能用”的框架。

它已经在承载真实业务闭环。

## 20. 当前还在收敛的地方

写架构介绍，不能只夸。

也要把现在还在收敛的地方写清楚。

### 20.1 `cmd/` 还是骨架多于实体

这层位置已经有了，但命令面还没铺开。

### 20.2 编译期注册已经有了，但规则还可以继续往前推

当前虽然有 `REGISTER_INIT()`，但很多业务规则仍主要由线程文件自己约定。

后面如果继续往前走，可能还会出现更明确的：

- 启动优先级规则
- 中间阶段职责规则
- 组件注册规范

### 20.3 有些边界已经成立，有些边界还需要长期守

最容易退化的地方通常是：

- 线程层重新直接碰太多底层细节
- 模块层过重，开始承担太多业务策略
- 算法层掺进设备依赖
- topic 结构体塞太多“为了方便”的临时字段

这些都不是今天一定已经出问题。

但它们是最值得持续盯住的风险点。

### 20.4 项目层仍然是最重的装配层

这不一定是坏事。

但它意味着：

- `project/thread/` 仍然是复杂度最容易堆积的地方；
- 这层写得好不好，直接决定整套架构看起来是平台，还是又回到整机工程。

### 20.5 文件变多以后，笨重主要体现在查找成本

框架层不断沉淀之后，整体文件数量一定会增加。

一个设备可能有：

- 驱动头文件；
- 驱动实现文件；
- 模块头文件；
- 模块实现文件；
- Kconfig 条目；
- CMake 条目；
- topic 定义；
- 线程入口；
- 板级 overlay；
- 板级 `.conf`；
- 对应说明文档。

所以它带来的“笨重”，首先不是运行时笨重。

更多是：

- 文件多；
- 路径多；
- 搜索成本高；
- 第一次接触的人不容易快速定位；
- 一个功能可能需要沿着多层文件才能看完整。

这是分层和沉淀框架必然要付出的代价。

但对一个专门维护一场比赛、一个赛季、甚至一组相近机器的框架来说，这种体量反而刚刚好。

它牺牲了一点“一眼找到所有代码”的轻量感，换来了：

- 模块可以复用；
- 项目可以替换；
- 板子可以迁移；
- Demo 可以独立验证；
- 正式业务不容易被实验代码污染；
- 后续新人可以沿着目录和文档找到职责边界。

所以这里的取舍不是“文件少就是好，文件多就是坏”。

更准确的说法是：

> 文件变多会让查找变重，但只要边界稳定、项目差异收敛在 `project/`，这套体量对于比赛框架维护来说是合适的。

它不是追求做成一个庞大的通用操作系统。

也不是追求压缩成一份只能服务当前机器的单文件工程。

它处在一个很实用的位置：

- 足够沉淀一场比赛的公共能力；
- 足够支撑多个 Demo 和验证任务；
- 足够让新项目主要只改 `project/`；
- 又没有膨胀到必须维护一整套庞大平台团队才能使用。

## 21. 建议阅读顺序

如果你是第一次进这个仓库，建议别从某个随机模块开始抠。

推荐按下面顺序读：

### 21.1 第一步：看入口和启动机制

1. `src/main.c`
2. `project/apps/System_startup.h`
3. `project/apps/Init_entry.hpp`
4. `project/apps/Init_entry.cpp`
5. `cmd/linker/tflm_init.ld`

这一步读完，你会知道：

- 系统怎么起；
- 启动项怎么注册；
- 启动阶段怎么组织。

### 21.2 第二步：看编译期裁剪链

1. `Kconfig`
2. `prj.conf`
3. `CMakeLists.txt`
4. `project/thread/Kconfig`
5. `project/thread/CMakeLists.txt`

这一步读完，你会知道：

- 项目怎么被选中；
- 哪些线程怎么被编译进来；
- 板级配置怎么被找到。

### 21.3 第三步：看 topic 契约

1. `topic/remote_to/remote_to.hpp`
2. `topic/imu_to/imu_to.hpp`
3. `topic/to_can_tx/to_can_tx.hpp`

这一步读完，你会知道：

- 不同线程之间到底传什么；
- 数据是广播还是队列；
- 系统语义是怎么被表达的。

### 21.4 第四步：看典型线程

推荐顺序：

1. `project/thread/can/trd_can_tx.cpp`
2. `project/thread/remote/trd_remote.cpp`
3. `project/thread/imu/trd_imu.cpp`
4. `project/thread/chassis/trd_chassis.cpp`
5. `project/thread/gimbal/trd_gimbal.cpp`

这一步读完，你会知道：

- 模块怎么装进线程；
- topic 怎么被消费；
- 真实业务闭环怎么形成。

### 21.5 第五步：看对应模块和算法

推荐顺序：

1. `modules/remotes/`
2. `modules/imu/`
3. `modules/motors/`
4. `algorithm/controller/`
5. `algorithm/filter/`
6. `algorithm/identify/`
7. `algorithm/tflm/`

这样看，会比先钻进某个底层寄存器文件更快抓住整体结构。

## 22. 关键文件索引

最后给一个更直接的索引，方便你从 README 跳到真实代码。

### 22.1 入口与启动

- `src/main.c`
- `project/apps/System_startup.h`
- `project/apps/Init_entry.hpp`
- `project/apps/Init_entry.cpp`
- `cmd/linker/tflm_init.ld`

### 22.2 项目层

- `project/ARCHITECTURE.md`
- `project/README.md`
- `project/thread/Kconfig`
- `project/thread/CMakeLists.txt`
- `project/thread/thread.hpp`

### 22.3 线程实现

- `project/thread/can/trd_can_tx.cpp`
- `project/thread/remote/trd_remote.cpp`
- `project/thread/imu/trd_imu.cpp`
- `project/thread/chassis/trd_chassis.cpp`
- `project/thread/gimbal/trd_gimbal.cpp`
- `project/thread/pc/trd_pc.cpp`
- `project/thread/gpio/trd_gpio.cpp`
- `project/thread/tflm/trd_tflm.cpp`
- `project/thread/test/trd_test.cpp`

### 22.4 驱动层

- `drivers/ARCHITECTURE.md`
- `drivers/communication/uart/uart.hpp`
- `drivers/communication/can/can.hpp`
- `drivers/communication/usb/usb.hpp`
- `drivers/device/gpio/output.hpp`
- `drivers/device/pwm/pwm.hpp`

### 22.5 模块层

- `modules/ARCHITECTURE.md`
- `modules/imu/`
- `modules/remotes/`
- `modules/motors/`
- `modules/powermeter/`

### 22.6 算法层

- `algorithm/ARCHITECTURE.md`
- `algorithm/controller/pid/`
- `algorithm/controller/power_ctrl/`
- `algorithm/filter/kalman/`
- `algorithm/filter/quaternion/`
- `algorithm/identify/rls/`
- `algorithm/tflm/`

### 22.7 topic 层

- `topic/ARCHITECTURE.md`
- `topic/remote_to/remote_to.hpp`
- `topic/imu_to/imu_to.hpp`
- `topic/to_can_tx/to_can_tx.hpp`

### 22.8 进一步说明文档

- `doc/tflm架构分析.md`
- `doc/tflm初始化架构与编译期启动模式.md`
- `doc/三个嵌入式框架对比总结.md`

---

如果只想记住一句话，可以记这个：

> 这套仓库的核心不是“把线程跑起来”，而是把板级、驱动、模块、算法、消息和项目装配关系稳定下来，让真实机器人业务可以在 Zephyr 上按边界长期演进。
