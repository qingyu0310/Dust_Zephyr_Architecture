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

#include <zephyr/drivers/pwm.h>

class Pwm final
{
public:
    bool init(const pwm_dt_spec spec);
    bool SetPulse(uint32_t pulse);
    bool SetDuty(float duty);
    bool SetPeriodAndPulse(uint32_t period, uint32_t pulse);
    bool Stop();

private:
    pwm_dt_spec spec_{};
};
