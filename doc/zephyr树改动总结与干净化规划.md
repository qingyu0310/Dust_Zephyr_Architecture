# Zephyr 树改动总结与干净化规划

> 本文基于当前工作区事实整理，覆盖 **三个仓库**：
>
> | 仓库 | 路径 | 角色 |
> | --- | --- | --- |
> | zephyr | `D:\Zephyr\zephyr` | 官方 v4.3.0，唯一内核树 |
> | sdk_glue | `D:\Zephyr_HPMicro\sdk_glue` | HPMicro Zephyr 适配 module |
> | sdk_env | `D:\Zephyr_HPMicro\sdk_env` | HPMicro 原始 SDK（含 hpm_sdk + 工具链） |
>
> 目的：
> 1. 全面盘点「zephyr 生态」被改动了什么（含真实逻辑改动与 CRLF 噪声）；
> 2. 说明 HPM SDK 是「外部模块」而非「接入树」的现状；
> 3. 规划如何让整个环境保持干净、可复现。

---

## 0. 重要发现：很多 git 改动是「假改动」

在盘点之前必须先说明一个关键事实：**几个仓库里出现的大量 `M` 状态是 CRLF 行尾符差异，不是内容改动。**

用 `git diff -w`（忽略空白）验证后确认：

| 文件 | git status | 忽略空白后 | 结论 |
| --- | --- | --- | --- |
| `sdk_glue/drivers/pinctrl/pinctrl_hpmicro.c` | M | 无 diff | **纯 CRLF 噪声** |
| `sdk_env/hpm_sdk/components/usb/device/hpm_usb_device.c` | M | 无 diff | **纯 CRLF 噪声** |
| `sdk_env/tools/openocd/tcl/...`（125 个） | M | 无 diff | **纯 CRLF 噪声**（openocd 换新时批量引入） |

**这意味着本地的行尾符配置（core.autocrlf）和这些仓库的原始行尾符不一致，导致 git 把「换行符」当成了「内容改动」。** 真实逻辑改动远少于 `git status` 显示的数量。

> ⚠️ 这些 CRLF 噪声如果被 `git add -A` 提交，会造成 100+ 文件的虚假 diff，污染提交历史。**干净化第一步就是处理这个。**

---

## 1. Zephyr 树改动（`D:\Zephyr\zephyr`）

**版本：** v4.3.0（detached），无本地 commit，改动全在未提交工作区。

### 1.1 已修改官方文件（2 个）

#### ① `drivers/interrupt_controller/intc_plic.c`（+11 / -1）— 唯一的内核逻辑改动

`plic_irq_handler()` 中，当 PLIC claim 回来的 IRQ 在 `_sw_isr_table` 里未注册（`ite->isr == z_irq_spurious`）时：

```c
if (ite->isr == z_irq_spurious) {
#ifdef CONFIG_PLIC_SUPPORTS_TRIG_EDGE
    if (trig_val == PLIC_TRIG_LEVEL) {
        sys_write32(local_irq, claim_complete_addr);
    }
#else
    sys_write32(local_irq, claim_complete_addr);
#endif
    return;          // 完成中断应答后直接返回，不调用 z_irq_spurious
}
```

**作用：** 未注册中断被安全吸收（写 claim_complete 完成应答），不再调用 `z_irq_spurious`（官方逻辑下该函数不返回 → 死机）。与 HPM USB 枚举时的 PLIC 中断问题相关。

**归属：** 这是 Zephyr 通用 RISC-V 驱动，不在 sdk_glue 范围内，**必须保留为树级补丁或提 upstream**。

#### ② `subsys/usb/device_next/app/cdc_acm_serial.c`（+1）

只加了一行 `#include <zephyr/sys/printk.h>`，全文无 `printk(` 调用——**调试残留，建议删除**。

### 1.2 未跟踪新增（3 处）

| 路径 | 内容 | 归属 |
| --- | --- | --- |
| `boards/st/stm32f407igh6/` | 自定义 STM32F407IGH6 板卡（dts/pinctrl/defconfig/board.yml） | **业务板卡，移出** |
| `dts/bindings/mtd/winbond,w25q128.yaml` | W25Q128 SPI NOR Flash binding | **业务 binding，移出** |
| `doc/zmy_note/` | 个人学习笔记（step1/step2） | **个人文档，移出** |

---

## 2. sdk_glue 改动（`D:\Zephyr_HPMicro\sdk_glue`）

**HPM Zephyr 适配层**，独立 git 仓库（`zephyr/module.yml` 声明 board_root/soc_root/dts_root）。**所有改动未提交。**

### 2.1 真实逻辑改动

