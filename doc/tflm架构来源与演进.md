# tflm 架构来源与演进

## 从组件原型到项目装配型嵌入式平台

## 1. 文档定位

这篇文档不再按照“`drivers/` 是什么、`modules/` 是什么、`project/` 是什么”的方式单独介绍目录。

目录说明只能回答：

```text
现在有哪些东西
```

而这篇文档想回答的是：

```text
为什么会出现这些东西
它们分别解决了什么问题
为什么当前架构会变成现在这个样子
哪些设计已经落地
哪些设计仍然只是规划
```

因此，本文把 `tflm` 看成一个不断被真实问题推动的工程，而不是一次性设计完成的静态框架。

当前仓库中的架构，大致经历了下面这条路线：

```text
直接编写嵌入式功能
    ->
从已有框架学习分层、消息和工程规范
    ->
整理单板外设和控制组件
    ->
完成真实机器人整机项目
    ->
提炼出自己的组件化原型
    ->
解决原型中的启动、依赖、线程和边界问题
    ->
形成当前 tflm 的 drivers / modules / algorithm / topic / cmd / project / scripts
    ->
继续规划为可被外部业务项目嵌入的独立框架
```

其中，最重要的变化不是目录数量越来越多，而是“变化应该被放在哪里”这件事越来越明确。

例如：

- 换芯片和外设实例，应该主要影响 `project/boards/` 和 `drivers/` 的适配；
- 换 IMU 型号，应该主要增加或替换 `modules/imu/devices/` 的子类；
- 换遥控器协议，应该增加协议实现，而不是重写整个 UART 接收链；
- 换滤波器和控制算法，应该影响 `algorithm/` 或项目线程，而不是驱动层；
- 增加一个业务线程，应该主要修改 `project/thread/`；
- 增加调试变量，不应该再建立一套独立的调试系统；
- 改进架构本身，不应该要求每个业务项目复制一份框架代码；
- 做 PC 侧实验，不应该把 Python 代码塞进 MCU 的运行时工程。

这就是当前 tflm 的核心方向：

> 让变化进入正确的层，让新增功能尽量沿着已有边界扩展，而不是把修改扩散到整个工程。

---

## 2. 先给出结论

当前 tflm 不是凭空设计出来的，也不是把某一个开源框架原样复制过来。

它实际上由四条经验线共同推动：

```text
成熟嵌入式框架
    -> 提供分层、消息、文档和复用意识

STM32H7 单板模板
    -> 提供板级外设、CMake 工具链和任务组织经验

真实机器人整机项目
    -> 提供设备拓扑、控制闭环、保护逻辑和联调经验

自己的 Zephyr_Components 原型
    -> 提供组件拉取、骨架复用和模块依赖管理的早期尝试
```

当前 tflm 又在这些经验之上继续做了几次更深的抽象：

```text
从“代码目录分开”
    发展为“职责边界分开”

从“手工调用初始化函数”
    发展为“编译期注册、链接期收集、运行时分阶段执行”

从“模块直接包含具体业务”
    发展为“驱动、模块、算法、topic、项目线程各自承担不同职责”

从“一个项目复制一份框架”
    发展为“project/ 作为当前项目装配层，未来再把业务仓库外置”

从“PC 脚本只是临时工具”
    发展为“scripts/ 作为固件实验、辨识、回放和验证闭环的一部分”
```

因此，tflm 的真正来源不是某一个目录，而是一连串具体的工程问题：

1. 模块之间互相包含，新增功能会牵连很多文件；
2. 外设代码、设备协议和业务控制混在一起，换设备时修改面过大；
3. 初始化顺序依赖人工维护，新增组件必须修改集中式启动代码；
4. 线程和模块的关系不清楚，有的模块应该主动运行，有的模块只应该提供状态；
5. 线程之间直接共享全局变量，数据流和数据所有权不够明确；
6. 同一套能力无法方便地被 Demo、正式业务和实验代码复用；
7. Python 实验脚本与固件代码之间缺少稳定的验证闭环；
8. 框架代码和具体机器人业务放在同一个工程里，长期难以外置和复用。

当前架构可以理解为对这些问题的逐项回应。

---

## 3. 这套架构最初想解决什么

### 3.1 目标不是“目录好看”

如果只是为了让目录看起来整齐，可以把文件按照设备、算法和任务分成几个文件夹。

但目录分开不等于耦合度降低。

真正的问题是：

```text
一个新增需求出现时，
到底需要修改几个地方？
这些修改是局部的，还是会沿着整个调用链扩散？
```

例如，增加一个新的遥控器协议，至少会涉及：

```text
UART 接收
    -> 缓冲区
    -> 帧边界
    -> 协议识别
    -> 数据校验
    -> 通道解码
    -> 状态发布
    -> 业务线程使用
```

如果这些逻辑全部放在一个线程文件里，第一次实现可能很快，但后续会出现：

- 协议解析和 UART 细节绑死；
- 新协议需要复制一整份接收线程；
- 数据发布方式跟着协议实现变化；
- 业务线程知道太多底层细节；
- 单独测试协议解析很困难；
- 后续想做双 UART 冗余时，几乎只能整体重写。

当前 tflm 的设计目标，就是让这条链被拆成可以独立替换的部分：

```text
drivers/communication/stream/uart/
    只负责 UART DMA 字节流和接收通知

modules/remotes/
    负责遥控器协议识别、解码和状态管理

topic/remote_to/
    负责把遥控器状态表达为线程间契约

project/thread/remote/
    负责把远程控制能力接入当前项目

project/thread/chassis/
    只消费业务需要的数据，不关心 UART 和协议细节
```

这不是为了让代码层数变多，而是为了让每一层只承担一种变化。

### 3.2 目标是降低新增功能的痛苦

架构是否有价值，可以用一个很实际的问题检验：

> 新增一个功能时，原有代码要不要大面积重写？

如果新增 IMU 只需要：

```text
增加一个设备子类
增加一个设备树节点或 board 配置
在 Kconfig/CMake 中打开对应能力
```

那么这个系统的扩展性就比“复制一份旧 IMU 驱动，再修改线程和业务代码”更好。

如果新增 shell 调试变量只需要：

```cpp
REGISTER_SHELL_VAR("imu_gain", imu_gain);
```

而不需要修改中央变量表，那么这套机制就把调试能力的扩展成本降下来了。

如果增加一个初始化项只需要：

```cpp
REGISTER_INIT(thread_init, EarlyInit, High, "imu_init");
```

而不需要同时修改 `main.c`、集中式初始化函数、任务控制块和启动顺序表，那么这个注册机制就真正承担了架构价值。

### 3.3 目标是让项目差异停留在项目层

当前 tflm 最核心的边界之一是：

```text
框架层：
    drivers/
    modules/
    algorithm/
    topic/
    cmd/

项目层：
    project/apps/
    project/boards/
    project/thread/
```

框架层提供可复用能力。

项目层决定：

- 当前使用哪一块板；
- 当前实例化哪些设备；
- 当前有哪些机器人线程；
- 当前业务如何组合这些能力；
- 当前实验需要打开哪些模块；
- 当前项目如何处理板级启动和中断。

理想状态下，换一台机器人时，首先变化的是 `project/`，而不是框架层的公共实现。

这也是后来“让业务层嵌入架构层”规划的来源。

---

## 4. 外部参考带来的四种影响

当前 tflm 不是只从一个项目学习。

此前对比过的几个工程，各自解决了不同的问题：

| 参考来源 | 主要提供的启发 | 最终被吸收的方向 |
| --- | --- | --- |
| `basic_framework-master` | 分层、消息中心、文档和队伍传承 | 当前的职责边界、topic 意识和架构文档 |
| `COD_H7_Template_CLion-main` | H7 单板外设、CMake、任务骨架 | 当前 drivers、板级适配和工具链经验 |
| `Dust_SentinelRobot_L_Game` | 整机对象、控制闭环、设备拓扑和实战保护 | 当前 modules、项目线程和真实数据链路 |
| `temp/Zephyr_Components-main` | 自己对组件拉取、依赖和工程骨架的早期尝试 | 当前 Kconfig/CMake/项目装配方向的前身 |

这些工程不是“谁取代谁”的关系。

它们分别回答了不同的问题：

```text
basic_framework-master
    如何做一套能够传承的队伍级底座？

COD_H7_Template_CLion-main
    如何把一块高性能控制板的外设和任务快速整理好？

Dust_SentinelRobot_L_Game
    如何把一台真实机器人完整地做出来？

Zephyr_Components-main
    如何把自己的组件、骨架和项目分支组织起来？

tflm
    如何把这些经验进一步收敛成一个可被项目嵌入的架构？
```

### 4.1 `basic_framework-master` 的影响

`basic_framework-master` 最重要的价值，不是某一个具体模块，而是它把“代码组织”提高到了“工程规则”的层面。

它明确区分：

```text
bsp
    负责底层硬件能力

module
    负责设备和通用能力

application
    负责机器人业务和整机逻辑
```

同时，它通过 `message_center` 让多个应用之间尽量不要互相直接包含。

这种设计给当前 tflm 带来了几个重要启发：

1. 层级不是目录名字，而是依赖方向；
2. 应用之间的关系应该通过数据契约表达；
3. 公共能力必须有稳定的复用边界；
4. 文档是架构的一部分，而不是代码完成后的附属品；
5. 一个框架如果想长期使用，就必须考虑后来接手的人。

