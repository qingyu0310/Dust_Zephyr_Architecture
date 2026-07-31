"""分析预热日志，复算固件 MeanStable 判据，定位预热过不去的原因。

固件日志格式（imu.cpp Task 每 1ms 打印一行）:
    temp, duty                     # 例如 "39.524, 0.123"
    或带 Zephyr 时间戳: [00:00:04.023,000] <inf> imu: 39.524, 0.123

固件判据（MeanStable<100, 3>, heater.cpp Preheat）:
    stable_.Check(40.0, temp, dt_s, 0.5, 0.3)
    实参: error_limit=0.5, var_limit=0.3,
          max_err_limit=1.0(默认), slope_limit=0.01(默认), max_slp_limit=0.05(默认)

通过条件（窗口 100 帧, 连续 3 窗口全过）:
    mean|temp-40| <= 0.5
    var|temp-40|  <= 0.3
    max|temp-40|  <= 1.0
    mean|slope|   <= 0.01 °C/s   <-- 折到 1ms 帧 = 0.00001°C/帧
    max |slope|   <= 0.05 °C/s
"""

from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

TARGET_TEMP = 40.0
WINDOW = 100
ROUNDS = 3
DT_S = 0.001  # 1ms 采样间隔

LIMITS = {
    "mean_err": 10.0,
    "var_err": 10.0,
    "max_err": 10.0,
    "mean_slope": 20.0,
    "max_slope": 10.0,
}

TS_RE = re.compile(r"\[(\d+):(\d+):(\d+)\.(\d+),(\d+)\]")
SIMPLE_RE = re.compile(r"([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)")


def _ts_to_sec(m: re.Match) -> float:
    """Zephyr 时间戳 [HH:MM:SS.mmm,mmm] → 秒。"""
    h, mi, s, ms, us = map(int, m.groups())
    return h * 3600 + mi * 60 + s + ms * 1e-3 + us * 1e-6


@dataclass(frozen=True)
class WindowResult:
    window_idx: int
    mean_err: float
    var_err: float
    max_err: float
    mean_slope: float
    max_slope: float
    passed: bool
    fail_reasons: tuple[str, ...]