| 文件 | 改动量 | 内容 |
| --- | --- | --- |
| `drivers/pwm/pwm_hpmicro.c` | **~200 行大改** | PWMv2 多通道共存修复：新增 `configured_channels` 位图追踪已配置通道、`set_cmp_shadow_sel()` 封装 CMP shadow 选择、去掉重复的 `pwm_setup_waveform`/`pwm_config_cmp` 配置、修 channel→CMP 索引映射、统一缩进 |
| `drivers/usb/udc/udc_hpmicro.c` | **~217 行大改** | USB device controller 驱动：新增 `USB_HPM_DCD_DATA_SECTION`（DCD 数据结构放 `AHB_SRAM.usb_dcd` 段）、`udc_hpm_sys_addr()`（DLM↔系统地址转换）、`USB_HPM_TRACE` 调试宏、`udc_hpm_dump_regs`/`udc_hpm_dump_ep0_qhd` 寄存器/EP0 QHD dump、`udc_hpm_reinit_ctrl_ep()` 重建控制端点、`lock`/`unlock` 返回值 void、对齐新版 UDC API（`udc_ep_set_busy(cfg)` 等） |
| `drivers/serial/uart_hpmicro.c` | ~10 行 | HPM5361 硬件 RX idle：新增 `UART_HPM_USE_HW_RX_IDLE` 选择宏、`rxidle_config` 配置（detect_enable/irq_enable/条件/阈值）、ISR 处理 `uart_is_rxline_idle`、parity 配置修复（`UART_CFG_PARITY_*` 原 SDK 只解析不赋值）、异步 RX 超时 workqueue |
| `drivers/usb/cherryusb/CMakeLists.txt` | 6 行 | 用 `if(CONFIG_CHERRYUSB_DEVICE)` 包裹整个库，源文件改为 `zephyr_library_sources_ifdef`，新增 `include_directories(../modules/lib/CherryUSB/port/hpmicro)` |
| `drivers/usb/cherryusb/cherryusb_hpmicro.c` | +1 | 加 `printk.h` include（调试残留，同 cdc_acm） |
| `drivers/usb/udc/Kconfig.hpmicro` | 4 行 | `select UDC_DRIVER`、`select NOCACHE_MEMORY if ARCH_HAS_NOCACHE_MEMORY_SUPPORT` |
| `drivers/pwm/Kconfig.hpmicro` | 1 行 | `select PWM` |
| `drivers/spi/spi_hpmicro.c` | 1 行 | `CONFIG_SPI_INTERRUPT_DRIVEN 1 → 0`（关闭 SPI 中断模式） |
| `dts/riscv/hpmicro/hpm53xx.dtsi` | +8 | 新增 `pwm1: pwm@f031c000` 节点（hpm-pwm compatible，PWM 双通道） |
| `boards/hpmicro/hpm5361icb/hpm5361icb-pinctrl.dtsi` | +140 | 新增 12 组 pinmux：`gpiob_spi`、`spi1`、`spi2_local`、`uart3`、`pwm0_p7`、`pwm0_p5`、`pwm0_p57`、`pwm1_p2`、`pwm1_p3`、`pwm1_p23`、`gpioy`、`uart1` |

### 2.2 删除（清理备份）

- `boards/hpmicro/hpm5361icb/hpm5361icb-pinctrl.dtsi.codex_backup_20260520_223323` — codex 备份残留，删除
- `boards_backup/hpm5361icb.ai_backup_20260520/` 整个目录 — AI 备份残留，删除

### 2.3 CRLF 噪声（非内容改动）

- `drivers/pinctrl/pinctrl_hpmicro.c` — 纯行尾符

---

## 3. sdk_env 改动（`D:\Zephyr_HPMicro\sdk_env`）

**HPMicro 原始 SDK**，git 仓库根为 `D:/Zephyr_HPMicro/sdk_env`，`hpm_sdk/` 是其子目录。

### 3.1 真实逻辑改动

| 文件 | 改动 | 内容 |
| --- | --- | --- |
| `hpm_sdk/soc/HPM5300/HPM5361/hpm_misc.h` | +9 | **DLM/ILM 地址转换修复**：`core_local_mem_to_sys_address()` 增加 `ADDRESS_IN_DLM → DLM_TO_SYSTEM`、`ADDRESS_IN_ILM → ILM_TO_SYSTEM`；`sys_address_to_core_local_mem()` 增加 `ADDRESS_IN_CORE0_DLM_SYSTEM → SYSTEM_TO_DLM`。对应 USB DCD 放 AHB_SRAM 后 DMA 地址正确 |
| `hpm_sdk/drivers/src/hpm_usb_drv.c` | +1 | 仅加一个空行（**接近噪声**） |

### 3.2 工具链替换（非源码逻辑）

- **`tools/openocd/` 被整体替换**：新增 36 个文件 + 修改 125 个文件（多为 CRLF 噪声）。`sdk_env/tools/` 下现存 `openocd/`（新）、`openocd_new/`（解压来源）、`openocd_backup/`（旧版备份）、`openocd-windows-i686.zip`（压缩包）——openocd 升级过程留下的中间产物
- `hpm_sdk/boards/openocd/interface/cmsis-dap.cfg` — 新增调试配置

