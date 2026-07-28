# 自研 USB CDC ACM 当前代码审查

## 结论

当前 USB 栈的总体方向没有问题：

```text
单一 CDC ACM
UsbDevPort 负责 USB Device Core
UsbCdcAcm 负责 CDC 协议
UsbHalHpm 负责 HPM EHCI
Usb 负责 Stream 和业务收发
```

不需要因为“代码专一 CDC”而重新引入：

```text
UsbFunction 多态
HID/MSC/Vendor Class
多 Function 注册框架
多 USB 控制器实例抽象
```

当前真正需要处理的是两类问题：

```text
第一类：没有实际作用的死代码、半成品接口和失效注释
第二类：错误路径、EP0 路径和端点恢复路径仍有行为错误
```

最严重的问题如下：

```text
bulk IN 出错时被错误地按 OUT RX 端点恢复
IN 错误后 tx_busy_ 不会释放
GET_DESCRIPTOR 直接绕过统一 SubmitDataIn()
EP0 DATA IN 错误仍然继续提交 STATUS OUT
EP0 STATUS 错误没有统一处理
UsbCdcAcm notification 成员没有任何发送路径
HPM HAL 有未实现的死声明和未处理的 suspend 中断
UsbDevPort 中多个字段只写不读
```

当前代码可以继续做正常枚举和收发验证，但在修复 bulk IN 错误路径之前，不能认为错误恢复闭环已经完成。

本次只做源码审查和文档重写，没有执行构建、烧录或硬件测试。

---

## 1. 当前真实调用链

```text
业务线程
  -> usb::Usb
  -> UsbDevPort
  -> UsbCdcAcm
  -> UsbDescriptorSet
  -> UsbHal
  -> UsbHalHpm
  -> HPM USB SDK
  -> EHCI/QHD/qTD/DMA
```

主要文件：

```text
D:\Zephyr\projects\tflm\drivers\communication\stream\usb\usb.hpp
D:\Zephyr\projects\tflm\drivers\communication\stream\usb\usb.cpp
D:\Zephyr\projects\tflm\drivers\communication\stream\usb\usb_rx_queue.hpp

D:\Zephyr\modules\user\usb\core\usb_dev_port.hpp
D:\Zephyr\modules\user\usb\core\usb_dev_port.cpp
D:\Zephyr\modules\user\usb\core\usb_cdc_acm.hpp
D:\Zephyr\modules\user\usb\core\usb_cdc_acm.cpp
D:\Zephyr\modules\user\usb\core\usb_descriptor.hpp
D:\Zephyr\modules\user\usb\core\usb_descriptor.cpp

D:\Zephyr\modules\user\usb\interface\usb_hal.hpp
D:\Zephyr\modules\user\usb\hal\usb_hal_hpm.hpp
D:\Zephyr\modules\user\usb\hal\usb_hal_hpm.cpp
```

### 1.1 `Usb`

负责：

```text
Stream::Read/Send
配置状态
发送忙状态
bulk IN ZLP
接收队列
业务线程 semaphore
```

### 1.2 `UsbDevPort`

负责：

```text
USB 设备状态
标准请求
EP0 状态机
CDC 类请求分发
CDC 端点打开和关闭
OUT RX 首次提交
OUT RX 重提
OUT RX 错误恢复
```

### 1.3 `UsbCdcAcm`

负责：

```text
SET_LINE_CODING
GET_LINE_CODING
SET_CONTROL_LINE_STATE
SEND_BREAK
line coding
DTR/RTS
CDC 描述符查询
CDC 端点配置查询
```

### 1.4 `UsbHalHpm`

负责：

```text
PHY 和时钟
IRQ
USB reset
EP0
bulk endpoint
DMA/cache
QHD/qTD
端点 flush/close/open
HPM SDK
```

---

## 2. P0：bulk IN 错误恢复方向完全错误

### 当前路径

`UsbDevPort::HandleTransferComplete()` 当前逻辑是：

```cpp
cdc_acm_.OnEndpointComplete(
    event.endpoint,
    event.data,
    event.length,
    event.error);

if (event.error) {
    RecoverRxEndpoint(event.endpoint);
}
```

但 `UsbCdcAcm::OnEndpointComplete()` 遇到错误会直接返回，不再调用 `Usb::OnDataEvent()`。

因此：

```text
bulk OUT error
  -> 不进入业务回调
  -> RecoverRxEndpoint(OUT)
  -> 重开 OUT
  -> 重新 EpStartRx
```

这条方向是对的。

但是：

```text
bulk IN error
  -> 不进入 Usb::OnBulkIn()
  -> tx_busy_ 不会清零
  -> RecoverRxEndpoint(IN)
```

