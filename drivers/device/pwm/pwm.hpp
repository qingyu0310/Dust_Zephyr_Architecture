/**
 * @file pwm.hpp
 * @author qingyu
 * @brief
 * @version 0.1
 * @date 2026-06-04
 *
 * @copyright Copyright (c) 2026
 *
 */

#pragma once

#include "zephyr/device.h"
#include <zephyr/drivers/pwm.h>
#include <zephyr/sys/printk.h>

class Pwm final
{
public:
    bool init(const pwm_dt_spec spec)
    {
        spec_ = spec;
        if (!device_is_ready(spec.dev)) {
            printk("Error: PWM device %s is not ready\n", spec.dev->name);
            return false;
        }

        return SetPulse(0);
    }

    bool SetPulse(uint32_t pulse)
    {
        int ret = pwm_set_pulse_dt(&spec_, pulse);
        if (ret != 0) {
            printk("Error %d: failed to set PWM pulse %u on channel %u\n",
                   ret, pulse, spec_.channel);
            return false;
        }

        return true;
    }

    bool SetDuty(float duty)
    {
        if (duty < 0.0f) {
            duty = 0.0f;
        }
        if (duty > 1.0f) {
            duty = 1.0f;
        }

        const uint32_t pulse = static_cast<uint32_t>(spec_.period * duty);
        return SetPulse(pulse);
    }

    bool SetPeriodAndPulse(uint32_t period, uint32_t pulse)
    {
        int ret = pwm_set_dt(&spec_, period, pulse);
        if (ret != 0) {
            printk("Error %d: failed to set PWM period %u pulse %u on channel %u\n",
                   ret, period, pulse, spec_.channel);
            return false;
        }

        spec_.period = period;
        return true;
    }

    bool Stop()
    {
        return SetPulse(0);
    }

private:
    pwm_dt_spec spec_{};
};
