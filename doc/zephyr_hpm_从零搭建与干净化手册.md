# Zephyr + HPM SDK 从零搭建与干净化手册（基于 v4.3）

> 目标：**从零开始，搭建一套能编译 + 烧录的 Zephyr + HPM5361 + STM32F407 工程，且 zephyr 树保持「可控」——官方内容一个不改，自维护内容（板卡/binding/补丁）留在树里并显式标注。**
>
> **版本结论先行：定格 Zephyr v4.3.0。** 这不是随意选择，而是经过完整分析后的唯一合理答案（第 2 章详细论证）。
>
> 本文档覆盖：
> 1. 完整盘点当前三个仓库（zephyr / sdk_glue / sdk_env）被改动了什么；
> 2. 版本选型论证（为什么是 v4.3，为什么不能 v3.7 / v4.4）；
> 3. 参考 Zephyr 官网 [Getting Started Guide](https://docs.zephyrproject.org/latest/develop/getting_started/index.html) 的从零搭建流程（针对本工程定制）；
> 4. 板卡/binding 归属原则（留在树中 + 显式标注）；
> 5. 干净化分步规划与验收清单。
>
> 本文档从头到尾写，冗余优先，宁可啰嗦不可省略。

---

## 目录

- [第 0 章 最终结论：先看这个](#第-0-章-最终结论先看这个)
- [第 1 章 架构总览：谁负责什么](#第-1-章-架构总览谁负责什么)
- [第 2 章 版本选型：为什么是 v4.3（重点）](#第-2-章-版本选型为什么是-v43重点)
- [第 3 章 三个仓库改动全景盘点](#第-3-章-三个仓库改动全景盘点)
- [第 4 章 版本带来的固有问题与对策](#第-4-章-版本带来的固有问题与对策)
- [第 5 章 从零搭建完整流程（参考官网）](#第-5-章-从零搭建完整流程参考官网)
- [第 6 章 板卡/binding 归属：留在树中，显式标注](#第-6-章-板卡binding-归属留在树中显式标注)
- [第 7 章 内核补丁处理：intc_plic](#第-7-章-内核补丁处理-intc_plic)
- [第 8 章 自维护层标注规范](#第-8-章-自维护层标注规范)
- [第 9 章 干净化分步规划](#第-9-章-干净化分步规划)
- [第 10 章 验收清单](#第-10-章-验收清单)
- [附录 A 官网 Getting Started 速查（原版）](#附录-a-官网-getting-started-速查原版)
- [附录 B 本工程从零到烧录速查](#附录-b-本工程从零到烧录速查)
- [附录 C 关键路径速查](#附录-c-关键路径速查)
- [附录 D 一句话总结](#附录-d-一句话总结)

---

## 第 0 章 最终结论：先看这个

### 0.1 一句话

> **定格 Zephyr v4.3.0。zephyr 树 = 官方内容（不改）+ 显式标注的自维护内容（板卡/binding/补丁）。HPM 适配住在 sdk_glue / hpm_sdk。业务逻辑住在 tflm。`west build -b hpm5361icb -p` + `west flash` 能跑通，这套架构就立住了。**

### 0.2 版本定格（为什么不是别的）

| 候选 | 结论 | 一句话原因 |
| --- | --- | --- |
| **v4.3.0** | ✅ **采用** | 唯一能编译你现有 sdk_glue 的版本；已跑通双板 |
| v3.7.0 LTS | ❌ 回不去 | sdk_glue 官方虽绑 v3.7，但你的 sdk_glue USB 驱动已用 v4.x 新 API，v3.7 编译不过 |
| v4.4.0 | ❌ 已排除 | CMSIS Kconfig 冲突 + 模块加载时序问题（你从 v4.4 降到 v4.3 的原因） |

详细论证见第 2 章。

### 0.3 三个仓库各自的定位

| 仓库 | 角色 | 规则 |
| --- | --- | --- |
| zephyr（`D:\Zephyr\zephyr`） | 官方内核树 | 官方内容不改；自维护内容（板卡/binding/补丁）留在树里 + `SELF-MAINTAINED` 标注 |
| sdk_glue（`D:\Zephyr_HPMicro\sdk_glue`） | HPM ⇄ Zephyr 适配层 | 所有 HPM 平台 soc/dts/driver 适配住这里，标注 + commit |
| sdk_env（`D:\Zephyr_HPMicro\sdk_env`） | HPM 原厂 SDK | 只读；唯一例外 hpm_misc.h（带标注） |
| tflm（`D:\Zephyr\projects\tflm`） | 框架 + 参考业务 | 业务线程/算法/topic/脚本；**不放板卡/binding** |

---

## 第 1 章 架构总览：谁负责什么

### 1.1 四层职责划分

```text
┌─────────────────────────────────────────────────────────┐
│ ① zephyr 树（D:\Zephyr\zephyr）                          │
│    角色：官方源（RTOS / 内核 / 官方驱动 / 构建系统）         │
│         + 自维护内容（板卡 / binding / 内核补丁）           │
│    规则：git checkout v4.3.0 后，官方内容一个都不准改；      │
│          需要放进树里的东西（板卡/binding/补丁）必须         │
│          显式标注 SELF-MAINTAINED（见第 8 章）            │
│    判定：这个文件是「官方原有」还是「我加的」？             │
│          我加的 → 必须标注，否则不允许留在树里             │
└─────────────────────────────────────────────────────────┘
        ▲ 只被上层使用，官方内容不被上层修改
        │
┌─────────────────────────────────────────────────────────┐
│ ② hpm_sdk（D:\Zephyr_HPMicro\sdk_env\hpm_sdk）           │
│    角色：HPMicro 原厂 SDK（芯片寄存器/外设驱动/中间件）     │
│    规则：原则上只读；极少数芯片适配改动要显式标注 HPM 自维护  │
│          当前唯一例外：hpm_misc.h 的 DLM/ILM 地址转换      │
└─────────────────────────────────────────────────────────┘
        ▲ 只被 sdk_glue 引用
        │
┌─────────────────────────────────────────────────────────┐
│ ③ sdk_glue（D:\Zephyr_HPMicro\sdk_glue）                 │
│    角色：HPM ⇄ Zephyr 的桥（board/soc/dts/driver 适配）    │
│    规则：这是「自维护层」，所有 HPM 平台改动必须住在这里     │
│          并且文件头显式标注「HPM self-maintained layer」   │
└─────────────────────────────────────────────────────────┘
        ▲ 被应用工程通过 module 机制引用
        │
┌─────────────────────────────────────────────────────────┐
│ ④ 应用工程（D:\Zephyr\projects\tflm + 业务仓库）          │
│    角色：框架（tflm）+ 业务（线程/算法/topic/脚本）          │
│    规则：业务线程/算法/topic/脚本在这层；                   │
│          板卡/binding 不进这层（它们属于树，见第 6 章）   │
└─────────────────────────────────────────────────────────┘
```

### 1.2 铁律

1. **zephyr 树只允许两种内容**：官方 commit + 显式标注的自维护内容（板卡/binding/内核补丁）。任何「我加的」内容都必须标注 `SELF-MAINTAINED`，否则不允许留在树里。
2. **适配只能住在适配层**：HPM 平台的 soc/dts/driver 适配改动，一律进 sdk_glue 或 hpm_sdk（同样显式标注）。
3. **业务逻辑住在业务层**：业务线程/算法/topic/脚本进 tflm/业务仓库。**但板卡和 binding 不是业务逻辑**——板卡/binding 本来就是树的职责，留在树中、标注清楚即可。
4. **显式标注**：凡是自维护层里「偏离原厂」的代码，文件头必须写清楚 `SELF-MAINTAINED` 和原因（第 8 章）。
5. **能编译、能烧录才算数**：规划写得再好，`west build` 不过就是零。

### 1.3 判断口诀：这个内容属于哪一类

```text
这个内容属于哪一类？
  ├─ 平台/板的物理描述（板卡 dts、binding、芯片寄存器库）
  │     → 放树里 / sdk_glue / hpm_sdk，但【必须显式标注 SELF-MAINTAINED】
  ├─ 内核驱动补丁（intc_plic 这类 module 覆盖不了的）
  │     → zephyr 树受控补丁（patch 文件归档 + 能 apply/能 revert）
  ├─ HPM 平台适配（soc/dts/driver）
  │     → sdk_glue（自维护层，标注）
  └─ 业务逻辑（线程/算法/topic/脚本）
        → tflm / 业务仓库

关键判断：这个文件是「官方原有」还是「我加的」？
  官方原有 → 一个字节都不改
  我加的   → 必须在树里，且必须标注，两者缺一不可
```

---

## 第 2 章 版本选型：为什么是 v4.3（重点）

> 这一章是整个手册的决策依据。回答三个问题：为什么不是官方推荐的 v3.7 LTS？为什么不是更新的 v4.4？为什么最终是 v4.3？

### 2.1 背景：sdk_glue 官方绑定的是 v3.7.0 LTS

HPMicro 的 sdk_glue 官方 README 明确声明：

> **This repository is bound to Zephyr v3.7.0 (LTS)** and undergoes related iterations on this version basis.

sdk_glue 的 west.yml 头部也写死：

```text
##  zephyr    v3.7.0  ##
##  hpm_sdk   v1.11.0 ##
```

这是一个「绑定 LTS、深度适配、不追新」的厂商策略。对 HPMicro 来说这是合理选择（LTS 稳定、支持到 2029），**不是 sdk_glue 太低级**——sdk_glue v0.7.0 发布于 2026-01-30，很新，且支持 hpm6e00evk/hpm6200evk 等新板卡。

### 2.2 但你当前实际用的是 v4.3.0

你的 zephyr 树 HEAD 是 `v4.3.0`（从 v4.4.0 降级而来，原因见 2.4）。**也就是说：你在用 sdk_glue 官方不支持的更高版本。**

### 2.3 为什么不能回到 v3.7.0 LTS（决定性证据）

**因为你的 sdk_glue 代码已经被改成了 v4.x API，v3.7 编译不过。**

关键证据：sdk_glue 的 USB 驱动 `drivers/usb/udc/udc_hpmicro.c` 使用了 **UDC 辅助函数的新签名**：

```c
// sdk_glue udc_hpmicro.c 当前代码（v4.x 签名）：
if (!udc_ep_is_busy(cfg)) {        // 参数是 cfg（struct udc_ep_config*）
    udc_ep_set_busy(cfg, true);    // 参数是 cfg
}
```

对比 Zephyr 各版本的 `udc_common.h` 签名：

| Zephyr 版本 | `udc_ep_is_busy` 签名 | `udc_ep_set_busy` 签名 |
| --- | --- | --- |
| **v3.7.0** | `udc_ep_is_busy(const struct device *dev, const uint8_t ep)` | `udc_ep_set_busy(const struct device *dev, const uint8_t ep, const bool busy)` |
| **v4.3.0** | `udc_ep_is_busy(const struct udc_ep_config *const ep_cfg)` | `udc_ep_set_busy(struct udc_ep_config *const ep_cfg, const bool busy)` |
| **v4.4.0** | 同 v4.3（`cfg` 签名） | 同 v4.3（`cfg` 签名） |

**结论：** sdk_glue 的 udc_hpmicro.c 用的是 v4.x 的 `cfg` 签名。**回到 v3.7，这个文件直接编译失败**（参数类型不匹配：v3.7 要 `dev, ep`，你的代码传的是 `cfg`）。

也就是说，sdk_glue 在你手里已经被 v4.x 化了，v3.7 已经回不去了——除非重写 USB 驱动。

### 2.4 为什么不能用 v4.4.0

你从 v4.4 降到 v4.3 是有明确原因的（`temp/doc/env_changes.md` / `temp/doc/zephyr_4.4_issues.md` 记录）：

**问题 A — CMSIS 模块 Kconfig 冲突：**
- v4.4 新增 `modules/cmsis_6/` 但仍保留 `modules/cmsis/`，两个 Kconfig 都定义 `HAS_CMSIS_CORE`；
- 通过 `modules/Kconfig` 第 136 行的 `if 0` 回退块加载，导致符号依赖为 `n` 却被其他模块 `select y`；
- Kconfig 报 warning，Zephyr 把 warning 当 error，阻断编译。

**问题 B — 模块 CMakeLists 与 Kconfig 加载时序：**
- 外部模块的 CMakeLists 在 Kconfig 之前加载（步骤 1），此时 `CONFIG_*` 全为空；
- `hal_stm32/CMakeLists.txt` 的 `add_subdirectory_ifdef(CONFIG_HAS_STM32CUBE stm32cube)` 永假 → STM32Cube HAL 源文件不参与编译；
- 标准 west workspace 有 pre-cache 机制掩盖，out-of-tree 工程暴露。

这两个问题在 v4.4 都存在，所以 v4.4 排除。

### 2.5 为什么 v4.3 是唯一合理答案

综合推理：

```text
你的 sdk_glue USB 驱动已 v4.x 化（cfg 签名）
        │
        ├─ v3.7：❌ UDC 签名不匹配，编译不过
        ├─ v4.4：❌ CMSIS Kconfig 冲突 + 模块时序
        └─ v4.3：✅ UDC 签名匹配（v4.3 = v4.4 的 cfg 签名）
                  ✅ 无 v4.4 的 Kconfig 冲突问题（v4.3 没有 cmsis_6 冲突的严重程度）
                  ✅ 已在你机器上跑通双板
```

**v4.3 是唯一能编译你现有 sdk_glue 代码、且避开了 v4.4 两个大坑的版本。**

### 2.6 三版本完整对比表

| 维度 | **v3.7.0 LTS** | **v4.3.0** | **v4.4.0** |
| --- | --- | --- | --- |
| sdk_glue 官方绑定 | ✅ | ❌ | ❌ |
| CMSIS Cortex-M 支持 | ✅ 原生（cmsis_core.h 有 M 分支） | ❌ 空 stub（cmsis_6 分离方案） | ❌ + Kconfig 冲突 |
| SHPR/SHP 命名断裂 | ✅ 无（scb.c 无此代码） | ❌ 有 | ❌ 有 |
| **你的 sdk_glue UDC 驱动** | **❌ 编译不过（cfg 签名）** | ✅ | ✅ |
| CMSIS Kconfig 冲突 | ✅ 无 | ⚠️ 需应用侧预定义 `HAS_CMSIS_CORE` | ❌ 严重 |
| 模块加载时序 | ✅ 正常 | ⚠️ 影响小，可绕 | ❌ 严重 |
| 你的 cmsis_core.h 补丁 | 不需要 | 需要（接受并管理） | 需要 |
| **结论** | ❌ 回不去 | ✅ **采用** | ❌ 已排除 |

### 2.7 结论

> **定格 v4.3.0。** 不是因为 v4.3 完美，而是因为它是唯一「能编译你的 sdk_glue + 避开 v4.4 大坑」的版本。v4.3 的 CMSIS 空 stub 和 SHPR/SHP 断裂是**固有问题**，需要接受并管理（第 4 章），但这是可控的、有补丁的。

---

## 第 3 章 三个仓库改动全景盘点

> 完整盘点当前工作区三个仓库被改动的内容。**重要：很多 git 改动是 CRLF 行尾符差异（假改动），不是内容改动。**

### 3.0 先说 CRLF 假改动问题

用 `git diff -w`（忽略空白）验证后确认：

| 文件 | git status | 忽略空白后 | 结论 |
| --- | --- | --- | --- |
| `sdk_glue/drivers/pinctrl/pinctrl_hpmicro.c` | M | 无 diff | **纯 CRLF 噪声** |
| `sdk_env/hpm_sdk/components/usb/device/hpm_usb_device.c` | M | 无 diff | **纯 CRLF 噪声** |
| `sdk_env/tools/openocd/tcl/...`（125 个） | M | 无 diff | **纯 CRLF 噪声**（openocd 换新时批量引入） |

**这意味着本地的行尾符配置（core.autocrlf）和这些仓库的原始行尾符不一致。** 如果 `git add -A` 提交，会产生 100+ 文件的虚假 diff，污染提交历史。**干净化第一步就是处理这个**（第 9 章阶段 1）。

### 3.1 zephyr 树改动（`D:\Zephyr\zephyr`）

**版本：** v4.3.0（detached），无本地 commit，改动全在未提交工作区。

#### 3.1.1 已修改官方文件（2 个）

**① `drivers/interrupt_controller/intc_plic.c`（+11 / -1）— 唯一的内核逻辑改动**

```c
ite = &config->isr_table[local_irq];
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
ite->isr(ite->arg);
```

作用：PLIC claim 到未注册中断时，先写 claim_complete 完成应答并返回，不再调用 `z_irq_spurious`（官方逻辑下不返回 → 死机）。与 HPM USB 枚举的 PLIC 问题相关。**这是唯一的内核代码逻辑改动，必须保留为受控补丁**（第 7 章）。

**② `subsys/usb/device_next/app/cdc_acm_serial.c`（+1）**

```c
#include <zephyr/sys/printk.h>
```

只加了一行 include，全文无 `printk(` 调用——**调试残留，建议删除**。

#### 3.1.2 未跟踪新增（3 处）

| 路径 | 内容 | 归属 |
| --- | --- | --- |
| `boards/st/stm32f407igh6/` | 自定义 STM32F407IGH6 板卡（dts/pinctrl/defconfig/board.yml 全套） | **留在树中，加 `SELF-MAINTAINED` 标注**（第 6 章） |
| `dts/bindings/mtd/winbond,w25q128.yaml` | W25Q128 SPI NOR Flash binding | **留在树中，加 `SELF-MAINTAINED` 标注**（第 6 章） |
| `doc/zmy_note/` | 个人学习笔记（step1/step2） | 个人文档，移出树 |

### 3.2 sdk_glue 改动（`D:\Zephyr_HPMicro\sdk_glue`）

**HPM Zephyr 适配层**，独立 git 仓库（`zephyr/module.yml` 声明 board_root/soc_root/dts_root）。**所有改动未提交。**

#### 3.2.1 真实逻辑改动（保留并 commit）

| 文件 | 改动量 | 内容 |
| --- | --- | --- |
| `drivers/pwm/pwm_hpmicro.c` | **~200 行大改** | PWMv2 多通道共存修复：`configured_channels` 位图追踪、`set_cmp_shadow_sel()` 封装、去重复配置、channel→CMP 索引映射修正 |
| `drivers/usb/udc/udc_hpmicro.c` | **~217 行大改** | USB device controller 驱动：DCD 放 AHB_SRAM 段、DLM↔系统地址转换、寄存器 dump、重建控制端点、对齐新版 UDC API（cfg 签名） |
| `drivers/serial/uart_hpmicro.c` | ~10 行 | HPM5361 硬件 RX idle + parity 配置修复 + RX 超时 workqueue |
| `drivers/usb/cherryusb/CMakeLists.txt` | 6 行 | `if(CONFIG_CHERRYUSB_DEVICE)` 包裹 + include 修正 |
| `drivers/usb/cherryusb/cherryusb_hpmicro.c` | +1 | 加 `printk.h` include（调试残留，确认删） |
| `drivers/usb/udc/Kconfig.hpmicro` | 4 行 | `select UDC_DRIVER`、`select NOCACHE_MEMORY if ARCH_HAS_NOCACHE_MEMORY_SUPPORT` |
| `drivers/pwm/Kconfig.hpmicro` | 1 行 | `select PWM` |
| `drivers/spi/spi_hpmicro.c` | 1 行 | `CONFIG_SPI_INTERRUPT_DRIVEN 1 → 0` |
| `dts/riscv/hpmicro/hpm53xx.dtsi` | +8 | 新增 `pwm1: pwm@f031c000` 节点 |
| `boards/hpmicro/hpm5361icb/hpm5361icb-pinctrl.dtsi` | +140 | 新增 12 组 pinmux（gpiob_spi/spi1/spi2_local/uart3/pwm0_*/pwm1_*/gpioy/uart1） |

#### 3.2.2 删除（清理备份）

- `hpm5361icb-pinctrl.dtsi.codex_backup_20260520_223323` — codex 备份残留，已删
- `boards_backup/hpm5361icb.ai_backup_20260520/` 整个目录 — AI 备份残留，已删

#### 3.2.3 CRLF 噪声

- `drivers/pinctrl/pinctrl_hpmicro.c` — 纯行尾符（非内容改动）

### 3.3 sdk_env 改动（`D:\Zephyr_HPMicro\sdk_env`）

**HPMicro 原始 SDK**，git 仓库根为 `D:/Zephyr_HPMicro/sdk_env`，`hpm_sdk/` 是其子目录。

#### 3.3.1 真实逻辑改动

| 文件 | 改动 | 内容 |
| --- | --- | --- |
| `hpm_sdk/soc/HPM5300/HPM5361/hpm_misc.h` | +9 | **DLM/ILM 地址转换修复**：`core_local_mem_to_sys_address()` 增加 DLM/ILM 判断，`sys_address_to_core_local_mem()` 增加 DLM 判断。对应 USB DCD 放 AHB_SRAM 后 DMA 地址正确 |
| `hpm_sdk/drivers/src/hpm_usb_drv.c` | +1 | 仅加一个空行（接近噪声） |

#### 3.3.2 工具链替换（非源码逻辑）

- **`tools/openocd/` 被整体替换**：新增 36 文件 + 修改 125 文件（多为 CRLF 噪声）。`sdk_env/tools/` 下现存 `openocd/`（新）、`openocd_new/`（解压来源）、`openocd_backup/`（旧版备份）、`openocd-windows-i686.zip`（压缩包）——openocd 升级中间产物
- `hpm_sdk/boards/openocd/interface/cmsis-dap.cfg` — 新增调试配置

#### 3.3.3 CRLF 噪声

- `hpm_sdk/components/usb/device/hpm_usb_device.c` — 纯行尾符
- `tools/openocd/tcl/...`（125 个 M）— 纯行尾符

### 3.4 改动全景图

```text
zephyr 树 (v4.3.0)
├── intc_plic.c               ← 真逻辑改动（PLIC spurious 吸收）→ 受控补丁
├── cdc_acm_serial.c          ← 调试残留（printk include）→ 删
├── boards/st/stm32f407igh6/  ← 业务板卡 → 留树中 + 标注
├── dts/bindings/winbond,w25q128.yaml ← 业务 binding → 留树中 + 标注
└── doc/zmy_note/             ← 个人笔记 → 移出

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

## 第 4 章 版本带来的固有问题与对策

> v4.3 有四个「固有问题」——不是配置错误，而是 v4.3 这个版本本身的设计。必须接受，并分别管理。

### 4.1 问题 1：CMSIS 对 Cortex-M 是空 stub

**现象：** `modules/cmsis/cmsis_core.h` 只处理 Cortex-A/R，对 Cortex-M 什么都不 include。

```c
// v4.3 的 modules/cmsis/cmsis_core.h：
#if defined(CONFIG_CPU_AARCH32_CORTEX_A) || defined(CONFIG_CPU_AARCH32_CORTEX_R)
#include "cmsis_core_a_r.h"
#endif
// 没有 Cortex-M 分支！
```

**为什么 v4.3 这样：** Zephyr v4.0 把 CMSIS Cortex-M 支持拆成了独立的 `cmsis_6` 模块。但你的 out-of-tree 构建里 cmsis_6 加载不了：
- `modules/cmsis_6/CMakeLists.txt` 只在 `CONFIG_CPU_CORTEX_M` 时 `zephyr_include_directories(.)`，而 out-of-tree 时序里 Kconfig 尚未处理；
- `D:\Zephyr\modules\hal\cmsis_6` 是**空目录**（只有 .git，无内容）。

**对策：** tflm 提供 `include/cmsis_core.h`，通过 `zephyr_include_directories` 把 include 目录加到全局路径最前面，遮蔽 zephyr 的空 stub：

```c
/* CMSIS core header for Cortex-M — shadows Zephyr's empty stub */
/* SELF-MAINTAINED — qingyu
 * Why: Zephyr v4.3 cmsis_core.h 对 Cortex-M 是空 stub（cmsis_6 分离方案在
 * out-of-tree 构建里没加载）。本文件通过 include 路径优先遮蔽它。 */
#if defined(CONFIG_CPU_CORTEX_M)
#define SHPR SHP       /* v4.3 内核用 SHPR，CMSIS 5.9 用 SHP（见 4.2） */
#include <soc.h>
#include <core_cm4.h>
#elif defined(CONFIG_CPU_AARCH32_CORTEX_A) || defined(CONFIG_CPU_AARCH32_CORTEX_R)
#endif
```

### 4.2 问题 2：SHPR/SHP 命名断裂

**现象：** zephyr v4.3 内核代码（`arch/arm/core/cortex_m/scb.c`）用 `SCB->SHPR`，而 CMSIS 5.9（`hal/cmsis` 的 core_cm4.h）把字段改名为 `SHP`。

```c
// zephyr v4.3 scb.c：
volatile uint32_t *shpr = (volatile uint32_t *)SCB->SHPR;   // 用旧名 SHPR

// hal/cmsis 5.9 core_cm4.h：
__IOM uint8_t  SHP[12U];   // 新名 SHP
```

**为什么：** Zephyr v4.x 新增的 `scb_context` 备份/恢复代码用了旧名 SHPR，没跟上 CMSIS v5.7 的改名。

**对策：** tflm 的 `include/cmsis_core.h` 里 `#define SHPR SHP` 一行解决。这是必需的，不能删。

### 4.3 问题 3：CMSIS Kconfig `if 0` 冲突（v4.3 有但影响小）

**现象：** `modules/Kconfig` 里有历史遗留的 `if 0 osource "modules/*/Kconfig"` 块，kconfiglib 仍会解析其中的符号，导致 `HAS_CMSIS_CORE` 依赖为 n 却被 select y。

**对策（应用工程 Kconfig 绕）：** 在 `source "Kconfig.zephyr"` 前预定义：

```kconfig
config HAS_CMSIS_CORE
    bool

source "Kconfig.zephyr"
```

这是应用工程侧绕，**不是改树**。

### 4.4 问题 4：模块加载时序（v4.3 有但影响小）

**现象：** 外部模块 CMakeLists 在 Kconfig 之前加载，`add_subdirectory_ifdef(CONFIG_HAS_STM32CUBE ...)` 永假。

**对策（应用工程 CMake 绕）：** 手动补 CMSIS/STM32 头文件路径 + `__PROGRAM_START`：

```cmake
if(CONFIG_CPU_CORTEX_M)
  zephyr_include_directories("${ZEPHYR_BASE}/../modules/hal/cmsis/CMSIS/Core/Include")
  zephyr_include_directories("${CMAKE_CURRENT_SOURCE_DIR}/include")
  zephyr_compile_definitions(__PROGRAM_START)
endif()
```

### 4.5 对策总结

| 固有问题 | 对策 | 改树？ |
| --- | --- | --- |
| CMSIS 空 stub | tflm `include/cmsis_core.h` 遮蔽 | ❌（应用侧） |
| SHPR/SHP 断裂 | `#define SHPR SHP` | ❌（应用侧） |
| CMSIS Kconfig 冲突 | 应用 Kconfig 预定义 `HAS_CMSIS_CORE` | ❌（应用侧） |
| 模块加载时序 | 应用 CMake 手动补路径 | ❌（应用侧） |

**全部用应用侧补丁解决，不需要改树。** 这些补丁就是"自维护层"的内容，要标注。

---

## 第 5 章 从零搭建完整流程（参考官网）

> 这一章是实操流程，参考 Zephyr 官网 [Getting Started Guide](https://docs.zephyrproject.org/latest/develop/getting_started/index.html)，按本工程定制。官网原版见附录 A。

### 5.0 前提：版本组合锁定

```text
zephyr v4.3.0     ⇔  Zephyr SDK 0.16.8    ⇔  GCC 12.2.0
                   ⇔  sdk_glue zsg_v0.7.0
                   ⇔  hpm_sdk v1.11.0
```

**这套组合是当前已验证能跑通的。不要随便换。**

### 5.1 安装主机依赖（Windows）

官网用 winget 安装：

```powershell
winget install Kitware.CMake Ninja-build.Ninja oss-winget.gperf Python.Python.3.12 Git.Git oss-winget.dtc wget 7zip.7zip
```

注意：
- **Python 3.12 强烈推荐**（官网明确说更新的 Python 可能失败）；
- 安装后关闭终端，可能需要把 7zip 加入 PATH；
- 本工程额外需要：`west`（见 5.3）。

### 5.2 创建 Python 虚拟环境

官网流程（venv 隔离，避免污染系统 Python）：

```powershell
# cmd 或 PowerShell
cd %HOMEPATH%
py -3.12 -m venv zephyrproject\.venv
```

激活（PowerShell 需要先允许脚本）：

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
zephyrproject\.venv\Scripts\activate.bat
```

激活后 shell 前缀会变成 `(.venv)`。**每次开新终端都要重新激活**，否则 `west` 找不到。

### 5.3 安装 west

```powershell
pip install west
```

west 是 Zephyr 的 workspace 管理器，后面的 `west init` / `west update` / `west build` / `west flash` 全靠它。

### 5.4 获取 zephyr 源码（方式一：west workspace）

官网标准方式：

```powershell
west init -m https://github.com/zephyrproject-rtos/zephyr zephyrproject
cd zephyrproject
west update
```

> ⚠️ **但本工程特殊：** 你的 zephyr 树已经存在（`D:\Zephyr\zephyr`，checkout 在 v4.3.0）。且本工程是 out-of-tree 应用（不走完整 workspace），用 `ZEPHYR_EXTRA_MODULES` + `BOARD_ROOT` 方式。
>
> 如果你要重建，方式二更贴近你现在的结构（见 5.5）。

### 5.5 获取 zephyr 源码（方式二：直接 clone，本工程推荐）

```powershell
git clone https://github.com/zephyrproject-rtos/zephyr D:\Zephyr\zephyr
cd D:\Zephyr\zephyr
git fetch origin --tags
git checkout v4.3.0
```

验证树是干净的：

```powershell
git status          # 期望：working tree clean
git log --oneline -1  # 期望：3568e1b6d5c release: Zephyr v4.3.0
```

> **树只读。** 之后所有自维护内容（板卡/binding/补丁）加进来时要标注（第 6/7/8 章）。

### 5.6 安装 Zephyr Python 依赖

官网方式（从 checkout 的 zephyr 读取依赖，版本匹配）：

```powershell
cd D:\Zephyr\zephyr
cmd /c zephyr\scripts\utils\west-packages-pip-install.cmd
```

> 注意：这可能升级/降级 west 本身，是正常的。

### 5.7 导出 Zephyr CMake 包

```powershell
west zephyr-export
```

这会把当前 zephyr checkout 注册到 CMake 用户包注册表，让 `find_package(Zephyr)` 自动找到。

### 5.8 安装 Zephyr SDK（0.16.8）

官网用 `west sdk install`（默认装最新，但本工程要 0.16.8 匹配 v4.3）：

```powershell
# 方式一：官网命令（会装默认 SDK 版本，注意版本匹配）
cd D:\Zephyr\zephyr
west sdk install

# 方式二：手动下载指定版本（本工程推荐，确保 0.16.8）
# 下载 zephyr-sdk-0.16.8_windows-x86_64.7z → 解压到 D:\Zephyr\zephyr-sdk-0.16.8
```

**为什么必须 0.16.8：** SDK 1.0.1 自报最低兼容 1.0，zephyr v4.3 请求 0.16.x，CMake 拒绝 1.0.1。0.16.8 与 v4.3 匹配（GCC 12.2.0）。

验证：

```powershell
D:\Zephyr\zephyr-sdk-0.16.8\riscv64-zephyr-elf\bin\riscv64-zephyr-elf-gcc --version
# 期望：GCC 12.2.0
```

### 5.9 拉取 hpm_sdk（sdk_env）

```powershell
git clone <hpm_sdk_repo> D:\Zephyr_HPMicro\sdk_env
cd D:\Zephyr_HPMicro\sdk_env
git checkout v1.11.0
git submodule update --init --recursive
```

hpm_sdk 是 HPM 原厂 SDK，被 sdk_glue 引用。**默认只读**，唯一例外 hpm_misc.h（带标注）。

### 5.10 拉取 sdk_glue

```powershell
git clone <sdk_glue_repo> D:\Zephyr_HPMicro\sdk_glue
cd D:\Zephyr_HPMicro\sdk_glue
git checkout zsg_v0.7.0
```

sdk_glue 是 HPM Zephyr 适配层，通过 `zephyr/module.yml` 声明为 Zephyr module。

### 5.11 拉取外部模块（CherryUSB / user/usb）

```powershell
git clone <cherryusb_repo> D:\Zephyr_HPMicro\modules\lib\CherryUSB
git clone <user_usb_repo> D:\Zephyr\modules\user\usb
```

> ⚠️ **绝对路径问题：** 这些路径当前硬编码在 tflm 根 CMakeLists 里。未来要收敛到 west manifest（第 9 章阶段 4）。

### 5.12 设置环境变量

```powershell
$env:ZEPHYR_BASE="D:\Zephyr\zephyr"
$env:ZEPHYR_SDK_INSTALL_DIR="D:\Zephyr\zephyr-sdk-0.16.8"
$env:ZEPHYR_TOOLCHAIN_VARIANT="zephyr"
$env:SDK_GLUE_DIR="D:\Zephyr_HPMicro\sdk_glue"
```

> 未来这些路径应统一由环境脚本 / west config 管理，不散落在 CMakeLists。

### 5.13 准备应用工程（tflm）

本工程已有 tflm。它负责：
- 声明这是 Zephyr 应用；
- 加入业务板卡（`project/boards/` → `BOARD_ROOT`）；
- 加入业务 binding（`dts/bindings/` → `DTS_ROOT`）；
- 选择 sdk_glue / hpm_sdk / cherryusb / user-usb 模块（`ZEPHYR_EXTRA_MODULES`）；
- 加入业务源码（thread/apps）。

根 CMakeLists 关键片段：

```cmake
# 路径来源：优先环境变量，其次默认值
if(DEFINED ENV{SDK_GLUE_DIR})
  set(SDK_GLUE_DIR "$ENV{SDK_GLUE_DIR}")
else()
  set(SDK_GLUE_DIR "D:/Zephyr_HPMicro/sdk_glue")
endif()

# 注册外部 board/soc/dts 搜索路径（sdk_glue 提供 HPM 平台）
list(APPEND BOARD_ROOT "${SDK_GLUE_DIR}")
list(APPEND SOC_ROOT   "${SDK_GLUE_DIR}")
list(APPEND DTS_ROOT   "${SDK_GLUE_DIR}")
list(APPEND DTS_ROOT   "${SDK_GLUE_DIR}/dts")

# 外部模块
set(ZEPHYR_EXTRA_MODULES
  "${SDK_GLUE_DIR}"
  "D:/Zephyr_HPMicro/modules/lib/CherryUSB"
  "D:/Zephyr/modules/user/usb")

# CMSIS 头路径（v4.3 固有问题，见 4.4）
if(CONFIG_CPU_CORTEX_M)
  zephyr_include_directories("${ZEPHYR_BASE}/../modules/hal/cmsis/CMSIS/Core/Include")
  zephyr_include_directories("${CMAKE_CURRENT_SOURCE_DIR}/include")
  zephyr_compile_definitions(__PROGRAM_START)
endif()
```

应用 Kconfig（`HAS_CMSIS_CORE` 预定义，见 4.3）：

```kconfig
config HAS_CMSIS_CORE
    bool

source "Kconfig.zephyr"
```

### 5.14 编译

```powershell
# 方式一：直接用 west
cd D:\Zephyr\projects\tflm
west build -b hpm5361icb -p

# 方式二：你现有的 build.bat
D:\Zephyr\projects\tflm\cmd\build\build.bat hpm5361icb -p
```

双板验证：

```powershell
west build -b hpm5361icb -p       # HPM（RISC-V，走 sdk_glue）
west build -b stm32f407igh6 -p    # STM32（ARM，走 hal_stm32 + CMSIS 补丁）
```

编译通过标志：生成 `build/zephyr/zephyr.hex/bin/elf`，Memory region 无溢出。

### 5.15 烧录

```powershell
west flash
```

HPM5361ICB 注意事项：
- OpenOCD 用与 hpm_sdk 配套的，固定 `adapter speed 500`；
- BOOT 全部悬空或切 ISP 可恢复烧录（固件异常时）；
- 烧录失败可能是底层状态异常，先确认固件无死循环；
- XIP flash 调试用 `hbreak` 硬件断点。

烧录通过标志：`Flash write complete` + `Verified OK`，板子能跑（串口有输出 / LED 变）。

---

## 第 6 章 板卡/binding 归属：留在树中，显式标注

### 6.1 原则

**板卡定义和 devicetree binding 是「平台/板的物理描述」，不是业务逻辑。它们本来就是树的职责——Zephyr 官方板也全在树里。**

- 官方板 → zephyr 树 `boards/`（无标注）
- **你的业务板（stm32f407igh6）→ zephyr 树 `boards/`，加 `SELF-MAINTAINED` 标注**
- **业务 binding（w25q128）→ zephyr 树 `dts/bindings/`，加 `SELF-MAINTAINED` 标注**

**为什么不搬进 tflm：**
- tflm 是架构层，掺入具体板卡会让框架和硬件耦合；
- 树天然就是放板卡/binding 的地方；
- 正确的做法不是搬家，而是**在树里显式标注**：这是我加的，不是官方内容。

### 6.2 板卡：stm32f407igh6（留在树中 + 标注）

位置不变（`D:\Zephyr\zephyr\boards\st\stm32f407igh6\`），但要标注：

**board.yml 顶部：**
```yaml
# SELF-MAINTAINED — qingyu
# Why: custom STM32F407IGH6 (176-pin BGA) board for the robot chassis project
# This board is NOT an official Zephyr board. Maintained in-tree, self-owned.
board:
  name: stm32f407igh6
  full_name: Custom STM32F407IGH6 Board
  vendor: st
  socs:
    - name: stm32f407xx
```

**stm32f407igh6.dts 顶部：**
```dts
/*
 * SELF-MAINTAINED — qingyu
 * Why: custom STM32F407IGH6 board, not in upstream Zephyr.
 * SPDX-License-Identifier: Apache-2.0
 */
```

**README.md**：写清硬件差异（引脚/晶振/外设连接）、维护者、日期。

板卡放树里后，Zephyr 自动发现，不需要 `BOARD_ROOT` 手动指：

```powershell
west build -b stm32f407igh6 -p   # 直接按名字选板
```

### 6.3 binding：w25q128（留在树中 + 标注）

位置不变（`D:\Zephyr\zephyr\dts\bindings\mtd\winbond,w25q128.yaml`），加注释：

```yaml
# SELF-MAINTAINED — qingyu
# Why: custom binding for Winbond W25Q128 SPI NOR Flash,
# used by tflm cmd/flash. Not present in upstream Zephyr.
description: Winbond W25Q128 SPI NOR Flash
include: [spi-device.yaml]
compatible: "winbond,w25q128"
on-bus: spi
```

binding 放树里后，DTC 自动搜索，不需要 `DTS_ROOT` 手动指。

### 6.4 什么时候板卡放 sdk_glue

- **HPM 平台参考板** → sdk_glue `boards/`（跟着 HPM 适配层走）；
- **hpm5361icb 自制核心板** → 取决于它算「参考板」还是「业务板」。关键不是目录，而是**放哪里都标注 + 固定 + 文档化**。

---

## 第 7 章 内核补丁处理：intc_plic

### 7.1 问题

`drivers/interrupt_controller/intc_plic.c` 需要加 spurious 中断吸收逻辑。这是**内核中断控制器驱动**，Zephyr module 机制覆盖不了（由 `CONFIG_PLIC` 在树内注册）。

### 7.2 三种方案

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| A. 提 upstream | 提交给 zephyr 官方，合入后树变纯净 | 理想但周期长、不确定 |
| B. 受控补丁 | 存成 patch 文件，树干净，需要时 apply | **推荐** |
| C. sdk_glue 覆盖 | 在 sdk_glue 声明私有 compatible 驱动 | 复制官方驱动，维护成本高，不推荐 |

### 7.3 推荐：方案 B + 构建脚本自动 apply

```text
D:\Zephyr\projects\tflm\zephyr-patches\
└── 0001-plic-handle-spurious-irq.patch
```

生成 patch：

```powershell
cd D:\Zephyr\zephyr
git diff drivers/interrupt_controller/intc_plic.c > D:\Zephyr\projects\tflm\zephyr-patches\0001-plic-handle-spurious-irq.patch
git checkout drivers/interrupt_controller/intc_plic.c   # 还原工作区
```

需要时 apply：

```powershell
cd D:\Zephyr\zephyr
git apply D:\Zephyr\projects\tflm\zephyr-patches\0001-plic-handle-spurious-irq.patch
```

build.bat 开头自动 apply（幂等）：

```powershell
cd %ZEPHYR_BASE%
git apply --check %TFLM_ROOT%\zephyr-patches\0001-plic-handle-spurious-irq.patch 2>nul
if %errorlevel% equ 0 (
    git apply %TFLM_ROOT%\zephyr-patches\0001-plic-handle-spurious-irq.patch
    echo [ok] applied zephyr patch
) else (
    echo [info] patch already applied or tree modified
)
```

### 7.4 记录

`zephyr-patches/README.md` 写清：补丁编号、文件、作用、对应问题、apply/revert 方法、upstream 状态。

---

## 第 8 章 自维护层标注规范

### 8.1 为什么必须标注

如果 sdk_glue / hpm_sdk / 树里的代码「悄悄偏离原厂」，三个月后没人知道哪里改了、为什么改、能不能动。标注让「自维护」变成可审计的。

### 8.2 标注格式（文件头）

```c
/* ┌──────────────────────────────────────────────────────────┐
 * │ SELF-MAINTAINED — <owner>                                │
 * │ File: <path>                                             │
 * │ Why: <一句话原因>                                        │
 * │ Changes vs upstream: <改了什么>                          │
 * │ Date: <YYYY-MM-DD>                                       │
 * │ Status: [active|proposed-upstream|deprecated]            │
 * └──────────────────────────────────────────────────────────┘ */
```

### 8.3 标注格式（代码块内）

```c
/* SELF-MAINTAINED: <原因> —— <owner> <date> */
```

### 8.4 需要标注的清单

#### zephyr 树内的自维护内容

| 位置 | 内容 | 状态 |
| --- | --- | --- |
| `zephyr/boards/st/stm32f407igh6/` | 自定义 STM32F407IGH6 业务板卡 | active（留树中，标注） |
| `zephyr/dts/bindings/mtd/winbond,w25q128.yaml` | W25Q128 SPI NOR binding | active（留树中，标注） |
| `zephyr/drivers/.../intc_plic.c` | spurious 吸收补丁 | proposed-upstream（patch） |

#### sdk_glue 内的自维护内容

| 位置 | 内容 | 状态 |
| --- | --- | --- |
| `sdk_glue/drivers/pwm/pwm_hpmicro.c` | PWMv2 多通道共存修复 | active |
| `sdk_glue/drivers/usb/udc/udc_hpmicro.c` | DCD 段/地址转换/调试 | active |
| `sdk_glue/drivers/serial/uart_hpmicro.c` | 硬件 RX idle + parity | active |
| `sdk_glue/drivers/spi/spi_hpmicro.c` | 关中断模式 | active |
| `sdk_glue/drivers/usb/cherryusb/*` | 条件编译 | active |
| `sdk_glue/dts/riscv/hpmicro/hpm53xx.dtsi` | +pwm1 | active |
| `sdk_glue/boards/.../hpm5361icb-pinctrl.dtsi` | +12 pinmux | active |

#### hpm_sdk 内的自维护内容

| 位置 | 内容 | 状态 |
| --- | --- | --- |
| `sdk_env/hpm_sdk/soc/.../hpm_misc.h` | DLM/ILM 地址转换 | active |

#### 应用工程内的自维护内容

| 位置 | 内容 | 状态 |
| --- | --- | --- |
| `tflm/include/cmsis_core.h` | v4.3 CMSIS stub 补丁（含 SHPR/SHP） | active |
| `tflm/Kconfig` | HAS_CMSIS_CORE 预定义 | active |

### 8.5 标注和 commit 的关系

- 标注负责「说明」；commit 负责「固化」；
- 每个自维护改动都要 commit，commit message 写 `self-maintained` 关键字；
- `git log --grep=self-maintained` 可审计。

---

## 第 9 章 干净化分步规划

### 9.1 目标状态

```text
zephyr 树          = 官方 v4.3.0 + 显式标注的自维护内容（板卡/binding）+ 受控补丁（intc_plic patch）
sdk_glue + hpm_sdk = 树外独立仓库，真实改动已 commit + 标注，CRLF 噪声已清理
tflm               = 独立框架 module + 应用侧补丁（cmsis_core.h/Kconfig）
业务仓库           = boards/、app/、thread/、scripts/
```

### 9.2 阶段 1：修行尾符（高优先，先做）

- [ ] 三个仓库配置 `.gitattributes`（`* text=auto` + C/C++ 强制 `eol=lf`）
- [ ] `git add --renormalize` 归一化，让 CRLF 噪声从 git 视野消失
- [ ] 验证 `git status` 只剩真实改动

### 9.3 阶段 2：真实改动 commit 固化

- [ ] sdk_glue：pwm/udc/uart/spi/cherryusb/Kconfig/dtsi 改动 commit（这是你的实际成果，不能丢）
- [ ] sdk_env：commit `hpm_misc.h`；openocd 定版后 commit
- [ ] 删除调试残留（cdc_acm_serial printk、cherryusb printk 确认删）
- [ ] 清理 openocd 中间产物（openocd_new/backup/zip）

### 9.4 阶段 3：树内自维护内容标注

- [ ] `zephyr/boards/st/stm32f407igh6/` 加 `SELF-MAINTAINED`（board.yml/dts/README）
- [ ] `zephyr/dts/bindings/mtd/winbond,w25q128.yaml` 加 `SELF-MAINTAINED`
- [ ] intc_plic 生成 patch 文件归档，工作区还原
- [ ] `doc/zmy_note/` 移到个人目录

### 9.5 阶段 4：接入收敛到 west

- [ ] 建立 `west.yml` 声明 zephyr/sdk_glue/sdk_env/cherryusb/user-usb 版本
- [ ] 根 CMakeLists 去掉绝对路径，改用 west module 发现
- [ ] `SDK_GLUE_DIR`/`ZEPHYR_SDK_INSTALL_DIR` 改环境变量/默认覆盖

### 9.6 阶段 5：验证

- [ ] `west build -b hpm5361icb -p` 与 `west build -b stm32f407igh6 -p` 均通过
- [ ] 三个仓库 `git status` 干净（仅受控内容）
- [ ] 更新 `temp/doc/env_changes.md`

### 9.7 长期原则

1. **zephyr 树只允许官方 commit + 显式标注的自维护内容 + 受控补丁**。
2. **HPM 支持永远在树外**：sdk_glue 是 module，hpm_sdk 被 module 引用，west 固定版本。
3. **板卡/binding 留在树中 + 标注**，不是搬进架构层。
4. **版本管理用 west，不用绝对路径**。
5. **统一行尾符**，避免 CRLF 噪声污染 git。
6. **真实改动必须 commit**——没有 git 痕迹的功能改动，等于没记录。
7. **v4.3 的固有问题用应用侧补丁解决**（cmsis_core.h/Kconfig），不靠改树。

---

## 第 10 章 验收清单

### 10.1 树可控（而非零改动）

- [ ] `git -C D:\Zephyr\zephyr status` → 只有显式标注的自维护内容（板卡/binding）+ 受控补丁
- [ ] 树里每个自维护文件都有 `SELF-MAINTAINED` 标注
- [ ] 官方文件一个都没被改（intc_plic 走 patch，工作区还原后干净）
- [ ] `git -C D:\Zephyr_HPMicro\sdk_glue status` → 只剩真实逻辑改动（CRLF 已清理）
- [ ] `git -C D:\Zephyr_HPMicro\sdk_env status` → 只剩 hpm_misc.h 等真实改动

### 10.2 自维护层标注

- [ ] stm32f407igh6 板卡、w25q128 binding 有标注
- [ ] intc_plic 补丁归档为 patch 文件
- [ ] sdk_glue 每个真实改动有标注 + commit
- [ ] hpm_sdk hpm_misc.h 有标注
- [ ] `git log --grep=self-maintained` 能查到所有自维护改动

### 10.3 编译 + 烧录

- [ ] `west build -b hpm5361icb -p` 通过
- [ ] `west build -b stm32f407igh6 -p` 通过
- [ ] `west flash` 烧进 hpm5361icb，板子能跑
- [ ] 串口 / LED / 线程有实际输出

### 10.4 版本可复现

- [ ] west.yml 声明所有依赖版本
- [ ] 根 CMakeLists 无绝对路径泄漏
- [ ] 换一台机器按本文档能完整重建

### 10.5 收尾

- [ ] 更新 `temp/doc/env_changes.md`
- [ ] 更新本文档反映现状

---

## 附录 A 官网 Getting Started 速查（原版）

来源：[Zephyr Getting Started Guide](https://docs.zephyrproject.org/latest/develop/getting_started/index.html)

```powershell
# 1. 安装主机依赖（Windows）
winget install Kitware.CMake Ninja-build.Ninja oss-winget.gperf Python.Python.3.12 Git.Git oss-winget.dtc wget 7zip.7zip

# 2. 创建 venv
cd %HOMEPATH%
py -3.12 -m venv zephyrproject\.venv
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
zephyrproject\.venv\Scripts\activate.bat

# 3. 安装 west
pip install west

# 4. 获取 zephyr 源码
west init -m https://github.com/zephyrproject-rtos/zephyr zephyrproject
cd zephyrproject
west update

# 5. 安装 Python 依赖
cmd /c zephyr\scripts\utils\west-packages-pip-install.cmd

# 6. 导出 CMake 包
west zephyr-export

# 7. 安装 SDK
cd %HOMEPATH%\zephyrproject\zephyr
west sdk install

# 8. 构建 blinky
cd %HOMEPATH%\zephyrproject\zephyr
west build -p always -b <your-board-name> samples\basic\blinky

# 9. 烧录
west flash
```

### 官网关键提醒（原文要点）

- **Python 3.12 强烈推荐**：使用更新的 Python 可能失败（例如安装所需包时）；
- **每次开新终端都要激活 venv**：否则 `west` 找不到，或用到不同 Python 环境；
- **`-p always` 强制 pristine build**：避免陈旧文件；之后可换 `-p auto`；
- **west flash 报错**：可能缺主机工具，按提示安装。

---

## 附录 B 本工程从零到烧录速查

```powershell
# 1. zephyr 树（只读 v4.3.0）
git clone https://github.com/zephyrproject-rtos/zephyr D:\Zephyr\zephyr
cd D:\Zephyr\zephyr && git fetch origin --tags && git checkout v4.3.0

# 2. SDK 0.16.8
# 下载 zephyr-sdk-0.16.8_windows-x86_64.7z → 解压到 D:\Zephyr\zephyr-sdk-0.16.8

# 3. hpm_sdk
git clone <hpm_sdk_repo> D:\Zephyr_HPMicro\sdk_env
cd D:\Zephyr_HPMicro\sdk_env && git checkout v1.11.0 && git submodule update --init

# 4. sdk_glue
git clone <sdk_glue_repo> D:\Zephyr_HPMicro\sdk_glue
cd D:\Zephyr_HPMicro\sdk_glue && git checkout zsg_v0.7.0

# 5. 外部模块
git clone <cherryusb_repo> D:\Zephyr_HPMicro\modules\lib\CherryUSB
git clone <user_usb_repo> D:\Zephyr\modules\user\usb

# 6. 应用工程（已有 tflm）

# 7. 环境变量
$env:ZEPHYR_BASE="D:\Zephyr\zephyr"
$env:ZEPHYR_SDK_INSTALL_DIR="D:\Zephyr\zephyr-sdk-0.16.8"
$env:SDK_GLUE_DIR="D:\Zephyr_HPMicro\sdk_glue"

# 8. 恢复树内自维护内容（板卡/binding 已标注）
#    ├─ zephyr/boards/st/stm32f407igh6/          ← 自维护板卡（SELF-MAINTAINED）
#    └─ zephyr/dts/bindings/mtd/winbond,w25q128.yaml ← 自维护 binding（标注）

# 9. 打内核补丁（intc_plic）
cd D:\Zephyr\zephyr
git apply D:\Zephyr\projects\tflm\zephyr-patches\0001-plic-handle-spurious-irq.patch

# 10. 编译 + 烧录
cd D:\Zephyr\projects\tflm
west build -b hpm5361icb -p
west flash
```

---

## 附录 C 关键路径速查

| 实体 | 路径 | 角色 |
| --- | --- | --- |
| Zephyr 树 | `D:\Zephyr\zephyr` | 官方 v4.3.0 |
| sdk_glue | `D:\Zephyr_HPMicro\sdk_glue` | HPM Zephyr 适配 module（zsg_v0.7.0） |
| sdk_env | `D:\Zephyr_HPMicro\sdk_env` | HPM 原始 SDK + 工具链（仓库根） |
| hpm_sdk | `sdk_env\hpm_sdk` | HPM SDK 源码子目录（v1.11.0） |
| openocd | `sdk_env\tools\openocd` | 调试工具（已替换） |
| tflm | `D:\Zephyr\projects\tflm` | 框架 + 参考业务 |
| 受控补丁 | `tflm\zephyr-patches\` | intc_plic patch 归档 |
| 环境降级记录 | `temp/doc/env_changes.md` | v4.4→v4.3 / SDK 降级 |
| 板卡移植记录 | `temp/doc/hpm5361icb_porting_record.md` | HPM5361 自制板 |
| USB 枚举修复 | `doc/usb_enumeration_fix_record.md` | PLIC/DCD/SET_ADDRESS |
| PWM 双通道修复 | `doc/pwm_hpmicro_driver_changes.md` | PWMv2 多通道共存 |
| 官网文档 | [Getting Started Guide](https://docs.zephyrproject.org/latest/develop/getting_started/index.html) | 从零搭建官方流程 |

---

## 附录 D 一句话总结

> **定格 Zephyr v4.3.0**（唯一能编译你已 v4.x 化的 sdk_glue、且避开 v4.4 大坑的版本）。zephyr 树 = 官方内容（不改）+ 显式标注的自维护内容（板卡/binding）+ 受控补丁（intc_plic patch）。v4.3 的 CMSIS 空 stub / SHPR 断裂用应用侧补丁解决（`cmsis_core.h` + Kconfig 预定义）。HPM 适配住在 sdk_glue / hpm_sdk 并显式标注 + commit。业务逻辑住在 tflm。最终 `west build -b hpm5361icb -p` + `west flash` 能跑通，这套架构就立住了。
