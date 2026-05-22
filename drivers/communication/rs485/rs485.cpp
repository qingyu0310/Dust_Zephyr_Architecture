/**
 * @file rs485.cpp
 * @author qingyu
 * @brief RS485 half-duplex driver implementation.
 * @version 0.1
 * @date 2026-05-23
 */

#include "rs485.hpp"

#include <string.h>

#ifdef CONFIG_COM_RS485

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

bool Rs485::Init(const struct device* dev, const RxStream::Config& cfg)
{
    Config rs485_cfg {};
    rs485_cfg.buf_size   = cfg.buf_size;
    rs485_cfg.rx_timeout = cfg.rx_timeout;
    return Init(dev, rs485_cfg);
}

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
        return false;
    }

    if (dir_ != nullptr) {
        if (!device_is_ready(dir_->port)) {
            return false;
        }
        if (gpio_pin_configure_dt(dir_, GPIO_OUTPUT_INACTIVE) != 0) {
            return false;
        }
        if (!SetDirection(rx_level_)) {
            return false;
        }
    }

    int ret = uart_callback_set(dev_, rs485_uart_callback, this);
    if (ret < 0) return false;

    uint16_t bs = cfg.buf_size > kMaxBufSize ? kMaxBufSize : cfg.buf_size;
    if (bs == 0) {
        bs = kMaxBufSize;
    }

    dma_buf_size_ = bs;
    ret = uart_rx_enable(dev_, dma_buf_[0], dma_buf_size_, rx_timeout_);
    if (ret < 0) return false;

    ready_ = true;
    return true;
}

void Rs485::SetNotify(struct k_sem* sem)
{
    notify_sem_ = sem;
}

uint16_t Rs485::Read(uint8_t* buf, uint16_t max_len)
{
    uint16_t cnt = 0;
    while (cnt < max_len && tail_ != head_) {
        buf[cnt++] = rx_buf_[tail_];
        tail_ = (tail_ + 1) % sizeof(rx_buf_);
    }
    return cnt;
}

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

void Rs485::Stop()
{
    ready_ = false;
    tx_busy_ = false;
    uart_rx_disable(dev_);
    (void)SetDirection(rx_level_);
}

bool Rs485::SetDirection(int level)
{
    if (dir_ == nullptr) {
        return true;
    }

    return gpio_pin_set_dt(dir_, level) == 0;
}

void Rs485::StoreRx(const uint8_t* data, uint16_t len)
{
    if (data == nullptr || len == 0) {
        return;
    }

    for (uint16_t i = 0; i < len; i++)
    {
        uint16_t next = (head_ + 1) % sizeof(rx_buf_);
        if (next != tail_) {
            rx_buf_[head_] = data[i];
            head_ = next;
        }
    }

    if (notify_sem_) {
        k_sem_give(notify_sem_);
    }
}

#endif // CONFIG_COM_RS485
