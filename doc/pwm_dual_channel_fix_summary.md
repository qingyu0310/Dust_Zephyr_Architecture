# PWM 双通道冲突 — 问题总结

## 现象

同一个 PWM 控制器下启用两个通道时，**只有偶数通道（ch2、ch5）工作，奇数通道（ch3、ch7）无输出**。第一次烧录正常，reset/重新上电后失效。只配单通道时完全正常。

## 根因

### 1. 驱动初始化不完整

`pwm_hpmicro_v1_init` 没有复位所有 PWM 寄存器。正常 boot 时 PWM 寄存器处于不确定状态，驱动在此之上配置双通道，一路异常。烧录器连接时 PWM 外设被硬件复位，寄存器是确定的默认值，所以正常工作。

### 2. 所有 channel 共用同一个 CMP

驱动中所有 channel 共用 `PWM_SOC_CMP_MAX_COUNT - 1`（CMP[23]）作为 reload 比较器。多 channel 同时使用时，同一个 CMP 寄存器被反复配置，导致资源竞争。

## 改动 1：init 加 deinit

**文件：** `D:\Zephyr_HPMicro\sdk_glue\drivers\pwm\pwm_hpmicro.c`

```c
static int pwm_hpmicro_v1_init(const struct device *dev)
{
    const struct pwm_hpmicro_config *config = dev->config;
    pwm_config_t pwm_config;
    uint32_t freqc;
    HPM_PWM_BASE_TYPE *pwm_base = config->base;
    int err;

    /* ===== [CUSTOM] 强制复位 PWM 寄存器 ====================
     * 目的：正常 boot 时 PWM 寄存器残留未知状态，导致
     *       双通道同时启用时共享寄存器被覆盖，一路无输出。
     *       烧录器连接时外设被硬件复位所以正常，reset/上电时没有。
     * 改动：2026-07-31
     * ====================================================*/
    pwm_deinit(pwm_base);

    err = pinctrl_apply_state(config->pincfg, PINCTRL_STATE_DEFAULT);
    // ... 后续初始化不变 ...
```

## 改动 2：各 channel 使用独立 CMP

**文件：** `D:\Zephyr_HPMicro\sdk_glue\drivers\pwm\pwm_hpmicro.c`

**修改前（所有 channel 共享 CMP[23]）：**

```c
if (data->configured_channels == 0) {
    pwm_load_cmp_shadow_on_capture(pwm_base, PWM_SOC_CMP_MAX_COUNT - 1, 0);
    pwm_config_cmp(pwm_base, PWM_SOC_CMP_MAX_COUNT - 1, &cmp_config[1]);
    pwm_start_counter(pwm_base);
}
```

**修改后（各 channel 使用自己的 CMP，每 channel 都配自身 CMP）：**

```c
if (data->configured_channels == 0) {
    pwm_load_cmp_shadow_on_capture(pwm_base, channel, 0);
    pwm_start_counter(pwm_base);
}
pwm_config_cmp(pwm_base, channel, &cmp_config[1]);
```

变化：
- `PWM_SOC_CMP_MAX_COUNT - 1`（CMP[23]）→ `channel`（CMP[2]/CMP[3]）
- `pwm_config_cmp` 移出 `if` 守卫，每个 channel 都配置自己的 reload CMP
- GCR 写入和 counter 启动仍在首个 channel 执行一次

两处改动均带 `[CUSTOM]` 标记，与 SDK 原始内容区分。

## 验证

| 场景 | 结果 |
|------|------|
| pwm1 ch2 + ch3 同时输出 | 两路正常 |
| pwm0 ch5 + ch7（蜂鸣器+加热器）同时输出 | 两路正常 |
| 按 reset | 正常 |
| 断电重上电 | 正常 |
| 单通道回退 | 不受影响 |
