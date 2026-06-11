/**
 * @file rs485.hpp
 * @author qingyu
 * @brief RS485 半双工驱动 — 基于 UART DMA + 方向 GPIO
 * @version 0.1
 * @date 2026-05-23
 *
 * # RS485 使用说明
 *
 * 基于 UART DMA + GPIO 方向控制引脚实现半双工收发。
 * 发送前拉高方向引脚，完成后自动切回接收。
 *
 * ## 设备树
 *
 * 项目 overlay 中定义：
 * ```dts
 * aliases {
 *     user-rs485 = &uart3;
 * };
 * rs485_dir: rs485_dir {
 *     gpios = <&gpioa 10 GPIO_ACTIVE_HIGH>;
 * };
 * ```
 *
 * ### Kconfig
 * ```kconfig
 * config TRD_RS485
 *     select COM_RS485
 * ```
 *
 * ### 初始化
 * ```cpp
 * static Rs485 rs485{};
 * static const gpio_dt_spec dir = GPIO_DT_SPEC_GET(DT_NODELABEL(rs485_dir), gpios);
 *
 * void init() {
 *     Rs485::Config cfg{};
 *     cfg.buf_size   = 128;
 *     cfg.rx_timeout = 1000;
 *     cfg.dir        = &dir;
 *     cfg.tx_level   = 1;
 *     cfg.rx_level   = 0;
 *     rs485.Init(DEVICE_DT_GET(DT_ALIAS(user_rs485)), cfg);
 *     rs485.SetNotify(&rx_sem);
 * }
 * ```
 *
 * ### 接收
 * ```cpp
 * k_sem_take(&rx_sem, K_FOREVER);
 * uint8_t buf[64];
 * uint16_t len = rs485.Read(buf, sizeof(buf));
 * ```
 *
 * ### 发送
 * ```cpp
 * rs485.Send(data, len);  // 自动切方向引脚 → 发送 → 切回接收
 * ```
 *
 * ### 停止
 * ```cpp
 * rs485.Stop();  // 停止 DMA 接收，方向引脚回到接收态
 * ```
 *
 * @copyright Copyright (c) 2026
 */

#pragma once

#include "uart.hpp"

#include <stdint.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>

/**
 * @brief RS485 半双工驱动
 *
 * 基于 UART DMA + GPIO 方向控制引脚实现半双工收发。
 * 发送前拉高方向引脚，完成后自动切回接收。
 */
class Rs485 final : public RxStream
{
    friend void rs485_uart_callback(const struct device* dev, struct uart_event* evt, void* user_data);

public:
    /**
     * @brief RS485 初始化参数
     */
    struct Config : public RxStream::Config 
    {
        const struct gpio_dt_spec* dir = nullptr;  // 方向控制 GPIO
        int tx_level = 1;        // 发送时 GPIO 电平
        int rx_level = 0;        // 接收时 GPIO 电平
        int32_t tx_timeout = 0;  // 发送超时（ms）
    };

    bool     Init(const struct device* dev, const RxStream::Config& cfg) override;
    bool     Init(const struct device* dev, const Config& cfg);
    void     SetNotify(struct k_sem* sem) override;
    uint16_t Read(uint8_t* buf, uint16_t max_len) override;
    bool     Send(const uint8_t* data, uint32_t len);
    void     Stop();

    bool IsReady() const { return ready_; }
    bool IsTxBusy() const { return tx_busy_; }

private:
    static constexpr uint16_t kMaxBufSize = 512;
    static constexpr uint16_t kTxBufSize  = 256;

    bool SetDirection(int level);
    void StoreRx(const uint8_t* data, uint16_t len);

    const struct device* dev_       = nullptr;  // UART 设备
    const struct gpio_dt_spec* dir_ = nullptr;  // 方向 GPIO

    // DMA 双缓冲
    uint8_t  dma_buf_[2][kMaxBufSize] {};
    uint8_t  rx_buf_[kMaxBufSize * 2] {};    // 接收合并缓冲
    uint8_t  tx_buf_[kTxBufSize] {};         // 发送缓冲
    uint16_t dma_buf_size_ = 0;              // DMA 单缓冲大小
    uint16_t head_         = 0;              // 环形缓冲写指针
    uint16_t tail_         = 0;              // 环形缓冲读指针
    uint8_t  cur_buf_      = 0;              // 当前 DMA 缓冲索引
    int32_t  rx_timeout_   = 0;              // 接收超时（ms）
    int32_t  tx_timeout_   = 0;              // 发送超时（ms）
    int      tx_level_     = 1;              // 发送方向 GPIO 电平
    int      rx_level_     = 0;              // 接收方向 GPIO 电平
    bool     ready_        = false;
    bool     tx_busy_      = false;

    k_sem* notify_sem_ = nullptr;
};
