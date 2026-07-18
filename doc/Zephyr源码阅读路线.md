# Zephyr 源码阅读路线

> 这份路线改成 **Zephyr 本体源码优先**。  
> 你的项目架构你当然熟，不需要我反复让你看自己写的东西。  
> 所以这里的主线是 `D:\Zephyr\zephyr`；`D:\Zephyr\projects\tflm` 只在最后作为“把 Zephyr 知识落回工程”的对照点。

---

## 目录

- [1. 阅读目标](#1-阅读目标)
- [2. 总路线](#2-总路线)
- [3. 第一层：启动与初始化](#3-第一层启动与初始化)
- [4. 第二层：线程、栈与调度](#4-第二层线程栈与调度)
- [5. 第三层：时间、等待与同步](#5-第三层时间等待与同步)
- [6. 第四层：设备模型与 Devicetree](#6-第四层设备模型与-devicetree)
- [7. 第五层：Kconfig 与 CMake](#7-第五层kconfig-与-cmake)
- [8. 第六层：驱动模型](#8-第六层驱动模型)
- [9. 第七层：zbus 子系统](#9-第七层zbus-子系统)
- [10. 第八层：中断、DMA、cache、链接](#10-第八层中断dmacache链接)
- [11. HPMicro 适配层怎么读](#11-hpmicro-适配层怎么读)
- [12. 回到当前工程时只看什么](#12-回到当前工程时只看什么)
- [13. 8 周阅读安排](#13-8-周阅读安排)
- [14. 暂时不要读什么](#14-暂时不要读什么)
- [15. 最终验收标准](#15-最终验收标准)

---

## 1. 阅读目标

这份路线不是让你复习 `tflm` 架构，而是让你读懂 Zephyr 本体：

1. Zephyr 从哪里接管 CPU？
2. `main()` 是怎么被 Zephyr 调起来的？
3. 线程对象、线程栈、优先级、调度器到底怎么工作？
4. `k_msleep()`、`k_sem_take()`、`k_sem_give()` 背后改变了什么内核状态？
5. `.dts/.overlay/Kconfig/CMake` 怎么生成最终的 C 宏、设备对象和编译结果？
6. `uart_rx_enable()`、`can_send()`、`pwm_set()` 这种 API 怎么落到芯片驱动？
7. 中断、DMA、cache、链接脚本为什么会影响实时行为？

一句话：

> 先把 Zephyr 的“启动、线程、设备、配置、驱动”五条主干读通，再把它们映射回你的工程。

---

## 2. 总路线

不要从 `D:\Zephyr\zephyr` 根目录按字母顺序硬啃。按下面 8 条主线读：

```text
启动初始化
  -> 线程与调度
  -> 时间与同步
  -> 设备模型与 Devicetree
  -> Kconfig / CMake / 生成文件
  -> 驱动模型
  -> zbus 子系统
  -> 中断 / DMA / cache / 链接
```

每条主线都按这个顺序追：

```text
公开头文件
  -> 核心实现文件
  -> 生成机制
  -> 架构/SoC/驱动适配
  -> 再回到当前工程找一个使用点验证
```

注意：**当前工程只做验证点，不做阅读起点。**

---

## 3. 第一层：启动与初始化

### 要读的 Zephyr 文件

1. `D:\Zephyr\zephyr\include\zephyr\init.h`
2. `D:\Zephyr\zephyr\kernel\init.c`
3. `D:\Zephyr\zephyr\include\zephyr\kernel.h`
4. `D:\Zephyr\zephyr\arch\riscv`

### 要读的 HPMicro 接入口

1. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\HPM5300\start.S`
2. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\HPM5300\soc.c`
3. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\HPM5300\linker.ld`

### 重点读什么

#### `kernel\init.c`

重点找：

- `z_cstart()`
- `z_sys_init_run_level()`
- `bg_thread_main()`
- main thread 创建和切换逻辑

你要知道：

- Zephyr 不是一上来就执行应用 `main()`。
- `z_cstart()` 是 Zephyr 内核接管后的核心入口。
- Zephyr 按 init level 初始化系统：

```text
EARLY
  -> PRE_KERNEL_1
  -> PRE_KERNEL_2
  -> POST_KERNEL
  -> APPLICATION
  -> main thread
```

- 驱动初始化、设备 ready、应用 `main()` 是三个不同阶段。

#### `init.h`

重点找：

- init level 的说明
- `SYS_INIT` 相关定义

你要知道：

- 很多驱动和子系统不是在 `main()` 里初始化，而是通过 `SYS_INIT()` 挂到某个 init level。
- 设备“还没 ready”时，不要先怀疑业务逻辑，要先回查 init level、DTS、Kconfig。

#### `start.S`

重点看：

- reset 后最早做了什么
- 栈如何准备
- `.data/.bss` 是否清理或搬运
- 最终怎么跳进 Zephyr C 入口

你要知道：

- `start.S` 是 SoC/架构接入 Zephyr 的桥。
- 它不是业务代码，但它决定了 C 环境什么时候可用。

### 读懂标准

你能回答这些问题就过关：

1. 为什么 MCU 上电不会直接进入 `main()`？
2. `z_cstart()` 在启动链里处于什么位置？
3. Zephyr init level 解决什么问题？
4. `SYS_INIT()` 和应用 `main()` 有什么区别？
5. 设备没 ready 时，为什么要先查 init level / DTS / Kconfig？

---

## 4. 第二层：线程、栈与调度

### 要读的 Zephyr 文件

1. `D:\Zephyr\zephyr\include\zephyr\kernel.h`
2. `D:\Zephyr\zephyr\include\zephyr\kernel\thread.h`
3. `D:\Zephyr\zephyr\include\zephyr\kernel\thread_stack.h`
4. `D:\Zephyr\zephyr\kernel\thread.c`
5. `D:\Zephyr\zephyr\kernel\sched.c`
6. `D:\Zephyr\zephyr\kernel\include\ksched.h`

### 重点读什么

#### `kernel.h`

重点找：

- `k_thread_create()`
- `k_thread_start()`
- `k_thread_abort()`
- `k_current_get()`
- `k_yield()`
- `k_sleep()`
- `k_msleep()`

你要知道：

- `kernel.h` 是应用层能看到的内核 API 总入口。
- 很多公开 API 最后会落到 `z_impl_xxx()` 实现。

#### `thread_stack.h`

重点找：

- `K_THREAD_STACK_DEFINE`
- `K_KERNEL_STACK_DEFINE`
- `K_THREAD_STACK_SIZEOF`
- `K_KERNEL_STACK_MEMBER`

你要知道：

- Zephyr 线程栈不是普通 C 数组那么简单。
- 栈可能带保护区、对齐、架构相关保留区域。
- 栈大小不是“你写多少就真实可用多少”，要看宏展开和架构要求。

#### `thread.c`

重点找：

- `z_impl_k_thread_create()`
- 线程初始化函数
- 线程状态设置
- entry、priority、stack、delay 如何进入 `struct k_thread`

你要知道：

- 创建线程的本质是构造一个内核线程对象。
- `entry` 不会神奇地“马上跑完”，它要进入调度器。
- 线程的优先级、栈、状态都保存在内核对象里。

#### `sched.c`

重点找：

- ready queue
- priority 比较
- reschedule
- context switch 触发条件

你要知道：

- 调度器决定“现在谁运行”。
- 高优先级 ready 线程通常会抢占低优先级线程。
- 睡眠、阻塞、等待事件都会让线程离开 ready 状态。

### 读懂标准

你能回答：

1. `k_thread_create()` 最少需要哪些东西？
2. 线程栈宏为什么不能简单当普通数组看？
3. Zephyr 里优先级数字越小还是越大越优先？
4. 线程创建后什么时候进入 ready queue？
5. `k_msleep()` 后线程状态发生了什么？

---

## 5. 第三层：时间、等待与同步

### 要读的 Zephyr 文件

1. `D:\Zephyr\zephyr\kernel\timeout.c`
2. `D:\Zephyr\zephyr\kernel\timer.c`
3. `D:\Zephyr\zephyr\kernel\sem.c`
4. `D:\Zephyr\zephyr\kernel\mutex.c`
5. `D:\Zephyr\zephyr\kernel\msg_q.c`
6. `D:\Zephyr\zephyr\include\zephyr\kernel.h`

### 重点读什么

#### timeout / sleep

重点找：

- timeout queue
- `k_sleep()`
- `k_msleep()`
- tick / timeout 转换

你要知道：

- `k_msleep()` 不是 CPU 原地空转。
- 它会让当前线程进入等待，调度器可以运行别的线程。
- 周期抖动来自调度、中断、高优先级线程、临界区和 timeout 精度。

#### semaphore

重点找：

- `k_sem_init()`
- `k_sem_take()`
- `k_sem_give()`
- wait queue

你要知道：

- 信号量适合“通知”和“计数”。
- 它不负责保存复杂数据。
- ISR 或驱动回调里常见做法是快速写 buffer，然后 `k_sem_give()` 唤醒线程。

#### mutex

重点找：

- owner
- lock count
- priority inheritance

你要知道：

- mutex 用于保护共享资源。
- 实时系统里 mutex 可能引入优先级反转，所以 Zephyr 需要 priority inheritance。

#### message queue

重点找：

- `k_msgq_put()`
- `k_msgq_get()`
- queue buffer
- timeout

你要知道：

- msgq 传递的是固定大小消息拷贝。
- 队列满/空/超时都会改变线程状态。

### 读懂标准

你能回答：

1. sleep、busy wait、blocked wait 有什么区别？
2. 信号量为什么适合通知，不适合直接保存数据流？
3. mutex 为什么可能影响实时性？
4. msgq 和 zbus 的思路有什么不同？
5. 周期线程抖动时，应该从哪些内核机制查？

---

## 6. 第四层：设备模型与 Devicetree

这是 Zephyr 最关键的一层。你要重点读，不要绕开。

### 要读的 Zephyr 文件

1. `D:\Zephyr\zephyr\include\zephyr\device.h`
2. `D:\Zephyr\zephyr\include\zephyr\devicetree.h`
3. `D:\Zephyr\zephyr\include\zephyr\drivers\gpio.h`
4. `D:\Zephyr\zephyr\include\zephyr\drivers\uart.h`
5. `D:\Zephyr\zephyr\include\zephyr\drivers\can.h`
6. `D:\Zephyr\zephyr\include\zephyr\drivers\pwm.h`
7. `D:\Zephyr\zephyr\scripts\dts\gen_defines.py`
8. `D:\Zephyr\zephyr\scripts\dts\python-devicetree\src\devicetree\edtlib.py`

### 要看的生成文件

如果当前 `build` 目录对应你要看的板子，可以看：

1. `D:\Zephyr\projects\tflm\build\zephyr\zephyr.dts`
2. `D:\Zephyr\projects\tflm\build\zephyr\include\generated\zephyr\devicetree_generated.h`

注意：`build` 是上一次构建结果，不是永恒真相。换板子或 overlay 后要重新生成。

### 重点读什么

#### `device.h`

重点找：

- `struct device`
- `DEVICE_DEFINE`
- `DEVICE_DT_DEFINE`
- `DEVICE_DT_INST_DEFINE`
- `DEVICE_DT_GET`
- `device_is_ready()`

你要知道：

- Zephyr 设备对象通常由编译期宏生成。
- `struct device` 一般包含 device name、config、data、api 等。
- `device_is_ready()` 是检查设备初始化结果，不是可有可无。

#### `devicetree.h`

重点找：

- `DT_NODELABEL()`
- `DT_ALIAS()`
- `DT_PATH()`
- `DT_PROP()`
- `DT_REG_ADDR()`
- `DT_IRQN()`
- `DT_INST_FOREACH_STATUS_OKAY()`

你要知道：

- Devicetree 在编译期变成 C 宏。
- 节点是否 `okay` 会影响驱动实例是否生成。
- `label`、`alias`、`chosen` 是不同概念。

#### DTS 合并链

要理解这条链：

```text
SoC .dtsi
  -> board .dts
  -> application .overlay
  -> zephyr.dts
  -> devicetree_generated.h
  -> DEVICE_DT_GET / DT_* 宏
  -> struct device
```

### 读懂标准

你能回答：

1. `.dtsi`、`.dts`、`.overlay` 各自负责什么？
2. `DT_NODELABEL(uart4)` 和 `DT_ALIAS(user_uart)` 有什么区别？
3. 为什么 `status = "disabled"` 会让驱动实例消失？
4. `DEVICE_DT_GET()` 拿到的到底是什么？
5. `device_is_ready()` 失败时，应该先看什么？

---

## 7. 第五层：Kconfig 与 CMake

Zephyr 很多问题不是代码错，而是配置没打开、文件没编译、符号没生成。

### 要读的 Zephyr 文件

1. `D:\Zephyr\zephyr\Kconfig`
2. `D:\Zephyr\zephyr\cmake\modules\kconfig.cmake`
3. `D:\Zephyr\zephyr\cmake\modules\dts.cmake`
4. `D:\Zephyr\zephyr\cmake\modules\boards.cmake`
5. `D:\Zephyr\zephyr\cmake\modules\hwm_v2.cmake`
6. `D:\Zephyr\zephyr\cmake\modules\zephyr_module.cmake`
7. `D:\Zephyr\zephyr\scripts\kconfig\kconfig.py`
8. `D:\Zephyr\zephyr\scripts\kconfig\kconfiglib.py`

### 要看的生成文件

1. `D:\Zephyr\projects\tflm\build\zephyr\.config`
2. `D:\Zephyr\projects\tflm\build\zephyr\include\generated\zephyr\autoconf.h`

### 重点读什么

#### Kconfig 基础

重点理解：

- `config`
- `menuconfig`
- `default`
- `depends on`
- `select`
- `imply`
- `choice`
- `rsource`

你要知道：

- `depends on` 是“这个符号能不能被打开”。
- `select` 是“我打开后强行打开别人”，不检查对方依赖是否合理。
- `default y` 不代表最终一定是 `y`。
- `.config` 才是最终配置结果。

#### CMake 基础

重点理解：

- `find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})`
- `target_sources`
- `zephyr_library_sources_ifdef`
- board / soc / module 加载顺序

你要知道：

- Kconfig 决定 `CONFIG_XXX`。
- CMake 根据 `CONFIG_XXX` 决定哪些 `.c/.cpp` 加入编译。
- 文件没被加入编译时，改代码不会进入固件。

### 读懂标准

你能回答：

1. `CONFIG_XXX` 从哪里来？
2. `.config` 和 `autoconf.h` 分别是什么？
3. `select` 和 `depends on` 的区别是什么？
4. 为什么 Kconfig warning 会阻断 Zephyr 构建？
5. 一个源文件为什么可能根本没进最终固件？

---

## 8. 第六层：驱动模型

驱动模型是 Zephyr 把“统一 API”和“具体芯片”接起来的地方。

### 要读的 Zephyr 文件

1. `D:\Zephyr\zephyr\include\zephyr\device.h`
2. `D:\Zephyr\zephyr\include\zephyr\drivers\uart.h`
3. `D:\Zephyr\zephyr\include\zephyr\drivers\can.h`
4. `D:\Zephyr\zephyr\include\zephyr\drivers\gpio.h`
5. `D:\Zephyr\zephyr\include\zephyr\drivers\pwm.h`
6. `D:\Zephyr\zephyr\drivers\serial`
7. `D:\Zephyr\zephyr\drivers\can`
8. `D:\Zephyr\zephyr\drivers\gpio`
9. `D:\Zephyr\zephyr\drivers\pwm`

### 重点读什么

#### 统一 API 头文件

以 UART 为例：

```text
include/zephyr/drivers/uart.h
  -> 定义上层可见 API
  -> 定义 driver api table
  -> 通过 struct device 找到底层实现
```

你要知道：

- `uart_rx_enable()` 不是直接操作寄存器。
- 它会通过 `struct device` 里的 API 表调用具体驱动实现。
- 同一个 Zephyr API，在不同芯片上对应不同 driver。

#### 驱动实例生成

典型链路：

```text
DTS compatible/status okay
  -> DT_INST_FOREACH_STATUS_OKAY()
  -> DEVICE_DT_INST_DEFINE()
  -> struct device
  -> driver api table
  -> 上层 API 调用
```

你要知道：

- 驱动实例通常是编译期生成。
- DTS 和 Kconfig 任一不匹配，都可能导致设备不可用。

### 读懂标准

你能回答：

1. Zephyr driver API table 是什么？
2. `struct device` 里的 config、data、api 各自干什么？
3. DTS 的 `compatible` 如何匹配到驱动？
4. 为什么同一个 `uart_rx_enable()` 可以在不同芯片上跑？
5. driver init 失败后上层会看到什么？

---

## 9. 第七层：zbus 子系统

zbus 是 Zephyr 的一个子系统，不是你的工程自创机制。你要读 Zephyr 实现本身。

### 要读的 Zephyr 文件

1. `D:\Zephyr\zephyr\include\zephyr\zbus\zbus.h`
2. `D:\Zephyr\zephyr\subsys\zbus\zbus.c`

### 重点读什么

#### `zbus.h`

重点找：

- `ZBUS_CHAN_DEFINE`
- `ZBUS_SUBSCRIBER_DEFINE`
- `ZBUS_OBSERVERS`
- `ZBUS_MSG_INIT`
- `zbus_chan_pub()`
- `zbus_chan_read()`
- `zbus_sub_wait()`

你要知道：

- channel 有固定消息类型。
- observer/subscriber 用来通知消费者。
- subscriber queue 保存的是通知，不等于保存多份完整业务消息。

#### `zbus.c`

重点找：

- channel lock
- message copy
- validator
- observer notify
- timeout handling

你要知道：

- `zbus_chan_pub()` 有锁、拷贝和通知成本。
- 发布频率高、消息体大、subscriber 队列小，都会影响实时行为。
- zbus 是解耦工具，但不是零成本魔法。

### 读懂标准

你能回答：

1. zbus channel 保存几份消息？
2. subscriber queue 里保存什么？
3. `zbus_chan_pub()` 会不会拷贝数据？
4. subscriber 队列满会发生什么？
5. zbus 和 msgq 最大区别是什么？

---

## 10. 第八层：中断、DMA、cache、链接

这一层不要一开始钻太深，但必须逐步建立底层感觉。

### 要读的 Zephyr / 架构文件

1. `D:\Zephyr\zephyr\include\zephyr\irq.h`
2. `D:\Zephyr\zephyr\arch\riscv`
3. `D:\Zephyr\zephyr\include\zephyr\linker`
4. `D:\Zephyr\zephyr\kernel\irq_offload.c`

### 要读的 HPMicro 文件

1. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\HPM5300\start.S`
2. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\HPM5300\soc.c`
3. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\HPM5300\linker.ld`
4. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\common\entry.ld`
5. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\common\ISR.ld`

### 重点读什么

#### 中断

你要知道：

- ISR 是硬件事件进来的入口。
- ISR 里应该尽量短：清标志、搬少量数据、唤醒线程。
- 复杂业务逻辑应放到线程里。

#### DMA

你要知道：

- DMA 是外设直接访问内存。
- buffer 地址、对齐、cache 属性可能影响是否正常。
- UART/USB/CAN 一旦涉及 DMA，就不能只看 API 调用。

#### cache

你要知道：

- CPU cache 和外设 DMA 看到的内存可能不一致。
- USB、DMA buffer 经常需要 nocache 区域或 cache 同步。

#### linker

你要知道：

- 代码、只读数据、全局变量、bss、栈、特殊 section 都由链接脚本安排。
- 链接脚本能直接影响运行时行为。

### 读懂标准

你能回答：

1. ISR 和线程的边界在哪里？
2. DMA 为什么会被 cache 影响？
3. 链接脚本如何影响栈和 buffer？
4. nocache 区域通常解决什么问题？
5. 为什么 USB/DMA 问题不能只在应用层找？

---

## 11. HPMicro 适配层怎么读

HPMicro `sdk_glue` 不是 Zephyr 主线，但它是当前 HPM 板子跑 Zephyr 的关键桥梁。

### 必读路径

| 路径 | 读什么 |
|---|---|
| `D:\Zephyr_HPMicro\sdk_glue\boards\hpmicro\hpm5361icb` | board、pin、runner、默认配置 |
| `D:\Zephyr_HPMicro\sdk_glue\dts\riscv\hpmicro\hpm5361.dtsi` | SoC 外设节点 |
| `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\HPM5300` | 启动、链接、SoC 初始化 |
| `D:\Zephyr_HPMicro\sdk_glue\drivers\serial\uart_hpmicro.c` | UART API 的 HPM 实现 |
| `D:\Zephyr_HPMicro\sdk_glue\drivers\can` | CAN API 的 HPM 实现 |
| `D:\Zephyr_HPMicro\sdk_glue\drivers\pwm\pwm_hpmicro.c` | PWM API 的 HPM 实现 |
| `D:\Zephyr_HPMicro\sdk_glue\drivers\usb` | USB UDC / CherryUSB 路径 |

### 阅读方法

每个外设都按这条链读：

```text
Zephyr 公开 API
  -> Zephyr driver API table
  -> HPMicro driver 实现
  -> DTS compatible / reg / irq / clocks / pinctrl
  -> HPM SDK 寄存器或 HAL 调用
```

### 读懂标准

你能回答：

1. HPM 的 UART driver 是怎么接到 Zephyr UART API 的？
2. DTS 里的 `compatible` 怎么选中 HPM driver？
3. HPM `soc.c` 在 Zephyr init 里做了什么？
4. HPM board `.dts` 和项目 `.overlay` 谁覆盖谁？
5. USB CherryUSB path 和 Zephyr UDC path 为什么要分开判断？

---

## 12. 回到当前工程时只看什么

这里才看你的工程，而且只看“Zephyr 知识落地的锚点”，不是让你重新学习自己写的架构。

### 启动映射

只看：

1. `D:\Zephyr\projects\tflm\src\main.c`
2. `D:\Zephyr\projects\tflm\project\apps\System_startup.cpp`

目的：

- 确认 Zephyr `main thread` 进入应用后，你如何组织业务初始化。
- 不在这里学 Zephyr 启动本体，Zephyr 启动本体在 `kernel\init.c`。

### 线程映射

只看：

1. `D:\Zephyr\projects\tflm\project\thread\thread.hpp`
2. 具体线程的 `trd_xxx.cpp`

目的：

- 把 `Thread<StackSize>` 对回 `k_thread_create()`。
- 看栈大小、优先级、入口函数、等待方式。

### 配置映射

只看：

1. `D:\Zephyr\projects\tflm\Kconfig`
2. `D:\Zephyr\projects\tflm\project\thread\Kconfig`
3. `D:\Zephyr\projects\tflm\CMakeLists.txt`

目的：

- 把工程功能开关对回 Zephyr Kconfig/CMake 机制。
- 不在这里学 Kconfig 语法本体，语法本体看 `D:\Zephyr\zephyr\scripts\kconfig` 和 Zephyr `Kconfig`。

### 数据映射

只看：

1. `D:\Zephyr\projects\tflm\topic`
2. 使用 `zbus_chan_pub()` / `zbus_sub_wait()` 的模块或线程

目的：

- 把 topic 用法对回 Zephyr zbus 本体。

### 驱动映射

只看：

1. `D:\Zephyr\projects\tflm\drivers\communication\uart\uart.cpp`
2. `D:\Zephyr\projects\tflm\drivers\communication\can\can.cpp`
3. `D:\Zephyr\projects\tflm\drivers\communication\usb\usb.cpp`

目的：

- 看你怎么调用 Zephyr API。
- 真正的底层行为回到 `D:\Zephyr\zephyr\include\zephyr\drivers\*.h` 和 HPM `sdk_glue` driver。

---

## 13. 8 周阅读安排

### 第 1 周：Zephyr 启动主线

读：

1. `D:\Zephyr\zephyr\include\zephyr\init.h`
2. `D:\Zephyr\zephyr\kernel\init.c`
3. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\HPM5300\start.S`
4. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\HPM5300\soc.c`

产出：

- 画出 `reset -> start.S -> z_cstart -> init levels -> main thread`。

### 第 2 周：线程与调度

读：

1. `D:\Zephyr\zephyr\include\zephyr\kernel.h`
2. `D:\Zephyr\zephyr\include\zephyr\kernel\thread_stack.h`
3. `D:\Zephyr\zephyr\kernel\thread.c`
4. `D:\Zephyr\zephyr\kernel\sched.c`

产出：

- 能解释线程创建、ready、blocked、sleep、抢占。

### 第 3 周：时间与同步

读：

1. `D:\Zephyr\zephyr\kernel\timeout.c`
2. `D:\Zephyr\zephyr\kernel\timer.c`
3. `D:\Zephyr\zephyr\kernel\sem.c`
4. `D:\Zephyr\zephyr\kernel\msg_q.c`

产出：

- 能解释 `k_msleep()`、`k_sem_take()`、`k_msgq_get()` 对线程状态的影响。

### 第 4 周：设备模型与 Devicetree

读：

1. `D:\Zephyr\zephyr\include\zephyr\device.h`
2. `D:\Zephyr\zephyr\include\zephyr\devicetree.h`
3. `D:\Zephyr\zephyr\scripts\dts\gen_defines.py`
4. `D:\Zephyr\projects\tflm\build\zephyr\zephyr.dts`
5. `D:\Zephyr\projects\tflm\build\zephyr\include\generated\zephyr\devicetree_generated.h`

产出：

- 能从一个 DTS 节点追到 `struct device`。

### 第 5 周：Kconfig 与 CMake

读：

1. `D:\Zephyr\zephyr\Kconfig`
2. `D:\Zephyr\zephyr\scripts\kconfig\kconfig.py`
3. `D:\Zephyr\zephyr\scripts\kconfig\kconfiglib.py`
4. `D:\Zephyr\zephyr\cmake\modules\kconfig.cmake`
5. `D:\Zephyr\zephyr\cmake\modules\dts.cmake`

产出：

- 能解释 `CONFIG_XXX -> autoconf.h -> CMake 条件编译`。

### 第 6 周：驱动模型

读：

1. `D:\Zephyr\zephyr\include\zephyr\device.h`
2. `D:\Zephyr\zephyr\include\zephyr\drivers\uart.h`
3. `D:\Zephyr\zephyr\include\zephyr\drivers\can.h`
4. `D:\Zephyr\zephyr\include\zephyr\drivers\pwm.h`
5. `D:\Zephyr_HPMicro\sdk_glue\drivers\serial\uart_hpmicro.c`
6. `D:\Zephyr_HPMicro\sdk_glue\drivers\can`
7. `D:\Zephyr_HPMicro\sdk_glue\drivers\pwm\pwm_hpmicro.c`

产出：

- 能解释 Zephyr API 如何通过 device API table 进入 HPM driver。

### 第 7 周：zbus

读：

1. `D:\Zephyr\zephyr\include\zephyr\zbus\zbus.h`
2. `D:\Zephyr\zephyr\subsys\zbus\zbus.c`

产出：

- 能解释 channel、observer、subscriber、publish、read 的关系。

### 第 8 周：中断、DMA、cache、链接

读：

1. `D:\Zephyr\zephyr\include\zephyr\irq.h`
2. `D:\Zephyr\zephyr\arch\riscv`
3. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\HPM5300\linker.ld`
4. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\common\entry.ld`
5. `D:\Zephyr_HPMicro\sdk_glue\soc\hpmicro\common\ISR.ld`
6. `D:\Zephyr_HPMicro\sdk_glue\drivers\usb`

产出：

- 能解释为什么 ISR/DMA/cache/linker 会影响应用层表现。

---

## 14. 暂时不要读什么

现在先别把精力放在：

1. Bluetooth
2. 网络协议栈
3. 文件系统
4. mcumgr
5. userspace / memory domain 完整安全模型
6. SMP 多核调度
7. 与 HPM5361ICB 无关的大量 board
8. 与当前外设无关的 sample

不是它们不重要，而是它们现在不会最快提升你读当前系统的能力。

---

## 15. 最终验收标准

你读完这条路线后，应该能做到：

1. 解释 `reset -> z_cstart -> init level -> main thread -> main()`。
2. 解释 `k_thread_create()` 如何创建线程对象、栈、优先级和 entry。
3. 解释 `k_msleep()`、`k_sem_take()`、`k_sem_give()` 如何影响调度。
4. 从一个 DTS 节点追到 `devicetree_generated.h` 和 `struct device`。
5. 从一个 `CONFIG_XXX` 追到 `.config`、`autoconf.h` 和 CMake 源文件选择。
6. 从 `uart_rx_enable()` 追到 Zephyr UART API table，再追到 `uart_hpmicro.c`。
7. 解释 zbus channel、subscriber、observer、publish/read 的真实关系。
8. 判断一个问题该看 Zephyr 主线、HPMicro `sdk_glue`、还是你自己的应用层。

最后记住这个阅读姿势：

```text
先 Zephyr 本体
  -> 再 SoC / board 适配
  -> 最后才回到当前工程验证
```

这样才是在学 Zephyr，而不是反复看你自己写的架构。
