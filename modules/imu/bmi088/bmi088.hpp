/**
 * @file bmi088.hpp
 * @author qingyu
 * @brief BMI088 IMU Source 接口与配置定义
 * @version 0.1
 * @date 2026-06-02
 */

#pragma once

#include "imu_source_base.hpp"
#include "spi.hpp"

#include <cstdint>
#include <zephyr/drivers/spi.h>

namespace bmi088 {

/**
 * @brief BMI088 驱动错误类型
 */
enum class Error : uint8_t
{
    None = 0,
    AccelNotReady,
    GyroNotReady,
    AccelChipId,
    GyroChipId,
    AccelConfig,
    GyroConfig,
    ReadFailed,
};

/**
 * @brief BMI088 数据源实现
 *
 * BMI088 由 accel/gyro 两个 SPI 设备组成：
 * - 子类负责底层寄存器访问与初始化
 * - 公共工程量转换、校准与读样流程由基类复用
 */
class Bmi088 final : public CalibratedImuSource
{
public:
    /**
     * @brief BMI088 运行时配置
     */
    struct Config {
        const struct spi_dt_spec *accel = nullptr;
        const struct spi_dt_spec *gyro = nullptr;
        ImuCommonConfig common {};
    };

    /**
     * @brief 更新当前 BMI088 配置
     */
    void Configure(const Config& config);

    /**
     * @brief 初始化 BMI088 两个子器件并按需执行自动标定
     */
    bool Init() override;
    bool Calibrate() override;

    /**
     * @brief 返回最近一次驱动错误码
     */
    Error LastError() const;

private:
    static constexpr float kAccel6gSensitivity = 0.00179443359375f;
    static constexpr float kGyro2000Sensitivity = 0.0010652644360316953f;
    static constexpr float kTemperatureFactor = 0.125f;
    static constexpr float kTemperatureOffset = 23.0f;
    static constexpr float kGravity = 9.8f;

    bool  InitAccel          ();
    bool  InitGyro           ();
    bool  AutoCalibrate      ();
    bool  ReadRaw            (ImuRawSample& raw) override;
    bool  WriteAccel         (uint8_t addr, uint8_t  value);
    bool  WriteGyro          (uint8_t addr, uint8_t  value);
    bool  ReadAccel          (uint8_t addr, uint8_t  *data, uint32_t len);
    bool  ReadGyro           (uint8_t addr, uint8_t  *data, uint32_t len);
    bool  ReadAccelReg       (uint8_t addr, uint8_t& value);
    bool  ReadGyroReg        (uint8_t addr, uint8_t& value);
    bool  WriteCheckedAccel  (uint8_t addr, uint8_t  value);
    bool  WriteCheckedGyro   (uint8_t addr, uint8_t  value);
    float ConvertAccel       (int16_t raw) const override;
    float ConvertGyro        (int16_t raw) const override;
    float ConvertTemperature (int16_t raw) const override;
    void  SleepMs            (uint32_t ms) const override;

    Config config_ {};
    Spi accel_ {};
    Spi gyro_  {};
    Error last_error_ = Error::None;

    // BMI088 当前最大单次 SPI 事务为 accel 连读 6 字节数据加 2 字节开销。
    static constexpr uint32_t kSpiBufferSize = 8U;
    uint8_t tx_[kSpiBufferSize] {};
    uint8_t rx_[kSpiBufferSize] {};
};

/**
 * @brief 返回 BMI088 单例数据源
 */
Bmi088& Instance();

/**
 * @brief 根据 devicetree alias 构造 BMI088 配置
 */
bool RegisterFromDevicetree(uint32_t period_ms = 1);

} // namespace bmi088
