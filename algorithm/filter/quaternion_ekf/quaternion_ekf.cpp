/**
 * @file quaternion_ekf.cpp
 * @author qingyu
 * @brief 
 * @version 0.1
 * @date 2026-06-01
 * 
 * @copyright Copyright (c) 2026
 * 
 */

#include "quaternion_ekf.hpp"
#include "imu.hpp"

#include <math.h>
#include <string.h>

namespace {

/* 初始状态转移矩阵为单位阵，Update() 中再按当前角速度填入四元数积分项。 */
constexpr float kBaseTransition[36] {
     1.0f,  0.0f,  0.0f,  0.0f,  0.0f,  0.0f,
     0.0f,  1.0f,  0.0f,  0.0f, 0.0f, 0.0f,
    0.0f, 0.0f, 1.0f, 0.0f, 0.0f, 0.0f,
    0.0f, 0.0f, 0.0f, 1.0f, 0.0f, 0.0f,
    0.0f, 0.0f, 0.0f, 0.0f, 1.0f, 0.0f,
    0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 1.0f,
};

/*
 * 初始协方差给四元数较大的不确定度，让滤波器启动时更愿意接受加速度方向修正；
 * 零偏协方差相对小一些，避免刚启动时过快改变零偏估计。
 */
constexpr float kInitialCovariance[36] {
     100000.0f, 0.1f,      0.1f,       0.1f,       0.1f,    0.1f,
     0.1f,      100000.0f, 0.1f,       0.1f,      0.1f,   0.1f,
    0.1f,     0.1f,     100000.0f, 0.1f,      0.1f,   0.1f,
    0.1f,     0.1f,     0.1f,      100000.0f, 0.1f,   0.1f,
    0.1f,     0.1f,     0.1f,      0.1f,      100.0f, 0.1f,
    0.1f,     0.1f,     0.1f,      0.1f,      0.1f,   100.0f,
};

constexpr float kRadToDeg = 57.295779513f;
constexpr float kYawBiasStaticThreshold = 0.02f;

float Clamp(float value, float min_value, float max_value)
{
    if (value < min_value) {
        return min_value;
    }
    if (value > max_value) {
        return max_value;
    }
    return value;
}

} // namespace

