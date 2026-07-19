"""
imu_temp_identify.py - IMU 加热器温度曲线拟合（按占空比阶段）

用法：
    python scripts/imu_temp_identify.py
    python scripts/imu_temp_identify.py run1.log run2.log
    python scripts/imu_temp_identify.py run1.csv run2.csv --csv
    python scripts/imu_temp_identify.py --port COM5 --baud 115200

输入格式：
    1) Zephyr 日志行：
       seq=123,t_us=456789,dt_us=1000,stage=0,state=4,temp_c=37.520,duty=0.200
    2) CSV 文件，列名如：
       time_s,temp_c,duty,dt_s
       或
       source,stage,time_s,temp_c,duty,dt_s

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
import sys
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np


# 直接修改这里配置串口；命令行 --port/--baud 可临时覆盖。
SERIAL_PORT = "COM21"                   # 默认串口号
SERIAL_BAUD = 921600                    # 串口波特率
ONLINE_AVERAGE_SAMPLES = 10             # 在线模式平均帧数
OUTLIER_SIGMA = 2.5                     # 离群值鲁棒 Z 分数阈值
PRINT_RAW_LOG = False                   # 打印原始日志行

ANSI_RESET = "\033[0m"
ANSI_DIM = "\033[90m"
ANSI_CYAN = "\033[36m"
ANSI_BLUE = "\033[34m"
ANSI_YELLOW = "\033[33m"
ANSI_GREEN = "\033[32m"
ANSI_MAGENTA = "\033[35m"
ANSI_RED = "\033[31m"


def colorize_log(text: str) -> str:
    """按在线辨识日志类型着色；重定向输出时保持纯文本。"""
    if not sys.stdout.isatty():
        return text

    color = ANSI_RESET
    if "[IDENT][EVENT]" in text:
        color = ANSI_YELLOW
    elif "[IDENT][FIT_DONE]" in text or "[IDENT][DONE]" in text:
        color = ANSI_GREEN
    elif "[IDENT][FIT_SKIP]" in text or "[IDENT][ERROR]" in text:
        color = ANSI_RED
    elif "[IDENT][STATE]" in text:
        color = ANSI_MAGENTA
    elif "[IDENT][HEARTBEAT]" in text:
        color = ANSI_BLUE
    elif "[IDENT][STAGE" in text and "[START]" in text:
        color = ANSI_CYAN
    elif "[IDENT][STAGE" in text and "[RUNNING]" in text:
        color = ANSI_DIM
    elif "[IDENT][START]" in text or "[IDENT][RECEIVED]" in text:
        color = ANSI_CYAN

    return f"{color}{text}{ANSI_RESET}"


def ident_print(text: str, *, end: str = "\n", flush: bool = True) -> None:
    print(colorize_log(text), end=end, flush=flush)


CURRENT_SAMPLE_RE = re.compile(
    r"seq=(?P<seq>\d+),"
    r"t_us=(?P<t_us>\d+),"
    r"dt_us=(?P<dt_us>\d+),"
    r"stage=(?P<stage>\d+),"
    r"state=(?P<state>\d+),"
    r"temp_c=(?P<temp>[-+0-9.eE]+),"
    r"duty=(?P<duty>[-+0-9.eE]+)"
)
OPEN_FINISH_RE = re.compile(r"\b(?:Finished|Safety Stop)\b")
OPEN_STAGE_END_RE = re.compile(r"\bStage Done\b")
OPEN_EVENT_RE = re.compile(
    r"\b(?P<event>Cooldown Done|"
    r"Stage Done|Finished|Safety Stop)\b"
)
IDENT_STATE_NAMES = {
    0: "Cooldown",
    1: "Heating",
    2: "SafetyStop",
    3: "Finished",
}
OPEN_HEATING_STATE = 1
START_IDENT_COMMAND = b"StartIdent"
STOP_IDENT_COMMAND = b"Stop"
EXPECTED_STAGE_COUNT = 6
EXPECTED_RUN_COUNT = 6
WARMUP_RUN_COUNT = 1
OPEN_MODE_START_RE = re.compile(r"\bopen_ident_start\b")
IMU_READY_RE = re.compile(r"\bimu ready\b")


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


@dataclass
class FullRun:
    run_index: int
    segments: list[Segment]
    series: Series
    stage: np.ndarray
    fit: dict
    mean_prediction: np.ndarray | None = None
    mean_error: np.ndarray | None = None


def parse_log_sample(raw: str) -> tuple[float, float, float, float] | None:
    match = CURRENT_SAMPLE_RE.search(raw)
    if not match:
        return None

    temp_v = float(match.group("temp"))
    duty_v = float(match.group("duty"))
    dt_v = int(match.group("dt_us")) * 1.0e-6
    timestamp_s = int(match.group("t_us")) * 1.0e-6
    return temp_v, duty_v, dt_v, timestamp_s


def parse_log_timestamp(raw: str) -> float | None:
    sample = CURRENT_SAMPLE_RE.search(raw)
    if sample is None:
        return None
    return int(sample.group("t_us")) * 1.0e-6


def parse_log_segments(lines: Iterable[str], source: str = "") -> list[Segment]:
    """Parse the current Identifier::Update log and keep Heating samples only."""
    segments: list[Segment] = []
    temps: list[float] = []
    duties: list[float] = []
    dts: list[float] = []
    times: list[float] = []
    stage_index: int | None = None
    stage_duty = 0.0
    stage_active = False
    stage_time = 0.0

    def finish_stage() -> None:
        nonlocal temps, duties, dts, times, stage_time, stage_active
        if len(temps) < 3:
            temps = []
            duties = []
            dts = []
            times = []
            stage_time = 0.0
            stage_active = False
            return

        segments.append(
            Segment(
                index=stage_index if stage_index is not None else -1,
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
        stage_active = False

    for raw in lines:
        match = CURRENT_SAMPLE_RE.search(raw)
        if match is None:
            continue

        state = int(match.group("state"))
        if state != OPEN_HEATING_STATE:
            if stage_active:
                finish_stage()
            continue

        current_stage = int(match.group("stage"))
        temp_v = float(match.group("temp"))
        duty_v = float(match.group("duty"))
        dt_v = int(match.group("dt_us")) * 1.0e-6

        if (
            stage_active
            and (
                current_stage != stage_index
                or abs(duty_v - stage_duty) > 1.0e-6
            )
        ):
            finish_stage()

        if not stage_active:
            stage_index = current_stage
            stage_duty = duty_v
            stage_active = True
            stage_time = 0.0

        temps.append(temp_v)
        duties.append(duty_v)
        dts.append(dt_v)
        times.append(stage_time)
        stage_time += dt_v

    if stage_active:
        finish_stage()
    return segments


def parse_csv_file(path: Path) -> list[Segment]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("empty csv")

        fields = {name.strip().lower(): name for name in reader.fieldnames}
        if "temp_c" not in fields or "duty" not in fields:
            raise ValueError("csv needs temp_c and duty columns")
        if "time_s" not in fields and "dt_s" not in fields:
            raise ValueError("csv needs time_s or dt_s column")

        time: list[float] = []
        temp: list[float] = []
        duty: list[float] = []
        dt: list[float] = []
        t = 0.0
        for row in reader:
            temp_v = float(row[fields["temp_c"]])
            duty_v = float(row[fields["duty"]])
            if "time_s" in fields:
                t = float(row[fields["time_s"]])
                dt_v = 0.0 if not time else t - time[-1]
            else:
                dt_v = float(row[fields["dt_s"]])
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


def write_fit_results_csv(
    fit_results: list[tuple[Segment, dict, dict]],
    path: Path,
    common_shape: dict | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    common_by_key = {}
    if common_shape is not None:
        common_by_key = {
            (item["source"], item["stage"]): item
            for item in common_shape["stage_fits"]
        }

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "source",
                "stage",
                "duty",
                "t0_temp_c",
                "tss_temp_c",
                "rise_c",
                "delay_s",
                "tau_s",
                "rmse_c",
                "common_t0_temp_c",
                "common_tss_temp_c",
                "common_rise_c",
                "common_rmse_c",
            ]
        )
        for segment, fit, metrics in fit_results:
            common = common_by_key.get((segment.source, segment.index), {})
            writer.writerow(
                [
                    segment.source,
                    segment.index,
                    f"{segment.duty:.6f}",
                    f"{fit['t0_temp']:.6f}",
                    f"{fit['tss_temp']:.6f}",
                    f"{fit['delta_temp']:.6f}",
                    f"{fit['l']:.6f}",
                    f"{fit['tau']:.6f}",
                    f"{metrics['rmse']:.6f}",
                    f"{common.get('t0_temp', float('nan')):.6f}",
                    f"{common.get('tss_temp', float('nan')):.6f}",
                    f"{common.get('delta_temp', float('nan')):.6f}",
                    f"{common.get('rmse', float('nan')):.6f}",
                ]
            )
    print(f"Saved fit results: {path}")


def write_fit_data_csv(
    fit_results: list[tuple[Segment, dict, dict]],
    path: Path,
    common_shape: dict | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    common_by_key = {}
    if common_shape is not None:
        common_by_key = {
            (item["source"], item["stage"]): item
            for item in common_shape["stage_fits"]
        }

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "source",
                "stage",
                "time_s",
                "temp_c",
                "duty",
                "dt_s",
                "individual_fit_c",
                "common_fit_c",
                "individual_error_c",
                "common_error_c",
            ]
        )
        for segment, fit, _ in fit_results:
            common = common_by_key.get((segment.source, segment.index))
            individual = predict_heating_curve(segment.series.time, fit)
            common_fit = (
                common["y_hat"]
                if common is not None
                else np.full(segment.series.time.shape, np.nan)
            )
            for index in range(segment.series.time.size):
                writer.writerow(
                    [
                        segment.source,
                        segment.index,
                        f"{segment.series.time[index]:.6f}",
                        f"{segment.series.temp[index]:.6f}",
                        f"{segment.series.duty[index]:.6f}",
                        f"{segment.series.dt[index]:.6f}",
                        f"{individual[index]:.6f}",
                        f"{common_fit[index]:.6f}",
                        f"{individual[index] - segment.series.temp[index]:.6f}",
                        f"{common_fit[index] - segment.series.temp[index]:.6f}",
                    ]
                )
    print(f"Saved fit data: {path}")


class OnlineIdentifier:
    def __init__(self) -> None:
        self._pending: list[tuple[float, float, float, float]] = []
        self._current_segment: Segment | None = None
        self._segments: list[Segment] = []
        self._raw_time_s = 0.0
        self._sample_count = 0
        self._wall_start = time.monotonic()
        self._last_state: int | None = None
        self._state_sample_count = 0
        self._last_seq: int | None = None
        self._started = False
        self._armed = False
        self._run_t0_us: int | None = None

    def reset_run(self) -> None:
        self._current_segment = None
        self._segments.clear()
        self._raw_time_s = 0.0
        self._sample_count = 0
        self._last_state = None
        self._state_sample_count = 0
        self._last_seq = None
        self._wall_start = time.monotonic()
        self._started = True
        self._run_t0_us = None

    def arm_command_start(self) -> None:
        self._armed = True
        self._started = False

    def feed(
        self,
        stage_index: int,
        duty: float,
        sample: tuple[float, float, float, float],
    ) -> None:
        if (
            self._current_segment is None
            or self._current_segment.index != stage_index
            or abs(self._current_segment.duty - duty) > 1.0e-6
        ):
            self._finish_current_segment()
            self._current_segment = Segment(
                index=stage_index,
                duty=duty,
                series=Series(
                    time=np.asarray([], dtype=float),
                    temp=np.asarray([], dtype=float),
                    duty=np.asarray([], dtype=float),
                    dt=np.asarray([], dtype=float),
                ),
                source="serial",
            )
            self._raw_time_s = 0.0
            self._sample_count = 0
            ident_print(
                f"\n[{datetime.now():%H:%M:%S}] "
                f"[IDENT][STAGE {stage_index + 1}][START] "
                f"duty={duty:.3f}; collecting temperature response",
                flush=True,
            )

        self._append_raw_sample(sample)

    def _append_raw_sample(
        self,
        sample: tuple[float, float, float, float],
    ) -> None:
        if self._current_segment is None:
            return

        temp_v, duty_v, dt_v, timestamp_s = sample
        timestamp_us = int(round(timestamp_s * 1.0e6))
        if self._run_t0_us is None:
            self._run_t0_us = timestamp_us

        series = self._current_segment.series
        series.time = np.append(
            series.time,
            max(0.0, timestamp_us - self._run_t0_us) * 1.0e-6,
        )
        series.temp = np.append(series.temp, temp_v)
        series.duty = np.append(series.duty, duty_v)
        series.dt = np.append(series.dt, dt_v)
        self._raw_time_s += max(dt_v, 0.0)
        self._sample_count += 1

        if self._sample_count == 1 or self._sample_count % 100 == 0:
            ident_print(
                f"\r[{datetime.now():%H:%M:%S}] "
                f"[IDENT][STAGE {self._current_segment.index + 1}]"
                f"[RUNNING] samples={self._sample_count},"
                f"elapsed={self._raw_time_s:.1f}s,"
                f"temperature={temp_v:.2f}C,"
                f"duty={duty_v:.3f}",
                end="",
                flush=True,
            )

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
        sample_match = CURRENT_SAMPLE_RE.search(raw)
        if sample_match:
            if not self._started:
                if not self._armed:
                    return True, False
                self.reset_run()
                self._armed = False
                ident_print(
                    f"\n[{datetime.now():%H:%M:%S}] "
                    "[IDENT][START] StartIdent acknowledged by first sample; "
                    "new identification started",
                    flush=True,
                )

            state = int(sample_match.group("state"))
            stage = int(sample_match.group("stage"))
            temp = float(sample_match.group("temp"))
            duty = float(sample_match.group("duty"))
            self._state_sample_count += 1

            if state != self._last_state:
                state_name = IDENT_STATE_NAMES.get(state, f"Unknown({state})")
                ident_print(
                    f"\n[{datetime.now():%H:%M:%S}] "
                    f"[IDENT][STATE] stage={stage + 1},"
                    f"state={state_name},temp={temp:.2f}C,duty={duty:.3f}",
                    flush=True,
                )
                if state == OPEN_HEATING_STATE:
                    ident_print(
                        f"[{datetime.now():%H:%M:%S}] "
                        f"[IDENT][STAGE {stage + 1}/{EXPECTED_STAGE_COUNT}]"
                        f"[HEATING_START] duty applied; collecting response immediately,"
                        f"duty={duty:.3f}",
                        flush=True,
                    )
                self._last_state = state

            if state != OPEN_HEATING_STATE and (
                self._state_sample_count == 1
                or self._state_sample_count % 100 == 0
            ):
                state_name = IDENT_STATE_NAMES.get(state, f"Unknown({state})")
                ident_print(
                    f"\r[{datetime.now():%H:%M:%S}] "
                    f"[IDENT][{state_name}] stage={stage + 1},"
                    f"samples={self._state_sample_count},"
                    f"temp={temp:.2f}C,duty={duty:.3f}",
                    end="\n",
                    flush=True,
                )

            if state == OPEN_HEATING_STATE:
                parsed = parse_log_sample(raw)
                if parsed is not None:
                    self.feed(
                        stage,
                        duty,
                        parsed,
                    )
            elif state != OPEN_HEATING_STATE:
                self._finish_current_segment()
            return True, False

        event_match = OPEN_EVENT_RE.search(raw)
        if event_match:
            if not self._started:
                return True, False

            event = event_match.group("event")
            ident_print(
                f"\n[{datetime.now():%H:%M:%S}] "
                f"[IDENT][EVENT] {event}",
                flush=True,
            )

        if OPEN_STAGE_END_RE.search(raw):
            ident_print(
                f"[{datetime.now():%H:%M:%S}] "
                "[IDENT][ACTION] captured completed Heating stage; "
                "full-run fit waits for all 6 stages",
                flush=True,
            )
            self._finish_current_segment()
            return True, False

        if OPEN_FINISH_RE.search(raw):
            self.flush_pending()
            self._finish_current_segment()
            ident_print(
                f"[{datetime.now():%H:%M:%S}] "
                "[IDENT][DONE] identification stopped",
                flush=True,
            )
            return True, True

        return False, False

    def _finish_current_segment(self) -> None:
        if self._current_segment is None:
            return
        if self._current_segment.series.time.size >= 3:
            self._segments.append(self._current_segment)
            ident_print(
                f"[{datetime.now():%H:%M:%S}] "
                f"[IDENT][SEGMENT_CAPTURED] "
                f"stage={self._current_segment.index},"
                f"samples={self._current_segment.series.time.size},"
                f"duty={self._current_segment.duty:.3f}",
                flush=True,
            )
        self._current_segment = None
        self._raw_time_s = 0.0
        self._sample_count = 0

    def segments(self) -> list[Segment]:
        return list(self._segments)


def read_serial_online(
    port: str,
    baud: int,
    *,
    wait_boot_flags: bool = True,
    run_index: int = 1,
) -> list[Segment]:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("serial mode requires pyserial: pip install pyserial") from exc

    online = OnlineIdentifier()
    ident_print(
        "[IDENT][COMMAND] sends StartIdent to MCU without a line ending"
    )
    ident_print(
        "[IDENT][WAITING] first sample after StartIdent starts this run"
    )
    ident_print(
        "[IDENT][STOP] sends Stop after Finished/Safety Stop or Ctrl+C"
    )
    ident_print(
        f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
        f"[IDENT][READY] serial={port},baud={baud}"
    )
    ident_print(
        "[IDENT][WAITING] waiting for current IMU logs: "
        "seq,t_us,dt_us,stage,state,temp_c,duty"
    )
    ident_print(
        "[IDENT][STOP] stops on event=finished/event=safety_stop "
        "or Ctrl+C"
    )
    ident_print(
        f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
        f"串口已打开: {port} @ {baud} baud"
    )
    ident_print(
        f"[IDENT][RUN {run_index}/{EXPECTED_RUN_COUNT}] "
        "waiting for open_ident_start and imu ready before StartIdent"
        if wait_boot_flags
        else f"[IDENT][RUN {run_index}/{EXPECTED_RUN_COUNT}] "
        "boot flags already received; preparing StartIdent"
    )
    ident_print("[IDENT][STOP] 收到 event=finished/event=safety_stop 或按 Ctrl+C 停止。")
    wait_start = time.monotonic()
    last_wait_notice = wait_start - 5.0
    first_sample_seen = False
    status_start = time.monotonic()
    last_status = status_start - 5.0
    total_lines = 0
    sample_lines = 0
    active_samples = 0
    open_mode_seen = not wait_boot_flags
    imu_ready_seen = not wait_boot_flags
    command_sent = False

    try:
        with serial.Serial(port, baudrate=baud, timeout=0.5) as ser:
            if not wait_boot_flags:
                ser.write(START_IDENT_COMMAND)
                ser.flush()
                online.arm_command_start()
                command_sent = True
                ident_print(
                    f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                    f"[IDENT][RUN {run_index}/{EXPECTED_RUN_COUNT}]"
                    "[COMMAND_SENT] StartIdent",
                    flush=True,
                )
            while True:
                raw = ser.readline()
                if not raw:
                    now = time.monotonic()
                    if now - last_status >= 5.0:
                        elapsed = now - status_start
                        if not command_sent:
                            ident_print(
                                f"\n[{datetime.now():%H:%M:%S}] "
                                f"[IDENT][RUN {run_index}/{EXPECTED_RUN_COUNT}]"
                                "[HEARTBEAT][WAITING_START] "
                                f"elapsed={elapsed:.0f}s,lines={total_lines},"
                                f"mode_start={open_mode_seen},"
                                f"imu_ready={imu_ready_seen}; "
                                "waiting for both boot flags",
                                flush=True,
                            )
                        else:
                            ident_print(
                                f"\n[{datetime.now():%H:%M:%S}] "
                                f"[IDENT][HEARTBEAT][NO_NEW_DATA] "
                                f"elapsed={elapsed:.0f}s,active_samples={active_samples}; "
                                "serial opened but no new bytes received",
                                flush=True,
                            )
                        last_status = now
                        last_wait_notice = now
                    if False and now - last_wait_notice >= 5.0:
                        print(
                            f"\n[{datetime.now():%H:%M:%S}] "
                            f"状态: 尚未收到辨识日志，已等待 "
                            f"{now - wait_start:.0f}s",
                            flush=True,
                        )
                        last_wait_notice = now
                    continue
                line = raw.decode(errors="replace").rstrip("\r\n")
                if PRINT_RAW_LOG:
                    ident_print(f"[IDENT][RAW] {line}", flush=True)
                total_lines += 1

                if OPEN_MODE_START_RE.search(line) and not open_mode_seen:
                    open_mode_seen = True
                    ident_print(
                        f"[{datetime.now():%H:%M:%S}] "
                        f"[IDENT][RUN {run_index}/{EXPECTED_RUN_COUNT}]"
                        "[BOOT] received open_ident_start",
                        flush=True,
                    )
                if IMU_READY_RE.search(line) and not imu_ready_seen:
                    imu_ready_seen = True
                    ident_print(
                        f"[{datetime.now():%H:%M:%S}] "
                        f"[IDENT][RUN {run_index}/{EXPECTED_RUN_COUNT}]"
                        "[BOOT] received imu ready",
                        flush=True,
                    )

                if not command_sent and open_mode_seen and imu_ready_seen:
                    ser.write(START_IDENT_COMMAND)
                    ser.flush()
                    online.arm_command_start()
                    command_sent = True
                    status_start = time.monotonic()
                    ident_print(
                        f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                        f"[IDENT][RUN {run_index}/{EXPECTED_RUN_COUNT}]"
                        "[COMMAND_SENT] StartIdent",
                        flush=True,
                    )
                    ident_print(
                        f"[{datetime.now():%H:%M:%S}] "
                        "[IDENT][START] MCU starts open-loop capture immediately",
                        flush=True,
                    )

                if not command_sent:
                    continue

                sample_match = CURRENT_SAMPLE_RE.search(line)
                if sample_match:
                    sample_lines += 1
                    if not first_sample_seen:
                        ident_print(
                            f"\n[{datetime.now():%H:%M:%S}] "
                            +
                            (
                                "[IDENT][RECEIVED_BUT_IGNORED] sample received "
                                "before StartIdent was acknowledged"
                                if not online._started
                                else "[IDENT][RECEIVED] first active identification sample"
                            ),
                            flush=True,
                        )
                        first_sample_seen = True
                        last_wait_notice = wait_start - 5.0
                    if False and last_wait_notice != wait_start - 5.0:
                        ident_print(
                            f"[{datetime.now():%H:%M:%S}] "
                            f"状态: 已收到 IMU 辨识首帧，等待提示结束。",
                            flush=True,
                        )
                    last_wait_notice = wait_start - 5.0
                _, should_stop = online.handle_event(line)
                if should_stop:
                    ser.write(STOP_IDENT_COMMAND)
                    ser.flush()
                    ident_print(
                        f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                        f"[IDENT][RUN {run_index}/{EXPECTED_RUN_COUNT}]"
                        "[COMMAND_SENT] Stop",
                        flush=True,
                    )
                    break

                now = time.monotonic()
                if online._started and sample_match:
                    active_samples += 1
                if now - last_status >= 5.0:
                    elapsed = now - status_start
                    if not online._started:
                        ident_print(
                            f"[{datetime.now():%H:%M:%S}] "
                            f"[IDENT][HEARTBEAT][WAITING_START] "
                            f"elapsed={elapsed:.0f}s,lines={total_lines},"
                            f"samples_seen={sample_lines}; "
                            "waiting for first sample after StartIdent",
                            flush=True,
                        )
                    elif not sample_match:
                        ident_print(
                            f"[{datetime.now():%H:%M:%S}] "
                            f"[IDENT][HEARTBEAT][NO_NEW_SAMPLE] "
                            f"elapsed={elapsed:.0f}s,active_samples={active_samples}; "
                            "identification started but no sample in this interval",
                            flush=True,
                        )
                    last_status = now
    except KeyboardInterrupt:
        try:
            ser.write(STOP_IDENT_COMMAND)
            ser.flush()
            ident_print(
                f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                "[IDENT][COMMAND_SENT] Stop",
                flush=True,
            )
        except (NameError, AttributeError, OSError):
            pass
        print("\nSerial capture stopped.")
        online._finish_current_segment()

    segments = online.segments()
    if not segments:
        raise ValueError("no current IMU identification Heating samples captured")
    if len(segments) != EXPECTED_STAGE_COUNT:
        ident_print(
            f"[{datetime.now():%H:%M:%S}] "
            f"[IDENT][WARNING] expected {EXPECTED_STAGE_COUNT} Heating stages, "
            f"captured {len(segments)}",
            flush=True,
        )
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


def fit_one_open_loop_run(
    segments: list[Segment],
    run_index: int,
) -> tuple[list[tuple[Segment, dict, dict]], dict]:
    if len(segments) != EXPECTED_STAGE_COUNT:
        raise ValueError(
            f"run {run_index} contains {len(segments)} stages; "
            f"expected {EXPECTED_STAGE_COUNT}"
        )

    fit_results: list[tuple[Segment, dict, dict]] = []
    for segment in segments:
        fit = fit_heating_curve(segment.series)
        prediction = predict_heating_curve(segment.series.time, fit)
        metrics = calc_error_metrics(segment.series.temp, prediction)
        fit_results.append((segment, fit, metrics))

    common_shape = fit_common_shape(fit_results)
    error_values = []
    for segment_fit in common_shape["stage_fits"]:
        error_values.extend(
            segment_fit["y_hat"] - next(
                segment.series.temp
                for segment, _, _ in fit_results
                if segment.source == segment_fit["source"]
                and segment.index == segment_fit["stage"]
            )
        )
    error_array = np.asarray(error_values, dtype=float)
    run_rmse = float(np.sqrt(np.mean(error_array * error_array)))
    run_rise = float(
        np.mean([item["delta_temp"] for item in common_shape["stage_fits"]])
    )
    common_shape["joint_rmse"] = run_rmse
    common_shape["rise_mean"] = run_rise

    ident_print(
        f"[{datetime.now():%H:%M:%S}] "
        f"[IDENT][RUN {run_index}/{EXPECTED_RUN_COUNT}][FIT_DONE] "
        f"stages={len(segments)},"
        f"tau={common_shape['tau']:.3f}s,"
        f"delay={common_shape['l']:.3f}s,"
        f"rise_mean={run_rise:.3f}C,"
        f"RMSE={run_rmse:.3f}C",
        flush=True,
    )
    return fit_results, common_shape


def plot_run_response(
    fit_results: list[tuple[Segment, dict, dict]],
    common_shape: dict,
    path: Path,
    run_index: int,
) -> None:
    import matplotlib.pyplot as plt

    common_by_stage = {
        item["stage"]: item for item in common_shape["stage_fits"]
    }
    figure, axes = plt.subplots(2, 3, figsize=(14, 8), squeeze=False)
    for axis, (segment, fit, _) in zip(axes.ravel(), fit_results):
        common = common_by_stage[segment.index]
        axis.plot(segment.series.time, segment.series.temp, label="measured")
        axis.plot(
            segment.series.time,
            predict_heating_curve(segment.series.time, fit),
            "--",
            label="individual fit",
        )
        axis.plot(
            segment.series.time,
            common["y_hat"],
            ":",
            linewidth=2.0,
            label="run common fit",
        )
        axis.set_title(f"Stage {segment.index}, duty={segment.duty:.3f}")
        axis.set_xlabel("Time (s)")
        axis.set_ylabel("Temperature (C)")
        axis.grid(True, alpha=0.3)
        axis.legend(fontsize=8)
    figure.suptitle(
        f"IMU Open-Loop Fit Run {run_index}/{EXPECTED_RUN_COUNT} "
        f"(tau={common_shape['tau']:.3f}s, "
        f"delay={common_shape['l']:.3f}s)"
    )
    figure.tight_layout()
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    print(f"Saved run plot: {path}")


def plot_run_parameter_summary(
    run_results: list[dict],
    path: Path,
) -> None:
    import matplotlib.pyplot as plt

    run_ids = np.asarray([item["run_index"] for item in run_results], dtype=float)
    taus = np.asarray([item["tau"] for item in run_results], dtype=float)
    delays = np.asarray([item["delay"] for item in run_results], dtype=float)
    rises = np.asarray([item["rise_mean"] for item in run_results], dtype=float)
    rmses = np.asarray([item["rmse"] for item in run_results], dtype=float)

    figure, axes = plt.subplots(2, 2, figsize=(12, 8), squeeze=False)
    series = [
        (axes[0, 0], taus, "tau (s)", "tau"),
        (axes[0, 1], delays, "delay (s)", "delay"),
        (axes[1, 0], rises, "mean rise (C)", "rise"),
        (axes[1, 1], rmses, "RMSE (C)", "RMSE"),
    ]
    for axis, values, ylabel, label in series:
        axis.plot(run_ids, values, "o-", label=label)
        axis.axhline(
            float(np.mean(values)),
            color="tab:red",
            linestyle="--",
            label=f"mean={np.mean(values):.3f}",
        )
        axis.set_xlabel("Complete identification run")
        axis.set_ylabel(ylabel)
        axis.set_xticks(run_ids)
        axis.grid(True, alpha=0.3)
        axis.legend()
    figure.suptitle("Six Open-Loop Identification Runs and Parameter Means")
    figure.tight_layout()
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    print(f"Saved parameter summary: {path}")


def concatenate_run_segments(segments: list[Segment], run_index: int) -> FullRun:
    """把一轮中的 6 个 Heating 阶段拼成一条连续的输入/温度轨迹。"""
    ordered = sorted(segments, key=lambda item: item.index)
    if len(ordered) != EXPECTED_STAGE_COUNT:
        raise ValueError(
            f"run {run_index} contains {len(ordered)} stages; "
            f"expected {EXPECTED_STAGE_COUNT}"
        )

    time_parts: list[np.ndarray] = []
    temp_parts: list[np.ndarray] = []
    duty_parts: list[np.ndarray] = []
    dt_parts: list[np.ndarray] = []
    stage_parts: list[np.ndarray] = []
    time_offset = 0.0

    for segment_index, segment in enumerate(ordered):
        series = segment.series
        if series.time.size < 3:
            raise ValueError(
                f"run {run_index} stage {segment.index} has too few samples"
            )

        # 每轮可能因串口重连或 MCU 重启导致 t_us 回零，因此用每帧
        # dt_us 重建阶段相对时间，避免第二轮出现 duration=0。
        sample_steps = np.asarray(series.dt, dtype=float)
        sample_steps = np.where(
            np.isfinite(sample_steps) & (sample_steps > 1.0e-6),
            sample_steps,
            0.001,
        )
        local_time = np.r_[0.0, np.cumsum(sample_steps[:-1])]
        if time_parts:
            previous_time = time_parts[-1][-1]
            sample_dt = float(np.median(series.dt[series.dt > 0.0]))
            if not np.isfinite(sample_dt) or sample_dt <= 0.0:
                sample_dt = 0.001
            time_offset = previous_time + sample_dt

        time_parts.append(local_time + time_offset)
        temp_parts.append(np.asarray(series.temp, dtype=float))
        duty_parts.append(np.asarray(series.duty, dtype=float))
        dt_parts.append(np.asarray(series.dt, dtype=float))
        stage_parts.append(
            np.full(series.time.size, segment.index, dtype=int)
        )

    time = np.concatenate(time_parts)
    temp = np.concatenate(temp_parts)
    duty = np.concatenate(duty_parts)
    dt = np.concatenate(dt_parts)
    stage = np.concatenate(stage_parts)

    keep = np.ones(time.size, dtype=bool)
    keep[1:] = np.diff(time) > 1.0e-9
    series = Series(
        time=time[keep],
        temp=temp[keep],
        duty=duty[keep],
        dt=dt[keep],
    )
    return FullRun(
        run_index=run_index,
        segments=ordered,
        series=series,
        stage=stage[keep],
        fit={},
    )


def delayed_duty(time: np.ndarray, duty: np.ndarray, delay_s: float) -> np.ndarray:
    """按实际占空比轨迹取延迟输入，保持阶跃输入而不是插值温度。"""
    query = time[1:] - delay_s
    indexes = np.searchsorted(time, query, side="right") - 1
    indexes = np.clip(indexes, 0, duty.size - 1)
    return duty[indexes]


def simulate_open_loop_plant(
    series: Series,
    gain: float,
    tau_s: float,
    delay_s: float,
    ambient_c: float,
) -> np.ndarray:
    prediction = np.empty_like(series.temp)
    prediction[0] = series.temp[0]
    dt_s = np.diff(series.time)
    input_delayed = delayed_duty(series.time, series.duty, delay_s)

    for index, dt_s_value in enumerate(dt_s, start=1):
        alpha = float(np.exp(-dt_s_value / tau_s))
        equilibrium = ambient_c + gain * input_delayed[index - 1]
        prediction[index] = (
            alpha * prediction[index - 1]
            + (1.0 - alpha) * equilibrium
        )
    return prediction


def reduce_series_for_fit(series: Series, max_samples: int = 3000) -> Series:
    """只压缩网格搜索输入，最终误差仍使用完整原始轨迹计算。"""
    if series.time.size <= max_samples:
        return series

    indexes = np.linspace(0, series.time.size - 1, max_samples, dtype=int)
    duty_changes = np.flatnonzero(
        np.abs(np.diff(series.duty)) > 1.0e-6
    ) + 1
    indexes = np.unique(np.concatenate((indexes, duty_changes)))
    return Series(
        time=series.time[indexes],
        temp=series.temp[indexes],
        duty=series.duty[indexes],
        dt=np.diff(
            np.r_[series.time[indexes[0]], series.time[indexes]]
        ),
    )


def fit_full_open_loop_run(run: FullRun) -> dict:
    """用一轮完整的 6 阶段轨迹拟合一个 FOPDT 开环模型。"""
    series = run.series
    if series.time.size < 20:
        raise ValueError(f"run {run.run_index} has too few samples")

    duration_s = float(series.time[-1] - series.time[0])
    if duration_s <= 0.0:
        raise ValueError(f"run {run.run_index} duration is not positive")

    fit_series = reduce_series_for_fit(series)
    max_delay = min(5.0, max(0.5, duration_s * 0.20))
    delay_grid = np.linspace(0.0, max_delay, 41)
    tau_grid = np.linspace(
        max(0.5, duration_s * 0.01),
        max(2.0, min(80.0, duration_s * 1.5)),
        100,
    )
    ident_print(
        f"[{datetime.now():%H:%M:%S}] "
        f"[IDENT][RUN {run.run_index}/{EXPECTED_RUN_COUNT}][FIT_SEARCH] "
        f"full_samples={series.time.size},fit_samples={fit_series.time.size},"
        f"grid={delay_grid.size}x{tau_grid.size}",
        flush=True,
    )
    dt_s = np.diff(fit_series.time)
    valid_dt = dt_s > 1.0e-6
    if not np.all(valid_dt):
        raise ValueError(f"run {run.run_index} contains invalid timestamps")

    best: dict | None = None
    for delay_index, delay_s in enumerate(delay_grid, start=1):
        input_delayed = delayed_duty(
            fit_series.time,
            fit_series.duty,
            float(delay_s),
        )
        for tau_s in tau_grid:
            alpha = np.exp(-dt_s / tau_s)
            beta = 1.0 - alpha
            equivalent_temp = (
                fit_series.temp[1:] - alpha * fit_series.temp[:-1]
            ) / beta
            matrix = np.column_stack(
                (np.ones_like(input_delayed), input_delayed)
            )
            ambient_c, gain = np.linalg.lstsq(
                matrix,
                equivalent_temp,
                rcond=None,
            )[0]
            if gain <= 0.0:
                continue

            search_prediction = simulate_open_loop_plant(
                fit_series,
                float(gain),
                float(tau_s),
                float(delay_s),
                float(ambient_c),
            )
            metrics = calc_error_metrics(fit_series.temp, search_prediction)
            if best is None or metrics["rmse"] < best["rmse"]:
                best = {
                    "gain": float(gain),
                    "tau_s": float(tau_s),
                    "delay_s": float(delay_s),
                    "ambient_c": float(ambient_c),
                    "search_prediction": search_prediction,
                    **metrics,
                }

        if delay_index == 1 or delay_index % 5 == 0 or delay_index == delay_grid.size:
            best_text = (
                f"{best['rmse']:.4f}C"
                if best is not None
                else "n/a"
            )
            ident_print(
                f"[{datetime.now():%H:%M:%S}] "
                f"[IDENT][RUN {run.run_index}/{EXPECTED_RUN_COUNT}]"
                f"[FIT_SEARCH][PROGRESS] "
                f"delay_grid={delay_index}/{delay_grid.size},"
                f"best_RMSE={best_text}",
                flush=True,
            )

    if best is None:
        raise ValueError(f"failed to fit run {run.run_index}")

    # 搜索阶段使用抽样数据；最终结果和图形必须回放完整原始轨迹。
    best["prediction"] = simulate_open_loop_plant(
        series,
        best["gain"],
        best["tau_s"],
        best["delay_s"],
        best["ambient_c"],
    )
    full_metrics = calc_error_metrics(series.temp, best["prediction"])
    best.update(full_metrics)
    best["duration_s"] = duration_s
    best["samples"] = int(series.time.size)
    best["fit_samples"] = int(fit_series.time.size)
    return best


def fit_joint_open_loop_runs(runs: list[FullRun]) -> dict:
    """六轮共用动态参数，每轮单独拟合温度偏置。"""
    if len(runs) < 2:
        raise ValueError("joint fitting needs at least two complete runs")

    fit_series = [reduce_series_for_fit(run.series, 1800) for run in runs]
    tau_values = np.asarray(
        [run.fit["tau_s"] for run in runs],
        dtype=float,
    )
    delay_values = np.asarray(
        [run.fit["delay_s"] for run in runs],
        dtype=float,
    )
    tau_min = max(0.5, float(np.min(tau_values) * 0.65))
    tau_max = max(tau_min + 1.0, float(np.max(tau_values) * 1.35))
    delay_max = min(3.0, max(0.5, float(np.max(delay_values) * 1.5 + 0.25)))
    tau_grid = np.linspace(tau_min, tau_max, 100)
    delay_grid = np.linspace(0.0, delay_max, 61)

    ident_print(
        f"[{datetime.now():%H:%M:%S}] "
        "[IDENT][JOINT_FIT][START] "
        f"runs={len(runs)},grid={delay_grid.size}x{tau_grid.size},"
        f"fit_samples_per_run<=1800",
        flush=True,
    )

    best: dict | None = None
    for delay_index, delay_s in enumerate(delay_grid, start=1):
        for tau_s in tau_grid:
            z_parts: list[np.ndarray] = []
            u_parts: list[np.ndarray] = []
            for series in fit_series:
                dt_s = np.diff(series.time)
                alpha = np.exp(-dt_s / tau_s)
                beta = 1.0 - alpha
                z = (series.temp[1:] - alpha * series.temp[:-1]) / beta
                u = delayed_duty(series.time, series.duty, float(delay_s))
                z_parts.append(z)
                u_parts.append(u)

            z_all = np.concatenate(z_parts)
            u_all = np.concatenate(u_parts)
            means_z = np.asarray([np.mean(z) for z in z_parts])
            means_u = np.asarray([np.mean(u) for u in u_parts])
            centered_z = np.concatenate(
                [z - mean for z, mean in zip(z_parts, means_z)]
            )
            centered_u = np.concatenate(
                [u - mean for u, mean in zip(u_parts, means_u)]
            )
            denominator = float(np.dot(centered_u, centered_u))
            if denominator <= 1.0e-12:
                continue

            gain = float(np.dot(centered_u, centered_z) / denominator)
            if gain <= 0.0:
                continue
            ambient_values = means_z - gain * means_u

            total_sse = 0.0
            for run, series, ambient_c in zip(
                runs,
                fit_series,
                ambient_values,
            ):
                prediction = simulate_open_loop_plant(
                    series,
                    gain,
                    float(tau_s),
                    float(delay_s),
                    float(ambient_c),
                )
                total_sse += float(
                    np.sum((prediction - series.temp) ** 2)
                )

            if best is None or total_sse < best["sse"]:
                best = {
                    "gain": gain,
                    "tau_s": float(tau_s),
                    "delay_s": float(delay_s),
                    "ambient_c_by_run": ambient_values.tolist(),
                    "sse": total_sse,
                }

        if delay_index == 1 or delay_index % 10 == 0 or delay_index == delay_grid.size:
            best_text = (
                f"{best['sse']:.3f}"
                if best is not None
                else "n/a"
            )
            ident_print(
                f"[{datetime.now():%H:%M:%S}] "
                "[IDENT][JOINT_FIT][PROGRESS] "
                f"delay_grid={delay_index}/{delay_grid.size},"
                f"best_SSE={best_text}",
                flush=True,
            )

    if best is None:
        raise ValueError("joint fit failed")

    predictions: list[np.ndarray] = []
    metrics: list[dict] = []
    for run, ambient_c in zip(runs, best["ambient_c_by_run"]):
        prediction = simulate_open_loop_plant(
            run.series,
            best["gain"],
            best["tau_s"],
            best["delay_s"],
            float(ambient_c),
        )
        predictions.append(prediction)
        metrics.append(calc_error_metrics(run.series.temp, prediction))

    best["prediction_by_run"] = predictions
    best["metrics_by_run"] = metrics
    best["ambient_c"] = float(np.mean(best["ambient_c_by_run"]))
    best["rmse"] = float(np.mean([item["rmse"] for item in metrics]))
    best["mae"] = float(np.mean([item["mae"] for item in metrics]))
    best["max_error"] = float(
        np.mean([item["max_error"] for item in metrics])
    )
    ident_print(
        f"[{datetime.now():%H:%M:%S}] [IDENT][JOINT_FIT][DONE] "
        f"K={best['gain']:.6f}C/duty,"
        f"tau={best['tau_s']:.3f}s,"
        f"delay={best['delay_s']:.3f}s,"
        f"RMSE={best['rmse']:.4f}C,"
        f"MAE={best['mae']:.4f}C",
        flush=True,
    )
    return best


def write_full_run_results_csv(
    run_results: list[dict],
    path: Path,
    mean_fit: dict,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "run",
                "samples",
                "fit_samples",
                "duration_s",
                "gain_c_per_duty",
                "tau_s",
                "delay_s",
                "ambient_c",
                "rmse_c",
                "mae_c",
                "max_error_c",
            ]
        )
        for item in run_results:
            fit = item["fit"]
            writer.writerow(
                [
                    item["run_index"],
                    fit["samples"],
                    fit["fit_samples"],
                    f"{fit['duration_s']:.6f}",
                    f"{fit['gain']:.9f}",
                    f"{fit['tau_s']:.9f}",
                    f"{fit['delay_s']:.9f}",
                    f"{fit['ambient_c']:.9f}",
                    f"{fit['rmse']:.9f}",
                    f"{fit['mae']:.9f}",
                    f"{fit['max_error']:.9f}",
                ]
            )
        writer.writerow(
            [
                "mean",
                "",
                "",
                f"{mean_fit['gain']:.9f}",
                f"{mean_fit['tau_s']:.9f}",
                f"{mean_fit['delay_s']:.9f}",
                f"{mean_fit['ambient_c']:.9f}",
                "",
                "",
                "",
            ]
        )
    print(f"Saved full-run fit results: {path}")


def write_full_run_data_csv(runs: list[FullRun], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "run",
                "stage",
                "time_s",
                "temp_c",
                "duty",
                "fit_c",
                "mean_fit_c",
                "fit_error_c",
                "mean_error_c",
            ]
        )
        for run in runs:
            fit_prediction = run.fit["prediction"]
            mean_prediction = (
                run.mean_prediction
                if run.mean_prediction is not None
                else np.full(run.series.time.size, np.nan)
            )
            for index in range(run.series.time.size):
                writer.writerow(
                    [
                        run.run_index,
                        int(run.stage[index]),
                        f"{run.series.time[index]:.6f}",
                        f"{run.series.temp[index]:.6f}",
                        f"{run.series.duty[index]:.6f}",
                        f"{fit_prediction[index]:.6f}",
                        f"{mean_prediction[index]:.6f}",
                        f"{fit_prediction[index] - run.series.temp[index]:.6f}",
                        f"{mean_prediction[index] - run.series.temp[index]:.6f}",
                    ]
                )
    print(f"Saved full-run fit data: {path}")


def plot_six_run_comparison(
    runs: list[FullRun],
    mean_fit: dict,
    path: Path,
    excluded_run_count: int = 0,
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(3, 1, figsize=(15, 12), sharex=False)
    colors = plt.get_cmap("tab10")(np.linspace(0.0, 1.0, len(runs)))
    common_end = min(float(run.series.time[-1]) for run in runs)
    common_time = np.linspace(0.0, common_end, 1200)
    measured_stack: list[np.ndarray] = []
    mean_model_stack: list[np.ndarray] = []

    for color, run in zip(colors, runs):
        label = f"run {run.run_index}"
        if run.run_index <= excluded_run_count:
            run_ambient = float(mean_fit["ambient_c"])
        else:
            formal_index = run.run_index - excluded_run_count - 1
            run_ambient = float(
                mean_fit["ambient_c_by_run"][formal_index]
            )
        axes[0].plot(
            run.series.time,
            run.series.temp,
            color=color,
            linewidth=1.2,
            label=f"{label} measured",
        )
        axes[0].plot(
            run.series.time,
            run.fit["prediction"],
            color=color,
            linestyle="--",
            linewidth=1.0,
            label=f"{label} fit",
        )
        run.mean_prediction = simulate_open_loop_plant(
            run.series,
            mean_fit["gain"],
            mean_fit["tau_s"],
            mean_fit["delay_s"],
            run_ambient,
        )
        run.mean_error = run.mean_prediction - run.series.temp
        measured_stack.append(
            np.interp(common_time, run.series.time, run.series.temp)
        )
        mean_model_stack.append(
            np.interp(common_time, run.series.time, run.mean_prediction)
        )
        axes[0].plot(
            run.series.time,
            run.mean_prediction,
            color=color,
            linestyle=":",
            linewidth=1.8,
            alpha=0.9,
            label=f"{label} joint model",
        )
        axes[1].plot(
            run.series.time,
            run.series.duty,
            color=color,
            linewidth=1.0,
            label=label,
        )
        axes[2].plot(
            run.series.time,
            run.mean_error,
            color=color,
            linewidth=1.0,
            label=f"{label} joint-model error",
        )

    measured_mean = np.mean(np.vstack(measured_stack), axis=0)
    model_mean = np.mean(np.vstack(mean_model_stack), axis=0)
    axes[0].plot(
        common_time,
        measured_mean,
        color="black",
        linewidth=3.0,
        label="six-run measured mean",
    )
    axes[0].plot(
        common_time,
        model_mean,
        color="black",
        linestyle="-.",
        linewidth=2.5,
        label="six-run joint-model mean",
    )

    axes[0].set_ylabel("Temperature (C)")
    axes[0].set_title(
        "IMU Open-Loop: Runs, Individual Fits, and Joint Aggregate"
        + (
            f" (first {excluded_run_count} warm-up run excluded)"
            if excluded_run_count > 0
            else ""
        )
    )
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(ncol=3, fontsize=8)

    axes[1].set_ylabel("Duty")
    axes[1].set_title("Actual MCU Duty Input")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(ncol=3, fontsize=8)

    axes[2].axhline(0.0, color="black", linewidth=0.8)
    axes[2].set_xlabel("Time from complete run start (s)")
    axes[2].set_ylabel("Mean-model error (C)")
    axes[2].set_title("Error of Six-Run Joint Transfer Function")
    axes[2].grid(True, alpha=0.3)
    axes[2].legend(ncol=2, fontsize=8)

    figure.text(
        0.5,
        0.01,
        (
            "Joint model: "
            f"G(s) = {mean_fit['gain']:.3f} / "
            f"({mean_fit['tau_s']:.3f}s + 1) "
            f"* exp(-{mean_fit['delay_s']:.3f}s), "
            "per-run temperature offsets"
        ),
        ha="center",
        fontsize=10,
    )
    figure.tight_layout(rect=(0.0, 0.03, 1.0, 1.0))
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    print(f"Saved six-run comparison plot: {path}")


def plot_full_run_parameter_summary(
    run_results: list[dict],
    mean_fit: dict,
    path: Path,
    excluded_run_count: int = 0,
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    run_ids = np.asarray([item["run_index"] for item in run_results])
    values = [
        (
            "gain (C/duty)",
            np.asarray([item["fit"]["gain"] for item in run_results]),
            mean_fit["gain"],
        ),
        (
            "tau (s)",
            np.asarray([item["fit"]["tau_s"] for item in run_results]),
            mean_fit["tau_s"],
        ),
        (
            "delay (s)",
            np.asarray([item["fit"]["delay_s"] for item in run_results]),
            mean_fit["delay_s"],
        ),
        (
            "ambient (C)",
            np.asarray([item["fit"]["ambient_c"] for item in run_results]),
            mean_fit["ambient_c"],
        ),
        (
            "RMSE (C)",
            np.asarray([item["fit"]["rmse"] for item in run_results]),
            float(np.mean([item["fit"]["rmse"] for item in run_results])),
        ),
    ]
    figure, axes = plt.subplots(2, 3, figsize=(14, 8), squeeze=False)
    for axis, (title, data, mean_value) in zip(axes.ravel(), values):
        axis.plot(run_ids, data, "o-", label="individual run")
        axis.axhline(
            mean_value,
            color="tab:red",
            linestyle="--",
            label=f"joint={mean_value:.3f}",
        )
        axis.set_title(title)
        axis.set_xlabel("Complete run")
        axis.set_xticks(run_ids)
        axis.grid(True, alpha=0.3)
        axis.legend(fontsize=8)
    axes[1, 2].set_visible(False)
    figure.suptitle(
        "Open-Loop Fits and Joint Dynamic Parameters"
        + (
            f" (first {excluded_run_count} warm-up run excluded)"
            if excluded_run_count > 0
            else ""
        )
    )
    figure.tight_layout()
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    print(f"Saved full-run parameter summary: {path}")


def run_online_identification(
    port: str,
    baud: int,
    result_prefix: Path,
) -> int:
    runs: list[FullRun] = []
    run_results: list[dict] = []

    for run_index in range(1, EXPECTED_RUN_COUNT + 1):
        ident_print(
            f"\n[{datetime.now():%H:%M:%S}] "
            f"[IDENT][RUN {run_index}/{EXPECTED_RUN_COUNT}][WAITING] "
            "waiting for one complete 6-stage open-loop run",
            flush=True,
        )
        segments = read_serial_online(
            port,
            baud,
            wait_boot_flags=(run_index == 1),
            run_index=run_index,
        )
        for segment in segments:
            segment.source = f"run_{run_index}"

        run = concatenate_run_segments(segments, run_index)
        run.fit = fit_full_open_loop_run(run)
        runs.append(run)
        fit = run.fit
        run_results.append(
            {
                "run_index": run_index,
                "fit": fit,
            }
        )
        ident_print(
            f"[{datetime.now():%H:%M:%S}] "
            f"[IDENT][RUN {run_index}/{EXPECTED_RUN_COUNT}][FIT_DONE] "
            f"stages={len(segments)},samples={fit['samples']},"
            f"duration={fit['duration_s']:.3f}s,"
            f"K={fit['gain']:.6f}C/duty,"
            f"tau={fit['tau_s']:.3f}s,"
            f"delay={fit['delay_s']:.3f}s,"
            f"ambient={fit['ambient_c']:.3f}C,"
            f"RMSE={fit['rmse']:.4f}C,"
            f"MAE={fit['mae']:.4f}C,"
            f"max_error={fit['max_error']:.4f}C",
            flush=True,
        )
        print(
            f"Run {run_index}/{EXPECTED_RUN_COUNT} open-loop transfer function:"
        )
        print(
            f"  G_{run_index}(s) = {fit['gain']:.6f} / "
            f"({fit['tau_s']:.6f}*s + 1) * "
            f"exp(-{fit['delay_s']:.6f}*s)"
        )
        print(
            f"  ambient={fit['ambient_c']:.6f} C, "
            f"RMSE={fit['rmse']:.6f} C, "
            f"MAE={fit['mae']:.6f} C, "
            f"max_error={fit['max_error']:.6f} C"
        )

    if len(runs) <= WARMUP_RUN_COUNT:
        raise ValueError(
            "not enough formal runs after warm-up exclusion"
        )

    fit_runs = runs[WARMUP_RUN_COUNT:]
    ident_print(
        f"[{datetime.now():%H:%M:%S}] [IDENT][JOINT_FIT][CONFIG] "
        f"warmup_runs_excluded={WARMUP_RUN_COUNT},"
        f"formal_runs={len(fit_runs)},"
        f"formal_run_ids="
        f"{','.join(str(run.run_index) for run in fit_runs)}",
        flush=True,
    )
    mean_fit = fit_joint_open_loop_runs(fit_runs)
    mean_rmses: list[float] = []
    mean_maes: list[float] = []
    mean_max_errors: list[float] = []
    for run_index, run in enumerate(runs):
        run.mean_prediction = simulate_open_loop_plant(
            run.series,
            mean_fit["gain"],
            mean_fit["tau_s"],
            mean_fit["delay_s"],
            float(mean_fit["ambient_c_by_run"][run_index]),
        )
        run.mean_error = run.mean_prediction - run.series.temp
        mean_metrics = calc_error_metrics(run.series.temp, run.mean_prediction)
        if run.run_index > WARMUP_RUN_COUNT:
            mean_rmses.append(mean_metrics["rmse"])
            mean_maes.append(mean_metrics["mae"])
            mean_max_errors.append(mean_metrics["max_error"])

    mean_fit["rmse"] = float(np.mean(mean_rmses))
    mean_fit["mae"] = float(np.mean(mean_maes))
    mean_fit["max_error"] = float(np.mean(mean_max_errors))

    print("\nSix individual open-loop transfer functions:")
    for item in run_results:
        fit = item["fit"]
        print(
            f"  run {item['run_index']}: "
            f"G(s)={fit['gain']:.6f}/({fit['tau_s']:.6f}*s+1)"
            f"*exp(-{fit['delay_s']:.6f}*s), "
            f"ambient={fit['ambient_c']:.6f} C, "
            f"RMSE={fit['rmse']:.6f} C"
        )
    print("\nJoint six-run open-loop transfer function:")
    print(
        f"  G_mean(s) = {mean_fit['gain']:.6f} / "
        f"({mean_fit['tau_s']:.6f}*s + 1) * "
        f"exp(-{mean_fit['delay_s']:.6f}*s)"
    )
    print(
        "  ambient offsets : "
        + ", ".join(
            f"run{i + 1}={value:.6f} C"
            for i, value in enumerate(mean_fit["ambient_c_by_run"])
        )
    )
    print(f"  joint-model RMSE : {mean_fit['rmse']:.6f} C")
    print(f"  joint-model MAE  : {mean_fit['mae']:.6f} C")
    print(f"  joint-model max error : {mean_fit['max_error']:.6f} C")

    plot_six_run_comparison(
        runs,
        mean_fit,
        Path(f"{result_prefix}_six_run_comparison.png"),
        excluded_run_count=WARMUP_RUN_COUNT,
    )
    plot_full_run_parameter_summary(
        run_results,
        mean_fit,
        Path(f"{result_prefix}_six_run_parameters.png"),
        excluded_run_count=WARMUP_RUN_COUNT,
    )
    ident_print(
        f"[{datetime.now():%H:%M:%S}] [IDENT][DONE] "
        f"completed_runs={len(runs)}/{EXPECTED_RUN_COUNT},"
        f"formal_runs={len(fit_runs)},"
        f"warmup_excluded={WARMUP_RUN_COUNT}; "
        "six-stage trajectories were fitted as complete runs",
        flush=True,
    )
    return 0


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
        "--result-prefix",
        type=Path,
        default=Path("imu_identification"),
        help="prefix for final fit CSV and PNG outputs",
    )
    parser.add_argument("--port", default=None, help="serial port, for example COM5")
    parser.add_argument("--baud", type=int, default=None, help="serial baud rate")
    parser.add_argument(
        "--outlier-sigma",
        type=float,
        default=OUTLIER_SIGMA,
        help="robust z-score threshold for outlier reporting/rejection",
    )
    parser.add_argument(
        "--reject-outliers",
        action="store_true",
        help="remove reported outlier stages from the inlier model and plots",
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
        return run_online_identification(
            port,
            baud,
            args.result_prefix,
        )

    if not segments:
        raise ValueError("no heating segments found")

    fit_results: list[tuple[Segment, dict, dict]] = []
    ident_print(
        f"[{datetime.now():%H:%M:%S}] "
        f"[IDENT][FINAL_FIT][START] stages={len(segments)}",
        flush=True,
    )
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
            if args.reject_outliers:
                inlier_fit_results = remove_outlier_segments(fit_results, outliers)
                if len(inlier_fit_results) >= 2:
                    print_fit_summary(inlier_fit_results, "Inlier summary")
                    inlier_common_shape = fit_common_shape(inlier_fit_results)
                    print_common_shape(
                        inlier_common_shape,
                        "Joint least-squares, inliers only",
                    )
            else:
                print("Outlier handling: reported only; all stages retained.")
    except ValueError:
        pass

    plot_fit_results = inlier_fit_results if inlier_common_shape is not None else fit_results
    plot_common_shape = inlier_common_shape if inlier_common_shape is not None else common_shape

    result_prefix = str(args.result_prefix)
    if plot_common_shape is not None:
        ident_print(
            f"[{datetime.now():%H:%M:%S}] "
            f"[IDENT][FINAL_FIT][DONE] stages={len(plot_fit_results)},"
            f"tau={plot_common_shape['tau']:.3f}s,"
            f"delay={plot_common_shape['l']:.3f}s",
            flush=True,
        )
        print(
            f"Final joint fit output: stages={len(plot_fit_results)},"
            f"tau={plot_common_shape['tau']:.3f}s,"
            f"L={plot_common_shape['l']:.3f}s"
        )

    if not args.no_plot:
        import matplotlib.pyplot as plt

        duties = np.asarray([segment.duty for segment, _, _ in plot_fit_results], dtype=float)
        taus = np.asarray([fit["tau"] for _, fit, _ in plot_fit_results], dtype=float)
        rises = np.asarray([fit["delta_temp"] for _, fit, _ in plot_fit_results], dtype=float)
        colors = plt.get_cmap("tab10")(np.linspace(0.0, 1.0, max(len(plot_fit_results), 1)))

        # 每个 Heating 阶段单独显示，避免不同阶段的温度基线和响应互相遮挡。
        response_columns = 2
        response_rows = (len(plot_fit_results) + response_columns - 1) // response_columns
        response_fig, response_axes = plt.subplots(
            response_rows,
            response_columns,
            figsize=(14, max(4.5 * response_rows, 5.0)),
            squeeze=False,
            sharex=False,
        )
        response_axes_flat = response_axes.ravel()
        for plot_index, (segment, fit, _) in enumerate(plot_fit_results):
            ax = response_axes_flat[plot_index]
            color = colors[plot_index]
            ax.plot(
                segment.series.time,
                segment.series.temp,
                color=color,
                linewidth=1.4,
                label="measured",
            )
            ax.plot(
                segment.series.time,
                predict_heating_curve(segment.series.time, fit),
                color=color,
                linestyle="--",
                linewidth=1.8,
                label="individual fit",
            )
            if plot_common_shape is not None:
                stage_fit = next(
                    item
                    for item in plot_common_shape["stage_fits"]
                    if item["source"] == segment.source and item["stage"] == segment.index
                )
                ax.plot(
                    segment.series.time,
                    stage_fit["y_hat"],
                    color=color,
                    linestyle=":",
                    linewidth=1.8,
                    label="common tau/L",
                )
            ax.set_title(f"Stage {segment.index + 1}, duty={segment.duty:.3f}")
            ax.set_xlabel("Time from stage start (s)")
            ax.set_ylabel("Temperature (C)")
            ax.grid(True, alpha=0.3)
            ax.legend(loc="best", fontsize=8)

        for ax in response_axes_flat[len(plot_fit_results):]:
            ax.set_visible(False)
        response_fig.suptitle("IMU Heating Response by Stage", fontsize=14)
        response_fig.tight_layout()
        response_fig.savefig(
            f"{result_prefix}_stage_fits.png",
            dpi=150,
            bbox_inches="tight",
        )
        print(f"Saved stage fit plot: {result_prefix}_stage_fits.png")

        summary_fig, summary_axes = plt.subplots(2, 2, figsize=(14, 10))
        ax_duty, ax_norm, ax_tau, ax_rise = summary_axes.ravel()

        for plot_index, (segment, _, _) in enumerate(plot_fit_results):
            color = colors[plot_index]
            ax_duty.plot(
                segment.series.time,
                segment.series.duty,
                color=color,
                linewidth=2.0,
                label=f"stage {segment.index + 1}, duty={segment.duty:.3f}",
            )
        ax_duty.set_title("Applied Duty by Stage")
        ax_duty.set_xlabel("Time from stage start (s)")
        ax_duty.set_ylabel("Duty")
        ax_duty.grid(True, alpha=0.3)
        ax_duty.legend(fontsize=8)

        for plot_index, (segment, fit, _) in enumerate(plot_fit_results):
            color = colors[plot_index]
            norm = (segment.series.temp - fit["t0_temp"]) / max(fit["delta_temp"], 1e-9)
            ax_norm.plot(
                segment.series.time,
                np.clip(norm, 0.0, 1.2),
                color=color,
                linewidth=1.5,
                label=f"duty={segment.duty:.3f}",
            )
        if plot_common_shape is not None:
            tau_common = plot_common_shape["tau"]
            l_common = plot_common_shape["l"]
            ref_time = max(float(np.max(segment.series.time)) for segment, _, _ in plot_fit_results)
            t_ref = np.linspace(0.0, ref_time, 300)
            z_ref = 1.0 - np.exp(-np.maximum(t_ref - l_common, 0.0) / tau_common)
            ax_norm.plot(
                t_ref,
                z_ref,
                color="black",
                linestyle=":",
                linewidth=2.0,
                label="common shape",
            )
        ax_norm.set_title("Normalized Heating Response")
        ax_norm.set_xlabel("Time from stage start (s)")
        ax_norm.set_ylabel("Normalized temperature")
        ax_norm.grid(True, alpha=0.3)
        ax_norm.legend(fontsize=8)

        ax_tau.plot(duties, taus, color="#1f77b4", marker="o", linewidth=2.0, label="stage tau")
        if plot_common_shape is not None:
            ax_tau.axhline(
                plot_common_shape["tau"],
                linestyle=":",
                color="black",
                linewidth=2.0,
                label=f"common tau={plot_common_shape['tau']:.3f}s",
            )
        ax_tau.set_title("Time Constant")
        ax_tau.set_xlabel("Duty")
        ax_tau.set_ylabel("Tau (s)")
        ax_tau.grid(True, alpha=0.3)
        ax_tau.legend(fontsize=8)

        ax_rise.plot(duties, rises, color="#d62728", marker="o", linewidth=2.0, label="stage rise")
        if plot_fit_results and np.ptp(duties) > 1e-6:
            slope, intercept = np.polyfit(duties, rises, 1)
            duty_fit = np.linspace(np.min(duties), np.max(duties), 100)
            ax_rise.plot(
                duty_fit,
                slope * duty_fit + intercept,
                color="black",
                linestyle="--",
                linewidth=1.8,
                label=f"rise fit: {slope:.3f}*duty+{intercept:.3f}",
            )
        ax_rise.set_title("Temperature Rise vs Duty")
        ax_rise.set_xlabel("Duty")
        ax_rise.set_ylabel("Rise (C)")
        ax_rise.grid(True, alpha=0.3)
        ax_rise.legend(fontsize=8)

        summary_fig.suptitle("IMU Identification Summary", fontsize=14)
        summary_fig.tight_layout()
        summary_fig.savefig(
            f"{result_prefix}_summary.png",
            dpi=150,
            bbox_inches="tight",
        )
        print(f"Saved summary plot: {result_prefix}_summary.png")
        plt.show()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
