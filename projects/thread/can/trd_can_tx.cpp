/**
 * @file trd_can_tx.cpp
 * @author qingyu
 * @brief
 * @version 0.1
 * @date 2026-04-30
 *
 * @copyright Copyright (c) 2026
 *
 */

#include "trd_can_tx.hpp"
#include <zephyr/drivers/gpio.h>
#include "to_can_tx.hpp"
#include "thread.hpp"
#include <string.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/byteorder.h>
#include "can.hpp"
#include "Irq_handlers.h"

namespace thread::can {

// k_msgq vs zbus：zbus 多个发布者共用一个 channel 会互相覆盖；
// k_msgq 内部拷贝数据，多 put 一 get 天然支持多发布者，且满时丢帧不阻塞。
static Thread<> thread_{};
static Can user_can1{};

#ifdef CONFIG_COM_CAN_STBY
/* 在 projects/boards/ 下对应 board 的 overlay 中定义 stby_gpio 节点来提供引脚 */
#define STBY_GPIO_NODE DT_NODELABEL(can_stby)
static const struct gpio_dt_spec stby_ = GPIO_DT_SPEC_GET(STBY_GPIO_NODE, gpios);
#endif

static void Task(void*, void*, void*)
{
    can_frame tx{};
    topic::to_can_tx::Message msg{};

    for (;;)
    {
        k_msgq_get(&user_can1_msgq, &msg, K_FOREVER);
        tx.id  = msg.tx_id;
        tx.dlc = 8;
        memcpy(tx.data, msg.data, 8);

        user_can1.Send(&tx);

        // if (!user_can1.Send(&tx)) {
        //     printk("trd_can_tx: send failed id=0x%x\n", tx.id);
        // }
    }
}

void thread_init()
{
#ifdef CONFIG_COM_CAN_STBY
    Can::InitStby(&stby_);
#endif
    {
        const device* dev = DEVICE_DT_GET(DT_ALIAS(user_can1));
        if (!device_is_ready(dev)) return;
        const can_filter filter { .id = 0, .mask = 0, .flags = 0 };
        user_can1.Init(dev, filter);
        user_can1.SetRxCallback(user_can1_rx_callback);
    }
}

void thread_start(uint8_t prio)
{
    thread_.Start(Task, prio);
}


} // namespace thread::can