def parse_log(text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """解析日志, 返回 (temp, duty, dt)。优先 Zephyr 时间戳算 dt, 否则按固定间隔。"""
    temps: list[float] = []
    duties: list[float] = []
    times: list[float] = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        ts = TS_RE.search(line)
        if ts:
            t_s = _ts_to_sec(ts)
            # 去掉时间戳后只剩数据部分, 避免时间戳里的 ",000" 被误配
            rest = line[ts.end():]
        else:
            t_s = None
            rest = line

        m = SIMPLE_RE.search(rest)
        if not m:
            continue
        temps.append(float(m.group(1)))
        duties.append(float(m.group(2)))
        if t_s is not None:
            times.append(t_s)

    temp = np.array(temps, dtype=np.float64)
    duty = np.array(duties, dtype=np.float64)
    if len(times) == len(temp):
        t_arr = np.array(times, dtype=np.float64)
        dt = np.diff(t_arr)
    else:
        dt = np.full(len(temp) - 1, DT_S)
    return temp, duty, dt


def compute_windows(temp: np.ndarray, dt: np.ndarray) -> list[WindowResult]:
    """按固件 MeanStable<100,3> 逐窗口复算判据。"""
    n = len(temp)
    results: list[WindowResult] = []

    for w in range(0, n - WINDOW + 1, WINDOW):
        seg = temp[w : w + WINDOW]
        err = np.abs(seg - TARGET_TEMP)
        mean_err = float(err.mean())
        var_err = float(err.var(ddof=0))  # 固件用 sum_sq/n - mean^2, 等价无偏校正 ddof=0
        max_err = float(err.max())

        dseg = dt[w : w + WINDOW - 1]
        slp = np.abs(np.diff(seg)) / np.where(dseg > 0, dseg, 1e-9)
        mean_slope = float(slp.mean()) if slp.size else 0.0
        max_slope = float(slp.max()) if slp.size else 0.0

        reasons = []
        if mean_err > LIMITS["mean_err"]:
            reasons.append(f"mean_err={mean_err:.4f} > {LIMITS['mean_err']}")
        if var_err > LIMITS["var_err"]:
            reasons.append(f"var_err={var_err:.4f} > {LIMITS['var_err']}")
        if max_err > LIMITS["max_err"]:
            reasons.append(f"max_err={max_err:.4f} > {LIMITS['max_err']}")
        if mean_slope > LIMITS["mean_slope"]:
            reasons.append(f"mean_slope={mean_slope:.4f} > {LIMITS['mean_slope']}")
        if max_slope > LIMITS["max_slope"]:
            reasons.append(f"max_slope={max_slope:.4f} > {LIMITS['max_slope']}")

        results.append(WindowResult(
            window_idx=w // WINDOW,
            mean_err=mean_err,
            var_err=var_err,
            max_err=max_err,
            mean_slope=mean_slope,
            max_slope=max_slope,
            passed=not reasons,
            fail_reasons=tuple(reasons),
        ))

    return results


def report_stable_region(temp: np.ndarray, dt: np.ndarray) -> None:
    """在温度接近目标(±1°C)的最后一段, 统计实际斜率与噪声。"""
    mask = np.abs(temp - TARGET_TEMP) <= 1.0
    idx = np.flatnonzero(mask)
    if idx.size < 10:
        print("  目标附近样本不足，无法分析稳定区")
        return

    # 只在连续段内算斜率: dt[i] 是 temp[i]->temp[i+1] 的间隔, 需要 i 和 i+1 都在段内
    slp_list: list[float] = []
    for k in range(1, idx.size):
        i = idx[k]
        # 断点则跳过该帧的斜率（不跨段）
        if i != idx[k - 1] + 1:
            continue
        d = dt[i - 1] if i - 1 < len(dt) and dt[i - 1] > 0 else DT_S
        slp_list.append(abs(temp[i] - temp[i - 1]) / d)

    seg = temp[mask]
    print(f"  目标 {TARGET_TEMP}±1°C 区间样本数: {idx.size}")
    print(f"  温度范围     : {seg.min():.3f} ~ {seg.max():.3f} °C")
    print(f"  峰峰噪声     : {seg.max() - seg.min():.4f} °C")
    if slp_list:
        print(f"  平均|斜率|   : {np.mean(slp_list):.5f} °C/s  (限 {LIMITS['mean_slope']})")
        print(f"  最大|斜率|   : {np.max(slp_list):.5f} °C/s  (限 {LIMITS['max_slope']})")
    print(f"  每帧平均变化 : {np.abs(np.diff(seg)).mean():.6f} °C/帧")


SERIAL_PORT = "COM21"
SERIAL_BAUD = 921600
CAPTURE_SECONDS = 30.0


def capture_serial(port: str, baud: int, seconds: float) -> str:
    """从串口捕获预热日志, 返回原始文本。"""
    try:
        import serial
    except ImportError as exc:
        raise SystemExit("需要 pyserial，请运行: pip install pyserial") from exc

    print(f"连接 {port} @ {baud}, 捕获 {seconds:.0f}s 预热数据... Ctrl+C 提前结束")
    try:
        ser = serial.Serial(port, baud, timeout=0.2)
    except serial.SerialException as exc:
        raise SystemExit(f"打开 {port} 失败: {exc}") from exc

    lines: list[str] = []
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            data = ser.readline().decode("utf-8", errors="replace").strip()
            if not data:
                continue
            print(data)
            lines.append(data)
    except KeyboardInterrupt:
        print("\n手动结束")
    finally:
        ser.close()

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", nargs="?", help="日志文件路径; 缺省从 stdin 读")
    parser.add_argument("--serial", nargs="?", const=SERIAL_PORT,
                        help=f"串口捕获模式, 端口 (默认 {SERIAL_PORT})")
    parser.add_argument("--baud", type=int, default=SERIAL_BAUD,
                        help=f"串口波特率 (默认 {SERIAL_BAUD})")
    parser.add_argument("--seconds", type=float, default=CAPTURE_SECONDS,
                        help=f"串口捕获时长 s (默认 {CAPTURE_SECONDS})")
    parser.add_argument("--dt-ms", type=float, default=1.0,
                        help="无时间戳时的采样间隔 ms (默认 1)")
    args = parser.parse_args()

    import sys
    if args.serial:
        text = capture_serial(args.serial, args.baud, args.seconds)
    elif args.log:
        text = Path(args.log).read_text(encoding="utf-8", errors="replace")
    else:
        if sys.stdin.isatty():
            print("用法:")
            print("  串口: python preheat_stability_analysis.py --serial")
            print("  文件: python preheat_stability_analysis.py <日志文件>")
            print("  管道: cat log.txt | python preheat_stability_analysis.py")
            print("  重定向: python preheat_stability_analysis.py < log.txt")
            return
        text = sys.stdin.read()

    global DT_S
    DT_S = args.dt_ms * 1e-3

    temp, duty, dt = parse_log(text)
    if len(temp) < WINDOW:
        print(f"样本数 {len(temp)} < 窗口 {WINDOW}, 无法判稳")
        return

    print(f"共解析 {len(temp)} 帧 (temp/duty)")
    print(f"温度范围: {temp.min():.3f} ~ {temp.max():.3f} °C, 均值 {temp.mean():.3f}")
    print(f"duty 范围: {duty.min():.3f} ~ {duty.max():.3f}")
    print()

    windows = compute_windows(temp, dt)
    good = sum(1 for w in windows if w.passed)
    print(f"窗口数: {len(windows)}, 通过: {good}, 不通过: {len(windows) - good}")
    print()

    # 展示最后 5 个窗口 + 第一个失败窗口
    shown = set()
    for w in windows:
        if w.window_idx in shown:
            continue
        if len(shown) >= 5 and w.passed:
            continue
        shown.add(w.window_idx)
        status = "PASS" if w.passed else "FAIL"
        print(f"[窗口 {w.window_idx}] {status}  "
              f"mean_err={w.mean_err:.4f} var_err={w.var_err:.4f} "
              f"max_err={w.max_err:.4f} mean_slp={w.mean_slope:.4f} max_slp={w.max_slope:.4f}")
        for r in w.fail_reasons:
            print(f"          └ {r}")
        if len(shown) >= 6:
            break

    print()
    print("=== 稳定区(目标±1°C) 斜率与噪声分析 ===")
    report_stable_region(temp, dt)

    last3 = windows[-3:]
    if all(w.passed for w in last3) and len(windows) >= 3:
        print("\n结论: 最后 3 窗口全过 → 预热应判稳定")
    else:
        fails = [r for w in last3 for r in w.fail_reasons]
        main_fail = max(fails, key=lambda r: LIMITS.get(r.split("=")[0].split("[")[0], 0)) if fails else None
        print("\n结论: 预热未判稳定")
        if main_fail:
            print(f"最可能卡住的判据: {main_fail}")


if __name__ == "__main__":
    main()
