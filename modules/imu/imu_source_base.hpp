/**
 * @file imu_source_base.hpp
 * @author qingyu
 * @brief IMU 数据源公共基类与共享数据结构
 * @version 0.1
 * @date 2026-06-03
 */

#pragma once

#include "imu.hpp"

#include <math.h>

/**
 * @brief 原始 IMU 寄存器样本
 *
 * 统一保存 accel/gyro/temperature 原始计数，供公共后处理流程复用。
 */
struct ImuRawSample
{
    int16_t accel[3] = {0, 0, 0};
    int16_t gyro [3] = {0, 0, 0};
    int16_t temperature = 0;
};

/**
 * @brief IMU 静态校准参数
 */
struct ImuCalibration
{
    float gyro_offset [3] = {0.0f, 0.0f, 0.0f};
    float gyro_scale  [3] = {1.0f, 1.0f, 1.0f};
    float accel_offset[3] = {0.0f, 0.0f, 0.0f};
    float accel_scale [3] = {1.0f, 1.0f, 1.0f};
};

/**
 * @brief 各 IMU 数据源共享的运行时配置
 */
struct ImuCommonConfig
{
    ImuCalibration static_calibration {};
    uint32_t period_ms = 1;
    uint16_t calibration_samples = 200;
    uint32_t calibration_settle_ms = 50;
};

/**
 * @brief 带公共校准与单位换算流程的 IMU 数据源基类
 *
 * 子类只需提供：
 * - 原始样本读取
 * - 原始计数到工程量的换算
 * - 平台相关的 sleep 实现
 */
class CalibratedImuSource : public Source
{
public:
    /**
     * @brief 返回建议的线程轮询周期
     */
    uint32_t PeriodMs() const override
    {
        return common_config_.period_ms;
    }

    /**
     * @brief 读取原始样本并完成公共工程量转换与校准
     */
    bool Read(Sample& sample) override
    {
        ImuRawSample raw {};
        if (!ReadRaw(raw)) {
            return false;
        }

        for (uint8_t axis = 0; axis < 3; axis++) 
        {
            const float accel = ConvertAccel(raw.accel[axis]);
            const float gyro  = ConvertGyro (raw.gyro[axis]);

            sample.accel[axis] = Correct(accel,
                                         common_config_.static_calibration.accel_offset[axis],
                                         common_config_.static_calibration.accel_scale[axis]);
            sample.gyro [axis] = Correct(gyro,
                                         common_config_.static_calibration.gyro_offset[axis],
                                         common_config_.static_calibration.gyro_scale[axis]);
        }

        sample.temperature = ConvertTemperature(raw.temperature);
        sample.dt = static_cast<float>(common_config_.period_ms) * 0.001f;
        return true;
    }

protected:
    ImuCommonConfig common_config_ {};

    /**
     * @brief 保存公共运行时配置
     */
    void ConfigureCommon(const ImuCommonConfig& config)
    {
        common_config_ = config;
        if (common_config_.period_ms == 0) {
            common_config_.period_ms = 1;
        }
    }

    /**
     * @brief 复用的启动阶段自动标定流程
     *
     * 在 IMU 静止条件下采集多帧原始样本，估计：
     * - 陀螺零偏（各轴均值，静止时应为 0）
     * - 加速度零偏（实测均值与理论重力向量之差）
     *
     * @param gravity 当地重力加速度值（单位与 ConvertAccel 一致）
     * @return 标定是否成功
     */
    bool AutoCalibrateCommon(float gravity)
    {
        // 无需校准 samples 数为 0，直接跳过。
        if (common_config_.calibration_samples == 0) {
            return true;
        }

        // 校准阶段直接读取原始样本求静态均值，不经过当前保存的 offset/scale。
        if (common_config_.calibration_settle_ms > 0) {
            SleepMs(common_config_.calibration_settle_ms);
        }

        float gyro_sum [3] = {0.0f, 0.0f, 0.0f};
        float accel_sum[3] = {0.0f, 0.0f, 0.0f};

        // 累积 calibration_samples 帧原始样本（已换算为工程量）。
        for (uint16_t sample_idx = 0; sample_idx < common_config_.calibration_samples; sample_idx++)
        {
            ImuRawSample raw {};
            if (!ReadRaw(raw)) {
                return false;
            }

            for (uint8_t axis = 0; axis < 3; axis++) {
                gyro_sum [axis] += ConvertGyro (raw.gyro [axis]);
                accel_sum[axis] += ConvertAccel(raw.accel[axis]);
            }

            SleepMs(common_config_.period_ms);
        }

        // 求均值 —— 陀螺均值即零偏（静止时理论值 = 0）。
        const float inv_samples = 1.0f / static_cast<float>(common_config_.calibration_samples);
        float accel_mean[3] = {0.0f, 0.0f, 0.0f};
        float accel_norm_sq = 0.0f;

        for (uint8_t axis = 0; axis < 3; axis++) {
            common_config_.static_calibration.gyro_offset[axis] = gyro_sum[axis] * inv_samples;
            accel_mean[axis] = accel_sum [axis] * inv_samples;
            accel_norm_sq   += accel_mean[axis] * accel_mean[axis];
        }

        // 加速度零偏 = 实测均值 - 理论重力向量。
        // 理论重力向量 = 均值方向上的单位向量 × 当地重力标量。
        if (accel_norm_sq > 0.0f)
        {
            const float accel_norm = sqrtf(accel_norm_sq);
            const float gravity_scale = gravity / accel_norm;

            for (uint8_t axis = 0; axis < 3; axis++) {
                const float expected_gravity = accel_mean[axis] * gravity_scale;
                common_config_.static_calibration.accel_offset[axis] = accel_mean[axis] - expected_gravity;
            }
        }

        return true;
    }

    /**
     * @brief 对单通道应用 offset/scale 校准参数
     */
    static float Correct(float value, float offset, float scale)
    {
        return (value - offset) * scale;
    }

    /**
     * @brief 读取一帧原始样本
     */
    virtual bool ReadRaw(ImuRawSample& raw) = 0;

    /**
     * @brief 将原始加速度计数转换为工程量
     */
    virtual float ConvertAccel(int16_t raw) const = 0;

    /**
     * @brief 将原始角速度计数转换为工程量
     */
    virtual float ConvertGyro(int16_t raw) const = 0;

    /**
     * @brief 将原始温度计数转换为摄氏度
     */
    virtual float ConvertTemperature(int16_t raw) const = 0;

    /**
     * @brief 平台相关的毫秒级延时
     */
    virtual void SleepMs(uint32_t ms) const = 0;
};
