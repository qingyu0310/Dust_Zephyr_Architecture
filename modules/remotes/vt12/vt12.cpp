/**
 * @file vt12.cpp
 * @author qingyu
 * @brief
 * @version 0.1
 * @date 2026-05-12
 *
 * @copyright Copyright (c) 2026
 *
 */

#include "vt12.hpp"
#include "protocol_base.hpp"

using namespace topic::remote_to;

namespace vt12 {

struct OutputData
{
    Mouse mouse;
    Keyboard keyboard;
};

inline static void fillOutputData(const OutputData& od, Message& pub)
{
    pub.chassisy = od.keyboard.w ? 1.0f : od.keyboard.s ? -1.0f : 0.0f;
    pub.chassisx = od.keyboard.a ? 1.0f : od.keyboard.d ? -1.0f : 0.0f;
    pub.pitch    = od.mouse.y;
    pub.yaw      = od.mouse.x;

    if (od.keyboard.shift) {
        pub.chassis_mode = ChassisMode::Spin;
    }  else {
        pub.chassis_mode = ChassisMode::Normal;
    }

    pub.shoot_ctrl    = od.keyboard.f  ? StartMode::On : StartMode::Off;
    pub.reload_ctrl   = od.mouse.left  ? StartMode::On : StartMode::Off;
    pub.autoaim_ctrl  = od.mouse.right ? StartMode::On : StartMode::Off;
    pub.supercap_ctrl = od.keyboard.v  ? StartMode::On : StartMode::Off;

    pub.version++;
}

bool validate(const uint8_t* buffer, uint8_t len)
{
    if (len != kFrameSizeVT12) return false;
    if (buffer[0] != 0xA5) return false;

    return true;
}

bool decode(const uint8_t* buffer, uint8_t len, Message& pub)
{
    static KeyboardState keyboard_state_{};

    OutputData od{};

    int16_t dx = (int16_t)((uint16_t)buffer[6]  | ((uint16_t)buffer[7]  << 8));
    int16_t dy = (int16_t)((uint16_t)buffer[8]  | ((uint16_t)buffer[9]  << 8));
    int16_t dz = (int16_t)((uint16_t)buffer[10] | ((uint16_t)buffer[11] << 8));

    constexpr float kMouseScaleX = 30.0f;
    constexpr float kMouseScaleY = 2.0f;
    constexpr float kMouseScaleZ = 1.0f;

    od.mouse.x      = normMouse(static_cast<float>(dx), kMouseScaleX);
    od.mouse.y      = normMouse(static_cast<float>(dy), kMouseScaleY);
    od.mouse.z      = normMouse(static_cast<float>(dz), kMouseScaleZ);
    od.mouse.left   = buffer[12] != 0;
    od.mouse.right  = buffer[13] != 0;

    Keyboard cur_raw { .all = static_cast<uint16_t>((uint16_t)buffer[14] | ((uint16_t)buffer[15] << 8)) };
    keyboard_state_.Process(od.keyboard, cur_raw);

    fillOutputData(od, pub);
    return true;
}

bool dataprocess(const uint8_t* buffer, uint8_t len, Message& pub)
{
    return decode(buffer, len, pub);
}

}
