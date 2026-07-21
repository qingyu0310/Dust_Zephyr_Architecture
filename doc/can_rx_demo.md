# CAN RX 注册方案 v2

## 问题

v1（Zephyr 原生 `can_add_rx_filter`）让模块层直接写：

```cpp
can_add_rx_filter(DEVICE_DT_GET(DT_NODELABEL(can1)), ...);
```

模块层知道了 `can1`、`DT_NODELABEL` 等硬件细节，架构违规。

## 方案：项目层提供 CAN RX Hub

### 接口（放在 modules 公共头文件，或 algorithm 层）

```cpp
// can_rx_hub_api.hpp  — 模块层只看到这个
#pragma once
#include <stdint.h>
#include <functional>

using CanRxCallback = void(*)(const uint8_t *data, void *user_data);

struct CanRxRegistration {
    uint32_t    can_id;
    CanRxCallback callback;
    void        *user_data;
};

// 项目层实现这个函数，模块层 Init() 时调用。
void can_rx_register(const CanRxRegistration &reg);
```

### 模块层使用

```cpp
// chassis/init.cpp
#include "can_rx_hub_api.hpp"

bool Chassis::Init()
{
    can_rx_register({kSteerCanId[0],
                     [](const uint8_t *data, void *user_data) {
                         auto *motor = static_cast<Motor*>(user_data);
                         motor->CanCpltRxCallback(data);
                     },
                     &chassis_wheel[0].steer_motor});

    can_rx_register({kDriveCanId[0],
                     [](const uint8_t *data, void *user_data) {
                         auto *motor = static_cast<Motor*>(user_data);
                         motor->CanCpltRxCallback(data);
                     },
                     &chassis_wheel[0].drive_motor});
    return true;
}
```

### 项目层实现

```cpp
// project/can/can_rx_hub.cpp  — 唯一知道 can1 的地方
#include "can_rx_hub_api.hpp"
#include <zephyr/drivers/can.h>

static const struct device *const can_dev =
    DEVICE_DT_GET(DT_NODELABEL(can1));

struct HubEntry {
    CanRxRegistration reg;
};

static HubEntry entries[16];
static int entry_count = 0;

void can_rx_register(const CanRxRegistration &reg)
{
    if (entry_count >= 16) return;

    entries[entry_count++] = {reg};

    struct can_filter filter = {
        .id   = reg.can_id,
        .mask = CAN_STD_ID_MASK,
    };
    can_add_rx_filter(can_dev,
                      [](struct can_frame *frame, void *) {
                          // 查表找到对应 entry，调用回调
                      },
                      nullptr, &filter);
}
```

### Irq_handlers.cpp 删掉

不再需要中央 switch-case。各模块 `Init()` 时自己注册。

## 架构边界

```
project/can/can_rx_hub.cpp
    ─ 知道 can1, can2, DT_NODELABEL
    ─ 实现 void can_rx_register()
    ─ 把 Zephyr CAN 帧解包为 const uint8_t *data

modules/*/init.cpp
    ─ 不知道 can1 存在
    ─ 调用 can_rx_register({ID, callback, ptr})
    ─ 只关心 CAN ID 和数据格式

Irq_handlers.cpp
    ─ 删除
```