当前 tflm 的 `topic/` 并不是对 `message_center` 的直接复制。

当前使用 Zephyr zbus 及其消息结构，消息契约由 `topic/` 单独表达。它继承的是“应用间需要稳定通信边界”这个思想，而不是照搬具体实现。

### 4.2 `COD_H7_Template_CLion-main` 的影响

`COD_H7_Template_CLion-main` 带来的启发更加贴近硬件工程。

它把 STM32H723 控制板上的能力整理成：

```text
Core/
Drivers/
USER/BSP/
USER/Components/Device/
USER/Components/Controller/
USER/Components/Algorithm/
USER/Application/Task/
```

这让人看到另一种重要经验：

> 在真实嵌入式项目里，硬件底座的完整性本身就是生产力。

FDCAN、UART DMA、SPI、USB、PWM、ADC、BMI088 和电机控制链，如果没有先被整理好，业务开发就会反复陷入外设初始化、DMA 回调和中断配置。

当前 tflm 的 `drivers/` 继承了这种硬件能力集中封装的思路，但又进一步把它放进了更明确的框架边界：

```text
Zephyr device / devicetree
    -> drivers/
        -> modules/
            -> project/thread/
```

同时，`COD_H7` 也暴露了另一类问题：

- 固定任务集中在 `freertos.c`；
- 任务之间依赖全局变量；
- BSP 回调直接知道具体电机和协议对象；
- CMake 虽然存在，但没有真正成为项目裁剪和依赖表达中心。

当前 tflm 的 Kconfig、CMake、topic 和链接段注册，部分就是在回应这些问题。

### 4.3 `Dust_SentinelRobot_L_Game` 的影响

Dust 的价值在于，它把很多架构讨论落到了真实机器人上。

它展示了：

```text
通信
    -> 设备对象
        -> 控制环
            -> 整机决策
                -> 保护与失联处理
```

这一类代码链路很容易让人理解：

- 电机反馈必须稳定进入控制器；
- 云台、底盘、拨弹盘和裁判系统之间确实存在跨模块决策；
- 失联处理不是可有可无的附加逻辑；
- 任务周期、设备拓扑和数据所有权必须和真实硬件对应；
- 某些场景下直接调用对象，比经过很多中间层更容易联调。

当前 tflm 并没有完全抛弃这种“对象拥有设备状态”的思想。

例如：

- IMU 设备类持有具体传感器的 SPI；
- 电机对象保存自己的 ID、反馈和控制状态；
- PowerMeter 保存自己的采样快照；
- `project/thread/chassis/` 和 `project/thread/gimbal/` 负责多设备协同控制。

但 tflm 试图把 Dust 中容易变重的整机总控拆开：

```text
设备状态
    -> modules/

纯计算
    -> algorithm/

线程间数据契约
    -> topic/

当前机器人如何组合
    -> project/thread/
```

也就是说，吸收的是 Dust 的实战经验，而不是把一个越来越大的 `Robot` 总控继续复制到新的项目里。

### 4.4 这些参考不是直接复制

当前 tflm 的特点并不是“把几个项目拼起来”。

真正发生的是：

```text
参考项目提供问题意识
    ->
真实项目提供失败和联调经验
    ->
自己的代码不断试错
    ->
最终形成新的边界
```

所以，当前架构中的某些设计可能和参考工程看起来相似，但它们所在的上下文已经不同。

例如：

- `topic/` 受到消息中心启发，但不等同于原来的消息中心；
- `modules/` 受到设备模块思想启发，但不再把线程和业务全部塞进模块；
- `project/` 类似应用层，但它还承担了板级配置和项目装配；
- `REGISTER_INIT()` 类似各种初始化宏，但它与当前链接脚本和阶段式启动器共同构成了一套新机制。

---

## 5. 真正的前身：`temp/Zephyr_Components-main`

如果只看当前的 `tflm`，很容易误以为这套架构一开始就是：

```text
drivers/
modules/
algorithm/
topic/
project/
```

实际上，当前架构有一个非常明确的自己的前身：

```text
temp/Zephyr_Components-main
```

这个工程不是普通的 CubeMX 工程，也不是单纯的 Demo。

它已经在尝试解决：

- 组件如何独立维护；
- 模块如何从远程仓库拉取；
- 工程骨架如何复用；
- 组件之间如何声明依赖；
- CMake 如何自动发现子模块；
- 线程如何管理一组设备；
- 模块如何发布数据；
- 项目如何在 Zephyr 上保持可构建。

从思想上看，它已经是 tflm 的雏形。

从实现成熟度看，它仍然更接近“架构实验场”。

### 5.1 前身的目录结构

前身的主要目录是：

```text
Zephyr_Components-main/
├── apps/
├── boards/
├── bsp/
├── config/
├── controller/
├── modules/
├── src/
├── thread/
├── topic/
├── zpull/
├── modules.yaml
├── prj.conf
└── CMakeLists.txt
```

这和当前 tflm 已经有明显的亲缘关系：

| 前身目录 | 当前对应方向 |
| --- | --- |
| `bsp/` | `drivers/` |
| `modules/` | `modules/` |
| `controller/` | `algorithm/` 的一部分 |
| `topic/` | `topic/` |
| `thread/` | `project/thread/` |
| `apps/` | `project/apps/` |
| `boards/` | `project/boards/` |
| `zpull/` | 当前计划中的外部框架/业务装配关系 |

这不是简单的目录改名，而是边界逐渐被重新定义。

### 5.2 前身最有价值的想法：组件可以被拉取

前身引入了 `zpull` 和 `modules.yaml`。

例如：

```yaml
modules:
  - repo: git@github.com:qingyu0620/Zephyr_Components.git
    ref: main
    sparse: [modules/led, modules/key, bsp/bsp_uart]
    always: [apps, thread, .vscode, src, .clangd, boards, config, prj.conf]
    shallow: [thread]
```

它表达的是：

```text
从远程仓库中按需获取组件
保留一部分项目骨架
根据模块依赖递归补齐文件
```

`zpull` 还区分了几种不同的工程恢复方式：

```text
默认拉取
    -> 获取 modules.yaml 中声明的模块

骨架模式
    -> 只恢复能够构建和启动的基础文件

项目分支
    -> 获取某一条持续演进的项目线

标签快照
    -> 恢复某个确定版本的完整工程

更新骨架
    -> 只更新框架骨架，不覆盖业务快照
```

这说明前身已经意识到：

> 框架代码和项目业务代码不应该永远以“复制整个目录”的方式复用。

这个想法后来直接影响了当前“让业务层嵌入架构层”的规划。

不过，前身的组件复用主要发生在“源码拉取层面”。

它解决的是：

```text
代码从哪里来
```

而当前 tflm 还希望继续解决：

```text
代码如何编译
组件如何启动
数据如何通信
资源如何裁剪
项目如何装配
```

这就是从 `zpull` 走向当前 Kconfig/CMake/链接段注册的一个关键变化。

### 5.3 前身已经区分了两种依赖

前身的 `module.yaml` 和 CMakeLists 中明确区分了两类依赖。

模块文件里的依赖：

```yaml
depends:
  - path: bsp/bsp_gpio
  - path: thread/led
  - path: controller/timer
```

表示：

```text
拉取这个模块的源码时，还需要把哪些路径取下来
```

CMake 中的依赖：

```cmake
add_library(mod_led INTERFACE)
target_include_directories(mod_led INTERFACE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(mod_led INTERFACE bsp_gpio)
```

表示：

```text
编译和链接这个模块时，需要依赖哪些目标
```

这是一个非常有价值的早期意识。

因为很多工程会把“文件依赖”和“编译依赖”混成一件事，最后出现：

- 文件已经拉下来了，但没有进入编译；
- CMake 能编译，但依赖文件没有被同步；
- 模块声明说有依赖，实际目标却没有链接；
- 代码能在原作者电脑上工作，换一个环境就缺文件。

前身已经开始处理这些问题。

当前 tflm 的 Kconfig/CMake 继续把这个思路推进了一步：

```text
Kconfig
    表达功能是否启用以及能力依赖

CMake
    表达哪些源文件进入本次构建

Devicetree
    表达当前项目的硬件实例

Linker section
    表达运行时需要收集的注册项
```

### 5.4 前身的自动 CMake 发现

前身的根 `CMakeLists.txt` 会扫描：

```text
bsp/
modules/
controller/
```

并自动收集子目录中的 `CMakeLists.txt` 和 `module.conf`。

它还通过：

```cmake
add_subdirectory(bsp)
add_subdirectory(modules)
add_subdirectory(controller)
```

把子组件加入工程，再根据：

```text
BSP_TARGETS
MODULE_TARGETS
CONTROLLER_TARGETS
```

统一链接到 `app`。

这种方式的优点是：

- 新增子目录比较方便；
- 不需要每次在根 CMake 手工列出所有源文件；
- 组件可以有自己的目标；
- 工程骨架具备一定自动发现能力。

但它也有明显限制：

- 目录存在不等于功能应该启用；
- CMake 自动发现不能替代功能配置；
- 组件是否编译主要靠目录和目标名；
- 依赖关系仍然需要人工维持；
- 业务线程、模块和 BSP 的关系没有被真正隔离；
- 运行时启动顺序没有由编译系统表达。

当前 tflm 没有完全放弃自动化，但更强调“显式能力开关”：

