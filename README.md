# tflm — Zephyr 模块化嵌入式框架

## 核心理念

**这个框架是一个集成环境，不是一块积木。**

用户需要做的只是写好三样东西——**初始化流程（apps）、板级配置（boards）、线程逻辑（thread）**——然后塞进 `project/` 目录，框架自动把它们嵌入到整个编译和运行环境中。

```
                    ┌──────────────────────────┐
                    │          集成环境          │
                    │  驱动 / 算法 / 模块 / 话题  │
                    │   / 构建系统 / Kconfig体系  │
                    └──────────┬───────────────┘
                               │ 嵌入
                    ┌──────────▼───────────────┐
                    │    project/ （用户代码）   │
                    │  ┌──────┐ ┌──────┐ ┌───┐ │
                    │  │ apps │ │boards│ │thread│ │
                    │  │ 启动 │ │板配置│ │线程│ │
                    │  └──────┘ └──────┘ └───┘ │
                    └──────────────────────────┘
```

`project/` 与框架的关系是**嵌入，而不是相辅相成**。你完全可以复制一份 `project/` 到另一个框架项目里直接用，只要驱动/模块接口一致。

---

## 架构分层

```
┌─────────────────────────────────────────────────┐
│                   命令层 cmd/                     │
│              运行时 Shell 调试命令                │
└──────────────────────┬──────────────────────────┘
                       │ 调用
┌──────────────────────▼──────────────────────────┐
│                  应用层 project/                 │
│  ┌──────────┐ ┌──────────┐ ┌────────────────┐   │
│  │  apps/   │ │ boards/  │ │   thread/      │   │
│  │ 启动编排  │ │ DTS+配置  │ │  GPIO/遥控/    │   │
│  │          │ │ +烧录脚本 │ │  底盘/云台/    │   │
│  │          │ │          │ │  IMU/PC通信    │   │
│  └──────────┘ └──────────┘ └────────────────┘   │
│  每个 project/ 是一个完整的可移植项目单元         │
└──────────────────────┬──────────────────────────┘
                       │ 调用
         ┌─────────────┼─────────────┐
         │             │             │
  ┌──────▼──────┐ ┌────▼────┐ ┌─────▼─────┐
  │   模块层     │ │ 算法层  │ │   话题层   │
  │  modules/   │ │algorithm│ │  topic/   │
  │             │ │         │ │           │
  │ · 电机      │ │ · PID   │ │ · 遥控数据│
  │ · IMU       │ │ · EKF   │ │ · IMU姿态 │
  │ · 遥控器    │ │ · RLS   │ │ · CAN发送 │
  │ · 功率计    │ │ · TFLM  │ │           │
  └──────┬──────┘ └────┬────┘ └─────┬─────┘
         │             │             │
         └─────────────┼─────────────┘
                       │
              ┌────────▼────────┐
              │     驱动层       │
              │   drivers/      │
              │ UART SPI CAN   │
              │ RS485 USB GPIO │
              │      PWM        │
              └────────┬────────┘
                       │
              ┌────────▼────────┐
              │   Zephyr HAL    │
              │   + SDK Glue    │
              └─────────────────┘
```

### 各层的自洽性

每一层都是独立的 git 仓库，可单独版本管理。层间不反向依赖：

```
Zephyr HAL        ← 厂商提供，框架不关心
驱动层 drivers/   ← 封装 HAL，不依赖以上任何层
算法层 algorithm/ ← 纯数学，零硬件依赖，可独立测试
模块层 modules/   ← 面向硬件的功能封装，只依赖驱动层 Kconfig
话题层 topic/    ← zbus 数据结构定义，无业务逻辑
命令层 cmd/      ← 调试接口，依赖模块层
应用层 project/ ← 嵌入选中的模块/驱动/算法组成完整系统
```

---

## project/ —— 可移植项目单元

### 设计思想

`project/` 不是框架的一部分，而是**嵌入到框架中的用户代码**。

