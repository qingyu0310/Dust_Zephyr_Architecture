/**
 * @file output.hpp
 * @author qingyu
 * @brief
 * @version 0.1
 * @date 2026-04-24
 *
 * @copyright Copyright (c) 2026
 *
 */

#pragma once

#include "zephyr/device.h"
#include <zephyr/drivers/gpio.h>

class Output final
{
public:
    bool init(const gpio_dt_spec spec, gpio_flags_t extra_flags = GPIO_OUTPUT);

    bool Set(bool state) 
    {
        int ret = gpio_pin_set_dt(&spec_, state);
        return ret == 0;
    }

    bool Toggle()
    {
        int ret = gpio_pin_toggle_dt(&spec_);
        return ret == 0;
    }

private:
    gpio_dt_spec spec_{};
};
