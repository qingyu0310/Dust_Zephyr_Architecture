"""
tune_imu_pi.py - IMU heater PI parameter sweep

The default plant is the identified local model:
    G(s) = 41.797 / (10.408*s + 1) * exp(-1.793*s)

The controller simulation follows algorithm/controller/pid/pid.cpp.

Examples:
    python scripts/tune_imu_pi.py
    python scripts/tune_imu_pi.py --no-plot
    python scripts/tune_imu_pi.py --kp-min 0.005 --kp-max 0.08 --ki-min 0.0001 --ki-max 0.01
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np


# ==================== 可直接修改的默认参数 ====================
# 热对象模型：G(s) = PLANT_GAIN / (PLANT_TAU*s + 1) * exp(-PLANT_DELAY*s)
PLANT_GAIN              = 41.797
PLANT_TAU               = 10.408
PLANT_DELAY             = 1.793

# 仿真初始条件
TARGET_TEMPERATURE      = 40.0
INITIAL_TEMPERATURE     = 35.0
AMBIENT_TEMPERATURE     = 35.4

# PWM 占空比限制
DUTY_MIN                = 0.001
DUTY_MAX                = 0.90

# 当前固件 PI 参数
PI_KP                   = 0.13
PI_KI                   = 0.03

# 仿真设置
SIMULATION_DT           = 0.001
SIMULATION_DURATION     = 100.0
SETTLING_BAND           = 0.2
SETTLING_DEADLINE       = 5.0

# PI 搜索范围
KP_MIN                  = 0.001
KP_MAX                  = 0.50
KI_MIN                  = 0.0001
KI_MAX                  = 0.10
GRID_SIZE               = 12

# 异常检测权重
OVERSHOOT_WEIGHT        = 10.0
STEADY_ERROR_WEIGHT     = 100.0
SETTLING_TIME_WEIGHT    = 0.05
SATURATION_WEIGHT       = 0.5
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
    deadline_met: bool
    score: float


def simulate_pi(
    kp: float,
    ki: float,
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

        # Match Pid::CalcImpl() for the current firmware configuration:
        # dead zone, variable-speed integration, integral separation, D,
        # and feed-forward are all disabled, but anti-windup is retained.
        p_out = kp * current_error
        integral_limit = 0.5 / ki if ki != 0.0 else float("inf")
        integral_error = float(
            np.clip(integral_error, -integral_limit, integral_limit)
        )

        if (
            abs(previous_output) >= 0.90
            or (previous_error > 0.0 and current_error < 0.0)
            or (previous_error < 0.0 and current_error > 0.0)
        ):
            integral_error = 0.0

        integral_error += dt * current_error
        i_out = ki * integral_error
        pid_output = float(np.clip(p_out + i_out, -0.90, 0.90))
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

    inside_band = np.abs(temperature - target) <= settling_band
    outside_band = np.flatnonzero(~inside_band)
    if outside_band.size == 0:
        settling_time = 0.0
    elif outside_band[-1] >= len(time) - 1:
        settling_time = float(time[-1])
    else:
        settling_time = float(time[outside_band[-1] + 1])

    steady_window = max(1, int(round(10.0 / (time[1] - time[0]))))
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
    settling_deadline: float,
) -> Score:
    overshoot, first_reach_time, settling_time, steady_error = score_simulation(
        simulation,
        target,
        settling_band,
    )
    duty_range = simulation.duty.max() - simulation.duty.min()
    saturation_ratio = float(
        np.mean(
            (simulation.duty <= duty_min + 1e-6)
            | (simulation.duty >= duty_max - 1e-6)
        )
    )
    deadline_met = settling_time <= settling_deadline

    # 先强制优化 5 秒目标，再比较超调、稳态误差和占空比饱和。
    deadline_penalty = 1000.0 * max(0.0, settling_time - settling_deadline)
    score = (
        deadline_penalty
        + STEADY_ERROR_WEIGHT * steady_error
        + OVERSHOOT_WEIGHT * overshoot
        + SETTLING_TIME_WEIGHT * settling_time
        + SATURATION_WEIGHT * saturation_ratio
        + 0.01 * duty_range
    )
    return Score(
        kp=kp,
        ki=ki,
        overshoot=overshoot,
        first_reach_time=first_reach_time,
        settling_time=settling_time,
        steady_error=steady_error,
        saturation_ratio=saturation_ratio,
        deadline_met=deadline_met,
        score=score,
    )


def build_grid(lower: float, upper: float, count: int) -> np.ndarray:
    if lower <= 0.0 or upper <= lower:
        raise ValueError("grid bounds must satisfy 0 < lower < upper")
    return np.geomspace(lower, upper, count)


def sweep_pi(
    kp_values: np.ndarray,
    ki_values: np.ndarray,
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
    settling_band: float,
    settling_deadline: float,
) -> list[Score]:
    results: list[Score] = []
    for kp in kp_values:
        for ki in ki_values:
            simulation = simulate_pi(
                float(kp),
                float(ki),
                target=target,
                initial_temperature=initial_temperature,
                ambient_temperature=ambient_temperature,
                plant_gain=plant_gain,
                tau=tau,
                delay=delay,
                dt=dt,
                duration=duration,
                duty_min=duty_min,
                duty_max=duty_max,
            )
            results.append(
                make_score(
                    float(kp),
                    float(ki),
                    simulation,
                    target,
                    settling_band,
                    duty_min,
                    duty_max,
                    settling_deadline,
                )
            )
    return sorted(results, key=lambda item: item.score)


def main() -> int:
    parser = argparse.ArgumentParser(description="Tune IMU heater PI parameters")
    parser.add_argument("--target", type=float, default=TARGET_TEMPERATURE)
    parser.add_argument("--initial-temperature", type=float, default=INITIAL_TEMPERATURE)
    parser.add_argument("--ambient-temperature", type=float, default=AMBIENT_TEMPERATURE)
    parser.add_argument("--plant-gain", type=float, default=PLANT_GAIN)
    parser.add_argument("--tau", type=float, default=PLANT_TAU)
    parser.add_argument("--delay", type=float, default=PLANT_DELAY)
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
    parser.add_argument("--kp-min", type=float, default=KP_MIN)
    parser.add_argument("--kp-max", type=float, default=KP_MAX)
    parser.add_argument("--ki-min", type=float, default=KI_MIN)
    parser.add_argument("--ki-max", type=float, default=KI_MAX)
    parser.add_argument("--grid-size", type=int, default=GRID_SIZE)
    parser.add_argument("--no-plot", action="store_true", help="disable automatic plot")
    args = parser.parse_args()

    simulation = simulate_pi(
        PI_KP,
        PI_KI,
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
        PI_KP,
        PI_KI,
        simulation,
        args.target,
        args.settling_band,
        args.duty_min,
        args.duty_max,
        args.settling_deadline,
    )

    kp_values = build_grid(args.kp_min, args.kp_max, args.grid_size)
    ki_values = build_grid(args.ki_min, args.ki_max, args.grid_size)
    sweep_results = sweep_pi(
        kp_values,
        ki_values,
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
        settling_band=args.settling_band,
        settling_deadline=args.settling_deadline,
    )

    print("Plant model:")
    print(
        f"  G(s) = {args.plant_gain:.3f} / ({args.tau:.3f}*s + 1) "
        f"* exp(-{args.delay:.3f}*s)"
    )
    print("PI simulation:")
    print(f"  kp = {result.kp:.8f}")
    print(f"  ki = {result.ki:.8f}")
    print(f"  overshoot      : {result.overshoot:.3f} C")
    print(f"  first reach    : {result.first_reach_time:.3f} s")
    print(f"  settling time  : {result.settling_time:.3f} s")
    print(f"  steady error   : {result.steady_error:.3f} C")
    print(f"  saturation     : {result.saturation_ratio * 100.0:.1f}%")
    print("PI sweep top 5:")
    for rank, candidate in enumerate(sweep_results[:5], start=1):
        print(
            f"  {rank}: kp={candidate.kp:.8f}, ki={candidate.ki:.8f}, "
            f"score={candidate.score:.4f}, settle={candidate.settling_time:.3f}s, "
            f"steady={candidate.steady_error:.3f}C, "
            f"overshoot={candidate.overshoot:.3f}C"
        )
    print(
        f"  deadline       : {args.settling_deadline:.3f}s, "
        f"{'PASS' if result.deadline_met else 'NOT ACHIEVABLE'}"
    )
    if not result.deadline_met:
        print(
            "  warning        : 当前 PI 未在限定时间内满足稳定条件；"
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
