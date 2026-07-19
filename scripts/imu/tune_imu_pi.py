"""
tune_imu_pi.py - IMU heater PID simulation

The default plant is the current identified local model:
    G(s) = 41.888 / (12.049*s + 1) * exp(-2.450*s)

The controller simulation follows algorithm/controller/pid/pid.cpp.

Examples:
    python scripts/tune_imu_pi.py
    python scripts/imu/tune_imu_pi.py --no-plot
    python scripts/imu/tune_imu_pi.py --kp 0.13 --ki 0.03 --kd 0.0
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np


# ==================== 可直接修改的默认参数 ====================
# 热对象模型：G(s) = PLANT_GAIN / (PLANT_TAU*s + 1) * exp(-PLANT_DELAY*s)
PLANT_GAIN              = 41.888     # K, 稳态增益 (°C/duty)
PLANT_TAU               = 12.049     # tau, 时间常数 (s)
PLANT_DELAY             = 2.450      # delay, 纯延迟 (s)

# 仿真初始条件
TARGET_TEMPERATURE      = 40.0       # 目标温度 (°C)
INITIAL_TEMPERATURE     = 30.0       # 仿真起始温度 (°C)
AMBIENT_TEMPERATURE     = 37.330     # 联合模型温度偏置 (°C)

# PWM 占空比限制
DUTY_MIN                = 0.05       # 闭环 duty 下限
DUTY_MAX                = 0.95       # 闭环 duty 上限

# 当前固件 PID 参数
PI_KP                   = 0.13       # 比例增益
PI_KI                   = 0.03       # 积分增益
PI_KD                   = 0.0        # 微分增益

# 仿真设置
SIMULATION_DT           = 0.001      # 仿真步长 (s)
SIMULATION_DURATION     = 100.0      # 仿真总时长 (s)
SETTLING_BAND           = 0.5        # 判稳温度带 (°C)
SETTLING_DEADLINE       = 5.0        # 判稳最大等待时间 (s)
STABLE_SLOPE_LIMIT      = 0.02       # 稳定斜率限 (°C/s)
STABLE_REQUIRED_SAMPLES = 500        # 连续稳定帧数

# ============================================================


@dataclass
class Simulation:
    time: np.ndarray
    temperature: np.ndarray
    duty: np.ndarray
    error: np.ndarray


@dataclass
class Score:
    kp: float
    ki: float
    overshoot: float
    first_reach_time: float
    settling_time: float
    steady_error: float
    saturation_ratio: float


def simulate_pi(
    kp: float,
    ki: float,
    kd: float,
    *,
    target: float,
    initial_temperature: float,
    ambient_temperature: float,
    plant_gain: float,
    tau: float,
    delay: float,
    dt: float,
    duration: float,
    duty_min: float,
    duty_max: float,
) -> Simulation:
    sample_count = int(round(duration / dt)) + 1
    time = np.arange(sample_count, dtype=float) * dt
    temperature = np.empty(sample_count, dtype=float)
    duty = np.empty(sample_count, dtype=float)
    error = np.empty(sample_count, dtype=float)

    delay_samples = max(0, int(round(delay / dt)))
    duty_buffer = np.full(delay_samples + 1, duty_min, dtype=float)
    buffer_index = 0
    temperature[0] = initial_temperature
    integral_error = 0.0
    previous_output = 0.0
    previous_error = 0.0

    for index in range(sample_count):
        if index > 0:
            delayed_duty = duty_buffer[buffer_index]
            equilibrium = ambient_temperature + plant_gain * delayed_duty
            temperature[index] = temperature[index - 1] + (
                equilibrium - temperature[index - 1]
            ) * dt / tau

        current_error = target - temperature[index]

        # Match Pid::CalcImpl(): DFirst is disabled, so D acts on error.
        p_out = kp * current_error
        integral_limit = 0.5 / ki if ki != 0.0 else float("inf")
        integral_error = float(
            np.clip(integral_error, -integral_limit, integral_limit)
        )

        if (
            abs(previous_output) >= duty_max
            or (previous_error > 0.0 and current_error < 0.0)
            or (previous_error < 0.0 and current_error > 0.0)
        ):
            integral_error = 0.0

        integral_error += dt * current_error
        i_out = ki * integral_error
        d_out = kd * (current_error - previous_error) / dt
        pid_output = float(np.clip(p_out + i_out + d_out, -duty_max, duty_max))
        heater_duty = float(np.clip(pid_output, duty_min, duty_max))

        duty[index] = heater_duty
        error[index] = current_error
        previous_output = pid_output
        previous_error = current_error
        duty_buffer[buffer_index] = heater_duty
        buffer_index = (buffer_index + 1) % duty_buffer.size

    return Simulation(time, temperature, duty, error)


def score_simulation(
    simulation: Simulation,
    target: float,
    settling_band: float,
) -> tuple[float, float, float, float]:
    temperature = simulation.temperature
    time = simulation.time
    overshoot = max(0.0, float(np.max(temperature) - target))
    reached = np.flatnonzero(temperature >= target)
    first_reach_time = (
        float(time[reached[0]]) if reached.size else float("inf")
    )

    sample_dt = time[1] - time[0]
    slope = np.empty_like(temperature)
    slope[0] = float("inf")
    slope[1:] = np.diff(temperature) / sample_dt
    stable = (
        (np.abs(temperature - target) <= settling_band)
        & (np.abs(slope) <= STABLE_SLOPE_LIMIT)
    )
    stable_count = 0
    settling_time = float(time[-1])
    for index, is_stable in enumerate(stable):
        stable_count = stable_count + 1 if is_stable else 0
        if stable_count >= STABLE_REQUIRED_SAMPLES:
            settling_time = float(time[index - STABLE_REQUIRED_SAMPLES + 1])
            break

    steady_window = max(1, int(round(10.0 / sample_dt)))
    steady_error = float(
        np.mean(np.abs(temperature[-steady_window:] - target))
    )
    return overshoot, first_reach_time, settling_time, steady_error


def make_score(
    kp: float,
    ki: float,
    simulation: Simulation,
    target: float,
    settling_band: float,
    duty_min: float,
    duty_max: float,
) -> Score:
    overshoot, first_reach_time, settling_time, steady_error = score_simulation(
        simulation,
        target,
        settling_band,
    )
    saturation_ratio = float(
        np.mean(
            (simulation.duty <= duty_min + 1e-6)
            | (simulation.duty >= duty_max - 1e-6)
        )
    )
    # 先强制优化 5 秒目标，再比较超调、稳态误差和占空比饱和。
    return Score(
        kp=kp,
        ki=ki,
        overshoot=overshoot,
        first_reach_time=first_reach_time,
        settling_time=settling_time,
        steady_error=steady_error,
        saturation_ratio=saturation_ratio,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Tune IMU heater PI parameters")
    parser.add_argument("--target", type=float, default=TARGET_TEMPERATURE)
    parser.add_argument("--initial-temperature", type=float, default=INITIAL_TEMPERATURE)
    parser.add_argument("--ambient-temperature", type=float, default=AMBIENT_TEMPERATURE)
    parser.add_argument("--plant-gain", type=float, default=PLANT_GAIN)
    parser.add_argument("--tau", type=float, default=PLANT_TAU)
    parser.add_argument("--delay", type=float, default=PLANT_DELAY)
    parser.add_argument("--kp", type=float, default=PI_KP)
    parser.add_argument("--ki", type=float, default=PI_KI)
    parser.add_argument("--kd", type=float, default=PI_KD)
    parser.add_argument("--dt", type=float, default=SIMULATION_DT)
    parser.add_argument("--duration", type=float, default=SIMULATION_DURATION)
    parser.add_argument("--duty-min", type=float, default=DUTY_MIN)
    parser.add_argument("--duty-max", type=float, default=DUTY_MAX)
    parser.add_argument("--settling-band", type=float, default=SETTLING_BAND)
    parser.add_argument(
        "--settling-deadline",
        type=float,
        default=SETTLING_DEADLINE,
        help="要求进入并保持误差带的最长时间，单位 s",
    )
    parser.add_argument("--no-plot", action="store_true", help="disable automatic plot")
    args = parser.parse_args()

    simulation = simulate_pi(
        args.kp,
        args.ki,
        args.kd,
        target=args.target,
        initial_temperature=args.initial_temperature,
        ambient_temperature=args.ambient_temperature,
        plant_gain=args.plant_gain,
        tau=args.tau,
        delay=args.delay,
        dt=args.dt,
        duration=args.duration,
        duty_min=args.duty_min,
        duty_max=args.duty_max,
    )
    result = make_score(
        args.kp,
        args.ki,
        simulation,
        args.target,
        args.settling_band,
        args.duty_min,
        args.duty_max,
    )
    print("Plant model:")
    print(
        f"  G(s) = {args.plant_gain:.3f} / ({args.tau:.3f}*s + 1) "
        f"* exp(-{args.delay:.3f}*s)"
    )
    print("PID simulation:")
    print(f"  kp = {args.kp:.8f}")
    print(f"  ki = {args.ki:.8f}")
    print(f"  kd = {args.kd:.8f}")
    print(f"  overshoot      : {result.overshoot:.3f} C")
    print(f"  first reach    : {result.first_reach_time:.3f} s")
    print(f"  settling time  : {result.settling_time:.3f} s")
    print(f"  steady error   : {result.steady_error:.3f} C")
    print(f"  saturation     : {result.saturation_ratio * 100.0:.1f}%")
    if False:
        print(
            "  warning        : 当前 PID 未在限定时间内满足稳定条件；"
            "这不等于无法在限定时间内首次达到目标。"
        )

    if not args.no_plot:
        import matplotlib.pyplot as plt

        plt.figure(figsize=(10, 6))
        plt.subplot(2, 1, 1)
        plt.plot(
            simulation.time,
            simulation.temperature,
            label="temperature",
        )
        plt.axhline(args.target, linestyle="--", color="gray", label="target")
        plt.ylabel("Temperature (C)")
        plt.grid(True, alpha=0.3)
        plt.legend()

        plt.subplot(2, 1, 2)
        plt.plot(simulation.time, simulation.duty, label="duty")
        plt.ylabel("Duty")
        plt.xlabel("Time (s)")
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.tight_layout()
        plt.show()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
