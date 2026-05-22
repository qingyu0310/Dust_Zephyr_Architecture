# RS485 使用说明

本文说明 `drivers/communication/rs485` 中 `Rs485` 驱动的使用方式，从设备树到线程初始化。

`Rs485` 是基于 Zephyr `UART_ASYNC_API` 的半双工封装：

- RX 使用 UART async DMA 回调，把数据放入内部环形缓冲区。
- TX 调用 `Send()` 时先把方向 GPIO 切到发送态。
- 收到 `UART_TX_DONE` 或 `UART_TX_ABORTED` 后，方向 GPIO 自动切回接收态。
- 对上层实现 `RxStream` 接口，可以用 `SetNotify()` + `Read()` 的方式消费接收数据。

## 1. 设备树 overlay

RS485 至少需要两个设备树信息：

- 一个 UART 节点：提供 TX/RX、波特率、DMA 等配置。
- 一个方向 GPIO 节点：连接 RS485 收发器的 `DE` 或 `DE/RE` 控制脚。

项目自有的 alias、方向脚和 pinmux 建议放在当前项目 overlay 中，例如：

`projects/boards/hpm/hpm5361icb/hpm5361icb.overlay`

```dts
#include <dt-bindings/gpio/gpio.h>

/ {
	aliases {
		user-rs485 = &uart3;
	};

	rs485_dir: rs485_dir {
		gpios = <&gpioa 10 GPIO_ACTIVE_HIGH>;
	};
};

&gpioa {
	pinctrl-0 = <&pinmux_gpioa>;
	pinctrl-names = "default";
	status = "okay";
};

&uart3 {
	current-speed = <115200>;
	pinctrl-0 = <&pinmux_uart3>;
	pinctrl-names = "default";
	dmas = <&hdma 4 0x1A>, <&hdma 5 0x1B>;
	dma-names = "rx", "tx";
	status = "okay";
};
```

如果所选 UART 的 pinmux 还没有同时配置 TX/RX，需要在 overlay 或板级 pinctrl 中补齐。示例：

```dts
&pinctrl {
	pinmux_uart3: pinmux_uart3 {
		group0 {
			pinmux = <HPMICRO_PINMUX(HPMICRO_PIN(HPMICRO_PORTA, 14), IOC_TYPE_IOC, 0, 2)>,
				 <HPMICRO_PINMUX(HPMICRO_PIN(HPMICRO_PORTA, 15), IOC_TYPE_IOC, 0, 2)>;
			input-enable;
		};
	};
};
```

注意：

- `user-rs485` 在 C/C++ 中通过 `DT_ALIAS(user_rs485)` 获取。
- `rs485_dir` 通过 `DT_NODELABEL(rs485_dir)` 获取。
- 如果收发器是低电平发送、高电平接收，把 `GPIO_ACTIVE_HIGH` 或初始化里的 `tx_level/rx_level` 按硬件改掉。
- 如果只接了 `DE`，通常 `tx_level = 1`、`rx_level = 0`。
- 如果 `DE` 和 `/RE` 接在一起，常见接法也是发送高、接收低。

## 2. Kconfig

驱动开关是：

```conf
CONFIG_COM_RS485=y
```

通常不直接写在 `prj.conf`，而是在使用 RS485 的线程或模块 Kconfig 中 `select COM_RS485`：

```kconfig
config TRD_RS485
    bool "RS485 thread"
    default n
    select COM_RS485
    help
      Enable RS485 communication thread.
```

`COM_RS485` 会自动选择：

- `SERIAL`
- `UART_ASYNC_API`
- `GPIO`

CMake 已经在 `drivers/CMakeLists.txt` 中接入，打开 `COM_RS485` 后会编译 `rs485.cpp` 并加入 include 路径。

## 3. 初始化

线程或模块中包含头文件：

```cpp
#include "rs485.hpp"
```

定义设备树节点：

```cpp
#define RS485_UART_NODE DT_ALIAS(user_rs485)
#define RS485_DIR_NODE  DT_NODELABEL(rs485_dir)

static const struct gpio_dt_spec rs485_dir = GPIO_DT_SPEC_GET(RS485_DIR_NODE, gpios);
static Rs485 rs485 {};
static k_sem rs485_rx_sem;
```

初始化：

```cpp
void rs485_init()
{
    k_sem_init(&rs485_rx_sem, 0, 1);

    Rs485::Config cfg {};
    cfg.buf_size   = 128;
    cfg.rx_timeout = 1000;
    cfg.dir        = &rs485_dir;
    cfg.tx_level   = 1;
    cfg.rx_level   = 0;
    cfg.tx_timeout = 0;

    if (!rs485.Init(DEVICE_DT_GET(RS485_UART_NODE), cfg)) {
        return;
    }

    rs485.SetNotify(&rs485_rx_sem);
}
```

