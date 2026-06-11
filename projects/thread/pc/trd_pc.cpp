/**
 * @file trd_pc.cpp
 * @author qingyu
 * @brief PC 通信线程 — USB CDC ACM 回环测试
 * @version 0.2
 * @date 2026-06-11
 *
 * @copyright Copyright (c) 2026
 *
 */

#include "trd_pc.hpp"
#include "thread.hpp"
#include <zephyr/kernel.h>
#include "usb.hpp"
#include <zephyr/devicetree.h>

namespace thread::pc {

static Thread<2048> thread_ {};
static Usb usb_ {};

static void Task(void*, void*, void*)
{
    for (;;)
    {
        k_sleep(K_MSEC(1000));
    }
}

void thread_init()
{
    Usb::Config cfg {};
    cfg.busid = 0;
    cfg.reg_base = DT_REG_ADDR(DT_NODELABEL(cherryusb_usb0));
    cfg.buf_size = 512;

    while (!usb_.Init(cfg)) {
        // printf
    };
}

void thread_start(uint8_t prio)
{
    if (!usb_.IsReady()) {
        return;
    }

    thread_.Start(Task, prio);
}

} // namespace thread::pc
