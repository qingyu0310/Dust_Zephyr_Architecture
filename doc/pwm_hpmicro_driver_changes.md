# pwm_hpmicro.c 改动记录

> 基于 HPMicro Zephyr SDK 原始驱动，针对 HPM5361 多通道共存问题的自定义修改。

## 改动总览

| # | 改动 | 原因 | 文件位置 |
|---|------|------|----------|
| 1 | `struct pwm_hpmicro_data` 加 `configured_channels` | 追踪已初始化通道，避免重复配置 | L42-44 |
| 2 | 新增 `set_cmp_shadow_sel()` | 替代 SDK 的 `pwm_load_cmp_shadow_on_capture()`，后者会误写 CMPCFG | L47-51 |
| 3 | set_cycles 加 `cmp_idx = channel` | per-channel CMP，替代所有通道共用 CMP[23] | L65 |
| 4 | set_cycles 加 `pulse_cycles == 0` 判断 | pulse=0 时 CMP > RLD 实现 0% 占空比 | L118-120 |
| 5 | set_cycles 改 first-init 守卫逻辑 | 从 `prld` 变化改为 `configured_channels` 位检测 | L122 |
| 6 | 首通道用 `cmp_idx` 替代固定 CMP[23] | 各通道配置自己独立的 CMP | L137-142 |
| 7 | 非首通道走 RLD 更新分支 | `else if` 只更新 reload，不重新 setup_waveform | L147-149 |
| 8 | 运行时 CMP 更新用 `cmp_idx` + `set_cmp_shadow_sel` | 每次 SetDuty 设对 CMPSHDWSEL，只动自己的 CMP | L151-154 |
| 9 | init 加 `pwm_deinit()` | 清除上电/bootloader 残留状态 | L268 |
| 10 | init 加 `pwm_start_counter()` | init 阶段启动 counter，应用层只管更新 CMP | L275 |
| 11 | init 加 `data->configured_channels = 0` | 复位通道追踪状态 | L276 |
| 12 | init 删 `enable_output=true`、`invert_output=false`、`pwm_config` | 这些在 init 中有人设没人用，死代码 | L260-278 |
| 13 | TRIG_ENABLE 分支用 `set_cmp_shadow_sel` | 同上，替代 `pwm_load_cmp_shadow_on_capture` | L81 |
| 14 | PWMv2 代码简化 | 去冗余注释和 {}，保持功能一致 | L160-213 |

---

## 改动 1：configured_channels

**文件：** `pwm_hpmicro.c` L42-44

```c
// 原版
struct pwm_hpmicro_data {
    uint32_t period_cycles[1];
};

// 新版
struct pwm_hpmicro_data {
    uint32_t period_cycles[1];
    uint32_t configured_channels;
};
```

**原因：** 原版 SDK 没有追踪哪些通道已经初始化过。每次 set_cycles 都重复执行完整的 `pwm_setup_waveform()`，后初始化的通道会覆盖前一个的配置。

---

## 改动 2：set_cmp_shadow_sel

**文件：** `pwm_hpmicro.c` L47-51

```c
// 新增函数
static inline void set_cmp_shadow_sel(HPM_PWM_BASE_TYPE *pwm, uint8_t ch)
{
    pwm->GCR = (pwm->GCR & ~PWM_GCR_CMPSHDWSEL_MASK)
         | PWM_GCR_CMPSHDWSEL_SET(ch);
}
```

**原因：** SDK 原版使用 `pwm_load_cmp_shadow_on_capture(pwm, index, 0)`，"看起来"像是设 CMPSHDWSEL，但内部有副作用：

```c
// SDK 原版实现（hpm_pwm_drv.h）
static inline void pwm_load_cmp_shadow_on_capture(PWM_Type *pwm_x,
                                                   uint8_t index,
                                                   bool is_falling_edge)
{
    pwm_x->CMPCFG[index] |= PWM_CMPCFG_CMPMODE_MASK;  // CMPMODE=1！切到 input capture
    pwm_x->GCR = ... | PWM_GCR_CMPSHDWSEL_SET(index) | ...;
}
```

CMPMODE=1 将比较器从 output compare 切到 input capture，PWM 输出停止。

`set_cmp_shadow_sel` 只写 GCR.CMPSHDWSEL，**不动 CMPCFG**。

---

## 改动 3：per-channel CMP

**文件：** `pwm_hpmicro.c` L65