字段说明：

| 字段 | 作用 |
| --- | --- |
| `buf_size` | 单个 DMA RX buffer 大小，最大会限制到驱动内部 `512` |
| `rx_timeout` | 传给 `uart_rx_enable()` 的 RX timeout |
| `dir` | RS485 方向 GPIO，可为 `nullptr`，但实际 RS485 通常需要提供 |
| `tx_level` | 发送时方向 GPIO 电平 |
| `rx_level` | 接收时方向 GPIO 电平 |
| `tx_timeout` | 传给 `uart_tx()` 的 TX timeout |

## 4. 接收

`Rs485` 收到数据后会 `k_sem_give()`，线程里等待信号量后读完当前缓冲区：

```cpp
static void rs485_task(void *, void *, void *)
{
    uint8_t buf[64];

    for (;;)
    {
        if (k_sem_take(&rs485_rx_sem, K_FOREVER) != 0) {
            continue;
        }

        while (true)
        {
            uint16_t len = rs485.Read(buf, sizeof(buf));
            if (len == 0) {
                break;
            }

            /* TODO: parse buf[0..len) */
        }
    }
}
```

`Read()` 是非阻塞的，只从内部缓冲区取已有数据。

## 5. 发送

发送一帧：

```cpp
const uint8_t req[] = { 0x01, 0x03, 0x00, 0x00, 0x00, 0x02 };

if (!rs485.Send(req, sizeof(req))) {
    /* TODO: handle busy or send failure */
}
```

发送流程：

1. `Send()` 检查驱动是否 ready、TX 是否 busy。
2. 拷贝数据到内部 TX buffer。
3. 方向 GPIO 切到 `tx_level`。
4. 调用 `uart_tx()`。
5. UART async 回调收到 `UART_TX_DONE` 后切回 `rx_level`。

内部 TX buffer 当前大小是 `256` 字节，超过会返回 `false`。

## 6. 最小线程骨架

```cpp
#include "rs485.hpp"
#include "thread.hpp"

#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>

namespace thread::rs485 {

#define RS485_UART_NODE DT_ALIAS(user_rs485)
#define RS485_DIR_NODE  DT_NODELABEL(rs485_dir)

static Thread<2048> thread_ {};
static Rs485 rs485_ {};
static k_sem rx_sem_;
static const struct gpio_dt_spec dir_ = GPIO_DT_SPEC_GET(RS485_DIR_NODE, gpios);

static void Task(void*, void*, void*)
{
    uint8_t rx[64];

    for (;;)
    {
        k_sem_take(&rx_sem_, K_FOREVER);

        while (true)
        {
            uint16_t len = rs485_.Read(rx, sizeof(rx));
            if (len == 0) {
                break;
            }

            /* TODO: parse rx data */
        }
    }
}

void thread_init()
{
    k_sem_init(&rx_sem_, 0, 1);

    Rs485::Config cfg {};
    cfg.buf_size   = 128;
    cfg.rx_timeout = 1000;
    cfg.dir        = &dir_;
    cfg.tx_level   = 1;
    cfg.rx_level   = 0;
    cfg.tx_timeout = 0;

    if (!rs485_.Init(DEVICE_DT_GET(RS485_UART_NODE), cfg)) {
        return;
    }

    rs485_.SetNotify(&rx_sem_);
}

void thread_start(uint8_t prio)
{
    if (!rs485_.IsReady()) {
        return;
    }

    thread_.Start(Task, prio);
}

} // namespace thread::rs485
```

## 7. 常见问题

### 编译提示 alias 不存在

确认 overlay 中有：

```dts
aliases {
	user-rs485 = &uart3;
};
```

C/C++ 中使用：

```cpp
DEVICE_DT_GET(DT_ALIAS(user_rs485))
```

### 方向脚没有切换

确认：

- `rs485_dir` 节点存在。
- `GPIO_DT_SPEC_GET(DT_NODELABEL(rs485_dir), gpios)` 使用的 node label 和 overlay 一致。
- GPIO 控制器 `&gpioa` 已经 `status = "okay"`。
- `tx_level/rx_level` 和硬件收发器方向逻辑一致。

### 能发不能收

优先检查：

- `UART_TX_DONE` 是否产生，方向脚是否回到接收态。
- UART pinmux 是否同时包含 RX。
- RS485 A/B 是否接反。
- 收发器 `/RE` 是否真的处在接收使能状态。

### 收到数据但分包不稳定

`Rs485` 只提供字节流，不做协议分帧。上层需要按协议处理帧头、长度、CRC 或超时分帧。
