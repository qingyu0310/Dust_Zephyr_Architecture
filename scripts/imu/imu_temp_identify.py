"""
imu_temp_identify.py - IMU 加热器温度曲线拟合（按占空比阶段）

用法：
    python scripts/imu_temp_identify.py
    python scripts/imu_temp_identify.py run1.log run2.log
    python scripts/imu_temp_identify.py run1.csv run2.csv --csv
    python scripts/imu_temp_identify.py --port COM5 --baud 115200

输入格式：
    1) Zephyr 日志行：
       heater_id,temp=41.23,duty=0.500,dt=0.0010
    2) CSV 文件，列名如：
       time,temp,duty
       或
       temp,duty,dt

该脚本对每个加热阶段拟合一阶滞后曲线：
    T(t) = T0 + dT * (1 - exp(-(t - L) / tau))

也可以对所有阶段拟合联合模型：
    所有阶段共享同一个 tau 和同一个 L，
    每个占空比阶段有自己的 dT。
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np


# 直接修改这里配置串口；命令行 --port/--baud 可临时覆盖。
SERIAL_PORT = "COM21"
SERIAL_BAUD = 921600
ONLINE_AVERAGE_SAMPLES = 10
OUTLIER_SIGMA = 2.5


LOG_RE = re.compile(
    r"heater_id,.*?"
    r"temp=(?P<temp>[-+0-9.]+),"
    r"duty=(?P<duty>[-+0-9.]+),"
    r"dt=(?P<dt>[-+0-9.]+)"
)
MCU_TIME_RE = re.compile(r"(?:^|,)t_ms=(?P<t_ms>\d+)(?:,|$)")
TIMESTAMP_RE = re.compile(
    r"\[(?P<h>\d+):(?P<m>\d+):(?P<s>\d+)\.(?P<ms>\d+),\d+\]"
)
STAGE_START_RE = re.compile(
    r"heater_id,stage_start(?:,.*?)?target=(?P<duty>[-+0-9.]+)"
)
STAGE_END_RE = re.compile(
    r"heater_id,stage_end(?:,.*?)?target=(?P<duty>[-+0-9.]+),"
    r"temp=(?P<temp>[-+0-9.]+),.*?stable=(?P<stable>\d+)"
)
SWEEP_FINISHED_RE = re.compile(
    r"heater_id,sweep_finished(?:,.*?)?temp=(?P<temp>[-+0-9.]+),"
    r"safety=(?P<safety>\d+)"
)


@dataclass
class Series:
    time: np.ndarray
    temp: np.ndarray
    duty: np.ndarray
    dt: np.ndarray


@dataclass
class Segment:
    index: int
    duty: float
    series: Series
    source: str = ""


def parse_log_sample(raw: str) -> tuple[float, float, float, float | None] | None:
    match = LOG_RE.search(raw)
    if not match:
        return None

    temp_v = float(match.group("temp"))
    duty_v = float(match.group("duty"))
    dt_v = float(match.group("dt"))
    mcu_time = MCU_TIME_RE.search(raw)
    if mcu_time is not None:
        return temp_v, duty_v, dt_v, int(mcu_time.group("t_ms")) / 1000.0

    timestamp = TIMESTAMP_RE.search(raw)
    if timestamp is None:
        return temp_v, duty_v, dt_v, None

    timestamp_s = (
        int(timestamp.group("h")) * 3600.0
        + int(timestamp.group("m")) * 60.0
        + int(timestamp.group("s"))
        + int(timestamp.group("ms")) / 1000.0
    )
    return temp_v, duty_v, dt_v, timestamp_s


def parse_log_timestamp(raw: str) -> float | None:
    mcu_time = MCU_TIME_RE.search(raw)
    if mcu_time is not None:
        return int(mcu_time.group("t_ms")) / 1000.0

    timestamp = TIMESTAMP_RE.search(raw)
    if timestamp is None:
        return None
    return (
        int(timestamp.group("h")) * 3600.0
        + int(timestamp.group("m")) * 60.0
        + int(timestamp.group("s"))
        + int(timestamp.group("ms")) / 1000.0
    )


def build_delta_t(
    timestamp_s: float | None,
    last_timestamp_s: float | None,
    anchor_timestamp_s: float | None,
    fallback_dt: float,
) -> tuple[float, float | None]:
    if timestamp_s is None:
        return fallback_dt, last_timestamp_s
    if last_timestamp_s is not None:
        return max(timestamp_s - last_timestamp_s, 0.0), timestamp_s
    if anchor_timestamp_s is not None:
        return max(timestamp_s - anchor_timestamp_s, 0.0), timestamp_s
    return fallback_dt, timestamp_s


def parse_log_segments(lines: Iterable[str], source: str = "") -> list[Segment]:
    segments: list[Segment] = []
    temps: list[float] = []
    duties: list[float] = []
    dts: list[float] = []
    times: list[float] = []
    stage_index = -1
    stage_duty = 0.0
    stage_active = False
    stage_anchor_timestamp: float | None = None
    last_timestamp_s: float | None = None
    stage_time = 0.0

    def finish_stage() -> None:
        nonlocal temps, duties, dts, times, stage_time
        if len(temps) < 3:
            temps = []
            duties = []
            dts = []
            times = []
            stage_time = 0.0
            return

        segments.append(
            Segment(
                index=stage_index,
                duty=stage_duty,
                series=Series(
                    time=np.asarray(times, dtype=float),
                    temp=np.asarray(temps, dtype=float),
                    duty=np.asarray(duties, dtype=float),
                    dt=np.asarray(dts, dtype=float),
                ),
                source=source,
            )
        )
        temps = []
        duties = []
        dts = []
        times = []
        stage_time = 0.0

    for raw in lines:
        stage_start = STAGE_START_RE.search(raw)
        if stage_start:
            if stage_active:
                finish_stage()
            stage_index += 1
            stage_duty = float(stage_start.group("duty"))
            stage_active = True
            stage_anchor_timestamp = parse_log_timestamp(raw)
            last_timestamp_s = None
            stage_time = 0.0
            continue

        if STAGE_END_RE.search(raw) or SWEEP_FINISHED_RE.search(raw):
            if stage_active:
                finish_stage()
            stage_active = False
            stage_anchor_timestamp = None
            last_timestamp_s = None
            continue

        parsed = parse_log_sample(raw)
        if parsed is None or not stage_active:
            continue

        temp_v, duty_v, dt_v, timestamp_s = parsed
        delta_t, last_timestamp_s = build_delta_t(
            timestamp_s,
            last_timestamp_s,
            stage_anchor_timestamp,
            dt_v,
        )
        stage_time += delta_t
        temps.append(temp_v)
        duties.append(duty_v)
        dts.append(delta_t)
        times.append(stage_time)

    if stage_active:
        finish_stage()
    return segments


def parse_csv_file(path: Path) -> list[Segment]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("empty csv")

        fields = {name.strip().lower(): name for name in reader.fieldnames}
        if "temp" not in fields or "duty" not in fields:
            raise ValueError("csv needs temp and duty columns")
        if "time" not in fields and "dt" not in fields:
            raise ValueError("csv needs time or dt column")

        time: list[float] = []
        temp: list[float] = []
        duty: list[float] = []
        dt: list[float] = []
        t = 0.0
        for row in reader:
            temp_v = float(row[fields["temp"]])
            duty_v = float(row[fields["duty"]])
            if "time" in fields:
                t = float(row[fields["time"]])
                dt_v = 0.0 if not time else t - time[-1]
            else:
                dt_v = float(row[fields["dt"]])
                t += dt_v
            time.append(t)
            temp.append(temp_v)
            duty.append(duty_v)
            dt.append(dt_v)

    if not time:
        raise ValueError("no csv rows found")

    return split_segments(
        Series(
            time=np.asarray(time, dtype=float),
            temp=np.asarray(temp, dtype=float),
            duty=np.asarray(duty, dtype=float),
            dt=np.asarray(dt, dtype=float),
        ),
        source=str(path),
    )


def print_segments(segments: list[Segment]) -> None:
    print("Read data:")
    print("source,stage,index,time_s,temp_c,duty,dt_s")
    total_samples = 0
    for segment in segments:
        for idx, (time_s, temp_c, duty, dt_s) in enumerate(
            zip(
                segment.series.time,
                segment.series.temp,
                segment.series.duty,
                segment.series.dt,
            )
        ):
            print(
                f"{segment.source},{segment.index},{idx},"
                f"{time_s:.6f},{temp_c:.3f},{duty:.6f},{dt_s:.6f}"
            )
            total_samples += 1
    print(f"Total stages: {len(segments)}")
    print(f"Total samples: {total_samples}")


def write_segments_csv(segments: list[Segment], path: Path) -> None:
    """保存辨识原始样本，时间使用每个阶段开始后的相对时间。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["source", "stage", "time_s", "temp_c", "duty", "dt_s"])
        for segment in segments:
            for time_s, temp_c, duty, dt_s in zip(
                segment.series.time,
                segment.series.temp,
                segment.series.duty,
                segment.series.dt,
            ):
                writer.writerow([
                    segment.source,
                    segment.index,
                    f"{time_s:.6f}",
                    f"{temp_c:.6f}",
                    f"{duty:.6f}",
                    f"{dt_s:.6f}",
                ])
    print(f"Saved samples: {path}")


