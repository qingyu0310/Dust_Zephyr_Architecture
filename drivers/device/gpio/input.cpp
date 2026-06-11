/**
 * @file input.cpp
 * @author qingyu
 * @brief GPIO 输入驱动
 * @version 0.1
 * @date 2026-06-11
 *
 * @copyright Copyright (c) 2026
 *
 */

#include "input.hpp"
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(gpio_in, LOG_LEVEL_INF);

/**
 * @brief 初始化 GPIO 输入引脚
 * @param spec         设备树 GPIO 规格
 * @param extra_flags  额外 GPIO 标志（默认 GPIO_INPUT）
 */
bool Input::init(const gpio_dt_spec spec, gpio_flags_t extra_flags)
{
    spec_ = spec;
    if (!device_is_ready(spec.port)) {
        LOG_ERR("device not ready %s", spec.port->name);
        return false;
    }
    int ret = gpio_pin_configure_dt(&spec, extra_flags);
    if (ret != 0) {
        LOG_ERR("config pin %d fail %d", spec.pin, ret);
        return false;
    }
    LOG_INF("gpio_in ready pin=%d", spec.pin);
    return true;
}
