# PWM ch5（蜂鸣器）+ ch7（加热器）冲突

## 现象

PWM0 同时配置 ch5（PA09, 蜂鸣器）和 ch7（PB07, IMU 加热器）时，输出不稳定：

- ~50% 概率两通道都正常
- ~50% 概率只有第一个初始化的通道有输出
- 单通道各自都正常

## 根因

**HPMicro pinctrl_soc.h 的 `Z_PINCTRL_STATE_PINS_INIT` 宏用 `DT_PHANDLE` 只解析 `pinctrl-0` 的第一个 phandle，后续 phandle 被忽略。**

```dts
pinctrl-0 = <&pinmux_a &pinmux_b>;  // 只配了 pinmux_a，pinmux_b 丢了
```

这是 HPMicro 该宏的实现限制——`DT_PHANDLE()` 只取第 0 个 phandle。不是 Zephyr 框架不支持，是 HPMicro 没实现多 phandle。

## 修复

两个 pin 合并到一个 pinmux 节点，`pinctrl-0` 只引用一个节点：

**pinctrl dtsi** 新增：
```dts
pinmux_pwm0_p57: pinmux_pwm0_p57 {
    group0 {
        pinmux = <HPMICRO_PINMUX(HPMICRO_PIN(HPMICRO_PORTA, 9), IOC_TYPE_IOC, 0, 16)>,
                 <HPMICRO_PINMUX(HPMICRO_PIN(HPMICRO_PORTB, 7), IOC_TYPE_IOC, 0, 16)>;
    };
};
```

**overlay** 改为引用合并节点：
```dts
&pwm0 {
    pinctrl-0 = <&pinmux_pwm0_p57>;
};
```

这种写法在 BSP 中已有先例（uart0、mcan0、gpiob_spi 等）。

## 排查过程

| 步骤 | 方法 | 发现 |
|------|------|------|
| PWM 寄存器 dump | trd_test.cpp 加 dump_regs() | 正常/异常组寄存器值完全一致 |
| IOC 寄存器 dump | 加 IOC FUNC_CTL 读取 | 异常组 PB07=0x00（GPIO 模式） |
| 单通道对比 | heater-only 烧录 | 单通道时 PB07 正常 |
| 引脚顺序实验 | 交换 pinctrl-0 顺序 | 第一个永远正常，第二个永远异常 |
| pinctrl 日志 | pinctrl 循环加 LOG_INF | PWM 的 pin_cnt=1，第二个没生成 |
| 宏分析 | 查 pinctrl_soc.h | `DT_PHANDLE` 只取第 0 个 phandle |

## 涉及文件

| 文件 | 改动 |
|------|------|
| `sdk_glue/boards/.../hpm5361icb-pinctrl.dtsi` | 新增 `pinmux_pwm0_p57` 合并节点 |
| `project/boards/.../hpm5361icb.overlay` | `pinctrl-0` 改为 `<&pinmux_pwm0_p57>` |
| `sdk_glue/drivers/pwm/pwm_hpmicro.c` | 移除遗留调试打印 |
| `doc/pwm_pinctrl_fix_changelog.md` | 变更记录 |
| `doc/pwm_pinctrl_rootcause.md` | 根因摘要+全记录 |

## 注意事项

HPMicro 的 pinctrl-0 **不要写成 `<&a &b>` 多 phandle 格式**，多个 pin 必须放在同一个节点内。
