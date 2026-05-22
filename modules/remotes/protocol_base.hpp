/**
 * @file protocol_base.hpp
 * @author qingyu
 * @brief 
 * @version 0.1
 * @date 2026-05-12
 * 
 * @copyright Copyright (c) 2026
 * 
 */

#pragma once

#include <cstdint>
#include "remote_to.hpp"

// 共用类型 — 各协议解析的内部数据结构
struct Mouse
{
    float x = 0.0f, y = 0.0f, z = 0.0f;
    float left = 0.0f, right = 0.0f;
};

union Keyboard {
    uint16_t all;
    struct {
        uint16_t w     : 1;  // bit 0
        uint16_t s     : 1;  // bit 1
        uint16_t a     : 1;  // bit 2
        uint16_t d     : 1;  // bit 3
        uint16_t shift : 1;  // bit 4
        uint16_t ctrl  : 1;  // bit 5
        uint16_t q     : 1;  // bit 6
        uint16_t e     : 1;  // bit 7
        uint16_t r     : 1;  // bit 8
        uint16_t f     : 1;  // bit 9
        uint16_t g     : 1;  // bit 10
        uint16_t z     : 1;  // bit 11
        uint16_t x     : 1;  // bit 12
        uint16_t c     : 1;  // bit 13
        uint16_t v     : 1;  // bit 14
        uint16_t b     : 1;  // bit 15
    };
};

struct KeyboardState
{
    Keyboard toggle_output{};
    uint16_t last_raw_all = 0;
    uint16_t keyboard_mode = 0;

    void Process(Keyboard& current_output, const Keyboard& current_raw)
    {
        uint16_t trigger = current_raw.all & (~last_raw_all);
        uint16_t toggle_mask = keyboard_mode;
        toggle_output.all ^= (trigger & toggle_mask);
        uint16_t normal_mask = ~toggle_mask;
        current_output.all = (toggle_output.all & toggle_mask) | (current_raw.all & normal_mask);
        last_raw_all = current_raw.all;
    }
};

// 归一化函数
inline float normChannel(int16_t v, int16_t center, int16_t max)
{
    float maxDist = max - center;
    float r = (static_cast<float>(v) - center) / maxDist;
    if (r >  1.0f) r =  1.0f;
    if (r < -1.0f) r = -1.0f;
    return r;
}

inline float normChannelInv(int16_t v, int16_t center, float inv_max_dist)
{
    float r = (static_cast<float>(v) - static_cast<float>(center)) * inv_max_dist;
    if (r >  1.0f) r =  1.0f;
    if (r < -1.0f) r = -1.0f;
    return r;
}

inline float normMouse(float v, float scale)
{
    constexpr float kInvNorm = 1.0f / 32767.0f;
    v *= scale * kInvNorm;
    if (v >  1.0f) return  1.0f;
    if (v < -1.0f) return -1.0f;
    return v;
}

template<typename OutData>
inline void processChannel(topic::remote_to::Message& pub, const OutData& od)
{
    pub.chassisy = od.keyboard.w ? 1.0f : od.keyboard.s ? -1.0f : od.ch.chassisy;
    pub.chassisx = od.keyboard.a ? 1.0f : od.keyboard.d ? -1.0f : od.ch.chassisx;
    pub.pitch    = od.mouse.y             + od.ch.pitch;
    pub.yaw      = od.mouse.x             + od.ch.yaw;
}