```
复制一个项目到其他框架：
  cp -r project/my_project another_framework/project/
  修改 CMakeLists.txt 的 PROJECT_DIR 和 CONFIG_SYM
  west build → 直接跑

换一个项目到当前框架：
  把旧的 project/ 备份，新的 project/ 放进来
  重新配置 → 编译 → 跑
```

### 项目包含的三部分

| 目录 | 内容 | 职责 |
|------|------|------|
| `apps/` | `main.c`、`System_startup.cpp`、`Irq_handlers.cpp` | 系统入口 + 三段式初始化编排 |
| `boards/` | DTS overlay、Kconfig 覆写、烧录脚本 | 将框架的驱动绑定到具体硬件引脚 |
| `thread/` | 各 RTOS 线程的 `thread_init()` / `thread_start()` | 功能逻辑 |

三者组合决定了一个项目跑在哪个板子上、启动哪些线程、初始化顺序是什么。

### 三段式初始化

`System_startup.cpp` 是项目的总控：

```
System_Bsp_Init()       → CAN 控制器等底层硬件初始化
    ↓
System_Modules_Init()   → 各模块按顺序 thread_init()
    ↓
System_Thread_Start()   → 各模块按优先级 thread_start()
```

每个步骤通过 `#ifdef CONFIG_TRD_XXX` 条件编译。新增线程只需：

1. `project/thread/xxx/` 下写 `trd_xxx.cpp` + `trd_xxx.hpp`
2. `project/thread/Kconfig` 加 `config TRD_XXX`
3. `project/thread/CMakeLists.txt` 加编译条件
4. `System_startup.cpp` 加三段 `#ifdef` 调用

### 板级配置

`project/boards/` 按 `厂商/板型/` 组织：

```
project/boards/
├── hpm/
│   ├── hpm5361icb/     ← HPM5361ICB 板
│   └── hpm6e00evk/     ← HPM6E00EVK 板
└── st/
    ├── puzhong/         ← STM32F4 普中板
    └── board_rm_c/      ← STM32F4 RM 板
```

每个板型目录包含：

| 文件 | 作用 |
|------|------|
| `hpm5361icb.overlay` | 设备树引脚映射、alias 定义 |
| `hpm5361icb.conf` | 芯片层 Kconfig（CAN/DMA/USB IP 选择） |
| `board.cmake` | 烧录脚本路径（可选） |

### 项目切换

```cmake
# CMakeLists.txt — 只需改这两行
set(PROJ_DIR projects)          # 项目目录
set(CONFIG_SYM PRJ_TEST)        # 对应 Kconfig 门禁
```

```bash
# 构建时选择板型
west build -b hpm5361icb -- -DBOARD_CFG=hpm5361icb
west build -b stm32f407igh6 -- -DBOARD_CFG=board_rm_c
```

`BOARD_CFG` 指向 `project/boards/*/<BOARD_CFG>/` 下的配置组。

---

## 层间解耦

### 驱动层与模块层：Kconfig 自动拉依赖

模块通过 `select` 拉驱动，不关心驱动文件在哪：

```kconfig
config MOD_DEV_REMOTE
    bool "Remote receiver"
    select COM_UART_DMA       # 遥控器需要 UART DMA 驱动
    select TPC_REMOTE_TO      # 需要发布遥控话题数据
```

CMakeLists 按条件加入编译，未选中的代码**完全不编译**：

```cmake
if(CONFIG_MOD_DEV_REMOTE)
    target_sources(...)
endif()
```

### 模块层与模块层：zbus 零耦合

模块之间不直接调用，通过 zbus 发布-订阅通信：

```
遥控器模块 → pub remote_to → zbus → 底盘线程（订阅 remote_to）
IMU 模块   → pub imu_to    → zbus → 底盘线程（订阅 imu_to）
底盘模块   → pub to_can_tx → zbus → CAN TX 线程（订阅 to_can_tx）
```

新增一个消费者不需要改生产者代码，只需订阅对应的 topic。

### 应用层与驱动层：RxStream 抽象