```c
// 原版 — setup_waveform 和 config_cmp 全部用 channel 索引
if (status_success != pwm_setup_waveform(pwm_base, channel, &pwm_config, channel, ...));
pwm_config_cmp(pwm_base, channel, &cmp_config[1]);

// 新版 — 统一使用 cmp_idx = channel
uint8_t cmp_idx = channel;
// ...
if (status_success != pwm_setup_waveform(pwm_base, channel, &pwm_config, cmp_idx, ...));
pwm_config_cmp(pwm_base, cmp_idx, &cmp_config[1]);
```

**原因：** 原版 set_cycles 中 setup_waveform 等 CMP 操作使用 `PWM_SOC_CMP_MAX_COUNT - 1`（CMP[23]），多通道共用同一比较器，互相覆盖。

改为每个通道使用自己索引的比较器：ch5→CMP[5]，ch7→CMP[7]。

---

## 改动 4：pulse=0 修复

**文件：** `pwm_hpmicro.c` L118-120

```c
// 新增
if (pulse_cycles == 0) {
    rld_cmp = rld + 1;
}
```

**原因：** pulse=0 时 `rld_cmp = period_cycles - 0 = period_cycles = rld`。当 `CMP == RLD` 时，PWM 输出固定高电平（100%），不是 0%。

设为 `rld_cmp = rld + 1`，使 `CMP > RLD`，comparator 输出始终不触发，实现 0% 占空比。

---

## 改动 5：first-init 守卫

**文件：** `pwm_hpmicro.c` L122

```c
// 原版 — 每次 prld 变化都走完整初始化
if (prld != ((PWM_RLD_XRLD_GET(pwm_base->RLD) << 24) | PWM_RLD_RLD_GET(pwm_base->RLD))) {
    // setup_waveform + config_cmp + start_counter
    // ...所有配置...
}

// 新版 — configured_channels 控制首/次通道路径
if (((data->configured_channels & (1UL << channel)) == 0U)) {
    // 首通道：setup_waveform + config_cmp
    // ...
    data->configured_channels |= (1UL << channel);
} else if (prld != ...) {
    // 后续通道或周期变化：只更新 reload
    pwm_set_reload(pwm_base, xrld, rld);
}
```

**原因：** 原版逻辑用 `prld` 是否变化判断是否首次配置。每次 set_cycles 时，如果 RLD 不变就直接跳过所有配置——多通道时后面 init 的通道永远跳过了自己的配置。

新版用 `configured_channels` 位图追踪：每个通道首次 set_cycles 时执行完整的 `pwm_setup_waveform`（配 OEN、CMPSEL、CMP shadow），之后只更新 CMP 值。

---

## 改动 6：首通道用 cmp_idx

**文件：** `pwm_hpmicro.c` L137-142

```c
// 原版
pwm_setup_waveform(pwm_base, channel, &pwm_config, channel, &cmp_config[0], 1);
pwm_load_cmp_shadow_on_capture(pwm_base, PWM_SOC_CMP_MAX_COUNT - 1, 0);
pwm_config_cmp(pwm_base, PWM_SOC_CMP_MAX_COUNT - 1, &cmp_config[1]);

// 新版
pwm_setup_waveform(pwm_base, channel, &pwm_config, cmp_idx, &cmp_config[0], 1);
set_cmp_shadow_sel(pwm_base, cmp_idx);
pwm_config_cmp(pwm_base, cmp_idx, &cmp_config[1]);
```

变化：
- `pwm_load_cmp_shadow_on_capture` → `set_cmp_shadow_sel`（避免 CMPMODE 污染）
- 固定 CMP[23] → `cmp_idx`（per-channel CMP）

---

## 改动 7：非首通道 RLD 更新

**文件：** `pwm_hpmicro.c` L147-149

```c
// 新增 else if 分支
} else if (prld != ((PWM_RLD_XRLD_GET(pwm_base->RLD) << 24) | PWM_RLD_RLD_GET(pwm_base->RLD))) {
    pwm_set_reload(pwm_base, xrld, rld);
}
```

已配置过的通道再次调用 set_cycles 时，如果 RLD 变了就更新 reload，不变则跳过。不重新走 `pwm_setup_waveform`。

---

## 改动 8：运行时 CMP 更新

**文件：** `pwm_hpmicro.c` L151-154

