# USB 枚举失败问题修复记录

> 日期：2026-07-27
> 问题：HPM5361ICB USB0 CDC ACM 枚举失败，Windows 代码 43（设备描述符请求失败）

---

## 问题根因（按修复顺序）

### 1. dcd_data 未按 2048 对齐

**现象：** QHD/qTD 池起始地址不满足 ENDPTLISTADDR 要求。
**根因：** `USB_SOC_DCD_DATA_RAM_ADDRESS_ALIGNMENT = 2048`，原代码只对齐到 64（cacheline）。
控制器读 QHD 时地址错误，EP0 不可用。
**修复：** 用 `USB_SOC_DCD_DATA_RAM_ADDRESS_ALIGNMENT` 对齐 + `HPM_ALIGN_UP` 调整数组大小。
**文件：** `usb_hal_hpm.cpp` — s_dcd_data 声明

### 2. irq_enable 缺失

**现象：** CPU 收不到任何 USB 中断。
**根因：** `irq_connect_dynamic` 只注册了 ISR handler，没有调 `irq_enable` 使能 PLIC 级中断。
**修复：** Init 末尾加 `irq_enable(cfg.irq_num)`。
**文件：** `usb_hal_hpm.cpp` — Init 函数

### 3. irq_connect_dynamic priority=0

**现象：** PLIC 不转发 USB 中断到 CPU。
**根因：** `irq_connect_dynamic(cfg.irq_num, 0, ...)` 传 priority=0。
在 RISC-V PLIC 中 priority=0 表示中断禁用（interrupt never asserted）。
CherryUSB 的 IRQ_CONNECT 从 DT 读优先级（`interrupts = <51 1>` → priority=1）。
**修复：** Config 加 `irq_priority` 成员，默认 1，传给 `irq_connect_dynamic`。
**文件：** `usb_hal.hpp`、`usb_hal_hpm.cpp`、`usb.hpp`、`usb.cpp`

### 4. HandleReset 没开 EP0

**现象：** 收到 USB reset 后 EP0 的 ENDPTCTRL[0] 未使能，无法响应主机 SETUP。
**根因：** `HandleReset` 只调了 `usb_device_bus_reset`（清 dcd_data 建 QHD），
没调 `usb_device_edpt_open`（配 ENDPTCTRL 的 RXE/TXE）。
原注释以为 `usb_device_bus_reset` 配了端点，实际 SDK 源码确认它只配 QHD。
**修复：** HandleReset 加 `usb_device_edpt_open(&s_handle, &ep0_out)` + `usb_device_edpt_open(&s_handle, &ep0_in)`。
**文件：** `usb_hal_hpm.cpp` — HandleReset

### 5. SET_ADDRESS 时序错误

**现象：** 主机发送 SET_ADDRESS 后设备响应 STATUS IN 成功，
但主机在新地址发 GET_DESCRIPTOR 时无响应，500ms 超时后复位（循环）。
**根因：** `usb_dcd_set_address` 写 DEVICEADDR 同时设置 USBADRA=1，
地址在"当前传输完成后"自动生效。
原代码把地址设置推迟到 STATUS IN 完成之后才写 DEVICEADDR，
此时没有活跃传输，USBADRA 无法触发地址切换，地址不生效。
**修复：** 在 SETUP 处理阶段（STATUS IN 之前）直接写 DEVICEADDR，
与 CherryUSB 的 `usbd_std_device_req_handler` 做法一致。
删掉 `pending_address_` 延迟设置机制。
**文件：** `usb_device.cpp`、`usb_device.hpp`

### 6. 描述符字节与 CherryUSB 不一致

**现象：** 部分描述符字节值与工作版本差异。
**差异清单：**
- IAD bFunctionProtocol = 1（应为 0 = CDC_COMMON_PROTOCOL_NONE）
- 控制接口 bInterfaceProtocol = 1（应为 0）
- 中断端点 bInterval = 16（应为 10）
**修复：** 全部改为与 CherryUSB `CDC_ACM_DESCRIPTOR_INIT` 宏一致。
**文件：** `usb_descriptor.cpp` — BuildConfig

