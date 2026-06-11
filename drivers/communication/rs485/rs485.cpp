/**
 * @file rs485.cpp
 * @author qingyu
 * @brief RS485 half-duplex driver implementation.
 * @version 0.1
 * @date 2026-05-23
 */

#include "rs485.hpp"
#include <zephyr/logging/log.h>
#include <string.h>

LOG_MODULE_REGISTER(rs485, LOG_LEVEL_INF);

/**
 * @brief UART 事件回调（RS485 内部使用）
 *
 * 处理数据接收、缓冲切换、DMA 重启、发送完成及异常等事件。
 * 发送完成后自动将方向引脚切回接收。
 */
void rs485_uart_callback(const struct device* dev, struct uart_event* evt, void* user_data)
{
    auto* self = static_cast<Rs485*>(user_data);

    switch (evt->type)
    {
        case UART_RX_RDY:
        {
            const uint8_t* data = evt->data.rx.buf + evt->data.rx.offset;
            uint16_t len = evt->data.rx.len;
            self->StoreRx(data, len);
            break;
        }
        case UART_RX_BUF_REQUEST:
        {
            self->cur_buf_ = 1 - self->cur_buf_;
            (void)uart_rx_buf_rsp(dev, self->dma_buf_[self->cur_buf_],
                                  self->dma_buf_size_);
            break;
        }
        case UART_RX_DISABLED:
        {
            if (!self->ready_) {
                break;
            }

            self->cur_buf_ = 0;
            int ret = uart_rx_enable(dev, self->dma_buf_[self->cur_buf_],
                                     self->dma_buf_size_, self->rx_timeout_);
            if (ret < 0) {
                self->ready_ = false;
            }
            break;
        }
        case UART_TX_DONE:
        case UART_TX_ABORTED:
        {
            self->tx_busy_ = false;
            (void)self->SetDirection(self->rx_level_);
            break;
        }
        default:
            break;
    }
}

/**
 * @brief 通过 RxStream 通用配置初始化
 */
bool Rs485::Init(const struct device* dev, const RxStream::Config& cfg)
{
    Config rs485_cfg {};
    rs485_cfg.buf_size   = cfg.buf_size;
    rs485_cfg.rx_timeout = cfg.rx_timeout;
    return Init(dev, rs485_cfg);
}

/**
 * @brief 通过 RS485 完整配置初始化
 */
bool Rs485::Init(const struct device* dev, const Config& cfg)
{
    dev_         = dev;
    dir_         = cfg.dir;
    head_        = 0;
    tail_        = 0;
    cur_buf_     = 0;
    rx_timeout_  = cfg.rx_timeout;
    tx_timeout_  = cfg.tx_timeout;
    tx_level_    = cfg.tx_level;
    rx_level_    = cfg.rx_level;
    ready_       = false;
    tx_busy_     = false;

    if (!device_is_ready(dev_)) {
        LOG_ERR("device not ready %s", dev->name);
        return false;
    }

    if (dir_ != nullptr) {
        if (!device_is_ready(dir_->port)) {
            LOG_ERR("dir gpio not ready");
            return false;
        }
        if (gpio_pin_configure_dt(dir_, GPIO_OUTPUT_INACTIVE) != 0) {
            LOG_ERR("dir gpio config fail");
            return false;
        }
        if (!SetDirection(rx_level_)) {
            LOG_ERR("set direction fail");
            return false;
        }
    }

    int ret = uart_callback_set(dev_, rs485_uart_callback, this);
    if (ret < 0) {
        LOG_ERR("callback_set fail %d", ret);
        return false;
    }

    uint16_t bs = cfg.buf_size > kMaxBufSize ? kMaxBufSize : cfg.buf_size;
    if (bs == 0) {
        bs = kMaxBufSize;
    }

    dma_buf_size_ = bs;
    ret = uart_rx_enable(dev_, dma_buf_[0], dma_buf_size_, rx_timeout_);
    if (ret < 0) {
        LOG_ERR("rx_enable fail %d", ret);
        return false;
    }

    ready_ = true;
    LOG_INF("rs485 ready %s", dev->name);
    return true;
}

/**
 * @brief 注册接收通知信号量
 */
void Rs485::SetNotify(struct k_sem* sem)
{
    notify_sem_ = sem;
}

/**
 * @brief 读取接收环形缓冲
 */
uint16_t Rs485::Read(uint8_t* buf, uint16_t max_len)
{
    uint16_t available = (head_ - tail_ + sizeof(rx_buf_)) % sizeof(rx_buf_);
    uint16_t cnt = (max_len < available) ? max_len : available;

    if (cnt > 0) {
        uint16_t to_end = sizeof(rx_buf_) - tail_;
        if (cnt <= to_end) {
            memcpy(buf, &rx_buf_[tail_], cnt);
        } else {
            memcpy(buf, &rx_buf_[tail_], to_end);
            memcpy(buf + to_end, &rx_buf_[0], cnt - to_end);
        }
        tail_ = (tail_ + cnt) % sizeof(rx_buf_);
    }

    return cnt;
}

/**
 * @brief 发送一帧（自动切方向引脚）
 */
bool Rs485::Send(const uint8_t* data, uint32_t len)
{
    if (!ready_) return false;
    if (data == nullptr || len == 0 || len > sizeof(tx_buf_)) return false;
    if (tx_busy_) return false;

    memcpy(tx_buf_, data, len);

    if (!SetDirection(tx_level_)) {
        return false;
    }

    tx_busy_ = true;
    int ret = uart_tx(dev_, tx_buf_, len, tx_timeout_);
    if (ret != 0) {
        tx_busy_ = false;
        (void)SetDirection(rx_level_);
        return false;
    }

    return true;
}

/**
 * @brief 停止 RS485 接收
 */
void Rs485::Stop()
{
    ready_ = false;
    tx_busy_ = false;
    uart_rx_disable(dev_);
    (void)SetDirection(rx_level_);
}

/**
 * @brief 设置方向引脚电平
 * @param level   GPIO 输出值
 */
bool Rs485::SetDirection(int level)
{
    if (dir_ == nullptr) {
        return true;
    }

    return gpio_pin_set_dt(dir_, level) == 0;
}

/**
 * @brief 将 DMA 收到的一帧数据存入环形缓冲
 */
void Rs485::StoreRx(const uint8_t* data, uint16_t len)
{
    if (data == nullptr || len == 0) {
        return;
    }

    // 计算可用空间（保留一格区分空/满）
    uint16_t used = (head_ - tail_ + sizeof(rx_buf_)) % sizeof(rx_buf_);
    uint16_t free = sizeof(rx_buf_) - 1 - used;
    if (len > free) {
        len = free;
    }
    if (len > 0) 
    {
        uint16_t to_end = sizeof(rx_buf_) - head_;
        if (len <= to_end) {
            memcpy(&rx_buf_[head_], data, len);
        } else {
            memcpy(&rx_buf_[head_], data, to_end);
            memcpy(&rx_buf_[0], data + to_end, len - to_end);
        }
        head_ = (head_ + len) % sizeof(rx_buf_);
    }

    if (notify_sem_) {
        k_sem_give(notify_sem_);
    }
}