class OnlineIdentifier:
    def __init__(self) -> None:
        self._pending: list[tuple[float, float, float, float | None]] = []
        self._current_segment: Segment | None = None
        self._segments: list[Segment] = []
        self._stage_index = -1
        self._stage_anchor_timestamp: float | None = None
        self._last_timestamp_s: float | None = None
        self._raw_time_s = 0.0

    def feed(self, sample: tuple[float, float, float, float | None]) -> None:
        if self._current_segment is None:
            return

        self._append_raw_sample(sample)
        self._pending.append(sample)
        if len(self._pending) >= ONLINE_AVERAGE_SAMPLES:
            self.flush_pending()

    def _append_raw_sample(
        self,
        sample: tuple[float, float, float, float | None],
    ) -> None:
        temp_v, duty_v, dt_v, timestamp_s = sample

        if timestamp_s is not None:
            if self._last_timestamp_s is None:
                if self._stage_anchor_timestamp is not None:
                    self._raw_time_s = max(
                        timestamp_s - self._stage_anchor_timestamp,
                        0.0,
                    )
                else:
                    self._raw_time_s = 0.0
            else:
                self._raw_time_s += max(
                    timestamp_s - self._last_timestamp_s,
                    0.0,
                )
            self._last_timestamp_s = timestamp_s
        else:
            self._raw_time_s += max(dt_v, 0.0)

        series = self._current_segment.series
        series.time = np.append(series.time, self._raw_time_s)
        series.temp = np.append(series.temp, temp_v)
        series.duty = np.append(series.duty, duty_v)
        series.dt = np.append(series.dt, dt_v)

    def flush_pending(self) -> None:
        if not self._pending or self._current_segment is None:
            return

        temps = [sample[0] for sample in self._pending]
        duties = [sample[1] for sample in self._pending]
        dts = [sample[2] for sample in self._pending]
        self._pending.clear()

        # DATA 仅用于观察，辨识仍使用上面保存的原始点。
        print(
            f"DATA,{self._current_segment.index},"
            f"{self._current_segment.series.time.size},"
            f"{self._raw_time_s:.6f},"
            f"{float(np.mean(temps)):.3f},"
            f"{float(np.mean(duties)):.6f},"
            f"{float(np.sum(dts)):.6f}",
            flush=True,
        )

    def handle_event(self, raw: str) -> tuple[bool, bool]:
        stage_start = STAGE_START_RE.search(raw)
        if stage_start:
            self.flush_pending()
            self._stage_index += 1
            self._stage_anchor_timestamp = parse_log_timestamp(raw)
            self._last_timestamp_s = None
            self._raw_time_s = 0.0
            self._current_segment = Segment(
                index=self._stage_index,
                duty=float(stage_start.group("duty")),
                series=Series(
                    time=np.asarray([], dtype=float),
                    temp=np.asarray([], dtype=float),
                    duty=np.asarray([], dtype=float),
                    dt=np.asarray([], dtype=float),
                ),
                source="serial",
            )
            print(f"RAW,{raw}", flush=True)
            return True, False

        if STAGE_END_RE.search(raw):
            self.flush_pending()
            self._finish_current_segment()
            print(f"RAW,{raw}", flush=True)
            return True, False

        if SWEEP_FINISHED_RE.search(raw):
            self.flush_pending()
            self._finish_current_segment()
            print(f"RAW,{raw}", flush=True)
            print("Sweep finished, serial capture stopped automatically.", flush=True)
            return True, True

        return False, False

    def _finish_current_segment(self) -> None:
        if self._current_segment is None:
            return
        if self._current_segment.series.time.size >= 3:
            self._segments.append(self._current_segment)
            try:
                fit = fit_heating_curve(self._current_segment.series)
                print(
                    "ONLINE,"
                    f"stage={self._current_segment.index},"
                    f"samples={self._current_segment.series.time.size},"
                    f"duty={self._current_segment.duty:.3f},"
                    f"T0={fit['t0_temp']:.3f},"
                    f"Tss={fit['tss_temp']:.3f},"
                    f"L={fit['l']:.3f},"
                    f"tau={fit['tau']:.3f},"
                    f"rise={fit['delta_temp']:.3f}",
                    flush=True,
                )
            except ValueError:
                pass
        self._current_segment = None
        self._stage_anchor_timestamp = None
        self._last_timestamp_s = None
        self._raw_time_s = 0.0

    def segments(self) -> list[Segment]:
        return list(self._segments)


