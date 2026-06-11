/**
 * @file trd_gpio.cpp
 * @author qingyu
 * @brief
 * @version 0.1
 * @date 2026-04-24
 *
 * @copyright Copyright (c) 2026
 *
 */

#include "trd_gpio.hpp"
#include "thread.hpp"
#include "output.hpp"
#include "timer.hpp"
#include "input.hpp"
#include "zephyr/sys/printk.h"

#define GPIO_GET(node_id)   GPIO_DT_SPEC_GET(DT_NODELABEL(node_id), gpios)

namespace thread::output {

static Thread<2048> thread_{};

static Output led_alert{};

static void Task(void*, void*, void*)
{
    static constexpr uint32_t kPeriodMs = 1;
    Timer timer(1000);

    for (;;)
    {
        const int64_t tick_start = k_uptime_get();

        timer.Update();

        timer.Clock(([](){
            printk("tick\n");
        }));

        const int64_t elapsed = k_uptime_get() - tick_start;
        const int64_t remain  = static_cast<int64_t>(kPeriodMs) - elapsed;
        if (remain > 0) {
            k_msleep(remain);
        }
    }
}

void thread_init()
{
    // led_alert.init(GPIO_GET(led_alert));
}

void thread_start(uint8_t prio)
{
    thread_.Start(Task, prio);
}

} // namespace thread::output

namespace thread::input {

static Thread<> thread_{};

/*  占位：预留用于按键/传感器输入，待实现 */
static void Task(void*, void*, void*)
{
    for (;;)
    {
        k_msleep(100);
    }
}

void thread_init()
{
    
}

void thread_start(uint8_t prio)
{
    thread_.Start(Task, prio);
}


} // namespace thread::input
