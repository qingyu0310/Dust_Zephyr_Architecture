# 开环辨识阶段切换判稳问题

## 问题场景

当前 `OpenLoop()` 判稳逻辑：

```cpp
if (stable_.Check(temperature, dt_us * 1e-6f)) {
    if (++stage < kNumStages) {
        duty_ = kDutySeq[stage];
        stable_.Reset();
        LOG_INF("Stage Done");
    }
}
```

`stable_` 是 `WinStable<100>`（100 帧滑动窗口），检查窗口内温度斜率是否在限内。

**担忧：高占空比切低占空比时，温度下降慢，斜率判稳过早通过。**

```
温度
│
│     0.35
│    ┌───┐
│   ┌┘   └──┐
│  ┌┘       └──┐  ← 下降斜率很小，但离 0.30 稳态还远
│ ┌┘           └──── 0.30 真实稳态
│┌┘
└──────────────────→ 时间
   ↑
   判稳通过 ← 错误
```

此时温度仍在缓慢下降，但速度低于斜率限，判稳通过。下一阶段用未稳态的温度作为起点做辨识，数据无效。

---

## 根因

| 因素 | 影响 |
|------|------|
| τ ≈ 11.5s | 3τ ≈ 35s 才到 ~95% 稳态 |
| 纯斜率判稳 | 下降末期斜率 < 0.02°C/s 但温度还差 1°C+ 才到稳态 |
| 双向阶梯 | 降温段和升温段用同一判据，降温更慢更危险 |

---

## 方案对比

### 方案 A：固定最小阶段时间（推荐）

原理：不管判稳结果，每个阶段强制跑够时间。

```cpp
constexpr float kMinStageTimeS = 40.0f;   // ≈ 3.5τ

// 阶段开始时记时间
if (elapsed_since_stage_start < kMinStageTimeS) {
    // 不到时间，不判稳
} else {
    // 超时后直接切，不判稳
}
```

**优点：**
- 温度变化方向无关，双向通用
- 实现极简单，5 行代码
- 不怕判稳误触发

**缺点：**
- 固定时间，效率不一定最优（低温区可能更快稳态）
- 必须确保 kMinStageTimeS 足够覆盖所有阶段的瞬态

**判断：** 3.5τ ≈ 40s，40s × 7 阶段 = 280s ≈ 4.7 分钟一轮，可接受。

---

### 方案 B：方向感知判稳

在判稳前加温度方向的合理性检查：

```cpp
// 计算窗口内平均温度变化率
float mean_slope = (window_last_temp - window_first_temp) / window_time;

if (duty_decreased && mean_slope > -kSlopeLimit * 0.5f) {
    // 降 duty 但温度下降太慢 → 不判稳，继续等
}
```

**优点：**
- 保留自适应判稳，高效利用时间

**缺点：**
- 实现复杂（需要区分升温/降温、记录窗口起止温度）
- 降温速率本身也在变化，阈值不好定

---

### 方案 C：方案 A + 下降段增益修正

在方案 A 的基础上，对降温段用更保守的稳定条件（更小的斜率限、更大的窗口）。

**优点：**
- 双向都安全
- 保留合理的时间有效率

**缺点：**
- 实现需要区分方向
- 参数多了一个

---

## 推荐：方案 A（固定最小阶段时间）

实现改动：

```cpp
void Identifier::OpenLoop(float temperature, uint32_t dt_us, IdentStage& state, uint8_t& stage)
{
    constexpr uint16_t kNumStages = sizeof(kDutySeq) / sizeof(kDutySeq[0]);
    constexpr float    kMinStageTimeS = 40.0f;

    static float elapsed = 0.0f;

    switch (state) 
    {
        case IdentStage::Cooldown: 
        {
            elapsed = 0.0f;
            if (temperature < kBaseC || (temperature <= kBaseC + kBaseTol && stable_.Check(temperature, dt_us * 1e-6f))) {
                state = IdentStage::Heating;
                duty_ = kDutySeq[0];
                stable_.Reset();
                elapsed = 0.0f;
                LOG_INF("Cooldown Done");
            }
            break;
        }
        case IdentStage::Heating: 
        {
            elapsed += dt_us * 1.0e-6f;
            if (elapsed >= kMinStageTimeS) {
                if (++stage < kNumStages) {
                    duty_ = kDutySeq[stage];
                    stable_.Reset();
                    elapsed = 0.0f;
                    LOG_INF("Stage Done");
                } else {
                    duty_ = kMinDuty;
                    state = IdentStage::Finished;
                    LOG_INF("Finished");
                }
            }
            break;
        }
        default:
            duty_ = kMinDuty;
            break;
    }
}
```

- kMinStageTimeS = 40s，覆盖 ≈ 3.5τ
- 不依赖判稳，阶段到时自动切
- 降温段和升温段行为一致
- 总共改 ~10 行

---

## 时间成本

| 指标 | 7 阶段单向（之前） | 7 阶段双向 + 40s |
|------|------------------|-----------------|
| 单阶段用时 | 判稳通过时切 | 固定 40s |
| 一轮总时间 | ~ 80–120s（取决于每段判稳时间） | **280s** = 4.7 min |
| 6 轮总时间 | ~ 8–12 min | **~28 min** |

28 分钟一轮辨识可以接受。如果觉得长，kMinStageTimeS 可以缩到 30s（≈ 2.6τ，~92% 稳态），总时间约 21 分钟。
