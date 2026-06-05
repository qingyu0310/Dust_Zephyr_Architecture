/**
 * @file imu.hpp
 * @author qingyu
 * @brief IMU 模块公共接口与采样数据结构
 * @version 0.1
 * @date 2026-06-02
 */

#pragma once

#include <cstdint>

/**
 * @brief 一帧 IMU 工程量样本
 *
 * 具体 IMU 驱动负责完成寄存器读取、单位换算与驱动侧校准，
 * 上层姿态线程统一消费这个结构。
 */
struct Sample
{
    float gyro[3] = {0.0f, 0.0f, 0.0f};
    float accel[3] = {0.0f, 0.0f, 0.0f};
    float temperature = 0.0f;
    float dt = 0.001f;
};

/**
 * @brief 具体 IMU 驱动需要实现的统一接口
 */
class Source
{
public:
    virtual ~Source() = default;

    /**
     * @brief 初始化具体传感器与相关总线资源
     */
    virtual bool Init() = 0;

    /**
     * @brief 读取一帧工程量样本
     */
    virtual bool Read(Sample& sample) = 0;

    /**
     * @brief 按当前驱动配置执行一次静态校准
     */
    virtual bool Calibrate() { return true; }

    /**
     * @brief 返回建议的线程轮询周期，单位 ms
     */
    virtual uint32_t PeriodMs() const { return 1; }
};

namespace thread::imu {

/**
 * @brief 初始化 IMU 模块
 */
void thread_init();

/**
 * @brief 启动 IMU 工作线程
 */
void thread_start(uint8_t prio = 5);

} // namespace thread::imu
