/**
 * @file icm42688p.hpp
 * @author qingyu
 * @brief ICM42688P IMU Source 接口与配置定义
 * @version 0.1
 * @date 2026-06-02
 */

#pragma once

#include "imu_source_base.hpp"
#include "spi.hpp"

#include <cstdint>
#include <zephyr/drivers/spi.h>

namespace icm42688p {

/**
 * @brief ICM42688P 驱动错误类型
 */
enum class Error : uint8_t
{
    None = 0,
    DeviceNotReady,
    ChipId,
    Config,
    ReadFailed,
};

/**
 * @brief ICM42688P 数据源实现
 *
 * ICM42688P 使用单 SPI 设备访问，寄存器分段通过 `SEG_SEL` 切换。
 * 公共工程量转换、校准与读样流程由基类复用。
 */
class Icm42688p final : public CalibratedImuSource
{
public:
    /**
     * @brief ICM42688P 运行时配置
     */
    struct Config {
        const struct spi_dt_spec *spi = nullptr;
        ImuCommonConfig common {};
    };

    /**
     * @brief 更新当前 ICM42688P 配置
     */
    void Configure(const Config& config);

    /**
     * @brief 初始化 SPI、校验芯片 ID 并按需执行自动标定
     */
    bool Init() override;
    bool Calibrate() override;

    /**
     * @brief 返回最近一次驱动错误码
     */
    Error LastError() const;

private:
    static constexpr float kAccel16gSensitivity     = 16.0f / 32768.0f;
    static constexpr float kGyro2000Sensitivity     = 0.0010652644360316953f;
    static constexpr float kTemperatureSensitivity  = 1.0f / 512.0f;
    static constexpr float kTemperatureOffset       = 23.0f;
    static constexpr float kGravity                 = 9.8f;

    bool  SoftReset          ();
    bool  InitRegisters      ();
    bool  AutoCalibrate      ();
    bool  SelectSegment      (uint8_t segment);
    bool  ReadRaw            (ImuRawSample& raw) override;
    bool  WriteReg           (uint8_t addr, uint8_t  value);
    bool  ReadReg            (uint8_t addr, uint8_t& value);
    bool  ReadRegs           (uint8_t addr, uint8_t  *data, uint32_t len);
    bool  WriteChecked       (uint8_t addr, uint8_t  value);
    float ConvertAccel       (int16_t raw) const override;
    float ConvertGyro        (int16_t raw) const override;
    float ConvertTemperature (int16_t raw) const override;
    void  SleepMs            (uint32_t ms) const override;

    Config config_ {};
    Spi spi_ {};
    Error last_error_ = Error::None;
    uint8_t current_segment_ = 0xFFU;
    
    // ICM42688P 当前最大单次 SPI 事务为连续读 12 字节运动数据加 1 字节命令，
    // 这里额外留出少量余量，统一使用 16 字节缓冲区。
    static constexpr uint32_t kSpiBufferSize = 16U;
    uint8_t tx_[kSpiBufferSize] {};
    uint8_t rx_[kSpiBufferSize] {};
};

/**
 * @brief 返回 ICM42688P 单例数据源
 */
Icm42688p& Instance();

/**
 * @brief 根据 devicetree alias 构造 ICM42688P 配置
 */
bool RegisterFromDevicetree(uint32_t period_ms = 1);

} // namespace icm42688p
