/**
 * @file remote.cpp
 * @author qingyu
 * @brief 基于 UART RX 通知驱动的遥控器线程。
 * @version 0.3
 * @date 2026-05-13
 *
 * @copyright Copyright (c) 2026
 *
 */

#include "remote.hpp"
#include "thread.hpp"
#include "uart.hpp"

#include "dr16.hpp"
#include "vt12.hpp"
#include "vt13.hpp"

#include <string.h>

using namespace topic::remote_to;

namespace thread::remote {

/**
 * @brief 带状态机的遥控器解码与自动识别 worker。
 */
class Remote final
{
public:
    using ValidateFunc = bool (*)(const uint8_t *buffer, uint8_t len);
    using DecodeFunc = bool (*)(const uint8_t *buffer, uint8_t len, Message &pub);

    /**
     * @brief 固定协议模式和自动识别共用的协议描述项。
     */
    struct Protocol {
        RemoteType    type;             // 协议类型，用于记录当前锁定的遥控器来源。
        const char    *name;            // 调试日志中显示的协议名称。
        uint16_t      frame_size;       // 该协议单帧长度，滑窗和消费缓冲区时使用。
        ValidateFunc  validate;         // 只判断当前窗口是否像本协议合法帧，不修改发布数据。
        DecodeFunc    decode;           // 将合法帧解码到 topic::remote_to::Message，不直接发布。
        uint8_t       lock_score;       // 连续命中多少帧后才锁定该协议，用于降低误识别概率。
    };

    /**
     * @brief UART 字节流解析器当前状态。
     */
    enum class DetectState : uint8_t
    {
        Detecting,
        Locked,
    };

    /**
     * @brief 绑定 UART RX 数据流，并选择协议工作模式。
     *
     * @param type 固定协议类型，或 RemoteType::Auto。
     * @param uart RX 数据流，通过 rx_sem_ 唤醒本线程。
     */
    void Init(RemoteType type, RxStream &uart)
    {
        configured_type_ = type;
        active_type_     = RemoteType::None;
        detect_state_    = DetectState::Detecting;
        k_sem_init(&rx_sem_, 0, 1);
        uart_ = &uart;
        uart_->SetNotify(&rx_sem_);
        InitFrameSizeRange();
        GetProcessFunc();
    }

    /**
     * @brief 启动遥控器 worker 线程。
     *
     * @param prio Zephyr 线程优先级。
     */
    void Start(uint8_t prio = 5)
    {
        thread_.Start(TaskEntry, prio, this);
    }

private:
    Thread<1024 * 8> thread_ {};
    RxStream *uart_ = nullptr;

    RemoteType  configured_type_ = RemoteType ::Auto;
    RemoteType  active_type_     = RemoteType ::None;
    DetectState detect_state_    = DetectState::Detecting;

    static constexpr uint16_t kFrameBufSize     = 64;
    static constexpr uint8_t  kUnlockFailLimit  = 5;
    static constexpr uint32_t kRemoteTimeoutMs  = 100;
    static constexpr uint8_t  kProtocolCount    = static_cast<uint8_t>(RemoteType::Auto);

    uint8_t  frame_buf_[kFrameBufSize] {};
    uint16_t frame_pos_ = 0;
    k_sem    rx_sem_;

    Message  pub_   {};
    Protocol proto_ {};
    uint8_t  hit_count_[kProtocolCount] {};
    uint8_t  fail_count_     = 0;
    uint32_t last_valid_ms_  = 0;
    uint16_t min_frame_size_ = 0;
    uint16_t max_frame_size_ = 0;
    
    void InitFrameSizeRange();
    void GetProcessFunc();
    const Protocol *FindProtocol(RemoteType type);
    void ResetDetect();
    void Consume(uint16_t len);
    void DropOneByte();
    void ClearPubData();
    void Publish();
    void HandleLocked();
    void HandleDetecting();
    void ProcessBuffered();
    void ProcessChunk(const uint8_t *data, uint16_t len);
    void Task();