```text
Kconfig 决定是否选择能力
    ->
CMake 根据 CONFIG_XXX 决定是否编译实现
    ->
链接脚本收集注册项
    ->
运行时按照阶段执行
```

### 5.5 前身的启动模型

前身已经有了一个很重要的概念：

```cpp
void Init()
{
    System_Bsp_Init();
    System_Modules_Init();
    System_Thread_Start();
}
```

这三个函数本身当时还是空的，真正的启动工作仍然需要人工填充。

但它至少把启动过程拆成了三个阶段：

```text
BSP
    ->
Modules
    ->
Threads
```

这说明“启动顺序不应该散落在各个业务文件里”的意识已经出现。

当前 tflm 的：

```text
PreInit
PreThread
EarlyInit
EarlyThread
MidInit
MidThread
LateInit
LateThread
AppInit
AppThread
```

可以看成是对早期三段式启动的继续细化。

变化在于，当前不再要求一个中央函数手工知道所有组件，而是允许组件通过：

```cpp
REGISTER_INIT(...)
```

把自己的初始化项提交给统一启动器。

### 5.6 前身的线程模板

前身还有一个早期的线程容器：

```cpp
template<typename T, uint8_t N, size_t StackSize = 256>
class Thread
{
public:
    bool Join(const T& item);
    void Start(int prio, k_thread_entry_t entry, void* p2, void* p3);
    T& operator[](uint8_t i);
    uint8_t Count() const;
};
```

这个设计想解决的问题是：

```text
一个线程不一定只服务一个对象
```

例如：

```text
一个 LED 线程管理多个 LED
一个按键线程管理多个按键
一个 PWM 线程管理多个输出
一个舵机线程管理多个舵机
```

这是一种很有价值的资源组织思路。

但前身的实现仍然比较早期。

例如 `Start()` 接收了 `p2`、`p3`，却没有把它们传给 `k_thread_create()`，而是传入了空指针。

如果线程入口再执行：

```cpp
auto& led = *static_cast<Led*>(p2);
```

就会产生空指针解引用。

另外，容器访问越界时会回退到 `items_[0]`，这虽然避免了立即越界，但也可能把真正的逻辑错误隐藏起来。

这类问题说明：

> 前身的架构方向已经出现，但接口契约、失败策略和运行时防呆还没有收敛。

当前 tflm 继承了“线程是资源组合边界”的思想，但将线程归属重新分配给：

- 独立运行的设备模块；
- 需要多模块协同的项目线程；
- 运行时辅助能力的 `cmd` 组件。

### 5.7 前身的模块和 topic 耦合

前身的 `modules/remote_fs/fs_i6` 已经有：

- UART 初始化；
- 数据接收回调；
- 协议解析；
- zbus 发布；
- 模块自己的类；
- 模块自己的线程入口。

这说明它已经在尝试把遥控器变成一个独立模块。

但是，它的 UART 回调会直接调用解析函数：

```text
UART 回调
    -> DataProcess()
        -> 解析
        -> 发布
```

这会让中断/异步回调承担太多工作，也使得协议处理和接收驱动的节奏被绑在一起。

前身的 topic 还直接使用模块类型：

```cpp
using Message = FsI6::OutputData;
```

这意味着 topic 依赖具体模块。

更理想的方向应该是：

```text
topic 定义稳定的数据契约
modules 负责产生契约中的数据
project/thread 负责消费契约
```

而不是：

```text
topic 直接暴露某个具体设备类的内部类型
```

当前 tflm 的 `topic/remote_to/`、`topic/imu_to/` 正是在解决这种反向依赖。

### 5.8 前身的实际意义

`Zephyr_Components-main` 的意义不是“它已经成熟到可以直接作为最终框架”。

它的意义是：

```text
它第一次把组件、骨架、依赖、线程、topic 和项目组织放在同一个问题里思考
```

它是当前 tflm 的架构实验室。

如果没有这一步，很难意识到：

- 源码拉取和编译依赖不是同一个问题；
- 目录自动发现不能代替功能配置；
- 模块可以拥有能力，但不一定应该拥有线程；
- topic 不应该反向依赖具体设备类型；
- 启动顺序最终需要一种比手工函数调用更稳定的表达方式；
- 业务线程和框架组件之间需要更明确的边界。

---

## 6. 从前身到当前 tflm：真正发生了什么变化

可以把这次演进概括成六个转变。

### 6.1 从“组件能拉下来”到“组件能被正确装配”

前身重点解决：

```text
如何把模块源码拉进项目
```

当前重点进一步扩展到：

```text
如何选择模块
如何编译模块
如何满足依赖
如何初始化模块
如何让模块和项目通信
如何处理模块失败
```

因此当前出现了：

```text
Kconfig
CMake
Devicetree
topic
Linker section
REGISTER_INIT()
```

这些东西看起来分散，实际上共同解决的是“装配”问题。

### 6.2 从“按功能分目录”到“按责任分层”

早期工程里，`bsp`、`modules`、`thread` 这些目录可能都包含：

- 硬件操作；
- 数据解析；
- 线程循环；
- 业务决策；
- 初始化；
- 消息发布。

当前 tflm 则逐渐区分：

```text
drivers/
    怎么访问硬件

modules/
    一个设备或能力如何被封装

algorithm/
    如何计算

topic/
    线程之间如何表达数据

project/thread/
    当前项目如何让这些能力协同运行

cmd/
    如何提供跨项目的辅助执行能力

scripts/
    如何在主机侧验证、辨识和回放
```

### 6.3 从“所有模块都有线程”到“按运行模型决定线程归属”

前身的模块和线程关系较紧。

当前 tflm 则区分两类模块。

主动运行型模块：

```text
IMU
Remote
```

它们需要持续：

- 等待异步输入；
- 处理数据；
- 管理超时；
- 发布最新状态；
- 维护内部状态机。

因此它们可以拥有自己的线程。

被动提供型模块：

```text
Motor
PowerMeter
```

它们通常由：

- CAN 接收回调写入状态；
- 控制线程读取快照；
- 项目线程决定控制输出。

它们不需要再创建一个只负责“转发状态”的线程。

这不是简单的“有线程更高级”。

恰恰相反：

> 线程是一种运行时资源，只有当模块确实拥有独立的等待、调度和状态机时，线程才值得存在。

### 6.4 从“驱动直接理解设备协议”到“驱动提供通用输入”

前身的 UART 模块更接近：

```text
UART 回调
    -> 直接调用某个遥控器解析器
```

当前 tflm 更强调：

```text
UartDma
    -> 提供字节流、缓冲区和唤醒语义

RemoteProtocol
    -> 负责协议识别和解码

Remote
    -> 负责状态机、超时和发布
```

这样 UART 不知道：

- 这是 DR16 还是 SBUS；
- 这是遥控器还是 shell；
- 这是命令还是日志；
- 是否需要双 UART 切换。

它只提供稳定的传输能力。

### 6.5 从“中央初始化表”到“分散声明、集中执行”

早期的集中式启动是：

```text
System_Bsp_Init()
System_Modules_Init()
System_Thread_Start()
```

这种方式的优点是容易从一个文件看到全局顺序。

但它的问题是：

- 每增加一个模块都要修改中央函数；
- 中央函数越来越长；
- 初始化项和实现文件分离；
- 组件无法真正独立维护；
- 项目装配和框架实现互相牵连。

当前改为：

```text
组件本地声明 REGISTER_INIT()
    ->
链接器收集到 .user_init
    ->
System_Startup() 按阶段遍历
    ->
按等级处理失败
```

这是一种“本地声明、集中执行”的模式。

### 6.6 从“项目复制框架”到“业务嵌入框架”

前身的 `zpull` 已经尝试让项目按需拉取组件。

当前进一步思考：

```text
能不能让业务项目像使用 Zephyr 一样使用 tflm？
```

目标关系是：

```text
业务项目
    -> 使用 tflm 的 drivers/modules/algorithm/topic/cmd
    -> 使用 Zephyr 的 RTOS 和硬件抽象
```

而不是：

```text
业务项目复制整个 tflm
    -> 修改框架内部文件
    -> 最后无法区分哪些是业务，哪些是公共能力
```

当前 `project/` 仍然在同一个仓库中，它承担的是：

```text
当前参考项目
    + 框架能力验证
    + 板级配置
    + 业务线程装配
```

将来再逐步把业务项目外置。

---

## 7. 当前 tflm 的层不是凭空出现的

### 7.1 `drivers/`：把硬件细节挡在设备模块之外

`drivers/` 的来源，主要有两部分：

1. 前身 `bsp/` 的硬件封装尝试；
2. H7 单板模板中对 UART DMA、FDCAN、SPI、USB、PWM 等能力的整理经验。

当前 `drivers/` 负责：

- 保存 Zephyr device 和 devicetree 配置；
- 调用 Zephyr 外设 API；
- 管理 DMA 缓冲区；
- 处理异步回调；
- 提供 semaphore、ring buffer、BipBuffer 等同步和缓冲语义；
- 把硬件失败转化为上层可以判断的返回值；
- 对外暴露稳定的 C++ 访问对象。

它不负责：

- 遥控器协议解码；
- IMU 姿态解算；
- 底盘控制；
- 业务线程周期；
- 项目级 CAN ID 拓扑；
- 业务 topic 的定义。

典型边界是：

```text
UartDma
    只知道如何接收和发送字节

Remote
    才知道如何解释这些字节
```

```text
Spi
    只知道如何完成同步 SPI 传输

Icm42688p
    才知道寄存器地址、复位流程和量程换算
```

