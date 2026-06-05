/**
 * @file imu.cpp
 * @author qingyu
 * @brief IMU 数据源管理、加热控制与姿态发布
 * @version 0.1
 * @date 2026-06-01
 */

#include "imu.hpp"
#include "imu_to.hpp"
#include "bmi088/bmi088.hpp"
#include "icm42688p/icm42688p.hpp"

#include "quaternion_ekf.hpp"
#include "thread.hpp"

#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/time_units.h>
#include "zephyr/sys/printk.h"

#include "pid.hpp"
#include "pwm.hpp"
#include "timer.hpp"
#include <zephyr/devicetree.h>
#include <zephyr/drivers/pwm.h>

namespace {

constexpr uint32_t kDefaultImuPeriodMs      = 1;

/**
 * @brief 用真实经过时间修正当前样本积分周期
 *
 * @param now_cycle   当前 cycle 计数
 * @param last_cycle  上一帧成功样本的 cycle 计数
 * @param fallback_dt 驱动侧提供的名义 dt
 * @return 修正后的真实 dt，单位秒
 */
float MeasuredDtSeconds(uint32_t now_cycle, uint32_t last_cycle, float fallback_dt)
{
    if (last_cycle == 0) {
        return fallback_dt;
    }

    const uint32_t delta_cycles = now_cycle - last_cycle;
    const uint64_t delta_ns = k_cyc_to_ns_floor64(delta_cycles);
    if (delta_ns == 0) {
        return fallback_dt;
    }

    return static_cast<float>(delta_ns) * 1.0e-9f;
}

using RegisterFromDevicetreeFn = bool (*)(uint32_t period_ms);

/**
 * @brief 通过各驱动提供的注册函数完成数据源配置
 *
 * @param register_fn 具体 IMU 驱动提供的 devicetree 注册函数
 * @return 是否注册成功
 */
bool RegisterSourceFromDevicetree(RegisterFromDevicetreeFn register_fn)
{
    if (register_fn == nullptr ||
        !register_fn(kDefaultImuPeriodMs)) {
        return false;
    }

    return true;
}

} // namespace

/**
 * @brief attitude
 *
 */
namespace attitude {

/**
 * @brief IMU 姿态解算与发布消息封装
 */
class Processor final
{
public:
    /**
     * @brief 初始化姿态解算器
     */
    void Init()
    {
        constexpr float kDefaultAccelLpfTimeConstant = 0.02f;
        alg::attitude::QuaternionEkf::Config cfg {};
        cfg.accel_lpf_coefficient = kDefaultAccelLpfTimeConstant;
        ekf_.Init(cfg);
    }

    /**
     * @brief 处理一帧 IMU 工程量样本并整理发布消息
     *
     * @param sample 当前 IMU 工程量样本
     * @param pub    输出到项目内部 topic 的发布消息
     */
    void Process(const Sample& sample, topic::imu_to::Message& pub)
    {
        // 姿态模块只消费工程量样本，并把 EKF 结果整理成项目内部 IMU topic。
        ekf_.Update(sample);

        const auto& state = ekf_.GetState();

        memcpy(pub.quaternion, state.q,    sizeof(pub.quaternion));
        memcpy(pub.gyro,       state.Gyro, sizeof(pub.gyro));

        pub.roll        = state.Roll;
        pub.pitch       = state.Pitch;
        pub.yaw         = state.Yaw;
        pub.yaw_total   = state.YawTotalAngle;
        pub.temperature = sample.temperature;
    }

private:
    alg::attitude::QuaternionEkf ekf_ {};
};

} // namespace attitude


/**
 * @brief heater
 *
 */
namespace heater {

/**
 * @brief IMU 加热 PWM 与 PID 控温封装
 */
class Heater final
{
public:
    /**
     * @brief 初始化 IMU 加热器
     *
     * 目标温度和 PID 参数当前使用类内默认值，后续需要开放时再从上层传入。
     *
     * @return 初始化是否成功
     */
    bool Init()
    {
        static const pwm_dt_spec heater_pwm = PWM_DT_SPEC_GET(DT_ALIAS(imu_pwm));

        if (!heater_pwm_.init(heater_pwm)) {
            return false;
        }

        pid_.Init(kDefaultPidConfig);

        duty_ = 0.0f; // 初始值占空比为0
        initialized_ = true;
        return heater_pwm_.SetDuty(duty_);
    }