namespace alg::attitude {

static void SyncLegacyExports(const QuaternionEkf::State& state)
{
    ::QEKF_INS = state;
    ::chiSquare = state.ChiSquare;
    ::ChiSquareTestThreshold = state.ChiSquareTestThreshold;
}

void QuaternionEkf::Init(const Config& config)
{
    config_ = config;
    if (config_.fading_factor > 1.0f) {
        config_.fading_factor = 1.0f;
    }

    state_ = {};
    state_.Initialized  = true;
    state_.Q1           = config_.process_noise_quaternion;
    state_.Q2           = config_.process_noise_bias;
    state_.R            = config_.measurement_noise;
    state_.lambda       = config_.fading_factor;
    state_.accLPFcoef   = config_.accel_lpf_coefficient;
    state_.ChiSquareTestThreshold = config_.chi_square_threshold;

    chi_square_data_[0] = 0.0f;
    filter::MatrixInit(&chi_square_matrix_, 1, 1, chi_square_data_);

    (void)filter_.Init(kStateSize, 0, kMeasurementSize);
    filter_.SetUserData(this);
    filter_.User_Func0_f = ObserveHook;
    filter_.User_Func1_f = LinearizeHook;
    filter_.User_Func2_f = SetHHook;
    filter_.User_Func3_f = UpdatePosteriorHook;
    /*
     * 标准 Kalman 的增益和状态修正步骤由 UpdatePosterior() 接管。
     * 这样可以在同一处插入卡方门限、自适应增益和零偏限幅。
     */
    filter_.SkipEq3 = true;
    filter_.SkipEq4 = true;

    filter_.xhat_data[0] = 1.0f;
    filter_.xhat_data[1] = 0.0f;
    filter_.xhat_data[2] = 0.0f;
    filter_.xhat_data[3] = 0.0f;

    memcpy(filter_.F_data, kBaseTransition, sizeof(kBaseTransition));
    memcpy(filter_.P_data, kInitialCovariance, sizeof(kInitialCovariance));

    SyncLegacyExports(state_);
}

void QuaternionEkf::Update(const Sample& sample)
{
    if (!state_.Initialized) {
        Init(Config{});
    }

    state_.dt = sample.dt;

    state_.Gyro[0] = sample.gyro[0] - state_.GyroBias[0];
    state_.Gyro[1] = sample.gyro[1] - state_.GyroBias[1];
    state_.Gyro[2] = sample.gyro[2] - state_.GyroBias[2];

    const float half_gx_dt = 0.5f * state_.Gyro[0] * state_.dt;
    const float half_gy_dt = 0.5f * state_.Gyro[1] * state_.dt;
    const float half_gz_dt = 0.5f * state_.Gyro[2] * state_.dt;

    /*
     * 四元数一阶离散化：
     * q(k) = q(k-1) + 0.5 * q(k-1) (*) omega * dt。
     * 这里把该关系写进 F 矩阵，让 Kalman 预测步骤完成姿态积分。
     */
    memcpy(filter_.F_data, kBaseTransition, sizeof(kBaseTransition));
    filter_.F_data[1]  = -half_gx_dt;
    filter_.F_data[2]  = -half_gy_dt;
    filter_.F_data[3]  = -half_gz_dt;
    filter_.F_data[6]  =  half_gx_dt;
    filter_.F_data[8]  =  half_gz_dt;
    filter_.F_data[9]  = -half_gy_dt;
    filter_.F_data[12] =  half_gy_dt;
    filter_.F_data[13] = -half_gz_dt;
    filter_.F_data[15] =  half_gx_dt;
    filter_.F_data[18] =  half_gz_dt;
    filter_.F_data[19] =  half_gy_dt;
    filter_.F_data[20] = -half_gx_dt;

    if (state_.UpdateCount == 0) {
        state_.Accel[0] = sample.accel[0];
        state_.Accel[1] = sample.accel[1];
        state_.Accel[2] = sample.accel[2];
    }

    /* 对加速度做一阶低通，减小机械振动和采样尖峰对重力方向观测的影响。 */
    const float accel_filter_den = state_.dt + state_.accLPFcoef;
    if (accel_filter_den > 0.0f) {
        state_.Accel[0] = state_.Accel[0] * state_.accLPFcoef / accel_filter_den + sample.accel[0] * state_.dt / accel_filter_den;
        state_.Accel[1] = state_.Accel[1] * state_.accLPFcoef / accel_filter_den + sample.accel[1] * state_.dt / accel_filter_den;
        state_.Accel[2] = state_.Accel[2] * state_.accLPFcoef / accel_filter_den + sample.accel[2] * state_.dt / accel_filter_den;
    } else {
        state_.Accel[0] = sample.accel[0];
        state_.Accel[1] = sample.accel[1];
        state_.Accel[2] = sample.accel[2];
    }

    const float accel_inv_norm = FastInverseSqrt(state_.Accel[0] * state_.Accel[0] + state_.Accel[1] * state_.Accel[1] +
                                                state_.Accel[2] * state_.Accel[2]);
    /* EKF 的观测值使用方向而不是模长，避免线加速度大小直接进入姿态修正。 */
    for (uint8_t i = 0; i < kMeasurementSize; i++) {
        filter_.MeasuredVector[i] = state_.Accel[i] * accel_inv_norm;
    }

    const float gyro_inv_norm = FastInverseSqrt(state_.Gyro[0] * state_.Gyro[0] + state_.Gyro[1] * state_.Gyro[1] +
                                                state_.Gyro[2] * state_.Gyro[2]);
    state_.gyro_norm = gyro_inv_norm > 0.0f ? 1.0f / gyro_inv_norm : 0.0f;
    state_.accl_norm = accel_inv_norm > 0.0f ? 1.0f / accel_inv_norm : 0.0f;

    /*
     * 静止判定只在算法内部使用：当角速度小且加速度模长接近重力时，
     * 才更大胆地累计异常次数和修正零偏。
     */
    if (state_.gyro_norm < config_.stable_gyro_threshold &&
        state_.accl_norm > config_.stable_accel_reference - config_.stable_accel_tolerance &&
        state_.accl_norm < config_.stable_accel_reference + config_.stable_accel_tolerance) {
        state_.StableFlag = true;
    } else {
        state_.StableFlag = false;
    }

    filter_.Q_data[0]  = state_.Q1 * state_.dt;
    filter_.Q_data[7]  = state_.Q1 * state_.dt;
    filter_.Q_data[14] = state_.Q1 * state_.dt;
    filter_.Q_data[21] = state_.Q1 * state_.dt;
    filter_.Q_data[28] = state_.Q2 * state_.dt;
    filter_.Q_data[35] = state_.Q2 * state_.dt;

    filter_.R_data[0] = state_.R;
    filter_.R_data[4] = state_.R;
    filter_.R_data[8] = state_.R;

    /* filter_.Update() 会按 Hook 顺序调用 Observe/Linearize/SetH/UpdatePosterior。 */
    filter_.Update();

    state_.q[0] = filter_.FilteredValue[0];
    state_.q[1] = filter_.FilteredValue[1];
    state_.q[2] = filter_.FilteredValue[2];
    state_.q[3] = filter_.FilteredValue[3];
    state_.GyroBias[0] = filter_.FilteredValue[4];
    state_.GyroBias[1] = filter_.FilteredValue[5];

    /*
     * yaw 没有磁力计等绝对航向观测时，本质上还是靠 gz 积分。
     * 这里在静止判定成立时，缓慢把 z 轴零偏拉向当前测得的角速度，
     * 这样至少能压住温漂/残余零偏带来的持续 yaw 漂移。
     */
    if (state_.StableFlag &&
        fabsf(state_.Gyro[0]) < config_.stable_gyro_threshold &&
        fabsf(state_.Gyro[1]) < config_.stable_gyro_threshold &&
        fabsf(state_.Gyro[2]) < kYawBiasStaticThreshold) {
        const float bias_limit = config_.bias_correction_limit * state_.dt;
        const float bias_error = sample.gyro[2] - state_.GyroBias[2];
        state_.GyroBias[2] += Clamp(bias_error, -bias_limit, bias_limit);
    }

    state_.Gyro[0] = sample.gyro[0] - state_.GyroBias[0];
    state_.Gyro[1] = sample.gyro[1] - state_.GyroBias[1];
    state_.Gyro[2] = sample.gyro[2] - state_.GyroBias[2];

    /*
     * Export standard ZYX Euler angles:
     * - Roll : rotation around body x
     * - Pitch: rotation around body y (the singular middle angle)
     * - Yaw  : rotation around body z
     *
     * With this definition, the unavoidable Euler singularity appears when
     * Pitch approaches +/-90 deg rather than on Roll.
     */
    state_.Yaw  = atan2f(2.0f * (state_.q[0] * state_.q[3] + state_.q[1] * state_.q[2]),
                         2.0f * (state_.q[0] * state_.q[0] + state_.q[1] * state_.q[1]) - 1.0f) * kRadToDeg;
    state_.Roll = atan2f(2.0f * (state_.q[0] * state_.q[1] + state_.q[2] * state_.q[3]),
                         2.0f * (state_.q[0] * state_.q[0] + state_.q[3] * state_.q[3]) - 1.0f) * kRadToDeg;
    const float pitch_input = Clamp(-2.0f * (state_.q[1] * state_.q[3] - state_.q[0] * state_.q[2]), -1.0f, 1.0f);
    state_.Pitch = asinf(pitch_input) * kRadToDeg;

    /* yaw_total 保留跨越 +/-180 度后的连续角度，方便云台或底盘做连续角控制。 */
    if (state_.Yaw - state_.YawAngleLast > 180.0f) {
        state_.YawRoundCount--;
    } else if (state_.Yaw - state_.YawAngleLast < -180.0f) {
        state_.YawRoundCount++;
    }

    state_.YawTotalAngle = 360.0f * state_.YawRoundCount + state_.Yaw;
    state_.YawAngleLast  = state_.Yaw;
    state_.UpdateCount++;

    SyncLegacyExports(state_);
}

void QuaternionEkf::Observe()
{
    /* 上一次后验更新计算出的卡方值会在下一轮 Hook 前同步到状态里。 */
    state_.ChiSquare = chi_square_data_[0];
}

void QuaternionEkf::LinearizeAndFade()
{
    float q0 = filter_.xhatminus_data[0];
    float q1 = filter_.xhatminus_data[1];
    float q2 = filter_.xhatminus_data[2];
    float q3 = filter_.xhatminus_data[3];

    const float q_inv_norm = FastInverseSqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3);
    if (q_inv_norm > 0.0f) {
        /* 四元数必须保持单位长度，否则姿态反解和观测雅可比都会失真。 */
        for (uint8_t i = 0; i < 4; i++) {
            filter_.xhatminus_data[i] *= q_inv_norm;
        }
    } else {
        filter_.xhatminus_data[0] = 1.0f;
        filter_.xhatminus_data[1] = 0.0f;
        filter_.xhatminus_data[2] = 0.0f;
        filter_.xhatminus_data[3] = 0.0f;
    }

