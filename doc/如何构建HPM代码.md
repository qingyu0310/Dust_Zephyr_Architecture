# 从零构建当前 Zephyr 架构并编译 HPM 代码（带 Zephyr_HPMicro SDK）

> **适用**：任何 Windows 电脑，需要编译 HPMicro（RISC-V）代码，例如自研核心板 `hpm5361icb`。
> 与「[如何构建STM32代码.md](如何构建STM32代码.md)」配套：那份是**不带 HPM SDK** 的 ST 构建；这份是**带 HPM SDK** 的 HPM 构建。
> **约定**：`<workspace>` 是你的 Zephyr 工作区（例：`D:\Zephyr`），`<root>` 是它和 Zephyr_HPMicro 共同的父目录（例：`D:\`）。用 `<...>` 占位符代表你自己的值。
> 所有命令在 **PowerShell** 或 **CMD** 中执行。

---

## 目录

- [第 0 步：构建链总览](#第-0-步构建链总览)
- [第 1 步：安装基础工具](#第-1 步安装基础工具)
- [第 2 步：安装 Python 并建虚拟环境](#第-2-步安装-python-并建虚拟环境)
- [第 3 步：安装 west 与 Zephyr Python 依赖](#第-3-步安装-west-与-zephyr-python-依赖)
- [第 4 步：安装 Zephyr SDK（RISC-V 工具链）](#第-4-步安装-zephyr-sdkrisc-v-工具链)
- [第 5 步：获取 Zephyr 树、项目与自研层](#第-5-步获取-zephyr-树项目与自研层)
- [第 6 步：获取 HPM SDK（sdk_glue / sdk_glue_user / sdk_env）](#第-6-步获取-hpm-sdksdk_glue--sdk_glue_user--sdk_env)
- [第 7 步：设置环境变量（关键）](#第-7-步设置环境变量关键)
- [第 8 步：构建 HPM](#第-8-步构建-hpm)
- [第 9 步：验证](#第-9-步验证)
- [第 10 步：常见问题](#第-10-步常见问题)
- [附录 A：作者电脑的已知环境参考](#附录-a作者电脑的已知环境参考)
- [附录 B：HPM 相关路径假设](#附录-bhpm-相关路径假设)

---

## 第 0 步：构建链总览

```text
项目源码                        <workspace>\projects\dust
    ↓ west（Zephyr 构建工具）     Python 里 pip 装的
    ↓ Zephyr 树                  <workspace>\zephyr
    ↓ Zephyr SDK（RISC-V 编译器） <workspace>\zephyr-sdk-0.16.8\riscv64-zephyr-elf
    ↓ HPM SDK（板级/SOC/驱动）    <root>\Zephyr_HPMicro\sdk_glue + sdk_glue_user + sdk_env
    ↓ 项目板级配置                项目内 project\boards\hpm\hpm5361icb（或 hpm6e00evk）
    ↓ 产物
    <workspace>\projects\dust\build\<板卡>\zephyr\zephyr.elf
```

**HPM 与 ST 构建的唯一区别**：HPM 需要额外的 **Zephyr_HPMicro SDK**（提供 HPMicro 的板卡/SOC/DTS/驱动），通过环境变量 `SDK_GLUE_DIR` 接入。

**磁盘上需要出现的东西**（装完后）：

```text
<root>\                          # 父目录（E:\ 之类）
├── Zephyr\                      # workspace
│   ├── zephyr\                  # Zephyr 树（v4.3.0）
│   ├── zephyr-sdk-0.16.8\       # Zephyr SDK（含 riscv64-zephyr-elf）
│   ├── zephyr_user\             # 自研层（CMSIS/usb 模块；HPM 板卡在 sdk_glue_user）
│   ├── projects\dust\           # 本项目
│   └── .venv\                   # Python venv
└── Zephyr_HPMicro\              # HPM SDK（见第 6 步）
    ├── sdk_glue\                # HPMicro 官方 Zephyr glue（board/soc/dts/driver）
    ├── sdk_glue_user\           # 用户自研（hpm5361icb 板卡 + HPM5300 SoC）
    ├── sdk_env\                 # HPM SDK 源码（hpm_sdk，驱动/组件）
    └── modules\                 # CherryUSB 等