def read_serial_online(port: str, baud: int) -> list[Segment]:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("serial mode requires pyserial: pip install pyserial") from exc

    online = OnlineIdentifier()
    print(f"Reading serial: {port} @ {baud} baud")
    print("Press Ctrl+C to stop.")

    try:
        with serial.Serial(port, baudrate=baud, timeout=0.5) as ser:
            while True:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode(errors="replace").rstrip("\r\n")
                handled, should_stop = online.handle_event(line)
                if handled:
                    if should_stop:
                        break
                    continue
                parsed = parse_log_sample(line)
                if parsed is None:
                    continue
                online.feed(parsed)
    except KeyboardInterrupt:
        print("\nSerial capture stopped.")
        online.flush_pending()

    segments = online.segments()
    if not segments:
        raise ValueError("no heater_id samples captured")
    return segments


def robust_mean(x: np.ndarray) -> float:
    if x.size == 0:
        return float("nan")
    return float(np.median(x))


def split_segments(series: Series, duty_threshold: float = 1e-3, source: str = "") -> list[Segment]:
    if series.time.size == 0:
        return []

    segments: list[Segment] = []
    start = 0
    segment_index = 0
    for idx in range(1, series.time.size):
        if abs(series.duty[idx] - series.duty[idx - 1]) < duty_threshold:
            continue
        if idx - start >= 3:
            seg_time = series.time[start:idx] - series.time[start]
            segments.append(
                Segment(
                    index=segment_index,
                    duty=float(robust_mean(series.duty[start:idx])),
                    series=Series(
                        time=seg_time,
                        temp=series.temp[start:idx],
                        duty=series.duty[start:idx],
                        dt=series.dt[start:idx],
                    ),
                    source=source,
                )
            )
            segment_index += 1
        start = idx

    if series.time.size - start >= 3:
        seg_time = series.time[start:] - series.time[start]
        segments.append(
            Segment(
                index=segment_index,
                duty=float(robust_mean(series.duty[start:])),
                series=Series(
                    time=seg_time,
                    temp=series.temp[start:],
                    duty=series.duty[start:],
                    dt=series.dt[start:],
                ),
                source=source,
            )
        )
    return segments