    q0 = filter_.xhatminus_data[0];
    q1 = filter_.xhatminus_data[1];
    q2 = filter_.xhatminus_data[2];
    q3 = filter_.xhatminus_data[3];

    /*
     * 对零偏状态做线性化：角速度零偏会通过四元数积分影响姿态，
     * 所以 F 中需要补 q 对 bias_x/bias_y 的偏导。
     */
    filter_.F_data[4]  =  q1 * state_.dt * 0.5f;
    filter_.F_data[5]  =  q2 * state_.dt * 0.5f;
    filter_.F_data[10] = -q0 * state_.dt * 0.5f;
    filter_.F_data[11] =  q3 * state_.dt * 0.5f;
    filter_.F_data[16] = -q3 * state_.dt * 0.5f;
    filter_.F_data[17] = -q0 * state_.dt * 0.5f;
    filter_.F_data[22] =  q2 * state_.dt * 0.5f;
    filter_.F_data[23] = -q1 * state_.dt * 0.5f;

    /* 渐消因子放大零偏协方差，让长时间运行后仍保留一定可修正性。 */
    filter_.P_data[28] /= state_.lambda;
    filter_.P_data[35] /= state_.lambda;

    if (filter_.P_data[28] > config_.bias_covariance_limit) {
        filter_.P_data[28] = config_.bias_covariance_limit;
    }
    if (filter_.P_data[35] > config_.bias_covariance_limit) {
        filter_.P_data[35] = config_.bias_covariance_limit;
    }
}