```

> **布局约束（不能随便改）**：项目 `CMakeLists.txt` 用相对路径定位 HPM：
> - `SDK_GLUE_USER_DIR = ../../../Zephyr_HPMicro/sdk_glue_user`（从 `<workspace>\projects\dust` 上溯 3 级 = `<root>`，再进 `Zephyr_HPMicro`）；
> - `SDK_GLUE_DIR` 默认 `E:/Zephyr_HPMicro/sdk_glue`（可用环境变量覆盖）。
>
> 所以 **`<root>\Zephyr` 和 `<root>\Zephyr_HPMicro` 必须在同一父目录**。

---

## 第 1 步：安装基础工具

与 ST 构建完全相同的四件套：

| 工具 | 版本要求 | 获取方式 |
| --- | --- | --- |
| Git | 任意较新 | https://git-scm.com/download/win |
| Python | 3.10+（推荐 3.12） | https://www.python.org/downloads/（勾选 Add to PATH） |
| CMake | ≥ 3.20 | https://cmake.org/download/（勾选 Add to PATH） |
| Ninja | 任意 | 装完 Python 后 `pip install ninja` |

验证：

```powershell
git --version
python --version
cmake --version
ninja --version
```

> 同样**必须用 Ninja 生成器**，不要用 Visual Studio（见第 10 步问题 2）。

---

## 第 2 步：安装 Python 并建虚拟环境

```powershell
# 创建 venv
python -m venv <workspace>\.venv

# 激活（PowerShell）：
<workspace>\.venv\Scripts\Activate.ps1
# 或（CMD）：
<workspace>\.venv\Scripts\activate.bat
```

激活后提示符前出现 `(.venv)`。**之后所有命令都在这个 venv 里执行。**

---

## 第 3 步：安装 west 与 Zephyr Python 依赖

```powershell
pip install west pyelftools pykwalify packaging jsonschema PyYAML pyserial

west --version        # → West version: v1.5.0
```

---

## 第 4 步：安装 Zephyr SDK（RISC-V 工具链）

HPM 是 **RISC-V** 平台，需要 SDK 里的 **`riscv64-zephyr-elf`** 工具链。

### 4.1 下载 SDK

```text
https://github.com/zephyrproject-rtos/sdk-ng/releases
```

下载 `zephyr-sdk-<版本>-windows_x86_64.zip`（本项目验证 0.16.8；Zephyr v4.3.0 建议 0.17.x）。

### 4.2 解压

```powershell
Expand-Archive .\zephyr-sdk-0.16.8_windows_x86_64.zip -DestinationPath <workspace>\zephyr-sdk-0.16.8
```

### 4.3 确认 RISC-V 工具链

```powershell
ls <workspace>\zephyr-sdk-0.16.8\riscv64-zephyr-elf\bin\riscv64-zephyr-elf-gcc.exe
```

> SDK 里同时有 `arm-zephyr-eabi`（ST 用）和 `riscv64-zephyr-elf`（HPM 用），一份 SDK 两种平台都能编。

### 4.4 （推荐）运行 setup 脚本

```powershell
python <workspace>\zephyr-sdk-0.16.8\setup.cmd
```

---

## 第 5 步：获取 Zephyr 树、项目与自研层

与 ST 文档一致：

```powershell
# 1) 建 workspace 并初始化 west（拉 Zephyr 树）
mkdir <workspace>
cd <workspace>
west init
west update

# 2) 拉取自研层 zephyr_user
git clone <你的 zephyr_user 仓库 URL> zephyr_user

