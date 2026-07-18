"""Read IMU temp/duty logs, fit plant model G(s) = K / (tau*s + 1) * exp(-delay*s)."""

from __future__ import annotations

import csv
import re
import time
from pathlib import Path

import numpy as np


# 直接修改这里的配置，运行脚本时不需要填写参数。
SERIAL_PORT = "COM21"
SERIAL_BAUD = 921600
LOG_FILE: str | None = None  # 例如 r"D:\logs\imu_ctrl.log"；None 表示读取串口
OUTPUT_CSV: str | None = "imu_ctrl_capture.csv"

# 拟合配置
AMBIENT_TEMPERATURE: float | None = None  # None = 自动取第一条温度值
# 参考模型参数（tune_imu_pi.py 中的值，用于对比）
REF_K = 41.797
REF_TAU = 10.408
REF_DELAY = 1.793

# 已知的 PI 参数和植物约束（与固件一致）
PI_KP = 0.13
PI_KI = 0.03
PI_DT = 0.001
PI_IOUT_MAX = 0.5
TARGET_TEMPERATURE = 40.0
DUTY_MIN = 0.001
DUTY_MAX = 0.90


LOG_RE = re.compile(
    r"temp=(?P<temp>[-+0-9.eE]+),duty=(?P<duty>[-+0-9.eE]+)"
)
STRUCTURED_LOG_RE = re.compile(
    r"heater_id,.*?temp=(?P<temp>[-+0-9.eE]+),"
    r"duty=(?P<duty>[-+0-9.eE]+),dt=(?P<dt>[-+0-9.eE]+)"
)
FIELD_RE = re.compile(
    r"(?:(?<=,)|^)(?P<name>seq|stage|t_ms|dt)="
    r"(?P<value>[-+0-9.eE]+)(?=,|$)"
)
TIMESTAMP_RE = re.compile(
    r"\[(?P<h>\d+):(?P<m>\d+):(?P<s>\d+)\.(?P<ms>\d+),\d+\]"
)


