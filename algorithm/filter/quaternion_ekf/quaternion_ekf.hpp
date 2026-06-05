/**
 * @file quaternion_ekf.hpp
 * @author qingyu
 * @brief 
 * @version 0.1
 * @date 2026-06-01
 * 
 * @copyright Copyright (c) 2026
 * 
 */

#pragma once

#include "kalman.hpp"

#include <stdint.h>

struct Sample;

namespace alg::attitude {

/**
 * @brief 基于四元数的 IMU 姿态扩展 Kalman 滤波器。
 *
 * 状态向量维度为 6：
 * - q0, q1, q2, q3: 姿态四元数。
 * - gyro_bias_x, gyro_bias_y: 陀螺 x/y 零偏。
 *
 * 观测量维度为 3，使用归一化后的加速度方向作为重力方向观测。
 * yaw 主要由陀螺积分得到，当前算法不估计 z 轴陀螺零偏。
 */
class QuaternionEkf final
{
public:
    struct Config {
        /* 四元数状态过程噪声，越大越相信陀螺积分的状态变化。 */
        float process_noise_quaternion = 10.0f;
        /* 陀螺零偏过程噪声，越大零偏跟随越快，但也更容易被动态加速度带偏。 */
        float process_noise_bias       = 0.001f;
        /* 加速度方向观测噪声，越大越不信任加速度修正。 */
        float measurement_noise        = 1000000.0f;
        /* 零偏协方差渐消因子，用于让长期运行时仍能缓慢修正零偏。 */
        float fading_factor            = 1.0f;
        /* 加速度一阶低通系数，0 表示不滤波。 */
        float accel_lpf_coefficient    = 0.0f;
        /* 卡方检验门限，用于判断当前加速度观测是否可信。 */
        float chi_square_threshold     = 1e-8f;
        /* 静止判定阈值，供卡方异常计数和零偏修正使用。 */
        float stable_gyro_threshold    = 0.3f;
        float stable_accel_reference   = 9.8f;
        float stable_accel_tolerance   = 0.5f;
        /* 收敛后单次零偏修正限幅，防止异常观测瞬间拉飞零偏。 */
        float bias_correction_limit    = 1e-2f;
        float bias_covariance_limit    = 10000.0f;
        /* 连续异常超过该次数后认为滤波器发散，允许重新进入收敛过程。 */
        uint32_t divergence_limit      = 50;
    };

    struct State {
        /* 这些标志主要给算法内部使用，不再发布给上层 topic。 */
        bool Initialized  = false;
        bool ConvergeFlag = false;
        bool StableFlag   = false;

        uint64_t ErrorCount  = 0;
        uint64_t UpdateCount = 0;

        float q[4]                 {1.0f, 0.0f, 0.0f, 0.0f};
        /* x/y 由 Kalman 状态估计，z 由静止时的缓慢在线零偏修正维护。 */
        float GyroBias[3]          {0.0f, 0.0f, 0.0f};
        /* Gyro 为扣除零偏后的角速度，Accel 为低通后的加速度。 */
        float Gyro[3]              {0.0f, 0.0f, 0.0f};
        float Accel[3]             {0.0f, 0.0f, 0.0f};
        /* 观测方向与机体系轴的夹角，用于削弱不敏感方向上的零偏修正。 */
        float OrientationCosine[3] {0.0f, 0.0f, 0.0f};

        float accLPFcoef        = 0.0f;
        float gyro_norm         = 0.0f;
        float accl_norm         = 0.0f;
        float AdaptiveGainScale = 1.0f;

        /* Standard body-frame Euler angles: roll(x), pitch(y), yaw(z). */
        float Roll = 0.0f;
        float Pitch = 0.0f;
        float Yaw = 0.0f;
        float YawTotalAngle = 0.0f;

        float Q1 = 10.0f;
        float Q2 = 0.001f;
        float R  = 1000000.0f;
        float dt = 0.0f;
        float ChiSquare = 0.0f;
        float ChiSquareTestThreshold = 1e-8f;
        float lambda = 1.0f;

        int16_t YawRoundCount = 0;
        float   YawAngleLast  = 0.0f;
    };

    QuaternionEkf() {
        filter::MatrixInit(&chi_square_matrix_, 1, 1, chi_square_data_);
    };

    void Init(const Config& config);
    void Update(const Sample& sample);

    const State& GetState() const { return state_; }
    State&       GetState()       { return state_; }
    const filter::KalmanFilter& Filter() const { return filter_; }
    filter::KalmanFilter&       Filter()       { return filter_; }

private:
    static constexpr uint8_t kStateSize       = 6;
    static constexpr uint8_t kMeasurementSize = 3;

    static void ObserveHook(filter::KalmanFilter *kf);
    static void LinearizeHook(filter::KalmanFilter *kf);
    static void SetHHook(filter::KalmanFilter *kf);
    static void UpdatePosteriorHook(filter::KalmanFilter *kf);

    static float FastInverseSqrt(float x);

    void Observe();
    void LinearizeAndFade();
    void SetObservationJacobian();
    void UpdatePosterior();

    Config config_;
    State  state_ {};
    filter::KalmanFilter filter_ {};
    filter::Matrix chi_square_matrix_ {};
    float chi_square_data_[1] = {};
};

QuaternionEkf& GlobalQuaternionEkf();

} // namespace alg::attitude

using QEKF_INS_t = alg::attitude::QuaternionEkf::State;

extern QEKF_INS_t QEKF_INS;
extern float      chiSquare;
extern float      ChiSquareTestThreshold;

void IMU_QuaternionEKF_Init(float process_noise1, float process_noise2,
                            float measure_noise, float lambda, float lpf);
void IMU_QuaternionEKF_Update(float gx, float gy, float gz,
                              float ax, float ay, float az, float dt);