# 3) 拉取项目 dust（含 submodule）
git clone <你的 dust 仓库 URL> projects\dust
cd projects\dust
git submodule update --init --recursive
```

验证：

```powershell
git -C <workspace>\zephyr describe --tags
ls <workspace>\zephyr_user\modules\cmsis        # CMSIS 补丁（ARM 用，HPM 构建无碍）
```

---

## 第 6 步：获取 HPM SDK（sdk_glue / sdk_glue_user / sdk_env）

这是 HPM 构建与 ST 构建**唯一不同的部分**：多一个 `Zephyr_HPMicro` 目录。

### 6.1 三个组件的职责

| 组件 | 内容 | 来源 |
| --- | --- | --- |
| `sdk_glue` | HPMicro 官方 Zephyr SDK glue：官方板卡（hpm6e00evk 等）、SoC、DTS、驱动（PWM/UART/SPI/CAN/CherryUSB）；含 `module.yml`（board_root/dts_root/soc_root） | HPMicro 官方仓库 |
| `sdk_glue_user` | 用户自研：`hpm5361icb` 自研核心板、`HPM5300` SoC、PLLv2 binding | 用户仓库（qingyu0310） |
| `sdk_env` | HPM SDK 源码：`hpm_sdk`（驱动源，如 `hpm_usb_device.c`、`hpm_usb_drv.c`）、工具链、`env.cmd` | HPMicro 官方 SDK |

### 6.2 放置到 `<root>\Zephyr_HPMicro`

```powershell
# 在 <root> 下建目录（与 <workspace> 同级，见第 0 步布局）
mkdir <root>\Zephyr_HPMicro
cd <root>\Zephyr_HPMicro

# 拉取 sdk_glue（HPMicro 官方）
git clone <HPMicro 的 sdk_glue 仓库 URL> sdk_glue

# 拉取 sdk_glue_user（用户自研）
git clone <你的 sdk_glue_user 仓库 URL> sdk_glue_user

# 拉取 sdk_env（HPMicro 官方 SDK）
git clone <HPMicro 的 sdk_env 仓库 URL> sdk_env