```c
// 原版
pwm_shadow_register_unlock(pwm_base);
pwm_cmp_update_cmp_value(pwm_base, channel, rld_cmp, xrld_cmp);
pwm_issue_shadow_register_lock_event(pwm_base);

// 新版
pwm_shadow_register_unlock(pwm_base);
pwm_cmp_update_cmp_value(pwm_base, cmp_idx, rld_cmp, xrld_cmp);
set_cmp_shadow_sel(pwm_base, cmp_idx);        // 加这句
pwm_issue_shadow_register_lock_event(pwm_base);
```

**原因：** `pwm_issue_shadow_register_lock_event` 只加载当前 `CMPSHDWSEL` 指向的 CMP 的 shadow。不加 `set_cmp_shadow_sel(cmp_idx)` 的话，CMPSHDWSEL 还指向上一次 SetDuty 的通道，CMP 值写到错误的寄存器。

---

## 改动 9：init 加 pwm_deinit

**文件：** `pwm_hpmicro.c` L268

```c
// 新版 init
static int pwm_hpmicro_v1_init(const struct device *dev)
{
    // ...
    pwm_deinit(pwm_base);           // 新增：清除所有 PWM 寄存器
    err = pinctrl_apply_state(...);
    // ...
```

`pwm_deinit` 将所有 PWM 寄存器置为确定默认值：
- 清 IRQEN、DMAEN、SR
- CMP[i] 设最大值（禁止输出）
- CHCFG[i] 设 CMPSEL=最大
- PWMCFG[i] = 0（OEN=0，输出禁）
- GCR = 0，FRCMD = 0

**原因：** 正常 boot 时 PWM 寄存器残留 bootloader/上电随机状态。不先复位的话，多通道配置时部分寄存器状态不确定。

---

## 改动 10：init 加 pwm_start_counter

**文件：** `pwm_hpmicro.c` L275

```c
    pwm_set_reload(pwm_base, 0, freqc / config->period);
    pwm_set_start_count(pwm_base, 0, 0);
    pwm_start_counter(pwm_base);           // 新增
    data->configured_channels = 0;         // 新增
```

**原因：** counter 在 init 阶段启动，应用层 set_cycles 时 PWM 时钟域已在运行。各通道的 OC 状态机在 init 后稳定，后续只写 CMP/CMPSHDWSEL 即可。

---

## 改动 11：init 清理死代码

**文件：** `pwm_hpmicro.c` L260-278

```c
// 原版 init
static int pwm_hpmicro_v1_init(const struct device *dev)
{
    // ...
    pwm_config_t pwm_config;                    // 声明了但没用
    // ...
    pwm_get_default_pwm_config(pwm_base, &pwm_config);        // 读取了但没人用
    pwm_config.enable_output = true;            // 设了但后面不传
    pwm_config.dead_zone_in_half_cycle = ...;   // 设了但后面不传
    pwm_config.invert_output = false;           // 设了但后面不传
    // ...
}

// 新版 init
static int pwm_hpmicro_v1_init(const struct device *dev)
{
    const struct pwm_hpmicro_config *config = dev->config;
    struct pwm_hpmicro_data *data = dev->data;
    uint32_t freqc;
    HPM_PWM_BASE_TYPE *pwm_base = config->base;
    int err;

    pwm_deinit(pwm_base);
    err = pinctrl_apply_state(config->pincfg, PINCTRL_STATE_DEFAULT);
    if (err < 0) return err;

    freqc = clock_get_frequency(config->clock_name);
    pwm_set_reload(pwm_base, 0, freqc / config->period);
    pwm_set_start_count(pwm_base, 0, 0);
    pwm_start_counter(pwm_base);
    data->configured_channels = 0;
    return 0;
}
```

**原因：** 原版 init 中的 `pwm_config` 是局部变量，设了 `enable_output=true`、`dead_zone_in_half_cycle=...`、`invert_output=false`，但这些值从未被传给任何函数——写到局部变量就丢弃了，完全不起作用。

---

## 改动 12：TRIG_ENABLE 分支

**文件：** `pwm_hpmicro.c` L81

```c
// 原版
pwm_load_cmp_shadow_on_capture(pwm_base, PWM_SOC_CMP_MAX_COUNT - 1, 0);

// 新版
set_cmp_shadow_sel(pwm_base, PWM_SOC_CMP_MAX_COUNT - 1);
```

TRIG_ENABLE 分支也用了 `pwm_load_cmp_shadow_on_capture`，同样有 CMPMODE 污染问题。替换为 `set_cmp_shadow_sel`。