### 7. EP0 操作返回值未检查

**现象：** EP0 提交失败时静默推进状态机。
**修复：** 所有 `Ep0StartIn`/`Ep0StartOut`/`Ep0StatusIn`/`Ep0StatusOut`
调用处加返回值检查，失败时 LOG_ERR + STALL + 状态回退到 Idle。
**文件：** `usb_device.cpp`、`usb_hal_hpm.cpp`

### 8. EP0 IN 数据路径 cache 一致性

**现象：** 描述符数组在普通 BSS 内存中，DMA 直接从原址读可能读到脏 cache。
**修复：** `Ep0StartIn` 先 memcpy 到 .nocache 段的 `s_tx_buf`，再从 .nocache 做 DMA。
与 CherryUSB 的 `req_data`（在 g_usbd_core 内，USB_NOCACHE_RAM_SECTION）等效。
**文件：** `usb_hal_hpm.cpp` — Ep0StartIn

### 9. EP0 无 ep_enable 检查

**现象：** CherryUSB 的 `usbd_ep_start_write` 检查 `in_ep[ep_idx].ep_enable`，
原 Ep0StartIn 不检查，端点未使能时仍提交 qTD。
**修复：** `Ep0StartIn` 加 `s_in_ep[0].enable` 检查。
**文件：** `usb_hal_hpm.cpp` — Ep0StartIn

---

## 仍未修复的问题（需要 ISR + 枚举成功后暴露）

### 10. EP0 DATA IN 分段发送

配置描述符 75 字节 > EP0 MPS 64 字节。
CherryUSB 的 `usbd_event_ep0_in_complete_handler` 在 data_in 完成后检查
`residue`，继续发送剩余数据直到全部发完。
本工程 `HandleTransferComplete` 的 DataIn 分支直接进 StatusOut，不检查剩余数据。
**影响：** 75 字节只发前 64 字节，剩余 11 字节丢失，主机超时。
**文件：** `usb_device.cpp` — HandleTransferComplete DataIn 分支

### 11. ZLP 处理

CherryUSB 在 `__usbd_event_ep0_setup_complete_handler` 中检查
`wLength > data_len && data_len % 64 == 0`，设置 `zlp_flag`。
`usbd_event_ep0_in_complete_handler` 在数据发完后检查 `zlp_flag` 发送 ZLP。
本工程无 ZLP 处理。
**影响：** 数据长度是 64 整数倍时需要 ZLP 收尾，否则主机不知传输结束。

---

## 根因模式总结

| 模式 | 出现次数 | 典型表现 |
|------|---------|---------|
| 不逐行对比工作版本 | 多次 | 有 1ee8db3 能枚举的 CherryUSB 代码不看 |
| 不查 SDK/PLIC 文档 | PLIC priority, USBADRA | 凭直觉猜参数含义 |
| 自作主张改时序 | SET_ADDRESS 推迟 | 以为比 CherryUSB "更规范" |
| 注释误导不验证 | "CPU 中断已开启" | 信注释不读代码 |
| 不验证 Edit 结果 | 多次 | 没 cat 确认写入 |

## 文件变更清单

| 文件 | 变更内容 |
|------|---------|
| `usb_hal.hpp` | Config 加 irq_priority 成员 |
| `usb_hal_hpm.cpp` | dcd_data 对齐 2048、irq_enable、irq_priority、HandleReset 开 EP0、Ep0StartIn nocache+ep_enable+log |
| `usb_device.hpp` | 删 pending_address_ |
| `usb_device.cpp` | SET_ADDRESS 立即写、StatusIn 删地址设置、ResetState 删 pending、LOG 注册、EP0 返回值检查 |
| `usb_descriptor.cpp` | IAD 协议 0、接口协议 0、中断间隔 10 |
| `usb.hpp` | Config 加 irq_priority |
| `usb.cpp` | hal_cfg 传递 irq_priority |