    /**
     * @brief 根据当前温度更新加热占空比
     *
     * @param temperature 当前 IMU 温度，单位摄氏度
     */
    void Update(float temperature)
    {
        if (!initialized_) {
            return;
        }

        duty_ = pid_.Calc(target_temperature_, temperature);
        if (duty_ < 1.f - kMaxDuty) {
            duty_ = 1.f - kMaxDuty;
        }
        if (duty_ > kMaxDuty) {
            duty_ = kMaxDuty;
        }
        (void)heater_pwm_.SetDuty(duty_);
    }

    /**
     * @brief 判断当前温度是否已经稳定在目标温度附近
     *
     * @param temperature 当前 IMU 温度
     * @param tolerance   允许偏差，单位摄氏度
     * @return 是否处于稳定温区
     */
    bool IsStable(float temperature, float tolerance = 0.5f) const
    {
        return temperature >= target_temperature_ - tolerance &&
               temperature <= target_temperature_ + tolerance;
    }

    float GetDuty() const
    {
        return duty_;
    }

private:
    static constexpr float kMaxDuty = 0.95f;
    static constexpr float target_temperature_ = 40.0f;

    // 默认控温参数以“能稳定拉温”为主，后续再按实测热惯性细调。
    static constexpr alg::pid::Pid::Config kDefaultPidConfig {
        .kp         = 0.13f,
        .ki         = 0.03f,
        .kd         = 0.0f,
        .iOutMax    = 0.5f,
        .outMax     = kMaxDuty,
        .dt         = 0.001f,
    };

    Pwm heater_pwm_ {};
    alg::pid::Pid pid_ {};
    float duty_ = 0.0f;
    bool  initialized_ = false;
};

} // namespace heater

/**
 * @brief imu
 *
 */
namespace thread::imu {

/**
 * @brief IMU 顶层管理对象
 *
 * 负责三类事情：
 * 1. 选择并初始化底层 IMU 数据源
 * 2. 按需执行预热与静态校准
 * 3. 创建线程并调度控温和姿态发布流程
 */
class ImuManager final
{
public:
    /**
     * @brief 初始化 IMU 管理器
     *
     * @param enable_auto_calibration 是否在启动阶段执行预热后的静态校准
     */
    void Init(bool enable_auto_calibration = false);

    /**
     * @brief 启动 IMU 工作线程
     *
     * @param prio 线程优先级
     */
    void Start(uint8_t prio);

private:
    bool SelectSource();
    bool PrepareCalibration();
    bool Process(const Sample& sample);
    void Task();

    static void TaskEntry(void *p1, void *p2, void *p3)
    {
        ARG_UNUSED(p2);
        ARG_UNUSED(p3);
        auto *self = static_cast<ImuManager*>(p1);
        self->Task();
    }

