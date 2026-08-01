# 从零构建当前 Zephyr 架构并编译 STM32（通用指南，不依赖 Zephyr_HPMicro）

> **适用**：任何 Windows 电脑。全程 **不依赖 Zephyr_HPMicro SDK**。
> **约定**：下文所有 `<workspace>` 是你自己选的顶层目录（例：`D:\zephyr`、`C:\dev\zephyr`），请全文替换成你自己的路径。用 `<...>` 占位符代表"你自己的值"。
> 所有命令在 **PowerShell** 或 **CMD** 中执行。

---

## 目录

- [第 0 步：构建链总览](#第-0-步构建链总览)
- [第 1 步：安装基础工具](#第-1-步安装基础工具)
- [第 2 步：安装 Python 并建虚拟环境](#第-2-步安装-python-并建虚拟环境)
- [第 3 步：安装 west 与 Zephyr Python 依赖](#第-3-步安装-west-与-zephyr-python-依赖)
- [第 4 步：安装 Zephyr SDK（ARM 工具链）](#第-4-步安装-zephyr-sdkarm-工具链)
- [第 5 步：获取 Zephyr 树、项目与自研层](#第-5-步获取-zephyr-树项目与自研层)
- [第 6 步：设置/检查环境变量](#第-6-步设置检查环境变量)
- [第 7 步：构建 STM32](#第-7-步构建-stm32)
- [第 8 步：验证](#第-8 步验证)
- [第 9 步：常见问题](#第-9-步常见问题)
- [附录 A：作者电脑的已知环境参考](#附录-a作者电脑的已知环境参考)
- [附录 B：项目布局与路径假设（纯 ST 可不看 HPM）](#附录-b项目布局与路径假设纯-st-可不看-hpm)

---

## 第 0 步：构建链总览

先搞懂要装哪些东西、它们干什么：

```text
项目源码                        <workspace>\projects\dust
    ↓ west（Zephyr 构建工具）     Python 里 pip 装的
    ↓ Zephyr 树                  <workspace>\zephyr（west init/update 拉取）
    ↓ Zephyr SDK（ARM 编译器）    <workspace>\zephyr-sdk-0.16.8\arm-zephyr-eabi
    ↓ 项目板级配置                项目内 project\boards\st\puzhong（或 board_rm_c）
    ↓ 产物
    <workspace>\projects\dust\build\<板卡>\zephyr\zephyr.elf
```

**为什么不依赖 Zephyr_HPMicro**：STM32F407 的 SoC、pinctrl、驱动全在 Zephyr 官方树里。本架构额外需要的外部层只有 `zephyr_user`（自研板卡 + ARM 的 CMSIS 补丁）。

**磁盘上需要出现的东西**（装完后的目录）：

```text
<workspace>\
├── zephyr\                  # Zephyr 树（v4.3.0）
├── zephyr-sdk-0.16.8\       # Zephyr SDK（含 arm-zephyr-eabi 工具链）
├── zephyr_user\             # 自研层（自研板卡 stm32f407igh6 + CMSIS 补丁 + usb 模块）
├── projects\
│   └── dust\                # 本项目（含 git submodule：drivers/algorithm/modules/topic/cmd/init/project）
└── .venv\                   # Python 虚拟环境（west 装这里）
```

> 这个布局**不能随便改**：项目 `CMakeLists.txt` 用相对路径 `../../zephyr_user`、`../../zephyr-sdk-0.16.8` 定位自研层和 SDK。保持上表结构即可。

---

## 第 1 步：安装基础工具

| 工具 | 版本要求 | 用途 | 获取方式 |
| --- | --- | --- | --- |
| Git | 任意较新 | 拉代码、submodule | https://git-scm.com/download/win |
| Python | **3.10+**（推荐 3.12） | west 与 Zephyr 脚本 | https://www.python.org/downloads/ |
| CMake | **≥ 3.20** | Zephyr 构建 | https://cmake.org/download/（选 Windows x64 .msi，装时勾选 "Add to PATH"） |
| Ninja | 任意 | 构建生成器（**必须用 Ninja，不要用 VS**） | `pip install ninja`（装完 Python 后）或 https://github.com/ninja-build/ninja/releases |
| OpenOCD | 可选 | 烧录 STM32 | 板级 `board.cmake` 会引用，烧录才需要 |

### 1.1 安装 Git

官网下载安装，一路默认（确保勾选 "Git from the command line" 加入 PATH）。

验证：

```powershell
git --version
```

### 1.2 安装 Python

从 https://www.python.org/downloads/ 下载 3.12 的 Windows 安装器，**务必勾选 "Add python.exe to PATH"**。

验证：

```powershell
python --version      # 应显示 Python 3.12.x
```

> 若 `python` 命令没反应或弹出微软商店，说明 PATH 里被 Windows 商店的占位符占了。解决：到「设置 → 应用 → 应用执行别名」关掉 `python.exe`/`python3.exe` 别名，或改用下面 2.2 的 `py` 启动器。

### 1.3 安装 CMake

官网下载 `cmake-<版本>-windows-x86_64.msi`，安装时勾选 **"Add CMake to the system PATH"**。

验证：

```powershell
cmake --version
```

### 1.4 安装 Ninja

装完 Python 后，在 venv 里 `pip install ninja`（见第 2、3 步），或单独下载 ninja 的 zip 解压并加入 PATH。

验证：

```powershell
ninja --version
```

> **⚠️ 关键**：Zephyr 构建生成器**必须是 Ninja**。如果你之后看到日志出现 `Building for: Visual Studio ...`，说明用了 VS 生成器，会失败——用 `west build`（默认 Ninja）就能避开。

---

## 第 2 步：安装 Python 并建虚拟环境

Zephyr 官方建议用**虚拟环境（venv）**隔离 west 及其依赖，避免污染系统 Python。

### 2.1 确认 Python 可用

```powershell
python --version
# 或（如果 python 被占位符劫持）：
py --version
```

### 2.2 创建虚拟环境

```powershell
# 在 workspace 顶层建 .venv（用 python 或 py 都行）
python -m venv <workspace>\.venv

# 激活（PowerShell）：
<workspace>\.venv\Scripts\Activate.ps1
# 或（CMD）：
<workspace>\.venv\Scripts\activate.bat
```

激活成功后，命令行提示符前会出现 `(.venv)`。**之后所有命令都在这个激活的 venv 里执行。**

验证：

```powershell
python --version        # 用的是 venv 里的 python
where python            # 第一个结果应是 <workspace>\.venv\Scripts\python.exe
```

---

## 第 3 步：安装 west 与 Zephyr Python 依赖

在 **激活的 venv** 里：

```powershell
# 1) 安装 west 构建工具
pip install west

# 2) 安装 Zephyr 的 Python 依赖
#    最稳的方式是装 Zephyr 官方 requirements（需要先有 zephyr 树，见第 5 步；
#    想先装也行，装 zephyr 树后补一次即可）
pip install west pyelftools pykwalify packaging jsonschema PyYAML pyserial
```

> 需要的关键包：`west`、`pyelftools`、`pykwalify`、`packaging`、`jsonschema`、`PyYAML`、`pyserial`。缺 `jsonschema` 会在构建早期报 `ModuleNotFoundError`。

验证 west：

```powershell
west --version        # → West version: v1.5.0
```

---

## 第 4 步：安装 Zephyr SDK（ARM 工具链）

Zephyr SDK 提供编译 STM32 的 **ARM 交叉编译器**（`arm-zephyr-eabi-gcc`）。

### 4.1 下载 SDK

从 Zephyr 官方 GitHub Releases 下载：

```text
https://github.com/zephyrproject-rtos/sdk-ng/releases
```

选一个版本（本项目已验证 0.16.8；Zephyr v4.3.0 官方建议 0.17.x）：
- 推荐下载 `zephyr-sdk-<版本>-windows_x86_64.zip`（Windows）

### 4.2 解压到 workspace

```powershell
# 解压到 <workspace>\zephyr-sdk-<版本>
# 例如 0.16.8：
Expand-Archive .\zephyr-sdk-0.16.8_windows_x86_64.zip -DestinationPath <workspace>\zephyr-sdk-0.16.8
# 解压后会有一层嵌套目录，整理成 <workspace>\zephyr-sdk-0.16.8\ 直接含 sdk_version、arm-zephyr-eabi 等
```

### 4.3 确认 ARM 工具链

```powershell
ls <workspace>\zephyr-sdk-0.16.8\arm-zephyr-eabi\bin\arm-zephyr-eabi-gcc.exe
```

### 4.4 运行 SDK 安装脚本（推荐）

Zephyr SDK 0.16 起附带环境设置脚本，解压后跑一次即可注册工具链：

```powershell
# 用激活的 venv python 跑 SDK 的 setup 脚本（生成 Zephyr-sdkConfig.cmake 并注册环境）
python <workspace>\zephyr-sdk-0.16.8\setup.cmd
# 或按 README 用 west 提供的 sdk 处理
```

> 如果不用 setup 脚本，则手动设环境变量 `ZEPHYR_SDK_INSTALL_DIR`（见第 6 步）。本项目 `CMakeLists.txt` 会自动把 `ZEPHYR_SDK_INSTALL_DIR` 设为项目相对位置 `../../zephyr-sdk-0.16.8`（= `<workspace>\zephyr-sdk-0.16.8`），所以 SDK 放对位置后通常不用手动设。

---

## 第 5 步：获取 Zephyr 树、项目与自研层

### 5.1 建 workspace 并初始化 west（拉 Zephyr 树）

```powershell
mkdir <workspace>
cd <workspace>

# 初始化 west workspace（默认拉官方 zephyr 仓库）
west init
west update
```

> `west init` 会创建 `.west\config`，并把官方 zephyr 拉到 `<workspace>\zephyr`（默认 manifest 指向官方 zephyr 仓库）。
> 如果你要用自己的 zephyr 分支/manifest：`west init -m <manifest仓库URL> <workspace>`。

验证：

```powershell
git -C <workspace>\zephyr describe --tags    # → v4.3.0 之类
```

### 5.2 拉取自研层 zephyr_user

```powershell
cd <workspace>
git clone <你的 zephyr_user 仓库 URL> zephyr_user
```

`zephyr_user` 里包含：自研板卡 `boards\st\stm32f407igh6\`、ARM 的 CMSIS 补丁 `modules\cmsis\`、自研 USB 模块 `modules\usb\`。

### 5.3 拉取项目 dust（含 submodule）

```powershell
cd <workspace>
git clone <你的 dust 仓库 URL> projects\dust
cd projects\dust
git submodule update --init --recursive
```

项目的 `drivers/algorithm/modules/topic/cmd/init/project` 是 git submodule，必须拉取。

验证项目结构：

```powershell
ls <workspace>\projects\dust\drivers      # 应存在（submodule）
ls <workspace>\zephyr_user\boards\st\stm32f407igh6\   # 自研板卡
```

---

## 第 6 步：设置/检查环境变量

### 6.1 必设：ZEPHYR_BASE

```powershell
$env:ZEPHYR_BASE = "<workspace>\zephyr"
```

> 用 `west build` 时，west 会按 `.west\config` 自动找 zephyr 树，所以这个变量在走 west 时**通常可省**。但直接 `cmake` 时必需。

### 6.2 建议设：ZEPHYR_SDK_INSTALL_DIR

```powershell
$env:ZEPHYR_SDK_INSTALL_DIR = "<workspace>\zephyr-sdk-0.16.8"
```

> 本项目 `CMakeLists.txt` 已自动设置（相对路径），可省；但显式设了更稳。

### 6.3 工具链变量

```powershell
$env:ZEPHYR_TOOLCHAIN_VARIANT = "zephyr"
```

### 6.4 不要设：SDK_GLUE_DIR（除非你要 HPM）

项目根 `CMakeLists.txt` 的逻辑是：`if(DEFINED ENV{SDK_GLUE_DIR}) 用之，否则默认 E:/Zephyr_HPMicro/sdk_glue`。

- **纯 ST 构建**：**不要设** `SDK_GLUE_DIR`，让它走默认分支。默认值指向 HPM 目录，如果那目录不存在，需要在 CMake 里加保护（见附录 B）。
- 如果你确实要 HPM：再设 `SDK_GLUE_DIR` 指向 HPM 的 sdk_glue。

> 建议把这些变量写进一个每次构建前 source 的脚本（`env.ps1` / `env.bat`），避免每次手敲：

```powershell
# <workspace>\env.ps1 内容
$env:ZEPHYR_BASE = "<workspace>\zephyr"
$env:ZEPHYR_TOOLCHAIN_VARIANT = "zephyr"
$env:ZEPHYR_SDK_INSTALL_DIR = "<workspace>\zephyr-sdk-0.16.8"
```

### 6.5 把 Python/ninja 放对位置

确认激活 venv 后 `python`、`west`、`ninja` 都指向 venv：

```powershell
where python      # → <workspace>\.venv\Scripts\python.exe
where west        # → <workspace>\.venv\Scripts\west.exe
where ninja       # → <workspace>\.venv\Scripts\ninja.exe
```

---

## 第 7 步：构建 STM32

### 7.0 前提

```powershell
cd <workspace>\projects\dust
# 确认在激活的 venv 里（提示符前有 (.venv)）
west --version
```

### 7.1 用「官方板 stm32f4_disco」构建（推荐先跑通这个）

```powershell
west build -b stm32f4_disco -d build/stm32f4_disco -- -DBOARD_CFG=puzhong
```

**参数说明**：
- `-b stm32f4_disco`：官方板卡（Zephyr 树自带，不需要自研板卡）；
- `-d build/stm32f4_disco`：独立构建目录。**不要复用别的板的 build 目录**（会缓存串扰）；
- `-- -DBOARD_CFG=puzhong`：传给 CMake，指定项目板级配置分组 `project\boards\st\puzhong\`。**必须传**，否则 CMake 按 `BOARD_CFG=<板卡名>` 去找 `project\boards\st\stm32f4_disco\`（不存在），overlay/conf 不会加载。

**预期输出**：

```text
-- Found BOARD.dts: <...stm32f4_disco...>.dts
-- Found devicetree overlay: <workspace>/projects/dust/project/boards/st/puzhong/stm32f4_disco.overlay
Parsing <workspace>/projects/dust/Kconfig
-- Configuring done
-- Generating done
-- Build files have been written to: <workspace>/projects/dust/build/stm32f4_disco
[...] Linking CXX executable zephyr\zephyr_pre0.elf
[...] Generating files ...
[...] zephyr.elf      ← 固件产物
```

### 7.2 用「自研板 stm32f407igh6」构建

```powershell
west build -b stm32f407igh6 -d build/stm32f407igh6 -- -DBOARD_CFG=board_rm_c
```

`stm32f407igh6` 是自研板卡（176 脚 BGA），定义在 `zephyr_user\boards\st\stm32f407igh6\`，靠项目 `CMakeLists.txt` 的 `BOARD_ROOT`（追加 `zephyr_user`）发现。板级 `board_rm_c` 的 overlay 挂了 BMi088 SPI + CAN + UART。

### 7.3 只配置不编译（快速查配置错误）

```powershell
west build -b stm32f4_disco -d build/stm32f4_disco -- -DBOARD_CFG=puzhong -t configure
```

### 7.4 烧录（可选）

```powershell
west flash -d build/stm32f4_disco          # 用 project\boards\st\puzhong\board.cmake + openocd.cfg
west flash -d build/stm32f407igh6
```

### 7.5 最小构建验证（推荐第一步先跑这个）

先只留 `TRD_GPIO`，关掉其它功能线程，用**最小链路**判断环境是否 OK。

在 `prj.conf` 里追加：

```conf
# 最小构建验证：只留 TRD_GPIO，其余全关
CONFIG_TRD_GPIO=y
CONFIG_TRD_REMOTE=n
CONFIG_TRD_IMU=n
CONFIG_TRD_CHASSIS=n
CONFIG_TRD_GIMBAL=n
CONFIG_TRD_CAN_TX=n
CONFIG_TRD_PC=n
CONFIG_TRD_TFLM=n
CONFIG_TRD_TEST=n
CONFIG_USE_CMD_SHELL=n
CONFIG_USE_CMD_BUZZER=n
CONFIG_USE_CMD_FLASH=n
```

> 当前 `prj.conf` 只有 `CONFIG_PRJ_TEST=y`，默认 `TRD_*` 大多是 n、`TRD_GPIO=y`——实际只需补一条 `CONFIG_USE_CMD_FLASH=n`（它是唯一 `default y` 的）即可达到"只留 TRD_GPIO"。

**为什么**：`TRD_GPIO` 是唯一**不依赖 UART/CAN/SPI/USB/DMA 等外设**的线程（只 GPIO 输出 + 软件定时器）。其它线程各拉一堆底层（UART DMA / SPI / CAN / USB / TFLM 依赖栈），失败时无法区分是**环境问题**还是**功能依赖问题**。只留 TRD_GPIO，编译链路最小化——通，则 Zephyr/SDK/west/Ninja/板级/CMake 全 OK。

**判断标准（三条都满足 = 环境 OK）**：

```text
1. 配置成功：日志出现 -- Configuring done
2. 编译成功：无 Kconfig/CMake/路径/toolchain 错误
3. 链接成功：生成 build\<板卡>\zephyr\zephyr.elf
```

环境跑通后，再逐步打开 `TRD_REMOTE` → `TRD_IMU` → ... 逐个验证功能，出问题能精确定位到那一层。

---

## 第 8 步：验证

```powershell
# 1) 固件产物存在
ls build\stm32f4_disco\zephyr\zephyr.elf

# 2) 是 STM32 SoC
grep "CONFIG_SOC_STM32" build\stm32f4_disco\zephyr\.config
# → CONFIG_SOC_STM32F407XG=y 之类

# 3) 默认线程只有 GPIO（project\Kconfig 默认 TRD_GPIO=y，其余 n）
grep "CONFIG_TRD" build\stm32f4_disco\zephyr\.config

# 4) 没有引入 HPM 符号
grep -i "HPM" build\stm32f4_disco\zephyr\.config   # 预期无
```

---

## 第 9 步：常见问题

| # | 现象 | 原因 | 解决 |
| --- | --- | --- | --- |
| 1 | `python` 弹出微软商店/无输出 | Windows 商店占位符劫持 PATH | 设置→应用→应用执行别名，关掉 python/python3 别名；或用 `py` 启动器；或重装并勾选 Add to PATH |
| 2 | 日志出现 `Building for: Visual Studio ...` | CMake 选了 VS 生成器 | 确保 `ninja` 在 PATH，用 `west build`（默认 Ninja） |
| 3 | `not a valid zephyr module`（路径含 HPM/SDK_GLUE） | `SDK_GLUE_DIR` 被设成了不存在路径 | `Remove-Item Env:SDK_GLUE_DIR`；或按附录 B 加 `if(EXISTS)` 保护 |
| 4 | `ModuleNotFoundError: No module named 'jsonschema'` | 没在 venv 里 / 依赖缺 | 确认 venv 激活；`pip install jsonschema pyelftools pykwalify packaging PyYAML pyserial` |
| 5 | `Error finding board: stm32f407igh6` | `BOARD_ROOT` 没含 `zephyr_user`，或自研板卡目录缺失 | 确认 `CMakeLists.txt` 的 `ZEPHYR_USER_DIR` 指向 `<workspace>\zephyr_user`，且 `zephyr_user\boards\st\stm32f407igh6\` 存在 |
| 6 | overlay/conf 没加载 | 没传 `-DBOARD_CFG` | 构建命令末尾加 `-- -DBOARD_CFG=puzhong`（或 board_rm_c） |
| 7 | 编译报 CMSIS 相关错误 | ARM 需要 `zephyr_user\modules\cmsis` 补丁 | 确认它在 `CMakeLists.txt` 的 `ZEPHYR_EXTRA_MODULES`（当前已含） |
| 8 | 报 SDK 版本过低/工具链找不到 | SDK 版本或目录问题 | 确认 `<workspace>\zephyr-sdk-0.16.8\arm-zephyr-eabi\bin\arm-zephyr-eabi-gcc.exe` 存在；必要时升级 SDK 到 0.17.x |
| 9 | `CONFIG_PRJ_TEST` 未生效，7 层没编译 | `prj.conf` 少了 `CONFIG_PRJ_TEST=y` | 确认 `prj.conf` 有 `CONFIG_PRJ_TEST=y`（根门禁，控制架构层编译） |

---

## 附录 A：作者电脑的已知环境参考

> 以下仅作参考，**不是安装步骤**。作者机器的关键路径：

```text
workspace  : E:\Zephyr
Python     : 3.12.13（uv 管理）；venv 在 E:\Zephyr\.venv（含 west 1.5.0 及全部 Zephyr 依赖）
west       : 仅 E:\Zephyr\.venv\Scripts\west.exe 可用（PATH 里的 python/west 是坏的商店占位符）
Zephyr 树  : E:\Zephyr\zephyr（v4.3.0）
SDK        : E:\Zephyr\zephyr-sdk-0.16.8（含 arm-zephyr-eabi）
项目       : E:\Zephyr\projects\dust
zephyr_user: E:\Zephyr\zephyr_user
```

作者机器曾有一套指向 `E:\Zephyr_Test` 的**过期环境变量**（`ZEPHYR_BASE`/`ZEPHYR_SDK_INSTALL_DIR`/`SDK_GLUE_DIR`），构建前必须清掉：

```powershell
Remove-Item Env:ZEPHYR_BASE,Env:ZEPHYR_SDK_INSTALL_DIR,Env:SDK_GLUE_DIR -ErrorAction SilentlyContinue
```

---

## 附录 B：项目布局与路径假设（纯 ST 可不看 HPM）

### B.1 项目对目录布局的固定假设

项目 `CMakeLists.txt` 用相对路径定位外部依赖，所以布局要固定：

```text
<workspace>\
├── zephyr\                       # ../../zephyr           （从 projects/dust 上溯 2 级）
├── zephyr-sdk-0.16.8\            # ../../zephyr-sdk-0.16.8
├── zephyr_user\                  # ../../zephyr_user
└── projects\
    └── dust\                     # 项目根
```

### B.2 纯 ST 构建与 HPM 的关系

项目 `CMakeLists.txt` 无条件把 `${SDK_GLUE_DIR}` 和 `${SDK_GLUE_DIR}/../modules/lib/CherryUSB` 加进 `ZEPHYR_EXTRA_MODULES`（HPM 用）。

- **如果你机器上 HPM 目录存在**（默认分支 `E:/Zephyr_HPMicro/sdk_glue`）：HPM 作为额外 module **无害加载**，ST 不 select 它的任何符号，直接构建即可。
- **如果你机器上没有 HPM 目录**（纯 ST 环境）：`ZEPHYR_EXTRA_MODULES` 引用不存在的目录会报 "not a valid zephyr module"。解决办法：给 `CMakeLists.txt` 的 SDK_GLUE 相关加 `if(EXISTS)` 保护，或把 `SDK_GLUE_DIR` 指到一个不存在的空目录前先保护。**ST 构建不需要 HPM 的任何能力**。

```cmake
# CMakeLists.txt 建议改法（纯 ST 时）
if(DEFINED ENV{SDK_GLUE_DIR} AND EXISTS "$ENV{SDK_GLUE_DIR}")
  set(SDK_GLUE_DIR "$ENV{SDK_GLUE_DIR}")
else()
  set(SDK_GLUE_DIR "")
endif()
if(EXISTS "${SDK_GLUE_DIR}")
  list(APPEND ZEPHYR_EXTRA_MODULES "${SDK_GLUE_DIR}" "${SDK_GLUE_DIR}/../modules/lib/CherryUSB")
endif()
```

### B.3 USB 是 HPM 专用

`TRD_PC`（`DUST_COM_USB`）依赖 HPM 的 USB 控制器（`hpmicro,hpm-qingyuusb`）。**ST 板没有该设备树节点，不要开 `CONFIG_TRD_PC`**（默认即关闭）。