    static void TaskEntry(void *p1, void *p2, void *p3)
    {
        ARG_UNUSED(p2);
        ARG_UNUSED(p3);
        auto self = static_cast<Remote *>(p1);
        self->Task();
    }
};

/**
 * @brief 自动识别使用的协议优先级表。
 *
 * 帧特征更强的协议优先尝试（即越靠前的协议被锁的优先级更高）
 * 因此需要连续多帧命中后才允许锁定。
 * 最后一个常量为协议置信度，为n时，则为正确协议 n 次才开始锁定
 */
static constexpr Remote::Protocol kProtocolTable[] {
    { RemoteType::VT13, "VT13", vt13::kFrameSizeVT13, vt13::validate, vt13::decode, 1 },
    { RemoteType::VT12, "VT12", vt12::kFrameSizeVT12, vt12::validate, vt12::decode, 2 },
    { RemoteType::DR16, "DR16", dr16::kFrameSizeDR16, dr16::validate, dr16::decode, 3 },
};

static Remote remote_ {};
static bool remote_ready_ = false;

/**
 * @brief 初始化 UART DMA，并将遥控器解码器配置为自动识别模式。
 */
void thread_init()
{
    static UartDma rx {};
    RxStream::Config cfg {};

    // HPM UART4 uses the low-level RX idle fallback to flush DMA early.
    // Keep the DMA buffer large enough to avoid full-buffer interrupt pressure.
    constexpr uint16_t kBufferSize = 128;
    constexpr uint16_t kTimeour    = 1000;

    cfg.buf_size   = kBufferSize;
    cfg.rx_timeout = kTimeour;

    if (!rx.Init(DEVICE_DT_GET(DT_ALIAS(uart_remote)), cfg)) {
        remote_ready_ = false;
        return;
    }

    remote_.Init(RemoteType::Auto, rx);
    remote_ready_ = true;
}

/**
 * @brief 如果初始化成功，则启动遥控器线程。
 *
 * @param prio Zephyr 线程优先级。
 */
void thread_start(uint8_t prio)
{
    if (!remote_ready_) {
        return;
    }

    remote_.Start(prio);
}

/**
 * @brief 按协议类型查找协议描述项。
 *
 * @param type 需要查找的协议类型。
 * @return 找到时返回协议描述项指针，否则返回 nullptr。
 */
const Remote::Protocol *Remote::FindProtocol(RemoteType type)
{
    for (const auto& protocol : kProtocolTable)
    {
        if (protocol.type == type) {
            return &protocol;
        }
    }

    return nullptr;
}

/**
 * @brief 根据协议表计算自动识别所需的最短帧长和最长帧长。
 */
void Remote::InitFrameSizeRange()
{
    min_frame_size_ = kProtocolTable[0].frame_size;
    max_frame_size_ = kProtocolTable[0].frame_size;

    for (const auto& protocol : kProtocolTable)
    {
        if (protocol.frame_size < min_frame_size_) {
            min_frame_size_ = protocol.frame_size;
        }

        if (protocol.frame_size > max_frame_size_) {
            max_frame_size_ = protocol.frame_size;
        }
    }
}

/**
 * @brief 根据配置进入固定协议模式或自动识别模式。
 */
void Remote::GetProcessFunc()
{
    if (configured_type_ == RemoteType::Auto) {
        // Auto 模式从未知协议开始，等待字节流里出现可识别的完整帧。
        ResetDetect();
        return;
    }

    const Protocol *protocol = FindProtocol(configured_type_);
    if (protocol == nullptr) {
        // 防御非法配置：没有对应协议时退回探测状态，不保留半初始化状态。
        proto_ = {};
        ResetDetect();
        return;
    }

    // 固定协议只作为初始锁定协议；失配或断连后仍会回到探测状态，允许运行中更换遥控器。
    proto_ = *protocol;
    active_type_ = proto_.type;
    detect_state_ = DetectState::Locked;
}

/**
 * @brief 回到协议探测状态，并清空探测计数。
 */
void Remote::ResetDetect()
{
    proto_ = {};
    active_type_ = RemoteType::None;
    detect_state_ = DetectState::Detecting;
    fail_count_ = 0;

    for (uint8_t& hit : hit_count_) {
        hit = 0;
    }
}

/**
 * @brief 从帧缓冲区头部移除已经消费的字节。
 *
 * @param len 需要消费的字节数。
 */
void Remote::Consume(uint16_t len)
{
    if (len >= frame_pos_) {
        frame_pos_ = 0;
        return;
    }

    // 缓冲区前 len 字节已经形成一帧或噪声，剩余字节前移继续参与下一轮解析。
    uint16_t rem = frame_pos_ - len;
    memmove(frame_buf_, frame_buf_ + len, rem);
    frame_pos_ = rem;
}

/**
 * @brief 从帧缓冲区头部丢弃 1 字节，用于重新同步。
 */
void Remote::DropOneByte()
{
    if (frame_pos_ == 0) {
        return;
    }

    frame_pos_--;
    memmove(frame_buf_, frame_buf_ + 1, frame_pos_);
}

/**
 * @brief 清空对外发布的控制量，同时保留当前协议类型。
 */
void Remote::ClearPubData()
{
    pub_ = {};
}

/**
 * @brief 通过 zbus 发布最新遥控器消息。
 */
void Remote::Publish()
{
    zbus_chan_pub(&pub_remote_to, &pub_, K_MSEC(1));
}

/**
 * @brief 使用已锁定协议解码帧，连续失败后解除锁定。
 */
void Remote::HandleLocked()
{
    if (proto_.frame_size == 0 || proto_.validate == nullptr || proto_.decode == nullptr) {
        ResetDetect();
        return;
    }

    while (frame_pos_ >= proto_.frame_size)
    {
        if (proto_.validate(frame_buf_, proto_.frame_size))
        {
            if (proto_.decode(frame_buf_, proto_.frame_size, pub_)) 
            {
                // 锁定后只有完整合法帧才刷新有效时间，超时逻辑据此判断遥控器是否断连。
                last_valid_ms_ = k_uptime_get_32();
                fail_count_ = 0;
                Publish();
                Consume(proto_.frame_size);
            } else {
                fail_count_++;
                DropOneByte();
            }
        } else {
            fail_count_++;
            DropOneByte();
        }

        if (fail_count_ >= kUnlockFailLimit) {
            // 已锁定协议连续失配，认为字节流可能换协议或错位，回到 Auto 探测。
            ResetDetect();
            break;
        }
    }
}

/**
 * @brief 在当前缓冲的字节流中搜索合法协议帧。
 *
 * 自动识别按 kProtocolTable 顺序尝试各协议。如果暂未命中，
 * 且缓冲长度还短于最长帧，则继续等待更多数据，避免过早丢字节。
 */
void Remote::HandleDetecting()
{
    while (frame_pos_ >= min_frame_size_)
    {
        bool matched = false;

        // 按表顺序尝试协议。帧头/校验更强的协议应放在 DR16 之前，降低误锁概率。
        for (uint8_t i = 0; i < sizeof(kProtocolTable) / sizeof(kProtocolTable[0]); i++)
        {
            const Protocol& protocol = kProtocolTable[i];
            if (frame_pos_ < protocol.frame_size) continue;
            bool valid = protocol.validate(frame_buf_, protocol.frame_size);
            if (!valid) continue;

            matched = true;
            hit_count_[i]++;

            // 当前窗口只允许一个协议累积命中，避免旧协议的命中数污染新判断。
            for (uint8_t j = 0; j < sizeof(hit_count_) / sizeof(hit_count_[0]); j++) {
                if (j != i) {
                    hit_count_[j] = 0;
                }
            }

            if (hit_count_[i] >= protocol.lock_score)
            {
                // 达到锁定分数后才发布第一帧；DR16 这类弱特征协议依赖多帧确认。
                proto_ = protocol;
                active_type_ = protocol.type;
                detect_state_ = DetectState::Locked;
                fail_count_ = 0;

                if (proto_.decode(frame_buf_, proto_.frame_size, pub_)) {
                    last_valid_ms_ = k_uptime_get_32();
                    Publish();
                }
            }

            // 无论是否已锁定，命中的这段字节都已经被识别为一帧，不能重复参与探测。
            Consume(protocol.frame_size);
            break;
        }

        if (!matched) 
        {
            if (frame_pos_ < max_frame_size_) {
                // 还没攒到最长帧时先不丢字节，避免把长帧前半段误当噪声丢掉。
                break;
            }

            // 已经有足够长窗口仍无协议命中，丢 1 字节滑窗重新对齐。
            for (uint8_t& hit : hit_count_) {
                hit = 0;
            }
            DropOneByte();
        }

        if (detect_state_ == DetectState::Locked) {
            break;
        }
    }
}

/**
 * @brief 基于当前帧缓冲区运行解析状态机。
 */
void Remote::ProcessBuffered()
{
    if (detect_state_ == DetectState::Locked) {
        HandleLocked();
    } else {
        HandleDetecting();
    }
}

/**
 * @brief 追加一段 UART RX 数据，并尽可能解析其中的完整帧。
 *
 * @param data RX 数据流返回的字节指针。
 * @param len 本段数据的字节数。
 */
void Remote::ProcessChunk(const uint8_t *data, uint16_t len)
{
    while (len > 0)
    {
        uint16_t cap = kFrameBufSize - frame_pos_;
        if (cap == 0) {
            // 理论上协议帧都小于 frame_buf_，满了说明长期没对齐，丢 1 字节继续滑窗。
            DropOneByte();
            cap = kFrameBufSize - frame_pos_;
        }

        // UART DMA 可能给出碎片数据，先追加到 frame_buf_，再让状态机跨 chunk 拼帧。
        uint16_t n = len > cap ? cap : len;
        memcpy(frame_buf_ + frame_pos_, data, n);
        frame_pos_ += n;
        data += n;
        len -= n;

        ProcessBuffered();
    }
}

/**
 * @brief 遥控器线程主循环。
 *
 * 每次收到 RX 通知后读完当前可用 UART 数据并送入解析器；
 * 已锁定遥控器超时时，发布归零后的控制消息。
 */
void Remote::Task()
{
    for (;;)
    {
        if (k_sem_take(&rx_sem_, K_MSEC(50)) == 0) {
            uint8_t tmp[32];
            while (true) {
                uint16_t n = uart_->Read(tmp, sizeof(tmp));
                if (n == 0) {
                    break;
                }

                ProcessChunk(tmp, n);
            }
        }
        else
        {
            uint32_t now = k_uptime_get_32();
            if (active_type_ != RemoteType::None && now - last_valid_ms_ >= kRemoteTimeoutMs)
            {
                // 已锁定但长时间没有合法帧，向控制侧发布一次归零数据，避免保持旧控制量。
                frame_pos_ = 0;
                ClearPubData();
                Publish();

                // 断连后无论初始选择哪个协议，都回到探测状态，允许运行中更换遥控器。
                ResetDetect();
            }
        }
    }
}

} // namespace thread::remote
