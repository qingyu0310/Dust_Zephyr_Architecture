/**
 * @file usb.cpp
 * @author qingyu
 * @brief CherryUSB CDC ACM communication driver implementation.
 * @version 0.2
 * @date 2026-06-11
 */

#include "usb.hpp"

#include "usbd_core.h"
#include "usbd_cdc_acm.h"

#include <string.h>
#include <zephyr/devicetree.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(usb, LOG_LEVEL_INF);

// CDC ACM 端点号
static constexpr uint8_t  CDC_IN_EP   = 0x81;   // bulk IN  （设备→主机）
static constexpr uint8_t  CDC_OUT_EP  = 0x01;   // bulk OUT （主机→设备）
static constexpr uint8_t  CDC_INT_EP  = 0x83;   // interrupt（通知）

// 配置描述符总长：USB 标准配置头 + CDC ACM 复合描述符
static constexpr uint16_t USB_CONFIG_SIZE = 9 + CDC_ACM_DESCRIPTOR_LEN;

static constexpr uint16_t kUsbMaxBufSize = 512;  // 接收 DMA 乒乓缓冲大小
static constexpr uint16_t kUsbTxBufSize  = 512;  // 发送 DMA 缓冲大小

/* --------------------------------------------------------------------------
 * USB 描述符
 * -------------------------------------------------------------------------- */

static const uint8_t device_descriptor[] {
    USB_DEVICE_DESCRIPTOR_INIT(USB_2_0, 0xEF, 0x02, 0x01, USBD_VID, USBD_PID, 0x0100, 0x01)
};

static const uint8_t config_descriptor_hs[] {
    USB_CONFIG_DESCRIPTOR_INIT(USB_CONFIG_SIZE, 0x02, 0x01, USB_CONFIG_BUS_POWERED, USBD_MAX_POWER),
    CDC_ACM_DESCRIPTOR_INIT(0x00, CDC_INT_EP, CDC_OUT_EP, CDC_IN_EP, USB_BULK_EP_MPS_HS, 0x02),
};

static const uint8_t config_descriptor_fs[] {
    USB_CONFIG_DESCRIPTOR_INIT(USB_CONFIG_SIZE, 0x02, 0x01, USB_CONFIG_BUS_POWERED, USBD_MAX_POWER),
    CDC_ACM_DESCRIPTOR_INIT(0x00, CDC_INT_EP, CDC_OUT_EP, CDC_IN_EP, USB_BULK_EP_MPS_FS, 0x02),
};

static const uint8_t device_quality_descriptor[] {
    USB_DEVICE_QUALIFIER_DESCRIPTOR_INIT(USB_2_0, 0xEF, 0x02, 0x01, 0x01),
};

static const uint8_t other_speed_config_descriptor_hs[] {
    USB_OTHER_SPEED_CONFIG_DESCRIPTOR_INIT(USB_CONFIG_SIZE, 0x02, 0x01, USB_CONFIG_BUS_POWERED, USBD_MAX_POWER),
    CDC_ACM_DESCRIPTOR_INIT(0x00, CDC_INT_EP, CDC_OUT_EP, CDC_IN_EP, USB_BULK_EP_MPS_FS, 0x02),
};

static const uint8_t other_speed_config_descriptor_fs[] {
    USB_OTHER_SPEED_CONFIG_DESCRIPTOR_INIT(USB_CONFIG_SIZE, 0x02, 0x01, USB_CONFIG_BUS_POWERED, USBD_MAX_POWER),
    CDC_ACM_DESCRIPTOR_INIT(0x00, CDC_INT_EP, CDC_OUT_EP, CDC_IN_EP, USB_BULK_EP_MPS_HS, 0x02),
};

// 字符串描述符
static const char lang_id[] { 0x09, 0x04 };

static const char* string_descriptors[] {
    lang_id,
    "MCHCK",            // iManufacturer
    "USB CDC ACM",      // iProduct
    "qingyu_king",      // iSerialNumber
};

/**
 * @brief 设备描述符回调
 */
static const uint8_t* DeviceDescriptorCallback(uint8_t speed)
{
    (void)speed;
    return device_descriptor;
}

/**
 * @brief 配置描述符回调（区分 HS/FS）
 */
static const uint8_t* ConfigDescriptorCallback(uint8_t speed)
{
    if (speed == USB_SPEED_HIGH) return config_descriptor_hs;
    if (speed == USB_SPEED_FULL) return config_descriptor_fs;
    return nullptr;
}