void QuaternionEkf::SetObservationJacobian()
{
    const float double_q0 = 2.0f * filter_.xhatminus_data[0];
    const float double_q1 = 2.0f * filter_.xhatminus_data[1];
    const float double_q2 = 2.0f * filter_.xhatminus_data[2];
    const float double_q3 = 2.0f * filter_.xhatminus_data[3];

    memset(filter_.H_data, 0, sizeof(float) * filter_.zSize * filter_.xhatSize);

    /*
     * 观测模型 h(q) 为机体系下的重力方向：
     * [2(q1q3-q0q2), 2(q0q1+q2q3), q0^2-q1^2-q2^2+q3^2]^T。
     * H 是该观测模型对四元数状态的雅可比。
     */
    filter_.H_data[0]  = -double_q2;
    filter_.H_data[1]  =  double_q3;
    filter_.H_data[2]  = -double_q0;
    filter_.H_data[3]  =  double_q1;

    filter_.H_data[6]  =  double_q1;
    filter_.H_data[7]  =  double_q0;
    filter_.H_data[8]  =  double_q3;
    filter_.H_data[9]  =  double_q2;

    filter_.H_data[12] =  double_q0;
    filter_.H_data[13] = -double_q1;
    filter_.H_data[14] = -double_q2;
    filter_.H_data[15] =  double_q3;
}

