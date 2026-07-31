# PWM 双通道冲突修复记录

## 问题

PWM0 同时配置 ch5（PA09, buzzer）和 ch7（PB07, heater）时，只有第一个 pin 有输出。

## 根因

HPMicro pinctrl 驱动 `pinctrl_soc.h` 的 `Z_PINCTRL_STATE_PINS_INIT` 宏：

```c
#define Z_PINCTRL_STATE_PINS_INIT(node_id, prop)	\
	{DT_FOREACH_CHILD_VARGS(DT_PHANDLE(node_id, prop),	\
	DT_FOREACH_PROP_ELEM, pinmux,	\
	Z_PINCTRL_STATE_PIN_INIT)}
```

`DT_PHANDLE(node_id, prop)` 只取 `pinctrl-0` 的第 0 个 phandle。写 `pinctrl-0 = <&a &b>` 时第二个 phandle 被忽略。

## 修改清单

### 1. pinctrl DTSI — 新增合并 pinmux 节点

`D:\Zephyr_HPMicro\sdk_glue\boards\hpmicro\hpm5361icb\hpm5361icb-pinctrl.dtsi`

新增 `pinmux_pwm0_p57` 节点，PA09 + PB07 合到同一个 `group0.pinmux` 数组里。

### 2. overlay — 改为引用合并节点

`d:\Zephyr\projects\tflm\project\boards\hpm\hpm5361icb\hpm5361icb.overlay`

```
- pinctrl-0 = <&pinmux_pwm0_p7 &pinmux_pwm0_p5>;
+ pinctrl-0 = <&pinmux_pwm0_p57>;
```

### 3. PWM 驱动 — 清理调试代码

`D:\Zephyr_HPMicro\sdk_glue\drivers\pwm\pwm_hpmicro.c`
- 移除通道 init 的 LOG_DBG 全寄存器 dump
- 移除 PWM init 首行的 LOG_DBG

### 4. pinctrl 驱动 — 清理调试代码 + 移除 read-back

`D:\Zephyr_HPMicro\sdk_glue\drivers\pinctrl\pinctrl_hpmicro.c`
- 移除调试用的 LOG_INF
- 移除 read-back barrier 测试代码
- 移除 LOG_MODULE_REGISTER
- 恢复原始代码

## 验证

- 双通道同时初始化，ch5 和 ch7 都正常输出
- IOC 寄存器：PA09 和 PB07 的 FUNC_CTL 均为 0x10（ALT=16）
- 反复烧录 10 次以上无异常

## 教训

- HPMicro pinctrl-0 不支持多 phandle 写法，多个 pin 必须放在同一个节点里
- 调试流程：PWM 寄存器 dump → IOC 寄存器 dump → 引脚顺序验证 → pinctrl pin_cnt 日志 → 发现宏不解析多 phandle
