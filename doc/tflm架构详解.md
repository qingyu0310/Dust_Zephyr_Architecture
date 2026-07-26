# tflm 架构详解

> 本文只讲当前 `tflm` 仓库自身的架构。
>
> 重点覆盖：
>
> - `cmd/`
> - `algorithm/`
> - `modules/`
> - `project/`
> - `topic/`
> - `scripts/`
>
> 其中，`scripts/` 不是固件运行时目录，而是与固件配合使用的主机侧 Python 测试、辨识、回放和性能分析工具。
>
> 本文同时区分：
>
> - 当前已经在代码中落地的结构；
> - 已经有接口但仍在收敛的结构；
> - 目前只存在于 `doc/` 规划中的结构。

> **事实边界说明**
>
> 本文描述的是当前工作区代码快照，而不是一份只描述目标架构的设计宣言。
> 为了避免把愿景误写成现状，全文使用下面三种状态：
>
> | 状态 | 含义 |
> | --- | --- |
> | **已落地** | 当前源码、CMake、Kconfig 或链接脚本中已经存在对应实现 |
> | **基础设施已落地** | 扩展点或接口已经存在，但当前业务使用还不完整 |
> | **规划中** | 主要记录在 `doc/`，当前实现尚未形成可验证闭环 |
>
> 例如：`REGISTER_INIT()` 和 `.user_init` 属于已落地机制；`CAN_RX_HANDLER()` 的分发基础设施已经存在，但当前业务代码还没有搜索到实际注册项；Remote 双 UART 属于规划中的扩展，不属于当前单 UART 实现。

---

## 目录

