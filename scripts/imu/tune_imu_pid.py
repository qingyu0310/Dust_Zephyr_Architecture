"""
IMU heater PID simulation aligned to the current MCU control path.

Plant model:
    G(s) = K / (tau*s + 1) * exp(-delay*s)

Controller path:
    modules/imu/drivers/heater.cpp
    algorithm/controller/pid/pid.cpp

This script keeps the plant open-loop, but makes the PID behavior match the
firmware as closely as possible:
    - heater_.Update() cadence uses CONTROL_UPDATE_DT
    - PID internal integration/derivative uses PID_INTERNAL_DT
    - anti-windup / sign-flip reset follows pid.cpp
    - output is clamped by outMax, then heater duty is clamped again
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass

import numpy as np


# ==================== 默认参数，可直接改 ====================
# 开环热对象模型：G(s) = PLANT_GAIN / (PLANT_TAU*s + 1) * exp(-PLANT_DELAY*s)
PLANT_GAIN = 46.256014
PLANT_TAU = 11.460969
PLANT_DELAY = 1.700000

# 仿真初始条件
TARGET_TEMPERATURE = 40.0
INITIAL_TEMPERATURE = 35.0
AMBIENT_TEMPERATURE = 35.823

# 占空比限制，和 heater.cpp 保持一致
DUTY_MIN = 0.001
DUTY_MAX = 0.95

# 当前固件 PID 参数，和 modules/imu/drivers/heater.cpp 保持一致
PI_KP = 0.22
PI_KI = 0.05
PI_KD = 0.005
PID_IOUT_MAX = 0.5
PID_OUT_MAX = DUTY_MAX

# 时间设置
CONTROL_UPDATE_DT = 0.0035
PID_INTERNAL_DT = 0.001
SIMULATION_DURATION = 100.0

# 性能评估
SETTLING_BAND = 0.5
SETTLING_DEADLINE = 5.0
STABLE_SLOPE_LIMIT = 0.02
STABLE_REQUIRED_SAMPLES = 500

# 可选：把温度量化成真实日志那种离散步进。0 表示关闭。
TEMPERATURE_QUANTUM = 0.0
# ==========================================================


@dataclass
class Simulation:
    time: np.ndarray
    temperature: np.ndarray
    duty: np.ndarray
    applied_duty: np.ndarray
    error: np.ndarray
    p_term: np.ndarray
    i_term: np.ndarray
    d_term: np.ndarray


@dataclass
class Score:
    overshoot: float
    first_reach_time: float
    settling_time: float
    steady_error: float
    saturation_ratio: float


def maybe_quantize_temperature(value: float, quantum: float) -> float:
    if quantum <= 0.0:
        return value
    return round(value / quantum) * quantum


def simulate_pid(
    *,
    kp: float,
    ki: float,
    kd: float,
    target: float,
    initial_temperature: float,
    ambient_temperature: float,
    plant_gain: float,
    tau: float,
    delay: float,
    control_dt: float,
    pid_dt: float,
    duration: float,
    duty_min: float,
    duty_max: float,
    pid_iout_max: float,
    pid_out_max: float,
    temperature_quantum: float,
) -> Simulation:
    if control_dt <= 0.0 or pid_dt <= 0.0:
        raise ValueError("control_dt and pid_dt must be positive")
    if tau <= 0.0:
        raise ValueError("tau must be positive")
    if duty_max < duty_min:
        raise ValueError("duty_max must be >= duty_min")

    sample_count = int(round(duration / control_dt)) + 1
    time = np.arange(sample_count, dtype=float) * control_dt

    temperature = np.empty(sample_count, dtype=float)
    duty = np.empty(sample_count, dtype=float)
    applied_duty = np.empty(sample_count, dtype=float)
    error = np.empty(sample_count, dtype=float)
    p_term = np.empty(sample_count, dtype=float)
    i_term = np.empty(sample_count, dtype=float)
    d_term = np.empty(sample_count, dtype=float)

    temperature[0] = initial_temperature

    delay_steps = max(0, int(round(delay / control_dt)))
    delay_queue: deque[float] = deque([duty_min] * delay_steps)

    integral_error = 0.0
    previous_error = 0.0
    previous_out = 0.0
    alpha = float(np.exp(-control_dt / tau))

    for index in range(sample_count):
        current_temperature = temperature[index]
        current_error = target - current_temperature
        abs_error = abs(current_error)

        # 对齐 pid.cpp：先按 iOutMax 对积分误差做一次限幅。
        if pid_iout_max != 0.0 and ki != 0.0:
            i_clamp = pid_iout_max / ki
            integral_error = float(np.clip(integral_error, -i_clamp, i_clamp))

        # 对齐 pid.cpp：上一拍输出饱和，或者误差翻向时，积分清零。
        if (
            (pid_out_max != 0.0 and abs(previous_out) >= pid_out_max)
            or (previous_error > 0.0 and current_error < 0.0)
            or (previous_error < 0.0 and current_error > 0.0)
        ):
            integral_error = 0.0

        # 当前配置下没有 deadZone / variable integral / integral separation，
        # 所以这里就是标准积分，但时间基准必须是 PID_INTERNAL_DT。
        integral_error += pid_dt * current_error

        p_out = kp * current_error
        i_out = ki * integral_error
        d_out = kd * (current_error - previous_error) / pid_dt

        pid_out = p_out + i_out + d_out
        if pid_out_max != 0.0:
            pid_out = float(np.clip(pid_out, -pid_out_max, pid_out_max))

        heater_duty = float(np.clip(pid_out, duty_min, duty_max))
        duty[index] = heater_duty
        error[index] = current_error
        p_term[index] = p_out
        i_term[index] = i_out
        d_term[index] = d_out

        if delay_steps == 0:
            delayed_duty = heater_duty
        else:
            delayed_duty = delay_queue.popleft()
            delay_queue.append(heater_duty)
        applied_duty[index] = delayed_duty

        previous_out = pid_out
        previous_error = current_error

        if index + 1 < sample_count:
            equilibrium = ambient_temperature + plant_gain * delayed_duty
            next_temperature = alpha * current_temperature + (1.0 - alpha) * equilibrium
            temperature[index + 1] = maybe_quantize_temperature(
                next_temperature,
                temperature_quantum,
            )

    return Simulation(
        time=time,
        temperature=temperature,
        duty=duty,
        applied_duty=applied_duty,
        error=error,
        p_term=p_term,
        i_term=i_term,
        d_term=d_term,
    )


def score_simulation(
    simulation: Simulation,
    *,
    target: float,
    settling_band: float,
    duty_min: float,
    duty_max: float,
) -> Score:
    temperature = simulation.temperature
    time = simulation.time

    overshoot = max(0.0, float(np.max(temperature) - target))
    reached = np.flatnonzero(temperature >= target)
    first_reach_time = float(time[reached[0]]) if reached.size else float("inf")

    sample_dt = float(time[1] - time[0])
    slope = np.empty_like(temperature)
    slope[0] = 0.0
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
    steady_error = float(np.mean(np.abs(temperature[-steady_window:] - target)))

    saturation_ratio = float(
        np.mean((simulation.duty <= duty_min + 1e-9) | (simulation.duty >= duty_max - 1e-9))
    )

    return Score(
        overshoot=overshoot,
        first_reach_time=first_reach_time,
        settling_time=settling_time,
        steady_error=steady_error,
        saturation_ratio=saturation_ratio,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="IMU heater PID simulation aligned to MCU code")
    parser.add_argument("--target", type=float, default=TARGET_TEMPERATURE)
    parser.add_argument("--initial-temperature", type=float, default=INITIAL_TEMPERATURE)
    parser.add_argument("--ambient-temperature", type=float, default=AMBIENT_TEMPERATURE)
    parser.add_argument("--plant-gain", type=float, default=PLANT_GAIN)
    parser.add_argument("--tau", type=float, default=PLANT_TAU)
    parser.add_argument("--delay", type=float, default=PLANT_DELAY)
    parser.add_argument("--kp", type=float, default=PI_KP)
    parser.add_argument("--ki", type=float, default=PI_KI)
    parser.add_argument("--kd", type=float, default=PI_KD)
    parser.add_argument("--pid-iout-max", type=float, default=PID_IOUT_MAX)
    parser.add_argument("--pid-out-max", type=float, default=PID_OUT_MAX)
    parser.add_argument("--control-dt", type=float, default=CONTROL_UPDATE_DT)
    parser.add_argument("--pid-dt", type=float, default=PID_INTERNAL_DT)
    parser.add_argument("--duration", type=float, default=SIMULATION_DURATION)
    parser.add_argument("--duty-min", type=float, default=DUTY_MIN)
    parser.add_argument("--duty-max", type=float, default=DUTY_MAX)
    parser.add_argument("--settling-band", type=float, default=SETTLING_BAND)
    parser.add_argument("--settling-deadline", type=float, default=SETTLING_DEADLINE)
    parser.add_argument("--temperature-quantum", type=float, default=TEMPERATURE_QUANTUM)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    simulation = simulate_pid(
        kp=args.kp,
        ki=args.ki,
        kd=args.kd,
        target=args.target,
        initial_temperature=args.initial_temperature,
        ambient_temperature=args.ambient_temperature,
        plant_gain=args.plant_gain,
        tau=args.tau,
        delay=args.delay,
        control_dt=args.control_dt,
        pid_dt=args.pid_dt,
        duration=args.duration,
        duty_min=args.duty_min,
        duty_max=args.duty_max,
        pid_iout_max=args.pid_iout_max,
        pid_out_max=args.pid_out_max,
        temperature_quantum=args.temperature_quantum,
    )

    score = score_simulation(
        simulation,
        target=args.target,
        settling_band=args.settling_band,
        duty_min=args.duty_min,
        duty_max=args.duty_max,
    )

    delay_steps = max(0, int(round(args.delay / args.control_dt)))

    print("Plant model:")
    print(f"  G(s) = {args.plant_gain:.6f} / ({args.tau:.6f}*s + 1) * exp(-{args.delay:.6f}*s)")
    print("Controller alignment:")
    print(f"  heater.Update dt : {args.control_dt:.6f} s")
    print(f"  PID internal dt  : {args.pid_dt:.6f} s")
    print(f"  delay steps      : {delay_steps}")
    print(f"  duty clamp       : [{args.duty_min:.6f}, {args.duty_max:.6f}]")
    print(f"  pid out max      : {args.pid_out_max:.6f}")
    print(f"  pid iOut max     : {args.pid_iout_max:.6f}")
    print("PID parameters:")
    print(f"  kp = {args.kp:.8f}")
    print(f"  ki = {args.ki:.8f}")
    print(f"  kd = {args.kd:.8f}")
    print("System performance:")
    print(f"  overshoot      : {score.overshoot:.3f} C")
    print(f"  first reach    : {score.first_reach_time:.3f} s")
    print(f"  settling time  : {score.settling_time:.3f} s")
    print(f"  steady error   : {score.steady_error:.3f} C")
    print(f"  saturation     : {score.saturation_ratio * 100.0:.1f}%")
    if score.settling_time > args.settling_deadline:
        print(
            f"  deadline       : {args.settling_deadline:.3f}s, not settled within deadline"
        )

    if not args.no_plot:
        import matplotlib.pyplot as plt

        figure, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)

        axes[0].plot(simulation.time, simulation.temperature, label="temperature")
        axes[0].axhline(args.target, linestyle="--", color="gray", label="target")
        axes[0].set_ylabel("Temperature (C)")
        axes[0].grid(True, alpha=0.3)
        axes[0].legend()

        axes[1].plot(simulation.time, simulation.duty, label="pid duty")
        axes[1].plot(simulation.time, simulation.applied_duty, linestyle="--", label="delayed duty")
        axes[1].set_ylabel("Duty")
        axes[1].set_xlabel("Time (s)")
        axes[1].grid(True, alpha=0.3)
        axes[1].legend()

        figure.tight_layout()
        plt.show()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