`RecoverRxEndpoint()` 不区分方向，直接：

```text
清除 STALL
关闭 endpoint
按 Bulk 配置重新打开
调用 EpStartRx
```

所以 bulk IN 出错时，当前代码会尝试：

```text
对 IN endpoint 提交 RX
```

这既不能恢复发送，也会把 IN 端点的软件状态弄乱。

### 后果

```text
一次 IN qTD error
  -> tx_busy 永久保持 1
  -> 后续 Send 全部返回 false
  -> IN 端点却被当成 OUT 重新启动
```

这是当前最严重的错误路径。

### 修复方向

在 `UsbDevPort::HandleTransferComplete()` 分方向处理：

```text
OUT error
  -> RecoverRxEndpoint(OUT)

IN error
  -> 通知 Usb 清除 tx_busy
  -> 不调用 EpStartRx
  -> 必要时只关闭并重新打开 IN endpoint
```

更简单的当前方案是增加一个独立的错误回调：

```cpp
using ErrorCallback = void (*)(void* ctx, uint8_t ep);
```

然后：

```text
OUT 错误由 UsbDevPort 恢复
IN 错误由 Usb 清理 tx_busy
```

不能让一个名为 `RecoverRxEndpoint()` 的函数处理所有方向。

---

## 3. P0：GET_DESCRIPTOR 仍绕过统一 EP0 提交路径

`UsbDevPort` 已经有：

```cpp
SubmitDataIn()
SubmitDataOut()
SubmitStatusIn()
SubmitStatusOut()
```

这些函数统一处理：

```text
stage 更新
HAL 返回值
错误日志
EP0 STALL
回退 Idle
```

但是 `SendDescriptor()` 仍然直接调用：

```cpp
ep0_stage_ = Ep0Stage::DataIn;
return hal_->Ep0StartIn(data, length);
```

这会造成：

```text
描述符路径和普通 DATA IN 路径行为不一致
Ep0StartIn 失败时没有统一日志
失败后 ep0_stage_ 可能仍然是 DataIn
外层只做一次 STALL，状态机没有完整回退
```

### 修复方向

直接改成：

```cpp
return SubmitDataIn(data, length);
```

这样所有 EP0 DATA IN 都走同一条路径。

---

## 4. P0：EP0 DATA IN 出错后仍然提交 STATUS OUT

当前 `HandleTransferComplete()` 对 EP0 的处理：

```cpp
case Ep0Stage::DataIn:
    SubmitStatusOut();
    break;
```

没有判断：

```cpp
event.error
event.endpoint 方向是否正确
```

因此：

```text
GET_DESCRIPTOR DATA IN 出错
  -> 仍然提交 STATUS OUT
```

正确行为应该是：

```text
EP0 DATA IN error
  -> ep0_stage_ = Idle
  -> EP0 STALL
  -> 丢弃当前控制传输
```

`DataOut` 分支已经检查了 `event.error`，但 `DataIn` 分支没有对称处理。

---

## 5. P1：EP0 STATUS 错误没有处理

当前：

```cpp
case Ep0Stage::StatusIn:
case Ep0Stage::StatusOut:
    ep0_stage_ = Ep0Stage::Idle;
    break;
```

无论：

```text
STATUS 成功
STATUS 失败
qTD error
方向错误
```

都会直接回到 Idle。

这会把控制传输错误伪装成正常完成。

建议：

```text
STATUS error
  -> 记录错误
  -> 回到 Idle
  -> 必要时 STALL EP0
```

至少不能让 `event.error=true` 和正常 completion 完全一样。

---

## 6. P1：`UsbCdcAcm` 中的死代码和半成品成员

### 6.1 `kMaxBufSize` 未使用

位置：

```text
D:\Zephyr\modules\user\usb\core\usb_cdc_acm.hpp
```

```cpp
static constexpr uint16_t kMaxBufSize = 512;
```

当前 CDC 类内部没有使用它。

真正限制 `Usb::Send()` 长度的是：

```cpp
Usb::kMaxBufSize
```

建议删除 CDC 类中的这一份，避免出现两个“最大缓冲区”事实。

### 6.2 `configured_` 只写不读

当前只出现：

```cpp
configured_ = true;
configured_ = false;
```

没有任何逻辑读取 `UsbCdcAcm::configured_`。

实际使用 configured 状态的是：

```cpp
Usb::configured_
UsbDevPort::state_
```

建议：

```text
删除 UsbCdcAcm::configured_
```

或者提供明确的 `IsConfigured()` 并让 `CanSend()` 等逻辑使用它。

当前状态下，它只是重复保存了一份永远不参与决策的状态。

### 6.3 `notify_buf_` 和 `notify_busy_` 没有发送路径

