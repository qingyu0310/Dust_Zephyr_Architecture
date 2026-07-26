# tflm

## Zephyr 模块化嵌入式框架

`tflm` 是一个基于 Zephyr 的模块化嵌入式工程，面向机器人控制、传感器处理、设备验证、参数辨识和轻量级推理等场景。

这个仓库的重点不只是把功能编译出来，而是逐步建立一套可以长期维护的工程边界：

- 板级配置和设备树绑定属于项目层；
- UART、CAN、SPI、USB、GPIO、PWM 等底层外设属于驱动层；
- IMU、遥控器、电机和功率计等设备能力属于模块层；
- PID、滤波、姿态解算、RLS、电机辨识和 TFLM 属于算法层；
- 线程之间的数据形状和传输语义属于 topic 层；
- 启动注册、变量调试和链接段扩展属于 `cmd/` 与 `project/apps/`；
- 主机侧 Python 脚本负责硬件测试、日志回放、参数辨识和性能分析。

因此，`tflm` 不是一个单纯的 CubeMX 工程，也不是一个只提供目录模板的空框架。它是一套正在由真实 IMU、Remote、CAN、电机、底盘、云台和实验脚本不断推动演进的嵌入式架构。

---

## 目录

1. [项目定位](#项目定位)
2. [一句话理解](#一句话理解)
3. [当前能力](#当前能力)
4. [快速开始](#快速开始)
5. [架构总览](#架构总览)
6. [启动链](#启动链)
7. [编译期装配](#编译期装配)
8. [目录说明](#目录说明)
9. [`cmd/` 调试命令层](#cmd-调试命令层)
10. [`algorithm/` 算法层](#algorithm-算法层)
11. [`modules/` 设备模块层](#modules-设备模块层)
12. [`topic/` 数据契约层](#topic-数据契约层)
13. [`project/` 项目装配层](#project-项目装配层)
14. [`scripts/` Python 实验层](#scripts-python-实验层)
15. [典型数据链路](#典型数据链路)
16. [新增功能应该改哪里](#新增功能应该改哪里)
17. [当前状态与边界](#当前状态与边界)
18. [推荐阅读顺序](#推荐阅读顺序)
19. [文档索引](#文档索引)

---

## 项目定位

### 这是什么

`tflm` 的目标，是把一个机器人或嵌入式控制项目拆成几类可以独立理解、独立裁剪、独立验证的能力：

```text
板级配置
    -> 驱动
        -> 设备模块
            -> 项目线程
                -> 具体业务
```

与此同时，控制器、滤波器、辨识器和推理运行时不应该被某一块板子或某一个线程锁死：

```text
算法
    -> 接收数据
    -> 更新内部状态
    -> 输出计算结果
```

线程负责把这些能力组合起来，topic 负责把线程之间的关系写出来，Kconfig/CMake 负责在构建时决定本次固件包含什么。

### 这不是什么

它不是：

- 把所有业务都写进 `main.c` 的单文件工程；
- 把所有设备都直接写进一个总控类的整机工程；
- 只依赖 IDE 工程文件维护的不可移植项目；
- 把 Python 解释器编译进 MCU 的运行时系统；
- 追求目录数量本身的形式化框架。

它更关注下面这个问题：

> 当项目换板子、换 IMU、换遥控器、换控制算法，或者需要加入一轮新的硬件实验时，应该改哪一层，而不是把整个工程重新揉成一团。

### 当前项目特点

当前仓库的架构有几个非常明显的特点：

1. 使用 Zephyr 作为 RTOS、设备树和构建基础。
2. 使用 Kconfig 和 CMake 做功能选择与源文件裁剪。
3. 使用 GCC section 和 linker script 收集初始化项、设备源、协议和调试变量。
4. 使用 `project/` 把板卡、启动和业务线程嵌入框架。
5. 使用 `topic/` 区分状态广播和逐帧队列。
6. 使用 `scripts/` 把主机侧实验正式纳入开发流程。
7. 允许通过 `project/thread/test/` 快速验证新设备和新算法。

---

## 一句话理解

可以用下面这句话理解当前架构：

> `tflm` 是一套以 Zephyr 为运行时基础、以 Kconfig/CMake 为编译期装配手段、以链接段注册为扩展机制、以 `drivers / modules / algorithm / topic / project` 为职责边界，并由 Python 脚本参与实验验证的模块化嵌入式框架。

再压缩成几个问题：

| 目录 | 它回答的问题 |
| --- | --- |
| `drivers/` | 如何访问 UART、CAN、SPI、USB、GPIO、PWM |
| `modules/` | 一个具体设备如何变成可用对象 |
| `algorithm/` | 控制、滤波、辨识和推理如何复用 |
| `topic/` | 线程之间传什么数据，采用什么传输语义 |
| `project/` | 当前项目使用哪些板子、模块和线程 |
| `cmd/` | 固件运行时如何查看和修改调试变量 |
| `scripts/` | 如何在主机侧采集、回放、拟合和分析 |
| `doc/` | 设计、问题、实验和架构演进如何沉淀 |

---

## 当前能力

当前代码已经覆盖了从底层外设到上层业务的一部分完整链路。

### 底层外设

- UART 异步/DMA 接收；
- CAN 初始化、发送和回调入口；
- SPI 设备访问；
- USB/CherryUSB 相关接入；
- GPIO 输入输出；
- PWM 输出；
- Zephyr devicetree alias 绑定。

### 设备模块

- BMI088；
- ICM42688P；
- IMU 统一数据源接口；
- IMU 静态校准；
- IMU 加热器闭环控制；
- IMU 加热开环/闭环辨识；
- DR16；
- SBUS；
- VT12；
- VT13；
- DJI C610/C620；
- DM 电机；
- 功率计。

### 算法能力

- PID；
- 功率控制；
- 一阶低通和高通滤波；
- 线性 Kalman；
- 模板化 EKF；
- 四元数姿态解算；
- RLS；
- 电机本体辨识；
- 稳定判据和波形发生器；
- RingBuffer 和 BipBuffer；
- TensorFlow Lite Micro 运行时。

### 项目线程

当前项目线程包括：

- GPIO；
- CAN TX；
- Remote；
- IMU；
- 底盘；
- 云台；
- PC/USB；
- TFLM；
- Test。

### 调试与实验

- `REGISTER_SHELL_VAR()` 调试变量注册；
- `l/g/s/?` 调试命令；
- UART 日志；
- IMU 加热在线辨识；
- 加热模型离线回放；
- PID 仿真调参；
- UART 吞吐和读取延迟测试；
- Python 日志解析、拟合、绘图和结果导出。

---

## 快速开始

### 前置环境

当前工程依赖：

- Zephyr；
- Zephyr SDK；
- CMake；
- Ninja；
- `west`；
- 对应芯片的 SDK glue；
- 如果使用 Python 实验脚本，还需要 Python 及脚本依赖。

根 `CMakeLists.txt` 当前会使用：

```text
ZEPHYR_BASE
SDK_GLUE_DIR
```

如果没有设置 `SDK_GLUE_DIR`，当前 Windows 开发环境会回退到：

```text
D:/Zephyr_HPMicro/sdk_glue
```

在其他机器上建议显式设置：

```powershell
$env:SDK_GLUE_DIR = "D:\path\to\sdk_glue"
```

Zephyr 本身应通过环境变量提供：

```powershell
$env:ZEPHYR_BASE = "D:\path\to\zephyr"
```

### 直接使用 west 构建

当前根 CMake 会根据 `BOARD` 和 `BOARD_CFG` 查找项目板级文件。

常见形式：

```powershell
west build -b hpm6e00evk
```

如果需要传入项目板级配置分组：

```powershell
west build -b stm32f4_disco -- -DBOARD_CFG=puzhong
```

具体可用的 `BOARD_CFG` 和 board 文件，以当前目录为准：

```text
project/boards/hpm/hpm5361icb/
project/boards/hpm/hpm6e00evk/
project/boards/st/board_rm_c/
project/boards/st/puzhong/
```

根 CMake 会根据下面的模式寻找文件：

```text
project/boards/*/<BOARD_CFG>/<BOARD>.overlay
project/boards/*/<BOARD_CFG>/<BOARD>.conf
project/boards/*/<BOARD_CFG>/board.cmake
```

### 使用构建脚本

PowerShell 构建入口位于：

```text
cmd/build/build.ps1
```

示例：

```powershell
powershell -ExecutionPolicy Bypass `
    -File .\cmd\build\build.ps1 `
    -Name hpm6e00evk
```

如果只需要快速指定板卡，也可以使用更简化的 `.bat` 入口：

```powershell
.\cmd\build\build.bat hpm5361icb
```

其中第一个参数是板卡名或板级配置名，建议在仓库根目录执行。比如上面的命令会以 `hpm5361icb` 作为目标开始构建。

构建脚本只是 `west build` 的封装。遇到板级路径、board 名或 SDK glue 问题时，应优先回到根 `CMakeLists.txt` 和 `project/boards/` 检查真实装配关系。

### 选择构建目录

为了避免不同板卡之间相互污染，建议为不同目标使用不同构建目录：

```powershell
west build -b hpm6e00evk -d build/hpm6e00evk
west build -b hpm5361icb -d build/hpm5361icb
```

### 配置功能

公共配置位于：

```text
prj.conf
```

项目和线程配置位于：

```text
Kconfig
project/thread/Kconfig
```

例如，打开某个线程通常不是只打开一个 C++ 文件，而是：

```text
CONFIG_TRD_IMU=y
    -> MOD_DEV_IMU
    -> TPC_IMU_TO
    -> FLT_QUATERNION
    -> SPI/PWM/PID 等依赖
```

### 编译和烧录

本 README 不固定某一块板子的烧录命令。不同板卡的烧录器、OpenOCD 路径和 `board.cmake` 配置可能不同，建议使用对应 board 的 runner 配置执行：

```powershell
west flash
```

如果 runner 路径尚未配置，先检查：

```text
project/boards/<vendor>/<board_cfg>/board.cmake
project/boards/<vendor>/<board_cfg>/openocd.cfg
```

---

## 架构总览

### 总体依赖关系

```text
                         project/
                    apps / boards / thread
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
    modules/              algorithm/              topic/
        │                     │                     │
        └──────────────┬──────┴──────┬──────────────┘
                       │             │
                   drivers/       cmd/
                       │             │
                       └──────┬──────┘
                              │
                     Zephyr / DTS / SDK glue
```

`scripts/` 不在固件依赖图中：

```text
固件
    -> UART / USB / 日志
        -> scripts/
            -> 采集 / 回放 / 拟合 / 绘图 / 报告
```

它与固件属于同一个工程闭环，但不会通过 CMake 编译进 MCU 镜像。

### 三个时间视角

读这套架构时，应该同时看三个时间点。

#### 编译期

回答：

```text
这次镜像包含哪些能力？
```

主要参与者：

- Kconfig；
- `prj.conf`；
- board `.conf`；
- CMake；
- `project/thread/CMakeLists.txt`；
- 各层 `CMakeLists.txt`。

#### 链接期

回答：

```text
各个源文件注册的初始化项、协议、设备和调试变量如何形成可遍历表？
```

当前链接段包括：

| 链接段 | 用途 |
| --- | --- |
| `.user_init` | `REGISTER_INIT()` 初始化项 |
| `.remote` | Remote 协议注册项 |
| `.imu` | IMU 数据源注册项 |
| `.can_rx1/2/3` | CAN ID 分发项 |
| `.shell_var` | 调试变量注册项 |

#### 运行时

回答：

```text
系统如何启动，线程如何运行，数据如何流动？
```

运行时由 `System_Startup()` 执行初始化表，再由各注册项启动线程。

### 编译期注册不是编译期执行

当前注册机制的真实过程是：

```text
REGISTER_xxx()
    -> 生成静态 Entry
    -> 放入指定 section
    -> linker script KEEP()
    -> 生成 start/end 边界符号
    -> 启动器运行时遍历
```

所以：

- 注册项是否存在，由编译和链接结果决定；
- 注册项什么时候执行，仍由运行时启动器决定；
- `KEEP()` 用于避免没有普通引用的注册对象被链接器回收；
- 新增注册项通常不需要修改中央分发文件。

---

## 启动链

### 根入口

根入口位于：

```text
src/main.c
```

当前 `main()` 的职责非常窄：

```text
main()
    -> System_Startup()
    -> 主循环睡眠
```

它不直接初始化 IMU、Remote、CAN、底盘或云台。

### 当前十阶段启动顺序

`project/apps/Init_entry.cpp` 当前按下面的顺序调用 `RunStage()`：

```text
PreInit
    -> PreThread
    -> EarlyInit
    -> EarlyThread
    -> MidInit
    -> MidThread
    -> LateInit
    -> LateThread
    -> AppInit
    -> AppThread
```

每一对阶段通常表达：

```text
先初始化对象或资源
    -> 再启动依赖这些资源的线程
```

当前阶段定义位于：

```text
project/apps/Init_entry.hpp
```

当前启动遍历器位于：

```text
project/apps/Init_entry.cpp
```

### 当前阶段中的典型组件

| 阶段 | 当前典型入口 |
| --- | --- |
| `PreInit` | CAN、Remote、GPIO 输出、调试控制台 |
| `PreThread` | CAN TX、Remote、GPIO 输出 |
| `EarlyInit` | IMU、GPIO 输入 |
| `EarlyThread` | IMU、GPIO 输入 |
| `MidInit` | 底盘、云台 |
| `MidThread` | 底盘、云台 |
| `LateInit` | PC/USB |
| `LateThread` | PC/USB |
| `AppInit` | Test、TFLM |
| `AppThread` | Test、TFLM、调试变量控制台 |

这张表表达的是当前主要组织方式，不意味着每个功能在所有板卡和配置下都会参与编译。

### 初始化失败等级

每个 `InitEntry` 还带有 `InitLevel`：

| 等级 | 失败行为 |
| --- | --- |
| `High` | 打印错误并停机 |
| `Mid` | 打印错误并继续 |
| `Low` | 打印告警并继续 |

这里的 `InitLevel` 主要表示失败策略，不是同一阶段内的排序键。当前启动器按阶段过滤后，按照链接段中的条目顺序执行。

### 为什么要把初始化项放回业务文件

线程文件可以在本地写：

```cpp
bool thread_init();
bool thread_start();

REGISTER_INIT(thread_init, MidInit, Mid, "chassis_init");
REGISTER_INIT(thread_start, MidThread, Mid, "chassis_start");
```

这样做的好处是：

- 初始化实现和注册信息距离更近；
- 新增线程不需要不断扩大中央 `main.c`；
- 启动失败策略可以由组件声明；
- Kconfig 未选中时，注册项也不会进入最终镜像；
- 项目层仍然能够决定哪些线程参与本次装配。

---

## 编译期装配

### 根 CMake 的作用

根 `CMakeLists.txt` 主要负责：

1. 设置当前项目目录；
2. 注册 Zephyr、SoC、board 和 SDK glue 搜索路径；
3. 根据 `BOARD`、`BOARD_CFG` 查找 overlay、`.conf` 和 `board.cmake`；
4. 进入 Zephyr app；
5. 在项目门禁打开时加入框架层和 `project/`。

当前主要变量：

```cmake
set(ACTIVE_PRJ "test")
set(PROJ_DIR project)
set(CONFIG_SYM PRJ_TEST)
```

当前项目门禁：

```text
CONFIG_PRJ_TEST
```

当它打开时，根 CMake 才会添加：

```text
drivers/
algorithm/
modules/
topic/
cmd/
project/
```

### Kconfig 的依赖传播

`project/thread/Kconfig` 是业务功能的主要入口。用户通常选择一个线程，线程再通过 `select` 拉起它需要的模块、驱动、算法和 topic。

典型依赖：

```text
TRD_REMOTE
    -> MOD_DEV_REMOTE
        -> COM_UART_DMA
        -> TPC_REMOTE_TO

TRD_IMU
    -> MOD_DEV_IMU
        -> FLT_QUATERNION
        -> TPC_IMU_TO
        -> DEV_PWM
        -> CTL_PID
        -> COM_SPI

TRD_CHASSIS
    -> MOD_CTL_POWER
    -> MOD_DEV_MOTOR_DJI
    -> TPC_TO_CAN_TX
    -> TPC_REMOTE_TO

TRD_GIMBAL
    -> CTL_PID
    -> MOD_DEV_MOTOR_DM
    -> TPC_TO_CAN_TX
    -> TPC_REMOTE_TO
```

当前 `TRD_TEST` 默认打开，并选择：

```text
COM_UART_DMA
CMD_SHELL_VAR
```

因此当前测试项目天然带有调试变量控制台的编译入口。

### CMake 的源文件纳入

Kconfig 只决定符号是否打开，CMake 还必须把对应源文件加入目标。

例如：

```cmake
if(CONFIG_TRD_IMU)
    target_sources(app PRIVATE imu/trd_imu.cpp)
endif()
```

模块、算法、驱动和 topic 也采用同样模式：

```text
Kconfig 选择能力
    -> CMake 纳入源文件
        -> 编译
            -> 链接注册段
                -> 运行时遍历
```

这就是当前架构的完整装配链。

---

## 目录说明

```text
tflm/
├── algorithm/       纯计算、控制、滤波、辨识、TFLM
├── cmd/             固件命令、调试变量、构建辅助、链接段
├── drivers/         Zephyr 外设接口封装
├── modules/         IMU、Remote、电机、功率计等设备模块
├── project/         当前项目的 apps、boards、thread
├── topic/           zbus 和 k_msgq 数据通道
├── scripts/         Python 实验和性能工具
├── src/             根入口
├── doc/             架构、问题记录和实验报告
├── include/         公共补充头文件
├── third_party/     Eigen、TFLM 依赖等第三方代码
├── build/           构建输出，不属于源代码架构
└── temp/            参考项目和历史工程，不参与当前默认编译
```

### 层之间的基本边界

| 层 | 应该放什么 | 不应该放什么 |
| --- | --- | --- |
| `drivers` | 外设初始化、收发、设备树绑定 | 业务策略、底盘模式 |
| `algorithm` | PID、EKF、RLS、数学模型 | UART、CAN、SPI 句柄 |
| `modules` | 设备协议、状态、校准、设备能力 | 整机跨模块策略 |
| `topic` | 消息结构、发布订阅、队列 | 线程循环、设备实例 |
| `project/thread` | 当前项目的周期业务和对象装配 | 可复用底层能力 |
| `cmd` | 运行时调试入口、构建辅助、注册扩展 | 正式控制业务 |
| `scripts` | 主机侧采集、分析、回放、拟合 | MCU 实时安全逻辑 |

判断一段代码应该放哪一层时，可以先问：

```text
它是在提供能力，
还是在把已有能力组合成当前项目行为？
```

前者通常属于框架层，后者通常属于 `project/`。

---

## `cmd/` 调试命令层

### `cmd/` 的三类内容

当前 `cmd/` 包含三类职责：

```text
cmd/shell/
    固件内调试变量控制台

cmd/linker/
    初始化、协议、设备和调试变量链接段

cmd/build/
    west build 的开发机侧封装
```

它们虽然位于同一个目录下，但运行时间不同：

| 目录 | 运行位置 | 是否进入固件 |
| --- | --- | --- |
| `shell/` | MCU 运行时 | 是，按 Kconfig |
| `linker/` | 编译/链接阶段 | 是，作为链接配置 |
| `build/` | PC 开发机 | 否 |

### 调试变量控制台

当前实现位于：

```text
cmd/shell/shell.hpp
cmd/shell/shell.cpp
```

它不是 Zephyr 官方 shell 的直接使用，而是一个轻量的自维护 UART 调试线程。

核心思路：

```text
业务代码
    -> REGISTER_SHELL_VAR("name", variable)

编译期
    -> 生成 .shell_var Entry
    -> linker KEEP()

运行时
    -> DbgConsole 遍历 __shell_var_start/end
    -> UART DMA 接收一行命令
    -> l/g/s/? 处理变量
```

### 当前命令

| 命令 | 作用 |
| --- | --- |
| `l` | 列出所有注册变量、类型和值 |
| `g <name>` | 读取变量 |
| `s <name> <value>` | 修改变量 |
| `?` 或 `h` | 打印帮助 |

示例：

```text
l
g test_value
s test_value 1.25
s test_flag true
```

变量类型由 `TypeMap<decltype(var)>` 自动推导，当前支持：

```text
uint8_t / int8_t
uint16_t / int16_t
uint32_t / int32_t
uint64_t / int64_t
float / double / bool
```

### 当前调试变量注册

`project/thread/test/trd_test.cpp` 当前注册了：

```text
test_loop
test_value
test_flag
test_signal
obj_value
obj_flag
obj_fader
```

注册形式：

```cpp
static float test_value;
REGISTER_SHELL_VAR("test_value", test_value);
```

也可以注册类成员：

```cpp
static TestObj test_obj;
REGISTER_SHELL_VAR("obj_value", test_obj.value);
```

### UART 和线程边界

调试控制台当前：

- 使用 `UartDma`；
- 绑定 `DT_CHOSEN(zephyr_console)`；
- 使用 DMA 接收和信号量唤醒；
- 自己持有一个轻量线程；
- 使用 `LOG_INF` 输出结果；
- 通过 `CONFIG_CMD_SHELL_VAR` 控制是否编译。

它没有放进 `project/thread/`，是因为它本质上是一个跨业务的调试基础设施。它需要自己的 UART 资源和线程，但不属于某个机器人业务线程。

### 为什么没有直接使用 Zephyr shell

当前方案更关注：

- 复用已有 console；
- 不引入完整 shell 命令树；
- 让变量注册只需要一行宏；
- 让类型在编译期推导；
- 用链接段避免中心化变量表；
- 保持调试功能轻量。

因此它适合参数和状态调试，不适合替代复杂的正式命令协议。如果未来需要子命令、权限、命令补全或复杂参数解析，再考虑引入更完整的 shell 层。

---

## `algorithm/` 算法层

`algorithm/` 只处理数据和数学状态，不应该持有具体硬件句柄。

当前主要目录：

```text
algorithm/
├── buffer/
├── controller/
├── filter/
├── identify/
├── math/
└── tflm/
```

### controller

当前包括：

- PID；
- 功率控制；
- 软件定时器；
- 执行时间测量。

PID 可以被底盘、云台、IMU 加热器或主机侧仿真分别使用，而不需要知道输出最终是 PWM、CAN 电流还是一个离线数组。

### filter

当前包括：

- LPF；
- HPF；
- Kalman；
- EKF；
- 四元数姿态解算。

当前姿态方向逐步采用：

```text
通用 EKF
    -> 提供矩阵、预测、更新和协方差处理

Quaternion
    -> 提供姿态状态、观测模型和欧拉角输出
```

### identify

当前包括：

- RLS；
- 电机本体辨识；
- 稳定判据；
- 波形发生器。

这些能力既可以被正式线程调用，也可以被 `project/thread/test` 连接到真实设备实验中。

### tflm

`algorithm/tflm/` 是 MCU 端 TensorFlow Lite Micro 运行时和相关移植代码。

它负责：

- 模型解释；
- 张量和 arena 内存；
- 算子；
- TFLM 运行封装；
- 模型数据编译接入。

模型训练、数据清洗和复杂分析不属于 MCU 端 TFLM 线程，而属于主机侧工具链。

### 算法层的边界

正确的数据关系应该是：

```text
线程/模块
    -> 提供输入
        -> algorithm 计算
            -> 返回结果
                -> 线程/模块决定如何使用
```

不应该让 PID 直接初始化 PWM，也不应该让 EKF 直接读取 SPI。

---

## `modules/` 设备模块层

`modules/` 位于驱动层之上、项目线程之下。

它负责把多个底层能力组合成一个设备对象：

```text
SPI + 寄存器 + 校准 + 单位换算
    -> IMU 数据源

UART DMA + 协议探测 + 解码
    -> Remote 模块

CAN + 电机协议 + 状态转换
    -> 电机模块
```

### 当前模块

```text
modules/
├── imu/
├── motors/
├── powermeter/
└── remotes/
```

### IMU 前后端

IMU 当前被拆成两部分：

```text
前端
    -> 具体芯片寄存器读取
    -> 原始值转换
    -> 校准
    -> 输出统一 Sample

后端
    -> ImuManager
    -> 加热控制
    -> 姿态解算
    -> topic 消息生成
    -> 线程运行
```

具体设备实现 `Source` 或 `CalibratedImuSource`，上层只消费统一的：

```cpp
struct Sample
{
    float gyro[3];
    float accel[3];
    float temp;
    float dt;
};
```

当前 IMU 数据源使用：

```cpp
REGISTER_IMU(Bmi088, bmi088);
REGISTER_IMU(Icm42688p, icm42688p);
```

链接后由 `ImuManager` 遍历 `.imu` 段并选择数据源。当前单个镜像要求最终只能有一个有效 IMU 注册项。

### Remote 单 UART

当前 Remote 的实际结构是：

```text
一个 UartDma
    -> 一个 Remote
        -> Detecting / Locked
            -> Validate / Decode
                -> remote_to
```

协议通过：

```cpp
REGISTER_REMOTE(...)
```

收集到 `.remote` 链接段。当前协议包括：

- DR16；
- SBUS；
- VT12；
- VT13。

锁定后的热路径尽量保持：

```text
Read
    -> Decode
        -> Publish
```

协议探测、UART 线路配置切换和失败恢复属于冷路径。

### Remote 双 UART

双 UART 目前仍然是设计规划，不是当前 live tree 的实现。

规划目标是：

```text
UART A + UART B
    -> 一个 Remote
        -> 一个解析线程
            -> 当前活动 UART
                -> 一个 remote_to 输出
```

规划中的核心数据：

```text
uart_[2]
detect_[2]
uart_idx_
```

切换时需要处理：

- 当前源超时；
- 另一 UART 是否有数据；
- 目标 UART 是否初始化成功；
- stale FIFO；
- 共享帧缓冲清理；
- 协议探测进度；
- anti-flap 冷却。

详细规划见：

```text
doc/remote_dual_arch.md
```

### 模块层不应该承担什么

模块层不应该知道：

- 当前机器人什么时候自旋；
- 底盘如何进行运动学解算；
- 云台如何决定开火；
- 当前项目的业务状态机；
- 多个模块之间的整机策略。

模块提供能力，`project/thread/` 负责组合能力。

---

## `topic/` 数据契约层

`topic/` 用于明确线程之间传什么数据，以及数据采用什么传输语义。

当前通道：

```text
topic/
├── imu_to/
├── remote_to/
└── to_can_tx/
```

### remote_to

`remote_to` 发布遥控器的语义数据：

```text
chassisx
chassisy
yaw
pitch
chassis_mode
shoot_ctrl
reload_ctrl
autoaim_ctrl
supercap_ctrl
version
```

底盘和云台不需要知道 DR16、SBUS 或 VT13 的字节布局。

### imu_to

`imu_to` 传递：

```text
quaternion
gyro
temperature
roll
pitch
yaw
yaw_total
```

当前 topic 定义已经存在，但 IMU 后端中的实际发布调用仍需以 live tree 为准。阅读时要区分“通道定义存在”和“当前项目已经持续发布”这两个事实。

### to_can_tx

`to_can_tx` 当前使用 `k_msgq`：

```cpp
struct Message
{
    uint16_t tx_id;
    uint8_t  data[8];
};
```

底盘和云台将待发送帧放入队列，CAN TX 线程统一取出并调用 CAN driver。

### zbus 与 k_msgq

| 机制 | 适合的数据 |
| --- | --- |
| zbus | 状态广播、最新值、多个观察者 |
| k_msgq | 逐条保留的事件、发送帧、点对点队列 |

如果多个发布者向同一个通道写入最新状态，zbus 语义通常比较合适；如果每一帧都必须排队发送，消息队列更合适。

### topic 的边界

topic 不负责：

- 创建设备；
- 读取 UART；
- 运行控制循环；
- 进行运动学；
- 保存模块内部状态。

它只负责把数据形状、方向和传输方式写清楚。

---

## `project/` 项目装配层

`project/` 是框架内嵌的项目单元，不是一个游离在框架之外的独立工程。

根 CMake 通过：

```cmake
add_subdirectory(${PROJ_DIR})
```

把它和 `drivers`、`modules`、`algorithm`、`topic`、`cmd` 一起编译进同一个 Zephyr app。

### 三个部分

```text
project/
├── apps/
├── boards/
└── thread/
```

#### apps

负责：

- 系统入口；
- 初始化表遍历；
- 初始化失败处理；
- 中断回调入口；
- 链接段边界的运行时使用。

#### boards

负责：

- devicetree overlay；
- alias；
- pinctrl；
- UART/CAN/SPI/PWM/GPIO 节点；
- 板级 Kconfig；
- 烧录器和 OpenOCD 配置。

板级差异应该通过语义 alias 暴露，例如：

```text
remote-uart
imu-spi
imu-pwm
user-can1
```

模块不应该因为换板子就改成直接访问 UART3、SPI2 或某个固定 GPIO。

#### thread

负责：

- 当前项目有哪些运行线程；
- 模块如何实例化；
- 线程周期；
- topic 如何消费和发布；
- 控制链如何组合；
- 测试功能如何接入。

### 为什么项目层要嵌入框架

这套架构希望把变化分成两类：

```text
公共能力变化
    -> drivers / modules / algorithm / topic

当前项目变化
    -> project/apps / boards / thread
```

如果新增一个项目只需要换板卡、选择另一组线程和重新装配已有模块，那么框架层就可以保持稳定。

如果新增项目引入了全新的 IMU、总线或算法，再把新增能力沉淀回对应框架层。

### project/thread/test

`project/thread/test/` 是正式的实验入口，不是随手丢代码的垃圾桶。

它适合：

- 新设备验证；
- 新协议抓帧；
- 电机开环；
- RLS 或模型辨识；
- 调试变量注册；
- 新算法试跑；
- 还没有准备好进入正式业务线程的功能。

验证稳定后，再把通用部分移动到：

```text
algorithm/
modules/
topic/
drivers/
```

---

## `scripts/` Python 实验层

`scripts/` 不会编译进 MCU。它运行在 PC 上，但属于当前嵌入式工程的正式实验配套。

### 当前脚本

```text
scripts/imu/imu_temp_identify.py
scripts/imu/imu_open_loop_replay.py
scripts/imu/imu_closed_loop_identify.py
scripts/imu/stage_fit.py
scripts/imu/tune_imu_pid.py
scripts/uart/uart_perf.py
```

### IMU 加热辨识

固件侧：

```text
Identifier
    -> OpenIdent / ClosedIdent / Stop
    -> 读取温度
    -> 更新 PWM duty
    -> 输出带时间戳和阶段的日志
```

Python 侧：

```text
串口或日志文件
    -> 解析 seq/t_us/dt_us/stage/state/temp_c/duty
    -> 数据质量检查
    -> 曲线拟合或模型回放
    -> 输出参数、图像和误差
```

### 主要脚本职责

| 脚本 | 用途 |
| --- | --- |
| `imu_temp_identify.py` | 多阶段温度曲线拟合和数据质量检查 |
| `imu_open_loop_replay.py` | 用固定模型回放开环日志 |
| `imu_closed_loop_identify.py` | 根据闭环 duty/温度日志估计模型 |
| `stage_fit.py` | 分阶段提取和拟合温度曲线 |
| `tune_imu_pid.py` | 在主机侧仿真固件 PID 和热对象 |
| `uart_perf.py` | UART 吞吐、读取次数和延迟测试 |

### 为什么脚本要支持在线和离线

在线模式适合：

```text
连接真实板卡
    -> 下发命令
    -> 接收日志
    -> 现场观察
```

离线模式适合：

```text
保存原始日志
    -> 多次重复分析
    -> 比较不同参数
    -> 复现问题
```

如果脚本只能在线运行，一次实验失败后就很难复盘。当前脚本普遍保留在线和离线两种入口。

### 脚本与固件的边界

Python 可以负责：

- 拟合；
- 回放；
- 统计；
- 可视化；
- 参数搜索；
- 生成实验报告。

但 Python 不能替代固件里的：

- 超温保护；
- 失联保护；
- 电机限幅；
- 实时状态机；
- 关键故障处理。

固件负责实时安全，脚本负责实验效率。

---

## 典型数据链路

### IMU 数据链

```text
project/boards/<board>.overlay
    -> imu-spi / imu-pwm alias

drivers/spi + drivers/pwm
    -> 底层外设接口

modules/imu/devices/bmi088 或 icm42688p
    -> Source
    -> ReadRaw
    -> 单位换算和校准

modules/imu/drivers/imu
    -> ImuManager
    -> Heater
    -> Processor
    -> Quaternion EKF

topic/imu_to
    -> 其他线程消费姿态消息
```

### Remote 数据链

```text
project/boards/<board>.overlay
    -> remote-uart alias

drivers/uart
    -> UartDma

modules/remotes
    -> Remote
    -> 协议探测
    -> DR16/SBUS/VT12/VT13 Decode

topic/remote_to
    -> 底盘 / 云台线程
```

### 底盘控制链

```text
remote_to
    -> ReadRemote
        -> 运动学解算
            -> 角度/速度/力矩控制
                -> 功率预测和分配
                    -> to_can_tx
                        -> CAN TX thread
                            -> CAN driver
```

### 云台控制链

```text
remote_to
    -> yaw/pitch target
        -> DM motor feedback
            -> position PID
                -> omega PID
                    -> MIT CAN frame
                        -> gimbal_tx queue
```

### 调试变量链

```text
project/thread/test
    -> REGISTER_SHELL_VAR()
        -> .shell_var
            -> DbgConsole
                -> UartDma
                    -> l/g/s/?
```

### Python 实验链

```text
scripts/imu/*.py
    -> UART command
        -> modules/imu/heater Identifier
            -> PWM + temperature log
                -> Python parser
                    -> fit / replay / plot
```

---

## 新增功能应该改哪里

### 新增底层外设

修改：

```text
drivers/communication/ 或 drivers/device/
drivers/Kconfig
drivers/CMakeLists.txt
project/boards/<vendor>/<board_cfg>/*.overlay
```

原则：

- 使用 Zephyr device/devicetree API；
- 不在驱动层实现业务策略；
- 不直接把具体机器人逻辑写进 UART/CAN/SPI 封装。

### 新增 IMU

修改：

```text
modules/imu/devices/<name>/
modules/Kconfig
modules/CMakeLists.txt
project/boards/<vendor>/<board_cfg>/<board>.overlay
```

实现：

1. 继承 `Source` 或 `CalibratedImuSource`；
2. 实现设备初始化；
3. 实现原始数据读取；
4. 实现单位换算；
5. 配置校准参数；
6. 使用 `REGISTER_IMU()` 注册；
7. 让 `ImuManager` 复用统一后端。

不要把新 IMU 的选择逻辑写进 `Init_entry.cpp`。

### 新增遥控协议

修改：

```text
modules/remotes/<protocol>/
modules/Kconfig
modules/CMakeLists.txt
```

实现：

1. 继承 `RemoteProtocol`；
2. 配置 UART line；
3. 实现 `Validate()`；
4. 实现 `Decode()`；
5. 转换为 `remote_to::Message`；
6. 使用 `REGISTER_REMOTE()` 注册。

不要让底盘线程直接解析协议字节。

### 新增算法

修改：

```text
algorithm/<category>/<name>/
algorithm/Kconfig
algorithm/CMakeLists.txt
```

算法不应该：

- 创建 Zephyr 线程；
- 持有 UART/CAN/SPI/PWM 句柄；
- 依赖某个具体项目；
- 直接访问设备树。

### 新增 topic

先判断数据语义：

```text
状态广播、最新值
    -> zbus

逐条保留、点对点队列
    -> k_msgq
```

然后补齐：

```text
topic/<name>/<name>.hpp
topic/<name>/<name>.cpp
topic/Kconfig
topic/CMakeLists.txt
```

消息结构应该明确单位、更新者、消费者、超时和覆盖语义。

### 新增项目线程

修改：

```text
project/thread/<name>/trd_<name>.cpp
project/thread/Kconfig
project/thread/CMakeLists.txt
```

线程通常包含：

```cpp
bool thread_init();
bool thread_start();

REGISTER_INIT(thread_init, ..., ..., "xxx_init");
REGISTER_INIT(thread_start, ..., ..., "xxx_start");
```

项目线程负责周期和组合，不负责重新发明底层设备抽象。

### 新增调试变量

在业务或测试文件中：

```cpp
static float gain = 1.0f;
REGISTER_SHELL_VAR("gain", gain);
```

确认：

```text
CONFIG_CMD_SHELL_VAR=y
```

之后通过：

```text
l
g gain
s gain 2.5
```

进行调试。

调试变量适合实验和参数验证。正式业务参数如果需要复杂校验、持久化或权限控制，应设计独立配置接口，不要无限扩大裸变量写入范围。

### 新增 Python 测试

建议先定义：

1. 固件发送什么命令；
2. 固件输出什么事件；
3. 样本字段和单位是什么；
4. 什么条件代表完成；
5. 什么条件代表安全停止；
6. 离线日志如何保存；
7. 结果如何评价。

脚本应该识别真实事件，例如：

```text
imu ready
Cooldown Done
Stage Done
Finished
Safety Stop
```

不要只用固定睡眠时间假设固件一定已经完成。

---

## 当前状态与边界

### 已经落地

- Zephyr + CMake + Kconfig 基础工程；
- `project/` 嵌入式项目单元；
- 十阶段初始化遍历；
- `.user_init` 链接段；
- IMU 数据源注册；
- Remote 协议注册；
- `.remote` 和 `.imu` 链接段；
- UART/SPI/CAN/PWM/GPIO 驱动封装；
- IMU 前后端拆分；
- Remote 单 UART 自动协议探测；
- zbus 和 k_msgq topic；
- `cmd/shell` 调试变量控制台；
- `.shell_var` 链接段；
- Python IMU 和 UART 实验脚本。

### 基础设施已经存在，但仍需继续接入

- CAN RX section 分发基础设施；
- 更多设备的 `CAN_RX_HANDLER()` 注册；
- 更完整的固件状态命令；
- IMU topic 的稳定发布闭环；
- 多项目选择和更完整的项目配置抽象；
- 更完整的命令帮助和参数校验。

### 规划中

- Remote 双 UART 冗余输入；
- 两个 UART 的协议探测和切换事务；
- stale FIFO 清理；
- anti-flap 切换冷却；
- 更统一的实验命令协议；
- 更完整的主机侧脚本组织。

### 需要留意的构建现状

当前工程仍处于持续收敛阶段，部分历史文件或脚本可能保留旧路径、旧命名或旧配置约定。遇到构建问题时，优先以以下 live tree 文件为准：

```text
CMakeLists.txt
Kconfig
prj.conf
project/thread/Kconfig
project/thread/CMakeLists.txt
project/boards/
cmd/linker/tflm_init.ld
```

不要只根据旧文档中的 `projects/`、旧启动函数或旧板级目录判断当前工程。

---

## 推荐阅读顺序

### 第一步：先看根入口

```text
src/main.c
CMakeLists.txt
Kconfig
prj.conf
```

先回答：

- 当前项目是否参与编译；
- 当前 board 如何进入 CMake；
- 当前公共配置是什么；
- 系统入口在哪里。

### 第二步：看启动和链接段

```text
project/apps/Init_entry.hpp
project/apps/Init_entry.cpp
cmd/linker/tflm_init.ld
```

先理解：

```text
REGISTER_INIT
    -> .user_init
        -> RunStage
```

再理解其他注册段。

### 第三步：看编译期功能选择

```text
project/thread/Kconfig
project/thread/CMakeLists.txt
drivers/Kconfig
algorithm/Kconfig
modules/Kconfig
topic/Kconfig
cmd/Kconfig
```

### 第四步：看调试入口

```text
cmd/shell/shell.hpp
cmd/shell/shell.cpp
project/thread/test/trd_test.cpp
cmd/README.md
```

重点理解：

```text
REGISTER_SHELL_VAR
    -> .shell_var
        -> l/g/s/?
```

### 第五步：看 topic

```text
topic/remote_to/
topic/imu_to/
topic/to_can_tx/
```

先搞清楚线程之间传什么，再去看生产者和消费者。

### 第六步：看 drivers 和 modules

推荐顺序：

```text
drivers/communication/stream/uart/
drivers/communication/spi/
drivers/communication/can/
modules/remotes/
modules/imu/
modules/motors/
```

### 第七步：看项目线程

```text
project/thread/can/
project/thread/remote/
project/thread/imu/
project/thread/chassis/
project/thread/gimbal/
project/thread/test/
```

### 第八步：看 Python 实验层

```text
scripts/imu/
scripts/uart/
```

最后再回到对应固件模块，理解一次完整的硬件闭环。

---

## 文档索引

### 架构总览

- [tflm 架构详解](doc/tflm架构详解.md)
- [四个嵌入式框架对比总结](doc/四个嵌入式框架对比总结.md)
- [项目层架构说明](project/ARCHITECTURE.md)
- [驱动层架构说明](drivers/ARCHITECTURE.md)
- [算法层架构说明](algorithm/ARCHITECTURE.md)
- [模块层架构说明](modules/ARCHITECTURE.md)
- [Topic 层架构说明](topic/ARCHITECTURE.md)
- [命令层说明](cmd/README.md)

### 重点设计

- [Remote 双 UART 架构规划](doc/remote_dual_arch.md)

### 实验和验证

- `scripts/imu/imu_temp_identify.py`
- `scripts/imu/imu_open_loop_replay.py`
- `scripts/imu/imu_closed_loop_identify.py`
- `scripts/imu/stage_fit.py`
- `scripts/imu/tune_imu_pid.py`
- `scripts/uart/uart_perf.py`

---

## 最后的判断

这套架构最重要的地方，不是目录看起来有多少层，而是每一层开始承担不同的责任：

```text
drivers
    提供硬件访问

modules
    提供设备能力

algorithm
    提供纯计算

topic
    提供数据契约

project
    组合成具体项目

cmd
    提供固件内调试控制面

scripts
    提供主机侧实验控制面
```

当前架构还在继续演进，但主线已经比较清楚：

```text
编译期决定包含什么
    -> 链接期收集扩展点
        -> 启动器按阶段执行
            -> 项目线程组合模块
                -> topic 传递数据
                    -> Python 脚本参与验证
```

如果只是想快速找到代码，可以从本 README 开始。

如果想理解完整架构、IMU 前后端、Remote 双 UART 规划、初始化注册、topic 语义和 Python 实验闭环，继续阅读：

```text
doc/tflm架构详解.md
```
