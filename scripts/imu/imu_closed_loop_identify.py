"""Fit the IMU heater plant from normal closed-loop PID logs.

Required sample format:
    cl_ident,t_us=123456,temp_c=31.568,duty=0.950

The fitter uses the measured duty as the plant input and estimates:
    G(s) = K * exp(-L*s) / (tau*s + 1)
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SERIAL_PORT = "COM21"
SERIAL_BAUD = 921600
MAX_SAMPLES = 6000
CAPTURE_SECONDS = 120.0
MAX_CAPTURE_SAMPLES = 30000
START_TIMEOUT_SECONDS = 120.0
IDENT_START_TEMP_C = 35.0

SAMPLE_RE = re.compile(
    r"t_us=(?P<t_us>\d+).*?"
    r"temp_c=(?P<temp>[-+0-9.eE]+).*?"
    r"duty=(?P<duty>[-+0-9.eE]+)"
)
CLOSED_START_RE = re.compile(r"\bclosed_ident_start\b")
CLOSED_FINISH_RE = re.compile(r"\bclosed_ident_(?:finish|finished|stop)\b")


@dataclass
class ClosedLoopLog:
    time_s: np.ndarray
    temp_c: np.ndarray
    duty: np.ndarray


def parse_log_lines(lines: list[str]) -> ClosedLoopLog:
    time_s: list[float] = []
    temp_c: list[float] = []
    duty: list[float] = []
    first_t_us: int | None = None
    previous_t_us = -1
    started = False
    formal_started = False

    for line in lines:
        if CLOSED_START_RE.search(line):
            time_s.clear()
            temp_c.clear()
            duty.clear()
            first_t_us = None
            previous_t_us = -1
            started = True
            formal_started = False
            continue

        if not started:
            continue

        match = SAMPLE_RE.search(line)
        if match is None:
            continue

        current_temp_c = float(match.group("temp"))
        if not formal_started:
            if current_temp_c < IDENT_START_TEMP_C:
                continue
            formal_started = True

        current_t_us = int(match.group("t_us"))
        if current_t_us <= previous_t_us:
            continue
        if first_t_us is None:
            first_t_us = current_t_us

        previous_t_us = current_t_us
        time_s.append((current_t_us - first_t_us) * 1.0e-6)
        temp_c.append(float(match.group("temp")))
        duty.append(float(match.group("duty")))

    if not started:
        raise ValueError(
            "closed_ident_start was not found; this log is not a complete "
            "closed-loop identification run"
        )
    if len(time_s) < 3:
        raise ValueError(
            f"fewer than three formal samples found after "
            f"{IDENT_START_TEMP_C:.1f} C; expected "
            "cl_ident,t_us=...,temp_c=...,duty=..."
        )

    return ClosedLoopLog(
        time_s=np.asarray(time_s, dtype=float),
        temp_c=np.asarray(temp_c, dtype=float),
        duty=np.asarray(duty, dtype=float),
    )


def parse_csv(path: Path) -> ClosedLoopLog:
    time_s: list[float] = []
    temp_c: list[float] = []
    duty: list[float] = []

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise ValueError("empty CSV")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        required = {"time_s", "temp_c", "duty"}
        if not required.issubset(fields):
            raise ValueError("CSV requires time_s,temp_c,duty columns")

        for row in reader:
            time_s.append(float(row[fields["time_s"]]))
            temp_c.append(float(row[fields["temp_c"]]))
            duty.append(float(row[fields["duty"]]))

    if len(time_s) < 3:
        raise ValueError("CSV needs at least three samples")

    time_array = np.asarray(time_s, dtype=float)
    if np.any(np.diff(time_array) <= 0.0):
        raise ValueError("CSV time_s must be strictly increasing")
    return ClosedLoopLog(
        time_s=time_array - time_array[0],
        temp_c=np.asarray(temp_c, dtype=float),
        duty=np.asarray(duty, dtype=float),
    )


def _capture_serial_legacy(port: str, baud: int) -> ClosedLoopLog:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("serial capture requires pyserial") from exc

    lines: list[str] = []
    samples = 0
    ignored_samples = 0
    run_started = False
    total_lines = 0
    sample_lines = 0
    last_status = 0.0
    start_wall = 0.0
    last_temp = 0.0
    last_duty = 0.0
    print(f"[CLID][READY] serial={port},baud={baud}", flush=True)
    print(
        "[CLID][WAITING] waiting for closed_ident_start; "
        "samples before it are ignored",
        flush=True,
    )
    print(
        "[CLID][STOP] stops on closed_ident_finish/closed_ident_stop "
        "or Ctrl+C",
        flush=True,
    )

    try:
        with serial.Serial(port, baudrate=baud, timeout=0.5) as device:
            while True:
                raw = device.readline()
                if not raw:
                    now = __import__("time").monotonic()
                    if now - last_status >= 5.0:
                        elapsed = 0.0 if start_wall == 0.0 else now - start_wall
                        if not run_started:
                            print(
                                f"[CLID][HEARTBEAT][WAITING_START] "
                                f"elapsed={now - (start_wall or now):.0f}s,"
                                f"lines={total_lines},samples_seen={sample_lines}; "
                                "waiting for closed_ident_start",
                                flush=True,
                            )
                        else:
                            print(
                                f"[CLID][HEARTBEAT][NO_NEW_DATA] "
                                f"elapsed={elapsed:.0f}s,samples={samples},"
                                f"temp={last_temp:.2f}C,duty={last_duty:.3f}",
                                flush=True,
                            )
                        last_status = now
                    continue
                line = raw.decode(errors="replace").rstrip("\r\n")
                total_lines += 1

                if CLOSED_START_RE.search(line):
                    lines = [line]
                    samples = 0
                    ignored_samples = 0
                    run_started = True
                    start_wall = __import__("time").monotonic()
                    last_status = start_wall
                    print(
                        "[CLID][START] closed_ident_start received; "
                        "previous run cleared, new closed-loop capture started",
                        flush=True,
                    )
                    continue

                sample_match = SAMPLE_RE.search(line)
                if sample_match is None:
                    continue
                sample_lines += 1

                if not run_started:
                    ignored_samples += 1
                    if ignored_samples == 1:
                        print(
                            "[CLID][IGNORED] sample received before "
                            "closed_ident_start; waiting for a new run",
                            flush=True,
                        )
                    continue

                lines.append(line)
                samples += 1
                last_temp = float(sample_match.group("temp"))
                last_duty = float(sample_match.group("duty"))
                if samples == 1:
                    print("[CLID][RECEIVED] first closed-loop sample", flush=True)
                elif samples % 100 == 0:
                    print(
                        f"[CLID][RUNNING] samples={samples},"
                        f"elapsed={float(sample_match.group('t_us')) * 1.0e-6:.3f}s,"
                        f"temp={last_temp:.2f}C,duty={last_duty:.3f}",
                        flush=True,
                    )

                now = __import__("time").monotonic()
                if now - last_status >= 5.0:
                    print(
                        f"[CLID][HEARTBEAT][RECEIVING] "
                        f"elapsed={now - start_wall:.0f}s,samples={samples},"
                        f"temp={last_temp:.2f}C,duty={last_duty:.3f}",
                        flush=True,
                    )
                    last_status = now

                if CLOSED_FINISH_RE.search(line):
                    break
    except KeyboardInterrupt:
        print("\n[CLID][STOP] serial capture stopped", flush=True)

    if not run_started:
        raise ValueError(
            "closed_ident_start was not received; reset the IMU and start "
            "a new closed-loop identification run"
        )
    return parse_log_lines(lines)


def capture_serial(
    port: str,
    baud: int,
    capture_seconds: float,
    max_capture_samples: int,
    start_timeout_seconds: float,
) -> ClosedLoopLog:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("serial capture requires pyserial") from exc

    lines: list[str] = []
    run_started = False
    total_lines = 0
    sample_lines = 0
    active_samples = 0
    last_sample_temp = 0.0
    last_sample_duty = 0.0
    sample_seen_since_status = False
    formal_started = False
    status_start = time.monotonic()
    last_status = status_start - 5.0
    capture_start_t_us: int | None = None

    print(f"[CLID][READY] serial={port},baud={baud}", flush=True)
    print(
        "[CLID][WAITING] waiting for closed_ident_start; "
        "samples before start are ignored",
        flush=True,
    )
    print(
        "[CLID][STOP] stops on closed_ident_finish/closed_ident_stop "
        "or Ctrl+C",
        flush=True,
    )

    try:
        with serial.Serial(port, baudrate=baud, timeout=0.5) as device:
            while True:
                raw = device.readline()
                now = time.monotonic()

                if not raw:
                    if now - last_status >= 5.0:
                        elapsed = now - status_start
                        if not run_started:
                            print(
                                f"[CLID][HEARTBEAT][WAITING_START] "
                                f"elapsed={elapsed:.0f}s,lines={total_lines},"
                                f"samples_seen={sample_lines}; "
                                "waiting for closed_ident_start",
                                flush=True,
                            )
                            if elapsed >= start_timeout_seconds:
                                raise TimeoutError(
                                    "closed_ident_start was not received within "
                                    f"{start_timeout_seconds:.0f}s"
                                )
                        elif not formal_started:
                            print(
                                f"[CLID][HEARTBEAT][WARMUP] "
                                f"elapsed={elapsed:.0f}s,lines={total_lines},"
                                f"samples_seen={sample_lines},"
                                f"temp={last_sample_temp:.2f}C; "
                                f"waiting for temp>={IDENT_START_TEMP_C:.1f}C",
                                flush=True,
                            )
                        elif not sample_seen_since_status:
                            print(
                                f"[CLID][HEARTBEAT][NO_NEW_SAMPLE] "
                                f"elapsed={elapsed:.0f}s,"
                                f"active_samples={active_samples},"
                                f"temp={last_sample_temp:.2f}C,"
                                f"duty={last_sample_duty:.3f}",
                                flush=True,
                            )
                        sample_seen_since_status = False
                        last_status = now
                    continue

                line = raw.decode(errors="replace").rstrip("\r\n")
                total_lines += 1

                if CLOSED_START_RE.search(line):
                    lines = [line]
                    run_started = True
                    active_samples = 0
                    formal_started = False
                    sample_seen_since_status = False
                    capture_start_t_us = None
                    status_start = now
                    last_status = now
                    print(
                        "[CLID][START] closed_ident_start received; "
                        "previous run cleared",
                        flush=True,
                    )
                    continue

                sample_match = SAMPLE_RE.search(line)
                if sample_match is not None:
                    sample_lines += 1
                    if not run_started:
                        if sample_lines == 1:
                            print(
                                "[CLID][RECEIVED_BUT_IGNORED] sample received "
                                "before closed_ident_start",
                                flush=True,
                            )
                        continue

                    last_sample_temp = float(sample_match.group("temp"))
                    last_sample_duty = float(sample_match.group("duty"))
                    if not formal_started:
                        if last_sample_temp < IDENT_START_TEMP_C:
                            if sample_lines == 1 or sample_lines % 100 == 0:
                                print(
                                    f"[CLID][WARMUP] samples_seen={sample_lines},"
                                    f"temp={last_sample_temp:.2f}C,"
                                    f"duty={last_sample_duty:.3f}; "
                                    f"waiting for temp>={IDENT_START_TEMP_C:.1f}C",
                                    flush=True,
                                )
                            continue

                        formal_started = True
                        active_samples = 0
                        capture_start_t_us = None
                        print(
                            f"[CLID][IDENT_START] temperature reached "
                            f"{IDENT_START_TEMP_C:.1f}C; formal capture started",
                            flush=True,
                        )

                    lines.append(line)
                    active_samples += 1
                    sample_seen_since_status = True
                    current_t_us = int(sample_match.group("t_us"))
                    if capture_start_t_us is None:
                        capture_start_t_us = current_t_us

                    if active_samples == 1:
                        print(
                            "[CLID][RECEIVED] first closed-loop sample",
                            flush=True,
                        )
                    elif active_samples % 100 == 0:
                        print(
                            f"[CLID][RUNNING] samples={active_samples},"
                            f"elapsed={float(sample_match.group('t_us')) * 1.0e-6:.3f}s,"
                            f"temp={last_sample_temp:.2f}C,"
                            f"duty={last_sample_duty:.3f}",
                            flush=True,
                        )

                    capture_elapsed = (
                        current_t_us - capture_start_t_us
                    ) * 1.0e-6
                    if (
                        capture_elapsed >= capture_seconds
                        or active_samples >= max_capture_samples
                    ):
                        print(
                            f"[CLID][STOP] capture limit reached: "
                            f"samples={active_samples},"
                            f"elapsed={capture_elapsed:.3f}s",
                            flush=True,
                        )
                        break
                elif run_started:
                    lines.append(line)

                if run_started and CLOSED_FINISH_RE.search(line):
                    print("[CLID][EVENT] closed-loop capture finished", flush=True)
                    break
    except KeyboardInterrupt:
        print("\n[CLID][STOP] serial capture stopped", flush=True)

    if not run_started:
        raise ValueError(
            "closed_ident_start was not received; reset the IMU and start "
            "a new closed-loop identification run"
        )
    if not formal_started:
        raise ValueError(
            f"temperature never reached {IDENT_START_TEMP_C:.1f} C after "
            "closed_ident_start"
        )
    return parse_log_lines(lines)


def reduce_samples(log: ClosedLoopLog, max_samples: int) -> ClosedLoopLog:
    if max_samples < 3:
        raise ValueError("max_samples must be at least three")
    if log.time_s.size <= max_samples:
        return log

    indexes = np.linspace(0, log.time_s.size - 1, max_samples, dtype=int)
    indexes = np.unique(indexes)
    return ClosedLoopLog(
        time_s=log.time_s[indexes],
        temp_c=log.temp_c[indexes],
        duty=log.duty[indexes],
    )


def delayed_input(time_s: np.ndarray, duty: np.ndarray, delay_s: float) -> np.ndarray:
    delayed_time = time_s[1:] - delay_s
    indexes = np.searchsorted(time_s, delayed_time, side="right") - 1
    indexes = np.clip(indexes, 0, duty.size - 1)
    return duty[indexes]


def simulate_plant(
    log: ClosedLoopLog,
    gain: float,
    tau_s: float,
    delay_s: float,
    ambient_c: float,
) -> np.ndarray:
    prediction = np.empty_like(log.temp_c)
    prediction[0] = log.temp_c[0]
    dt_s = np.diff(log.time_s)
    delayed_duty = delayed_input(log.time_s, log.duty, delay_s)

    for index, dt in enumerate(dt_s, start=1):
        alpha = float(np.exp(-dt / tau_s))
        equilibrium = ambient_c + gain * delayed_duty[index - 1]
        prediction[index] = alpha * prediction[index - 1] + (1.0 - alpha) * equilibrium

    return prediction


def fit_closed_loop_plant(log: ClosedLoopLog) -> dict[str, float | np.ndarray]:
    duration_s = float(log.time_s[-1] - log.time_s[0])
    if duration_s <= 0.0:
        raise ValueError("closed-loop log duration must be positive")

    max_delay = min(3.0, duration_s * 0.25)
    delay_grid = np.linspace(0.0, max_delay, 61)
    tau_grid = np.linspace(0.5, min(40.0, max(3.0, duration_s * 2.0)), 160)
    best: dict[str, float | np.ndarray] | None = None
    dt_s = np.diff(log.time_s)

    for delay_s in delay_grid:
        input_delay = delayed_input(log.time_s, log.duty, delay_s)
        for tau_s in tau_grid:
            alpha = np.exp(-dt_s / tau_s)
            beta = 1.0 - alpha
            if np.any(beta <= 1.0e-12):
                continue

            # y[k+1] = alpha*y[k] + beta*(ambient + K*u[k-L])
            equivalent_temp = (log.temp_c[1:] - alpha * log.temp_c[:-1]) / beta
            matrix = np.column_stack((np.ones_like(input_delay), input_delay))
            ambient_c, gain = np.linalg.lstsq(matrix, equivalent_temp, rcond=None)[0]
            if gain <= 0.0:
                continue

            prediction = simulate_plant(log, float(gain), float(tau_s), float(delay_s), float(ambient_c))
            rmse = float(np.sqrt(np.mean((prediction - log.temp_c) ** 2)))
            if best is None or rmse < best["rmse"]:
                best = {
                    "gain": float(gain),
                    "tau_s": float(tau_s),
                    "delay_s": float(delay_s),
                    "ambient_c": float(ambient_c),
                    "rmse": rmse,
                    "prediction": prediction,
                }

    if best is None:
        raise ValueError("failed to fit a positive-gain plant")
    return best


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Identify the IMU heater plant from normal PID closed-loop logs"
    )
    parser.add_argument("log_file", nargs="?", type=Path, help="closed-loop log or CSV")
    parser.add_argument("--csv", action="store_true", help="treat log_file as CSV")
    parser.add_argument("--port", default=None, help="serial port, for example COM21")
    parser.add_argument("--baud", type=int, default=SERIAL_BAUD)
    parser.add_argument("--max-samples", type=int, default=MAX_SAMPLES)
    parser.add_argument(
        "--capture-seconds",
        type=float,
        default=CAPTURE_SECONDS,
        help="maximum capture time after the first closed-loop sample",
    )
    parser.add_argument(
        "--max-capture-samples",
        type=int,
        default=MAX_CAPTURE_SAMPLES,
        help="maximum raw samples before automatic stop",
    )
    parser.add_argument(
        "--start-timeout",
        type=float,
        default=START_TIMEOUT_SECONDS,
        help="maximum wait for closed_ident_start",
    )
    parser.add_argument("--no-plot", action="store_true", help="disable result plot")
    args = parser.parse_args()

    if args.log_file is not None and args.port is not None:
        parser.error("use either log_file or --port, not both")

    if args.log_file is not None:
        if args.csv:
            log = parse_csv(args.log_file)
        else:
            lines = args.log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            log = parse_log_lines(lines)
        source = str(args.log_file)
    else:
        log = capture_serial(
            args.port or SERIAL_PORT,
            args.baud,
            args.capture_seconds,
            args.max_capture_samples,
            args.start_timeout,
        )
        source = "serial"

    original_samples = log.time_s.size
    log = reduce_samples(log, args.max_samples)
    print(
        f"[CLID][FIT] source={source},samples={original_samples},"
        f"fit_samples={log.time_s.size},duration={log.time_s[-1]:.3f}s",
        flush=True,
    )

    result = fit_closed_loop_plant(log)
    print("Closed-loop plant identification:")
    print(
        "  G(s) = "
        f"{result['gain']:.3f} / ({result['tau_s']:.3f}*s + 1) "
        f"* exp(-{result['delay_s']:.3f}*s)"
    )
    print(f"  ambient       : {result['ambient_c']:.3f} C")
    print(f"  RMSE          : {result['rmse']:.3f} C")
    print(f"  temperature   : {log.temp_c[0]:.3f} -> {log.temp_c[-1]:.3f} C")
    print(f"  duty          : {log.duty.min():.3f} -> {log.duty.max():.3f}")

    if not args.no_plot:
        import matplotlib.pyplot as plt

        prediction = result["prediction"]
        error = prediction - log.temp_c
        figure, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
        axes[0].plot(log.time_s, log.temp_c, label="measured", linewidth=1.5)
        axes[0].plot(log.time_s, prediction, "--", label="plant replay", linewidth=1.8)
        axes[0].set_ylabel("Temperature (C)")
        axes[0].grid(True, alpha=0.3)
        axes[0].legend()

        axes[1].plot(log.time_s, log.duty, color="tab:orange", label="PID duty")
        axes[1].set_ylabel("Duty")
        axes[1].grid(True, alpha=0.3)
        axes[1].legend()

        axes[2].plot(log.time_s, error, color="tab:red", label="prediction error")
        axes[2].axhline(0.0, color="black", linewidth=0.8)
        axes[2].set_xlabel("Time (s)")
        axes[2].set_ylabel("Error (C)")
        axes[2].grid(True, alpha=0.3)
        axes[2].legend()
        figure.suptitle("IMU Heater Closed-Loop Plant Identification")
        figure.tight_layout()
        plt.show()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