void QuaternionEkf::UpdatePosterior()
{
    auto fallback_to_prediction = [this]() {
        /*
         * 矩阵维度错误或 S 不可逆时，放弃本次观测修正，只保留陀螺预测。
         * 这样比继续用坏矩阵更新更可控，最多表现为短时间漂移。
         */
        memcpy(filter_.xhat_data, filter_.xhatminus_data, sizeof(float) * filter_.xhatSize);
        memcpy(filter_.P_data, filter_.Pminus_data, sizeof(float) * filter_.xhatSize * filter_.xhatSize);
        filter_.SkipEq5 = false;
    };

    const float q0 = filter_.xhatminus_data[0];
    const float q1 = filter_.xhatminus_data[1];
    const float q2 = filter_.xhatminus_data[2];
    const float q3 = filter_.xhatminus_data[3];

    filter_.MatStatus = filter::MatrixTranspose(&filter_.H, &filter_.HT);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }

    filter_.temp_matrix.numRows = filter_.H.numRows;
    filter_.temp_matrix.numCols = filter_.Pminus.numCols;
    filter_.MatStatus = filter::MatrixMultiply(&filter_.H, &filter_.Pminus, &filter_.temp_matrix);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }

    filter_.temp_matrix1.numRows = filter_.temp_matrix.numRows;
    filter_.temp_matrix1.numCols = filter_.HT.numCols;
    filter_.MatStatus = filter::MatrixMultiply(&filter_.temp_matrix, &filter_.HT, &filter_.temp_matrix1);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }

    filter_.S.numRows = filter_.R.numRows;
    filter_.S.numCols = filter_.R.numCols;
    filter_.MatStatus = filter::MatrixAdd(&filter_.temp_matrix1, &filter_.R, &filter_.S);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }
    filter_.MatStatus = filter::MatrixInverse(&filter_.S, &filter_.temp_matrix1);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }

    filter_.temp_vector.numRows = filter_.H.numRows;
    filter_.temp_vector.numCols = 1;
    /* 先验四元数预测出的加速度方向，即 h(xhatminus)。 */
    filter_.temp_vector_data[0] = 2.0f * (q1 * q3 - q0 * q2);
    filter_.temp_vector_data[1] = 2.0f * (q0 * q1 + q2 * q3);
    filter_.temp_vector_data[2] = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3;

    for (uint8_t i = 0; i < 3; i++) {
        /* 记录观测方向夹角，后面用于降低不敏感轴对零偏估计的影响。 */
        state_.OrientationCosine[i] = acosf(Clamp(fabsf(filter_.temp_vector_data[i]), 0.0f, 1.0f));
    }

    filter_.temp_vector1.numRows = filter_.z.numRows;
    filter_.temp_vector1.numCols = 1;
    filter_.MatStatus = filter::MatrixSubtract(&filter_.z, &filter_.temp_vector, &filter_.temp_vector1);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }

    filter_.temp_matrix.numRows = filter_.temp_vector1.numRows;
    filter_.temp_matrix.numCols = 1;
    filter_.MatStatus = filter::MatrixMultiply(&filter_.temp_matrix1, &filter_.temp_vector1, &filter_.temp_matrix);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }

    filter_.temp_vector.numRows = 1;
    filter_.temp_vector.numCols = filter_.temp_vector1.numRows;
    filter_.MatStatus = filter::MatrixTranspose(&filter_.temp_vector1, &filter_.temp_vector);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }
    filter_.MatStatus = filter::MatrixMultiply(&filter_.temp_vector, &filter_.temp_matrix, &chi_square_matrix_);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }

    state_.ChiSquare = chi_square_data_[0];

    /*
     * 卡方检验衡量观测残差是否符合当前协方差预期。
     * 残差足够小时认为滤波器已经收敛；残差过大时进入异常处理。
     */
    if (state_.ChiSquare < 0.5f * state_.ChiSquareTestThreshold) {
        state_.ConvergeFlag = true;
    }

    if (state_.ChiSquare > state_.ChiSquareTestThreshold && state_.ConvergeFlag) {
        if (state_.StableFlag) {
            state_.ErrorCount++;
        } else {
            state_.ErrorCount = 0;
        }

        if (state_.ErrorCount > config_.divergence_limit) {
            state_.ConvergeFlag = false;
            filter_.SkipEq5 = false;
        } else {
            /*
             * 已收敛且偶发异常时跳过本次观测更新，避免线加速度或冲击把姿态拉偏。
             * 连续异常过多才解除收敛状态，让滤波器重新接受观测修正。
             */
            memcpy(filter_.xhat_data, filter_.xhatminus_data, sizeof(float) * filter_.xhatSize);
            memcpy(filter_.P_data, filter_.Pminus_data, sizeof(float) * filter_.xhatSize * filter_.xhatSize);
            filter_.SkipEq5 = true;
            return;
        }
    } else {
        if (state_.ChiSquare > 0.1f * state_.ChiSquareTestThreshold && state_.ConvergeFlag) {
            /* 残差接近门限时降低 Kalman 增益，做软过渡而不是硬切换。 */
            state_.AdaptiveGainScale =
                (state_.ChiSquareTestThreshold - state_.ChiSquare) /
                (0.9f * state_.ChiSquareTestThreshold);
        } else {
            state_.AdaptiveGainScale = 1.0f;
        }
        state_.ErrorCount = 0;
        filter_.SkipEq5 = false;
    }

    filter_.temp_matrix.numRows = filter_.Pminus.numRows;
    filter_.temp_matrix.numCols = filter_.HT.numCols;
    filter_.MatStatus = filter::MatrixMultiply(&filter_.Pminus, &filter_.HT, &filter_.temp_matrix);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }
    filter_.MatStatus = filter::MatrixMultiply(&filter_.temp_matrix, &filter_.temp_matrix1, &filter_.K);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }

    for (uint16_t i = 0; i < filter_.K.numRows * filter_.K.numCols; i++) {
        filter_.K_data[i] *= state_.AdaptiveGainScale;
    }

    /* 根据姿态方向削弱零偏通道增益，减少不可观方向上的误修正。 */
    for (uint8_t i = 4; i < 6; i++) {
        for (uint8_t j = 0; j < 3; j++) {
            filter_.K_data[i * 3 + j] *= state_.OrientationCosine[i - 4] / 1.5707963f;
        }
    }

    filter_.temp_vector.numRows = filter_.K.numRows;
    filter_.temp_vector.numCols = 1;
    filter_.MatStatus = filter::MatrixMultiply(&filter_.K, &filter_.temp_vector1, &filter_.temp_vector);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }

    if (state_.ConvergeFlag) {
        for (uint8_t i = 4; i < 6; i++) {
            const float limit = config_.bias_correction_limit * state_.dt;
            /* 收敛后零偏修正必须限幅，否则一次错误观测就可能污染长期状态。 */
            if (filter_.temp_vector.pData[i] > limit) {
                filter_.temp_vector.pData[i] = limit;
            }
            if (filter_.temp_vector.pData[i] < -limit) {
                filter_.temp_vector.pData[i] = -limit;
            }
        }
    }

    /* 目前不估计 z 轴陀螺零偏，保持旧算法行为。 */
    /* The state vector only carries x/y gyro bias, so there is no bias_z term to zero. */
    filter_.MatStatus = filter::MatrixAdd(&filter_.xhatminus, &filter_.temp_vector, &filter_.xhat);
    if (filter_.MatStatus != filter::MatrixStatus::Ok) {
        fallback_to_prediction();
        return;
    }
}

