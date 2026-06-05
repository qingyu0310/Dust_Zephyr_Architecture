# HPM5361 IMU 加热 PWM 问题复盘与解决方案

## 1. 问题现象

在 `HPM5361ICB + ICM42688P` 这条链路上，IMU 加热引脚使用 `PB7`，目标是通过 `PWM0_P_7` 输出 PWM 驱动加热。

最初现象是：

- 把 `PB7` 配成普通 GPIO 时，可以直接拉高/拉低，说明硬件引脚本身是通的。
- 改回 PWM 后，不管占空比设成 `0`、`1`，还是中间值，`PB7` 都一直表现为低电平。
- 设备树、引脚复用、业务层 `pwm_set_*` 调用表面上都没有明显报错。

这说明问题不在硬件本体，而在 PWM 软件链路。

## 2. 确认过的正确配置

最终确认下面这些配置是对的：

### 2.1 引脚复用

`PB7` 对应 HPM5361 底层复用为：

```text
IOC_PB07_FUNC_CTL_PWM0_P_7
```

也就是说它对应的是：

```text
PWM0 channel 7
```

不是 `channel 0`。

### 2.2 设备树绑定

当前板级配置位于：

- `projects/boards/hpm/hpm5361icb/hpm5361icb.overlay`

关键点是：

```dts
imu_pwm: imu_pwm {
    pwms = <&pwm0 7 PWM_USEC(1000) PWM_POLARITY_NORMAL>;
    label = "ICM42688P Heater";
    status = "okay";
};

&pwm0 {
    pinctrl-0 = <&pinmux_pwm0_p7>;
    pinctrl-names = "default";
    period-init = <1000>;
    dead-zone-in-half-cycle = <0>;
    status = "okay";
};
```

这里有两个关键点：

- `pwms = <&pwm0 7 ...>` 必须是 `channel 7`
- `pinctrl-0` 必须绑到 `pinmux_pwm0_p7`

## 3. 排查过程

### 3.1 先排除硬件问题

先把 `PB7` 临时改成普通 GPIO 输出测试：

- 直接拉高能生效
- 断电重启后依然能复现

结论：

- 板子焊接、走线、PB7 本身没有硬件性损坏

### 3.2 再排除通道号错误

开始时把设备树写成了：

```dts
pwms = <&pwm0 0 ...>
```

后来对照 HPM SDK 的 `iomux` 定义后发现：

- `PB7` 对应 `PWM0_P_7`
- 所以应该是 `channel 7`

改成：

```dts
pwms = <&pwm0 7 ...>
```

之后问题依旧存在，说明不只是通道号问题。

### 3.3 用 HPM 底层直配验证

为了进一步区分“Zephyr PWM 驱动问题”还是“HPM PWM 外设本身问题”，做了一个绕过 Zephyr 驱动的验证：

- 直接把 `PB7` 复用成 `IOC_PB07_FUNC_CTL_PWM0_P_7`
- 直接调用 HPM SDK 的：
  - `pwm_get_default_pwm_config()`
  - `pwm_setup_waveform()`
  - `pwm_start_counter()`

测试结果：

- 底层直配后，加热输出正常

结论：

- HPM PWM 外设本身没问题
- `PB7/PWM0_P_7` 硬件链路没问题
- 问题在 Zephyr `sdk_glue` 里的 `pwm_hpmicro.c`

## 4. 根因分析

问题文件：

- `D:\Zephyr_HPMicro\sdk_glue\drivers\pwm\pwm_hpmicro.c`

问题函数：

- `hpmicro_pwm_v1_set_cycles()`

### 4.1 错误逻辑

原逻辑里，只有在下面这个条件满足时，才会调用：

```c
pwm_setup_waveform(...)
```

条件大意是：

```c
if (当前周期 prld != PWM 外设里现有的 RLD) {
    pwm_setup_waveform(...);
}
```

这就带来一个问题：

- `pwm_hpmicro_v1_init()` 初始化时，已经根据 `period-init` 把 `RLD` 配好了
- 后面业务层第一次调用 `pwm_set_pulse_dt()` 时，如果运行时的 `period_cycles` 恰好和 `period-init` 一样
- 那么这个 `if` 条件不成立
- `pwm_setup_waveform()` 根本不会执行

结果就是：

- 比较值可能被更新了
- 但 PWM channel 从来没有真正完成首次输出配置
- 最终引脚一直保持低电平

### 4.2 为什么 HPM 底层直配正常

因为底层测试代码是明确执行了：

```c
pwm_setup_waveform(...)
```

所以 channel 7 的输出结构真正建起来了，波形能正常打出去。

## 5. 最终修复方案

### 5.1 修复思路

不能只靠“周期是否变化”来判断要不要做 `pwm_setup_waveform()`。

还必须额外判断：

```text
这个 channel 是不是第一次被使用
```

### 5.2 实际修改

在 `struct pwm_hpmicro_data` 里新增：

```c
uint32_t configured_channels;
```

用于记录哪些 channel 已经做过首次 waveform 配置。

然后把判断条件从：

```c
if (prld != current_rld) {
    ...
}
```

改成：

```c
if (((data->configured_channels & (1UL << channel)) == 0U) ||
    (prld != current_rld)) {
    ...
    pwm_setup_waveform(...);
    ...
    data->configured_channels |= (1UL << channel);
}
```

### 5.3 修复后的效果

修复后：

- 第一次使用某个 PWM channel 时，一定会执行 `pwm_setup_waveform()`
- 即使 `period-init` 和运行时周期完全一致，也不会漏掉首次输出配置
- `PB7 / PWM0 channel 7` 能正常输出 PWM

## 6. 业务层最终保留方案

业务层不再保留绕过 Zephyr 的 HPM 直配测试代码。

最终恢复为标准用法：

- `modules/imu/imu.cpp`

使用：

```cpp
static const pwm_dt_spec heater_pwm = PWM_DT_SPEC_GET(DT_ALIAS(imu_pwm));
heater_pwm_.init(heater_pwm);
heater_pwm_.SetDuty(...);
```

这样后续维护成本更低，也更符合当前工程结构。

## 7. 结论

这次问题的根因不是：

- 硬件坏了
- `PB7` 不支持 PWM
- `PWM0 channel 7` 配错了

真正根因是：

```text
Zephyr HPM PWM v1 驱动把“周期变化”误当成了“是否需要首次配置 channel 输出”的条件，
导致首次使用且周期未变化时，channel 没有执行 pwm_setup_waveform()。
```

最终解决办法是：

```text
给驱动增加 channel 首次配置状态记录，首次使用该 channel 时强制执行 pwm_setup_waveform()。
```

## 8. 后续建议

1. 后续如果再碰到 “PWM 配置看起来都对，但引脚始终不出波” 的情况，优先检查：
   - 通道号是否和具体引脚复用一致
   - 驱动是否真的执行了首次 waveform setup

2. 如果是 HPMicro 这类 PWM 外设，排查顺序建议固定为：
   - GPIO 直拉验证硬件
   - 校对 pinmux 和 channel 映射
   - HPM SDK 底层直配验证
   - 最后再回查 Zephyr `sdk_glue` 驱动逻辑

3. 如果后面要继续做 IMU 加热 PID 调参，建议以当前修复后的 Zephyr PWM 路径为正式链路，不要再长期保留 HPM 直配测试分支。
