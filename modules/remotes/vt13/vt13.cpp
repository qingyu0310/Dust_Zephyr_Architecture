/**
 * @file vt13.cpp
 * @author qingyu
 * @brief
 * @version 0.1
 * @date 2026-05-12
 *
 * @copyright Copyright (c) 2026
 *
 */

#include "vt13.hpp"
#include "protocol_base.hpp"

using namespace topic::remote_to;

namespace vt13 {

enum SwitchCNS
{
    SWITCH_C = 0,
    SWITCH_N,
    SWITCH_S,
};

struct RawData
{
    uint8_t start_of_frame_1 = 0xA9;
    uint8_t start_of_frame_2 = 0x53;
    uint64_t channel_0  : 11;
    uint64_t channel_1  : 11;
    uint64_t channel_2  : 11;
    uint64_t channel_3  : 11;
    uint64_t cns        : 2;
    uint64_t pause      : 1;
    uint64_t fn_1       : 1;
    uint64_t fn_2       : 1;
    uint64_t wheel      : 11;
    uint64_t trigger    : 1;
    uint64_t reserved_1 : 3;
    int16_t  mouse_x;
    int16_t  mouse_y;
    int16_t  mouse_z;
    uint8_t  mouse_l    : 2;
    uint8_t  mouse_r    : 2;
    uint8_t  mouse_m    : 2;
    uint8_t  reserved_2 : 2;
    uint16_t keyboard;
    uint16_t crc16;
} __attribute__((packed));

struct Switch
{
    uint8_t fn1, fn2;
    uint8_t trigger;
    uint8_t pause;
    uint8_t cns;
    float wheel;
};

struct OutputData
{
    Channel ch;
    Switch sw;
    Mouse mouse;
    Keyboard keyboard;
};

inline static void fillOutputData(const OutputData& od, Message& pub)
{
    processChannel(pub, od);

    switch (od.sw.cns)
    {
        case SWITCH_C:
            pub.chassis_mode = ChassisMode::Spin;
            break;
        case SWITCH_N:
            pub.chassis_mode = ChassisMode::Normal;
            break;
        case SWITCH_S:
            break;
        default:
            pub.chassis_mode = ChassisMode::Normal;
            break;
    }

    pub.reload_ctrl   = od.sw.fn1     ? StartMode::On : StartMode::Off;
    pub.shoot_ctrl    = od.sw.fn2     ? StartMode::On : StartMode::Off;
    pub.autoaim_ctrl  = od.sw.fn2     ? StartMode::On : StartMode::Off;
    pub.supercap_ctrl = od.keyboard.v ? StartMode::On : StartMode::Off;

    pub.version++;
}

bool validate(const uint8_t* buffer, uint8_t len)
{
    if (len != kFrameSizeVT13) return false;
    if (buffer[0] != 0xA9 || buffer[1] != 0x53) return false;

    const RawData* raw_data = reinterpret_cast<RawData const*>(buffer);

    constexpr uint16_t kMaxChannel = 1684;
    constexpr uint16_t kMinChannel = 364;

    if (raw_data->channel_0 < kMinChannel || raw_data->channel_0 > kMaxChannel) return false;
    if (raw_data->channel_1 < kMinChannel || raw_data->channel_1 > kMaxChannel) return false;
    if (raw_data->channel_2 < kMinChannel || raw_data->channel_2 > kMaxChannel) return false;
    if (raw_data->channel_3 < kMinChannel || raw_data->channel_3 > kMaxChannel) return false;
    if (raw_data->wheel     < kMinChannel || raw_data->wheel     > kMaxChannel) return false;

    return true;
}

bool decode(const uint8_t* buffer, uint8_t len, Message& pub)
{
    const RawData* raw_data = reinterpret_cast<RawData const*>(buffer);

    static KeyboardState keyboard_state_{};
    
    OutputData od{};

    constexpr int16_t kCenter = 1024;
    constexpr float   kInvChannelSpan = 1.0f / 660.0f;

    od.ch.chassisx  = normChannelInv(raw_data->channel_0, kCenter, kInvChannelSpan);
    od.ch.chassisy  = normChannelInv(raw_data->channel_1, kCenter, kInvChannelSpan);
    od.ch.yaw       = normChannelInv(raw_data->channel_2, kCenter, kInvChannelSpan);
    od.ch.pitch     = normChannelInv(raw_data->channel_3, kCenter, kInvChannelSpan);

    od.sw.fn1       = raw_data->fn_1;
    od.sw.fn2       = raw_data->fn_2;
    od.sw.trigger   = raw_data->trigger;
    od.sw.pause     = raw_data->pause;
    od.sw.cns       = raw_data->cns;
    od.sw.wheel     = normChannelInv(raw_data->wheel, kCenter, kInvChannelSpan);

    constexpr float kMouseScaleX = 30.0f;
    constexpr float kMouseScaleY = 2.0f;
    constexpr float kMouseScaleZ = 1.0f;

    od.mouse.x      = normMouse(static_cast<float>(raw_data->mouse_x), kMouseScaleX);
    od.mouse.y      = normMouse(static_cast<float>(raw_data->mouse_y), kMouseScaleY);
    od.mouse.z      = normMouse(static_cast<float>(raw_data->mouse_z), kMouseScaleZ);

    od.mouse.left   = raw_data->mouse_l != 0;
    od.mouse.right  = raw_data->mouse_r != 0;

    Keyboard cur_raw { .all = raw_data->keyboard };
    keyboard_state_.Process(od.keyboard, cur_raw);

    fillOutputData(od, pub);
    return true;
}

bool dataprocess(const uint8_t* buffer, uint8_t len, Message& pub)
{
    return decode(buffer, len, pub);
}

}