### 3.3 CRLF 噪声

- `hpm_sdk/components/usb/device/hpm_usb_device.c` — 纯行尾符
- `tools/openocd/tcl/...`（125 个 M）— 纯行尾符

---

## 4. 改动全景图

```text
zephyr 树 (v4.3.0)
├── intc_plic.c               ← 真逻辑改动（PLIC spurious 吸收）
├── cdc_acm_serial.c          ← 调试残留（printk include）
├── boards/st/stm32f407igh6/  ← 业务板卡（放错位置）
├── dts/bindings/winbond,w25q128.yaml ← 业务 binding（放错位置）
└── doc/zmy_note/             ← 个人笔记（放错位置）

sdk_glue (HPM 适配层)
├── drivers/pwm/pwm_hpmicro.c ← 真逻辑大改（PWMv2 多通道共存修复）
├── drivers/usb/udc/udc_hpmicro.c ← 真逻辑大改（DCD/地址转换/调试）
├── drivers/serial/uart_hpmicro.c ← 真逻辑（硬件 RX idle + parity 修复）
├── drivers/usb/cherryusb/*   ← 真逻辑（Kconfig 条件编译）+ printk 残留
├── drivers/spi/spi_hpmicro.c ← 真逻辑（关中断模式）
├── drivers/pwm|usb/udc Kconfig ← select 补齐
├── dts/riscv/hpmicro/hpm53xx.dtsi ← +pwm1 节点
├── boards/.../hpm5361icb-pinctrl.dtsi ← +12 组 pinmux
├── 删除：codex 备份、boards_backup
└── pinctrl_hpmicro.c         ← CRLF 噪声

sdk_env (HPM 原始 SDK)
├── hpm_sdk/.../hpm_misc.h    ← 真逻辑（DLM/ILM 地址转换）
├── tools/openocd/            ← 整体替换 + CRLF 噪声
├── openocd_new/ openocd_backup/ zip ← 升级中间产物
└── hpm_usb_device.c          ← CRLF 噪声
```

---

## 5. HPM SDK 接入方式分析

### 5.1 当前形态：HPM 支持是「外部模块」，不是「接入树」

```
zephyr 树（官方 v4.3.0，唯一内核树）
   │  ZEPHYR_EXTRA_MODULES / BOARD_ROOT / SOC_ROOT / DTS_ROOT
   ▼
sdk_glue（HPM Zephyr 适配 module，独立仓库）
   │  引用 ../sdk_env/hpm_sdk
   ▼
hpm_sdk（HPMicro 原始 SDK + 工具链）
```

- **sdk_glue** 通过 `zephyr/module.yml` 声明为 Zephyr module（board_root/dts_root/snippet_root/soc_root/module_ext_root 全部指向自身），提供 HPM 的板级/SOC/DTS/驱动。
- **hpm_sdk** 被 sdk_glue 的 CMakeLists 以相对路径引用（`${HPM_ZEPHYR_DIR}/../sdk_env/hpm_sdk`）。
- 接入入口在 tflm 根 CMakeLists：`ZEPHYR_EXTRA_MODULES` + `BOARD_ROOT`/`SOC_ROOT`/`DTS_ROOT` 指向 sdk_glue。

### 5.2 两种做法的对比

| 做法 | 形态 | 后果 |
| --- | --- | --- |
| **A. 外部模块（当前）** | sdk_glue + hpm_sdk 放树外，module 机制加载 | 树干净；可独立升级；需 west manifest 收敛版本 |
| **B. 合入 zephyr 树**（fork） | 把 board/soc/dts 拷进树 | 树污染；升级冲突；**不推荐** |

**推荐 A，并升级为 west manifest 管理**——sdk_glue、hpm_sdk、CherryUSB、user/usb 全部声明进业务仓库 `west.yml`，由 west 固定版本和路径。

---

## 6. 干净化规划

### 6.1 目标状态

```text
zephyr 树          = 纯官方 v4.3.0（或仅受控 patch）
sdk_glue + hpm_sdk = 树外独立仓库，west manifest 固定版本
tflm               = 独立框架 module
业务仓库           = boards/、app/、thread/、scripts/
```

### 6.2 逐项处理建议

#### zephyr 树
| 改动 | 建议 |
| --- | --- |
| `intc_plic.c` | 保存为独立 patch（`tflm/zephyr-patches/`），工作区还原，需要时 `git apply`；尝试提 upstream |
| `cdc_acm_serial.c` | 删除 printk include |
| `stm32f407igh6` 板卡 | 移入 tflm `project/boards/st/` 或业务仓库 `boards/` |
| `winbond,w25q128.yaml` | 移入 tflm `dts/bindings/mtd/` |
| `doc/zmy_note/` | 移到个人目录 |