/**
 * @brief 设备限定符回调
 */
static const uint8_t* DeviceQualityDescriptorCallback(uint8_t speed)
{
    (void)speed;
    return device_quality_descriptor;
}

/**
 * @brief 其他速度配置描述符回调
 */
static const uint8_t* OtherSpeedConfigDescriptorCallback(uint8_t speed)
{
    if (speed == USB_SPEED_HIGH) return other_speed_config_descriptor_hs;
    if (speed == USB_SPEED_FULL) return other_speed_config_descriptor_fs;
    return nullptr;
}

/**
 * @brief 字符串描述符回调
 */
static const char* StringDescriptorCallback(uint8_t speed, uint8_t index)
{
    (void)speed;

    if (index >= (sizeof(string_descriptors) / sizeof(string_descriptors[0]))) {
        return nullptr;
    }

    return string_descriptors[index];
}

/**
 * @brief CherryUSB 描述符表
 */
static const struct usb_descriptor cdc_descriptor {
    DeviceDescriptorCallback,
    ConfigDescriptorCallback,
    DeviceQualityDescriptorCallback,
    OtherSpeedConfigDescriptorCallback,
    StringDescriptorCallback,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

/* --------------------------------------------------------------------------
 * 运行时对象
 * -------------------------------------------------------------------------- */

// DMA 直接访问，必须放在 nocache 区域
static USB_NOCACHE_RAM_SECTION USB_MEM_ALIGNX uint8_t read_buffer[2][kUsbMaxBufSize];
static USB_NOCACHE_RAM_SECTION USB_MEM_ALIGNX uint8_t tx_buffer[kUsbTxBufSize];

static Usb* usb_instances[CONFIG_USBDEV_MAX_BUS];

static struct usbd_interface cdc_intf0;
static struct usbd_interface cdc_intf1;

// 端点描述符
static struct usbd_endpoint cdc_out_ep {
    CDC_OUT_EP,
    usb_cdc_bulk_out,
};

static struct usbd_endpoint cdc_in_ep {
    CDC_IN_EP,
    usb_cdc_bulk_in,
};

// CDC ACM 线编码 — 固定值，主机串口工具读此做默认显示
static const struct cdc_line_coding line_coding_state {
    115200,
    0,
    0,
    8,
};

/**
 * @brief 从设备树获取 USB 控制器寄存器基址
 * @return 寄存器基址，0=未找到
 */
static uint32_t DefaultRegBase()
{
#if DT_NODE_HAS_STATUS(DT_NODELABEL(cherryusb_usb0), okay)
    return DT_REG_ADDR(DT_NODELABEL(cherryusb_usb0));
#else
    return 0;
#endif
}

/**
 * @brief 获取 busid 对应的 Usb 实例
 * @param busid USB 总线号
 * @return Usb 指针，越界返回 nullptr
 */
static Usb* UsbInstance(uint8_t busid)
{
    if (busid >= CONFIG_USBDEV_MAX_BUS) {
        return nullptr;
    }

    return usb_instances[busid];
}

/* --------------------------------------------------------------------------
 * CherryUSB 回调（C 函数 → Usb 对象转发）
 * -------------------------------------------------------------------------- */

/**
 * @brief 事件回调转发
 */
extern "C" void usb_cdc_event_handler(uint8_t busid, uint8_t event)
{
    Usb* self = UsbInstance(busid);
    if (self != nullptr) {
        self->OnEvent(busid, event);
    }
}

/**
 * @brief bulk OUT（接收）完成回调转发
 */
extern "C" void usb_cdc_bulk_out(uint8_t busid, uint8_t ep, uint32_t nbytes)
{
    Usb* self = UsbInstance(busid);
    if (self != nullptr) {
        self->OnBulkOut(busid, ep, nbytes);
    }
}

/**
 * @brief bulk IN（发送）完成回调转发
 */
extern "C" void usb_cdc_bulk_in(uint8_t busid, uint8_t ep, uint32_t nbytes)
{
    Usb* self = UsbInstance(busid);
    if (self != nullptr) {
        self->OnBulkIn(busid, ep, nbytes);
    }
}

/**
 * @brief CDC ACM 设置线编码（PC 端串口工具调用 — 忽略，固定值不受影响）
 */
extern "C" void usbd_cdc_acm_set_line_coding(uint8_t busid, uint8_t intf, cdc_line_coding* line_coding)
{
    (void)busid;
    (void)intf;
    (void)line_coding;
}

/**
 * @brief CDC ACM 获取线编码
 */
extern "C" void usbd_cdc_acm_get_line_coding(uint8_t busid, uint8_t intf, cdc_line_coding* line_coding)
{
    (void)busid;
    (void)intf;

    if (line_coding != nullptr) {
        *line_coding = line_coding_state;
    }
}

/* --------------------------------------------------------------------------
 * Usb 方法
 * -------------------------------------------------------------------------- */

/**
 * @brief 通过 Zephyr 设备树初始化
 * @param dev  USB 设备
 * @param cfg  接收缓冲配置
 */
bool Usb::Init(const struct device* dev, const RxStream::Config& cfg)
{
    (void)dev;

    Config usb_cfg {};
    usb_cfg.buf_size   = cfg.buf_size;
    usb_cfg.rx_timeout = cfg.rx_timeout;
    usb_cfg.reg_base   = DefaultRegBase();
    return Init(usb_cfg);
}

/**
 * @brief 通过手动参数初始化
 * @param cfg  完整配置（busid, reg_base, 缓冲大小等）
 */
bool Usb::Init(const Config& cfg)
{
    if (ready_) {
        return true;
    }

    busid_ = cfg.busid;
    if (busid_ >= CONFIG_USBDEV_MAX_BUS) {
        LOG_ERR("busid %d out of range", cfg.busid);
        return false;
    }

    uint32_t reg_base = cfg.reg_base;
    if (reg_base == 0) {
        reg_base = DefaultRegBase();
    }
    if (reg_base == 0) {
        LOG_ERR("no reg_base");
        return false;
    }

    uint16_t bs = cfg.buf_size > kMaxBufSize ? kMaxBufSize : cfg.buf_size;
    if (bs < 2) {
        bs = kMaxBufSize;
    }

    buf_size_          = bs;
    head_              = 0;
    tail_              = 0;
    read_buffer_index_ = 0;
    ready_             = false;
    configured_        = false;
    tx_busy_           = false;

    usb_instances[busid_] = this;

    memset(&cdc_intf0, 0, sizeof(cdc_intf0));
    memset(&cdc_intf1, 0, sizeof(cdc_intf1));

    // 注册描述符、接口、端点
    usbd_desc_register(busid_, &cdc_descriptor);
    usbd_add_interface(busid_, usbd_cdc_acm_init_intf(busid_, &cdc_intf0));
    usbd_add_interface(busid_, usbd_cdc_acm_init_intf(busid_, &cdc_intf1));
    usbd_add_endpoint(busid_, &cdc_out_ep);
    usbd_add_endpoint(busid_, &cdc_in_ep);

    if (usbd_initialize(busid_, reg_base, usb_cdc_event_handler) != 0) {
        LOG_ERR("usbd_initialize failed busid=%d base=0x%x", busid_, reg_base);
        usb_instances[busid_] = nullptr;
        return false;
    }

    ready_ = true;
    LOG_INF("usb ready busid=%d base=0x%x", busid_, reg_base);

    return true;
}

/**
 * @brief 注册接收通知信号量
 * @param sem  有数据时 give 的信号量
 */
void Usb::SetNotify(struct k_sem* sem)
{
    notify_sem_ = sem;
}

/**
 * @brief 读取接收环形缓冲
 * @param buf     输出缓冲区
 * @param max_len 最大读取字节数
 * @return 实际读取字节数
 */
uint16_t Usb::Read(uint8_t* buf, uint16_t max_len)
{
    if (buf == nullptr || max_len == 0) {
        return 0;
    }

    uint16_t available = (head_ - tail_ + buf_size_) % buf_size_;
    uint16_t cnt = (max_len < available) ? max_len : available;

    if (cnt > 0) {
        uint16_t to_end = buf_size_ - tail_;
        if (cnt <= to_end) {
            memcpy(buf, &rx_buf_[tail_], cnt);
        } else {
            memcpy(buf, &rx_buf_[tail_], to_end);
            memcpy(buf + to_end, &rx_buf_[0], cnt - to_end);
        }
        tail_ = (tail_ + cnt) % buf_size_;
    }

    return cnt;
}

/**
 * @brief 发送一帧数据（阻塞式拷贝，DMA 异步发送）
 * @param data 数据指针
 * @param len  数据长度
 * @return true=提交成功，false=忙或参数错误
 */
bool Usb::Send(const uint8_t* data, uint32_t len)
{
    if (!ready_ || !configured_) {
        return false;
    }
    if (data == nullptr || len == 0 || len > sizeof(tx_buffer)) {
        return false;
    }
    if (tx_busy_) {
        return false;
    }

    memcpy(tx_buffer, data, len);

    tx_busy_ = true;
    if (usbd_ep_start_write(busid_, CDC_IN_EP, tx_buffer, len) != 0) {
        tx_busy_ = false;
        return false;
    }

    return true;
}

/**
 * @brief 获取 USB 端口速度
 * @return USB_SPEED_HIGH=3(HS 480Mbps), USB_SPEED_FULL=2(FS 12Mbps)
 */
uint8_t Usb::GetSpeed() const
{
    return usbd_get_port_speed(busid_);
}

/**
 * @brief CherryUSB 事件回调：RESET/DISCONNECTED/CONFIGURED
 */
void Usb::OnEvent(uint8_t busid, uint8_t event)
{
    switch (event)
    {
        case USBD_EVENT_RESET:
        case USBD_EVENT_DISCONNECTED:
        {
            configured_ = false;
            tx_busy_ = false;
            head_ = 0;
            tail_ = 0;
            break;
        }
        case USBD_EVENT_CONFIGURED:
        {
            configured_ = true;
            tx_busy_ = false;
            head_ = 0;
            tail_ = 0;
            read_buffer_index_ = 0;
            (void)StartRead(busid, CDC_OUT_EP);
            break;
        }
        default:
            break;
    }
}

/**
 * @brief bulk OUT 完成回调：乒乓切换后存入环形缓冲，重新启动接收
 */
void Usb::OnBulkOut(uint8_t busid, uint8_t ep, uint32_t nbytes)
{
    // 乒乓切换 DMA 缓冲
    uint8_t index = read_buffer_index_;
    read_buffer_index_ = (index == 0U) ? 1U : 0U;

    if (nbytes > kMaxBufSize) {
        nbytes = kMaxBufSize;
    }

    StoreRx(&read_buffer[index][0], static_cast<uint16_t>(nbytes));

    // 重新启动接收
    (void)StartRead(busid, ep);
}

/**
 * @brief bulk IN 完成回调：MPS 整数倍时发 ZLP 收尾，否则释放 TX 忙标志
 */
void Usb::OnBulkIn(uint8_t busid, uint8_t ep, uint32_t nbytes)
{
    uint16_t mps = usbd_get_ep_mps(busid, ep);

    // ZLP — 如果数据恰好是端点大小的整数倍，发 ZLP 收尾
    if (mps != 0U && (nbytes % mps) == 0U && nbytes != 0U) {
        if (usbd_ep_start_write(busid, ep, nullptr, 0) == 0) {
            return;
        }
    }

    tx_busy_ = false;
}

/**
 * @brief 将 DMA 接收数据写入环形缓冲，通知消费方
 */
void Usb::StoreRx(const uint8_t* data, uint16_t len)
{
    if (data == nullptr || len == 0) {
        return;
    }

    // 计算可用空间（保留一格区分空/满）
    uint16_t used = (head_ - tail_ + buf_size_) % buf_size_;
    uint16_t free = buf_size_ - 1 - used;
    if (len > free) {
        len = free;
    }
    if (len == 0) {
        return;
    }

    // 分两段 memcpy 处理环形绕回
    uint16_t to_end = buf_size_ - head_;
    if (len <= to_end) {
        memcpy(&rx_buf_[head_], data, len);
    } else {
        memcpy(&rx_buf_[head_], data, to_end);
        memcpy(&rx_buf_[0], data + to_end, len - to_end);
    }

    head_ = (head_ + len) % buf_size_;

    if (notify_sem_ != nullptr) {
        k_sem_give(notify_sem_);
    }
}

/**
 * @brief 启动一次 bulk OUT 接收
 * @return true=提交成功
 */
bool Usb::StartRead(uint8_t busid, uint8_t ep)
{
    uint16_t mps = usbd_get_ep_mps(busid, ep);
    if (mps == 0) {
        return false;
    }
    if (mps > kMaxBufSize) {
        mps = kMaxBufSize;
    }

    return usbd_ep_start_read(busid, ep, &read_buffer[read_buffer_index_][0], mps) == 0;
}