    Thread<4096>                    thread_     {};
    Source                         *source_     = nullptr;
    Sample                          sample_     {};
    attitude::Processor             attitude_   {};
    Timer                           timer_      {100};
    heater::Heater                  heater_     {};     // 加热器只负责把 IMU 拉到目标温度，不参与姿态解算。
    topic::imu_to::Message          pub_        {};
    uint32_t                        last_sample_cycle_ = 0;
    bool                            ready_      = false;
};


/**
 * @brief 初始化 IMU 管理器
 *
 * 按依赖顺序串联各子模块：选择数据源 → 初始化驱动 → 初始化加热器
 * → 可选的预热与静态校准 → 初始化姿态解算器。
 *
 * @param enable_auto_calibration 是否在启动阶段执行预热后的静态校准
 */
void ImuManager::Init(bool enable_auto_calibration)
{
    ready_ = false;
    last_sample_cycle_ = 0;

    if (source_ == nullptr && !SelectSource()) {
        return;
    }

    if (source_ == nullptr) {
        return;
    }

    if (!source_->Init()) {
        return;
    }

    if (!heater_.Init()) {
        return;
    }

    // 先把温度和静态偏置准备好，再放行姿态线程，避免冷机直接进入 EKF。
    if (enable_auto_calibration && !PrepareCalibration()) {
        return;
    }

    attitude_.Init();
    ready_ = true;
}

/**
 * @brief 启动 IMU 工作线程
 *
 * @param prio 线程优先级
 */
void ImuManager::Start(uint8_t prio)
{
    if (!ready_) {
        return;
    }

    thread_.Start(TaskEntry, prio, this);
}


/**
 * @brief 预热并执行静态校准
 *
 * 等待温度连续稳定在目标值附近达指定样本数后，触发一次静态校准。
 *
 * @return 校准是否成功
 */
bool ImuManager::PrepareCalibration()
{
    uint32_t stable_count = 0;

    constexpr uint32_t kHeaterStableSamples     = 500;
    constexpr float    kHeaterStableTolerance   = 0.5f;

    // 预热阶段只做温控和温度判稳，不做姿态解算与对外发布。
    while (stable_count < kHeaterStableSamples)
    {
        if (source_ == nullptr || !source_->Read(sample_)) {
            k_msleep(source_ != nullptr ? source_->PeriodMs() : 1);
            continue;
        }

        heater_.Update(sample_.temperature);
        if (heater_.IsStable(sample_.temperature, kHeaterStableTolerance)) {
            stable_count++;
        } else {
            stable_count = 0;
        }

        k_msleep(source_->PeriodMs());
    }

    // 由上层决定何时触发一次静态校准，底层只提供执行入口。
    return source_ != nullptr && source_->Calibrate();
}

/**
 * @brief 处理一帧样本
 *
 * 正常工作阶段中，加热控温和姿态解算共享同一帧温度/IMU 数据。
 */
bool ImuManager::Process(const Sample& sample)
{
    timer_.Update();

    heater_.Update(sample.temperature);

    timer_.Clock([&](){
        printk("%f,%f,%f\n", (double)sample.temperature, (double)pub_.yaw, (double)pub_.pitch);
    });

    attitude_.Process(sample, pub_);

    return zbus_chan_pub(&pub_imu_to, &pub_, K_MSEC(1)) == 0;
}

/**
 * @brief 任务函数
 *
 */
void ImuManager::Task()
{
    for (;;)
    {
        if (source_ != nullptr && source_->Read(sample_))
        {
            // 用真实采样间隔覆盖驱动侧名义周期，避免调度抖动直接传进 EKF。
            const uint32_t now_cycle = k_cycle_get_32();
            sample_.dt = MeasuredDtSeconds(now_cycle, last_sample_cycle_, sample_.dt);
            last_sample_cycle_ = now_cycle;
            (void)Process(sample_);
        }

        k_msleep(source_ != nullptr ? source_->PeriodMs() : 1);
    }
}

/**
 * @brief 根据 Kconfig 选择底层 IMU 数据源
 *
 * 通过 CONFIG_MOD_DEV_IMU_* 编译时宏决定实例化哪个驱动，
 * 驱动在对应板的 .conf 中开启，代码层无需频繁切换。
 *
 * @return 是否成功选定并注册了数据源
 */
bool ImuManager::SelectSource()
{
#if CONFIG_MOD_DEV_IMU_ICM42688P

    source_ = &icm42688p::Instance();
    return source_ != nullptr &&
           RegisterSourceFromDevicetree(icm42688p::RegisterFromDevicetree);

#elif CONFIG_MOD_DEV_IMU_BMI088

    source_ = &bmi088::Instance();
    return source_ != nullptr &&
           RegisterSourceFromDevicetree(bmi088::RegisterFromDevicetree);

#else
    return false;

#endif
}

static ImuManager imu_ {};

void thread_init()
{
    imu_.Init(true);       // 开启后会自动进入校准
}

void thread_start(uint8_t prio)
{
    imu_.Start(prio);
}

} // namespace thread::imu