#### USB 为什么特殊

USB 没有被简单地当成另一个 UART。

当前 `drivers/communication/stream/usb/` 主要承担：

```text
项目侧 Stream 接口适配
```

而 USB Device 协议栈和 HPM 硬件端口位于外部用户模块：

```text
D:/Zephyr/modules/user/usb
```

这体现出一个现实判断：

> 某些底层能力已经由外部库负责，框架应该保留自己的适配边界，而不是为了“目录完整”把整个第三方协议栈复制进来。

所以 USB 既属于驱动能力，又保留了外部协议栈依赖和项目适配的特殊性。

### 7.2 `modules/`：把设备变成可组合能力

`modules/` 的直接来源是前身 `modules/`，但当前边界比前身更严格。

模块层负责：

- 具体设备类；
- 设备协议；
- 设备内部状态；
- 设备和算法的组合；
- 设备的就绪状态；
- 设备的防呆检查；
- 必要时提供独立运行线程；
- 通过 Kconfig 表达自身依赖。

模块层不负责：

- 直接操作硬件寄存器；
- 具体机器人业务；
- 多设备协同策略；
- 项目线程生命周期的统一编排；
- 由多个从设备共享的总线调度。

当前模块层最重要的思想是：

```text
模块是能力边界
线程不是模块的必然属性
```

这为 IMU、Remote、Motor、PowerMeter 采用不同运行模型提供了依据。

### 7.3 `algorithm/`：让计算脱离设备和项目

前身的 `controller/timer` 以及后来积累的 PID、滤波、RLS、姿态解算等能力，逐渐推动出 `algorithm/`。

算法层希望满足：

```text
输入数据
    -> 更新内部状态
        -> 输出计算结果
```

它尽量不应该知道：

- 当前是哪一块板；
- 数据来自 UART 还是 CAN；
- 当前线程叫什么；
- 当前机器人是底盘还是云台；
- 数据是否通过 zbus 传递。

这使得同一个算法可以被：

- 正式业务线程调用；
- `project/thread/test/` 验证；
- Python 脚本离线复现；
- 另一个项目复用。

算法层的形成，实际上是从“某个设备的附属代码”中把纯计算部分抽出来。

例如：

```text
IMU 驱动
    负责读取和换算

姿态算法
    负责融合和更新状态

姿态线程
    负责周期调度和 topic 发布
```

这三者不能因为当前都服务 IMU 就永久绑在一起。

### 7.4 `topic/`：把数据关系写成契约

前身已经使用 zbus，但 topic 和模块类型有较强耦合。

当前 `topic/` 的形成，是因为项目规模增大后，必须回答：

```text
一个线程发布的数据到底是什么？
谁拥有这份数据？
消费者是否需要知道生产者的类？
这是最新状态，还是每一帧都不能丢的事件？
```

当前 topic 层主要定义：

- 数据结构；
- zbus channel；
- 消息命名；
- 发布与订阅边界；
- 数据的语义。

它不负责：

- 采集数据；
- 解析协议；
- 控制设备；
- 业务状态机；
- 线程创建。

例如：

```text
modules/imu/
    产生 IMU 数据

topic/imu_to/
    定义 IMU 数据如何交给其他线程

project/thread/gimbal/
    决定如何使用姿态数据
```

这样，姿态数据的消费者不需要包含某个具体 IMU 设备类。

### 7.5 `project/`：让框架能力有一个装配场

`project/` 是当前 tflm 中最关键、也最容易被误解的一层。

它不是简单的“应用目录”，也不是框架之外的临时文件夹。

当前阶段，`project/` 同时承担三件事：

1. 作为参考机器人项目；
2. 作为框架各层的集成测试环境；
3. 作为未来业务外置之前的项目装配层。

它包含：

```text
project/apps/
    系统启动和中断装配

project/boards/
    板级 overlay、conf 和 board.cmake

project/thread/
    当前项目的 RTOS 线程和业务组合
```

这种设计允许框架层和业务层在同一个仓库中快速迭代。

如果当前正在开发 IMU：

```text
modules/imu/
    实现通用设备能力

project/thread/imu/
    创建当前项目实例

project/boards/
    提供当前板的 SPI、GPIO 和中断配置
```

如果当前正在验证一个新设备：

```text
project/thread/test/
    临时组合已有能力
    验证完成后再沉淀到框架层
```

这就是为什么 `project/thread/test/` 不是随意的临时垃圾场，而是正式的实验入口。

### 7.6 `cmd/`：横切式的运行时辅助层

`cmd/` 的出现不是因为传统层次不够，而是因为有些能力天然横切多个层：

- shell 调参；
- buzzer 启动反馈和错误提示；
- flash 参数持久化；
- 构建脚本；
- 链接段定义。

这些东西既不是普通业务，也不能完全归到驱动层。

因此 `cmd/` 形成了一个“运行时辅助执行层”：

```text
业务或框架代码
    -> EXEC_BUZZER_XXX()
    -> EXEC_FLASH_XXX()
    -> REGISTER_SHELL_VAR()
```

调用方只使用稳定头文件和宏。

Kconfig 决定能力是否启用。

CMake 决定实现文件是否参与编译。

关闭能力时，宏提供空操作或失败返回。

这样，业务代码不需要到处写：

```cpp
#ifdef CONFIG_CMD_BUZZER
...
#endif
```

`cmd/shell` 还拥有自己的独立线程，因为它本质上是一个输入驱动的调试控制面，而不是某个机器人业务线程。

### 7.7 `scripts/`：把主机侧实验纳入架构

随着 IMU 加热、滤波、RLS、电机辨识和串口吞吐测试越来越多，单靠固件内部日志已经不够。

需要在主机侧完成：

- 原始数据采集；
- 日志解析；
- 数据回放；
- 曲线绘制；
- 参数扫描；
- 模型拟合；
- 实验结果对比；
- 固件与离线结果的交叉验证。

因此 `scripts/` 不只是“几个临时 Python 文件”。

它承担的是：

```text
固件产生数据
    ->
scripts/ 接收和解析
    ->
离线计算或拟合
    ->
生成结论或参数
    ->
回到固件验证
```

它不编译进 MCU，也不属于设备模块，但它属于整个架构的工程闭环。

---

## 8. IMU 是当前架构演进最集中的一条线

IMU 是当前维护和抽象最深的模块之一。

它经历的变化，几乎包含了 tflm 设计中最重要的几类问题：

```text
硬件访问
    -> 设备差异
        -> 校准
            -> 加热
                -> 姿态处理
                    -> 线程
                        -> topic
                            -> Python 实验
```

### 8.1 早期 IMU 思路容易把所有东西放在一起

早期实现通常会把下面这些内容写在同一个设备文件或线程文件中：

- SPI 初始化；
- 寄存器读写；
- 芯片复位；
- 量程配置；
- 原始数据转换；
- 偏置校准；
- 加热控制；
- 姿态解算；
- 发布消息；
- 线程循环。

这样做的好处是“马上能跑”。

但一旦加入第二种 IMU，问题会迅速出现：

- 哪些函数是 BMI088 专属的；
- 哪些函数是 ICM42688P 专属的；
- SPI 是共享的还是独占的；
- 校准数据如何复用；
- 加热器属于设备还是项目；
- 线程应该服务设备还是服务姿态算法。

### 8.2 当前的 IMU 前后端分离

当前 IMU 试图把职责拆成：

```text
设备后端
    -> 访问寄存器
    -> 读取原始数据
    -> 完成芯片相关换算

公共前端
    -> 统一 Sample
    -> 校准
    -> 偏置和比例管理
    -> 加热/Flash 配合

ImuManager
    -> 调度 Source
    -> 运行处理器
    -> 发布 topic
    -> 持有线程
```

公共接口由 `Source` 提供：

```cpp
class Source
{
public:
    virtual bool Init() = 0;
    virtual bool LateInit() = 0;
    virtual bool Read(Sample& sample) = 0;
    virtual bool Calibrate() { return true; }
};
```

`CalibSource` 再把原始采样、单位换算和校准过程抽出来。

这样，上层只需要面对统一的：

```text
gyro
accel
temperature
dt
```

而不需要知道当前具体设备是 BMI088 还是 ICM42688P。

### 8.3 为什么 SPI 不放在 IMU 基类

这是当前 IMU 设计中一个非常重要的取舍。

表面上看，可以在基类中放一个：

```cpp
Spi spi_;
```

然后所有 IMU 子类共用。

但这会引入几个问题。

#### 第一，不同设备的总线拓扑不同

ICM42688P 可能只需要一条 SPI：

```text
ICM42688P
    -> spi_
```

BMI088 则可能有独立的加速度和陀螺仪片选：

```text
BMI088
    -> accel_
    -> gyro_
```

如果把 SPI 放进基类，基类就必须预留：

- 一个 SPI；
- 两个 SPI；
- 多个片选；
- 或者某种动态数组。

这会让“设备差异”反向污染公共抽象。

#### 第二，SPI 属于具体设备访问策略

基类应该表达：

```text
一个 IMU 可以初始化、延迟初始化、读取样本和校准
```

而不应该表达：

```text
所有 IMU 都以同一种 SPI 结构访问寄存器
```

后者并不成立。

具体设备子类才知道：

- 使用哪一个 SPI；
- 片选如何控制；
- 是否需要额外延时；
- 是否有多个内部传感器；
- 寄存器读写是否有特殊协议；
- 是否需要不同的初始化阶段。

