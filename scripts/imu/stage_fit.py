"""
stage_fit.py - 提取开环升温曲线，多项式拟合，支持多轮

用法：
    python scripts/imu/stage_fit.py run.log
    python scripts/imu/stage_fit.py --port COM21
    python scripts/imu/stage_fit.py run.log --order 5 --warmup-runs 1
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SERIAL_PORT = "COM21"
SERIAL_BAUD = 921600
EXPECTED_RUNS = 2
WARMUP_RUNS = 1

SAMPLE_RE = re.compile(
    r"seq=(?P<seq>\d+),"
    r"t_us=(?P<t_us>\d+),"
    r"dt_us=(?P<dt_us>\d+),"
    r"stage=(?P<stage>\d+),"
    r"state=(?P<state>\d+),"
    r"temp_c=(?P<temp>[-+0-9.eE]+),"
    r"duty=(?P<duty>[-+0-9.eE]+)"
)
COOLDOWN_DONE_RE = re.compile(r"\bCooldown Done\b")
FINISH_RE = re.compile(r"\bFinished\b")
SAFETY_STOP_RE = re.compile(r"\bSafety Stop\b")
OPEN_IDENT_CMD = b"OpenIdent"
STOP_IDENT_CMD = b"Stop"
HEATING_STATE = 1

STATE_NAMES = {0: "Cooldown", 1: "Heating", 2: "SafetyStop", 3: "Finished", 4: "Stop"}


@dataclass
class StageCurve:
    run: int
    stage: int
    duty: float
    time: np.ndarray
    temp: np.ndarray


def parse_log(path: Path) -> list[StageCurve]:
    """解析日志，按 (run, stage) 提取每段 Heating 曲线。"""
    curves: list[StageCurve] = []
    run_index = 0
    started = False
    stage_ready = False
    cur_stage: int | None = None
    cur_duty: float | None = None
    cur_t_us: list[float] = []
    cur_temp: list[float] = []
    first_t_us: float | None = None

    def flush_stage() -> None:
        nonlocal cur_stage, cur_duty, cur_t_us, cur_temp, first_t_us
        if cur_stage is not None and len(cur_t_us) >= 3:
            t = np.asarray(cur_t_us, dtype=float)
            if first_t_us is not None:
                t = (t - first_t_us) * 1.0e-6
            curves.append(StageCurve(
                run=run_index,
                stage=cur_stage,
                duty=cur_duty,
                time=t,
                temp=np.asarray(cur_temp, dtype=float),
            ))
        cur_stage = None
        cur_duty = None
        cur_t_us = []
        cur_temp = []
        first_t_us = None

    for line in path.read_text("utf-8", errors="ignore").splitlines():
        if FINISH_RE.search(line):
            flush_stage()
            run_index += 1
            started = False
            stage_ready = False
            continue

        if COOLDOWN_DONE_RE.search(line):
            flush_stage()
            started = True
            stage_ready = True
            continue

        if not started:
            continue

        match = SAMPLE_RE.search(line)
        if match is None:
            continue

        state = int(match.group("state"))
        if state != HEATING_STATE:
            stage_ready = False
            flush_stage()
            continue

        if not stage_ready:
            continue

        stage = int(match.group("stage"))
        duty = float(match.group("duty"))
        t_us = float(match.group("t_us"))
        temp = float(match.group("temp"))

        if cur_stage != stage or abs((cur_duty or 0) - duty) > 1e-6:
            flush_stage()
            cur_stage = stage
            cur_duty = duty
            first_t_us = t_us

        if first_t_us is None:
            first_t_us = t_us
        cur_t_us.append(t_us)
        cur_temp.append(temp)

    flush_stage()

    if not curves:
        raise ValueError("no heating stages found in log")
    return curves


def capture_serial(port: str, baud: int, expected_runs: int) -> list[StageCurve]:
    """在线捕获 expected_runs 轮开环辨识。"""
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("requires pyserial") from exc

    raw_lines: list[str] = []
    command_sent = False
    boot_done = False
    finished_count = 0
    last_state = -1
    last_stage = -1
    last_t_us = 0

    print(f"[STAGE_FIT] serial={port},baud={baud},runs={expected_runs}")
    print("[STAGE_FIT] waiting for set autoident mode + imu ready", flush=True)

    with serial.Serial(port, baudrate=baud, timeout=0.5) as ser:
        while finished_count < expected_runs:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode(errors="replace").rstrip("\r\n")

            # 启动前：不打印任何行，只等 boot flag
            if not command_sent:
                if re.search(r"\bset autoident mode\b", line):
                    boot_done = True
                if boot_done and re.search(r"\bimu ready\b", line):
                    ser.write(OPEN_IDENT_CMD)
                    ser.flush()
                    command_sent = True
                    print(">>> OpenIdent sent", flush=True)
                continue

            raw_lines.append(line)

            # 只打状态行，不打原始行
            m = SAMPLE_RE.search(line)
            if m:
                state = int(m.group("state"))
                stage = int(m.group("stage"))
                t_us = int(m.group("t_us"))
                temp = float(m.group("temp"))
                duty = float(m.group("duty"))

                if state != last_state or stage != last_stage:
                    sname = STATE_NAMES.get(state, f"state{state}")
                    print(f"[{sname}][stage {stage}] temp={temp:.2f}C  duty={duty:.3f}", flush=True)
                    last_state = state
                    last_stage = stage
                    last_t_us = t_us
                elif t_us - last_t_us >= 500_000:
                    sname = STATE_NAMES.get(state, f"state{state}")
                    print(f"[{sname}][stage {stage}] temp={temp:.2f}C  duty={duty:.3f}", flush=True)
                    last_t_us = t_us

            if re.search(r"\bFinished\b", line):
                finished_count += 1
                print(f">>> run {finished_count}/{expected_runs} finished", flush=True)

    Path("__capture_tmp.log").write_text("\n".join(raw_lines), encoding="utf-8")
    result = parse_log(Path("__capture_tmp.log"))
    Path("__capture_tmp.log").unlink()
    return result


def fit_stage_polynomial(curve: StageCurve, order: int) -> np.ndarray:
    return np.polyfit(curve.time, curve.temp, order)


def print_stage_result(curve: StageCurve, coeffs: np.ndarray, order: int) -> None:
    fitted = np.polyval(coeffs, curve.time)
    error = fitted - curve.temp
    rmse = float(np.sqrt(np.mean(error ** 2)))

    terms = " + ".join(
        f"{c:.6f}*t^{order - i}" if order - i > 0 else f"{c:.6f}"
        for i, c in enumerate(coeffs)
    )
    print(f"  run {curve.run}, stage {curve.stage}: duty={curve.duty:.3f}, "
          f"samples={curve.time.size}, "
          f"duration={curve.time[-1]:.1f}s, "
          f"temp={curve.temp[0]:.2f}->{curve.temp[-1]:.2f}C, "
          f"RMSE={rmse:.4f}C")
    print(f"    T(t) = {terms}")
    print(f"    dT/dt(0) = {coeffs[order - 1]:.4f} C/s")


def plot_stages(curves: list[StageCurve], coeffs_list: list[np.ndarray]) -> None:
    import matplotlib.pyplot as plt

    colors = plt.get_cmap("tab10")(np.linspace(0, 1, len(curves)))
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    t_max = max(c.time[-1] for c in curves)

    for i, (curve, coeffs) in enumerate(zip(curves, coeffs_list)):
        c = colors[i]
        t_fine = np.linspace(0, t_max, 500)
        fitted = np.polyval(coeffs, t_fine)

        axes[0].plot(curve.time, curve.temp, color=c, linewidth=1.0, alpha=0.5)
        axes[0].plot(t_fine, fitted, color=c, linewidth=1.8,
                     label=f"run{curve.run} stg{curve.stage} duty={curve.duty:.3f}")
        axes[1].step(curve.time, np.full_like(curve.time, curve.duty),
                     color=c, linewidth=1.5, where="post")

    axes[0].set_ylabel("Temperature (C)")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(fontsize=8)

    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("Duty")
    axes[1].set_xlim(0, t_max)
    axes[1].grid(True, alpha=0.3)

    fig.suptitle("Heater Stage Temperature Rise with Polynomial Fit")
    fig.tight_layout()
    plt.show()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract per-stage heating curves and fit polynomials"
    )
    parser.add_argument("log_file", nargs="?", type=Path)
    parser.add_argument("--port", default=None)
    parser.add_argument("--baud", type=int, default=SERIAL_BAUD)
    parser.add_argument("--order", type=int, default=5,
                        help="polynomial order (default 5)")
    parser.add_argument("--warmup-runs", type=int, default=WARMUP_RUNS,
                        help="skip first N runs in fitting (default 1)")
    parser.add_argument("--expected-runs", type=int, default=EXPECTED_RUNS,
                        help="capture N runs (default 2)")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    if args.log_file is not None:
        curves = parse_log(args.log_file)
    else:
        curves = capture_serial(args.port or SERIAL_PORT, args.baud,
                                args.expected_runs)

    if not curves:
        print("no heating curves found")
        return 1

    fit_curves = [c for c in curves if c.run >= args.warmup_runs]
    if not fit_curves:
        print(f"no curves after warmup (warmup_runs={args.warmup_runs})")
        return 1
    skip_curves = [c for c in curves if c.run < args.warmup_runs]

    warning_text = ""
    if skip_curves:
        warning_text = f" (warmup: {len(skip_curves)} curves from runs 0-{args.warmup_runs - 1} skipped)"

    print(f"Polynomial fit order={args.order}, fit_curves={len(fit_curves)}"
          f"{warning_text}:")
    coeffs_list = []
    for curve in fit_curves:
        coeffs = fit_stage_polynomial(curve, args.order)
        coeffs_list.append(coeffs)
        print_stage_result(curve, coeffs, args.order)

    if not args.no_plot:
        plot_stages(fit_curves, coeffs_list)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