UART、RS485、USB 共用同一个接收接口：

```cpp
class RxStream {
    virtual bool     Init(const struct device* dev, const Config& cfg) = 0;
    virtual void     SetNotify(struct k_sem* sem) = 0;
    virtual uint16_t Read(uint8_t* buf, uint16_t max_len) = 0;
};
```

上层写一次逻辑，驱动层切换物理接口不需要改上层代码。

### 算法层：零硬件依赖

所有算法是纯数学，不包含任何 `#include <zephyr/...>`：

```
algorithm/
├── controller/    PID、功率控制、定时器
├── filter/        LPF、HPF、Kalman、Quaternion EKF
├── identify/      RLS 递推最小二乘
└── tflm/          TensorFlow Lite Micro 推理
```

可独立于整个框架进行单元测试。

---

## 配置体系

```
prj.conf                        → 项目公共（与具体板型无关）
  ├ CONFIG_CPP, CONFIG_STD_CPP17
  ├ CONFIG_HW_STACK_PROTECTION
  └ CONFIG_UART_CONSOLE

project/boards/*/*.conf        → 芯片层（与 SoC IP 绑定）
  ├ CONFIG_MCAN_HPMICRO         → HPM CAN 控制器
  ├ CONFIG_DMAV2_HPMICRO        → HPM DMA 控制器
  └ CONFIG_CHERRYUSB_DEVICE_HPM → HPM USB 设备 IP

project/thread/Kconfig          → 功能模块开关
  ├ TRD_CHASSIS → select MOD_CTL_POWER, select TPC_TO_CAN_TX
  ├ TRD_IMU     → select MOD_DEV_IMU
  └ TRD_PC      → select COM_USB
```

---

## 子模块

| 子模块 | 仓库 | 说明 |
|--------|------|------|
| `algorithm/` | [Dust_Zephyr_Architecture_Algorithm](https://github.com/qingyu0310/Dust_Zephyr_Architecture_Algorithm) | 控制/滤波/辨识算法 |
| `drivers/` | [Dust_Zephyr_Architecture_Drivers](https://github.com/qingyu0310/Dust_Zephyr_Architecture_Drivers) | 硬件外设驱动 |
| `modules/` | [Dust_Zephyr_Architecture_Modules](https://github.com/qingyu0310/Dust_Zephyr_Architecture_Modules) | 设备模块封装 |
| `topic/` | [Dust_Zephyr_Architecture_Topic](https://github.com/qingyu0310/Dust_Zephyr_Architecture_Topic) | 线程间数据通道 |
| `cmd/` | [Dust_Zephyr_Architecture_Cmd](https://github.com/qingyu0310/Dust_Zephyr_Architecture_Cmd) | Shell 命令 |
| `project/` | [Dust_Zephyr_Architecture_Project](https://github.com/qingyu0310/Dust_Zephyr_Architecture_Project) | 应用层（app/board/thread） |

## 目录总览

```text
├── algorithm/       → submodule
├── cmd/             → submodule
├── drivers/         → submodule
├── modules/         → submodule
├── topic/           → submodule
├── project/         → submodule
├── src/               应用入口
├── include/           公共头文件
├── doc/               文档
├── scripts/           工具脚本
├── Kconfig            根 Kconfig
├── CMakeLists.txt     构建入口
└── prj.conf           默认配置
```

---

## 构建

```bash
# HPM5361ICB
west build -b hpm5361icb -- -DBOARD_CFG=hpm5361icb

# STM32F4 普中
west build -b stm32f4_disco -- -DBOARD_CFG=puzhong

# STM32F4 RM
west build -b stm32f407igh6 -- -DBOARD_CFG=board_rm_c

# HPM6E00EVK
west build -b hpm6e00evk -- -DBOARD_CFG=hpm6e00evk
```

HPMicro SDK Glue 默认从 `D:/Zephyr_HPMicro/sdk_glue` 引入，可通过 `SDK_GLUE_DIR` 环境变量覆盖。