因此当前的结构是：

```text
Source / CalibSource
    只定义统一能力

Icm42688p
    私有 Spi spi_

Bmi088
    私有 Spi accel_
    私有 Spi gyro_
```

这是一种“公共接口小，具体资源私有”的设计。

#### 第三，避免基类变成硬件资源仓库

如果以后不断把所有可能的硬件资源都放进基类：

```text
SPI
GPIO
PWM
DMA
中断
加热器
Flash
```

基类最终会变成一个设备资源仓库。

它看起来复用程度很高，实际上会导致：

- 无关设备携带无关成员；
- 基类接口持续膨胀；
- 子类被迫遵守不存在的资源关系；
- 资源生命周期难以判断；
- 设备差异被宏和空指针掩盖。

当前的做法是：

```text
公共层只保留真正公共的行为
具体资源由具体设备拥有
```

这也是低耦合设计中很重要的一条原则。

### 8.4 为什么 IMU 需要线程

IMU 是典型的主动数据源。

它需要周期性地：

- 等待或读取传感器；
- 处理原始数据；
- 执行校准和换算；
- 更新加热状态；
- 调用姿态处理；
- 计算实际时间间隔；
- 发布最新状态。

因此 IMU 线程拥有明确的运行职责：

```text
采样
    -> 前端校准
        -> 处理
            -> 发布
```

如果没有独立线程，就需要某个项目线程直接调用：

```cpp
imu.Read();
imu.Process();
imu.Publish();
```

这样 IMU 的内部运行模型会泄漏到所有使用它的项目。

当前 `ImuManager` 自己持有线程，是因为它拥有持续运行的状态机，而不是因为“设备模块都必须有线程”。

### 8.5 IMU 与 Flash、Shell、Scripts 的关系

IMU 的校准参数需要持久化，所以它会调用：

```cpp
EXEC_FLASH_READ(...)
EXEC_FLASH_WRITE(...)
```

运行时调参可以通过：

```cpp
REGISTER_SHELL_VAR(...)
```

实验数据则可以由：

```text
固件日志
    -> scripts/imu_*.py
    -> 拟合、绘图或辨识
```

这说明一个真实设备能力往往不是单独存在的。

但它们仍然可以保持边界：

```text
IMU
    只依赖 Flash 的稳定执行接口

Flash
    不知道 IMU 的校准语义

Shell
    不知道 IMU 如何计算姿态

Scripts
    不进入固件，不持有设备对象
```

---

## 9. Remote 是另一条重要的演进线

Remote 的变化更加能体现“从硬件回调到协议管理器”的过程。

### 9.1 前身的遥控器处理方式

前身的 `FsI6` 已经能够：

- 配置 UART；
- 接收数据；
- 识别协议；
- 解码通道；
- 发布 zbus。

但它的处理链更接近：

```text
UART 异步回调
    -> 直接解析
        -> 直接发布
```

这种实现的主要问题是：

- 回调路径较重；
- 协议处理和底层接收节奏绑定；
- 多协议支持不自然；
- 超时和失联管理容易分散；
- 后续做双 UART 冗余时没有独立状态容器。

### 9.2 当前 Remote 的协议注册

当前 Remote 将协议抽象成：

```cpp
class RemoteProtocol
{
public:
    virtual bool Decode(...);
    virtual bool Validate(...);
};
```

协议通过：

```cpp
REGISTER_REMOTE(...)
```

放入 `.remote` 段。

启动时，Remote 不需要手工维护一个中央协议表，而是可以遍历链接器收集到的：

```text
RemoteEntry
```

每个协议条目包含：

- 协议名字；
- 帧长度；
- 协议对象；
- 优先级；
- 锁定所需命中次数。

这样新增协议的主要工作变成：

```text
实现一个 RemoteProtocol 子类
声明 REGISTER_REMOTE()
```

而不是修改 Remote 的中央分发函数。

### 9.3 当前 Remote 的状态机

Remote 自己维护：

```text
Detecting
    -> 尝试识别协议

Locked
    -> 按锁定协议解析
```

同时管理：

- 候选协议；
- 连续命中次数；
- 失败次数；
- 当前帧缓存；
- 最小和最大帧长；
- 最后有效时间；
- 超时状态；
- zbus 发布数据。

这意味着底层 UART 不需要知道：

```text
当前应该尝试 DR16、SBUS、VT12 还是 VT13
```

模块层把协议管理封装起来，项目层只消费：

```text
topic::remote_to::Message
```

### 9.4 为什么 Remote 需要线程

Remote 和 IMU 一样，都是主动数据源。

它需要等待：

```text
UART 数据到达
```

然后执行：

```text
读取缓冲区
    -> 组帧
        -> 协议识别或锁定解析
            -> 超时判断
                -> 发布最新状态
```

如果把协议解析直接塞进 UART 回调，回调需要承担：

- 字节缓冲；
- 帧边界处理；
- 多协议尝试；
- 校验；
- 状态切换；
- 发布。

这会让中断或异步回调过重。

当前的思路是：

```text
UART 回调只负责接收和唤醒
Remote 线程负责解释和发布
```

这样可以把硬件到达时机和协议计算时机分开。

### 9.5 双 UART 冗余为什么会成为独立架构

当前代码仍然是单 UART 实现。

双 UART 设计目前规划在：

```text
doc/remote_dual_arch.md
```

它还没有作为当前主线实现落地。

但双 UART 的规划并不是简单地把：

```cpp
UartDma* uart_;
```

改成：

```cpp
UartDma* uart_[2];
```

真正需要解决的是：

```text
每条 UART 是否有独立缓冲区
每条 UART 是否有独立协议检测状态
当前输出如何原子切换
旧通道残留数据如何清理
如何防止两路状态来回抖动
超时和恢复如何定义
```

因此规划中区分了：

热路径：

```text
Read
    -> Decode
        -> Publish
```

冷路径：

```text
失联判断
    -> 候选通道评估
        -> 清理旧缓冲
            -> 原子切换
                -> 冷却和防抖
```

这体现了当前架构对实时路径的认识：

> 热路径必须笔直，复杂的切换策略应该放到低频状态管理路径中。

双 UART 设计的价值，不只是提高可靠性。

它还验证了当前 Remote 是否真的做到：

- 协议和传输解耦；
- 每个通道状态独立；
- 状态切换有明确契约；
- 业务层不感知底层冗余细节。

### 9.6 Remote 两次优化的架构意义

Remote 的每次优化，本质上不是为了“代码更短”，而是为了重新划分责任：

```text
UART
    只处理字节流

协议
    只处理帧格式和解码

Remote 管理器
    处理检测、锁定、超时和发布

项目线程
    处理当前机器人如何使用遥控状态
```

这就是为什么 Remote 会成为当前架构中最有代表性的模块之一。

---

## 10. Motor 和 PowerMeter 为什么没有线程

当前 tflm 没有把“有独立类”直接等同于“有独立线程”。

### 10.1 Motor 的运行模型

电机反馈通常来自 CAN 接收路径：

```text
CAN 接收
    -> 按 ID 找到电机对象
        -> 更新反馈状态
            -> 控制线程读取快照
                -> 计算目标
                    -> CAN 发送
```

电机对象需要保存：

- CAN ID；
- 反馈角度；
- 转速；
- 电流；
- 温度；
- 在线状态；
- PID 状态；
- 目标值。

但它不一定需要自己创建线程。

原因是：

1. 反馈到达由 CAN 驱动和接收路径决定；
2. 控制周期由底盘、云台或其他项目线程决定；
3. 多个电机往往属于同一个控制闭环；
4. 每个电机各自创建线程会造成调度碎片；
5. 电机本身不知道自己应该执行什么业务策略。

因此当前电机更适合作为：

```text
被动状态对象 + 控制接口
```

而不是：

```text
主动运行线程
```

### 10.2 PowerMeter 的运行模型

功率计也类似。

它的核心工作是：

```text
接收功率数据
    -> 更新快照
        -> 控制线程读取
            -> 进行功率限制或分配
```

PowerMeter 不应该自己决定：

- 底盘怎么限功率；
- 云台是否允许开火；
- 当前业务模式是什么；
- 哪个电机需要削减输出。

这些是项目控制逻辑。

所以 PowerMeter 也不需要独立线程。

### 10.3 没有线程不等于没有并发问题

Motor 和 PowerMeter 没有线程，不代表它们可以随意读写共享状态。

当前仍然需要考虑：

- CAN 回调与控制线程并发；
- 快照读取的一致性；
- 反馈状态是否可能读到半更新数据；
- 在线状态和时间戳是否同步；
- 多字段状态是否需要 seqlock 或临界区。

因此“无独立线程”的正确含义是：

```text
不拥有独立的调度循环
```

而不是：

```text
不需要并发保护
```

### 10.4 和 IMU、Remote 对比

| 模块 | 数据来源 | 是否主动持续处理 | 是否拥有独立线程 |
| --- | --- | --- | --- |
| IMU | SPI/传感器采样 | 是 | 是 |
| Remote | UART 字节流 | 是 | 是 |
| Motor | CAN 反馈回调 | 通常否 | 否 |
| PowerMeter | CAN/通信反馈 | 通常否 | 否 |

更准确的判断标准是：

```text
是否拥有独立的等待条件、状态机、周期和输出责任？
```

而不是：

```text
这个模块是不是一个 class？
```

