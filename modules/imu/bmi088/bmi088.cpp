/**
 * @file bmi088.cpp
 * @author qingyu
 * @brief BMI088 IMU Source 实现
 * @version 0.1
 * @date 2026-06-02
 */

#include "bmi088.hpp"
#include "bmi088_reg.hpp"

#include <string.h>
#include <zephyr/devicetree.h>
#include <zephyr/kernel.h>

namespace bmi088 {

namespace {

/**
 * @brief BMI088 单寄存器初始化项
 */
struct RegConfig {
    uint8_t reg;
    uint8_t value;
};

/**
 * @brief 加速度计初始化寄存器表
 */
constexpr RegConfig kAccelConfig[] {
    {reg::kAccPwrCtrl,      reg::kAccPowerOn            },
    {reg::kAccPwrConf,      reg::kAccPowerActive        },
    {reg::kAccConf,         static_cast<uint8_t>(reg::kAccNormal | reg::kAcc800Hz | reg::kAccConfMustSet)},
    {reg::kAccRange,        reg::kAccRange6g            },
    {reg::kAccInt1IoCtrl,   static_cast<uint8_t>(reg::kAccInt1Enable | reg::kAccInt1PushPull | reg::kAccInt1Low)},
    {reg::kAccIntMapData,   reg::kAccInt1Drdy           },
};

/**
 * @brief 陀螺仪初始化寄存器表
 */
constexpr RegConfig kGyroConfig[] {
    {reg::kGyroRange,       reg::kGyro2000Dps           },
    {reg::kGyroBandwidth,   reg::kGyro2000Hz230Hz       },
    {reg::kGyroLpm1,        reg::kGyroNormalMode        },
    {reg::kGyroCtrl,        reg::kGyroDrdyOn            },
    {reg::kGyroIoConf,      reg::kGyroInt3PushPullLow   },
    {reg::kGyroIoMap,       reg::kGyroDrdyInt3          },
};

static Bmi088 bmi088_ {};

} // namespace

/**
 * @brief 返回 BMI088 单例对象
 */
Bmi088& Instance()
{
    return bmi088_;
}

/**
 * @brief 通过板级 alias 构造 BMI088 Source 配置
 */
bool RegisterFromDevicetree(uint32_t period_ms)
{
    constexpr uint32_t kSpiOperation = SPI_WORD_SET(8) | SPI_TRANSFER_MSB | SPI_MODE_CPOL | SPI_MODE_CPHA;

    static const struct spi_dt_spec accel = SPI_DT_SPEC_GET(DT_ALIAS(bmi088_accel), kSpiOperation, 0);
    static const struct spi_dt_spec gyro  = SPI_DT_SPEC_GET(DT_ALIAS(bmi088_gyro),  kSpiOperation, 0);

    Bmi088::Config cfg {};
    cfg.accel = &accel;
    cfg.gyro  = &gyro;
    cfg.common.period_ms = period_ms;

    for (uint8_t axis = 0; axis < 3; axis++) 
    {
        cfg.common.static_calibration.gyro_offset [axis] = reg::kStaticGyroOffset [axis];
        cfg.common.static_calibration.gyro_scale  [axis] = reg::kStaticGyroScale  [axis];
        cfg.common.static_calibration.accel_offset[axis] = reg::kStaticAccelOffset[axis];
        cfg.common.static_calibration.accel_scale [axis] = reg::kStaticAccelScale [axis];
    }

    bmi088_.Configure(cfg);
    return true;
}

/**
 * @brief 保存当前 BMI088 运行时配置
 */
void Bmi088::Configure(const Config& config)
{
    config_ = config;
    ConfigureCommon(config.common);
}

/**
 * @brief 返回最近一次驱动错误码
 */
Error Bmi088::LastError() const
{
    return last_error_;
}

/**
 * @brief 初始化 BMI088 两个子器件并按需执行自动标定
 */
bool Bmi088::Init()
{
    last_error_ = Error::None;

    if (config_.accel == nullptr || !accel_.Init(*config_.accel)) {
        last_error_ = Error::AccelNotReady;
        return false;
    }

    if (config_.gyro == nullptr  || !gyro_.Init(*config_.gyro)) {
        last_error_ = Error::GyroNotReady;
        return false;
    }

    if (!InitAccel() || !InitGyro()) {
        return false;
    }

    return true;
}

bool Bmi088::Calibrate()
{
    return AutoCalibrate();
}

/**
 * @brief 读取一帧 BMI088 原始寄存器样本
 */
bool Bmi088::ReadRaw(ImuRawSample& raw)
{
    uint8_t accel_raw[6] {};
    uint8_t gyro_raw[6] {};
    uint8_t temp_raw[2] {};

    if (!ReadAccel(reg::kAccXoutL,  accel_raw, sizeof(accel_raw)) ||
        !ReadGyro (reg::kGyroXoutL, gyro_raw,  sizeof(gyro_raw))  ||
        !ReadAccel(reg::kAccTempM,  temp_raw,  sizeof(temp_raw))) 
    {
        last_error_ = Error::ReadFailed;
        return false;
    }

    raw.accel[0] = static_cast<int16_t>((static_cast<uint16_t>(accel_raw[1]) << 8) | static_cast<uint16_t>(accel_raw[0]));
    raw.accel[1] = static_cast<int16_t>((static_cast<uint16_t>(accel_raw[3]) << 8) | static_cast<uint16_t>(accel_raw[2]));
    raw.accel[2] = static_cast<int16_t>((static_cast<uint16_t>(accel_raw[5]) << 8) | static_cast<uint16_t>(accel_raw[4]));

    raw.gyro [0] = static_cast<int16_t>((static_cast<uint16_t>(gyro_raw[1]) << 8) | static_cast<uint16_t>(gyro_raw[0]));
    raw.gyro [1] = static_cast<int16_t>((static_cast<uint16_t>(gyro_raw[3]) << 8) | static_cast<uint16_t>(gyro_raw[2]));
    raw.gyro [2] = static_cast<int16_t>((static_cast<uint16_t>(gyro_raw[5]) << 8) | static_cast<uint16_t>(gyro_raw[4]));

    raw.temperature = static_cast<int16_t>((static_cast<uint16_t>(temp_raw[0]) << 3) | (static_cast<uint16_t>(temp_raw[1]) >> 5));
    if (raw.temperature > 1023) {
        raw.temperature -= 2048;
    }

    last_error_ = Error::None;
    return true;
}

/**
 * @brief 初始化加速度计子器件
 */
bool Bmi088::InitAccel()
{
    uint8_t chip_id = 0;
    // BMI088 在上电或复位后的前几次寄存器访问可能不稳定，
    // 这里先做两次预热读，再进入后续正式的 CHIP_ID 校验流程。
    (void)ReadAccelReg(reg::kAccChipId, chip_id);
    k_msleep(1);
    (void)ReadAccelReg(reg::kAccChipId, chip_id);
    k_msleep(1);

    if (!WriteAccel(reg::kAccSoftReset, reg::kAccResetValue)) {
        last_error_ = Error::AccelConfig;
        return false;
    }
    k_msleep(80);

    (void)ReadAccelReg(reg::kAccChipId, chip_id);
    k_msleep(1);
    if (!ReadAccelReg(reg::kAccChipId, chip_id) || chip_id != reg::kAccChipIdValue) {
        last_error_ = Error::AccelChipId;
        return false;
    }

    for (const auto& cfg : kAccelConfig) {
        if (!WriteCheckedAccel(cfg.reg, cfg.value)) {
            last_error_ = Error::AccelConfig;
            return false;
        }
        k_msleep(1);
    }

    return true;
}

/**
 * @brief 初始化陀螺仪子器件
 */
bool Bmi088::InitGyro()
{
    uint8_t chip_id = 0;
    (void)ReadGyroReg(reg::kGyroChipId, chip_id);   // BMI088 在上电或复位后的前几次寄存器访问可能不稳定，
    k_msleep(1);
    (void)ReadGyroReg(reg::kGyroChipId, chip_id);   // 这里先做两次预热读，再进入后续正式的 CHIP_ID 校验流程。
    k_msleep(1);

    if (!WriteGyro(reg::kGyroSoftReset, reg::kGyroResetValue)) {
        last_error_ = Error::GyroConfig;
        return false;
    }
    k_msleep(80);

    (void)ReadGyroReg(reg::kGyroChipId, chip_id);
    k_msleep(1);

    if (!ReadGyroReg(reg::kGyroChipId, chip_id) || chip_id != reg::kGyroChipIdValue) {
        last_error_ = Error::GyroChipId;
        return false;
    }

    for (const auto& cfg : kGyroConfig) 
    {
        if (!WriteCheckedGyro(cfg.reg, cfg.value)) {
            last_error_ = Error::GyroConfig;
            return false;
        }
        k_msleep(1);
    }

    return true;
}

/**
 * @brief 使用公共流程执行 BMI088 自动标定
 */
bool Bmi088::AutoCalibrate()
{
    return AutoCalibrateCommon(kGravity);
}

/**
 * @brief 向加速度计写一个寄存器
 */
bool Bmi088::WriteAccel(uint8_t addr, uint8_t value)
{
    tx_[0] = addr & reg::kWriteFlag;
    tx_[1] = value;
    return accel_.Send(tx_, 2);
}

/**
 * @brief 向陀螺仪写一个寄存器
 */
bool Bmi088::WriteGyro(uint8_t addr, uint8_t value)
{
    tx_[0] = addr & reg::kWriteFlag;
    tx_[1] = value;
    return gyro_.Send(tx_, 2);
}

/**
 * @brief 从加速度计读取连续寄存器
 */
bool Bmi088::ReadAccel(uint8_t addr, uint8_t *data, uint32_t len)
{
    constexpr uint8_t kReadDummyByte = 0x55U;
    constexpr uint8_t kRxClearByte   = 0x00U;

    if (data == nullptr || len == 0 || len + 2 > sizeof(tx_)) {
        return false;
    }

    memset(tx_, kReadDummyByte, len + 2);
    memset(rx_, kRxClearByte,   len + 2);
    tx_[0] = addr | reg::kReadFlag;

    if (!accel_.Transceive(tx_, rx_, len + 2)) {
        return false;
    }

    memcpy(data, &rx_[2], len);
    return true;
}

/**
 * @brief 从陀螺仪读取连续寄存器
 */
bool Bmi088::ReadGyro(uint8_t addr, uint8_t *data, uint32_t len)
{
    constexpr uint8_t kReadDummyByte = 0x55U;
    constexpr uint8_t kRxClearByte   = 0x00U;

    if (data == nullptr || len == 0 || len + 1 > sizeof(tx_)) {
        return false;
    }

    memset(tx_, kReadDummyByte, len + 1);
    memset(rx_, kRxClearByte,   len + 1);
    tx_[0] = addr | reg::kReadFlag;

    if (!gyro_.Transceive(tx_, rx_, len + 1)) {
        return false;
    }

    memcpy(data, &rx_[1], len);
    return true;
}

/**
 * @brief 读取一个加速度计寄存器
 */
bool Bmi088::ReadAccelReg(uint8_t addr, uint8_t& value)
{
    return ReadAccel(addr, &value, 1);
}

/**
 * @brief 读取一个陀螺仪寄存器
 */
bool Bmi088::ReadGyroReg(uint8_t addr, uint8_t& value)
{
    return ReadGyro(addr, &value, 1);
}

/**
 * @brief 写入加速度计寄存器并执行基本回读校验
 */
bool Bmi088::WriteCheckedAccel(uint8_t addr, uint8_t value)
{
    uint8_t readback = 0;
    if (!WriteAccel(addr, value)) {
        return false;
    }
    k_msleep(1);
    return ReadAccelReg(addr, readback) && readback == value;
}

/**
 * @brief 写入陀螺仪寄存器并执行基本回读校验
 */
bool Bmi088::WriteCheckedGyro(uint8_t addr, uint8_t value)
{
    uint8_t readback = 0;
    if (!WriteGyro(addr, value)) {
        return false;
    }
    k_msleep(1);
    return ReadGyroReg(addr, readback) && readback == value;
}

/**
 * @brief 将原始加速度计数转换为工程量
 */
float Bmi088::ConvertAccel(int16_t raw) const
{
    return static_cast<float>(raw) * kAccel6gSensitivity;
}

/**
 * @brief 将原始角速度计数转换为工程量
 */
float Bmi088::ConvertGyro(int16_t raw) const
{
    return static_cast<float>(raw) * kGyro2000Sensitivity;
}

/**
 * @brief 将原始温度计数转换为摄氏度
 */
float Bmi088::ConvertTemperature(int16_t raw) const
{
    return static_cast<float>(raw) * kTemperatureFactor + kTemperatureOffset;
}

/**
 * @brief 提供给公共基类使用的毫秒级延时
 */
void Bmi088::SleepMs(uint32_t ms) const
{
    k_msleep(ms);
}

} // namespace bmi088