---

## 改动 13：PWMv2 代码简化

**文件：** `pwm_hpmicro.c` L160-213

将原版多层 `{}` 和详细注释简化，功能不变：
- `if (channel > 7) { return -ENOTSUP; }` → `if (channel > 7) return -ENOTSUP;`
- 移除 `/* Calculate counter and cmp index... */` 等冗余注释
- 条件语句从 `if { } else { }` 简化为单行

**原因：** 该项目未使用 PWMv2，保留代码但清理格式以减少维护负担。

---

## 总对比

| 维度 | 原版 SDK | 修改后 |
|------|----------|--------|
| 通道追踪 | 无 | `configured_channels` 位图 |
| CMP 分配 | 所有通道共享 CMP[23] | per-channel CMP[cmp_idx] |
| CMPSHDWSEL 设置 | `pwm_load_cmp_shadow_on_capture`（污染 CMPCFG） | `set_cmp_shadow_sel`（只写 GCR） |
| 首次初始化判断 | `prld` 值变化 | `configured_channels` 位检测 |
| 运行时 CMP 更新 | unlock → update → lock | unlock → update → set_cmpshdwsel → lock |
| 0% 占空比 | 不支持（CMP=RLD → 100%） | `rld_cmp = rld + 1`（CMP>RLD → 0%） |
| init PWM 复位 | 无 | `pwm_deinit` |
| init counter | 无 | `pwm_start_counter` |
| init dead code | `pwm_config` 设了没用 | 删除 |

---
## 当前源码