---

## 11. 编译期注册机制是如何形成的

### 11.1 早期问题：中央代码知道太多

在传统工程中，增加一个初始化项往往需要修改：

```text
头文件声明
源文件定义
中央初始化函数
任务创建代码
任务控制块
优先级配置
```

这意味着模块的实现文件和启动装配表绑定在一起。

当模块越来越多时，中央文件会变成：

```text
所有模块的名字
所有模块的创建顺序
所有模块的错误处理
所有模块的线程启动
所有模块的资源依赖
```

它实际变成了另一个耦合中心。

### 11.2 当前方案：注册、收集、执行

当前的初始化机制可以分成三步。

#### 第一步：本地声明

组件在自己的源文件中写：

```cpp
REGISTER_INIT(thread_init, EarlyInit, High, "imu_init");
REGISTER_INIT(thread_start, EarlyThread, High, "imu_start");
```

#### 第二步：链接期收集

宏将 `InitEntry` 放入：

```text
.user_init
```

链接脚本提供：

```text
__user_init_start
__user_init_end
```

#### 第三步：运行时遍历

`System_Startup()` 依次执行：

```text
PreInit
PreThread
EarlyInit
EarlyThread
MidInit
MidThread
LateInit
LateThread
AppInit
AppThread
```

每个阶段内，再根据 `InitLevel` 决定失败策略：

```text
High
    记录错误并停机

Mid
    记录错误并继续

Low
    警告并继续
```

### 11.3 这个机制解决的不是“少写几行”

注册宏的价值不只是让启动代码变短。

它真正解决的是：

```text
启动项的声明位置回到功能实现所在的文件
```

新增模块时，维护者可以在模块附近看到：

- 它什么时候初始化；
- 它什么时候启动线程；
- 它的启动级别；
- 它的名字是什么；
- 它是否需要其他组件先就绪。

同时，系统仍然可以通过一个统一启动器保证：

- 启动阶段；
- 错误处理；
- 日志；
- 运行顺序；
- 初始化完成反馈。

这是一种局部可维护性和全局可控性的折中。

### 11.4 初始化宏的限制

当前注册机制仍然有成本：

- 链接脚本必须正确保留 section；
- 注册项的链接顺序需要明确；
- 不能只看一个中央文件理解全局启动；
- 依赖关系没有完全由类型系统表达；
- 不同编译器的 section 语法可能不同；
- 注册函数名和 section 名需要长期稳定。

所以这不是“完全自动化”。

它只是把人工维护从：

```text
中央启动表
```

移动到了：

```text
本地注册 + 阶段规则 + 链接脚本
```

### 11.5 初始化之外的注册

当前同一种思路已经扩展到其他对象：

```text
REGISTER_INIT()
    -> .user_init

REGISTER_REMOTE()
    -> .remote

REGISTER_IMU()
    -> .imu

CAN_RX_HANDLER()
    -> .can_rx1 / .can_rx2 / .can_rx3

REGISTER_SHELL_VAR()
    -> .shell_var
```

这些机制共同表达了一个方向：

> 让对象在自己的实现处声明，系统在链接后统一发现和执行。

这也是 tflm 与早期“中央数组手工登记”的重要区别。

---

## 12. Kconfig、CMake 和链接脚本为什么必须一起出现

如果只使用 Kconfig，不能自动完成所有事情。

如果只使用 CMake，也不能表达所有运行时契约。

如果只有链接段注册，也不能决定组件是否应该进入固件。

当前三者的分工是：

```text
Kconfig
    选择能力、表达依赖、关闭不需要的功能

CMake
    根据配置加入源文件、头文件和依赖目标

Linker script
    收集运行时注册表并提供边界符号
```

完整链路可以表示为：

```text
项目 prj.conf
    ->
Kconfig
    ->
CONFIG_XXX
    ->
CMake
    ->
源文件是否参与编译
    ->
链接器
    ->
注册项是否存在于最终固件
    ->
运行时遍历
```

以 shell 为例：

```text
CONFIG_CMD_SHELL_VAR=y
    -> select COM_UART_DMA
        -> 编译 shell.cpp 和 uart.cpp
            -> REGISTER_SHELL_VAR() 生成 .shell_var
                -> DbgConsole 遍历变量
```

以 IMU 为例：

```text
CONFIG_TRD_IMU=y
    -> 选择 SPI、IMU 模块和相关算法/topic
        -> 编译 trd_imu.cpp
            -> REGISTER_INIT()
                -> 启动 ImuManager
```

以功能关闭为例：

```text
CONFIG_CMD_BUZZER=n
    -> buzzer.cpp 不参与编译
        -> EXEC_BUZZER_SHORT() 变成空操作
```

这使得调用方能够保持稳定。

---

## 13. `cmd/` 为什么是当前架构中一个特殊层

`cmd/` 的形成说明传统的“底层、模块、应用”三层并不能解释所有代码。

### 13.1 shell 不是项目业务线程

`project/thread/` 的线程一般参与：

```text
传感器数据链
电机控制链
底盘/云台业务
通信业务
测试业务
```

而 `cmd/shell` 做的是：

```text
接收一行字符
    -> 解析命令
        -> 查找注册变量
            -> 读写调试参数
```

它不应该知道：

- 底盘的模式状态；
- 云台的控制策略；
- IMU 的姿态算法；
- Remote 的协议识别。

它只需要知道：

```text
有哪些变量允许被查看和修改
```

所以 shell 拥有自己的线程，不是破坏 `project/thread/` 的边界，而是为了避免把调试控制面误认为机器人业务。

### 13.2 buzzer 和 flash 的执行宏

`buzzer` 和 `flash` 的调用具有两个特点：

1. 可能被很多层调用；
2. 当前项目可能没有对应硬件。

因此使用：

```cpp
EXEC_BUZZER_SHORT();
EXEC_BUZZER_ERR(nullptr);
EXEC_FLASH_READ(...);
EXEC_FLASH_WRITE(...);
```

而不是让所有调用方手工判断配置。

宏在关闭时提供：

```text
buzzer
    -> 空操作或保留错误停机语义

flash
    -> 丢弃参数并返回 false
```

这样可以让调用方保持统一。

### 13.3 为什么头文件允许常包含

如果每一个调用点都必须知道：

```text
CONFIG_CMD_BUZZER 是否开启
CONFIG_CMD_FLASH 是否开启
```

那么功能开关就会扩散到所有业务文件。

最终会出现：

```cpp
#ifdef CONFIG_CMD_BUZZER
#include "buzzer.hpp"
#endif

#ifdef CONFIG_CMD_FLASH
#include "w25q128.hpp"
#endif
```

这种写法会让调用方直接暴露构建配置。

当前的设计是：

```text
头文件提供稳定声明
宏负责把不可用能力降级
Kconfig/CMake 负责决定真实实现是否存在
```

于是调用方可以长期包含：

```cpp
#include "buzzer.hpp"
#include "w25q128.hpp"
```

而不需要在每个文件中重复条件编译。

这是一种“接口稳定、实现可选”的策略。

它的前提是：

- 禁用宏必须有正确的参数处理；
- 禁用宏必须有正确的返回值；
- 不应在禁用路径访问不存在的对象；
- 头文件不能暴露强制链接的实现符号；
- 重要失败语义不能被静默吞掉。

---

## 14. `scripts/` 为什么不是架构外的临时代码

当前 tflm 的很多能力并不是单靠 MCU 内部代码形成的。

尤其是：

- IMU 加热参数；
- 温度模型；
- 姿态滤波；
- RLS 电机辨识；
- UART 吞吐和延迟；
- PID 参数；
- 数据回放；
- 控制算法对比。

这些工作通常需要：

```text
固件记录原始数据
    ->
Python 解析
        ->
离线分析
    ->
生成参数或模型
    ->
固件重新实验
```

如果没有 `scripts/`，每次实验都只能手工处理日志。

这会产生三个问题：

1. 实验不可重复；
2. 参数来源难以追踪；
3. 代码优化和数据结论之间缺少证据链。

因此脚本层的职责是：

```text
把“测出来、算出来、对比出来”的工作固定下来
```

它不应该侵入固件边界。

正确关系是：

```text
固件
    -> 输出稳定数据格式

scripts/
    -> 读取、校验、分析、回放

doc/
    -> 记录实验条件、结论和限制
```

这也是为什么架构中的 Python 不是 MCU 运行时的一部分，但仍然属于项目开发闭环。

---

## 15. 当前 project 为什么还没有完全移出仓库

从长期目标看，tflm 希望像 Zephyr 一样被业务项目使用。

理想关系是：

```text
业务仓库
    -> 使用 tflm
        -> 使用 Zephyr
```

业务仓库拥有：

```text
app/
boards/
thread/
prj.conf
west.yml
```

tflm 提供：

```text
drivers/
modules/
algorithm/
topic/
cmd/
```

但当前仓库还没有完全达到这个状态。

当前真实状态是：

```text
tflm 框架层
    + 当前参考 project/
    + 当前板级配置
    + 当前实验线程
    + 当前业务装配
```

根 `CMakeLists.txt` 仍然把：

```text
ACTIVE_PRJ
PROJ_DIR
CONFIG_PRJ_TEST
project/boards/
project/thread/
```

作为当前应用构建的一部分。

这并不意味着架构方向错误。

它说明当前 `project/` 还有三个现实任务：

1. 验证框架能力；
2. 承载当前赛季业务；
3. 为未来外置业务层提供参考实现。

