/**
 * @file ws2812b.hpp
 * @brief WS2812B driver using HPM GPIO fast register writes.
 */

#pragma once

#include <stdint.h>

#include <zephyr/irq.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/util.h>

#include <hpm_clock_drv.h>
#include <hpm_csr_drv.h>
#include <hpm_gpio_drv.h>

class Ws2812b final
{
public:
    struct Color {
        uint8_t r;
        uint8_t g;
        uint8_t b;
    };

    enum class ColorOrder {
        RGB,
        RBG,
        GRB,
        GBR,
        BRG,
        BGR,
    };

    bool init(GPIO_Type *gpio, uint32_t port, uint8_t pin)
    {
        if ((gpio == nullptr) || (pin >= 32U)) {
            ready_ = false;
            return false;
        }

        gpio_ = gpio;
        port_ = port;
        mask_ = BIT(pin);
        set_reg_ = &gpio_->DO[port_].SET;
        clear_reg_ = &gpio_->DO[port_].CLEAR;

        gpio_->OE[port_].SET = mask_;
        *clear_reg_ = mask_;
        k_busy_wait(reset_us);

        uint32_t cpu_hz = clock_get_frequency(clock_cpu0);
        if (cpu_hz == 0U) {
            cpu_hz = fallback_cpu_hz;
        }

        t0h_cycles_ = ns_to_cycles(cpu_hz, t0h_ns);
        t1h_cycles_ = ns_to_cycles(cpu_hz, t1h_ns);
        bit_cycles_ = ns_to_cycles(cpu_hz, bit_ns);
        clamp_timing();

        ready_ = true;
        return true;
    }

    void set_color_order(ColorOrder order)
    {
        order_ = order;
    }

    bool set(Color color)
    {
        if (!ready_) {
            return false;
        }

        uint8_t first  = 0;
        uint8_t second = 0;
        uint8_t third  = 0;
        map_color(color, first, second, third);

        const unsigned int key = irq_lock();
        send_byte(first);
        send_byte(second);
        send_byte(third);
        *clear_reg_ = mask_;
        irq_unlock(key);

        k_busy_wait(reset_us);
        return true;
    }

    bool off()
    {
        return set({0, 0, 0});
    }

private:
    static constexpr uint32_t fallback_cpu_hz = 480000000U;

    static constexpr uint32_t reset_us = 80U;
    static constexpr uint32_t t0h_ns = 350U;
    static constexpr uint32_t t1h_ns = 700U;
    static constexpr uint32_t bit_ns = 1250U;

    static uint32_t ns_to_cycles(uint32_t cpu_hz, uint32_t ns)
    {
        return MAX(1U, (uint32_t)(((uint64_t)cpu_hz * ns) / 1000000000ULL));
    }

    void clamp_timing()
    {
        bit_cycles_ = MAX(bit_cycles_, 4U);

        if (t1h_cycles_ >= bit_cycles_) {
            t1h_cycles_ = bit_cycles_ - 1U;
        }
        if (t0h_cycles_ >= t1h_cycles_) {
            t0h_cycles_ = MAX(1U, t1h_cycles_ / 2U);
        }
    }

    static inline uint32_t cycle_now()
    {
        return (uint32_t)hpm_csr_get_core_mcycle();
    }

    static inline void wait_until(uint32_t deadline)
    {
        while ((int32_t)(cycle_now() - deadline) < 0) {
            __asm__ volatile("nop");
        }
    }

    __attribute__((always_inline)) inline void send_bit(bool bit)
    {
        const uint32_t start = cycle_now();
        *set_reg_ = mask_;
        wait_until(start + (bit ? t1h_cycles_ : t0h_cycles_));
        *clear_reg_ = mask_;
        wait_until(start + bit_cycles_);
    }

    void send_byte(uint8_t value)
    {
        for (uint8_t mask = 0x80U; mask != 0U; mask >>= 1U) {
            send_bit((value & mask) != 0U);
        }
    }

    void map_color(Color color, uint8_t &first, uint8_t &second, uint8_t &third)
    {
        switch (order_) {
            case ColorOrder::RGB:
                first = color.r;
                second = color.g;
                third = color.b;
                break;
            case ColorOrder::RBG:
                first = color.r;
                second = color.b;
                third = color.g;
                break;
            case ColorOrder::GRB:
                first = color.g;
                second = color.r;
                third = color.b;
                break;
            case ColorOrder::GBR:
                first = color.g;
                second = color.b;
                third = color.r;
                break;
            case ColorOrder::BRG:
                first = color.b;
                second = color.r;
                third = color.g;
                break;
            case ColorOrder::BGR:
                first = color.b;
                second = color.g;
                third = color.r;
                break;
            default:
                break;
        }
    }

    GPIO_Type *gpio_{nullptr};
    uint32_t port_{0};
    uint32_t mask_{0};
    volatile uint32_t *set_reg_{nullptr};
    volatile uint32_t *clear_reg_{nullptr};
    bool ready_{false};
    ColorOrder order_{ColorOrder::GRB};
    uint32_t t0h_cycles_{1};
    uint32_t t1h_cycles_{1};
    uint32_t bit_cycles_{1};
};