# 其它依赖（如 CherryUSB）放 modules\
mkdir modules
git clone <CherryUSB 仓库 URL> modules\lib\CherryUSB   # 按需
```

验证结构：

```powershell
ls <root>\Zephyr_HPMicro
# → sdk_glue  sdk_glue_user  sdk_env  modules
ls <root>\Zephyr_HPMicro\sdk_glue_user\boards\hpmicro\hpm5361icb   # 自研板卡
ls <root>\Zephyr_HPMicro\sdk_env\hpm_sdk                          # HPM SDK 源码
```

---

## 第 7 步：设置环境变量（关键）

### 7.1 必设：SDK_GLUE_DIR（指向 sdk_glue）

```powershell
$env:SDK_GLUE_DIR = "<root>\Zephyr_HPMicro\sdk_glue"
```

> **这一步是 HPM 构建的关键**。项目 `CMakeLists.txt` 逻辑：`if(DEFINED ENV{SDK_GLUE_DIR}) 用之，否则默认 E:/Zephyr_HPMicro/sdk_glue`。
> - 设了：用你的路径（推荐）；
> - 不设：用默认值 `E:/Zephyr_HPMicro/sdk_glue`（如果目录存在也能用）。
> **坑**：如果环境变量指向**不存在的路径**，`ZEPHYR_EXTRA_MODULES` 会报 "not a valid zephyr module"。见第 10 步问题 3。

### 7.2 其余变量（可选但建议）

```powershell
$env:ZEPHYR_BASE = "<workspace>\zephyr"
$env:ZEPHYR_TOOLCHAIN_VARIANT = "zephyr"
$env:ZEPHYR_SDK_INSTALL_DIR = "<workspace>\zephyr-sdk-0.16.8"
```

> 建议写进 `<workspace>\env_hpm.ps1` 每次构建前执行：

```powershell
# env_hpm.ps1
$env:SDK_GLUE_DIR = "<root>\Zephyr_HPMicro\sdk_glue"
$env:ZEPHYR_BASE = "<workspace>\zephyr"
$env:ZEPHYR_TOOLCHAIN_VARIANT = "zephyr"
$env:ZEPHYR_SDK_INSTALL_DIR = "<workspace>\zephyr-sdk-0.16.8"
```

---

## 第 8 步：构建 HPM

### 8.0 前提

```powershell
cd <workspace>\projects\dust
# 激活 venv + 设置 SDK_GLUE_DIR（见第 2、7 步）
west --version
echo $env:SDK_GLUE_DIR        # 应显示你的 sdk_glue 路径
```

### 8.1 自研核心板 hpm5361icb

```powershell
west build -b hpm5361icb -d build/hpm5361icb
```

**说明**：
- `-b hpm5361icb`：自研核心板（HPM5300 系列），板卡定义在 `<root>\Zephyr_HPMicro\sdk_glue_user\boards\hpmicro\hpm5361icb\`，靠项目 `CMakeLists.txt` 的 `BOARD_ROOT`（追加 `sdk_glue`、`sdk_glue_user`、`zephyr_user`）发现；
- **不需要 `-DBOARD_CFG`**：因为项目板级分组目录 `project\boards\hpm\hpm5361icb\` 与板卡同名，`BOARD_CFG` 默认回退为 `hpm5361icb` 就能命中 overlay/conf（这一点和 ST 不同，ST 的分组名 ≠ 板卡名才要显式传）；
- `-d build/hpm5361icb`：独立构建目录，不要复用别的板的。

**预期输出**：

```text
-- Found BOARD.dts: <...hpm5361icb...>.dts
-- Found devicetree overlay: <workspace>/projects/dust/project/boards/hpm/hpm5361icb/hpm5361icb.overlay
-- Found toolchain: zephyr 0.16.8 (...riscv64-zephyr-elf...)
Parsing <workspace>/projects/dust/Kconfig
-- Configuring done
-- Generating done
-- Build files have been written to: <workspace>/projects/dust/build/hpm5361icb
[...] Linking CXX executable zephyr\zephyr_pre0.elf
[...] zephyr.elf
```

### 8.2 官方评估板 hpm6e00evk

```powershell
west build -b hpm6e00evk -d build/hpm6e00evk
```

`hpm6e00evk` 是 HPMicro 官方板，板卡定义在 `<root>\Zephyr_HPMicro\sdk_glue\boards\hpmicro\hpm6e00evk\`。

### 8.3 只配置不编译

```powershell
west build -b hpm5361icb -d build/hpm5361icb -t configure
```

### 8.4 烧录

```powershell
west flash -d build/hpm5361icb
```

板级 `project\boards\hpm\hpm5361icb\board.cmake` 已配置 OpenOCD runner（`adapter speed 500`）；需要 OpenOCD 支持 HPM（见第 10 步问题 6）。

### 8.5 最小构建验证（推荐第一步先跑这个）

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

**为什么**：`TRD_GPIO` 是唯一**不依赖 UART/CAN/SPI/USB/DMA 等外设**的线程（只 GPIO 输出 + 软件定时器）。其它线程各拉一堆底层（UART DMA / SPI / CAN / USB / TFLM 依赖栈），失败时无法区分是**环境问题**还是**功能依赖问题**。只留 TRD_GPIO，编译链路最小化——通，则 Zephyr/SDK/west/Ninja/板级/CMake/HPM 环境全 OK。

**判断标准（三条都满足 = 环境 OK）**：

```text
1. 配置成功：日志出现 -- Configuring done
2. 编译成功：无 Kconfig/CMake/路径/toolchain 错误
3. 链接成功：生成 build\<板卡>\zephyr\zephyr.elf
```

环境跑通后，再逐步打开 `TRD_REMOTE` → `TRD_IMU` → ... 逐个验证功能，出问题能精确定位到那一层。

> **注意**：HPM 最小构建时若出现 `GetDefaultHal()` 链接错误，那是 `hpm5361icb.conf` 里 `CONFIG_COM_USB_HAL_HPM=n` + 开了 USB 的项目配置问题，与构建环境无关（见第 10 步问题 7）。

---

## 第 9 步：验证

```powershell
# 1) 固件产物存在
ls build\hpm5361icb\zephyr\zephyr.elf