如果现在贸然拆出业务仓库，可能会带来：

- 当前比赛开发被打断；
- 框架接口尚未稳定；
- 实验代码无处落地；
- 参考项目和公共能力失去对照；
- 文档只能描述目标，无法对应当前真实代码。

因此更合理的顺序是：

```text
先稳定边界
    ->
先稳定公开接口
    ->
先明确项目层可以修改什么
    ->
再把 project/ 逐步抽成参考业务项目
    ->
最后让外部业务仓库通过 Zephyr module/west 使用 tflm
```

具体规划已经写在：

```text
doc/业务层嵌入架构规划.md
```

需要特别区分两件事：

```text
当前 project/ 是框架仓库里的项目装配层

未来业务仓库使用 tflm，
不代表 project/ 现在已经完成了外置
```

---

## 16. 这套架构为什么允许“项目嵌入架构”

“业务层嵌入架构”不是把业务代码硬塞进框架。

更准确地说，是让框架提供一组稳定的能力接口，让业务项目通过配置和装配来使用它们。

目标关系可以写成：

```text
Zephyr
    提供 RTOS、设备树、内核、驱动基础和构建体系

tflm
    提供机器人设备模块、算法、topic、调试和注册机制

业务项目
    提供板卡选择、设备实例、线程组合和机器人策略
```

业务项目不应该修改 tflm 内部的公共实现来完成日常开发。

它应该通过：

```text
Kconfig
CMake
Devicetree
REGISTER_INIT()
REGISTER_REMOTE()
REGISTER_SHELL_VAR()
topic
```

接入框架。

这和 Zephyr 的使用方式有相似之处：

```text
应用不修改 Zephyr 内核来添加一个线程
应用通过配置、设备树和 API 使用 Zephyr
```

对应到 tflm：

```text
业务项目不应该修改 modules/ 的公共类来添加一个项目策略
业务项目通过 project/thread/、topic 和公开接口使用 tflm
```

当然，tflm 目前还没有完全达到 Zephyr 的生态和稳定度。

这里的“像使用 Zephyr”主要指使用关系和边界方向，而不是声称当前已经具备 Zephyr 级别的模块分发能力。

---

## 17. 当前架构付出的代价

低耦合不是没有成本。

### 17.1 文件和路径变多

一个功能可能需要沿着下面的路径阅读：

```text
board overlay
    -> drivers
        -> modules
            -> topic
                -> project/thread
                    -> cmd 或 scripts
```

这比把所有代码写进一个文件更难快速浏览。

### 17.2 编译系统变复杂

需要同时理解：

- Kconfig；
- CMake；
- Zephyr devicetree；
- linker script；
- section 注册；
- 外部 USB 模块；
- 项目板级路径。

这会提高新成员的入门门槛。

### 17.3 注册机制会隐藏部分调用关系

使用 `REGISTER_INIT()` 后，一个线程可能不会出现在中央启动文件中。

维护者需要知道：

```text
在哪里注册
注册到哪个 section
哪个阶段执行
失败如何处理
```

这要求文档和命名保持稳定。

### 17.4 文档可能落后于代码

架构快速演进时，旧文档可能仍然写着：

```text
旧启动链
旧目录名
旧项目层路径
旧模块职责
```

因此当前必须明确：

```text
真实代码优先
文档跟随实现
规划文档明确标注未来状态
```

### 17.5 框架抽象不能代替真实业务

公共框架可以降低复用成本，但它不能自动解决：

- 控制参数；
- 机器人策略；
- 设备拓扑；
- 任务周期；
- 保护条件；
- 比赛规则；
- 机械结构约束。

这些仍然属于项目业务。

所以 tflm 的目标不是消灭业务代码，而是让业务代码在更清晰的边界内存在。

### 17.6 当前外部化还没有完成

必须诚实地说明：

```text
业务层外置是规划
不是当前仓库已经完成的事实
```

当前 tflm 的框架化能力已经明显形成，但它仍然包含：

- 当前参考项目；
- 当前板级路径；
- 当前实验线程；
- 外部 SDK glue 的本地路径假设；
- 外部 USB 用户模块依赖；
- 仍在收敛的公开接口。

这不是缺点被隐藏，而是当前演进阶段的真实状态。

---

## 18. 当前 tflm 的时间线

下面的时间线不是为了制造版本号，而是为了说明设计为什么会逐步变化。

### 阶段一：先把嵌入式功能做出来

最早关注的是：

```text
外设能不能跑
设备能不能读
线程能不能启动
控制链能不能闭环
```

这一阶段的代码通常更贴近硬件和业务，优先级是快速验证。

### 阶段二：从成熟项目中学习框架规则

开始观察：

- `bsp / module / app` 如何分层；
- 消息中心如何隔离应用；
- H7 模板如何整理外设；
- 真实机器人如何组织设备对象和总控。

这一阶段形成的是：

```text
分层意识
消息意识
设备对象意识
文档和传承意识
```

### 阶段三：形成自己的 `Zephyr_Components` 原型

开始尝试：

- `bsp/`、`modules/`、`controller/`、`thread/` 分目录；
- `zpull` 管理远程组件；
- `modules.yaml` 表达组件来源；
- CMake 自动发现；
- module.yaml 表达拉取依赖；
- zbus 表达数据传输；
- Thread 模板管理多个对象。

这一阶段解决了：

```text
组件如何组织和复用
```

但启动、线程、topic 依赖和运行时边界还不够成熟。

### 阶段四：开始把问题转向“框架装配”

逐渐意识到：

```text
源码能拉下来
不代表系统能正确装配
```

于是开始重视：

- Kconfig；
- CMake；
- 模块依赖；
- 源文件裁剪；
- 启动阶段；
- 初始化失败策略；
- 链接段注册；
- topic 契约；
- project 装配。

### 阶段五：IMU 和 Remote 成为主要抽象对象

IMU 和 Remote 的维护量不断增大，推动了：

```text
IMU 前后端分离
Source / CalibSource
具体设备私有 SPI
ImuManager
RemoteProtocol
RemoteEntry
Remote 状态机
UartDma + semaphore
topic 发布
```

这两个模块使架构从“有目录”进入“有运行模型”的阶段。

### 阶段六：注册宏和链接段机制落地

随着模块和线程增多，开始减少中央装配表：

```text
REGISTER_INIT()
REGISTER_IMU()
REGISTER_REMOTE()
CAN_RX_HANDLER()
REGISTER_SHELL_VAR()
```

这一步让“功能在本地声明，系统统一发现”成为当前架构的重要风格。

### 阶段七：cmd 和 scripts 被纳入正式边界

调试变量、蜂鸣器、Flash、构建脚本和 Python 实验越来越重要。

它们不再被视为零散工具，而是被放进：

```text
cmd/
scripts/
```

并形成各自的职责和调用方式。

### 阶段八：开始规划业务层外置

当前架构已经不满足于：

```text
所有东西都放在一个仓库里
```

而是开始规划：

```text
框架仓库
    -> 对外提供能力

业务仓库
    -> 通过配置、CMake、west 和公开接口使用能力
```

这一步是从“项目型框架”走向“可嵌入框架”的开始。

---

## 19. 当前架构的因果图

把整个过程压缩成一张图：

```text
真实项目需求增加
    ->
模块之间直接包含
    ->
新增功能修改面过大
    ->
开始分层和封装组件
    ->
外设、设备协议、业务逻辑逐渐分离
    ->
线程和模块的职责需要重新判断
    ->
主动数据源拥有线程
被动状态对象不拥有线程
    ->
线程之间需要稳定数据契约
    ->
引入 topic
    ->
模块和设备依赖需要按项目裁剪
    ->
引入 Kconfig/CMake
    ->
中央初始化表变重
    ->
引入 REGISTER_INIT() 和链接段
    ->
协议、设备和调试变量需要自动发现
    ->
扩展 REGISTER_REMOTE()
REGISTER_IMU()
CAN_RX_HANDLER()
REGISTER_SHELL_VAR()
    ->
调试和持久化能力横切多个层
    ->
形成 cmd/
    ->
固件实验需要离线验证
    ->
形成 scripts/
    ->
项目差异逐渐集中到 project/
    ->
开始规划业务层外置
```

这张图说明：

> 当前架构不是为了追求抽象而抽象，而是在每一次维护压力出现后，把原本扩散的变化重新放回合适的边界。

---

## 20. 当前各层分别回答什么问题

| 层 | 它回答的问题 | 它不应该回答的问题 |
| --- | --- | --- |
| `drivers/` | 如何访问硬件和管理底层传输 | 这个机器人如何决策 |
| `modules/` | 一个设备或能力如何成为可复用对象 | 多模块业务策略如何组合 |
| `algorithm/` | 如何对输入数据进行纯计算 | 数据来自哪块板、哪个线程 |
| `topic/` | 线程之间交换什么数据、遵守什么语义 | 如何采集、如何控制 |
| `project/apps/` | 系统如何启动、如何注册和分阶段执行 | 具体设备算法 |
| `project/boards/` | 当前项目使用哪块板、哪些硬件实例 | 通用设备协议 |
| `project/thread/` | 当前项目如何组合设备和算法 | 公共驱动实现 |
| `cmd/` | 如何提供调试、提示、持久化和构建辅助 | 机器人核心业务 |
| `scripts/` | 如何在主机侧实验、辨识、回放和分析 | MCU 实时线程 |
| `doc/` | 如何记录设计、问题、实验和规划 | 运行时功能 |

