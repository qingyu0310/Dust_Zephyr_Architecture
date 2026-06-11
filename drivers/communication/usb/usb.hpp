/**
 * @file usb.hpp
 * @author qingyu
 * @brief CherryUSB CDC ACM 虚拟串口驱动
 * @version 0.2
 * @date 2026-06-11
 *
 * # USB (CDC ACM) 使用说明
 *
 * `Usb` 是基于 CherryUSB 的 CDC ACM 虚拟串口封装：
 *
 * - RX 使用乒乓 DMA 双缓冲，收到数据后存入内部环形缓冲。
 * - TX 拷贝数据到内部缓冲后异步提交到 USB IN 端点。
 * - 对上层实现 `RxStream` 接口，`SetNotify()` + `Read()` 消费接收数据。
 * - 主机侧枚举为标准 CDC ACM 虚拟串口。
 *
 * ## 设备树
 *
 * USB 控制器节点通常在 SoC dtsi 中已定义。驱动通过 `DT_NODELABEL(cherryusb_usb0)`
 * 自动获取寄存器基址：
 *
 * ```dts
 * &usb0 {
 *     status = "okay";
 * };
 * ```
 *
 * 也可通过 `Config::reg_base` 手动指定，`reg_base = 0` 时回退到设备树自动检测。
 *
 * ## Kconfig
 *
 * ```kconfig
 * config TRD_USB
 *     bool "USB CDC ACM thread"
 *     default n
 *     select COM_USB
 *     help
 *       Enable USB CDC ACM communication thread.
 * ```
 *
 * ## 初始化
 *
 * ```cpp
 * static Usb usb {};
 * static k_sem rx_sem;
 *
 * void usb_init()
 * {
 *     k_sem_init(&rx_sem, 0, 1);
 *
 *     const struct device *dev = DEVICE_DT_GET(DT_NODELABEL(cherryusb_usb0));
 *     RxStream::Config cfg {};
 *     cfg.buf_size   = 256;
 *     cfg.rx_timeout = 0;
 *
 *     usb.Init(dev, cfg);
 *     usb.SetNotify(&rx_sem);
 * }
 * ```
 *
 * 也可用 `Usb::Config` 手动指定 busid、reg_base、baudrate。
 *
 * ## 接收
 *
 * 收到数据后驱动 `k_sem_give()`，线程中消费：
 *
 * ```cpp
 * k_sem_take(&rx_sem, K_FOREVER);
 * while (true) {
 *     uint16_t len = usb.Read(buf, sizeof(buf));
 *     if (len == 0) break;
 *     // process buf[0..len)
 * }
 * ```
 *
 * ## 发送
 *
 * ```cpp
 * if (!usb.Send(data, len)) {
 *     // 忙或参数错误
 * }
 * ```
 *
 * 单帧最大 512 字节，上一帧发完前返回 false。
 * 数据长度为端点 MPS 整数倍时自动发 ZLP 收尾。
 *
 * ## Config 字段
 *
 * | 字段 | 说明 |
 * |------|------|
 * | busid | USB 总线号，多控制器时区分（从 0 开始） |
 * | reg_base | 控制器寄存器基址，0=设备树自动获取 |
 * | buf_size | 接收环形缓冲大小，上限 512 |
 *
 * ## 常见问题
 *
 * - **设备不枚举**：检查 USB 控制器 status=okay、cherryusb_usb0 标签、PHY 时钟。
 * - **收不到数据**：确认主机串口参数匹配；检查 OnBulkOut 是否有数据到。
 * - **发送返回 false**：检查 IsConfigured() 和 IsTxBusy()，确认数据未超 512 字节。
 * - **主机收不全**：ZLP 已自动处理；检查串口工具流控设置。
 * - **波特率不生效**：baudrate 仅初始值，主机 SET_LINE_CODING 会覆盖。
 *     USB 是包传输，不依赖波特率。
 *
 * @copyright Copyright (c) 2026
 */

#pragma once

#include "uart.hpp"

#include <stdint.h>

/* CherryUSB 回调（C 函数，转进 Usb 对象） */
extern "C" {
void usb_cdc_event_handler(uint8_t busid, uint8_t event);
void usb_cdc_bulk_out(uint8_t busid, uint8_t ep, uint32_t nbytes);
void usb_cdc_bulk_in(uint8_t busid, uint8_t ep, uint32_t nbytes);
}

/**
 * @brief CDC ACM 串口设备驱动
 *
 * 封装 CherryUSB CDC ACM 的初始化、收发和事件处理，对外暴露 RxStream 接口。
 */
class Usb final : public RxStream
{
    friend void usb_cdc_event_handler(uint8_t busid, uint8_t event);
    friend void usb_cdc_bulk_out(uint8_t busid, uint8_t ep, uint32_t nbytes);
    friend void usb_cdc_bulk_in(uint8_t busid, uint8_t ep, uint32_t nbytes);

public:
    /**
     * @brief 初始化参数
     */
    struct Config : public RxStream::Config {
        uint8_t  busid    = 0;       // USB 总线号
        uint32_t reg_base = 0;       // USB 控制器寄存器基址，0=自动检测
    };

    bool     Init(const struct device* dev, const RxStream::Config& cfg) override;
    bool     Init(const Config& cfg);
    void     SetNotify(struct k_sem* sem) override;
    uint16_t Read(uint8_t* buf, uint16_t max_len) override;
    bool     Send(const uint8_t* data, uint32_t len);
    uint8_t  GetSpeed() const;

    /**
     * @brief 驱动初始化完成
     */
    bool IsReady()      const { return ready_; }
    /**
     * @brief USB 已枚举，CDC ACM 配置设置完成
     */
    bool IsConfigured() const { return configured_; }
    /**
     * @brief 上一帧发送未完成，不可发新数据
     */
    bool IsTxBusy()     const { return tx_busy_; }

private:
    static constexpr uint16_t kMaxBufSize = 512;

    // CherryUSB 事件回调
    void OnEvent(uint8_t busid, uint8_t event);
    void OnBulkOut(uint8_t busid, uint8_t ep, uint32_t nbytes);
    void OnBulkIn(uint8_t busid, uint8_t ep, uint32_t nbytes);

    // 内部辅助
    void StoreRx(const uint8_t* data, uint16_t len);
    bool StartRead(uint8_t busid, uint8_t ep);

    // USB 总线号
    uint8_t busid_ = 0;

    // 环形接收缓冲
    uint16_t buf_size_ = kMaxBufSize;
    uint8_t  rx_buf_[kMaxBufSize] {};
    uint16_t head_ = 0;
    uint16_t tail_ = 0;

    // 乒乓 DMA 缓冲切换索引
    volatile uint8_t read_buffer_index_ = 0;

    // 状态标志
    volatile bool ready_      = false;
    volatile bool configured_ = false;
    volatile bool tx_busy_    = false;

    // 外部通知
    k_sem* notify_sem_ = nullptr;
};