def parse_lines(lines: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    times: list[float] = []
    temperatures: list[float] = []
    duties: list[float] = []
    first_timestamp: float | None = None
    fallback_time = 0.0
    previous_time = -1.0

    for line in lines:
        if "heater_id," in line:
            continue
        structured = STRUCTURED_LOG_RE.search(line)
        match = structured if structured is not None else LOG_RE.search(line)
        if match is None:
            continue

        fields = {
            item.group("name"): float(item.group("value"))
            for item in FIELD_RE.finditer(line)
        }
        if "t_ms" in fields:
            if first_timestamp is None:
                first_timestamp = fields["t_ms"] / 1000.0
            current_timestamp = fields["t_ms"] / 1000.0
            # t_ms 是 uint32_t 单调时钟，允许一次回绕。
            if current_timestamp < first_timestamp:
                current_timestamp += 4294967.296
            time_s = current_timestamp - first_timestamp
            fallback_time = time_s
        else:
            timestamp = TIMESTAMP_RE.search(line)
            if timestamp is not None:
                current_timestamp = (
                    int(timestamp.group("h")) * 3600.0
                    + int(timestamp.group("m")) * 60.0
                    + int(timestamp.group("s"))
                    + int(timestamp.group("ms")) / 1000.0
                )
                if first_timestamp is None:
                    first_timestamp = current_timestamp
                time_s = current_timestamp - first_timestamp
                fallback_time = time_s
            else:
                fallback_time += float(fields.get("dt", 0.001))
                time_s = fallback_time

        if time_s <= previous_time:
            raise ValueError(
                f"non-increasing sample time at {time_s:.6f}s; "
                "check MCU t_ms, log drops, or duplicate samples"
            )
        previous_time = time_s

        times.append(time_s)
        temperatures.append(float(match.group("temp")))
        duties.append(float(match.group("duty")))

    if not times:
        raise ValueError("no imu_ctrl samples found")

    return (
        np.asarray(times, dtype=float),
        np.asarray(temperatures, dtype=float),
        np.asarray(duties, dtype=float),
    )


def print_summary(time: np.ndarray, temperature: np.ndarray, duty: np.ndarray) -> None:
    target = 40.0
    reached = np.flatnonzero(temperature >= target)
    sample_dt = np.diff(time)
    print(f"samples       : {time.size}")
    print(f"duration      : {time[-1]:.3f} s")
    print(f"temperature   : {temperature.min():.3f} .. {temperature.max():.3f} C")
    print(f"duty          : {duty.min():.6f} .. {duty.max():.6f}")
    if sample_dt.size:
        print(
            f"sample dt     : median={np.median(sample_dt):.6f} s, "
            f"p95={np.percentile(sample_dt, 95):.6f} s, "
            f"max={sample_dt.max():.6f} s"
        )
    if reached.size:
        print(f"first >= 40 C : {time[reached[0]]:.3f} s")
    else:
        print("first >= 40 C : never")


def _legacy_parse_lines(lines: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """保留旧解析函数名的兼容入口，实际解析统一走结构化版本。"""
    return parse_lines(lines)


def parse_csv_file(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """读取 write_samples_csv() 生成的普通闭环数据。"""
    times: list[float] = []
    temperatures: list[float] = []
    duties: list[float] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise ValueError("empty csv")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        required = {"time_s", "temp_c", "duty"}
        if not required.issubset(fields):
            raise ValueError("csv needs time_s,temp_c,duty columns")
        for row in reader:
            times.append(float(row[fields["time_s"]]))
            temperatures.append(float(row[fields["temp_c"]]))
            duties.append(float(row[fields["duty"]]))

    if not times:
        raise ValueError("no csv rows found")
    return (
        np.asarray(times, dtype=float),
        np.asarray(temperatures, dtype=float),
        np.asarray(duties, dtype=float),
    )


def write_samples_csv(
    time_s: np.ndarray,
    temperature: np.ndarray,
    duty: np.ndarray,
    path: Path,
) -> None:
    """保存普通温控日志，保留输入回放所需的原始时间轴。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        file.write("sample,time_s,temp_c,duty,dt_s\n")
        for index, (current_time, temp, current_duty) in enumerate(
            zip(time_s, temperature, duty)
        ):
            sample_dt = 0.0 if index == 0 else current_time - time_s[index - 1]
            file.write(
                f"{index},{current_time:.6f},{temp:.6f},"
                f"{current_duty:.6f},{sample_dt:.6f}\n"
            )
    print(f"Saved samples: {path}")


COOLDOWN_TEMP = 35.0
RISE_TEMP = 35.0


def read_serial(port: str, baud: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("serial mode requires pyserial") from exc

    print(f"\n[采集] Reading {port} @ {baud} baud")
    times: list[float] = []
    temperatures: list[float] = []
    duties: list[float] = []
    first_timestamp: float | None = None
    fallback_time = 0.0
    STEADY_WINDOW = 5000
    steady_count = 0
    steady_lower = TARGET_TEMPERATURE - 0.5
    steady_upper = TARGET_TEMPERATURE + 0.5

    try:
        with serial.Serial(port, baudrate=baud, timeout=0.5) as device:
            state = "cooldown"
            print(f"[采集] 等待降温至 {COOLDOWN_TEMP}C 以下 (当前 ?)")
            while state == "cooldown":
                raw = device.readline()
                if not raw:
                    continue
                line = raw.decode(errors="replace").rstrip("\r\n")
                if "heater_id," in line:
                    continue
                match = LOG_RE.search(line)
                if match is None:
                    continue
                temp = float(match.group("temp"))
                print(f"\r[采集] 等待降温... {temp:.2f}C", end="")
                if temp <= COOLDOWN_TEMP:
                    print(f"\n[采集] 已降温至 {temp:.2f}C，等待升温至 {RISE_TEMP}C")
                    time.sleep(2)
                    state = "warmup"

            while state == "warmup":
                raw = device.readline()
                if not raw:
                    continue
                line = raw.decode(errors="replace").rstrip("\r\n")
                if "heater_id," in line:
                    continue
                match = LOG_RE.search(line)
                if match is None:
                    continue
                temp = float(match.group("temp"))
                print(f"\r[采集] 等待升温... {temp:.2f}C", end="")
                if temp >= RISE_TEMP:
                    fields = {
                        item.group("name"): float(item.group("value"))
                        for item in FIELD_RE.finditer(line)
                    }
                    if "t_ms" in fields:
                        first_timestamp = fields["t_ms"] / 1000.0
                    else:
                        timestamp = TIMESTAMP_RE.search(line)
                        first_timestamp = (
                            int(timestamp.group("h")) * 3600.0
                            + int(timestamp.group("m")) * 60.0
                            + int(timestamp.group("s"))
                            + int(timestamp.group("ms")) / 1000.0
                            if timestamp is not None
                            else None
                        )
                    time_s = 0.0
                    fallback_time = 0.0
                    times.append(time_s)
                    temperatures.append(temp)
                    duties.append(float(match.group("duty")))
                    print(f"\n[采集] 开始采集（从 {RISE_TEMP}C 开始）")
                    state = "collect"
                    break

            while state == "collect":
                raw = device.readline()
                if not raw:
                    continue
                line = raw.decode(errors="replace").rstrip("\r\n")
                if "heater_id," in line:
                    continue
                match = LOG_RE.search(line)
                if match is None:
                    continue

                fields = {
                    item.group("name"): float(item.group("value"))
                    for item in FIELD_RE.finditer(line)
                }
                if "t_ms" in fields:
                    current = fields["t_ms"] / 1000.0
                    if first_timestamp is None:
                        first_timestamp = current
                    if current < first_timestamp:
                        current += 4294967.296
                    time_s = current - first_timestamp
                    fallback_time = time_s
                else:
                    timestamp = TIMESTAMP_RE.search(line)
                    if timestamp is not None:
                        current = (int(timestamp.group("h")) * 3600.0
                                   + int(timestamp.group("m")) * 60.0
                                   + int(timestamp.group("s"))
                                   + int(timestamp.group("ms")) / 1000.0)
                        if first_timestamp is None:
                            first_timestamp = current
                        time_s = current - first_timestamp
                        fallback_time = time_s
                    else:
                        fallback_time += float(fields.get("dt", 0.001))
                        time_s = fallback_time

                times.append(time_s)
                temperatures.append(float(match.group("temp")))
                duties.append(float(match.group("duty")))

                t = temperatures[-1]
                if steady_lower <= t <= steady_upper:
                    steady_count += 1
                else:
                    steady_count = 0

                if len(times) % 500 == 0:
                    print(f"  collected {len(times)} samples, {t:.2f}C", end="\r")

                if steady_count >= STEADY_WINDOW:
                    print(f"\n[采集] 稳定在 {t:.2f}C，停止 ({len(times)} samples)")
                    break

    except KeyboardInterrupt:
        print(f"\n[采集] 手动停止，{len(times)} samples collected.")

    if not times:
        raise ValueError("no imu_ctrl samples found")
    return (
        np.asarray(times, dtype=float),
        np.asarray(temperatures, dtype=float),
        np.asarray(duties, dtype=float),
    )


# ---------------------------------------------------------------------------
# 开环拟合：用测量 duty → 仿真温度 → 优化植物 K/tau/delay
#   植物 G(s) = K / (tau * s + 1) * exp(-delay * s)
#   拟合完再根据已知 C(s) = kp + ki/s 算闭环
# ---------------------------------------------------------------------------


def _simulate_plant(
    duty: np.ndarray,
    dt: float,
    K: float,
    tau: float,
    delay: float,
    T0: float,
    ambient: float,
) -> np.ndarray:
    n = len(duty)
    temp = np.empty(n)
    temp[0] = T0
    delay_n = max(1, int(round(delay / dt)))
    buf = np.full(delay_n, duty[0])
    bi = 0
    for i in range(n):
        if i > 0:
            delayed = buf[bi]
            equil = ambient + K * delayed
            temp[i] = temp[i - 1] + (equil - temp[i - 1]) * dt / tau
        buf[bi] = duty[i]
        bi = (bi + 1) % delay_n
    return temp


def fit_plant(
    time: np.ndarray,
    temperature: np.ndarray,
    duty: np.ndarray,
    ambient: float,
) -> tuple[float, float, float, float, np.ndarray]:
    from scipy.optimize import minimize

    dt = float(np.median(np.diff(time)))
    T0 = float(temperature[0])

    def _cost(params):
        K, tau, delay = params
        if tau <= 1e-9 or delay < 0:
            return 1e12
        sim = _simulate_plant(duty, dt, K, tau, delay, T0, ambient)
        return float(np.mean((sim - temperature) ** 2))

    starts = [
        np.array([REF_K, REF_TAU, REF_DELAY]),
        np.array([REF_K * 0.3, REF_TAU * 0.3, REF_DELAY * 0.3]),
        np.array([REF_K * 3.0, REF_TAU * 3.0, REF_DELAY * 3.0]),
        np.array([REF_K * 0.5, REF_TAU * 0.1, REF_DELAY * 2.0]),
        np.array([REF_K * 2.0, REF_TAU * 2.0, REF_DELAY * 0.2]),
    ]

    best_result = None
    best_cost = float("inf")
    for i, x0 in enumerate(starts):
        tracker = {"it": 0, "cv": float("inf")}
        def _track(p):
            tracker["cv"] = _cost(p)
            tracker["it"] += 1
            if tracker["it"] % 10 == 0:
                print(f"\r  [{tracker['it']}] cost={tracker['cv']:.4f}    ",
                      end="", flush=True)
            return tracker["cv"]
        print(f"\r  start {i + 1}/{len(starts)}:", end="", flush=True)
        result = minimize(
            _track, x0,
            method="Nelder-Mead",
            options={"maxiter": 2000, "xatol": 1e-6, "fatol": 1e-8},
        )
        print(f"\r  start {i + 1}/{len(starts)}: {tracker['it']} it, cost={result.fun:.4f}")
        if result.fun < best_cost:
            best_cost = result.fun
            best_result = result

    K_opt, tau_opt, delay_opt = best_result.x
    fitted = _simulate_plant(duty, dt, K_opt, tau_opt, delay_opt, T0, ambient)
    return K_opt, tau_opt, delay_opt, best_cost, fitted


# ---------------------------------------------------------------------------


def print_fit_comparison(K, tau, delay, mse, kp, ki):
    print("\n--- Plant Model Fit ---")
    print(f"  G(s)   = {K:.3f} / ({tau:.3f}*s + 1) * exp(-{delay:.3f}*s)")
    print(f"  C(s)   = {kp} + {ki}/s")
    w2 = K * ki / tau
    wn = w2 ** 0.5 if w2 > 0 else 0.0
    zeta = (1 + K * kp) / (2 * wn) if wn > 0 else 0.0
    print(f"  T(s)~  = ({kp}*s + {ki})*{K:.3f} / ({tau:.3f}*s^2 + {1+K*kp:.3f}*s + {K*ki:.3f})")
    print(f"  MSE    = {mse:.6f}")
    print(f"  wn     = {wn:.3f} rad/s, zeta = {zeta:.3f}")
    print("\n  Compare with reference (tune_imu_pi.py):")
    print(f"    K     : fitted={K:.3f},  ref={REF_K:.3f},  "
          f"diff={K / REF_K * 100.0 - 100.0:+.1f}%")
    print(f"    tau   : fitted={tau:.3f}, ref={REF_TAU:.3f},  "
          f"diff={tau / REF_TAU * 100.0 - 100.0:+.1f}%")
    print(f"    delay : fitted={delay:.3f}, ref={REF_DELAY:.3f},  "
          f"diff={delay / REF_DELAY * 100.0 - 100.0:+.1f}%")


def main() -> int:
    if LOG_FILE is None:
        time, temperature, duty = read_serial(SERIAL_PORT, SERIAL_BAUD)
    else:
        log_file = Path(LOG_FILE)
        if log_file.suffix.lower() == ".csv":
            time, temperature, duty = parse_csv_file(log_file)
        else:
            time, temperature, duty = parse_lines(
                log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            )

    print_summary(time, temperature, duty)

    ambient = (
        AMBIENT_TEMPERATURE
        if AMBIENT_TEMPERATURE is not None
        else float(temperature[0])
    )
    if OUTPUT_CSV is not None:
        write_samples_csv(time, temperature, duty, Path(OUTPUT_CSV))
    print("[拟合] Fitting plant G(s) = K / (tau*s + 1) * exp(-delay*s)")
    print(f"[拟合] PI fixed: kp={PI_KP}, ki={PI_KI}, segment: {RISE_TEMP}C up")
    result = fit_plant(time, temperature, duty, ambient)
    K_fit, tau_fit, delay_fit, mse, fitted_temp = result
    print_fit_comparison(K_fit, tau_fit, delay_fit, mse, PI_KP, PI_KI)

    print("[绘图] Rendering...")
    import matplotlib.pyplot as plt

    fig, (ax_temp, ax_duty) = plt.subplots(2, 1, sharex=True)

    ax_temp.plot(time, temperature, label="measured", color="tab:blue")
    ax_temp.plot(time, fitted_temp, label="fitted", color="tab:red", linestyle="--")
    ax_temp.axhline(TARGET_TEMPERATURE, color="gray", linestyle=":", label="target")
    ax_temp.set_ylabel("Temperature (C)")
    ax_temp.grid(True, alpha=0.3)
    ax_temp.legend()

    ax_temp.text(
        0.02, 0.98,
        f"$K={K_fit:.2f}$, $\\tau={tau_fit:.2f}$ s, $\\tau_d={delay_fit:.3f}$ s\n"
        f"MSE={mse:.4f}",
        transform=ax_temp.transAxes,
        va="top", fontsize=9,
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.6),
    )

    ax_duty.plot(time, duty, label="duty", color="tab:orange")
    ax_duty.set_xlabel("Time (s)")
    ax_duty.set_ylabel("Duty")
    ax_duty.grid(True, alpha=0.3)
    ax_duty.legend()

    fig.tight_layout()
    print("[绘图] Close the window to exit.")
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
