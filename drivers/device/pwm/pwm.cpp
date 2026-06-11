/**
 * @file pwm.cpp
 * @author qingyu
 * @brief PWM 输出驱动
 * @version 0.1
 * @date 2026-06-11
 *
 * @copyright Copyright (c) 2026
 *
 */

#include "pwm.hpp"
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(pwm, LOG_LEVEL_INF);

/**
 * @brief 初始化 PWM 输出
 * @param spec  设备树 PWM 规格
 */
bool Pwm::init(const pwm_dt_spec spec)
{
    spec_ = spec;
    if (!device_is_ready(spec.dev)) {
        LOG_ERR("device not ready %s", spec.dev->name);
        return false;
    }

    if (!SetPulse(0)) {
        LOG_ERR("set pulse 0 fail");
        return false;
    }
    LOG_INF("pwm ready ch=%d", spec.channel);
    return true;
}

bool Pwm::SetPulse(uint32_t pulse)
{
    int ret = pwm_set_pulse_dt(&spec_, pulse);
    if (ret != 0) {
        LOG_ERR("set pulse %u fail %d ch=%u", pulse, ret, spec_.channel);
        return false;
    }
    return true;
}

bool Pwm::SetDuty(float duty)
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

bool Pwm::SetPeriodAndPulse(uint32_t period, uint32_t pulse)
{
    int ret = pwm_set_dt(&spec_, period, pulse);
    if (ret != 0) {
        LOG_ERR("set period=%u pulse=%u fail %d ch=%u", period, pulse, ret, spec_.channel);
        return false;
    }

    spec_.period = period;
    return true;
}

bool Pwm::Stop()
{
    return SetPulse(0);
}
