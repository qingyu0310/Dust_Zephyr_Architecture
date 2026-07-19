"""Replay an open-loop IMU heater log with a fixed transfer function.

The script does not fit any parameter. It only compares:

    measured temperature
    fixed-model temperature replayed with the measured duty

Serial capture flow (same as imu_temp_identify.py):
    1. Wait for open_ident_start + imu ready boot flags
    2. Send StartIdent command
    3. Collect all samples (all states)
    4. Send Stop on Finished / Safety Stop

Expected Zephyr sample format:

    seq=1,t_us=123456,dt_us=1000,stage=0,state=4,temp_c=35.123,duty=0.200

Examples:

    python scripts/imu/imu_open_loop_replay.py run.log
    python scripts/imu/imu_open_loop_replay.py --port COM21
    python scripts/imu/imu_open_loop_replay.py run.log --gain 41.888 --tau 12.049 --delay 2.45
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np


SERIAL_PORT = "COM21"
SERIAL_BAUD = 921600

# Current open-loop model used by tune_imu_pi.py.
MODEL_GAIN          = 41.888        # 增益 (C/duty)
MODEL_TAU_S         = 12.049        # 时间常数 (s)
MODEL_DELAY_S       = 2.450         # 纯延迟 (s)

SAMPLE_RE = re.compile(
    r"seq=(?P<seq>\d+),"
    r"t_us=(?P<t_us>\d+),"
    r"dt_us=(?P<dt_us>\d+),"
    r"stage=(?P<stage>\d+),"
    r"state=(?P<state>\d+),"
    r"temp_c=(?P<temp>[-+0-9.eE]+),"
    r"duty=(?P<duty>[-+0-9.eE]+)"
)
OPEN_MODE_START_RE = re.compile(r"\bopen_ident_start\b")
IMU_READY_RE = re.compile(r"\bimu ready\b")
COOLDOWN_DONE_RE = re.compile(r"\bCooldown Done\b")
FINISH_RE = re.compile(r"\bFinished\b")
SAFETY_STOP_RE = re.compile(r"\bSafety Stop\b")
START_IDENT_CMD = b"StartIdent"
STOP_IDENT_CMD = b"Stop"


@dataclass
class OpenLoopLog:
    time_s: np.ndarray
    temperature_c: np.ndarray
    duty: np.ndarray
    stage: np.ndarray
    state: np.ndarray


def parse_log_file(path: Path) -> OpenLoopLog:
    """Parse a pre-recorded log file (offline mode)."""
    time_us: list[int] = []
    temperatures: list[float] = []
    duties: list[float] = []
    stages: list[int] = []
    states: list[int] = []
    started = False
    record = False

    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if OPEN_MODE_START_RE.search(line):
            time_us.clear()
            temperatures.clear()
            duties.clear()
            stages.clear()
            states.clear()
            started = True
            record = False
            continue

        if not started:
            continue

        if COOLDOWN_DONE_RE.search(line):
            record = True
            continue

        match = SAMPLE_RE.search(line)
        if match is None:
            continue

        if not record:
            continue

        time_us.append(int(match.group("t_us")))
        temperatures.append(float(match.group("temp")))
        duties.append(float(match.group("duty")))
        stages.append(int(match.group("stage")))
        states.append(int(match.group("state")))

    if not started:
        raise ValueError("open_ident_start was not found in the log")
    if len(time_us) < 3:
        raise ValueError("fewer than three open-loop samples were found")

    time_array = np.asarray(time_us, dtype=float) * 1.0e-6
    return OpenLoopLog(
        time_s=time_array - time_array[0],
        temperature_c=np.asarray(temperatures, dtype=float),
        duty=np.asarray(duties, dtype=float),
        stage=np.asarray(stages, dtype=int),
        state=np.asarray(states, dtype=int),
    )


def capture_serial(port: str, baud: int) -> OpenLoopLog:
    """Online capture with command flow matching imu_temp_identify.py."""
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("serial capture requires pyserial") from exc

    samples: list[tuple[int, float, float, int, int]] = []
    open_mode_seen = False
    imu_ready_seen = False
    command_sent = False
    record = False

    print(f"[{datetime.now():%H:%M:%S}] [REPLAY][READY] serial={port},baud={baud}")
    print(f"[{datetime.now():%H:%M:%S}] [REPLAY][WAITING] "
          "waiting for open_ident_start + imu ready boot flags")

    try:
        with serial.Serial(port, baudrate=baud, timeout=0.5) as ser:
            while True:
                raw = ser.readline()
                if not raw:
                    continue

                line = raw.decode(errors="replace").rstrip("\r\n")

                if OPEN_MODE_START_RE.search(line) and not open_mode_seen:
                    open_mode_seen = True
                    print(f"[{datetime.now():%H:%M:%S}] [REPLAY][BOOT] "
                          "open_ident_start received")

                if IMU_READY_RE.search(line) and not imu_ready_seen:
                    imu_ready_seen = True
                    print(f"[{datetime.now():%H:%M:%S}] [REPLAY][BOOT] "
                          "imu ready received")

                if not command_sent and open_mode_seen and imu_ready_seen:
                    ser.write(START_IDENT_CMD)
                    ser.flush()
                    command_sent = True
                    print(f"[{datetime.now():%H:%M:%S}] [REPLAY][START] "
                          "StartIdent sent")

                if not command_sent:
                    continue

                if COOLDOWN_DONE_RE.search(line):
                    record = True
                    print(f"[{datetime.now():%H:%M:%S}] [REPLAY][COOLDOWN] "
                          "Cooldown Done; start recording samples")
                    continue

                match = SAMPLE_RE.search(line)
                if match is not None and record:
                    samples.append((
                        int(match.group("t_us")),
                        float(match.group("temp")),
                        float(match.group("duty")),
                        int(match.group("stage")),
                        int(match.group("state")),
                    ))
                    if len(samples) == 1 or len(samples) % 500 == 0:
                        print(f"[{datetime.now():%H:%M:%S}] [REPLAY][CAPTURE] "
                              f"samples={len(samples)},"
                              f"stage={match.group('stage')},"
                              f"state={match.group('state')},"
                              f"temp={float(match.group('temp')):.2f}C,"
                              f"duty={float(match.group('duty')):.3f}")

                if FINISH_RE.search(line) or SAFETY_STOP_RE.search(line):
                    ser.write(STOP_IDENT_CMD)
                    ser.flush()
                    print(f"[{datetime.now():%H:%M:%S}] [REPLAY][STOP] "
                          f"Stop sent after event; samples={len(samples)}")
                    break

    except KeyboardInterrupt:
        print("\n[REPLAY][STOP] serial capture stopped")

    if len(samples) < 3:
        raise ValueError("fewer than three open-loop samples captured")

    time_us = np.asarray([s[0] for s in samples], dtype=float)
    return OpenLoopLog(
        time_s=(time_us - time_us[0]) * 1.0e-6,
        temperature_c=np.asarray([s[1] for s in samples], dtype=float),
        duty=np.asarray([s[2] for s in samples], dtype=float),
        stage=np.asarray([s[3] for s in samples], dtype=int),
        state=np.asarray([s[4] for s in samples], dtype=int),
    )


def delayed_duty(time_s: np.ndarray, duty: np.ndarray, delay_s: float) -> np.ndarray:
    if delay_s <= 0.0:
        return duty.copy()

    delayed_time = np.maximum(time_s - delay_s, time_s[0])
    indexes = np.searchsorted(time_s, delayed_time, side="right") - 1
    indexes = np.clip(indexes, 0, duty.size - 1)
    return duty[indexes]


def replay_model(
    log: OpenLoopLog,
    gain: float,
    tau_s: float,
    delay_s: float,
) -> np.ndarray:
    if gain <= 0.0 or tau_s <= 0.0 or delay_s < 0.0:
        raise ValueError("gain and tau must be positive; delay cannot be negative")

    prediction = np.empty_like(log.temperature_c)
    prediction[0] = log.temperature_c[0]
    initial_duty = log.duty[0]
    input_duty = delayed_duty(log.time_s, log.duty, delay_s)

    for index in range(1, log.time_s.size):
        dt_s = max(log.time_s[index] - log.time_s[index - 1], 1.0e-6)
        alpha = np.exp(-dt_s / tau_s)
        # 以实测初始温度和初始 duty 为基准，只回放 duty 变化造成的温升。
        equilibrium_c = (
            log.temperature_c[0]
            + gain * (input_duty[index - 1] - initial_duty)
        )
        prediction[index] = (
            alpha * prediction[index - 1]
            + (1.0 - alpha) * equilibrium_c
        )

    return prediction


def print_metrics(log: OpenLoopLog, prediction: np.ndarray) -> None:
    error = prediction - log.temperature_c
    rmse = float(np.sqrt(np.mean(error * error)))
    mae = float(np.mean(np.abs(error)))
    max_error = float(np.max(np.abs(error)))
    max_index = int(np.argmax(np.abs(error)))

    print("Open-loop model replay:")
    print(f"  samples       : {log.time_s.size}")
    print(f"  duration      : {log.time_s[-1]:.3f} s")
    print(f"  temperature   : {log.temperature_c[0]:.3f} -> "
          f"{log.temperature_c[-1]:.3f} C")
    print(f"  duty          : {log.duty.min():.3f} -> {log.duty.max():.3f}")
    print(f"  RMSE          : {rmse:.3f} C")
    print(f"  MAE           : {mae:.3f} C")
    print(f"  max error     : {max_error:.3f} C "
          f"at {log.time_s[max_index]:.3f} s")


def plot_result(log: OpenLoopLog, prediction: np.ndarray) -> None:
    import matplotlib.pyplot as plt

    error = prediction - log.temperature_c
    figure, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)

    axes[0].plot(
        log.time_s,
        log.temperature_c,
        color="tab:blue",
        linewidth=1.2,
        label="measured temperature",
    )
    axes[0].plot(
        log.time_s,
        prediction,
        color="tab:orange",
        linestyle="--",
        linewidth=1.6,
        label="fixed transfer-function replay",
    )
    axes[0].set_ylabel("Temperature (C)")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend()

    axes[1].plot(
        log.time_s,
        log.duty,
        color="tab:green",
        linewidth=1.1,
        label="measured duty",
    )
    axes[1].set_ylabel("Duty")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend()

    axes[2].plot(
        log.time_s,
        error,
        color="tab:red",
        linewidth=1.0,
        label="model error",
    )
    axes[2].axhline(0.0, color="black", linewidth=0.8)
    axes[2].axhline(0.5, color="gray", linestyle=":", linewidth=0.8)
    axes[2].axhline(-0.5, color="gray", linestyle=":", linewidth=0.8)
    axes[2].set_xlabel("Time (s)")
    axes[2].set_ylabel("Model - measured (C)")
    axes[2].grid(True, alpha=0.3)
    axes[2].legend()

    change_indexes = np.flatnonzero(np.diff(log.stage) != 0) + 1
    for idx in change_indexes:
        for axis in axes:
            axis.axvline(
                log.time_s[idx],
                color="tab:purple",
                linestyle=":",
                linewidth=0.8,
                alpha=0.65,
            )

    figure.suptitle("IMU Heater Open-Loop Transfer-Function Replay")
    figure.tight_layout()
    plt.show()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay a fixed open-loop IMU heater model; no fitting"
    )
    parser.add_argument("log_file", nargs="?", type=Path)
    parser.add_argument("--port", default=None)
    parser.add_argument("--baud", type=int, default=SERIAL_BAUD)
    parser.add_argument("--gain", type=float, default=MODEL_GAIN)
    parser.add_argument("--tau", type=float, default=MODEL_TAU_S)
    parser.add_argument("--delay", type=float, default=MODEL_DELAY_S)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    if args.log_file is not None and args.port is not None:
        parser.error("use either log_file or --port, not both")
    if args.log_file is None and args.port is None:
        args.port = SERIAL_PORT

    if args.log_file is not None:
        log = parse_log_file(args.log_file)
        source = str(args.log_file)
    else:
        log = capture_serial(args.port, args.baud)
        source = f"serial:{args.port}"

    print(
        f"[REPLAY][MODEL] source={source},"
        f"G(s)={args.gain:.3f}/({args.tau:.3f}*s+1)*"
        f"exp(-{args.delay:.3f}*s),"
        "deviation replay",
        flush=True,
    )
    prediction = replay_model(
        log,
        args.gain,
        args.tau,
        args.delay,
    )
    print_metrics(log, prediction)

    if not args.no_plot:
        plot_result(log, prediction)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
