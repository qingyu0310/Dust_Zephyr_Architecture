# tflm 架构分析

## 目录

- [1. 文档目的](#1-文档目的)
- [2. 三个工程阶段](#2-三个工程阶段)
- [3. 当前 tflm 的定位](#3-当前-tflm-的定位)
- [4. 当前分层](#4-当前分层)
- [5. 真实运行链](#5-真实运行链)
- [6. 当前已经完成的能力](#6-当前已经完成的能力)
- [7. 仍处于收敛期的部分](#7-仍处于收敛期的部分)
- [8. 架构成熟度判断](#8-架构成熟度判断)
- [9. 下一阶段重点](#9-下一阶段重点)

## 1. 文档目的

这份文档不是重新定义目录，而是回答三个问题：

1. `basic_framework-master`、`Dust_SentinelRobot_L_Game` 和当前 `tflm` 分别处于什么阶段？
2. 当前 `tflm` 的架构理念，哪些已经落实到代码？
3. 现在最应该继续完善什么，才能从“个人框架”走向“可复用平台”？

这里要区分两种顺序：

- **运行顺序**：系统启动时必须遵守依赖关系。
- **开发顺序**：模块、算法、线程和板级配置可以按实际需求交叉开发。

因此，当前架构不是要求所有代码按一条固定流水线写完，而是通过边界和依赖关系保持系统可组合。

## 2. 三个工程阶段

### 2.1 `basic_framework-master`：规则化基础平台

它最接近队伍级通用底座。

主要特点：

- `bsp / module / app` 层次清楚；
- 文档和开发规则比较完整；
- 适合多机器人、多成员、多赛季复用；
- 通过消息中心和配置宏维持模块间边界。

它的核心价值是“规则、传承和复用”。

局限是边界主要依靠文档、命名和开发纪律维护，仍然较多依赖 STM32 HAL、CubeMX 和 FreeRTOS 的工程习惯。

### 2.2 `Dust_SentinelRobot_L_Game`：完整整机工程

它最接近真实机器人项目。

主要特点：

- `Robot` 统一组织底盘、云台、通信、IMU、裁判系统等对象；
- 控制、通信、任务和保护逻辑已经形成整机闭环；
- 代码直接面对当前设备拓扑和比赛需求；
- 联调效率高，业务路径直观。

它的核心价值是“把一台真实机器人做完整”。

局限是总控容易变重，很多边界依赖作者对整个系统的理解，迁移到其他机器人时需要较多人工裁剪。

### 2.3 `tflm`：从整机经验中抽象框架

当前 `tflm` 不再只追求“把当前机器人做出来”，而是在主动拆分：

- 哪些属于板级和驱动；
- 哪些属于设备模块；
- 哪些属于纯算法；
- 哪些属于线程和业务；
- 哪些属于线程间消息；
- 哪些属于编译期配置。

因此它代表的不是简单升级，而是从“具体整机工程”进入“形成自己的框架语言”阶段。

## 3. 当前 tflm 的定位

当前仓库可以概括为：

> 一个以 Zephyr 为运行时基础、以 Kconfig/CMake 为编译期裁剪手段、以 `drivers / modules / algorithm / topic / project` 为主要边界的嵌入式项目框架。

它同时吸收了前两个工程的经验：

| 来源 | 在当前 tflm 中的体现 |
|------|----------------------|
| `basic_framework-master` | 分层、配置、消息通道、可复用意识 |
| `Dust_SentinelRobot_L_Game` | 底盘、云台、IMU、遥控和 CAN 的真实业务闭环 |
| Zephyr | Kconfig、CMake、设备树、线程和系统初始化机制 |

当前最重要的变化不是目录数量增加，而是职责开始稳定：

```text
板级配置 / 驱动
        ↓
设备模块
        ↓
算法与状态估计
        ↓
topic / 消息通道
        ↓
project/thread 业务线程
        ↓
系统启动编排
```

## 4. 当前分层

### 4.1 `drivers/`：硬件接口适配

这一层负责把 Zephyr 设备对象、UART、CAN、SPI、PWM、USB 等底层接口包装成项目可使用的接口。

它不应该承载：

- 底盘控制策略；
- IMU 姿态算法；
- 具体机器人业务状态机；
- 线程之间的业务通信。

### 4.2 `modules/`：设备能力与设备管理

当前模块层已经包含：

- `modules/imu/`
- `modules/remotes/`
- `modules/motors/`
- `modules/powermeter/`

模块层负责：

- 封装具体设备；
- 组合驱动和算法；
- 管理设备配置；
- 维护 `ready_` 状态；
- 对外提供相对完整的设备能力。

模块层不负责统一管理业务线程生命周期。需要多个模块配合时，线程放在 `project/thread/` 中更合适。

### 4.3 `algorithm/`：纯计算能力

当前算法层已经包含：

- PID；
- 功率控制；
- LPF/HPF；
- Kalman/EKF；
- 四元数姿态解算；
- RLS 和稳定性判据；
- TFLM 推理底座。

理想依赖方向是：

```text
algorithm → algorithm
algorithm ↛ modules
algorithm ↛ topic
algorithm ↛ project/thread
```

也就是说，算法只接收数据并输出结果，不创建线程、不持有 UART/CAN/PWM 等设备句柄。

### 4.4 `topic/`：线程间数据契约

当前已有：

- `topic/remote_to/`
- `topic/imu_to/`
- `topic/to_can_tx/`

这一层的价值不是“放几个结构体”，而是明确线程之间交换什么数据：

```text
遥控线程 → remote_to → 底盘/云台线程
IMU 模块 → imu_to → 其他消费者
底盘/云台 → to_can_tx → CAN 发送线程
```

其中：

- zbus 更适合发布/订阅；
- `k_msgq` 更适合 CAN 帧这种逐条消费的数据。

### 4.5 `project/`：项目装配与业务线程

当前 `project/` 负责：

- 板级 overlay、`.conf` 和烧录配置；
- 线程入口；
- 业务控制逻辑；
- 把模块、算法和 topic 组合成一台具体设备。

线程目录中的统一接口是：

```cpp
namespace thread::xxx {
    void thread_init();
    void thread_start(uint8_t prio);
}
```

这是一种有效的过渡形态：线程实现细节被隐藏，系统入口只看到统一调用接口。

## 5. 真实运行链

当前系统入口位于 [src/main.c](/D:/Zephyr/projects/tflm/src/main.c)：

```text
main()
  ├─ System_Bsp_Init()
  ├─ System_Modules_Init()
  └─ System_Thread_Start()
```

当前三个阶段分别承担：

### `System_Bsp_Init()`

目前主要处理 CAN 发送线程涉及的底层初始化入口。

### `System_Modules_Init()`

目前依次初始化：

- GPIO/output；
- 底盘；
- 云台；
- 遥控器；
- IMU；
- TFLM；
- 测试；
- PC 通信。

### `System_Thread_Start()`

目前按显式优先级启动：

- output：6；
- remote：4；
- imu：4；
- CAN：4；
- chassis：5；
- gimbal：5；
- test/pc：6。

这条链已经能表达真实系统依赖，但顺序目前写死在 [project/apps/System_startup.cpp](/D:/Zephyr/projects/tflm/project/apps/System_startup.cpp) 中。

## 6. 当前已经完成的能力

### 6.1 分层不是空目录

`modules`、`algorithm`、`topic` 和 `project/thread` 都已经有实际代码和真实调用关系，不再只是规划文档。

### 6.2 编译期裁剪已经成立

当前通过：

- `project/thread/Kconfig`；
- `modules/Kconfig`；
- `algorithm/Kconfig`；
- `topic/Kconfig`；
- 各层 `CMakeLists.txt`；
- 板级 `.conf`；

共同决定最终编译哪些模块和线程。

例如：

```text
CONFIG_TRD_CHASSIS
  → MOD_CTL_POWER
  → MOD_DEV_MOTOR_DJI
  → TPC_TO_CAN_TX
  → TPC_REMOTE_TO
```

这已经具备 Zephyr 风格的“配置驱动系统形态”。

### 6.3 业务闭环已经成立

当前底盘线程的主要链路是：

```text
remote_to
  → ReadRemote()
  → UpdateTarget()
  → ControlCalculate()
  → PowerAlloc()
  → to_can_tx
  → CAN 发送线程
```

这说明当前架构不是只做抽象，而是已经能承载控制业务。

### 6.4 设备选择已经开始编译期化

IMU 模块已经通过：

```text
CONFIG_MOD_DEV_IMU_BMI088
CONFIG_MOD_DEV_IMU_ICM42688P
```

选择具体传感器实现，公共 `ImuManager` 负责向上提供统一能力。

这说明“同一模块接口、不同底层设备实现”的方向已经成立。

## 7. 仍处于收敛期的部分

### 7.1 初始化仍然是手工注册

新增一个线程，当前仍然需要同时修改：

1. `project/thread/Kconfig`；
2. `project/thread/CMakeLists.txt`；
3. `project/apps/System_startup.cpp`；
4. 线程自己的 `.cpp/.hpp`。

这说明编译期裁剪已经自动化，但初始化注册还没有自动化。

### 7.2 线程优先级和启动顺序还没有成为配置

例如 `remote`、`imu`、`CAN` 的优先级直接写在 `System_startup.cpp` 中。

它们应该逐步变成：

- Kconfig 可配置默认值；
- 初始化等级；
- 初始化优先级；
- 启动失败策略。

### 7.3 `System_startup.cpp` 仍然知道所有线程

这使它成为系统装配中心，但也会逐渐变成“集中式注册表”。

短期内这很清晰；长期如果线程数量继续增加，就会出现：

- include 列表不断变长；
- `#ifdef` 不断增加；
- 初始化顺序和依赖关系依赖人工阅读；
- 删除模块时容易漏删启动代码。

### 7.4 架构文档和目录命名需要统一

当前实际目录是 `project/`，部分旧文档仍使用 `projects/` 表述。

这不影响编译，但会影响长期学习和新人理解，建议后续统一为当前真实路径。

## 8. 架构成熟度判断

可以把当前进度分成五个阶段：

| 阶段 | 状态 | 说明 |
|------|------|------|
| 1. 目录分层 | 已完成 | 主要层次已经建立 |
| 2. 依赖裁剪 | 已完成 | Kconfig/CMake 已参与实际编译 |
| 3. 业务闭环 | 已完成 | IMU、遥控、底盘、CAN 等链路已形成 |
| 4. 初始化注册 | 部分完成 | 统一接口已有，但仍手工集中调用 |
| 5. 框架自驱动 | 尚未完成 | 尚未做到新增组件自动进入初始化链 |

因此当前不是“架构还没做完”，而是：

> 第一轮框架架构已经完成，第二轮重点是减少人工装配和强化规则约束。

## 9. 下一阶段重点

推荐顺序如下：

### 第一优先级：初始化注册机制

把 `thread_init()`、`thread_start()` 从集中式手工调用，逐步改成编译期生成或链接段注册。

### 第二优先级：把启动参数配置化

至少配置以下内容：

- 是否启用；
- 初始化等级；
- 初始化优先级；
- 线程启动优先级；
- 初始化失败后的处理方式。

### 第三优先级：建立依赖验证

让配置系统明确表达：

```text
底盘线程依赖遥控 topic、CAN topic、DJI 电机和功率控制
IMU 线程依赖 IMU 模块和 IMU topic
CAN 线程依赖 CAN 驱动和 CAN TX topic
```

### 第四优先级：选择一个标准模板

建议把 IMU 或 CAN 线程做成标准样板，固定：

```text
Kconfig
  → CMake
  → module
  → topic
  → thread
  → init registration
```

完成这个闭环后，新增功能就不再是“复制旧线程”，而是遵守一套可验证的框架流程。