这些边界不是绝对不可变化的法律。

但它们提供了一个重要判断依据：

```text
新增代码应该先问：
它解决的是哪一类问题？
它的变化来源是什么？
谁应该拥有它？
```

---

## 21. 当前实现和未来规划必须分开

### 21.1 当前已经存在的能力

当前仓库已经具备：

- Zephyr 运行时基础；
- Kconfig/CMake 组件裁剪；
- `drivers/` 硬件访问层；
- `modules/` 设备能力层；
- `algorithm/` 算法层；
- `topic/` 数据契约层；
- `project/` 项目装配层；
- `cmd/` 运行时辅助层；
- `scripts/` 主机侧实验入口；
- `REGISTER_INIT()` 和 `.user_init`；
- IMU 设备前后端分离；
- Remote 协议注册和单 UART 运行链；
- Motor/PowerMeter 被动状态模型；
- shell 独立线程；
- buzzer/flash 执行宏；
- CAN 接收注册基础设施；
- USB Stream 适配和外部 USB 用户模块接入。

### 21.2 当前仍然属于规划或未完全落地的能力

以下内容需要明确标记为未来方向或部分实现：

- Remote 双 UART 冗余；
- 业务仓库完全外置；
- `tflm` 作为独立 Zephyr module 的完整发布方式；
- 通过 west manifest 固定业务项目使用的 tflm 版本；
- 更稳定的公共注册接口；
- 完整的多项目兼容策略；
- 更系统的版本、发布和兼容性管理。

尤其是双 UART：

```text
设计文档已经存在
当前主线实现仍然是单 UART
```

尤其是业务层嵌入：

```text
目标关系已经规划
当前 project/ 仍然位于本仓库并参与构建
```

把这两个边界写清楚，是为了避免架构文档把愿景误写成代码现状。

---

## 22. 这套架构目前最像什么

如果一定要给当前 tflm 一个定位，它还不是：

```text
完整通用商业 SDK
```

也不是：

```text
只针对一台机器人写死的整机工程
```

它更像：

> 一套以 Zephyr 为底座、由真实机器人和实验需求推动、正在从项目型工程演化为可嵌入框架的个人嵌入式平台。

它的“平台性”主要体现在：

- 层之间已经开始有稳定职责；
- 设备、算法、数据、项目装配有不同归属；
- 初始化、协议、变量和中断开始使用编译期注册；
- 框架层不再应该反向依赖具体项目；
- Python 实验被纳入工程闭环；
- 当前项目正在成为框架能力的参考实现，而不是框架唯一存在的形式。

它的“不完整”也同样真实：

- 公共接口仍在收敛；
- 文档和代码需要持续同步；
- 外部业务层还没有完全拆出；
- 部分依赖仍然使用本地路径；
- 某些模块和历史代码还需要继续统一。

这两点可以同时成立。

一个架构可以已经有明确的方向和水平，同时仍然处在演进期。

---

## 23. 对后来维护者的阅读建议

如果第一次阅读当前 tflm，不建议一开始就从所有目录逐个打开。

推荐按照架构形成的因果顺序阅读。

### 第一阶段：先看项目入口

```text
src/main.c
project/apps/Init_entry.hpp
project/apps/Init_entry.cpp
```

先理解：

- 系统从哪里启动；
- 注册项如何进入 `.user_init`；
- 阶段和等级是什么意思；
- 初始化失败如何处理。

### 第二阶段：看一条底层数据链

推荐：

```text
drivers/communication/stream/uart/
modules/remotes/
topic/remote_to/
project/thread/remote/
```

先沿 Remote 看清楚：

```text
硬件字节
    -> 驱动缓冲
        -> 协议识别
            -> 状态发布
                -> 项目线程
```

### 第三阶段：看 IMU 前后端

推荐：

```text
drivers/communication/spi/
modules/imu/devices/imu_device_layer.hpp
modules/imu/devices/icm42688p/
modules/imu/drivers/imu.hpp
project/thread/imu/
topic/imu_to/
```

重点理解：

- 为什么 SPI 在具体子类；
- 为什么公共层只保留统一采样接口；
- 为什么 IMU 拥有线程；
- 校准、Flash 和姿态处理分别在哪里。

### 第四阶段：看被动设备

推荐：

```text
modules/motors/
modules/powermeter/
project/thread/chassis/
project/thread/gimbal/
```

重点理解：

- CAN 回调如何更新状态；
- 控制线程如何读取快照；
- 为什么电机模块不拥有线程；
- 多设备控制为什么属于项目线程。

### 第五阶段：看 cmd

推荐：

```text
cmd/ARCHITECTURE.md
cmd/Kconfig
cmd/CMakeLists.txt
cmd/shell/shell.hpp
cmd/shell/shell.cpp
cmd/buzzer/
cmd/flash/
```

重点理解：

- shell 为什么独立线程；
- `REGISTER_SHELL_VAR()` 如何收集变量；
- buzzer/flash 为什么使用执行宏；
- 关闭能力后调用方如何保持可编译。

### 第六阶段：再看历史前身

推荐：

```text
temp/Zephyr_Components-main/CMakeLists.txt
temp/Zephyr_Components-main/modules.yaml
temp/Zephyr_Components-main/zpull/README.md
temp/Zephyr_Components-main/apps/Init.cpp
temp/Zephyr_Components-main/thread/thread.hpp
temp/Zephyr_Components-main/modules/remote_fs/fs_i6/
```

这时再看前身，会更容易理解当前设计不是凭空增加复杂度，而是在回应前身暴露的问题。

---

## 24. 相关文档和代码入口

### 24.1 当前架构说明

```text
README.md
drivers/README.md
drivers/ARCHITECTURE.md
modules/README.md
modules/ARCHITECTURE.md
algorithm/README.md
algorithm/ARCHITECTURE.md
topic/ARCHITECTURE.md
project/ARCHITECTURE.md
cmd/README.md
cmd/ARCHITECTURE.md
```

### 24.2 当前关键代码

```text
src/main.c
project/apps/Init_entry.hpp
project/apps/Init_entry.cpp
project/apps/Irq_handlers.h
project/apps/Irq_handlers.cpp
cmd/linker/tflm_init.ld
```

### 24.3 IMU 和 Remote

```text
modules/imu/devices/imu_device_layer.hpp
modules/imu/devices/icm42688p/icm42688p.hpp
modules/imu/devices/icm42688p/icm42688p.cpp
modules/imu/drivers/imu.hpp
project/thread/imu/trd_imu.cpp
modules/remotes/remote.hpp
project/thread/remote/trd_remote.cpp
doc/remote_dual_arch.md
```

### 24.4 业务层外置规划

```text
doc/业务层嵌入架构规划.md
```

### 24.5 自己的前身

```text
temp/Zephyr_Components-main/CMakeLists.txt
temp/Zephyr_Components-main/modules.yaml
temp/Zephyr_Components-main/zpull/README.md
temp/Zephyr_Components-main/apps/Init.cpp
temp/Zephyr_Components-main/thread/thread.hpp
temp/Zephyr_Components-main/bsp/
temp/Zephyr_Components-main/modules/
temp/Zephyr_Components-main/topic/
```

### 24.6 其他参考项目

```text
temp/basic_framework-master/
temp/COD_H7_Template_CLion-main/
temp/Dust_SentinelRobot_L_Game/
doc/四个嵌入式框架对比总结.md
```

---

## 25. 最后的总结

当前 tflm 的来源，可以用下面几句话概括。

它不是从一张完美架构图开始的。

它是从真实的嵌入式功能开始，逐步遇到：

```text
外设耦合
设备差异
线程膨胀
全局状态
启动维护
协议扩展
实验验证
项目迁移
```

然后不断把这些问题重新放回合适的边界。

它从成熟框架中学到了：

```text
分层、消息、文档和传承
```

从单板模板中学到了：

```text
外设底座、CMake、DMA 和工程化工具链
```

从真实机器人中学到了：

```text
设备拓扑、控制闭环、失联保护和整机联调
```

从自己的 `Zephyr_Components-main` 中学到了：

```text
组件拉取、骨架复用、依赖表达和自动装配
```

当前 tflm 则继续把这些经验推进为：

```text
drivers
    管硬件

modules
    管设备能力

algorithm
    管纯计算

topic
    管数据契约

project
    管当前项目装配

cmd
    管横切式运行时辅助能力

scripts
    管主机侧实验和验证闭环
```

再通过：

```text
Kconfig
CMake
Devicetree
Linker section
REGISTER_INIT()
```

把编译期选择、运行时注册和项目组合连接起来。

所以，当前 tflm 最核心的架构判断不是：

> 我能不能把所有功能都封装成一个类？

也不是：

> 我能不能把目录拆得足够多？

而是：

> 每一种变化，是否都有一个清晰的归属；每一个新增功能，是否可以沿着这个归属被加入，而不必重新撕开整个工程？

这就是当前 tflm 从前身走到现在的真正原因。

它还没有终点。

双 UART 冗余、业务层外置、公共接口稳定化、版本发布和跨项目复用，仍然是后续工作。

但从工程演进的角度看，当前架构已经完成了一个很重要的转变：

```text
从“我在写一个能运行的机器人项目”
    变成
“我在提炼一套让下一个机器人更容易被写出来的系统”
```

这也是 tflm 这套架构最值得继续维护和开源的来源。