void QuaternionEkf::ObserveHook(filter::KalmanFilter *kf)
{
    auto *self = static_cast<QuaternionEkf *>(kf->UserData());
    self->Observe();
}

void QuaternionEkf::LinearizeHook(filter::KalmanFilter *kf)
{
    auto *self = static_cast<QuaternionEkf *>(kf->UserData());
    self->LinearizeAndFade();
}

void QuaternionEkf::SetHHook(filter::KalmanFilter *kf)
{
    auto *self = static_cast<QuaternionEkf *>(kf->UserData());
    self->SetObservationJacobian();
}

void QuaternionEkf::UpdatePosteriorHook(filter::KalmanFilter *kf)
{
    auto *self = static_cast<QuaternionEkf *>(kf->UserData());
    self->UpdatePosterior();
}

float QuaternionEkf::FastInverseSqrt(float x)
{
    if (x <= 0.0f) {
        return 0.0f;
    }

    const float half_x = 0.5f * x;
    union
    {
        float    f;
        uint32_t i;
    } value = { x };

    value.i = 0x5f375a86u - (value.i >> 1);
    value.f = value.f * (1.5f - (half_x * value.f * value.f));
    return value.f;
}

QuaternionEkf& GlobalQuaternionEkf()
{
    static QuaternionEkf instance {};
    return instance;
}

} // namespace alg::attitude

QEKF_INS_t QEKF_INS {};
float      chiSquare = 0.0f;
float      ChiSquareTestThreshold = 0.0f;

void IMU_QuaternionEKF_Init(float process_noise1, float process_noise2, float measure_noise, float lambda, float lpf)
{
    alg::attitude::QuaternionEkf::Config config {};
    config.process_noise_quaternion = process_noise1;
    config.process_noise_bias       = process_noise2;
    config.measurement_noise        = measure_noise;
    config.fading_factor            = lambda;
    config.accel_lpf_coefficient    = lpf;

    auto& ekf = alg::attitude::GlobalQuaternionEkf();
    ekf.Init(config);
}

void IMU_QuaternionEKF_Update(float gx, float gy, float gz,
                              float ax, float ay, float az, float dt)
{
    Sample sample {};
    sample.gyro[0] = gx;
    sample.gyro[1] = gy;
    sample.gyro[2] = gz;
    sample.accel[0] = ax;
    sample.accel[1] = ay;
    sample.accel[2] = az;
    sample.dt = dt;

    auto& ekf = alg::attitude::GlobalQuaternionEkf();
    ekf.Update(sample);
}