当前成员：

```cpp
uint8_t notify_buf_[10] {};
bool notify_busy_ = false;
```

实际代码只有：

```text
notify_busy_ = false
```

没有：

```text
填充 notify_buf_
EpStartTx(notification_ep)
notification completion
notify_busy_ = true
```

这不是“暂时没用但以后可用”的完整模块，而是半成品代码。

建议二选一：

```text
实现 Serial State notification
```

或者：

```text
删除 notify_buf_、notify_busy_
并明确当前不实现通知发送
```

### 6.4 `break_value_` 只保存和打印

`SEND_BREAK` 当前做：

```cpp
break_value_ = setup.w_value;
LOG_INF(...)
```

工程中没有其他代码读取 `break_value_`。

如果项目不处理 BREAK：

```text
可以只记录日志，不保存成员
```

如果要保留状态：

```text
提供 GetBreakValue()
或增加实际串口控制行为
```

### 6.5 未实现的 HAL 私有声明

`usb_hal_hpm.hpp` 声明了：

```cpp
void ResetController();
void SetDeviceMode();
```

但 `.cpp` 中没有实现，也没有调用。

这是确定的死声明，应删除。

### 6.6 `kIntrSuspend` 只打开不处理

HPM HAL 初始化时启用：

```cpp
kIntrSuspend
```

但 `Isr()` 没有：

```text
识别 suspend
产生 Suspend 事件
产生 Resume 事件
```

当前 `UsbHal::EventType` 也没有 Suspend/Resume。

这意味着：

```text
打开了 suspend 中断
但没有任何业务行为
```

如果项目不实现挂起恢复，应从中断 mask 中删除。

如果要实现，就要补齐：

```text
EventType
HAL 状态识别
UsbDevPort 处理
CDC/Stream 状态策略
```

---

## 7. P1：`UsbDevPort` 中的死字段和重复状态

### 7.1 `control_len_` 没有实际用途

当前只在 `ResetState()` 中：

```cpp
control_len_ = 0;
```

没有记录长度，也没有读取。

EP0 实际使用的是：

```cpp
setup.w_length
局部 len
event.length
```

建议删除 `control_len_`。

### 7.2 `address_` 只写不读

当前：

```cpp
address_ = addr;
```

但后续没有通过 `address_` 返回地址，也没有状态判断使用它。

真正的地址已经交给：

```cpp
hal_->SetAddress(addr)
```

如果没有 `GET_ADDRESS` 或内部判断需求，`address_` 是重复状态。

### 7.3 `GetSpeed()` 当前没有工程调用者

`UsbDevPort::GetSpeed()` 只在类中定义，没有项目引用。

它可以保留为调试接口，但如果不需要对外查询，应删除，或者明确标注为调试 API。

---

## 8. P1：标准请求仍有明显协议缺口

### 8.1 `GET_STATUS` 永远返回 0

当前：

```cpp
uint16_t status = 0;
```

没有读取：

```text
设备 remote wakeup
endpoint halt
接口状态
```

CDC 基础枚举可能不受影响，但异常请求和不同主机下行为不完整。

### 8.2 `CLEAR_FEATURE` 校验不足

当前只要不是 endpoint halt，仍可能继续：

```cpp
SubmitStatusIn();
```

没有严格拒绝：

```text
非法 recipient
非法 selector
非法 endpoint
非法 wIndex
```

### 8.3 `SET_ADDRESS` 返回值被忽略

当前：

```cpp
hal_->SetAddress(addr);
address_ = addr;
state_ = DeviceState::Addressed;
```

没有检查：

```cpp
SetAddress() 是否成功
地址是否小于等于 127
wIndex 是否为 0
wLength 是否为 0
bmRequestType 是否正确
```

### 8.4 `GET_DESCRIPTOR` index 校验不完整

`UsbCdcAcm::GetDescriptor()` 对 Device、Configuration 等描述符没有严格校验 `index`。

例如：

```text
GET_DESCRIPTOR(Device, index=1)
```

理论上应该拒绝，但当前可能仍返回设备描述符。

### 8.5 CDC 类请求参数仍可收紧

当前已经严格校验了 `SET_LINE_CODING` 的 DATA OUT，但以下请求还可以更严格：

```text
GET_LINE_CODING 的 wLength 必须为 7
SET_CONTROL_LINE_STATE 的 wLength 必须为 0
SEND_BREAK 的 wLength 必须为 0
SET_CONTROL_LINE_STATE 的 wValue 只允许有效 DTR/RTS 位
```

---

## 9. P1：HPM HAL 仍有几个错误路径问题

### 9.1 ZLP 路径没有检查 IN endpoint enable

