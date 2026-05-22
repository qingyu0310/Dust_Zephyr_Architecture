/**
 * @file dr16.cpp
 * @author qingyu
 * @brief
 * @version 0.1
 * @date 2026-05-12
 *
 * @copyright Copyright (c) 2026
 *
 */

#include "dr16.hpp"
#include "protocol_base.hpp"

using namespace topic::remote_to;

namespace dr16 {

enum SwitchStatus
{
    SWITCH_UP   = 1,
    SWITCH_MID  = 3,
    SWITCH_DOWN = 2,
};

struct Switch
{
    uint8_t sw1;
    uint8_t sw2;
};

struct OutputData
{
    Channel ch;
    Switch sw;
    Mouse mouse;
    Keyboard keyboard;
};

static bool isValidSwitch(uint8_t sw)
{
    return sw == SWITCH_UP || sw == SWITCH_MID || sw == SWITCH_DOWN;
}

inline static void fillOutputData(const OutputData& od, Message& pub)
{
    processChannel(pub, od);

    switch (od.sw.sw1)
    {
        case SWITCH_UP:
            pub.chassis_mode = ChassisMode::Spin;
            break;
        case SWITCH_MID:
            pub.chassis_mode = ChassisMode::Normal;
            break;
        default:
            pub.chassis_mode = ChassisMode::Normal;
            break;
    }

    switch (od.sw.sw2)
    {
        case SWITCH_UP:
            pub.shoot_ctrl = StartMode::On;
            break;
        case SWITCH_MID:
            pub.shoot_ctrl  = StartMode::Off;
            pub.reload_ctrl = StartMode::Off;
            break;
        case SWITCH_DOWN:
            pub.reload_ctrl = StartMode::On;
            break;
        default:
            pub.shoot_ctrl  = StartMode::Off;
            pub.reload_ctrl = StartMode::Off;
            break;
    }

    pub.version++;
}

bool validate(const uint8_t* buffer, uint8_t len)
{
    if (len != kFrameSizeDR16) return false;

    uint16_t ch0 = ( buffer[0]       | (buffer[1] << 8)) & 0x07FF;
    uint16_t ch1 = ((buffer[1] >> 3) | (buffer[2] << 5)) & 0x07FF;
    uint16_t ch2 = ((buffer[2] >> 6) | (buffer[3] << 2)  | (buffer[4] << 10)) & 0x07FF;
    uint16_t ch3 = ((buffer[4] >> 1) | (buffer[5] << 7)) & 0x07FF;

    if (ch0 < 364 || ch0 > 1684 || ch1 < 364 || ch1 > 1684 || ch2 < 364 || ch2 > 1684 || ch3 < 364 || ch3 > 1684) {
        return false;
    }

    uint8_t sw1 = ((buffer[5] >> 4) & 0x0C) >> 2;
    uint8_t sw2 = ((buffer[5] >> 4) & 0x03);

    return isValidSwitch(sw1) && isValidSwitch(sw2);
}

bool decode(const uint8_t* buffer, uint8_t len, Message& pub)
{
    uint16_t ch0 = ( buffer[0]       | (buffer[1] << 8)) & 0x07FF;
    uint16_t ch1 = ((buffer[1] >> 3) | (buffer[2] << 5)) & 0x07FF;
    uint16_t ch2 = ((buffer[2] >> 6) | (buffer[3] << 2)  | (buffer[4] << 10)) & 0x07FF;
    uint16_t ch3 = ((buffer[4] >> 1) | (buffer[5] << 7)) & 0x07FF;

    static KeyboardState keyboard_state_{};

    OutputData od{};

    od.sw.sw1    = ((buffer[5] >> 4) & 0x0C) >> 2;
    od.sw.sw2    = ((buffer[5] >> 4) & 0x03);

    int16_t dx   = buffer[6]  | (buffer[7] << 8);
    int16_t dy   = buffer[8]  | (buffer[9] << 8);
    int16_t dz   = buffer[10] | (buffer[11] << 8);

    constexpr int16_t kCenter = 1024;
    constexpr float   kInvChannelSpan = 1.0f / 660.0f;

    od.ch.chassisx = normChannelInv(ch0, kCenter, kInvChannelSpan);
    od.ch.chassisy = normChannelInv(ch1, kCenter, kInvChannelSpan);
    od.ch.yaw      = normChannelInv(ch2, kCenter, kInvChannelSpan);
    od.ch.pitch    = normChannelInv(ch3, kCenter, kInvChannelSpan);

    constexpr float kMouseScaleX = 30.0f;
    constexpr float kMouseScaleY = 2.0f;
    constexpr float kMouseScaleZ = 1.0f;

    od.mouse.x     = normMouse(static_cast<float>(dx), kMouseScaleX);
    od.mouse.y     = normMouse(static_cast<float>(dy), kMouseScaleY);
    od.mouse.z     = normMouse(static_cast<float>(dz), kMouseScaleZ);

    od.mouse.left  = buffer[12] != 0;
    od.mouse.right = buffer[13] != 0;

    Keyboard cur_raw { .all = buffer[14] };
    keyboard_state_.Process(od.keyboard, cur_raw);

    fillOutputData(od, pub);

    return true;
}

bool dataprocess(const uint8_t* buffer, uint8_t len, Message& pub)
{
    return decode(buffer, len, pub);
}

}
