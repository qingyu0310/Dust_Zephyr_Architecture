/**
 * @file icm42688p.cpp
 * @author qingyu
 * @brief ICM42688P IMU Source 实现
 * @version 0.1
 * @date 2026-06-02
 */

#include "icm42688p.hpp"
#include "icm42688p_reg.hpp"

#include <string.h>
#include <zephyr/devicetree.h>
#include <zephyr/kernel.h>

namespace icm42688p {

namespace {

/**
 * @brief ICM42688P 单寄存器初始化项
 */
struct RegConfig {
    uint8_t reg;
    uint8_t value;
};

constexpr uint8_t kPwrCtrlEnableAccelGyro = 0x0F;

/**
 * @brief 当前已确认的通用寄存器初始化表
 */
constexpr RegConfig kInitConfig[] {
    {reg::kComCfg,       reg::kComCfgDefault    },
    {reg::kHpfLpfCfg,    reg::kHpfLpfCfgDefault },
    {reg::kStepCfg,      reg::kStepCfgDefault   },
    {reg::kAccConf,      reg::kAccConfDefault   },
    {reg::kAccRange,     reg::kAccRangeDefault  },
    {reg::kGyrConf,      reg::kGyrConfDefault   },
    {reg::kFifoDowns,    reg::kFifoDownsDefault },
};

static Icm42688p icm42688p_ {};

} // namespace

/**
 * @brief 返回 ICM42688P 单例对象
 */
Icm42688p& Instance()
{
    return icm42688p_;
}

/**
 * @brief 通过板级 alias 构造 ICM42688P Source 配置
 */
bool RegisterFromDevicetree(uint32_t period_ms)
{
    constexpr uint32_t kSpiOperation = SPI_WORD_SET(8) | SPI_TRANSFER_MSB | SPI_MODE_CPOL | SPI_MODE_CPHA;

    static const struct spi_dt_spec imu = SPI_DT_SPEC_GET(DT_ALIAS(spi_imu), kSpiOperation, 0);

    Icm42688p::Config cfg {};
    cfg.spi = &imu;
    cfg.common.period_ms = period_ms;

    icm42688p_.Configure(cfg);
    return true;
}

/**
 * @brief 保存当前 ICM42688P 运行时配置
 */
void Icm42688p::Configure(const Config& config)
{
    config_ = config;
    ConfigureCommon(config.common);
}

/**
 * @brief 返回最近一次驱动错误码
 */
Error Icm42688p::LastError() const
{
    return last_error_;
}

/**
 * @brief 初始化 ICM42688P 并按需执行自动标定
 */
bool Icm42688p::Init()
{
    last_error_ = Error::None;
    current_segment_ = 0xFFU;

    if (config_.spi == nullptr || !spi_.Init(*config_.spi)) {
        printk("ICM42688P: SPI init failed\n");
        last_error_ = Error::DeviceNotReady;
        return false;
    }

    k_msleep(3);

    if (!SoftReset()) {
        printk("ICM42688P: Soft reset failed\n");
        last_error_ = Error::Config;
        return false;
    }

    if (!SelectSegment(reg::kSegSelGeneral)) {
        printk("ICM42688P: Select general segment failed\n");
        last_error_ = Error::Config;
        return false;
    }

    uint8_t chip_id = 0;
    if (!ReadReg(reg::kWhoAmI, chip_id)) {
        printk("ICM42688P: Read WHO_AM_I failed\n");
        last_error_ = Error::ChipId;
        return false;
    }
    if (chip_id != reg::kWhoAmIValue) {
        printk("ICM42688P: WHO_AM_I mismatch, got 0x%02X, expected 0x%02X\n", chip_id, reg::kWhoAmIValue);
        last_error_ = Error::ChipId;
        return false;
    }

    if (!InitRegisters()) {
        printk("ICM42688P: Init registers failed\n");
        last_error_ = Error::Config;
        return false;
    }

    return true;
}

bool Icm42688p::Calibrate()
{
    if (!AutoCalibrate()) {
        printk("ICM42688P: Auto calibration failed\n");
        return false;
    }

    return true;
}

/**
 * @brief 读取一帧 ICM42688P 原始寄存器样本
 */
bool Icm42688p::ReadRaw(ImuRawSample& raw)
{
    uint8_t motion_data[12] {};
    uint8_t temp_data[2] {};

    if (!SelectSegment(reg::kSegSelGeneral) ||
        !ReadRegs(reg::kAccXH, motion_data, sizeof(motion_data)) ||
        !ReadRegs(reg::kTempH, temp_data, sizeof(temp_data))) 
    {
        last_error_ = Error::ReadFailed;
        return false;
    }

    raw.accel[0] = static_cast<int16_t>((static_cast<uint16_t>(motion_data[0]) << 8)  | static_cast<uint16_t>(motion_data[1]));
    raw.accel[1] = static_cast<int16_t>((static_cast<uint16_t>(motion_data[2]) << 8)  | static_cast<uint16_t>(motion_data[3]));
    raw.accel[2] = static_cast<int16_t>((static_cast<uint16_t>(motion_data[4]) << 8)  | static_cast<uint16_t>(motion_data[5]));

    raw.gyro [0] = static_cast<int16_t>((static_cast<uint16_t>(motion_data[6]) << 8)  | static_cast<uint16_t>(motion_data[7]));
    raw.gyro [1] = static_cast<int16_t>((static_cast<uint16_t>(motion_data[8]) << 8)  | static_cast<uint16_t>(motion_data[9]));
    raw.gyro [2] = static_cast<int16_t>((static_cast<uint16_t>(motion_data[10]) << 8) | static_cast<uint16_t>(motion_data[11]));

    raw.temperature = static_cast<int16_t>((static_cast<uint16_t>(temp_data[0]) << 8) | static_cast<uint16_t>(temp_data[1]));

    last_error_ = Error::None;
    return true;
}

/**
 * @brief 触发芯片软复位并清空本地段选择状态
 */
bool Icm42688p::SoftReset()
{
    current_segment_ = reg::kSegSelGeneral;
    if (!WriteReg(reg::kSegSel, reg::kSegSelGeneral) ||
        !WriteReg(reg::kSoftRstReg, 0x01)) {
        return false;
    }

    k_msleep(2);
    current_segment_ = 0xFFU;
    return true;
}

/**
 * @brief 写入启动寄存器表并打开 accel/gyro 工作模式
 */
bool Icm42688p::InitRegisters()
{
    if (!SelectSegment(reg::kSegSelGeneral)) {
        return false;
    }

    for (const auto& cfg : kInitConfig) 
    {
        if (!WriteChecked(cfg.reg, cfg.value)) {
            return false;
        }
        k_msleep(1);
    }

    if (!WriteChecked(reg::kPwrCtrl, kPwrCtrlEnableAccelGyro)) {
        return false;
    }

    k_msleep(50);
    return true;
}

/**
 * @brief 使用公共流程执行 ICM42688P 自动标定
 */
bool Icm42688p::AutoCalibrate()
{
    return AutoCalibrateCommon(kGravity);
}

/**
 * @brief 仅在段变化时切换寄存器段
 */
bool Icm42688p::SelectSegment(uint8_t segment)
{
    if (current_segment_ == segment) {
        return true;
    }

    tx_[0] = reg::kSegSel;
    tx_[1] = segment;

    if (!spi_.Send(tx_, 2)) {
        return false;
    }

    current_segment_ = segment;
    return true;
}

/**
 * @brief 写一个寄存器
 */
bool Icm42688p::WriteReg(uint8_t addr, uint8_t value)
{
    tx_[0] = addr;
    tx_[1] = value;
    return spi_.Send(tx_, 2);
}

/**
 * @brief 读一个寄存器
 */
bool Icm42688p::ReadReg(uint8_t addr, uint8_t& value)
{
    return ReadRegs(addr, &value, 1);
}

/**
 * @brief 通过一次 SPI transceive 连续读取多个寄存器
 */
bool Icm42688p::ReadRegs(uint8_t addr, uint8_t *data, uint32_t len)
{
    constexpr uint8_t kDummyByte = 0x00;

    if (data == nullptr || len == 0 || len + 1 > sizeof(tx_)) {
        return false;
    }

    memset(tx_, kDummyByte, len + 1);
    memset(rx_, 0x00, len + 1);
    tx_[0] = addr | reg::kReadFlag;

    if (!spi_.Transceive(tx_, rx_, len + 1)) {
        return false;
    }

    memcpy(data, &rx_[1], len);
    return true;
}

/**
 * @brief 写入寄存器并执行基本回读校验
 */
bool Icm42688p::WriteChecked(uint8_t addr, uint8_t value)
{
    uint8_t readback = 0;
    if (!WriteReg(addr, value)) {
        return false;
    }

    k_msleep(1);
    return ReadReg(addr, readback) && readback == value;
}

/**
 * @brief 将原始加速度计数转换为工程量
 */
float Icm42688p::ConvertAccel(int16_t raw) const
{
    return static_cast<float>(raw) * kAccel16gSensitivity * kGravity;
}

/**
 * @brief 将原始角速度计数转换为工程量
 */
float Icm42688p::ConvertGyro(int16_t raw) const
{
    return static_cast<float>(raw) * kGyro2000Sensitivity;
}

/**
 * @brief 将原始温度计数转换为摄氏度
 */
float Icm42688p::ConvertTemperature(int16_t raw) const
{
    return static_cast<float>(raw) * kTemperatureSensitivity + kTemperatureOffset;
}

/**
 * @brief 提供给公共基类使用的毫秒级延时
 */
void Icm42688p::SleepMs(uint32_t ms) const
{
    k_msleep(ms);
}

} // namespace icm42688p