#### sdk_glue
| 改动 | 建议 |
| --- | --- |
| `pwm_hpmicro.c` / `udc_hpmicro.c` / `uart_hpmicro.c` / `spi_hpmicro.c` / cherryusb / Kconfig / dtsi | **这些是真实需要的功能改动**，保留，但要 commit 到 sdk_glue 仓库形成版本记录（否则无 git 痕迹，靠人脑记忆） |
| `cherryusb_hpmicro.c` printk include | 确认是否为调试残留，是则删除 |
| `pinctrl_hpmicro.c` | 修行尾符后还原，避免噪声进提交 |
| codex 备份 / boards_backup | 已删，确认 |

#### sdk_env
| 改动 | 建议 |
| --- | --- |
| `hpm_misc.h` DLM/ILM 转换 | **真实功能改动，保留并 commit** |
| `tools/openocd/` | 决定 openocd 版本，删掉 `openocd_new/`、`openocd_backup/`、zip 中间产物；修行尾符 |
| CRLF 噪声文件 | 配置仓库 `core.autocrlf` / `.gitattributes` 统一行尾符后再提交 |

### 6.3 分步执行计划

**阶段 0：盘点（已完成）** — 本文件即盘点结果。

**阶段 1：修行尾符（高优先，先做）**
- [ ] 三个仓库配置 `.gitattributes`（`* text=auto` + 强制 `eol=lf` 对 C/C++/py/dts）
- [ ] 用 `git add --renormalize` 归一化，让 CRLF 噪声从 git 视野消失
- [ ] 验证 `git status` 只剩真实改动

**阶段 2：真实改动 commit 固化**
- [ ] sdk_glue：把 pwm/udc/uart/spi/cherryusb/Kconfig/dtsi 的改动 commit（这是你的实际成果，不能丢）
- [ ] sdk_env：commit `hpm_misc.h`；openocd 定版后 commit
- [ ] zephyr 树：intc_plic 提成 patch 文件或 commit 到独立分支

**阶段 3：清理放错位置的文件**
- [ ] stm32f407igh6 板卡 → 业务侧
- [ ] w25q128 binding → tflm dts
- [ ] zmy_note → 个人目录
- [ ] cdc_acm_serial printk → 删
- [ ] cherryusb printk → 确认删

**阶段 4：接入收敛到 west**
- [ ] 建立 `west.yml` 声明 zephyr/sdk_glue/sdk_env/cherryusb/user-usb 版本
- [ ] 根 CMakeLists 去掉绝对路径，改用 west module 发现
- [ ] `SDK_GLUE_DIR`/`ZEPHYR_SDK_INSTALL_DIR` 改环境变量/默认覆盖

**阶段 5：验证**
- [ ] `west build -b hpm5361icb -p` 与 `west build -b stm32f407igh6 -p` 均通过
- [ ] 三个仓库 `git status` 干净（仅受控内容）
- [ ] 更新 `temp/doc/env_changes.md`

### 6.4 长期原则

1. **zephyr 树只允许官方 commit + 受控 patch**，其余一律外置。
2. **HPM 支持永远在树外**：sdk_glue 是 module，hpm_sdk 被 module 引用，west 固定版本。
3. **板卡/binding/snippet 归属业务**，通过 BOARD_ROOT/DTS_ROOT 提供。
4. **版本管理用 west，不用绝对路径**。
5. **统一行尾符**，避免 CRLF 噪声污染 git。
6. **真实改动必须 commit**——没有 git 痕迹的功能改动，等于没记录。

---

## 7. 附录：关键路径速查

| 实体 | 路径 | 角色 |
| --- | --- | --- |
| Zephyr 树 | `D:\Zephyr\zephyr` | 官方 v4.3.0 |
| sdk_glue | `D:\Zephyr_HPMicro\sdk_glue` | HPM Zephyr 适配 module |
| sdk_env | `D:\Zephyr_HPMicro\sdk_env` | HPM 原始 SDK + 工具链（仓库根） |
| hpm_sdk | `sdk_env\hpm_sdk` | HPM SDK 源码子目录 |
| openocd | `sdk_env\tools\openocd` | 调试工具（已替换） |
| tflm | `D:\Zephyr\projects\tflm` | 框架 + 参考业务 |
| 环境降级记录 | `temp/doc/env_changes.md` | v4.4→v4.3 / SDK 降级 |
| 板卡移植记录 | `temp/doc/hpm5361icb_porting_record.md` | HPM5361 自制板 |
| USB 枚举修复 | `doc/usb_enumeration_fix_record.md` | PLIC/DCD/SET_ADDRESS |
| PWM 双通道修复 | `doc/pwm_hpmicro_driver_changes.md` | PWMv2 多通道共存 |