`UsbHalHpm::EpStartTx()` 对普通数据会检查：

```cpp
in_ep_[idx].enable
```

但是零长度路径直接：

```cpp
return usb_device_edpt_xfer(&s_handle, ep, nullptr, 0);
```

没有检查：

```text
endpoint 是否为 IN
endpoint 是否 enable
HAL 是否 ready
```

在 reset/disconnect 与 ZLP completion 交错时，可能向已关闭端点提交 ZLP。

### 9.2 EP0 OUT/STATUS 缺少对称的 ready/enable 检查

`Ep0StartIn()` 会检查：

```text
ready_
in_ep_[0].enable
```

但 `Ep0StartOut()`、`Ep0StatusIn()`、`Ep0StatusOut()` 没有同等检查。

这让 EP0 四条提交路径的失败条件不一致。

### 9.3 `EpClose()` 的 flush 等待没有超时

当前：

```cpp
while (s_handle.regs->ENDPTFLUSH & flush_bit) {}
```

如果硬件没有清除 flush bit，CPU 会永久卡在这里。

建议增加：

```text
有限次轮询
超时日志
错误返回
```

### 9.4 ISR 中直接打印日志

当前 ISR 直接调用：

```cpp
LOG_ERR()
LOG_INF()
```

如果日志后端同步输出，可能延长 ISR 执行时间。

当前不是第一优先级，但错误频繁发生时会放大 USB 时序问题。

---

## 10. P2：失效注释和接口契约

### 10.1 `usb_hal_hpm.hpp` 注释提到不存在的 `instance_`

头文件写着：

```text
ISR trampoline 使用 instance_ 静态成员
```

实际实现使用的是：

```cpp
usb_isr_entry(arg)
static_cast<UsbHalHpm*>(arg)->Isr()
```

注释已经过期，应改成真实实现。

### 10.2 `UsbCdcAcm` 注释宣称实现 notification

类注释写着：

```text
Interrupt IN notification
```

但当前没有 notification 发送函数。

应该明确写成：

```text
描述并打开 notification endpoint
当前未实现 Serial State notification 发送
```

### 10.3 `UsbHal::Connect()` 注释与实际语义不完全一致

接口注释写：

```text
连接 USB（USBCMD.RS=1）
```

HPM 实现实际调用：

```cpp
usb_device_connect(&s_handle);
```

应该写成平台无关的：

```text
使能 USB 设备连接/attach
```

不要把某个平台寄存器语义写进通用接口。

---

## 11. 当前死代码清单

### 可以直接删除

```text
UsbHalHpm::ResetController 声明
UsbHalHpm::SetDeviceMode 声明
UsbCdcAcm::kMaxBufSize
UsbDevPort::control_len_
```

### 需要实现或删除

```text
UsbCdcAcm::notify_buf_
UsbCdcAcm::notify_busy_
UsbCdcAcm::configured_
UsbCdcAcm::break_value_
HPM kIntrSuspend
```

### 暂时可保留但应明确用途

```text
UsbDevPort::GetSpeed()
Usb::IsTxBusy()
UsbRxQueue::OverflowCount()
UsbDevPort::address_
```

这些接口可能用于调试或业务查询，但当前工程没有完整使用链。

---

## 12. 推荐修复顺序

### 第一优先级

```text
1. 按 IN/OUT 分离错误处理
2. IN 错误时释放 tx_busy
3. OUT 错误才调用 RecoverRxEndpoint
4. SendDescriptor 改走 SubmitDataIn
5. EP0 DataIn/StatusIn/StatusOut 增加 error 判断
```

### 第二优先级

```text
1. 删除确定的死声明和死字段
2. 删除或实现 notification 半成品
3. 删除未处理的 suspend 中断
4. 补齐 EP0 四条路径的 ready/enable 检查
5. 给 EpClose flush 增加超时
```

### 第三优先级

```text
1. 补全标准请求校验
2. 补全 CDC 类请求校验
3. 清理过期注释
4. 为错误恢复增加计数器和日志上下文
```

---

## 13. 最终评价

当前 USB 栈不是架构方向错了，而是：

```text
专一 CDC 方向正确
主路径已经比较清楚
正常枚举和收发路径基本成形
```

但是：

```text
错误事件分方向处理还不完整
EP0 仍有一条绕过统一提交接口的路径
notification 是未完成半成品
若干成员和函数只是历史残留
标准请求还不够严格
HPM HAL 的硬件等待和 EP0 边界还需要补强
```

最关键的一句话：

> **现在最应该做的不是继续抽象 USB，而是把错误路径按端点方向修正确、把死代码删干净、把 EP0 和 notification 的真实状态写清楚。**