# 2) 是 HPM / RISC-V SoC
grep "CONFIG_SOC_HPM\|CONFIG_ARCH_RISCV" build\hpm5361icb\zephyr\.config

# 3) 工具链是 RISC-V
grep "CONFIG_TOOLCHAIN" build\hpm5361icb\zephyr\.config

# 4) 板卡识别正确
grep "CONFIG_BOARD" build\hpm5361icb\zephyr\.config    # → CONFIG_BOARD_HPM5361ICB=y
```

---

## 第 10 步：常见问题

| # | 现象 | 原因 | 解决 |
| --- | --- | --- | --- |
| 1 | `python`/`west` 报错或无输出 | PATH 被商店占位符/坏 shim 占用 | 用 venv 的 python/west；关掉应用执行别名 |
| 2 | `Building for: Visual Studio ...` | CMake 选了 VS 生成器 | 用 `west build`（Ninja） |
| 3 | `not a valid zephyr module`（路径含 HPM） | `SDK_GLUE_DIR` 指向不存在路径，或 HPM 目录缺失 | `echo $env:SDK_GLUE_DIR` 确认指向真实存在的 `<root>\Zephyr_HPMicro\sdk_glue`；确认第 6 步拉全了 |
| 4 | `Error finding board: hpm5361icb` | `BOARD_ROOT` 没含 sdk_glue_user，或板卡目录缺失 | 确认 `CMakeLists.txt` 的 `BOARD_ROOT` 追加了 `SDK_GLUE_DIR`/`SDK_GLUE_USER_DIR`；确认 `<root>\Zephyr_HPMicro\sdk_glue_user\boards\hpmicro\hpm5361icb\` 存在 |
| 5 | 编译报找不到 HPM 驱动源（如 `hpm_usb_device.c`） | `sdk_env` 缺失或路径不对 | 确认 `<root>\Zephyr_HPMicro\sdk_env\hpm_sdk\` 存在；`SDK_GLUE_DIR` 的 `../sdk_env/hpm_sdk` 相对关系正确 |
| 6 | 烧录失败 / OpenOCD 不支持 | 需要支持 HPM 的 OpenOCD | HPMicro 提供 OpenOCD 分支/工具；确认 `board.cmake` 的 `OPENOCD` 路径 |
| 7 | 链接报 `GetDefaultHal()` undefined | 板级 `hpm5361icb.conf` 里 `CONFIG_COM_USB_HAL_HPM=n` 且开了 USB | 这是项目 USB 配置问题，与构建环境无关：确认 `TRD_PC` 关闭或改对 USB HAL 配置 |
| 8 | SDK 版本过低警告 | Zephyr v4.3.0 期望 SDK 0.17.4，装的是 0.16.8 | 通常可编；必要时升级 SDK |
| 9 | `CONFIG_PRJ_TEST` 未生效，7 层没编译 | `prj.conf` 少了 `CONFIG_PRJ_TEST=y` | 确认 `prj.conf` 有它 |

---

## 附录 A：作者电脑的已知环境参考

> 仅作参考，不是安装步骤。作者机器的关键路径：

```text
<root>        : E:\
<workspace>   : E:\Zephyr
Zephyr_HPMicro: E:\Zephyr_HPMicro
    sdk_glue        : E:\Zephyr_HPMicro\sdk_glue
    sdk_glue_user   : E:\Zephyr_HPMicro\sdk_glue_user
    sdk_env         : E:\Zephyr_HPMicro\sdk_env（hpm_sdk 1.11.0）
