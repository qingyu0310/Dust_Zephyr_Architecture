# CAN 接收分发设计

## 现状

`Irq_handlers.cpp` 中 `user_can1_rx_callback` 使用 `switch-case` 硬编码分发：

```cpp
void user_can1_rx_callback(struct can_frame &frame, void *)
{
    switch (frame.id)
    {
    case kSteerCanId[0]:
        chassis_wheel[0].steer_motor.CanCpltRxCallback(frame.data);
        break;
    case kSteerCanId[1]:
        chassis_wheel[1].steer_motor.CanCpltRxCallback(frame.data);
        break;
    // ...
    }
}
```

**缺点：**
- 每加一个电机就要改 `Irq_handlers.cpp`
- `switch-case` 不能跨编译单元扩展，必须 `#ifdef` 包裹
- 代码不满足开闭原则

## 方案：链接段收集（类似 REGISTER_INIT）

### 核心思想

用宏在编译期将 `(CAN ID, 回调函数)` 条目放入自定义链接段，
运行时遍历该段查表分发。**零运行时注册开销，无容量限制。**

### 接口设计

```cpp
// can_dispatch.hpp
#pragma once

#include <zephyr/drivers/can.h>
#include <cstdint>

/** CAN 帧接收回调类型 */
using CanRxHandler = void (*)(uint8_t *data);

/** CAN 接收条目（放在 .can_rx 链接段） */
struct CanRxEntry {
    uint16_t      id;
    CanRxHandler  handler;
};

/**
 * @brief 注册 CAN ID 对应的接收处理器（编译期，链接段收集）
 *
 * 在文件作用域使用，每个电机一条，自动收集到 .can_rx 段。
 *
 * @code
 *   CAN_RX_HANDLER(0x201, [](uint8_t *data) {
 *       my_motor.CanCpltRxCallback(data);
 *   });
 * @endcode
 */
#define CAN_RX_HANDLER(id_, handler_)                                           \
    static const CanRxEntry kCanRxEntry_##id_                                   \
    __attribute__((used, __section__(".can_rx"))) = { id_, handler_ }

/**
 * @brief CAN 接收总入口（绑定到 Can::SetRxCallback）
 *
 * 遍历 .can_rx 段查表分发，未注册的 ID 自动丢弃。
 */
void CanRxDispatch(struct can_frame &frame, void *);
```

### 实现

```cpp
// can_dispatch.cpp
#include "can_dispatch.hpp"

// 链接段边界，由 linker script 定义
extern const CanRxEntry __can_rx_start[];
extern const CanRxEntry __can_rx_end[];

void CanRxDispatch(struct can_frame &frame, void *)
{
    for (const CanRxEntry *e = __can_rx_start; e < __can_rx_end; ++e)
    {
        if (e->id == frame.id)
        {
            e->handler(frame.data);
            return;
        }
    }
}
```

### 链接脚本

在 `tflm_init.ld` 或对应 linker script 中添加：

```ld
.can_rx : {
    __can_rx_start = .;
    KEEP(*(.can_rx))
    __can_rx_end = .;
}
```

### 使用方式

**各模块在文件作用域声明（编译期注册）：**

```cpp
// trd_chassis.cpp
CAN_RX_HANDLER(kSteerCanId[0], [](uint8_t *data) {
    chassis_wheel[0].steer_motor.CanCpltRxCallback(data);
});
CAN_RX_HANDLER(kDriveCanId[0], [](uint8_t *data) {
    chassis_wheel[0].drive_motor.CanCpltRxCallback(data);
});
CAN_RX_HANDLER(kSteerCanId[1], [](uint8_t *data) {
    chassis_wheel[1].steer_motor.CanCpltRxCallback(data);
});
CAN_RX_HANDLER(kDriveCanId[1], [](uint8_t *data) {
    chassis_wheel[1].drive_motor.CanCpltRxCallback(data);
});
```

```cpp
// trd_gimbal.cpp
CAN_RX_HANDLER(kBYawMasterId, [](uint8_t *data) {
    big_yaw_.motor.CanCpltRxCallback(data);
});
```

```cpp
// trd_test.cpp
CAN_RX_HANDLER(0x01, [](uint8_t *data) {
    dm_motor.CanCpltRxCallback(data);
});
```

**总入口：**

```cpp
// trd_can_tx.cpp
user_can1.SetRxCallback(CanRxDispatch);
```

### 与 REGISTER_INIT 对比

| | REGISTER_INIT | CAN_RX_HANDLER |
|---|---|---|
| 机制 | `__attribute__((section))` + 链接段遍历 | 完全一样 |
| 注册时机 | 编译期 | 编译期 |
| 运行时开销 | 遍历执行 init 函数 | 遍历匹配 CAN ID |
| 容量 | 无限制（链接器自动合并） | 无限制 |

### 优点

1. **开闭原则** — 加电机只需在模块文件加一行宏，不动 `Irq_handlers.cpp`
2. **去 `#ifdef`** — 宏在模块未编译时自然不存在，链接段自动不含其条目
3. **零运行时注册** — 没有初始化函数调用，没有 `kMaxEntries` 上限
4. **编译期安全** — 重复 CAN ID 会报链接错误（可根据需要降级）
5. **与现有架构一致** — `REGISTER_INIT` 是项目已有的成熟模式

### 缺点

1. **查表遍历** — `O(n)`，但电机数量通常 < 16，可忽略
2. **不能动态注册** — 条目在编译期固定，运行时不可增删
3. **宏的 scope** — 必须是文件作用域，不能放在函数或类内部

### 优化方向

- 如果电机数量多（> 32），可将 `.can_rx` 段按 ID 排序后用**二分查找**
  （需在链接后加排序步骤，或编译期用 `__attribute__((init_priority))` 变通）