1. [这套架构到底是什么](#一这套架构到底是什么)
2. [先建立三个视角](#二先建立三个视角)
3. [顶层目录与总体依赖](#三顶层目录与总体依赖)
4. [编译期、链接期和运行时如何接起来](#四编译期链接期和运行时如何接起来)
5. [`cmd/`：运行时命令、构建辅助和链接器扩展](#五cmd运行时命令构建辅助和链接器扩展)
6. [`algorithm/`：纯计算能力与可离线验证能力](#六algorithm纯计算能力与可离线验证能力)
7. [`modules/`：从硬件接口到设备能力](#七modules从硬件接口到设备能力)
8. [IMU 模块：前端数据源与后端处理链](#八imu-模块前端数据源与后端处理链)
9. [Remote 模块：当前单 UART 协议自动识别](#九remote-模块当前单-uart-协议自动识别)
10. [Remote 双 UART：规划中的冗余输入架构](#十remote-双-uart规划中的冗余输入架构)
11. [`project/`：嵌入到框架中的项目单元](#十一project嵌入到框架中的项目单元)
12. [`project/apps/`：初始化与中断的编译期组织](#十二projectapps初始化与中断的编译期组织)
13. [`project/boards/`：项目如何绑定具体板卡](#十三projectboards项目如何绑定具体板卡)
14. [`project/thread/`：把模块组合成真实业务](#十四projectthread把模块组合成真实业务)
15. [`topic/`：线程间数据契约](#十五topic线程间数据契约)
16. [`scripts/`：嵌入式工程外的 Python 实验层](#十六scripts嵌入式工程外的-python-实验层)
17. [固件和 Python 脚本如何协作](#十七固件和-python-脚本如何协作)
18. [从一个真实功能看完整调用链](#十八从一个真实功能看完整调用链)
19. [新增功能时每一层应该怎么改](#十九新增功能时每一层应该怎么改)
20. [当前架构的优点和代价](#二十当前架构的优点和代价)
21. [当前仍然需要继续收敛的地方](#二十一当前仍然需要继续收敛的地方)
22. [推荐源码阅读顺序](#二十二推荐源码阅读顺序)
23. [关键文件索引](#二十三关键文件索引)
24. [最终总结](#二十四最终总结)

---

## 一、这套架构到底是什么

### 1.1 一句话定义

`tflm` 可以理解成：

> 以 Zephyr 为运行时基础，以 Kconfig/CMake 为编译期装配手段，以链接段注册为初始化和中断扩展手段，以 `drivers / modules / algorithm / topic / project` 为主要职责边界，并允许主机侧 Python 脚本参与硬件测试和参数辨识的一套模块化嵌入式工程。

这句话里有几个关键词。

### 1.2 它不是只有固件代码

传统嵌入式工程通常只关注这一条链：

```text
源代码
    -> 编译
    -> 下载
    -> 运行
```

当前 `tflm` 还额外把主机侧实验纳入了工程结构：

```text
固件代码
    -> 运行在 MCU
    -> 采集传感器或控制器日志
    -> 通过 UART/USB 输出稳定格式
    -> Python 脚本接收、解析和拟合
    -> 得到参数、模型或诊断结论
    -> 再回到固件验证
```

因此它不是把 Python 脚本当成一个仓库外的临时文件夹。

`scripts/` 实际上承担了：

- 硬件在环测试；
- 设备协议回放；
- 参数辨识；
- 控制器仿真；
- 日志质量检查；
- 串口性能测试；
- 拟合结果可视化；

这些内容不能全部放进 MCU。

但它们又和 MCU 固件的日志、命令、状态机紧密相关。

所以 `scripts/` 是这套架构的“实验与验证侧”。

### 1.3 它也不是普通的 Zephyr 示例工程

Zephyr 负责提供：

- 内核；
- 线程；
- 信号量；
- 消息队列；
- 设备树；
- 驱动模型；
- Kconfig；
- logging；
- shell；

但当前仓库并不是把 Zephyr API 直接散落到所有业务代码里。

它在 Zephyr 之上又增加了自己的组织层：

```text
Zephyr
    -> drivers
        -> modules
            -> project/thread
```

同时还有两条横向能力：

```text
algorithm
    纯计算和模型

topic
    线程间数据契约
```

再由：

```text
cmd/linker
project/apps
Kconfig
CMake
```

把这些内容接成一个可选择、可注册、可启动的固件。

### 1.4 这套架构真正想解决的问题

它不是单纯想解决：

```text
“代码能不能跑”
```

而是想解决：

```text
“以后换项目、换板子、换设备、换算法、换实验方法时，应该改哪里”
```

这会自然引出几条边界：

- 板卡变化，优先落到 `project/boards/`；
- 硬件外设变化，优先落到 `drivers/`；
- 具体设备协议变化，优先落到 `modules/`；
- 数学模型变化，优先落到 `algorithm/`；
- 线程间数据变化，优先落到 `topic/`；
- 业务组合和控制周期变化，优先落到 `project/thread/`；
- 运行时人工调试入口，优先落到 `cmd/`；
- PC 侧测试、辨识和回放，优先落到 `scripts/`。

这套分工不可能永远绝对。

但它提供了一个非常重要的默认判断规则。

---

## 二、先建立三个视角

读 `tflm` 时，最容易把不同层次混在一起。

建议先分清三个视角：

1. 编译期；
2. 链接期；
3. 运行时。

### 2.1 编译期视角

编译期回答：

```text
这次固件要包含哪些能力？
```

主要参与者是：

- `Kconfig`；
- `prj.conf`；
- `project/thread/Kconfig`；
- 各层 `Kconfig`；
- 根 `CMakeLists.txt`；
- 各层 `CMakeLists.txt`；
- board overlay 和 `.conf`。

例如：

```text
CONFIG_TRD_IMU=y
    -> 选择 MOD_DEV_IMU
    -> 选择 TPC_IMU_TO
    -> 选择 FLT_QUATERNION
    -> 选择 SPI/PWM/PID 等依赖
    -> CMake 加入 IMU 源文件
```

编译期的目标是：

- 没选中的能力不进入镜像；
- 依赖关系尽量由配置表达；
- 新项目可以选择不同线程集合；
- 不同板卡可以加载不同设备树和配置。

### 2.2 链接期视角

链接期回答：

```text
编译进来的初始化项、协议、设备和中断处理器，如何形成可遍历的表？
```

当前使用链接段的地方包括：

- `.user_init`；
- `.can_rx1`；
- `.can_rx2`；
- `.can_rx3`；
- `.remote`；
- `.imu`。

典型链路是：

```text
源文件中的注册宏
    -> 特定 section
    -> linker script KEEP()
    -> section 起止符号
    -> 运行时遍历
```

这种机制的作用不是炫技。

它解决的是：

```text
新增一个协议或设备时，是否必须修改一个中央分发文件？
```

如果答案是“不一定”，系统就有了更好的扩展边界。

### 2.3 运行时视角

运行时回答：

```text
系统上电后，谁先初始化？
谁启动线程？
线程之间传什么？
数据最终流向哪里？
```

当前入口是：

```text
src/main.c
    -> System_Startup()
        -> RunStage(Bsp)
        -> RunStage(ThreadEarly)
        -> RunStage(Module)
        -> RunStage(ThreadMid)
        -> RunStage(ThreadLate)
```

运行时阶段是固定的。

但开发时不要求所有代码都必须按照这条顺序编写。

可以先写：

- 一个纯算法；
- 一个设备驱动；
- 一个 topic；
- 一个测试线程；
- 一个 Python 回放工具；

再把它接入编译期和运行时系统。

因此：

> 运行时启动顺序是架构约束，开发顺序是依赖驱动的弹性过程。

### 2.4 三个视角的关系

把三者合起来就是：

```text
Kconfig/CMake
    决定“有什么”

Linker section
    决定“怎么集中发现”

System_Startup/Thread
    决定“什么时候运行”
```

这是理解 `tflm` 的关键骨架。

---

## 三、顶层目录与总体依赖

### 3.1 当前顶层目录

当前仓库中与架构直接相关的目录可以整理成：

```text
tflm/
├── algorithm/
├── cmd/
├── drivers/
├── modules/
├── topic/
├── project/
├── scripts/
├── src/
├── include/
├── doc/
├── cmd/linker/
├── CMakeLists.txt
├── Kconfig
└── prj.conf
```

这里的 `scripts/` 需要单独强调：它虽然与固件共同存在于仓库，但不会通过根 `CMakeLists.txt` 进入 `app` 目标，也不会被编译进 MCU 镜像。它是开发机侧的实验、回放和验证层。相反，`cmd/linker/` 虽然也位于一个名为 `cmd` 的目录树下，却会通过 `project/CMakeLists.txt` 被加入 Zephyr 的 ROM section 链接配置，属于固件构建链的一部分。

### 3.2 每个目录的核心问题

| 目录 | 它主要回答的问题 |
| --- | --- |
| `drivers/` | 怎么访问 UART、CAN、SPI、USB、GPIO、PWM |
| `modules/` | 一个具体设备或设备能力如何封装 |
| `algorithm/` | 纯计算、控制、滤波、辨识如何复用 |
| `topic/` | 线程之间传递什么数据，采用什么通道语义 |
| `project/` | 当前项目需要哪些板子、线程和启动组织 |
| `cmd/` | 运行时如何人工查看和修改系统 |
| `scripts/` | 如何在主机上测试、辨识、回放和分析 |
| `doc/` | 设计、规划、整改和研究过程如何沉淀 |
| `src/` | 根入口如何进入项目启动器 |

### 3.3 主要依赖方向

理想依赖方向是：

```text
project/
    -> topic/
    -> modules/
    -> algorithm/
    -> drivers/
    -> cmd/

modules/
    -> drivers/
    -> algorithm/
    -> topic/（必要时）

algorithm/
    -> 数学库和自身子模块

topic/
    -> Zephyr zbus/k_msgq

drivers/
    -> Zephyr device/devicetree/driver API

cmd/
    -> shell + algorithm/modules/topic 接口
```

需要特别注意：

```text
框架层不应该反向依赖 project/
```

这条规则是项目可移植性的基础。

### 3.4 依赖方向不是目录上下关系

目录排在前面，不代表运行时一定先执行。

例如：

```text
project/thread/chassis
    可能调用
algorithm/controller/power_ctrl
```

但 `power_ctrl` 并不因此属于底盘业务。

它仍然是一个可复用的算法控制器。

同样：

```text
modules/imu
    可能使用
drivers/spi
drivers/pwm
algorithm/filter/quaternion
```

但 IMU 模块不应该把底盘策略放进去。

所以判断层边界时，应该问：

```text
这个代码是在提供能力，还是在组合能力完成当前项目行为？
```

---

## 四、编译期、链接期和运行时如何接起来

### 4.1 一条完整的装配链

当前系统可以画成：

```text
BOARD / BOARD_CFG
    -> project/boards/<vendor>/<cfg>/<board>.overlay
    -> project/boards/<vendor>/<cfg>/<board>.conf
    -> project/boards/<vendor>/<cfg>/board.cmake

prj.conf
    -> CONFIG_PRJ_TEST
    -> project/thread/Kconfig
    -> 线程选择
    -> 模块/驱动/算法/topic 依赖选择

CMake
    -> target_sources()
    -> target_include_directories()
    -> add_subdirectory()

编译
    -> 形成可执行代码

链接
    -> .user_init
    -> .can_rx1/2/3
    -> .remote
    -> .imu

启动
    -> System_Startup()
    -> 遍历注册表
    -> 创建和启动线程
```

### 4.2 根 CMake 的项目门禁

根 `CMakeLists.txt` 当前设置：

```cmake
set(ACTIVE_PRJ "test" CACHE STRING "Active project")
set(PROJ_DIR project)
set(CONFIG_SYM PRJ_TEST)
```

然后通过：

```cmake
if(CONFIG_${CONFIG_SYM})
    add_subdirectory(drivers)
    add_subdirectory(algorithm)
    add_subdirectory(modules)
    add_subdirectory(topic)
    add_subdirectory(cmd)
    add_subdirectory(${PROJ_DIR})
endif()
```

把整个框架和项目接入 Zephyr app。

这里的关键不是变量名称。

关键是：

```text
项目是否参与编译，先由项目配置门禁决定。
```

### 4.3 Kconfig 的依赖选择

线程开关通常在 `project/thread/Kconfig` 中定义。

例如：

```text
TRD_IMU
    -> MOD_DEV_IMU
    -> TPC_IMU_TO
```

模块开关再继续选择：

```text
MOD_DEV_IMU
    -> FLT_QUATERNION
    -> TPC_IMU_TO
    -> DEV_PWM
    -> CTL_PID
    -> CTL_TIMER
    -> ID_STABILITY
```

具体 IMU 源再选择：

```text
MOD_DEV_IMU_BMI088
    -> COM_SPI

MOD_DEV_IMU_ICM42688P
    -> COM_SPI
```

这条链表达的是：

```text
业务需要
    -> 设备能力
        -> 底层外设和算法
```

### 4.4 CMake 的真实纳入

Kconfig 选择符号之后，CMake 还必须真正加入源文件。

例如：

```cmake
if(CONFIG_MOD_DEV_IMU)
    target_sources(app PRIVATE
        imu/drivers/imu.cpp
        imu/drivers/heater.cpp
        imu/drivers/processor.cpp
    )
endif()
```

如果具体源打开：

```cmake
if(CONFIG_MOD_DEV_IMU_BMI088)
    target_sources(app PRIVATE
        imu/devices/bmi088/bmi088.cpp
    )
endif()
```

所以 Kconfig 和 CMake 是一对。

只改 Kconfig 不一定能把代码编译进来。

只改 CMake 又会绕开配置意图。

### 4.5 链接段的扩展

当前 `cmd/linker/tflm_init.ld` 定义了多个自定义区域：

```text
.user_init
.can_rx1
.can_rx2
.can_rx3
.remote
.imu
```

这些段分别承载：

| section | 作用 |
| --- | --- |
| `.user_init` | 初始化函数条目 |
| `.can_rx1` | CAN1/用户 CAN1 的 ID 处理器 |
| `.can_rx2` | CAN2 的 ID 处理器 |
| `.can_rx3` | CAN3 的 ID 处理器 |
| `.remote` | 遥控协议条目 |
| `.imu` | IMU 数据源条目 |

### 4.6 为什么要使用 `KEEP()`

注册项通常没有普通函数调用关系。

如果只靠普通链接器垃圾回收，链接器可能认为某个静态注册对象“没有被引用”而丢掉它。

因此链接脚本使用：

```text
KEEP(*(.user_init))
```

类似地，其他注册段也使用 `KEEP()`。

这保证：

```text
只要源文件参与编译，注册条目就能进入最终镜像。
```

### 4.7 当前架构的一个重要判断

这套架构不是只靠 C++ 类抽象。

它同时利用了：

- C++ 类型系统；
- Kconfig 符号；
- CMake 条件；
- devicetree；
- GCC section 属性；
- linker script；
- Zephyr runtime。

也就是说，`tflm` 的“框架感”主要来自多个机制的合力。

---

## 五、`cmd/`：运行时命令、构建辅助和链接器扩展

### 5.1 `cmd/` 不是一个单一含义

当前 `cmd/` 下面实际上有三种不同职责：

```text
cmd/
├── shell 命令层
├── build 构建辅助脚本
└── linker 链接段扩展
```

这三者都叫 `cmd`，但运行阶段完全不同。

### 5.2 `cmd/shell/` 的设计含义

架构文档把 `cmd/shell/` 定义为 Zephyr shell 命令实现目录。

它未来负责：

- 注册命令；
- 解析参数；
- 修改运行时参数；
- 查询模块状态；
- 查询传感器状态；
- 触发诊断动作；
- 调用算法或设备模块提供的接口。

典型命令注册方式是：

```cpp
SHELL_CMD_REGISTER(...)
```

它的运行链是：

```text
用户串口终端
    -> Zephyr shell
    -> cmd/shell/命令
    -> modules/algorithm/topic 接口
    -> 修改或读取系统状态
```

### 5.3 `cmd` 的边界

`cmd` 可以调用下层。

但它不应该自己实现业务核心。

例如：

```text
正确：
    cmd 解析 set_pid kp 1.2
        -> 调用 PID/模块提供的参数接口

不理想：
    cmd 直接保存一套 PID 状态
        -> 线程再保存另一套 PID 状态
```

命令层应该是：

```text
解析 + 路由 + 展示
```

而不是：

```text
解析 + 保存状态 + 复制业务逻辑 + 直接操作硬件
```

### 5.4 当前 `cmd` 的落地状态

当前 `cmd/ARCHITECTURE.md` 已经把目标边界写出来。

但当前源码仍然偏骨架：

- `cmd/Kconfig` 当前为空；
- `cmd/CMakeLists.txt` 预留了 `CONFIG_SHELL_PID`；
- `cmd/CMakeLists.txt` 预留了 `CONFIG_SHELL_DBG`；
- 对应的 `pid_cmd.cpp`、`dbg_cmd.cpp` 在当前目录清单中还没有形成完整实现；
- `cmd/shell/` 仍然是规划中的命令实现位置。

因此当前评价应该是：

> `cmd` 的架构位置已经确定，但运行时命令面还没有完全铺开。

还需要补充一个更严格的事实判断：当前 `cmd/CMakeLists.txt` 仍然引用了
`pid_cmd.cpp` 和 `dbg_cmd.cpp`，但这两个实现文件并不在当前仓库的有效文件清单中；
`cmd/Kconfig` 也没有形成与之对应的完整命令配置项。因此在没有补齐命令源文件、
Kconfig 符号和 `prj.conf` 配置之前，不能把 `cmd/` 描述成已经可用的 PID 调参
或调试命令集合。当前它更准确的定位是：

```text
命令层的边界和接入位置已经确定，
具体命令实现仍处于骨架阶段。
```

### 5.5 `cmd` 与 `scripts` 的区别

这两个目录都可能“通过串口调试”，但用途不同。

| 对比项 | `cmd/` | `scripts/` |
| --- | --- | --- |
| 运行位置 | MCU 固件内 | PC/主机 |
| 入口 | 串口 shell 命令 | Python 程序 |
| 典型动作 | 查状态、改参数、触发命令 | 采集、拟合、回放、统计 |
| 资源约束 | 受 MCU 内存和实时性约束 | 可使用 NumPy、SciPy、Matplotlib |
| 是否编进固件 | 是，按 Kconfig 选择 | 否 |
| 典型持续时间 | 短命令、交互式 | 长时间实验、批量处理 |

可以这样理解：

```text
cmd 是固件内的控制面。
scripts 是主机侧的实验面。
```

### 5.6 `cmd/build/` 的含义

`cmd/build/` 不是固件的一部分。

当前包含：

- `build.ps1`
- `build.bat`

它们用于：

- 封装常用 `west build` 参数；
- 根据板级配置选择 board；
- 给 Windows 开发环境提供快捷入口。

`build.ps1` 当前支持根据名称选择：

- `board_rm_c` 对应 `stm32f4_disco`；
- 其他名称直接作为 Zephyr board。

`build.bat` 还会尝试从板级目录中寻找 overlay，再传入 `BOARD_CFG`。

当前脚本内部仍保留了历史路径 `projects\boards\*`，而仓库的 live tree
实际使用的是 `project/boards/`。因此 `build.bat` 的设计意图是明确的，
但它和当前目录结构之间还存在需要继续收敛的路径差异。阅读构建脚本时，
应以根 `CMakeLists.txt` 和当前 `project/boards/` 目录为事实来源，不要把
脚本中的旧路径自动当成当前有效路径。

因此：

```text
cmd/build
    是开发机侧构建入口
    不是 MCU 运行时命令
```

### 5.7 `cmd/linker/` 的含义

`cmd/linker/tflm_init.ld` 也不是用户命令。

它是架构扩展点。

当前它把注册宏产生的对象组织成可遍历表。

因此 `cmd/` 的完整含义可以总结为：

```text
cmd/
    shell    固件内命令
    build    主机侧构建辅助
    linker   编译/链接期注册扩展
```

---

## 六、`algorithm/`：纯计算能力与可离线验证能力

### 6.1 算法层的核心边界

`algorithm/` 负责纯计算。

理想情况下，它不应该知道：

- 哪个 GPIO；
- 哪个 UART；
- 哪个 CAN；
- 哪块板子；
- 哪个线程；
- 哪个具体设备对象。

它接收输入，维护算法状态，产生输出。

例如：

```text
输入：
    目标值
    当前值
    dt
    参数

输出：
    控制量
    估计状态
    辨识参数
```

### 6.2 当前目录组织

当前 `algorithm/` 可以分为：

```text
algorithm/
├── buffer/
├── controller/
├── filter/
├── identify/
├── math/
├── tflm/
├── ARCHITECTURE.md
├── CMakeLists.txt
└── Kconfig
```

### 6.3 `controller/`

`controller/` 放控制相关能力。

当前包括：

- PID；
- 功率控制；
- 软件定时器；
- 执行时间测量。

#### PID

`algorithm/controller/pid/` 提供可复用 PID 控制器。

模块内部处理：

- 比例；
- 积分；
- 微分；
- 输出限幅；
- 积分限幅；
- 积分分离；
- 变速积分；
- D 项低通。

它不应该直接知道：

```text
这个输出最后是 PWM、CAN 电流，还是一个仿真值。
```

#### 功率控制

`algorithm/controller/power_ctrl/` 用于把电机控制和功率约束结合起来。

它可以组合：

- PID；
- RLS；
- 低通滤波；
- 功率模型预测；
- 功率分配；
- 受限输出。

当前底盘线程的业务链是：

```text
电机反馈
    -> 速度/力矩控制
    -> 功率预测
    -> 功率分配
    -> 限制电流
```

但 `PowerCtrl` 自身仍然是算法层能力。

底盘线程才决定：

- 转向组和行进组如何分配；
- 当前总功率预算；
- 哪个电机实例使用它。

#### 定时器

`algorithm/controller/timer/` 提供与业务对象无关的软件计时工具。

它适合：

- 周期动作；
- 降低日志频率；
- 延迟发送；
- 周期性状态检查。

### 6.4 `filter/`

`filter/` 放信号处理和状态估计。

当前包括：

- LPF；
- HPF；
- Kalman；
- EKF；
- 四元数姿态解算。

#### 普通滤波器

LPF 和 HPF 可以独立用于：

- 传感器信号；
- 电机反馈；
- 控制误差；
- 设备温度；
- 日志预处理。

它们不应该绑定具体传感器。

#### 通用 Kalman/EKF

当前 Kalman/EKF 逐步模板化。

理想边界是：

```text
通用滤波器
    负责矩阵、协方差、预测和更新

具体姿态模型
    负责四元数、状态方程、观测方程
```

这样可以避免把“通用 EKF”写死成“只能给 IMU 姿态用的 EKF”。

#### Quaternion

四元数算法属于具体的姿态应用算法。

它可以使用通用 EKF 作为数学引擎。

但它应该自己负责：

- 姿态状态；
- 陀螺仪偏置；
- 状态方程；
- 加速度观测；
- 四元数归一化；
- 欧拉角导出；
- `yaw_total` 累积。

当前 `modules/imu/drivers/processor.cpp` 正是把：

```text
Sample
    -> QuaternionEkf
    -> topic::imu_to::Message
```

组织在 IMU 后端处理器中。

### 6.5 `identify/`

`identify/` 放在线或离线参数辨识算法。

当前包括：

- RLS；
- 电机本体模型；
- 稳定判据；
- 波形发生器。

#### RLS

RLS 是纯数学算法。

它不需要知道数据来自：

- CAN 电机反馈；
- CSV 文件；
- 串口日志；
- 仿真模型。

所以它可以被：

- `project/thread/test` 使用；
- IMU 加热器测试使用；
- Python 脚本镜像使用；
- 后续在线参数更新使用。

#### 电机本体辨识

`algorithm/identify/motor/` 当前用于根据电机输入和反馈拟合本体模型。

`trd_test` 可以把它接入真实电机实验。

Python 脚本则可以在主机上做：

- 数据预处理；
- 参数拟合；
- 结果可视化；
- 固件模型复核。

### 6.6 `buffer/`

`buffer/` 提供通用缓冲区。

当前包括：

- RingBuffer；
- BipBuffer。

它们的价值在于把“数据怎么连续存取”从 UART、USB、协议解析中抽离出来。

这尤其适合：

- DMA；
- 串口帧解析；
- 流式数据；
- 不定长数据；
- 零拷贝或少拷贝路径。

### 6.7 `math/`

当前 `algorithm/math/eigen/` 引入 Eigen header-only 线性代数能力。

它主要为：

- Kalman；
- EKF；
- RLS；
- 其他矩阵计算；

提供底层数学支持。

通过 Kconfig 选择 `MATH_EIGEN`，由需要的算法自动 `select`。

### 6.8 `algorithm/tflm/`

`algorithm/tflm/` 是嵌入式端 TensorFlow Lite Micro 相关代码。

它负责：

- 模型解释；
- 张量内存；
- 算子；
- arena allocator；
- 模型数据；
- MCU 端推理入口所需的运行库。

需要区分两件事：

```text
algorithm/tflm
    是 MCU 内运行推理所需的嵌入式运行时

scripts/
    可以承担 PC 侧数据处理、模型实验和验证
```

未来如果用 Python 训练或分析模型，最终部署到 MCU 的部分仍然需要经过：

```text
模型训练/导出
    -> 模型转换
    -> C/C++ 模型数据
    -> algorithm/tflm
    -> project/thread/tflm
```

### 6.9 算法层为什么必须保持硬件无关

如果 PID 里面直接调用 PWM：

```text
PID
    -> PWM
```

那么 PID 就不能独立仿真。

如果 EKF 里面直接读取 SPI：

```text
EKF
    -> SPI
```

那么 EKF 就不能离线回放。

如果 RLS 里面直接读 CAN：

```text
RLS
    -> CAN
```

那么辨识算法就不能复用 Python 数据或测试线程。

所以正确的方向是：

```text
硬件/线程负责提供数据
算法负责处理数据
硬件/线程负责消费结果
```

---

## 七、`modules/`：从硬件接口到设备能力

### 7.1 模块层的定位

`modules/` 位于：

```text
drivers/
    之上
project/thread/
    之下
```

它不只是“设备驱动目录”。

更准确地说，它负责把底层外设组合成可用的设备能力。

例如：

```text
SPI + GPIO + 校准 + 单位换算
    -> BMI088 模块
```

```text
UART DMA + 协议探测 + 帧校验 + 语义转换
    -> Remote 模块
```

```text
CAN + 电机协议 + 状态转换 + 控制帧
    -> DJI/DM 电机模块
```

### 7.2 当前目录

```text
modules/
├── imu/
├── motors/
├── powermeter/
├── remotes/
├── Kconfig
└── CMakeLists.txt
```

### 7.3 模块层应该解决什么

模块层适合放：

- 设备对象；
- 协议解析；
- 单位转换；
- 设备状态；
- 设备级校准；
- 设备级故障判断；
- 设备级生命周期；
- 对应的简单内部任务。

### 7.4 模块层不应该吞掉什么

模块层不应该无限制吞掉：

- 整机模式；
- 底盘运动学；
- 云台和底盘的跨模块策略；
- 项目专属的任务周期；
- 项目专属的设备拓扑；
- 过于具体的比赛业务。

模块知道“一个 IMU 如何读”。

但不应该知道：

```text
这一台机器人什么时候旋转、什么时候开火。
```

模块知道“一个遥控器协议如何解码”。

但不应该知道：

```text
底盘具体怎样解释某个开关。
```

### 7.5 模块和线程的关系

当前架构允许两种情况。

#### 独立模块

一个模块如果自己就能完成独立采样或服务，可以内部拥有任务。

例如：

```text
ImuManager
    -> 自己拥有采样线程
```

#### 组合型模块

一个模块如果必须和其他模块一起完成业务，任务通常延迟到 `project/thread/`。

例如：

```text
电机模块
    -> 提供状态和控制接口

底盘线程
    -> 组合遥控、电机、PID、功率控制和 CAN
```

这条规则很重要。

否则每个设备都会自己创建任务，最后系统会出现：

- 任务数量不可控；
- 总线访问分散；
- 设备之间难以协调；
- 业务周期无法统一。

### 7.6 多从设备总线的特殊边界

如果一个 CAN 或 UART 总线上挂着多个设备，不能让每个模块都私自拥有一份底层总线线程。

更合理的是：

```text
总线线程
    -> 统一收发
    -> 按 ID 或协议分发
    -> 多个设备模块消费
```

当前 CAN TX 线程就是这个方向的示例。

底盘和云台通过 `to_can_tx` 提交帧。

CAN 线程统一调用底层 CAN driver。

---

## 八、IMU 模块：前端数据源与后端处理链

### 8.1 为什么要把 IMU 拆成前后端

IMU 是一个很适合展示模块层设计的对象。

因为它同时包含：

- 具体芯片寄存器；
- SPI；
- 校准；
- 单位换算；
- 温度；
- 加热；
- 姿态解算；
- 时间步长；
- topic 发布；

如果全部塞到一个 `bmi088.cpp`，后续换芯片、换姿态算法、做离线回放都会变得困难。

当前结构试图把它拆成：

```text
IMU 前端
    负责“从具体芯片得到统一 Sample”

IMU 后端
    负责“对统一 Sample 做温控、姿态处理和发布”
```

### 8.2 IMU 前端：数据源抽象

前端入口在：

```text
modules/imu/devices/imu_device_layer.hpp
```

核心数据结构是：

```cpp
struct Sample
{
    float gyro[3];
    float accel[3];
    float temp;
    float dt;
};
```

`Sample` 是前后端之间的统一工程量接口。

它不再暴露：

- SPI 句柄；
- 寄存器地址；
- 原始字节；
- 芯片私有状态；

### 8.3 `Source` 接口

具体 IMU 数据源实现：

```cpp
class Source
{
public:
    virtual ~Source() = default;

    virtual bool Init() = 0;
    virtual bool Read(Sample& sample) = 0;
    virtual bool Calibrate() { return true; }
};
```

这给出三个关键能力：

1. 初始化硬件；
2. 读取一帧统一数据；
3. 执行设备级校准。

上层不需要知道当前是：

- BMI088；
- ICM42688P；
- 未来其他 IMU。

上层只知道：

```text
Source 能不能初始化？
Source 能不能读到 Sample？
Source 能不能校准？
```

### 8.4 `CalibratedImuSource`

`CalibratedImuSource` 在 `Source` 上继续封装公共流程。

它的职责包括：

- 读取原始 `ImuRawSample`；
- 原始值到工程量转换；
- 读取静态校准参数；
- 应用 offset；
- 应用 scale；
- 填充 `Sample`。

其核心逻辑是：

```text
ReadRaw()
    -> ConvertAccel/ConvertGyro
    -> Correct(offset, scale)
    -> Sample
```

这样具体芯片只需要实现：

```cpp
ReadRaw()
ConvertAccel()
ConvertGyro()
ConvertTemperature()
```

公共校准和单位换算逻辑可以复用。

### 8.5 BMI088 和 ICM42688P

当前 IMU 设备目录包括：

```text
modules/imu/devices/
├── bmi088/
└── icm42688p/
```

每个设备通常包含：

- `xxx.hpp`
- `xxx.cpp`
- `xxx_reg.hpp`

这些文件负责：

- 芯片寄存器定义；
- SPI 读写；
- 芯片初始化；
- 原始数据读取；
- 芯片相关单位转换；
- 芯片默认校准参数。

### 8.6 IMU 数据源注册

当前 `modules/imu/drivers/imu.hpp` 已经提供：

```cpp
struct ImuEntry {
    const char *name;
    Source     *source;
};
```

并通过：

```cpp
REGISTER_IMU(ImuType, name_)
```

把静态设备实例放入：

```text
.imu
```

链接段。

`ImuManager::InitSource()` 会遍历：

```text
__imu_start
    -> __imu_end
```

并检查：

```text
0 个设备
    -> 失败

多个设备
    -> 失败

恰好一个设备
    -> 选择并调用 source_->Init()
```

这说明：

> 当前 IMU 数据源选择已经从“核心代码里的 if-else 选择”转向“设备源注册 + 运行时数量检查”。

需要区分：

- `doc/imu_register_arch.md` 记录的是这个机制的设计背景；
- 当前 `imu.hpp` 和 `imu.cpp` 已经包含对应的注册和遍历实现。

所以阅读 `doc/imu_register_arch.md` 时，应把它看成设计演进记录，而不是完全未落地的方案。

### 8.7 IMU 后端：`ImuManager`

后端总管理器在：

```text
modules/imu/drivers/imu.hpp
modules/imu/drivers/imu.cpp
```

`ImuManager` 内部持有：

```cpp
Source                  *source_;
Sample                   sample_;
attitude::Processor      attitude_;
heater::Heater           heater_;
Timer                    log_timer_;
topic::imu_to::Message   pub_;
Thread<4096>             thread_;
bool                     ready_;
```

这组成员清楚地展示了后端职责：

```text
source_
    -> 数据输入

sample_
    -> 当前样本

heater_
    -> 温控

attitude_
    -> 姿态解算

pub_
    -> 输出消息

thread_
    -> 周期运行
```

### 8.8 `ImuManager::Init()` 的顺序

当前初始化顺序是：

```text
InitSource()
    -> 选择并初始化具体 IMU

heater_.Init()
    -> 初始化加热 PWM/PID

heater_.SetMode(ClosedLoop)

可选 AutoCalib
    -> source_->Calibrate()

可选 AutoIdent
    -> heater_.SetMode(AutoIdent)

attitude_.Init()
    -> 初始化姿态处理器

ready_ = true
```

这里的一个重要架构点是：

```text
project/apps/Init_entry.cpp
    只负责调用 thread::imu::thread_init()

具体是 BMI088 还是 ICM42688P
    由 IMU 模块内部选择
```

这样板卡和传感器选择不会不断膨胀到系统启动器中。

### 8.9 IMU 任务循环

当前 `ImuManager::Task()` 的主要流程是：

```text
读取 Source
    -> 测量真实 dt
    -> 更新加热器
    -> 更新姿态处理器
    -> 填充 imu_to 消息
    -> 周期休眠
```

更具体地说：

```text
source_->Read(sample_)
    -> k_cycle_get_32()
    -> k_cyc_to_ns_floor64()
    -> sample_.dt
    -> heater_.Update(sample_.temp)
    -> attitude_.Process(sample_, pub_)
```

这里的 `dt` 不是简单永远使用名义周期。

系统会根据真实 cycle 差值估算时间间隔。

这样可以减少：

- 调度抖动；
- 线程执行时间变化；
- 传感器读取延迟；

对姿态积分的影响。

### 8.10 IMU 后端的姿态处理器

姿态处理器在：

```text
modules/imu/drivers/processor.hpp
modules/imu/drivers/processor.cpp
```

它持有：

```cpp
alg::attitude::QuaternionEkf ekf_;
```

处理接口是：

```cpp
void Process(const Sample& sample,
             topic::imu_to::Message& pub);
```

处理流程是：

```text
sample
    -> ekf_.Update(sample)
    -> GetState()
    -> quaternion
    -> gyro
    -> roll/pitch/yaw
    -> yaw_total
    -> temperature
    -> pub
```

这就是 algorithm 和 modules 的一个典型边界：

```text
algorithm
    负责 QuaternionEkf 本身

modules/imu
    负责把 IMU 样本送入 EKF
    再把 EKF 结果转成 topic 消息
```

### 8.11 IMU 加热后端

加热控制在：

```text
modules/imu/drivers/heater.hpp
modules/imu/drivers/heater.cpp
```

`Heater` 内部组合：

```text
PWM
    -> 输出加热功率

PID
    -> 闭环调温

稳定判据
    -> 判断预热或辨识阶段是否稳定
```

普通模式：

```text
temperature
    -> PID
    -> duty
    -> PWM
```

辨识模式：

```text
temperature
    -> Identifier
    -> duty
    -> PWM
    -> UART 日志
```

### 8.12 IMU 辨识状态机

在 `CONFIG_IMU_IDENTIFICATION` 下，`Identifier` 支持：

```text
OpenIdent
    -> 开环阶梯占空比

ClosedIdent
    -> 闭环控温并记录输入输出

Stop
    -> 停止辨识并降低 duty
```

开环状态大致是：

```text
Finished
    -> Cooldown
    -> Heating(stage 0)
    -> Stage Done
    -> Cooldown
    -> Heating(stage 1)
    -> ...
    -> Finished
```

日志包含：

```text
seq
t_us
dt_us
stage
state
temp_c
duty
```

这个日志格式是后面 Python 脚本能工作的基础。

### 8.13 IMU topic 当前状态

`topic/imu_to/imu_to.hpp` 定义了：

```cpp
struct Message
{
    float quaternion[4];
    float gyro[3];
    float temperature;
    float roll;
    float pitch;
    float yaw;
    float yaw_total;
};
```

设计上的输出链是：

```text
ImuManager
    -> pub_
    -> zbus_chan_pub(&pub_imu_to, &pub_, ...)
```

但需要以当前 live tree 为准：

`modules/imu/drivers/imu.cpp` 中的发布调用当前被注释掉了。

因此当前准确描述应该是：

```text
IMU 后端已经形成 topic 消息对象和 zbus 通道定义，
但当前快照中的实际发布调用仍处于注释/收敛状态。
```

这比直接写成“已经稳定发布”更准确。

### 8.14 IMU 前后端拆分的意义

这套拆分允许未来出现：

```text
BMI088
    -> Source
ICM42688P
    -> Source
模拟数据
    -> Source
日志回放
    -> Source
```

它们都可以进入同一个：

```text
ImuManager
    -> Heater
    -> Processor
    -> Topic
```

同样，后端也可以替换：

```text
Quaternion EKF
    -> Processor

互补滤波
    -> 另一个 Processor

离线回放处理器
    -> 测试路径
```

这就是 IMU 模块真正的可复用边界。

---

## 九、Remote 模块：当前单 UART 协议自动识别

### 9.1 当前实现必须先讲清楚

当前 `modules/remotes/remote.hpp` 和 `remote.cpp` 仍然是单 UART 结构。

当前 `Remote` 内部只有：

```cpp
UartDma *uart_;
detect_ {};
frame_ {};
Thread<...> thread_;
```

当前 `project/thread/remote/trd_remote.cpp` 也只初始化：

```cpp
DT_ALIAS(remote_uart)
```

并调用：

```cpp
remote_.Init(rx);
```

所以当前不能把双 UART 规划文档描述成已实现功能。

### 9.2 Remote 模块的目标

当前 Remote 模块负责：

```text
UART DMA 数据
    -> 帧缓存
    -> 协议探测
    -> 协议锁定
    -> Decode
    -> topic::remote_to::Message
```

它不负责：

- 底盘控制；
- 云台控制；
- 开火策略；
- 设备模式；
- 具体项目的控制周期。

这些由 topic 消费者和项目线程决定。

### 9.3 `RemoteProtocol`

协议基类提供：

```cpp
class RemoteProtocol
{
public:
    virtual bool Decode(...);
    virtual bool Validate(...);

protected:
    uart_config line_cfg_;
};
```

每个协议对象同时携带：

- UART 波特率；
- 校验位；
- 停止位；
- 数据位；
- 流控；
- `Validate()`；
- `Decode()`。

因此切换协议时，不需要在 Remote 核心里写一长串协议专属串口配置。

协议对象自己知道自己需要什么 UART 线路配置。

### 9.4 `RemoteEntry`

协议注册条目是：

```cpp
struct RemoteEntry
{
    const char      *name;
    uint16_t         frame_size;
    RemoteProtocol  *protocol;
    Priority         priority;
    uint8_t          need_hits;
};
```

它把一个协议的运行时信息集中起来：

- 协议名称；
- 帧长度；
- 协议对象；
- 优先级字段；
- 锁定所需命中次数。

### 9.5 `REGISTER_REMOTE`

协议实现文件末尾使用：

```cpp
REGISTER_REMOTE(
    Dr16Protocol,
    kFrameSizeDR16,
    remote::Priority::Low,
    3,
    dr16
);
```

宏展开后：

```text
静态协议对象
    + RemoteEntry
    -> .remote section
```

启动或运行时由：

```text
__remote_start
    -> __remote_end
```

提供遍历范围。

这使新增协议时原则上只需要：

1. 新建协议文件；
2. 继承 `RemoteProtocol`；
3. 实现 `Validate()`；
4. 实现 `Decode()`；
5. 填充 `line_cfg_`；
6. 追加一行 `REGISTER_REMOTE()`；
7. 在 Kconfig/CMake 中选择编译。

不需要改 Remote 核心状态机。

### 9.6 当前协议

当前协议目录包括：

```text
modules/remotes/
├── dr16/
├── sbus/
├── vt12/
└── vt13/
```

当前已存在的协议实现包括：

- DR16；
- SBUS；
- VT12；
- VT13。

协议层输出统一转换为：

```text
topic::remote_to::Message
```

这意味着底盘线程不应该直接知道 DR16 的字节布局。

### 9.7 当前探测状态机

当前 Remote 的状态是：

```text
Detecting
    -> Locked
```

探测时：

```text
从 .remote 表中取候选协议
    -> SwitchProto()
    -> 调整 UART line config
    -> 等待完整帧
    -> Validate()
```

如果验证成功：

```text
hits++
    -> hits >= need_hits
    -> Locked
```

如果验证失败：

```text
retry++
    -> retry >= need_hits
    -> 切换下一个协议
```

所有协议尝试失败后：

```text
清空探测进度
    -> 从第一个协议重新开始
```

### 9.8 Locked 热路径

锁定以后，数据路径尽量保持笔直：

```text
UART sem
    -> Read()
    -> ProcessChunk()
    -> HandleLocked()
    -> Decode()
    -> zbus_chan_pub()
```

锁定状态不应该每帧重新扫描所有协议。

连续解码失败超过阈值后，才回到：

```text
ResetDetect()
```

这体现了：

> 正常数据路径要短，协议探测和故障恢复放到冷路径。

### 9.9 断连和归零

当前任务循环在一段时间没有收到 UART 信号量时，会检查：

```text
now - last_valid_ms >= kRemoteTimeoutMs
```

超时后：

```text
记录一次 lost
pub_ = {}
zbus_chan_pub(&pub_remote_to, &pub_, ...)
```

这样底盘或云台可以收到全零遥控消息。

当前实现的一个设计选择是：

```text
暂时没有数据时发布归零，
但不立即因为超时而重新探测协议。
```

真正的重新探测主要由：

- 连续 `Decode()` 失败；
- `kUnlockFailLimit`；

触发。

### 9.10 `remote_to` 的语义转换

原始协议数据包括：

- 原始通道；
- 拨杆；
- 鼠标；
- 键盘；

但上层不应该看到这些协议细节。

协议层将它们转换为：

```cpp
topic::remote_to::Message
```

里面是：

- 归一化底盘横向/纵向通道；
- yaw/pitch 输入；
- 底盘模式；
- 射击开关；
- 装填开关；
- 自瞄开关；
- 超级电容开关；
- 版本号。

这就是模块层的一个重要职责：

```text
把协议数据翻译成项目可以理解的语义数据。
```

---

## 十、Remote 双 UART：规划中的冗余输入架构

### 10.1 当前状态

双 UART 目前主要记录在：

```text
doc/remote_dual_arch.md
```

当前实现仍是：

```text
一个 Remote
    -> 一个 UartDma
    -> 一个 detect_
    -> 一个解析线程
```

规划中的实现才是：

```text
一个 Remote
    -> uart_[2]
    -> detect_[2]
    -> uart_idx_
    -> 一个解析线程
```

### 10.2 双 UART 想解决什么

双 UART 不是为了同时启动两个解析线程。

主要用途是：

- 两个 UART 各自接一个输入源；
- 支持不同遥控器协议；
- 主输入失联后切到备用输入；
- 用户只管理遥控器开关；
- 系统根据有效数据自动接管。

典型场景：

```text
UART-A
    -> 高优先级遥控器

UART-B
    -> 低优先级遥控器或备用遥控器
```

### 10.3 双 UART 的三个状态层次

规划中的设计把状态拆成三层。

#### UART 层

每个 UART 自己有：

- 是否初始化成功；
- 当前探测状态；
- 当前候选协议；
- 命中次数；
- 失败次数；
- 最近有效时间；
- 是否锁定协议；
- FIFO 状态。

#### Remote 层

Remote 额外维护：

```cpp
uint8_t uart_idx_;
```

它表示当前活跃输入源。

#### 线程层

仍然只有一个 Remote 线程。

这个线程负责读取当前活跃 UART。

因此：

```text
detect_[2]
    是两个状态副本

不是两个并行解析线程
```

### 10.4 为什么不启动两个解析线程

如果两个 UART 各自启动线程，系统要额外处理：

- 两个线程同时发布；
- 两套协议状态；
- 两个输入源竞争；
- 当前有效源的仲裁；
- 消息覆盖；
- 切换瞬间的旧帧；
- 多线程访问共享 `pub_`；
- 两个线程同时改变 UART 配置。

而规划中的设计希望：

```text
两路输入
    -> 一个状态机
    -> 一个发布者
    -> 一个语义输出
```

这样用户仍然只看到一个：

```text
topic::remote_to::Message
```

### 10.5 热路径和冷路径

双 UART 规划里最重要的原则是：

```text
锁定后的热路径：
    Read -> Decode -> Publish

切换和探测冷路径：
    Timeout -> HasData -> Switch -> Flush -> Probe
```

锁定后不应该每一帧都检查另一个 UART。

只有当前输入源超时，才进入冷路径：

```text
当前 UART 超时
    -> 检查另一个 UART 是否有数据
    -> 必要时切换
```

### 10.6 UART 切换事务

规划中的切换不能只写：

```cpp
uart_idx_ ^= 1;
```

因为目标 UART 里可能已经积累了旧数据。

更完整的事务是：

```text
保存旧索引
    -> 设置新索引
    -> 清空共享 frame buffer
    -> 清理目标 UART 软件 FIFO
    -> 使用目标 UART 的独立 detect 状态
    -> 从新数据开始解析
```

切换时需要处理：

- 旧帧残留；
- 帧缓冲位置；
- 目标 UART 的旧 FIFO；
- 目标协议探测进度；
- 当前发布消息状态。

### 10.7 为什么必须清理 stale buffer

假设 UART-B 在非活动期间一直接收。

它的 DMA 或软件 FIFO 可能积累了：

```text
很久以前的完整帧
```

如果切换后立刻解析，会出现：

- 看起来刚切换就收到有效帧；
- 实际解析的是延迟数据；
- `last_valid_ms` 被旧数据刷新；
- 用户以为备用遥控器已经接管；
- 实时控制得到错误的时间语义。

因此规划中要求：

```text
切换时丢弃目标 UART 的旧软件缓存，
从切换后的新接收帧重新开始。
```

### 10.8 防抖和 anti-flap

双 UART 最危险的问题之一不是“切不过去”，而是：

```text
两个输入源都不稳定，
系统在 A/B 之间反复跳转。
```

因此规划中需要：

- 切换冷却时间；
- 目标 UART 有数据才考虑切换；
- 有数据不等于有效；
- 必须经过协议验证或锁定；
- 切换后不要立即因为单个错误帧再次切回。

### 10.9 `Priority` 在双 UART 中的含义变化

当前 `RemoteEntry::Priority` 主要作为协议条目中的字段存在。

当前探测代码按链接表顺序推进，不能简单说已经按照 `Priority` 完成排序。

双 UART 规划希望把优先级更多解释为：

```text
UART 输入源优先级
```

但这不应误解为：

```text
两个 UART 同时解析，Priority 负责每帧仲裁
```

更合理的含义是：

```text
初始选择和冷路径切换时，
高优先级 UART 更容易被选为当前源。
```

### 10.10 双 UART 规划的接口目标

理想情况下，上层接口变化应尽可能小：

```cpp
// 当前单 UART
remote_.Init(rx);
remote_.Start();
```

```cpp
// 规划双 UART
remote_.Init(rx_high, rx_low);
remote_.Start();
```

`Start()`、`topic::remote_to::Message` 和上层底盘/云台消费者不需要知道切换细节。

这说明双 UART 复杂度应该收敛在：

```text
modules/remotes/
```

而不是扩散到：

- `project/apps`；
- 底盘线程；
- 云台线程；
- topic 消费者；

### 10.11 双 UART 文档的事实边界

阅读 `doc/remote_dual_arch.md` 时应明确：

| 内容 | 当前状态 |
| --- | --- |
| `RemoteProtocol` 注册 | 已落地 |
| `.remote` section | 已落地 |
| 单 UART 自动协议探测 | 已落地 |
| 两个 `UartDma*` | 规划 |
| `detect_[2]` | 规划 |
| `TrySwitchUart()` | 规划 |
| stale FIFO 清理 | 规划 |
| anti-flap 冷却 | 规划 |
| 双 UART 板级 alias | 尚未落地 |

因此文档中最稳妥的表述是：

> 当前 remote 已经具备协议注册和单 UART 自动识别基础；双 UART 是在此基础上的输入冗余扩展，目前仍处于设计规划阶段。

---

## 十一、`project/`：嵌入到框架中的项目单元

### 11.1 `project/` 不是外部插件目录

“项目层嵌入到架构当中”有一个非常具体的含义：

```text
project/ 不是独立于框架之外的另一个工程
```

它会被根 CMake 直接加入当前 Zephyr app：

```cmake
add_subdirectory(${PROJ_DIR})
```

因此最终固件的源代码来自：

```text
Zephyr
    + drivers
    + algorithm
    + modules
    + topic
    + cmd
    + project
```

它们共同形成一个镜像。

### 11.2 `project/` 的依赖方向

方向是：

```text
project/
    -> 使用框架层能力

框架层
    - 不应该反向 include project/
    - 不应该知道当前机器人叫什么
    - 不应该知道当前项目有哪些线程
```

这意味着：

```text
项目是框架的使用者和装配者，
框架不是某个项目的业务从属。
```

### 11.3 `project/` 为什么能承载多项目

只要根构建系统可以切换：

- 活动项目；
- 板级目录；
- 线程配置；
- overlay；
- board conf；

同一个框架层就可以服务不同项目。

理想结构是：

```text
框架层
    保持公共能力

项目 A
    选择一组模块和线程

项目 B
    选择另一组模块和线程

项目 C
    复用算法但换板卡
```

### 11.4 项目层不是所有代码的“最后垃圾桶”

项目层很灵活。

但不能因为它灵活，就把所有内容都放进去。

以下代码不应该长期停留在 `project/thread/`：

- 可被多个项目复用的 PID；
- 可被多个设备使用的协议基础类；
- 可被多个线程使用的队列消息结构；
- 纯数学模型；
- 底层 UART/CAN/SPI 操作；
- 具体芯片寄存器定义。

更准确的原则是：

```text
project/thread/
    放项目组合和业务周期

框架层
    放可复用能力
```

### 11.5 `project/` 的三部分

```text
project/
├── apps/
├── boards/
└── thread/
```

分别对应：

```text
apps
    系统怎么进入和启动

boards
    系统绑定哪块板子

thread
    系统运行哪些业务
```

---

## 十二、`project/apps/`：初始化与中断的编译期组织

### 12.1 为什么初始化不只写在 `main.c`

如果所有初始化都堆在 `main.c`：

```text
main()
    -> can_init()
    -> imu_init()
    -> remote_init()
    -> chassis_init()
    -> gimbal_init()
    -> ...
```

短期很直观。

但长期会出现：

- `main.c` 变成业务清单；
- 新线程必须修改中央入口；
- 初始化失败策略散落；
- 初始化项和实现文件距离很远；
- 项目切换时中央代码越来越复杂。

当前 `project/apps/` 试图把“初始化组织”和“初始化实现”分开。

### 12.2 `Init_entry.hpp`

`Init_entry.hpp` 定义：

```cpp
enum class InitStage;
enum class InitLevel;
using InitFunc = bool (*)();
struct InitEntry;
```

以及：

```cpp
REGISTER_INIT(fn, stage_, level_, name_)
```

一个注册项包含：

- 初始化函数；
- 初始化阶段；
- 初始化等级；
- 调试名称。

### 12.3 `REGISTER_INIT` 的编译期思路

源文件中写：

```cpp
bool thread_init()
{
    ...
}

REGISTER_INIT(thread_init, Module, Mid, "xxx_init");
```

编译器会生成静态 `InitEntry`。

通过 GCC section 属性放入：

```text
.user_init
```

链接脚本收集所有对象。

所以“注册”发生在编译和链接阶段。

运行时只负责：

```text
遍历表
    -> 按 stage 过滤
    -> 调 func
    -> 根据 level 处理失败
```

这里的“编译期思路”需要精确理解。当前实现并没有在编译期执行初始化，
也没有生成一张由 CMake 排好所有业务优先级的普通数组。真实过程是：

```text
源文件
    -> 生成静态 InitEntry
    -> 放入 .user_init section
    -> linker script KEEP() 保留
    -> 产生 __user_init_start / __user_init_end
    -> System_Startup() 在运行时遍历
```

因此它是“编译期注册、链接期收集、运行时执行”，而不是“编译期执行”。
这个区别很重要：注册项是否存在由编译和链接结果决定，但 `func()` 什么时候
被调用，仍然由 `System_Startup()` 的运行时流程决定。

### 12.4 当前阶段顺序

当前 `Init_entry.cpp` 明确运行：

```text
Bsp
    -> ThreadEarly
    -> Module
    -> ThreadMid
    -> ThreadLate
```

当前阶段可理解为：

| 阶段 | 目标 |
| --- | --- |
| `Bsp` | 设备树设备、总线、底层硬件和基础初始化 |
| `ThreadEarly` | 需要尽早运行的基础线程 |
| `Module` | 设备和算法对象初始化 |
| `ThreadMid` | 中间层业务线程 |
| `ThreadLate` | 依赖前面能力的正式业务和测试线程 |

这不是单纯按目录层级排序。

它是在表达依赖关系。

还要注意一个容易误读的地方：`InitStage` 的枚举数值顺序是
`Bsp = 0、Module = 1、ThreadEarly = 2、ThreadMid = 3、ThreadLate = 4`，
但 `System_Startup()` 实际使用的是 `StageMap[]` 中的执行顺序：

```text
Bsp -> ThreadEarly -> Module -> ThreadMid -> ThreadLate
```

也就是说，当前真正决定运行顺序的是 `System_Startup()` 对 `RunStage()` 的
显式调用顺序，而不是枚举值自然递增顺序。后续如果要让阶段顺序更加自描述，
需要把枚举定义、阶段映射和文档中的顺序统一起来。

### 12.5 为什么 CAN 可以提前

当前 CAN TX 线程注册为：

```text
can_init
    -> Bsp

can_start
    -> ThreadEarly
```

这说明 CAN 被当作基础服务。

后面的电机、底盘和云台线程只需要把帧提交给 CAN 发送路径。

它们不必在自己的线程里重复初始化总线。

### 12.6 初始化等级

当前等级是：

```text
High
Mid
Low
```

对应：

```text
High
    -> 失败后停机

Mid
    -> 报错后继续

Low
    -> 告警后继续
```

这使项目可以明确表达：

```text
核心 CAN 不可用
    可能不能继续

可选 PC 调试线程不可用
    可以继续运行
```

当前 `InitLevel` 的实际用途主要是**初始化失败策略**，不是同一阶段内的排序键。
`RunStage()` 目前只按 `stage` 过滤条目，然后按照 `.user_init` 中的链接顺序
逐项调用；它没有对 `level` 做排序，也没有实现 `High` 一定先于 `Mid`、
`Mid` 一定先于 `Low` 的执行保证。

因此当前字段语义应写成：

```text
InitStage
    决定这个初始化项在哪个启动阶段执行

InitLevel
    决定该项失败后停机、报错继续还是告警继续
```

如果以后需要表达同一阶段内的严格优先级，应单独增加排序字段或拆分 section，
不能仅仅依赖现在的 `InitLevel` 名字来推断执行顺序。

### 12.7 当前启动器的角色

`Init_entry.cpp` 不是业务总控。

它不应该知道：

- 底盘运动学；
- IMU 芯片寄存器；
- 遥控协议字节；
- 电机 PID 参数。

它只知道：

- 什么时候调用初始化项；
- 初始化项失败怎么处理；
- 当前阶段叫什么；
- 如何打印启动过程。

因此它属于框架级启动设施。

### 12.8 中断分发基础设施也采用注册式思路

当前 `project/apps/Irq_handlers.h` 定义：

```cpp
CAN_RX_HANDLER(bus_, id_, handler_, name_)
```

它会根据总线编号进入：

```text
.can_rx1
.can_rx2
.can_rx3
```

`Irq_handlers.cpp` 运行时遍历对应 section：

```text
CAN callback
    -> dispatch(frame, start, end)
    -> 按 frame.id 查找
    -> 调用 handler(data)
```

但当前状态必须分开写：

```text
Irq_handlers.h/.cpp
    已经提供 section、边界符号和 dispatch 基础设施

CAN_RX_HANDLER(...)
    当前源码中暂未形成实际业务注册项
```

也就是说，“中断分发机制已经设计并实现了基础设施”是准确的；
“当前所有电机和设备都已经通过这个注册表接入”则不是当前代码事实。
当前 DJI 电机等模块虽然提供了 `CanCpltRxCallback()`，但在 live tree 中
还需要继续完成具体 CAN ID 与对象回调的注册连接。

### 12.9 这和传统 CAN 回调有什么区别

传统方式可能是：

```cpp
void HAL_CAN_RxCallback(...)
{
    if (id == 0x201) ...
    else if (id == 0x202) ...
    else if (id == 0x203) ...
}
```

当前注册式方式是：

```cpp
CAN_RX_HANDLER(USER_RX_CAN1, 0x201, motor_handler, motor1);
CAN_RX_HANDLER(USER_RX_CAN1, 0x202, motor_handler, motor2);
```

新增 ID 时，处理逻辑可以靠近设备模块或项目线程。

公共分发器不必不断增长。

### 12.10 编译期思路的边界

需要注意：

```text
编译期注册不等于编译期执行。
```

注册项是在编译/链接时收集。

真正调用仍然发生在运行时。

其收益是：

- 自动发现；
- 统一遍历；
- 减少中央手工列表；
- 便于按 section 分组。

其代价是：

- 需要理解 linker script；
- 注册项顺序不能只看源文件；
- section 名必须保持一致；
- 未正确 `KEEP()` 时可能被垃圾回收。

---

## 十三、`project/boards/`：项目如何绑定具体板卡

### 13.1 板级配置为什么属于 project

同一个 IMU 模块可能接在：

- 不同 SPI 总线；
- 不同 GPIO；
- 不同 PWM；
- 不同 UART；
- 不同 CAN alias；

这些差异不是 IMU 算法差异。

也不是 IMU 模块协议差异。

它们属于：

```text
某个项目在某块板上的具体绑定
```

所以板级配置放在：

```text
project/boards/
```

是合理的。

### 13.2 overlay 的职责

`.overlay` 负责设备树层绑定，例如：

- alias；
- pinctrl；
- SPI 节点；
- UART 节点；
- CAN 节点；
- PWM 节点；
- GPIO 节点；
- chosen；
- status。

模块通过：

```cpp
DT_ALIAS(...)
DT_NODELABEL(...)
```

找到设备。

这样 C++ 代码不需要写死：

```text
“一定是 uart3”
“一定是 spi2”
```

它只依赖：

```text
项目板级配置提供了某个语义 alias
```

### 13.3 `.conf` 的职责

板级 `.conf` 负责：

- 打开芯片能力；
- 覆写外设配置；
- 设置板级 Kconfig；
- 调整设备驱动选项；
- 为当前板子提供默认配置。

它和线程 `Kconfig` 的关系是：

```text
thread/Kconfig
    描述功能需要什么

board.conf
    描述这块板子默认能提供什么
```

### 13.4 `board.cmake` 的职责

`board.cmake` 主要用于：

- 烧录脚本；
- 调试器；
- OpenOCD；
- CMSIS-DAP；
- 板级构建辅助。

它不应该承担业务逻辑。

### 13.5 `BOARD_CFG`

根 CMake 会根据：

```text
BOARD_CFG
BOARD
```

去搜索：

```text
project/boards/*/<BOARD_CFG>/<BOARD>.overlay
project/boards/*/<BOARD_CFG>/<BOARD>.conf
project/boards/*/<BOARD_CFG>/board.cmake
```

这允许：

```text
Zephyr board 名
    和
项目板级配置分组
```

保持一定分离。

例如同一 Zephyr board 名，在不同项目配置分组下可以有不同 overlay 或 conf。

### 13.6 `SDK_GLUE_DIR`

当前根 CMake 还为 HPMicro 相关内容设置：

```text
SDK_GLUE_DIR
```

并把它加入：

- `BOARD_ROOT`；
- `SOC_ROOT`；
- `DTS_ROOT`；
- `ZEPHYR_EXTRA_MODULES`。

这说明板级 SoC/board 适配不一定全部放在当前仓库。

项目可以把外部 glue 仓库接入当前框架。

### 13.7 板级 alias 和模块解耦

以 remote 为例：

```cpp
DEVICE_DT_GET(DT_ALIAS(remote_uart))
```

Remote 模块不需要知道：

```text
remote_uart 实际是 UART3 还是 UART4
```

它只需要知道：

```text
remote_uart 这个语义设备存在且 ready
```

同样，IMU 可以通过：

```text
imu_spi
imu_pwm
```

这些 alias 完成板级绑定。

---

## 十四、`project/thread/`：把模块组合成真实业务

### 14.1 线程层的准确定位

`project/thread/` 是当前项目的业务装配层。

它回答：

```text
哪些模块一起工作？
以什么周期工作？
谁消费谁的数据？
谁组帧？
谁发布？
```

### 14.2 当前线程目录

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
├── Kconfig
├── CMakeLists.txt
└── thread.hpp
```

### 14.3 线程模板

线程通常遵循：

```cpp
namespace thread::xxx {

bool thread_init();
bool thread_start();

REGISTER_INIT(...);
REGISTER_INIT(...);

}
```

具体类实例通常是：

```cpp
static Module module_;
```

这样实例只属于当前项目线程。

模块层只提供类定义和能力。

### 14.4 `thread_init` 和 `thread_start`

这两个函数应该分工：

```text
thread_init
    设备检查
    参数初始化
    对象初始化
    创建线程前的准备

thread_start
    真正启动线程
    选择线程优先级
    进入运行阶段
```

这种拆分可以让：

```text
初始化失败
    和
线程运行失败
```

被区分处理。

### 14.5 CAN TX 线程

`project/thread/can/trd_can_tx.cpp` 的职责是：

```text
初始化 CAN 设备
    -> 注册 RX 回调
    -> 创建发送线程
    -> 从 k_msgq 读取帧
    -> 调用 Can::Send()
```

它是一个总线服务线程。

底盘和云台不需要直接操作 CAN driver。

它们只需要把：

```cpp
topic::to_can_tx::Message
```

放入对应队列。

### 14.6 IMU 线程

`project/thread/imu/trd_imu.cpp` 的职责很薄：

```text
声明 ImuManager 实例
    -> thread_init() 调 imu_.Init()
    -> thread_start() 调 imu_.Start()
    -> 注册初始化项
```

IMU 芯片选择、温控和姿态解算不放在启动器里。

它们都留在：

```text
modules/imu/
```

### 14.7 Remote 线程

当前 `project/thread/remote/trd_remote.cpp` 负责：

```text
获取 remote_uart alias
    -> 初始化 UartDma
    -> 设置线路参数
    -> 初始化 Remote
    -> 启动 Remote 线程
```

它没有直接解析 DR16 或 SBUS。

协议解析留在：

```text
modules/remotes/
```

### 14.8 Chassis 线程

`project/thread/chassis/trd_chassis.cpp` 是业务装配的典型。

主循环可以概括成：

```text
ReadRemote()
    -> UpdateTarget()
    -> ControlCalculate()
    -> PowerAlloc()
    -> FramePublish()
```

具体含义：

```text
ReadRemote
    读取 remote_to

UpdateTarget
    运动学解算、优劣弧、方向处理

ControlCalculate
    角度环、速度环、力矩环

PowerAlloc
    功率预测和分配

FramePublish
    组 CAN 帧并放入发送队列
```

这条链说明：

```text
算法层提供控制器
模块层提供电机状态和控制接口
topic 提供输入输出契约
线程层负责把它们按 1ms 业务周期组合起来
```

### 14.9 Gimbal 线程

`project/thread/gimbal/trd_gimbal.cpp` 组合：

- 遥控器 topic；
- DM 电机模块；
- PID；
- Timer；
- CAN TX topic。

主循环大致是：

```text
更新定时器
    -> 读取 remote_to
    -> 更新目标角
    -> 读取 DM 电机反馈
    -> 位置环
    -> 速度环
    -> 打包 MIT 控制帧
    -> 放入 gimbal_tx 队列
```

### 14.10 Test 线程

`project/thread/test/` 是一个正式的实验入口。

它适合：

- 新设备验证；
- 电机开环；
- 运行辨识；
- 临时串口测试；
- 参数采集；
- 新算法试跑；
- 不稳定功能隔离。

当前 `trd_test.cpp` 可以复用：

- DJI C610；
- CAN TX 队列；
- MotorPlant；
- RLS；
- CAN 回调。

这说明测试代码不需要脱离正式架构。

它可以：

```text
选择框架能力
    -> 通过 Kconfig/CMake 编译
    -> 通过 REGISTER_INIT 启动
    -> 和正式线程共享驱动/模块/算法
```

### 14.11 TFLM 线程

`project/thread/tflm/` 是 MCU 推理测试入口。

它的角色不是训练模型。

它负责：

- 加载或绑定模型数据；
- 初始化 TFLM；
- 提供输入；
- 运行推理；
- 输出结果；
- 验证 MCU 端资源和时间。

模型训练和离线分析应该放在主机侧工具或独立 Python 环境。

---

## 十五、`topic/`：线程间数据契约

### 15.1 为什么需要 topic

如果没有 topic，线程之间很容易变成：

```text
线程 A
    -> 直接 include 线程 B
    -> 直接访问线程 B 的全局对象
```

长期会造成：

- 数据所有权不清；
- 头文件依赖扩散；
- 线程替换困难；
- 测试难以模拟；
- 项目迁移时依赖穿透。

`topic/` 把跨线程通信中的“数据形状”和“通道机制”单独拿出来。

### 15.2 topic 的职责

topic 层负责：

- 消息结构体；
- zbus channel；
- zbus observer；
- k_msgq；
- 通道的 Kconfig；
- 通道的 CMake；
- 数据语义约定。

topic 不负责：

- 读取 UART；
- 控制电机；
- 创建线程；
- 保存设备实例；
- 实现运动学。

### 15.3 `remote_to`

`topic/remote_to/remote_to.hpp` 定义遥控语义消息：

```text
version
chassisx
chassisy
yaw
pitch
chassis_mode
shoot_ctrl
reload_ctrl
autoaim_ctrl
supercap_ctrl
```

它已经完成了从协议数据到业务语义的隔离。

底盘线程不需要知道：

```text
DR16 的某两个字节怎么拼接。
```

它只读取：

```text
chassisx/chassisy
chassis_mode
```

云台线程只读取：

```text
yaw/pitch
```

### 15.4 `imu_to`

`topic/imu_to/imu_to.hpp` 定义：

```text
quaternion
gyro
temperature
roll
pitch
yaw
yaw_total
```

这条消息将：

```text
IMU 后端内部的 EKF 状态
```

转换成：

```text
其他线程可以消费的姿态状态
```

### 15.5 `to_can_tx`

`topic/to_can_tx/to_can_tx.hpp` 定义：

```cpp
struct Message {
    uint16_t tx_id;
    uint8_t  data[8];
};
```

它没有使用 zbus。

当前使用：

```text
k_msgq
```

原因是 CAN 发送更接近：

```text
多个生产者
    -> 一个发送消费者
    -> 每条帧独立排队
```

而不是：

```text
多个发布者覆盖同一个“最新状态”
```

### 15.6 zbus 和 k_msgq 的选择

#### zbus 适合

- 状态广播；
- 多个观察者；
- 最新值语义；
- 结构化消息；
- 传感器状态。

#### k_msgq 适合

- 点对点；
- 多生产者单消费者；
- 每条消息都应保留；
- 输出帧队列；
- 不能只保留最新值的事件。

### 15.7 topic 不是“全局变量换个名字”

如果 topic 只是把全局变量包装起来，没有明确：

- 谁发布；
- 谁订阅；
- 更新频率；
- 消息是否覆盖；
- 队列是否阻塞；
- 超时如何处理；

那么它仍然会退化成隐式共享状态。

因此 topic 的真正价值是：

```text
把数据格式、数据方向和数据传输语义写出来。
```

### 15.8 topic 的版本和时间语义

`remote_to::Message` 有 `version` 字段。

它可以帮助消费者判断：

- 是否收到新消息；
- 数据是否发生更新；
- 输入是否停滞。

后续还可以继续统一：

- 时间戳；
- source；
- valid；
- timeout；
- sequence；

但这些字段不能为了“以后可能有用”无限堆进去。

每个字段都要有明确的消费者和语义。

### 15.9 topic 的编译期裁剪

当前：

```text
TPC_REMOTE_TO
    -> select ZBUS

TPC_IMU_TO
    -> select ZBUS

TPC_TO_CAN_TX
    -> 编译 CAN TX 消息队列
```

未启用的 topic 不应进入固件。

这使：

```text
测试项目
    可以只开一个队列

IMU 项目
    可以只开 IMU topic

底盘项目
    可以按需要开 remote + CAN topic
```

---

## 十六、`scripts/`：嵌入式工程外的 Python 实验层

### 16.1 为什么需要 `scripts/`

很多实验不适合直接在 MCU 上完成：

- 大量数据拟合；
- 画图；
- 多组实验比较；
- 复杂统计；
- 参数网格搜索；
- 离线日志回放；
- 结果导出 CSV；
- 直接使用 NumPy/Matplotlib/SciPy；

如果把这些功能塞进固件，会导致：

- 固件变大；
- 实时任务变复杂；
- 调试和算法混在一起；
- 运行时资源被实验代码占用；
- 结果难以重复。

所以 `scripts/` 的定位是：

> 用主机算力和 Python 生态，把嵌入式设备采集到的真实数据转成可验证的模型、参数和报告。

### 16.2 `scripts/` 当前目录

当前主要有：

```text
scripts/
├── imu/
└── uart/
```

当前实际存在的 Python 文件主要在：

```text
scripts/imu/
scripts/uart/
```

当前仓库没有形成独立的 `scripts/motor/` 工具目录。电机辨识算法本身位于
`algorithm/identify/motor/`，而实验线程位于 `project/thread/test/`；
主机侧 Python 工具目前主要集中在 IMU 和 UART 两类场景。未来当然可以
增加 `scripts/motor/`，但那属于扩展方向，不能当成当前已经存在的工具集。

### 16.3 Python 脚本和固件的边界

Python 脚本不会被编译进 MCU 固件。

它们运行在：

- Windows PC；
- Linux 主机；
- 开发机；
- 数据分析环境。

它们通过：

- UART；
- USB CDC；
- 日志文件；
- CSV；

和固件交换数据。

因此这里的“用户可以嵌入 Python 脚本进行功能测试”，准确含义是：

```text
用户可以把 Python 测试程序纳入同一个工程仓库和实验流程，
让固件和主机脚本形成闭环验证。
```

不是：

```text
把 Python 解释器塞进当前 MCU 固件。
```

### 16.4 IMU 脚本总览

| 脚本 | 主要用途 |
| --- | --- |
| `imu_temp_identify.py` | 多阶段 IMU 加热温度曲线拟合 |
| `imu_open_loop_replay.py` | 使用固定模型回放开环加热日志 |
| `imu_closed_loop_identify.py` | 从闭环日志拟合加热对象 |
| `stage_fit.py` | 对每个加热阶段做曲线提取和多项式拟合 |
| `tune_imu_pid.py` | 用固件一致的控制路径仿真和调 PID |
| `uart_perf.py` | UART 接收性能和吞吐测试 |

### 16.5 `imu_temp_identify.py`

这是当前脚本中最完整的 IMU 加热辨识工具之一。

它支持：

- 直接读取日志；
- 读取 CSV；
- 在线串口采集；
- 解析固件辨识状态；
- 按占空比阶段切分；
- 预拟合数据质量检查；
- 单阶段拟合；
- 多阶段联合拟合；
- 离群实验剔除；
- 结果 CSV；
- 曲线图；
- 多轮实验比较。

它识别的模型形式是：

```text
T(t) = T0 + dT * (1 - exp(-(t - L) / tau))
```

也就是：

- 初始温度；
- 温升幅度；
- 延迟；
- 时间常数。

脚本要求固件输出类似：

```text
seq=123,
t_us=456789,
dt_us=1000,
stage=0,
state=4,
temp_c=37.520,
duty=0.200
```

它不是简单地“把所有点塞进一个拟合器”。

当前代码包含很多数据门禁：

- 温度是否在合理范围；
- 每个阶段样本数是否足够；
- 总样本数是否足够；
- 时间间隔是否存在异常；
- 温升是否足够；
- duty 范围是否足够；
- 拟合窗口是否足够长；
- 多轮曲线是否存在明显离群。

这体现出 `scripts/` 不只是计算器。

它也承担：

```text
实验数据质量控制
```

### 16.6 `imu_open_loop_replay.py`

这个脚本不负责重新拟合参数。

它使用固定模型：

```text
G(s) = K / (tau*s + 1) * exp(-delay*s)
```

然后把日志中测得的 duty 重新输入模型。

输出比较：

```text
实测温度
    vs
固定模型回放温度
```

它适合回答：

- 当前固件使用的模型是否还能解释新实验；
- 模型误差发生在哪个阶段；
- 延迟是否明显；
- 实测输入变化和模型输入是否对齐；
- 参数更新后回放是否更接近真实曲线。

它和 `imu_temp_identify.py` 的区别是：

```text
temp_identify
    从数据估计模型参数

open_loop_replay
    用固定参数验证模型
```

这是“拟合”和“验证”分离。

### 16.7 `imu_closed_loop_identify.py`

这个脚本针对闭环加热日志。

它估计：

```text
G(s) = K * exp(-L*s) / (tau*s + 1)
```

输入不是简单的阶梯 duty。

而是 PID 控制器实际产生的 duty。

它需要识别：

- `imu ready`；
- `Cooldown Done`；
- 正式采样起点；
- 时间戳；
- 温度；
- duty。

它支持：

- serial 在线采集；
- CSV 离线读取；
- 时间单调性检查；
- 延迟网格；
- 时间常数搜索；
- 最小二乘估计；
- RMSE/MAE；
- 预测曲线；
- 误差曲线。

它的作用不是替代 `heater.cpp`。

它是为了判断：

```text
固件闭环工作时，
实际输入输出是否仍然符合当前热对象模型。
```

### 16.8 `stage_fit.py`

`stage_fit.py` 更偏阶段曲线工具。

它会：

- 从日志中提取每个 Heating 阶段；
- 按 `(run, stage)` 分组；
- 记录每阶段 duty；
- 对温度曲线做多项式拟合；
- 支持多轮；
- 画出阶段曲线；
- 计算 RMSE。

它适合快速观察：

```text
不同 duty 阶段的曲线形状
```

但它和一阶惯性模型拟合不是同一个目标。

多项式拟合适合：

- 快速可视化；
- 阶段曲线比较；
- 看数据形状；

不应自动等价为：

```text
最终可部署的物理模型
```

### 16.9 `tune_imu_pid.py`

这个脚本是控制器仿真工具。

它显式对齐固件控制路径：

```text
modules/imu/drivers/heater.cpp
    -> algorithm/controller/pid/pid.cpp
```

它模拟：

- 一阶热对象；
- 纯延迟；
- 控制周期；
- PID 内部积分周期；
- 积分限幅；
- 输出限幅；
- 饱和后的积分清零；
- 误差换向；
- 加热 duty 限制；
- 稳态评价。

因此它不只是一个泛化的：

```text
Kp/Ki/Kd 调参脚本
```

它的目标是：

> 让主机侧仿真的 PID 行为尽可能接近当前固件里的实际 PID 和 heater 逻辑。

### 16.10 `uart_perf.py`

`uart_perf.py` 是主机侧 UART 接收性能工具。

它会：

1. 列出串口；
2. 打开串口；
3. 等待固件启动标志；
4. 发送不同长度的数据；
5. 等待固件输出 `[BENCH]` 报告；
6. 解析 bytes、reads、min、max、avg、cycles、time、bandwidth；
7. 输出汇总表。

测试用例包括：

- 16 字节小包；
- 64 字节中包；
- 256 字节大包；
- 512 字节大包；
- 50 KB 连续流；
- 200 KB 连续流；
- 间隔突发；
- 混合长度；
- 单字节延迟。

这类脚本的价值是把：

```text
UART 驱动性能
```

从“感觉应该没问题”变成：

```text
实际读取次数、耗时和吞吐数据
```

### 16.11 Python 脚本的共同设计特点

当前脚本整体有几个共同特点。

#### 支持在线和离线两种输入

```text
在线：
    serial.Serial()

离线：
    log/CSV
```

这很重要。

硬件测试失败时，可以保留日志重新分析。

不必每次都重新占用设备。

#### 识别固件状态机输出

脚本不是盲目等待一段时间。

它会识别：

- `imu ready`；
- `Cooldown Done`；
- `Stage Done`；
- `Finished`；
- `Safety Stop`；
- `set autoident mode`。

这意味着：

```text
固件日志是实验协议的一部分。
```

#### 显式报告阶段

脚本会输出：

- 当前阶段；
- 当前状态；
- 当前样本数；
- 当前温度；
- 当前 duty；
- 当前拟合状态；
- 当前错误；
- 最终结果。

这比“脚本静默运行，最后只吐一个数字”更适合硬件实验。

#### 输出中间结果

脚本通常可以输出：

- CSV；
- PNG；
- RMSE；
- MAE；
- 参数汇总；
- 预拟合检查；
- 预测误差。

这让一次实验更容易复现。

---

## 十七、固件和 Python 脚本如何协作

### 17.1 典型闭环

当前 IMU 加热辨识可以画成：

```text
Python 脚本
    -> UART 发送 OpenIdent / ClosedIdent

modules/imu/heater
    -> 读取指令
    -> 执行辨识状态机
    -> 设置 PWM duty
    -> 读取 IMU 温度
    -> 输出日志

Python 脚本
    -> 接收日志
    -> 解析阶段
    -> 拟合/回放/画图
    -> 输出模型参数
```

### 17.2 命令协议

当前 `Identifier` 支持：

```text
OpenIdent
ClosedIdent
Stop
```

这不是完整的通用 shell。

它是一个面向 IMU 辨识器的最小串口控制协议。

它与 `cmd/` 的关系是：

```text
cmd/
    适合长期存在的固件交互命令

Identifier UART command
    面向实验流程的专用控制入口
```

未来可以考虑是否把稳定的实验命令迁移到 `cmd/shell/`。

但在实验快速收敛阶段，模块内部命令也有价值。

### 17.3 日志协议

当前辨识日志至少包含：

```text
seq
t_us
dt_us
stage
state
temp_c
duty
```

这些字段分别帮助脚本完成：

| 字段 | 用途 |
| --- | --- |
| `seq` | 检查样本顺序、丢帧 |
| `t_us` | 重建真实时间轴 |
| `dt_us` | 检查采样间隔 |
| `stage` | 区分不同占空比阶段 |
| `state` | 过滤 Cooldown/Heating/Finished |
| `temp_c` | 温度输出 |
| `duty` | 热对象输入 |

### 17.4 为什么日志格式必须稳定

Python 脚本不是读取 C++ 内部变量。

它只能看到：

- 串口输出；
- 文件；
- CSV。

如果日志字段经常变化：

- 解析器需要跟着改；
- 历史数据难以复用；
- 实验比较失去一致性；
- 自动化测试难以维护。

因此日志格式应该被当成：

```text
固件和主机脚本之间的接口
```

### 17.5 版本化和兼容

如果未来要正式扩大脚本使用范围，建议逐步增加：

- 日志格式版本；
- 固件版本；
- 板卡名称；
- IMU 型号；
- 参数配置；
- 单位声明；
- 运行编号。

但这类字段应当在确实需要时加入。

不要为了“看起来完整”而把日志变成难以解析的长字符串。

### 17.6 脚本不是固件的第二个业务层

Python 可以：

- 拟合；
- 分析；
- 仿真；
- 生成参数；
- 评估；

但它不应该成为固件运行时唯一的安全保障。

例如：

```text
Python 说温度没问题
```

不能替代固件里的：

```text
超温保护
```

固件必须拥有本地实时安全逻辑。

Python 负责：

```text
实验控制、数据解释和参数辅助
```

而不是替代 MCU 的实时保护。

---

## 十八、从一个真实功能看完整调用链

### 18.1 遥控到云台

```text
板级 overlay
    -> remote_uart alias

drivers/communication/uart
    -> UartDma

modules/remotes
    -> RemoteProtocol
    -> DR16/SBUS/VT12/VT13
    -> topic::remote_to::Message

topic/remote_to
    -> zbus channel

project/thread/gimbal
    -> 读取 yaw/pitch
    -> 位置环
    -> 速度环
    -> topic::to_can_tx

project/thread/can
    -> k_msgq
    -> Can::Send()
```

### 18.2 IMU 到姿态消息

```text
project/boards
    -> imu_spi / imu_pwm alias

drivers/spi + drivers/pwm
    -> 底层外设接口

modules/imu/devices/bmi088
    -> Source
    -> ReadRaw
    -> 单位换算
    -> 校准

modules/imu/drivers/imu
    -> ImuManager
    -> Sample

modules/imu/drivers/heater
    -> PWM + PID/辨识

modules/imu/drivers/processor
    -> QuaternionEkf
    -> topic::imu_to::Message

topic/imu_to
    -> zbus
```

当前发布调用是否真正启用，应以 `modules/imu/drivers/imu.cpp` 的 live tree 为准。

### 18.3 底盘控制

```text
remote_to
    -> ReadRemote()
    -> g_vx/g_vy/g_vw

UpdateTarget()
    -> 运动学
    -> 轮子目标角/速度

ControlCalculate()
    -> 角度环
    -> 速度环
    -> 力矩环

PowerAlloc()
    -> 预测
    -> 功率约束
    -> 电流限幅

FramePublish()
    -> to_can_tx

can thread
    -> CAN driver
```

### 18.4 IMU Python 辨识

```text
scripts/imu/imu_temp_identify.py
    -> 串口发命令

heater::Identifier
    -> OpenLoop/ClosedLoop

IMU temperature
    -> firmware log

Python parser
    -> stage segmentation
    -> data quality gates
    -> model fit
    -> plots/CSV
```

这条链证明：

```text
项目架构不仅覆盖 MCU 内部模块，
也覆盖 MCU 与开发机之间的测试接口。
```

---

## 十九、新增功能时每一层应该怎么改

### 19.1 新增一个底层外设驱动

适合步骤：

1. 在 `drivers/communication/` 或 `drivers/device/` 下建目录；
2. 用 `.hpp + .cpp` 提供 C++ 接口；
3. 只封装 Zephyr device/devicetree API；
4. 在 `drivers/Kconfig` 注册开关；
5. 在 `drivers/CMakeLists.txt` 添加源码；
6. 在模块或线程中使用；
7. 用 board overlay 提供具体设备 alias。

不应该在驱动层做：

- 遥控协议；
- 底盘模式；
- 姿态融合；
- 电机业务。

### 19.2 新增一种 IMU

适合步骤：

1. 在 `modules/imu/devices/<name>/` 建文件；
2. 继承 `Source` 或 `CalibratedImuSource`；
3. 实现寄存器初始化；
4. 实现原始数据读取；
5. 实现单位换算；
6. 准备默认校准参数；
7. 用 `REGISTER_IMU()` 注册；
8. 在 `modules/Kconfig` 增加设备开关；
9. 在 `modules/CMakeLists.txt` 加编译段；
10. 在板级 overlay 中提供 SPI/GPIO/PWM 绑定；
11. 通过 `ImuManager` 进入统一后端。

不要把新的芯片选择直接写进：

```text
project/apps/Init_entry.cpp
```

项目启动器只应该启动 IMU 模块。

### 19.3 修改 IMU 后端算法

如果是纯算法：

```text
algorithm/filter/
```

如果是把样本接入姿态处理器：

```text
modules/imu/drivers/processor.*
```

如果是修改线程周期或业务组合：

```text
project/thread/imu/
```

如果是修改主机侧拟合：

```text
scripts/imu/
```

这样可以避免把：

```text
算法问题
```

和：

```text
设备采集问题
```

和：

```text
测试脚本问题
```

混在同一个文件里。

### 19.4 新增遥控协议

适合步骤：

1. 在 `modules/remotes/<name>/` 建协议文件；
2. 继承 `RemoteProtocol`；
3. 设置 `line_cfg_`；
4. 实现 `Validate()`；
5. 实现 `Decode()`；
6. 转换到 `remote_to::Message`；
7. 添加 `REGISTER_REMOTE()`；
8. 在 Kconfig 中提供开关；
9. 在 CMake 中加入源文件；
10. 用日志或离线帧测试。

不需要修改：

- `Remote` 核心状态机；
- 底盘线程；
- 云台线程；
- `remote_to::Message`，除非语义字段确实不足。

### 19.5 落地双 UART

如果未来实现 `doc/remote_dual_arch.md`，建议分阶段：

1. 保持当前单 UART 热路径；
2. 把单个 `detect_` 抽象成可索引状态；
3. 引入 `uart_[2]`；
4. 保持一个解析线程；
5. 实现冷路径切换；
6. 清理 stale buffer；
7. 增加切换冷却；
8. 增加两个板级 alias；
9. 用两个实际输入源测试；
10. 再更新 `project/thread/remote/trd_remote.cpp`。

不要一开始就把：

- 两个线程；
- 两套 topic；
- 两个 Remote 对象；
- 业务层仲裁；

全部引入。

那会让切换复杂度扩散到整个系统。

### 19.6 新增 topic

先回答：

```text
这是状态广播，还是逐条事件？
```

如果是状态广播：

```text
优先考虑 zbus
```

如果是逐条输出帧：

```text
优先考虑 k_msgq
```

然后：

1. 定义消息结构；
2. 定义数据单位；
3. 定义有效性；
4. 定义发布者；
5. 定义消费者；
6. 定义队列容量或 observer；
7. 加 Kconfig；
8. 加 CMake；
9. 添加最小测试。

### 19.7 新增业务线程

1. 在 `project/thread/<name>/` 建目录；
2. 声明局部模块实例；
3. 写 `thread_init()`；
4. 写 `thread_start()`；
5. 在 `project/thread/Kconfig` 加开关；
6. 用 `select` 拉起依赖；
7. 在 `project/thread/CMakeLists.txt` 加源码；
8. 注册 `REGISTER_INIT()`；
9. 选择正确阶段和等级；
10. 把跨线程数据放入 `topic/`；
11. 把纯算法放入 `algorithm/`；
12. 把设备能力放入 `modules/`。

### 19.8 新增命令

如果是临时实验：

```text
project/thread/test
    + scripts/
```

如果是固件长期交互能力：

```text
cmd/shell/
```

如果是构建快捷命令：

```text
cmd/build/
```

三种用途不要混。

### 19.9 新增 Python 功能测试

建议步骤：

1. 明确固件输入命令；
2. 明确固件输出日志格式；
3. 明确结束条件；
4. 支持在线串口；
5. 支持离线日志；
6. 保存原始数据；
7. 输出质量检查；
8. 输出结果文件；
9. 输出可视化；
10. 把脚本参数和固件参数写在同一份说明中。

脚本不能只在自己的代码里假设：

```text
固件一定会在 30 秒后结束。
```

应该优先识别：

- `Finished`；
- `Safety Stop`；
- `Cooldown Done`；
- `imu ready`；
- 版本或启动标志。

---

## 二十、当前架构的优点和代价

### 20.1 优点一：项目差异有明确归属

当前最重要的架构收益是：

```text
板子差异
    -> project/boards

启动差异
    -> project/apps

业务差异
    -> project/thread

公共能力
    -> drivers/modules/algorithm/topic
```

这让新项目不必从根目录开始到处改。

### 20.2 优点二：能力可以被测试线程重新组合

`trd_test` 可以把：

- 电机；
- CAN；
- RLS；
- 定时器；
- topic；

组合成实验。

这说明框架层能力并没有被某个正式业务线程完全锁死。

### 20.3 优点三：初始化和中断开始具备扩展性

`REGISTER_INIT()` 和 `CAN_RX_HANDLER()` 都在减少中央分发文件的膨胀。

这让：

- 初始化；
- CAN ID；
- 协议；
- IMU 源；

可以更靠近实际实现文件注册。

### 20.4 优点四：算法可被固件和脚本分别使用

固件侧：

```text
algorithm/controller
algorithm/filter
algorithm/identify
```

脚本侧：

```text
numpy
matplotlib
拟合模型
```

两者不一定共享同一份 Python/C++ 代码。

但可以共享：

- 模型定义；
- 参数含义；
- 单位；
- 日志字段；
- 采样时间语义；
- 评价指标。

这是一种更现实的“跨语言复用”。

### 20.5 优点五：当前工程已经有真实业务

这套架构不是空架子。

当前已经有：

- CAN；
- UART；
- SPI；
- USB；
- PWM；
- GPIO；
- IMU；
- 遥控器；
- DJI/DM 电机；
- 底盘；
- 云台；
- 功率控制；
- TFLM；
- Python 辨识工具。

所以架构讨论可以落到真实链路。

### 20.6 代价一：第一次阅读成本高

一个功能可能需要沿着：

```text
board overlay
    -> Kconfig
    -> CMake
    -> driver
    -> module
    -> topic
    -> thread
    -> init registration
    -> Python script
```

才能看完整。

这比单文件工程慢。

### 20.7 代价二：Kconfig/CMake/链接器需要同时理解

仅懂 C++ 不足以完全读懂当前架构。

还要理解：

- Kconfig 符号；
- CMake 条件；
- devicetree；
- linker section；
- Zephyr 线程；
- zbus；
- k_msgq；
- shell。

这使新人上手门槛升高。

### 20.8 代价三：边界需要持续维护

分层不是一次性工作。

随着业务增长，容易出现：

- 线程直接操作底层设备；
- 模块开始承担项目策略；
- topic 变成万能结构体；
- algorithm 偷偷依赖硬件；
- scripts 和固件日志失去同步；
- cmd 和实验专用串口命令重复。

这些都需要通过代码 review 和文档持续压住。

### 20.9 代价四：部分规划和实现仍有时间差

当前明确存在：

- IMU 注册设计文档和 live implementation 同时存在；
- remote 双 UART 文档规划但代码仍单 UART；
- cmd 架构文档已经存在但命令实现尚未铺开；
- 某些 README 仍保留历史路径表述；
- `topic` 发布调用需要以当前源码为准。

因此文档写作必须使用：

```text
已实现
    规划中
    过渡中
```

三种状态标记。

---

## 二十一、当前仍然需要继续收敛的地方

### 21.1 `cmd` 需要真正形成命令接口

下一步可以优先建立最小命令集：

```text
help
version
status
imu
remote
motor
```

然后逐步加入：

- PID 参数查看；
- PID 参数修改；
- IMU 当前状态；
- 遥控器当前协议；
- CAN 统计；
- 线程运行状态；
- topic 更新时间。

### 21.2 IMU 发布路径需要明确

当前 IMU 后端已经填充 `pub_`，topic 也已经定义。

但发布调用在 live code 中仍处于注释状态。

需要明确：

- 当前是否故意关闭；
- 哪个项目打开；
- 是否通过 Kconfig 控制；
- 订阅者是否会阻塞；
- 发布失败如何处理；
- topic 队列是否需要扩容。

### 21.3 IMU 配置符号需要持续统一

IMU 辨识相关代码同时出现：

- `CONFIG_IMU_IDENTIFICATION`
- `CONFIG_MOD_DEV_IMU_IDENT`

这类符号可以存在不同层次。

但必须明确：

```text
项目功能开关
    -> 模块开关
    -> C++ 条件编译
```

否则容易出现：

- Kconfig 已经打开；
- 某段 C++ 没有编译；
- 另一段 C++ 却以为功能已存在。

### 21.4 双 UART 要保持冷路径复杂、热路径简单

双 UART 落地时最重要的不是先把数据结构写大。

而是保持：

```text
Locked:
    当前 UART Read -> Decode -> Publish

Timeout:
    检查另一路 -> 切换 -> 清理 -> 再探测
```

不要把切换判断放进每帧热路径。

### 21.5 串口实验协议需要更稳定

当前脚本已经依赖：

- `imu ready`
- `Cooldown Done`
- `Finished`
- `Safety Stop`
- `seq=...`

如果这些日志被随意修改，主机侧工具会失效。

建议未来把：

- 日志版本；
- 事件名称；
- 样本字段；
- 单位；

逐步形成稳定约定。

### 21.6 `scripts/` 需要一个统一入口说明

当前脚本功能已经不少。

但新用户仍然需要自己猜：

- 先采集还是先回放；
- 开环和闭环脚本怎么选；
- 日志格式是什么；
- 结果文件输出到哪里；
- 哪个参数来自固件；
- 哪个参数只是仿真默认值。

后续可以在 `scripts/README.md` 中增加：

```text
实验目标
    -> 固件配置
    -> 运行命令
    -> 输入日志
    -> 输出文件
    -> 判定标准
```

### 21.7 生成的 Python 缓存不属于架构

`scripts/imu/__pycache__`、`scripts/uart/__pycache__` 属于 Python 运行缓存。

它们不属于架构设计。

后续应确保：

- 不把缓存当成脚本；
- 不把缓存纳入文档索引；
- 在版本控制中按需要忽略。

### 21.8 项目层不要继续无限增重

`project/thread/` 很容易成为：

```text
所有暂时不知道放哪里的代码
```

应该持续用下面的判断清理：

```text
纯计算
    -> algorithm

设备能力
    -> modules

底层外设
    -> drivers

跨线程数据
    -> topic

项目组合和周期业务
    -> project/thread

临时实验
    -> project/thread/test

主机侧实验
    -> scripts
```

---

## 二十二、推荐源码阅读顺序

### 22.1 第一步：理解项目入口

先读：

```text
src/main.c
project/apps/Init_entry.hpp
project/apps/Init_entry.cpp
cmd/linker/tflm_init.ld
```

目标：

- 知道系统怎么启动；
- 知道注册项怎么进入镜像；
- 知道阶段和失败等级。

### 22.2 第二步：理解编译期选择

再读：

```text
Kconfig
prj.conf
CMakeLists.txt
project/thread/Kconfig
project/thread/CMakeLists.txt
```

目标：

- 知道项目怎么打开；
- 知道线程怎么选择；
- 知道依赖怎么拉起；
- 知道源文件什么时候加入编译。

### 22.3 第三步：理解 topic

推荐：

```text
topic/remote_to/remote_to.hpp
topic/imu_to/imu_to.hpp
topic/to_can_tx/to_can_tx.hpp
topic/remote_to/remote_to.cpp
topic/imu_to/imu_to.cpp
```

目标：

- 知道线程之间传什么；
- 知道状态和帧队列的区别；
- 知道消息结构的语义。

### 22.4 第四步：理解 drivers

推荐：

```text
drivers/communication/uart/
drivers/communication/can/
drivers/communication/spi/
drivers/device/pwm/
drivers/device/gpio/
```

目标：

- 知道 Zephyr device 如何进入项目；
- 知道 UART/CAN/SPI/PWM 的公共接口；
- 知道底层驱动不应该放业务。

### 22.5 第五步：理解 modules

推荐：

```text
modules/remotes/remote.hpp
modules/remotes/remote.cpp
modules/remotes/protocol_base.hpp
modules/imu/drivers/imu.hpp
modules/imu/drivers/imu.cpp
modules/imu/devices/imu_device_layer.hpp
modules/imu/drivers/heater.hpp
modules/imu/drivers/processor.hpp
```

目标：

- 知道设备能力如何封装；
- 知道协议和芯片如何注册；
- 知道 IMU 前后端如何分开；
- 知道当前实现和规划的差异。

### 22.6 第六步：理解算法

推荐：

```text
algorithm/controller/pid/
algorithm/controller/power_ctrl/
algorithm/filter/kalman/
algorithm/filter/quaternion/
algorithm/identify/rls/
algorithm/identify/motor/
algorithm/tflm/
```

目标：

- 知道纯算法和设备模块如何分工；
- 知道控制热路径；
- 知道辨识算法如何被测试线程使用；
- 知道 MCU 推理运行时的位置。

### 22.7 第七步：理解项目线程

最后读：

```text
project/thread/can/trd_can_tx.cpp
project/thread/remote/trd_remote.cpp
project/thread/imu/trd_imu.cpp
project/thread/chassis/trd_chassis.cpp
project/thread/gimbal/trd_gimbal.cpp
project/thread/test/trd_test.cpp
```

目标：

- 看模块如何被实例化；
- 看 topic 如何被消费；
- 看业务周期如何组织；
- 看正式线程和实验线程如何区分。

### 22.8 第八步：看 Python 实验层

最后看：

```text
scripts/imu/imu_temp_identify.py
scripts/imu/imu_open_loop_replay.py
scripts/imu/imu_closed_loop_identify.py
scripts/imu/tune_imu_pid.py
scripts/uart/uart_perf.py
```

目标：

- 知道固件输出什么日志；
- 知道 Python 如何控制实验；
- 知道模型如何从数据得到；
- 知道参数如何回到固件。

---

## 二十三、关键文件索引

### 23.1 总入口

```text
src/main.c
CMakeLists.txt
Kconfig
prj.conf
```

### 23.2 `cmd`

```text
cmd/README.md
cmd/ARCHITECTURE.md
cmd/Kconfig
cmd/CMakeLists.txt
cmd/build/build.ps1
cmd/build/build.bat
cmd/linker/tflm_init.ld
```

### 23.3 `algorithm`

```text
algorithm/ARCHITECTURE.md
algorithm/Kconfig
algorithm/CMakeLists.txt
algorithm/controller/pid/
algorithm/controller/power_ctrl/
algorithm/filter/kalman/
algorithm/filter/quaternion/
algorithm/identify/rls/
algorithm/identify/motor/
algorithm/tflm/
```

### 23.4 `modules`

```text
modules/Kconfig
modules/CMakeLists.txt
modules/ARCHITECTURE.md

modules/imu/drivers/imu.hpp
modules/imu/drivers/imu.cpp
modules/imu/drivers/heater.hpp
modules/imu/drivers/heater.cpp
modules/imu/drivers/processor.hpp
modules/imu/drivers/processor.cpp
modules/imu/devices/imu_device_layer.hpp
modules/imu/devices/bmi088/
modules/imu/devices/icm42688p/

modules/remotes/README.md
modules/remotes/remote.hpp
modules/remotes/remote.cpp
modules/remotes/protocol_base.hpp
modules/remotes/dr16/
modules/remotes/sbus/
modules/remotes/vt12/
modules/remotes/vt13/
```

### 23.5 `project`

```text
project/README.md
project/ARCHITECTURE.md
project/CMakeLists.txt

project/apps/Init_entry.hpp
project/apps/Init_entry.cpp
project/apps/Irq_handlers.h
project/apps/Irq_handlers.cpp

project/boards/
project/thread/Kconfig
project/thread/CMakeLists.txt
project/thread/thread.hpp
```

### 23.6 `topic`

```text
topic/README.md
topic/ARCHITECTURE.md
topic/Kconfig
topic/CMakeLists.txt
topic/remote_to/
topic/imu_to/
topic/to_can_tx/
```

### 23.7 `scripts`

```text
scripts/imu/imu_temp_identify.py
scripts/imu/imu_open_loop_replay.py
scripts/imu/imu_closed_loop_identify.py
scripts/imu/stage_fit.py
scripts/imu/tune_imu_pid.py
scripts/uart/uart_perf.py
```

### 23.8 规划和架构记录

```text
doc/imu_register_arch.md
doc/remote_dual_arch.md
doc/整改记录.md
doc/三个嵌入式框架对比总结.md
```

另外，当前 `modules/CMakeLists.txt` 中 DJI 电机源文件路径仍写作
`motors/dji/dji.cpp`，而 live tree 中实际文件名是
`motors/dji/dji_c6xx.cpp`。这属于构建配置与文件命名之间的待收敛项，
不是模块职责变化，但在阅读或继续维护构建链时应当单独留意。

阅读这些文档时应注意时间状态：

- 有些是设计规划；
- 有些是问题整改；
- 有些是历史架构解释；
- 有些已经被当前源码实现；
- 有些仍然没有落地。

---

## 二十四、最终总结

### 24.1 这套架构的真正核心

`tflm` 的核心不是某个目录。

也不是某个宏。

而是下面几层关系共同成立：

```text
drivers
    把 Zephyr 外设翻译成稳定接口

modules
    把外设组合成设备能力

algorithm
    把控制、滤波、辨识和推理组织成纯计算能力

topic
    把线程之间的数据和传输语义写成契约

project
    把这些能力装成一个具体嵌入式项目

cmd
    提供固件内运行时控制面

scripts
    提供主机侧实验、测试和分析面
```

### 24.2 为什么 `project/` 是核心装配层

因为框架层提供的是：

```text
能力
```

而 `project/` 决定的是：

```text
这次系统选择哪些能力，
按照什么周期运行，
在哪块板上运行，
通过哪些 topic 连接，
如何处理启动和中断。
```

所以 `project/` 不是简单的 app 目录。

它是框架到真实项目之间的装配边界。

### 24.3 为什么 `scripts/` 也应该被纳入架构说明

因为真实嵌入式开发不是：

```text
固件写完就结束
```

而是：

```text
固件采集
    -> 主机分析
    -> 模型辨识
    -> 参数调整
    -> 固件回灌
    -> 再次验证
```

Python 脚本让：

- IMU 加热模型；
- PID；
- UART 性能；
- 电机辨识；
- 日志质量；

可以在 PC 上更快迭代。

它们不替代固件。

它们让固件更容易被验证。

### 24.4 当前最值得继续坚持的规则

建议继续坚持：

1. 算法不持有硬件句柄；
2. 驱动不实现业务；
3. 模块不吞掉整机策略；
4. topic 明确消息语义；
5. project 负责组合和周期；
6. `trd_test` 负责快速实验；
7. `cmd` 负责固件内交互；
8. `scripts` 负责主机侧实验；
9. 初始化和中断尽量通过注册表扩展；
10. 规划文档必须标明“已实现/规划中”。

### 24.5 最后一句话

> `tflm` 不是把嵌入式代码拆成几个目录，而是把“板级能力、设备能力、纯算法、数据契约、项目装配、固件命令和主机实验”放进一套可以逐步扩展的系统里。

更直白地说：

```text
固件负责实时运行。

模块负责设备能力。

算法负责计算和模型。

topic 负责数据关系。

project 负责装配项目。

cmd 负责运行时交互。

scripts 负责测试、辨识、回放和分析。
```

这几层一起工作，才是当前 `tflm` 的完整架构。