Python        : 3.12.13（uv 管理）；venv 在 E:\Zephyr\.venv（west 1.5.0）
Zephyr 树     : E:\Zephyr\zephyr（v4.3.0）
SDK          : E:\Zephyr\zephyr-sdk-0.16.8（含 riscv64-zephyr-elf）
项目          : E:\Zephyr\projects\dust
```

作者机器曾有一套指向 `E:\Zephyr_Test` 的**过期环境变量**，HPM 构建前也要清（否则 SDK_GLUE_DIR 指向不存在路径会报错）：

```powershell
Remove-Item Env:SDK_GLUE_DIR,Env:ZEPHYR_BASE,Env:ZEPHYR_SDK_INSTALL_DIR -ErrorAction SilentlyContinue
# 然后重新设 SDK_GLUE_DIR 为真实路径
$env:SDK_GLUE_DIR = "E:\Zephyr_HPMicro\sdk_glue"
```

---

## 附录 B：HPM 相关路径假设

### B.1 项目 CMakeLists 对 HPM 的路径假设

```cmake
# 根 CMakeLists.txt（节选）
if(DEFINED ENV{SDK_GLUE_DIR})  set(SDK_GLUE_DIR "$ENV{SDK_GLUE_DIR}")
else()                          set(SDK_GLUE_DIR "E:/Zephyr_HPMicro/sdk_glue")   # 默认
endif()

set(SDK_GLUE_USER_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../Zephyr_HPMicro/sdk_glue_user")
#               ↑ 从 <workspace>\projects\dust 上溯 3 级 = <root>，再进 Zephyr_HPMicro

list(APPEND BOARD_ROOT "${SDK_GLUE_DIR}" "${SDK_GLUE_USER_DIR}" "${ZEPHYR_USER_DIR}")
list(APPEND SOC_ROOT   "${SDK_GLUE_DIR}" "${SDK_GLUE_USER_DIR}")
list(APPEND DTS_ROOT   "${SDK_GLUE_DIR}" ... "${SDK_GLUE_USER_DIR}" ...)

set(ZEPHYR_EXTRA_MODULES
  "${SDK_GLUE_DIR}"                              # HPM module（board/dts/soc root）
  "${SDK_GLUE_DIR}/../modules/lib/CherryUSB"     # CherryUSB
  "${ZEPHYR_USER_DIR}/modules/usb"
  "${ZEPHYR_USER_DIR}/modules/cmsis")
```

### B.2 布局结论

```text
<root>\                       （E:\ 之类）
├── Zephyr\                    ← workspace（zephyr/zephyr_user/projects/dust/.venv）
└── Zephyr_HPMicro\            ← SDK_GLUE_DIR 的父目录（sdk_glue/sdk_glue_user/sdk_env/modules）
```

`SDK_GLUE_DIR` 指向 `<root>\Zephyr_HPMicro\sdk_glue`；`sdk_env`（hpm_sdk）通过 `SDK_GLUE_DIR/../sdk_env/hpm_sdk` 相对引用（如 usb HAL 的 CMake）。所以三件套必须都放在 `<root>\Zephyr_HPMicro\` 下，层级别改。

### B.3 与 ST 构建的对应

| 项 | ST 构建（不带 HPM） | HPM 构建（带 HPM） |
| --- | --- | --- |
| HPM SDK | 不需要 | 需要 `<root>\Zephyr_HPMicro` |
| `SDK_GLUE_DIR` | **不要设** | **必须设**（指向 sdk_glue） |
| 板卡 | `stm32f4_disco` / `stm32f407igh6` | `hpm5361icb` / `hpm6e00evk` |
| 工具链 | `arm-zephyr-eabi` | `riscv64-zephyr-elf` |
| 板级分组 | 需显式 `-DBOARD_CFG` | 目录与板卡同名，无需传 |