```c
/*
 * Copyright (c) 2022 hpmicro
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 */

#include <errno.h>
#include <zephyr/drivers/pwm.h>
#include <zephyr/drivers/clock_control.h>
#include <soc.h>
#include <zephyr/drivers/pinctrl.h>
#include "hpm_clock_drv.h"
#include "dt-bindings/pwm/hpmicro-pwm-common.h"
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(pwm_hpmicro, CONFIG_PWM_LOG_LEVEL);

#if CONFIG_DT_HAS_HPMICRO_HPM_PWM_ENABLED && CONFIG_DT_HAS_HPMICRO_HPM_PWMV2_ENABLED
#error "Cannot enable both PWM and PWMv2 in the same build"
#elif CONFIG_DT_HAS_HPMICRO_HPM_PWM_ENABLED
#include <hpm_pwm_drv.h>
#define DT_DRV_COMPAT hpmicro_hpm_pwm
#define HPM_PWM_BASE_TYPE PWM_Type
#elif CONFIG_DT_HAS_HPMICRO_HPM_PWMV2_ENABLED
#include <hpm_pwmv2_drv.h>
#define DT_DRV_COMPAT hpmicro_hpm_pwmv2
#define HPM_PWM_BASE_TYPE PWMV2_Type
#else
#error "No PWM peripheral enabled"
#endif

struct pwm_hpmicro_config {
    HPM_PWM_BASE_TYPE *base;
    uint32_t clock_name;
    uint32_t period;
    uint32_t dead_zone_in_half_cycle;
    const struct pinctrl_dev_config *pincfg;
};

struct pwm_hpmicro_data {
    uint32_t period_cycles[1];
    uint32_t configured_channels;
};

#if CONFIG_DT_HAS_HPMICRO_HPM_PWM_ENABLED
static inline void set_cmp_shadow_sel(HPM_PWM_BASE_TYPE *pwm, uint8_t ch)
{
    pwm->GCR = (pwm->GCR & ~PWM_GCR_CMPSHDWSEL_MASK)
         | PWM_GCR_CMPSHDWSEL_SET(ch);
}

static int hpmicro_pwm_v1_set_cycles(const struct device *dev, uint32_t channel,
                       uint32_t period_cycles, uint32_t pulse_cycles,
                       pwm_flags_t flags)
{
    const struct pwm_hpmicro_config *config = dev->config;
    struct pwm_hpmicro_data *data = dev->data;
    pwm_config_t pwm_config;
    pwm_cmp_config_t cmp_config[2] = {0};
    HPM_PWM_BASE_TYPE *pwm_base = config->base;
    uint32_t rld = 0, xrld = 0, prld = 0;
    uint32_t rld_cmp = 0, xrld_cmp = 0;
    uint16_t i, j;
    uint8_t cmp_idx = channel;

    pwm_get_default_pwm_config(pwm_base, &pwm_config);
    if (flags == PWM_POLARITY_INVERTED) {
        pwm_config.invert_output = true;
    } else if (flags == PWM_POLARITY_NORMAL) {
        pwm_config.invert_output = false;
    } else if (flags == PWM_TRIG_ENABLE) {
        cmp_config[0].enable_ex_cmp  = false;
        cmp_config[0].mode = pwm_cmp_mode_output_compare;
        cmp_config[0].cmp = period_cycles + 1;
        cmp_config[0].update_trigger = pwm_shadow_register_update_on_hw_event;
        cmp_config[1].mode = pwm_cmp_mode_output_compare;
        cmp_config[1].cmp = period_cycles;
        cmp_config[1].update_trigger = pwm_shadow_register_update_on_modify;
        pwm_config_cmp(pwm_base, channel, &cmp_config[0]);
        set_cmp_shadow_sel(pwm_base, PWM_SOC_CMP_MAX_COUNT - 1);
        pwm_config_cmp(pwm_base, PWM_SOC_CMP_MAX_COUNT - 1, &cmp_config[1]);
        pwm_start_counter(pwm_base);
        pwm_issue_shadow_register_lock_event(pwm_base);
        return 0;
    } else {
        return -ENOTSUP;
    }

    if (period_cycles >  0xffffff) {
        for (i = 1; i <= 16; i++) {
            if ((period_cycles / (i + 1)) <= 0xffffff) {
                rld = period_cycles / (i + 1);
                xrld = i;
                prld = (xrld << 24) | rld;
                for (j = 0; j <= 16; j++) {
                    if (((period_cycles - pulse_cycles) / (j + 1)) <= 0xffffff) {
                        rld_cmp = (period_cycles - pulse_cycles) / (j + 1);
                        xrld_cmp = j;
                        break;
                    } else if (j >= 16) {
                        return -ENOTSUP;
                    }
                }
                break;
            } else if (i >= 16) {
                return -ENOTSUP;
            }
        }
    } else {
        rld = period_cycles;
        xrld = 0;
        prld = period_cycles;
        rld_cmp = period_cycles - pulse_cycles;
        xrld_cmp = 0;
    }

    if (pulse_cycles == 0) {
        rld_cmp = rld + 1;
    }

    if (((data->configured_channels & (1UL << channel)) == 0U)) {

        pwm_config.enable_output = true;

        cmp_config[0].enable_ex_cmp  = true;
        cmp_config[0].mode = pwm_cmp_mode_output_compare;
        cmp_config[0].cmp = rld + 1;
        cmp_config[0].ex_cmp = xrld;
        cmp_config[0].update_trigger = pwm_shadow_register_update_on_modify;
        cmp_config[1].enable_ex_cmp  = true;
        cmp_config[1].mode = pwm_cmp_mode_output_compare;
        cmp_config[1].cmp = rld;
        cmp_config[1].ex_cmp = xrld;
        cmp_config[1].update_trigger = pwm_shadow_register_update_on_modify;

        if (status_success != pwm_setup_waveform(pwm_base, channel, &pwm_config, cmp_idx, &cmp_config[0], 1)) {
            LOG_ERR("failed to setup waveform\n");
            return -ENOTSUP;
        }
        set_cmp_shadow_sel(pwm_base, cmp_idx);
        pwm_config_cmp(pwm_base, cmp_idx, &cmp_config[1]);
        pwm_issue_shadow_register_lock_event(pwm_base);
        data->configured_channels |= (1UL << channel);


    } else if (prld != ((PWM_RLD_XRLD_GET(pwm_base->RLD) << 24) | PWM_RLD_RLD_GET(pwm_base->RLD))) {
        pwm_set_reload(pwm_base, xrld, rld);
    }

    pwm_shadow_register_unlock(pwm_base);
    pwm_cmp_update_cmp_value(pwm_base, cmp_idx, rld_cmp, xrld_cmp);
    set_cmp_shadow_sel(pwm_base, cmp_idx);
    pwm_issue_shadow_register_lock_event(pwm_base);

    return 0;
}
#endif

#if CONFIG_DT_HAS_HPMICRO_HPM_PWMV2_ENABLED
static int hpmicro_pwm_v2_set_cycles(const struct device *dev, uint32_t channel,
                       uint32_t period_cycles, uint32_t pulse_cycles,
                       pwm_flags_t flags)
{
    const struct pwm_hpmicro_config *config = dev->config;
    HPM_PWM_BASE_TYPE *pwm_base = config->base;
    uint32_t rld, xrld, prld, rld_cmp, xrld_cmp;
    uint16_t i, j;

    if (channel > 7) return -ENOTSUP;
    pwm_counter_t counter = (pwm_counter_t)(channel >> 1);
    uint8_t cmp_start_index = (channel >> 1) << 2;
    uint8_t cmp_index1 = cmp_start_index + ((channel & 0x01) << 1);
    uint8_t cmp_index2 = cmp_index1 + 1;
    uint8_t shadow_reload = counter * 5;
    uint8_t shadow_cmp1 = shadow_reload + 1 + ((channel & 0x01) << 1);
    uint8_t shadow_cmp2 = shadow_cmp1 + 1;

    bool invert_output = false;
    if (flags == PWM_POLARITY_INVERTED) invert_output = true;
    else if (flags == PWM_POLARITY_NORMAL) invert_output = false;
    else return -ENOTSUP;

    if (period_cycles > 0xffffff) return -ENOTSUP;
    rld = period_cycles; xrld = 0; prld = period_cycles;
    rld_cmp = period_cycles - pulse_cycles; xrld_cmp = 0;

    pwmv2_shadow_register_unlock(pwm_base);
    pwmv2_set_shadow_val(pwm_base, shadow_reload, rld, xrld, false);
    pwmv2_set_shadow_val(pwm_base, shadow_cmp1, 0, 0, false);
    pwmv2_set_shadow_val(pwm_base, shadow_cmp2, rld_cmp, xrld_cmp, false);
    pwmv2_counter_select_data_offset_from_shadow_value(pwm_base, counter, shadow_reload);
    pwmv2_counter_burst_disable(pwm_base, counter);
    pwmv2_set_reload_update_time(pwm_base, counter, pwm_reload_update_on_reload);
    pwmv2_select_cmp_source(pwm_base, cmp_index1, cmp_value_from_shadow_val, shadow_cmp1);
    pwmv2_select_cmp_source(pwm_base, cmp_index2, cmp_value_from_shadow_val, shadow_cmp2);
    pwmv2_cmp_update_trig_time(pwm_base, cmp_index1, pwm_shadow_register_update_on_reload);
    pwmv2_cmp_update_trig_time(pwm_base, cmp_index2, pwm_shadow_register_update_on_reload);
    if (invert_output) pwmv2_enable_output_invert(pwm_base, (pwm_channel_t)channel);
    else pwmv2_disable_output_invert(pwm_base, (pwm_channel_t)channel);
    if (config->dead_zone_in_half_cycle > 0)
        pwmv2_set_dead_area(pwm_base, (pwm_channel_t)channel, config->dead_zone_in_half_cycle);
    pwmv2_shadow_register_lock(pwm_base);
    if (channel & 0x01) {
        if (pwmv2_get_cmp_working_status(pwm_base, (pwm_channel_t)(channel - 1)) == 0xFFFFFF00)
            pwmv2_enable_four_cmp(pwm_base, (pwm_channel_t)(channel - 1));
        else pwmv2_disable_four_cmp(pwm_base, (pwm_channel_t)(channel - 1));
    } else pwmv2_disable_four_cmp(pwm_base, (pwm_channel_t)channel);
    pwmv2_channel_enable_output(pwm_base, (pwm_channel_t)channel);
    pwmv2_enable_counter(pwm_base, counter);
    pwmv2_start_pwm_output(pwm_base, counter);
    return 0;
}
#endif

static int hpmicro_pwm_set_cycles(const struct device *dev, uint32_t channel,
                       uint32_t period_cycles, uint32_t pulse_cycles,
                       pwm_flags_t flags)
{
#if CONFIG_DT_HAS_HPMICRO_HPM_PWM_ENABLED
    return hpmicro_pwm_v1_set_cycles(dev, channel, period_cycles, pulse_cycles, flags);
#elif CONFIG_DT_HAS_HPMICRO_HPM_PWMV2_ENABLED
    return hpmicro_pwm_v2_set_cycles(dev, channel, period_cycles, pulse_cycles, flags);
#endif
}

#if CONFIG_DT_HAS_HPMICRO_HPM_PWM_ENABLED
static int hpmicro_pwm_v1_get_cycles_per_sec(const struct device *dev,
                       uint32_t channel, uint64_t *cycles)
{
    const struct pwm_hpmicro_config *config = dev->config;
    uint32_t freqc = clock_get_frequency(config->clock_name);
    *cycles = freqc;
    return 0;
}
#endif

#if CONFIG_DT_HAS_HPMICRO_HPM_PWMV2_ENABLED
static int hpmicro_pwm_v2_get_cycles_per_sec(const struct device *dev,
                       uint32_t channel, uint64_t *cycles)
{
    const struct pwm_hpmicro_config *config = dev->config;
    uint32_t freqc = clock_get_frequency(config->clock_name);
    *cycles = freqc;
    return 0;
}
#endif

static int hpmicro_pwm_get_cycles_per_sec(const struct device *dev,
                       uint32_t channel, uint64_t *cycles)
{
#if CONFIG_DT_HAS_HPMICRO_HPM_PWM_ENABLED
    return hpmicro_pwm_v1_get_cycles_per_sec(dev, channel, cycles);
#elif CONFIG_DT_HAS_HPMICRO_HPM_PWMV2_ENABLED
    return hpmicro_pwm_v2_get_cycles_per_sec(dev, channel, cycles);
#endif
}

#if CONFIG_DT_HAS_HPMICRO_HPM_PWM_ENABLED
static int pwm_hpmicro_v1_init(const struct device *dev)
{
    const struct pwm_hpmicro_config *config = dev->config;
    struct pwm_hpmicro_data *data = dev->data;
    uint32_t freqc;
    HPM_PWM_BASE_TYPE *pwm_base = config->base;
    int err;

    pwm_deinit(pwm_base);
    err = pinctrl_apply_state(config->pincfg, PINCTRL_STATE_DEFAULT);
    if (err < 0) return err;

    freqc = clock_get_frequency(config->clock_name);
    pwm_set_reload(pwm_base, 0, freqc / config->period);
    pwm_set_start_count(pwm_base, 0, 0);
    pwm_start_counter(pwm_base);
    data->configured_channels = 0;
    return 0;
}
#endif

#if CONFIG_DT_HAS_HPMICRO_HPM_PWMV2_ENABLED
static int pwm_hpmicro_v2_init(const struct device *dev)
{
    const struct pwm_hpmicro_config *config = dev->config;
    HPM_PWM_BASE_TYPE *pwm_base = config->base;
    int err;
    err = pinctrl_apply_state(config->pincfg, PINCTRL_STATE_DEFAULT);
    if (err < 0) return err;
    pwmv2_deinit(pwm_base);
    return 0;
}
#endif

static int pwm_hpmicro_init(const struct device *dev)
{
#if CONFIG_DT_HAS_HPMICRO_HPM_PWM_ENABLED
    return pwm_hpmicro_v1_init(dev);
#elif CONFIG_DT_HAS_HPMICRO_HPM_PWMV2_ENABLED
    return pwm_hpmicro_v2_init(dev);
#endif
}

static const struct pwm_driver_api pwm_hpmicro_driver_api = {
    .set_cycles = hpmicro_pwm_set_cycles,
    .get_cycles_per_sec = hpmicro_pwm_get_cycles_per_sec,
};

#define PWM_DEVICE_INIT_HPMICRO(n)              \
    static struct pwm_hpmicro_data pwm_hpmicro_data_##n;          \
    PINCTRL_DT_INST_DEFINE(n);                      \
    static const struct pwm_hpmicro_config pwm_hpmicro_config_##n = {     \
        .base = (HPM_PWM_BASE_TYPE *)DT_INST_REG_ADDR(n),      \
        .clock_name = DT_INST_CLOCKS_CELL(n, name),        \
        .pincfg = PINCTRL_DT_INST_DEV_CONFIG_GET(n),          \
        .period = DT_INST_PROP(n, period_init),            \
        .dead_zone_in_half_cycle = DT_INST_PROP(n, dead_zone_in_half_cycle),    \
    };                                  \
    DEVICE_DT_INST_DEFINE(n,                      \
                    pwm_hpmicro_init,              \
                    NULL,                      \
                    &pwm_hpmicro_data_##n,              \
                    &pwm_hpmicro_config_##n,              \
                    POST_KERNEL, CONFIG_KERNEL_INIT_PRIORITY_DEVICE,\
                    &pwm_hpmicro_driver_api);

DT_INST_FOREACH_STATUS_OKAY(PWM_DEVICE_INIT_HPMICRO)```