def fit_heating_curve(series: Series) -> dict:
    t = series.time - series.time[0]
    y = series.temp
    if y.size < 6:
        raise ValueError("not enough samples to fit heating curve")

    edge_n = max(3, min(8, y.size // 4 if y.size >= 8 else 3))
    t0_temp = robust_mean(y[:edge_n])

    duration = float(t[-1])
    l_grid = np.linspace(0.0, min(5.0, duration * 0.4), 80)
    tau_grid = np.linspace(max(0.5, duration * 0.05), max(1.0, duration * 2.0), 160)

    best: dict | None = None
    for l_value in l_grid:
        rel_t = np.maximum(t - l_value, 0.0)
        for tau_value in tau_grid:
            basis = 1.0 - np.exp(-rel_t / tau_value)
            denom = float(np.dot(basis, basis))
            if denom <= 1e-9:
                continue
            delta_temp = float(np.dot(basis, y - t0_temp) / denom)
            if delta_temp <= 0.0:
                continue
            y_hat = t0_temp + delta_temp * basis
            sse = float(np.sum((y - y_hat) ** 2))
            if best is None or sse < best["sse"]:
                best = {
                    "l": l_value,
                    "tau": tau_value,
                    "delta_temp": delta_temp,
                    "y_hat": y_hat,
                    "sse": sse,
                }

    if best is None:
        raise ValueError("failed to fit delayed heating curve")

    tss_temp = t0_temp + best["delta_temp"]
    return {
        "duty": float(robust_mean(series.duty)),
        "t0_temp": t0_temp,
        "tss_temp": tss_temp,
        "delta_temp": best["delta_temp"],
        "tau": best["tau"],
        "l": best["l"],
        "duration": duration,
        "y_hat": best["y_hat"],
    }


def predict_heating_curve(time: np.ndarray, fit: dict) -> np.ndarray:
    t = time - time[0]
    rel_t = np.maximum(t - fit.get("l", 0.0), 0.0)
    return fit["t0_temp"] + fit["delta_temp"] * (1.0 - np.exp(-rel_t / fit["tau"]))


def calc_error_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    error = y_pred - y_true
    abs_error = np.abs(error)
    return {
        "rmse": float(np.sqrt(np.mean(error * error))),
        "mae": float(np.mean(abs_error)),
        "max_error": float(np.max(abs_error)),
    }


def fit_common_shape(fit_results: list[tuple[Segment, dict, dict]]) -> dict:
    if len(fit_results) < 2:
        raise ValueError("need at least two stages for common-shape fitting")

    durations = np.asarray([fit["duration"] for _, fit, _ in fit_results], dtype=float)
    max_duration = float(np.max(durations))
    l_grid = np.linspace(0.0, min(5.0, max_duration * 0.4), 80)
    tau_grid = np.linspace(max(0.5, max_duration * 0.05), max(1.0, max_duration * 2.0), 160)

    best: dict | None = None
    for l_value in l_grid:
        for tau_value in tau_grid:
            total_sse = 0.0
            stage_fits: list[dict] = []
            for segment, _, _ in fit_results:
                t = segment.series.time - segment.series.time[0]
                y = segment.series.temp
                edge_n = max(3, min(8, y.size // 4 if y.size >= 8 else 3))
                t0_temp = robust_mean(y[:edge_n])
                basis = 1.0 - np.exp(-np.maximum(t - l_value, 0.0) / tau_value)
                denom = float(np.dot(basis, basis))
                if denom <= 1e-9:
                    total_sse = float("inf")
                    break
                delta_temp = float(np.dot(basis, y - t0_temp) / denom)
                if delta_temp <= 0.0:
                    total_sse = float("inf")
                    break
                y_hat = t0_temp + delta_temp * basis
                residual = y - y_hat
                sse = float(np.sum(residual * residual))
                total_sse += sse
                stage_fits.append(
                    {
                        "stage": segment.index,
                        "source": segment.source,
                        "duty": segment.duty,
                        "t0_temp": t0_temp,
                        "delta_temp": delta_temp,
                        "tss_temp": t0_temp + delta_temp,
                        "y_hat": y_hat,
                        "rmse": float(np.sqrt(np.mean(residual * residual))),
                        "sse": sse,
                    }
                )
            if best is None or total_sse < best["sse"]:
                best = {
                    "tau": tau_value,
                    "l": l_value,
                    "sse": total_sse,
                    "stage_fits": stage_fits,
                }

    if best is None:
        raise ValueError("failed to fit common shape")
    return best


def detect_outliers(common_shape: dict, sigma: float = OUTLIER_SIGMA) -> list[dict]:
    rmses = np.asarray([stage_fit["rmse"] for stage_fit in common_shape["stage_fits"]], dtype=float)
    if rmses.size < 3:
        return []
    center = float(np.median(rmses))
    mad = float(np.median(np.abs(rmses - center)))
    if mad < 1e-9:
        return []

    outliers: list[dict] = []
    for stage_fit in common_shape["stage_fits"]:
        robust_z = 0.6745 * (stage_fit["rmse"] - center) / mad
        if robust_z > sigma:
            outlier = dict(stage_fit)
            outlier["robust_z"] = robust_z
            outliers.append(outlier)
    return outliers


def remove_outlier_segments(
    fit_results: list[tuple[Segment, dict, dict]],
    outliers: list[dict],
) -> list[tuple[Segment, dict, dict]]:
    outlier_keys = {(item["source"], item["stage"]) for item in outliers}
    return [
        item
        for item in fit_results
        if (item[0].source, item[0].index) not in outlier_keys
    ]


def print_fit_summary(fit_results: list[tuple[Segment, dict, dict]], title: str = "Summary") -> None:
    taus = np.asarray([fit["tau"] for _, fit, _ in fit_results], dtype=float)
    delays = np.asarray([fit.get("l", 0.0) for _, fit, _ in fit_results], dtype=float)
    rises = np.asarray([fit["delta_temp"] for _, fit, _ in fit_results], dtype=float)
    duties = np.asarray([segment.duty for segment, _, _ in fit_results], dtype=float)

    print(f"{title}:")
    print(f"  stages      : {len(fit_results)}")
    print(f"  tau mean    : {np.mean(taus):.3f} s")
    print(f"  tau std     : {np.std(taus):.3f} s")
    print(f"  L mean      : {np.mean(delays):.3f} s")
    print(f"  L std       : {np.std(delays):.3f} s")
    print(f"  rise mean   : {np.mean(rises):.3f} C")
    if len(fit_results) >= 2 and np.ptp(duties) > 1e-6:
        slope, intercept = np.polyfit(duties, rises, 1)
        print(f"  rise(duty)  : rise ~= {slope:.3f} * duty + {intercept:.3f}")


def print_common_shape(common_shape: dict, title: str) -> None:
    print(f"{title}:")
    print(f"  tau common  : {common_shape['tau']:.3f} s")
    print(f"  L common    : {common_shape['l']:.3f} s")
    for stage_fit in common_shape["stage_fits"]:
        print(
            f"  source={stage_fit['source']}, stage={stage_fit['stage']}, duty={stage_fit['duty']:.3f}, "
            f"T0={stage_fit['t0_temp']:.3f} C, rise={stage_fit['delta_temp']:.3f} C, "
            f"Tss={stage_fit['tss_temp']:.3f} C, RMSE={stage_fit['rmse']:.3f} C"
        )


def load_segments(paths: list[Path], csv_mode: bool) -> list[Segment]:
    segments: list[Segment] = []
    for path in paths:
        if csv_mode:
            segments.extend(parse_csv_file(path))
            continue
        try:
            segments.extend(parse_csv_file(path))
        except Exception:
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                segments.extend(parse_log_segments(f, source=str(path)))
    return segments


def main() -> int:
    parser = argparse.ArgumentParser(description="IMU heater temperature curve fitting")
    parser.add_argument("log_files", type=Path, nargs="*", help="log or csv file path(s)")
    parser.add_argument("--csv", action="store_true", help="force CSV mode")
    parser.add_argument("--no-plot", action="store_true", help="disable curve plot")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="save parsed identification samples to CSV",
    )
    parser.add_argument("--port", default=None, help="serial port, for example COM5")
    parser.add_argument("--baud", type=int, default=None, help="serial baud rate")
    parser.add_argument(
        "--outlier-sigma",
        type=float,
        default=OUTLIER_SIGMA,
        help="robust z-score threshold for outlier stage rejection",
    )
    args = parser.parse_args()

    if args.log_files:
        if args.port or args.baud:
            parser.error("--port/--baud cannot be used with log_files")
        segments = load_segments(args.log_files, args.csv)
        print_segments(segments)
    else:
        port = args.port or SERIAL_PORT
        baud = args.baud or SERIAL_BAUD
        segments = read_serial_online(port, baud)

    if not segments:
        raise ValueError("no heating segments found")

    if args.output is not None:
        write_segments_csv(segments, args.output)

    fit_results: list[tuple[Segment, dict, dict]] = []
    print("Heating curve fits:")
    for segment in segments:
        fit = fit_heating_curve(segment.series)
        y_hat = predict_heating_curve(segment.series.time, fit)
        metrics = calc_error_metrics(segment.series.temp, y_hat)
        fit_results.append((segment, fit, metrics))
        print(
            f"  source={segment.source}, stage={segment.index}, duty={segment.duty:.3f}, "
            f"T0={fit['t0_temp']:.3f} C, Tss={fit['tss_temp']:.3f} C, "
            f"rise={fit['delta_temp']:.3f} C, L={fit['l']:.3f} s, tau={fit['tau']:.3f} s, "
            f"RMSE={metrics['rmse']:.3f} C"
        )
    print_fit_summary(fit_results, "Per-stage summary")

    common_shape = None
    inlier_fit_results = fit_results
    inlier_common_shape = None
    outliers: list[dict] = []

    try:
        common_shape = fit_common_shape(fit_results)
        print_common_shape(common_shape, "Joint least-squares, all stages")
        outliers = detect_outliers(common_shape, args.outlier_sigma)
        if outliers:
            print("Outliers:")
            for outlier in outliers:
                print(
                    f"  source={outlier['source']}, stage={outlier['stage']}, duty={outlier['duty']:.3f}, "
                    f"RMSE={outlier['rmse']:.3f} C, robust_z={outlier['robust_z']:.3f}"
                )
            inlier_fit_results = remove_outlier_segments(fit_results, outliers)
            if len(inlier_fit_results) >= 2:
                print_fit_summary(inlier_fit_results, "Inlier summary")
                inlier_common_shape = fit_common_shape(inlier_fit_results)
                print_common_shape(inlier_common_shape, "Joint least-squares, inliers only")
    except ValueError:
        pass

    plot_fit_results = inlier_fit_results if inlier_common_shape is not None else fit_results
    plot_common_shape = inlier_common_shape if inlier_common_shape is not None else common_shape

    if not args.no_plot:
        import matplotlib.pyplot as plt

        plt.figure(figsize=(10, 12))

        plt.subplot(5, 1, 1)
        for segment, fit, _ in plot_fit_results:
            plt.plot(segment.series.time, segment.series.temp, label=f"duty={segment.duty:.3f}")
            plt.plot(
                segment.series.time,
                predict_heating_curve(segment.series.time, fit),
                linestyle="--",
                alpha=0.8,
                label=f"fit_{segment.duty:.3f}",
            )
        if plot_common_shape is not None:
            for segment, _, _ in plot_fit_results:
                stage_fit = next(
                    item
                    for item in plot_common_shape["stage_fits"]
                    if item["source"] == segment.source and item["stage"] == segment.index
                )
                plt.plot(
                    segment.series.time,
                    stage_fit["y_hat"],
                    linestyle=":",
                    alpha=0.8,
                    label=f"common_{segment.duty:.3f}",
                )
        plt.ylabel("Temperature (C)")
        plt.legend()
        plt.grid(True, alpha=0.3)

        plt.subplot(5, 1, 2)
        for segment, _, _ in plot_fit_results:
            plt.plot(segment.series.time, segment.series.duty, label=f"duty={segment.duty:.3f}")
        plt.ylabel("Duty")
        plt.grid(True, alpha=0.3)

        plt.subplot(5, 1, 3)
        for segment, fit, _ in plot_fit_results:
            norm = (segment.series.temp - fit["t0_temp"]) / max(fit["delta_temp"], 1e-9)
            plt.plot(
                segment.series.time,
                np.clip(norm, 0.0, 1.2),
                label=f"duty={segment.duty:.3f}",
            )
        if plot_common_shape is not None:
            tau_common = plot_common_shape["tau"]
            l_common = plot_common_shape["l"]
            ref_time = max(float(np.max(segment.series.time)) for segment, _, _ in plot_fit_results)
            t_ref = np.linspace(0.0, ref_time, 200)
            z_ref = 1.0 - np.exp(-np.maximum(t_ref - l_common, 0.0) / tau_common)
            plt.plot(t_ref, z_ref, linestyle=":", linewidth=2.0, label="common_shape")
        plt.ylabel("Normalized")
        plt.grid(True, alpha=0.3)
        plt.legend()

        plt.subplot(5, 1, 4)
        duties = np.asarray([segment.duty for segment, _, _ in plot_fit_results], dtype=float)
        taus = np.asarray([fit["tau"] for _, fit, _ in plot_fit_results], dtype=float)
        plt.plot(duties, taus, marker="o", label="tau_stage")
        if plot_common_shape is not None:
            plt.axhline(plot_common_shape["tau"], linestyle=":", color="gray", label="tau_common")
        plt.ylabel("Tau (s)")
        plt.grid(True, alpha=0.3)
        plt.legend()

        plt.subplot(5, 1, 5)
        rises = np.asarray([fit["delta_temp"] for _, fit, _ in plot_fit_results], dtype=float)
        plt.plot(duties, rises, marker="o", label="rise")
        if plot_fit_results and np.ptp(duties) > 1e-6:
            slope, intercept = np.polyfit(duties, rises, 1)
            duty_fit = np.linspace(np.min(duties), np.max(duties), 100)
            plt.plot(duty_fit, slope * duty_fit + intercept, linestyle="--", label="rise_fit")
        plt.ylabel("Rise (C)")
        plt.xlabel("Duty / Time (s)")
        plt.grid(True, alpha=0.3)
        plt.legend()

        plt.tight_layout()
        plt.show()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
