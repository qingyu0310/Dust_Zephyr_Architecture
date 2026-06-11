/**
 * @file can.hpp
 * @author qingyu
 * @brief CAN 总线驱动 — 中断接收回调 + 阻塞发送
 * @version 0.2
 * @date 2026-05-10
 *
 * # CAN 使用说明
 *
 * ## 设备树
 *
 * 项目 overlay 中定义：
 * ```dts
 * aliases {
 *     can1-user = &mcan0;
 * };
 * &mcan0 {
 *     status = "okay";
 *     bitrate = <1000000>;
 * };
 * ```
 *
 * ### Kconfig
 * ```kconfig
 * config TRD_CAN_TX
 *     select COM_CAN
 * ```
 *
 * ### 初始化
 * ```cpp
 * static Can can{};
 *
 * void init() {
 *     const struct device *dev = DEVICE_DT_GET(DT_ALIAS(can1_user));
 *     struct can_filter filter{};
 *     filter.id = 0x200;
 *     filter.mask = CAN_EXT_ID_MASK;
 *     can.Init(dev, filter);
 *     can.SetRxCallback([](can_frame &frame, void*) {
 *         // 处理接收帧
 *     });
 * }
 * ```
 *
 * ### 发送
 * ```cpp
 * struct can_frame frame{};
 * frame.id = 0x200;
 * frame.dlc = 8;
 * frame.data[0..7] = ...;
 * can.Send(&frame);                      // 阻塞发送（K_NO_WAIT）
 * ```
 *
 * ### TX 完成通知
 * ```cpp
 * can.SetTxCallback([](const device *dev, int error, void*) {
 *     // 发送完成或出错
 * });
 * ```
 *
 * @copyright Copyright (c) 2026
 */

#pragma once

#include "zephyr/device.h"
#include <zephyr/drivers/can.h>

/**
 * @brief CAN 总线索引枚举
 */
enum CanIndex : uint8_t
{
    USER_CAN1 = 0,
    USER_CAN2,
    USER_CAN3,
    USER_CAN4,
    USER_CAN5,
};

/**
 * @brief CAN 总线驱动
 *
 * 支持中断接收回调、阻塞发送、发送完成回调。
 */
class Can final
{
public:
    using TxCallback = void (*)(const struct device *dev, int error, void *user_data);
    using RxCallback = void (*)(struct can_frame &frame, void *user_data);


    bool Init(const struct device *dev, const struct can_filter &filter,
              can_mode_t ctrl_mode = CAN_MODE_NORMAL);
    void SetTxCallback(TxCallback cb, void *user_data = nullptr);
    void SetRxCallback(RxCallback cb, void *user_data = nullptr);
    bool Send(const struct can_frame *frame);

    const struct device *Device() const { return dev_; }

private:
    // Zephyr CAN TX 完成回调（静态函数，转进对象）
    static void tx_callback(const struct device *dev, int error, void *user_data);

    // Zephyr CAN RX 回调（静态函数，转进对象）
    static void rx_callback(const struct device *dev, struct can_frame *frame,
                            void *user_data);

    const struct device *dev_{};        // CAN 设备
    int filter_id_ = -1;                // 接收过滤器 ID

    TxCallback tx_cb_      = nullptr;   // 外部 TX 回调
    void*      tx_cb_data_ = nullptr;   // TX 回调用户数据

    RxCallback rx_cb_      = nullptr;   // 外部 RX 回调
    void*      rx_cb_data_ = nullptr;   // RX 回调用户数据
};
