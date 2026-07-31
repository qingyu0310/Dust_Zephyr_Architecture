# Zephyr + HPM 先验测试：疑问清单

> **目标（已确认）：** 不改动 `D:\Zephyr` / `D:\Zephyr_HPMicro` 生产环境，在 `E:\Zephyr_Test` 与 `E:\Zephyr_HPMicro_Test` 从零搭建一套，按手册《[zephyr_hpm_从零搭建与干净化手册.md](./zephyr_hpm_从零搭建与干净化手册.md)》做**先验测试**——验证「zephyr v4.3.0 + sdk_glue + hpm_sdk + 应用工程」这套架构**能编译、能烧录、树可控**。
>
> 本清单是在这个目标下重写整理的待确认疑问，**取代旧版疑问清单**（旧版疑问 1「手册与当前工作区对应关系」已由该目标澄清，不再成立）。
>
> 每条疑问标注：出处、疑问内容、需要确认的点、可能走向。

---

## 目录

- [Zephyr + HPM 先验测试：疑问清单](#zephyr--hpm-先验测试疑问清单)
	- [目录](#目录)
	- [疑问 1：先验测试的范围](#疑问-1先验测试的范围)
		- [出处](#出处)
		- [疑问](#疑问)
		- [需要拍板的点](#需要拍板的点)
	- [疑问 2：e 盘目录结构如何对应 D 盘](#疑问-2e-盘目录结构如何对应-d-盘)
		- [出处](#出处-1)
		- [疑问](#疑问-1)
		- [需要拍板的点](#需要拍板的点-1)
	- [疑问 3：sdk\_glue 的 v4.x 化代码从哪里来](#疑问-3sdk_glue-的-v4x-化代码从哪里来)
		- [出处](#出处-2)
		- [疑问](#疑问-2)
		- [需要拍板的点](#需要拍板的点-2)
	- [疑问 4：HAS\_CMSIS\_CORE 预定义与 kconfiglib 重复定义行为](#疑问-4has_cmsis_core-预定义与-kconfiglib-重复定义行为)
		- [出处](#出处-3)
		- [疑问](#疑问-3)
		- [需要确认的点](#需要确认的点)
	- [疑问 5：intc\_plic 补丁与「禁止 git 恢复」铁律](#疑问-5intc_plic-补丁与禁止-git-恢复铁律)
		- [出处](#出处-4)
		- [疑问](#疑问-4)
		- [需要拍板的点](#需要拍板的点-3)
	- [疑问 6：两块板卡（hpm5361icb / stm32f407igh6）在测试环境中的处理](#疑问-6两块板卡hpm5361icb--stm32f407igh6在测试环境中的处理)
		- [出处](#出处-5)
		- [疑问](#疑问-5)
		- [需要拍板的点](#需要拍板的点-4)
	- [疑问 7：工具链 / SDK / west 环境](#疑问-7工具链--sdk--west-环境)
		- [出处](#出处-6)
		- [疑问](#疑问-6)
		- [需要拍板的点](#需要拍板的点-5)
	- [疑问 8：先验测试的验证标准](#疑问-8先验测试的验证标准)
		- [出处](#出处-7)
		- [疑问](#疑问-7)
	- [附：已确认无疑问的部分](#附已确认无疑问的部分)

---

## 疑问 1：先验测试的范围

### 出处

- 手册第 5.14 章《编译》（双板验证：hpm5361icb + stm32f407igh6）
- 手册第 4 章《版本带来的固有问题与对策》

### 疑问

手册的最终目标是双板都能编译烧录，但两个板的验证路径不同：

| 板 | 架构 | 走的适配 | 触发的固有问题 |
| --- | --- | --- | --- |
| hpm5361icb | RISC-V | sdk_glue | intc_plic 补丁、PLIC/DCD |
| stm32f407igh6 | ARM | hal_stm32 + CMSIS | **CMSIS 空 stub、SHPR/SHP、Kconfig 冲突、模块时序（第 4 章全部四个问题）** |

需要确认：

1. **先验测试只覆盖 HPM5361 路径**，还是 **HPM5361 + STM32F407 都要**？
   - 如果只测 HPM5361，第 4 章的四个应用侧补丁（cmsis_core.h / `#define SHPR SHP` / HAS_CMSIS_CORE 预定义 / CMake 补路径）大多只在 ARM 侧触发，等于验证不到。
   - 手册的干净化目标包含 stm32f407igh6 板卡留树 + 标注，如果先验测试不含 ARM，这部分也验证不到。
2. **验证到哪一步**：仅 `west build` 编译通过，还是 `west flash` 烧录到硬件跑起来？
   - 烧录需要实际硬件连接（HPM5361ICB 核心板 / OpenOCD / CMSIS-DAP）。

### 需要拍板的点

- 测试范围：仅 HPM5361、仅编译验证、还是双板 + 烧录全流程。

---

## 疑问 2：e 盘目录结构如何对应 D 盘

### 出处

- 手册附录 C《关键路径速查》（D 盘路径）
- 手册第 5 章《从零搭建完整流程》

### 疑问

D 盘现有结构与 E 盘先验环境的对应关系没有定义。先验测试环境应该镜像一套独立的目录树：

| D 盘（生产） | E 盘（先验测试）对应路径？ |
| --- | --- |
| `D:\Zephyr\zephyr` | `E:\Zephyr_Test\zephyr` ? |
| `D:\Zephyr\projects\tflm`（应用工程） | `E:\Zephyr_Test` 本身 ? 还是 `E:\Zephyr_Test\projects\...` ? |
| `D:\Zephyr\modules\user\usb`（自研 USB 模块） | `E:\Zephyr_Test\modules\user\usb` ? |
| `D:\Zephyr_HPMicro\sdk_glue` | `E:\Zephyr_HPMicro_Test\sdk_glue` ? |
| `D:\Zephyr_HPMicro\sdk_env`（hpm_sdk + openocd） | `E:\Zephyr_HPMicro_Test\sdk_env` ? |
| `D:\Zephyr_HPMicro\modules\lib\CherryUSB` | `E:\Zephyr_HPMicro_Test\modules\lib\CherryUSB` ? |
| `D:\Zephyr\zephyr-sdk-0.16.8`（工具链） | 在 E 盘重新解压 ? 还是共用 D 盘的 SDK ? |

需要确认：

1. **目录布局**：E:\Zephyr_Test 与 E:\Zephyr_HPMicro_Test 各自放什么、放哪些仓库？由我按上表设计，还是你有指定的布局？
2. **应用工程**：用 D 盘 tflm 整体拷贝一份到 E 盘，还是**新建一个最小验证应用**（只带 CMSIS 补丁 + 一个 blinky/串口线程）？
   - 最小应用更符合"先验测试"语义——先验证链路通，不掺业务。

### 需要拍板的点

- E 盘目录布局方案（建议给出一个，用户确认或改）。
- 应用工程用 tflm 拷贝还是新建最小应用。

---

## 疑问 3：sdk_glue 的 v4.x 化代码从哪里来

### 出处

- 手册第 2.3 章《为什么不能回到 v3.7.0 LTS》（决定性证据）
- 手册第 3.2.1 章 sdk_glue 真实逻辑改动清单

### 疑问

这是先验测试最核心的卡点：

- E 盘 sdk_glue 如果直接 `git clone` 官方 `zsg_v0.7.0` → 它绑定 **v3.7.0**，USB 驱动 `udc_hpmicro.c` 是 **v3.7 签名**（`udc_ep_is_busy(dev, ep)`），在 zephyr v4.3 下**编译不过**（v4.3 要 `cfg` 签名）。
- 手册第 2 章说得很清楚：**sdk_glue 是被 v4.x 化过的代码才能配 v4.3**。这份 v4.x 化目前只存在于 D 盘 sdk_glue 的未提交工作区里（pwm/udc/uart/spi 等大改）。

需要确认：**E 盘 sdk_glue 的 v4.x 化改动从哪来？**

| 方案 | 做法 | 优点 | 缺点 |
| --- | --- | --- | --- |
| A. 拷贝 D 盘文件 | 把 D 盘 sdk_glue 里改过的源文件整体拷到 E 盘 | 改动原样保留 | 需要筛选哪些文件改了、哪些是 CRLF 噪声 |
| B. 导出 patch 再打 | `git diff` D 盘 sdk_glue → patch 文件 → E 盘 apply | 干净、可审计、与手册第 7 章受控补丁同思路 | D 盘改动未提交，`git diff` 可得但需先确认内容 |
| C. 重新实现 | 在 E 盘按 v4.3 API 重写 | 不依赖 D 盘 | 工作量大，udc 是 ~200 行级大改，不符合先验测试初衷 |

### 需要拍板的点

- sdk_glue v4.x 化代码用哪种方式进 E 盘（推荐 A 或 B）。
- 用 A/B 时，是否先对 D 盘 sdk_glue 做一次 `git status` / `git diff` 核对，确认当前真实改动范围（手册第 3 章盘点是上一会话快照，7-31 的 git checkout 可能影响过 pwm_hpmicro.c）。

---

## 疑问 4：HAS_CMSIS_CORE 预定义与 kconfiglib 重复定义行为

### 出处

- 手册第 4.3 章《CMSIS Kconfig `if 0` 冲突》
- 手册第 5.13 章应用 Kconfig 片段

### 疑问

手册对策是在应用工程 Kconfig 里、`source "Kconfig.zephyr"` **之前**预定义：

```kconfig
config HAS_CMSIS_CORE
    bool

source "Kconfig.zephyr"
```

但冲突来源正是 zephyr `modules/Kconfig` 里也定义了 `HAS_CMSIS_CORE`（`if 0 osource` 回退块仍被 kconfiglib 解析）——即该符号会出现**两次定义**。

我不确定的点：

1. **kconfiglib 对同一符号两次定义的行为**：先定义者生效（后者 warning）？后定义覆盖？还是直接报错？
   - 若报错 / warning→error，应用侧预定义会触发同样问题，需换绕法（如定义不同符号、用 `select`/`default y` 而非重新 `config`）。
   - 若仅 warning 且 Zephyr 容忍，当前方案成立。
2. **v4.3 与 v4.4 的差别**：文档说 v4.3 里这个问题"影响小、可绕"，v4.4"严重"。差别具体是什么（符号数量不同 / v4.4 有 cmsis_6 与 cmsis 双份冲突 / v4.3 只是单份 `if 0` 残留）？

### 需要确认的点

- 这套"应用预定义 HAS_CMSIS_CORE"在 v4.3 下是否实际验证过能编译、无 warning？
- 先验测试的 ARM 侧正好可以验证这一点。

---

## 疑问 5：intc_plic 补丁与「禁止 git 恢复」铁律

### 出处

- 手册第 7 章《内核补丁处理：intc_plic》
- 记忆 [no-git-restore]（2026-07-31）

### 疑问

手册 7.3 生成 patch 的流程含一句：

```powershell
git checkout drivers/interrupt_controller/intc_plic.c   # 还原工作区
```

而记忆铁律 [no-git-restore]：**任何时候都不准用 git checkout / git restore 恢复文件**（起因：7-31 用 git checkout 清掉了用户未提交的 pwm_hpmicro.c 修改）。

需要区分：

- 手册这里的对象是 **zephyr 官方树文件的工作区改动**（我们自己加、要归档成 patch 的那几行）。先 `git diff > patch` 归档、再还原到官方状态，逻辑上安全——不会动任何"用户未提交成果"。
- 但铁律字面是"任何时候"。需要确认：还原 intc_plic 用 `git checkout`（官方树文件，安全）还是改走 **Edit 手工回退**（绝对不碰 git 恢复命令）？

另外：**先验测试里这个补丁打不打**？hpm5361icb 的 USB 枚举依赖 PLIC spurious 吸收（手册 3.1.1），如果不打这个补丁，HPM 路径的 USB 测试可能跑不起来。

### 需要拍板的点

- intc_plic 工作区还原方式：git checkout 还是 Edit 手工。
- 铁律 [no-git-restore] 的适用范围：禁止恢复一切文件，还是禁止恢复含用户未提交成果的文件。
- 先验测试是否打 intc_plic 补丁。

---

## 疑问 6：两块板卡（hpm5361icb / stm32f407igh6）在测试环境中的处理

### 出处

- 手册第 6 章《板卡/binding 归属：留在树中，显式标注》
- 手册第 6.4 章《什么时候板卡放 sdk_glue》

### 疑问

手册的方案：业务板卡/binding **留树 + `SELF-MAINTAINED` 标注**。先验测试环境里需要复现这一套才能验证：

| 板卡/binding | 手册方案 | 先验测试环境放哪 |
| --- | --- | --- |
| stm32f407igh6（业务板） | zephyr 树 `boards/st/stm32f407igh6/` + 标注 | E 盘 zephyr 树对应位置？ |
| winbond,w25q128 binding | zephyr 树 `dts/bindings/mtd/` + 标注 | E 盘 zephyr 树对应位置？ |
| hpm5361icb（自制核心板） | 未定死（参考板 vs 业务板，见手册 6.4） | sdk_glue `boards/` 还是别处？ |

需要确认：

1. hpm5361icb 在测试环境里定位为**参考板**（留 sdk_glue boards/，跟适配层走）还是**业务板**（搬出）？——手册第 6.4 章一直没定，先验测试正好可以定下来。
2. 这三项在 E 盘复现时，是否就是"从 D 盘拷对应文件到 E 盘对应位置 + 加标注"，还是重新按手册写。

### 需要拍板的点

- hpm5361icb 最终归属。
- 先验测试环境里自维护板卡/binding 的复现方式。

---

## 疑问 7：工具链 / SDK / west 环境

### 出处

- 手册第 5.2 / 5.3 / 5.6 / 5.8 / 5.12 章

### 疑问

E 盘先验环境需要独立的工具链环境，还是可以共用 D 盘的？

| 项 | 手册做法 | 先验测试的做法？ |
| --- | --- | --- |
| Python venv + west | venv 独立建 | E 盘新建一套？还是共用 D 盘的 venv/west？ |
| Zephyr SDK 0.16.8 | 手动解压到指定目录 | E 盘重新解压一份（下载 ~1GB）？还是 `ZEPHYR_SDK_INSTALL_DIR` 直接指 D 盘已有的？ |
| 环境变量 | `ZEPHYR_BASE` / `ZEPHYR_SDK_INSTALL_DIR` / `SDK_GLUE_DIR` | E 盘对应路径 |
| hpm_sdk 配套 openocd | sdk_env\tools\openocd | E 盘 sdk_env clone 自带 |

SDK 特别说明：手册 5.8 强调必须 0.16.8（v4.3 匹配，GCC 12.2.0；SDK 1.0.1 会被 CMake 拒绝）。如果 D 盘已有 0.16.8，先验测试直接用同一份是最省事的——SDK 是工具链不是源码，共用不冲突。

### 需要拍板的点

- SDK / venv / west：独立建还是共用 D 盘。
- 共用的话，验证手册"换一台机器按本文档能完整重建"的目标会在先验测试里打折扣（因为没验证下载+安装那一段），是否可接受。

---

## 疑问 8：先验测试的验证标准

### 出处

- 手册第 10 章《验收清单》

### 疑问

"先验测试通过"的判定标准是什么？建议从手册第 10 章里选子集：

| 层级 | 标准 | 手册出处 |
| --- | --- | --- |
| 编译 | `west build -b <board> -p` 通过、无 warning→error、Memory region 无溢出 | 10.3 |
| 烧录 | `west flash` 成功，板子有实际输出（串口/LED/线程） | 10.3 |
| 树可控 | E 盘 zephyr 树 `git status` 只剩标注的自维护内容 + 受控补丁 | 10.1 |
| 版本可复现 | E 盘根 CMakeLists 无 D 盘绝对路径泄漏 | 10.4 |

需要确认：先验测试做到**编译级**还是**烧录级**；树可控 / 版本可复现是否也纳入本次验证。

---

## 附：已确认无疑问的部分

- **目标**：不动 D 盘，E 盘先验测试（用户已确认）。
- **版本选型**：v3.7 回不去（sdk_glue 已 v4.x 化）、v4.4 排除（CMSIS Kconfig 冲突 + 模块时序）、v4.3 唯一可用。推理成立。
- **第 4 章固有问题对策**：CMSIS 空 stub / SHPR/SHP / 模块时序用应用侧补丁绕，不改树——方向明确，只是先验测试里需要实际跑一遍确认（见疑问 4）。
- **留树 + 标注**：业务板卡/binding 留树中 + `SELF-MAINTAINED` 标注，不搬进架构层。
- **干净化阶段**：修行尾符 → commit 固化 → 树内标注 → 收敛 west → 验证。
- **D 盘不动**：所有 git 操作、文件改动都限定在 E 盘先验环境内。

---

## 最终回应（用户拍板结论，2026-07-31）

### 前提约束（已确认）

1. **E 盘是纯先验环境，D/E 完全独立**，E 不依赖 D（SDK 也从网上下，E 盘独立解压）。
2. **zephyr 树本体**从网上下 `v4.3.0` 干净版，**不复制 D 盘树**。
3. **zephyr 树的改动**（stm32f407igh6 板卡 / winbond,w25q128 binding / intc_plic 补丁）通过 **patch 从 D 盘只读导出**（`git diff`，零写入）→ E 盘 `git apply`。
4. **HPM sdk 改动允许复制**（patch 方式）：sdk_glue / sdk_env(hpm_sdk) / CherryUSB 的改动全部保留。**HPM SDK 底层 bug 修复必须保留**（尤其 `hpm_misc.h` 的 DLM/ILM 地址转换）。
5. **tflm 允许复制**到 E 盘（作为应用工程）。
6. **SDK 0.16.8 从网上下**，E 盘独立解压；**venv/west E 盘独立建**；环境变量全指 E 盘。

### 疑问 1：测试范围 → ✅ 双板都测

- **HPM5361**：编译 + 烧录（有硬件，走 sdk_glue）。
- **STM32F407**：编译（第 4 章四个固有问题全在 ARM 侧，必须验证到）。
- 先 HPM 后 ARM，分层推进。

### 疑问 2：E 盘目录结构 → ✅ tflm 拷贝 + 镜像布局

```
E:\Zephyr_Test\                  ← 应用工程根（tflm 允许复制）
│   ├── zephyr\                  ← 网上 clone v4.3.0（干净）
│   ├── zephyr-sdk-0.16.8\       ← 网上下载解压（独立）
│   ├── app\                     ← tflm 复制（排除 build）
│   └── zephyr-patches\          ← intc_plic patch
E:\Zephyr_HPMicro_Test\
    ├── sdk_glue\                ← 网上 clone zsg_v0.7.0 + apply patch
    ├── sdk_env\                 ← 网上 clone v1.11.0 + apply patch
    └── modules\lib\CherryUSB\   ← 网上 clone + apply patch
```

### 疑问 3：sdk_glue 的 v4.x 化代码从哪里来 → ✅ patch 方式

- **方式**：D 盘 `git diff` 导出 patch（只读）→ E 盘 `git apply`。比直接复制文件干净（排除 CRLF 噪声）。
- **已核对的真实改动范围**：
  - `sdk_glue/drivers/pwm/pwm_hpmicro.c`：201 行（`configured_channels` 仍在，7-31 的 checkout 未清掉）
  - `sdk_glue/drivers/usb/udc/udc_hpmicro.c`：217 行（`udc_ep_is_busy(cfg)` cfg 签名确认）
  - `sdk_glue/drivers/serial/uart_hpmicro.c`：10 行（硬件 RX idle + parity）
  - `sdk_glue/drivers/spi/spi_hpmicro.c`、cherryusb CMakeLists/hpmicro.c、Kconfig、`hpm53xx.dtsi`、`hpm5361icb-pinctrl.dtsi`
  - `sdk_env/hpm_sdk/soc/.../hpm_misc.h`：9 行 DLM/ILM 地址转换 = **HPM 底层 bug 修复，必须保留**
  - `CherryUSB/osal/usb_osal_zephyr.c`：`#undef ARRAY_SIZE` 冲突修复

### 疑问 4：HAS_CMSIS_CORE 预定义与 kconfiglib 重复定义 → ✅ 方案成立

- 已查 kconfiglib 源码：**同一 symbol 多次定义 = 合并属性，不报错**。
- v4.4 报的 `HAS_CMSIS_CORE ... y-selected` 是 **`select y` vs `depends on n` 的语义冲突**，不是"重复定义"。应用侧预定义（依赖从 n→y）正是绕法。
- env_changes.md 记录过 v4.3 下这套能编译。**STM32 编译时实际验证。**

### 疑问 5：intc_plic 补丁与「禁止 git 恢复」铁律 → ✅ patch，D 盘只读，E 盘 apply

- D 盘 zephyr **只生成 patch，不还原工作区**（零写入，不触碰任何未提交成果）。
- E 盘 zephyr（网上 clone）`git apply` 补丁。
- **先验测试必须打 intc_plic 补丁**（HPM USB 枚举依赖 spurious 吸收）。
- 不违反 no-git-restore：D 盘零写入；E 盘是新 clone 无用户成果。

### 疑问 6：两块板卡在测试环境 → ✅ hpm5361icb 留 sdk_glue

- **hpm5361icb**：留 `sdk_glue/boards/hpmicro/hpm5361icb/`（作为参考板），加 `SELF-MAINTAINED` 标注，说明依赖 HPM 底层 bug 修复。
- **stm32f407igh6 / winbond,w25q128 binding**：在 zephyr 树里 → 通过 patch 从 D 盘导出到 E 盘 + 加 `SELF-MAINTAINED` 标注（与疑问 5 同机制）。

### 疑问 7：工具链 / SDK / west 环境 → ✅ 全部 E 盘独立

- **Zephyr SDK 0.16.8**：从网上下，E 盘独立解压（`ZEPHYR_SDK_INSTALL_DIR` 指 E 盘）。D/E 零耦合。
- **venv/west**：E 盘独立建（验证 5.2-5.3 流程）。
- 环境变量全指 E 盘路径。

### 疑问 8：先验测试的验证标准 → ✅ 四层都纳入

| 层级 | 标准 |
| --- | --- |
| 编译 | `west build -b hpm5361icb -p` + `west build -b stm32f407igh6 -p` 通过、无 warning→error、无溢出 |
| 烧录 | `west flash hpm5361icb` 成功，板子有实际输出 |
| 树可控 | E 盘 zephyr `git status` 只剩标注的自维护内容 + 受控补丁 |
| 版本可复现 | E 盘根 CMakeLists 无 D 盘绝对路径泄漏 |

---

_疑问清单到此，全部拍板完毕。执行者按本回应 + 手册第 5 章在 E 盘开搭。_
