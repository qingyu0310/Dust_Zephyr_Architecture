# HPM5361 UART 当前最终实现说明

日期：2026-05-23

## 当前结论

当前 HPM5361 上的遥控器接收链路已经进入可用的最终状态：

- UART4 接收正常
- DMA 搬运正常
- UART 硬件 RX idle 提前切帧正常
- 应用层已经可以稳定收到完整的 `18` 字节 DR16 帧

也就是说，当前问题已经不再是“UART 底层收不到”或者“DMA 只能等满 128B 才上报”，而是已经切换到：

`UART4 RX -> DMA -> UART 硬件 RX idle -> 提前 flush -> UART_RX_RDY -> uart.cpp -> remote.cpp`

目前剩余的性能差距主要在应用层，不在 UART 底层。

## 当前底层实现

相关文件：

- `D:\Zephyr_HPMicro\sdk_glue\drivers\serial\uart_hpmicro.c`

当前采用的是 **UART 硬件 RX idle + DMA 提前 flush** 的实现方式。

核心逻辑如下：

1. 打开 UART async RX + DMA
2. 配置 UART 硬件 RX idle 检测
3. 当 UART 判定 RX 进入 idle：
   - 暂停 DMA
   - 清除 idle 标志
   - 读取当前 DMA 已经收到的数据长度
   - 触发 `UART_RX_RDY`
   - 如有下一个 buffer，则切换到下一个 buffer
   - 恢复 DMA 与 idle 检测

### 当前关键参数

- DMA RX buffer：`128`
- DR16 单帧长度：`18`
- UART 硬件 idle 阈值：当前为较保守但稳定的配置

### 当前主路径

当前稳定工作的主路径是 UART 自身的硬件 idle 中断，不再依赖最初那条不稳定的“TRGM 软件 idle”为主路径。

GPTMR 相关代码仍然保留在驱动中，但当前稳定切帧依赖的是 UART 硬件 idle。

## 当前应用层行为

相关文件：

- [uart.cpp](D:\Zephyr\projects\tflm\drivers\communication\uart\uart.cpp)
- [remote.cpp](D:\Zephyr\projects\tflm\modules\remotes\remote.cpp)
- [dr16.cpp](D:\Zephyr\projects\tflm\modules\remotes\dr16\dr16.cpp)

当前应用层行为是：

1. 底层驱动产生 `UART_RX_RDY`
2. `UartDma` 将收到的数据写入软件缓冲区
3. `remote.cpp` 通过信号量被唤醒
4. `Remote::Task()` 读取数据并交给协议解析
5. DR16 解析能够在稳定状态下按完整一帧处理

当前已经观察到的稳定现象是：

- 每次 flush 出来的是完整 `18` 字节
- 上层一次 `Read()` 就能读出一帧
- `frame_pos = 0`

这说明：

- 帧边界已经在底层切齐
- 应用层不再长期积压残余字节

## 当前时钟环境

运行时确认到的频率如下：

- `cpu0 = 480 MHz`
- `ahb = 160 MHz`
- `uart4 = 800 MHz`
- `gptmr3 = 100 MHz`

因此，当前 HPM 路径比 STM32 慢，不能简单归因于“主频不够”。

## 当前性能认识

现在 UART 底层已经不是主要瓶颈。

已经确认的事实：

- RX 链路正常
- 提前切帧正常
- 不再退化成 `128B` 满缓冲上报

当前主要耗时已经转移到：

- `remote.cpp` 的应用层处理
- 协议 `decode`
- `zbus_chan_pub()`

也就是说，当前若继续优化时延，重点应放在应用层，不是 UART 驱动层。

## 当前限制

虽然底层已经可用，但目前仍有几个现实限制：

1. 端到端应用层耗时仍明显高于 STM32 参考实现
2. `zbus` 发布开销不小
3. 协议处理在 HPM5361 上仍然占据较明显的时间

所以当前状态可以定义为：

- **功能正确**
- **链路稳定**
- **底层可交付**
- **性能还可继续优化**

## 后续如果再次验证

后续如果要重新确认这套实现是否仍然正确，建议重点看下面几项：

1. DR16 是否仍然稳定按 `18` 字节一帧上报
2. `remote.cpp` 是否还能保持 `frame_pos = 0`
3. 是否重新退化回 `128B` 满缓冲后才上报
4. UART 硬件 idle 是否仍然是主切帧路径

## 最终说明

当前 HPM5361 的 UART 底层已经达到“当前最终实现说明”所需的状态：

- 可以稳定工作
- 可以稳定切帧
- 可以支撑 DR16 正常上层解析

后续如果还要继续追时间，应该主要从应用层继续下手，而不是再回到 UART 底层反复排查。
