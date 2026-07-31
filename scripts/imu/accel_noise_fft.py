"""
加速度计三轴频域噪声分析

采集静止状态下的 ax,ay,az 数据（固件 printk 格式），
计算每轴的功率谱密度（PSD）并输出统计，可选绘频域图。

用法:
    python scripts/imu/accel_noise_fft.py -p COM21 -b 921600 -t 30
    python scripts/imu/accel_noise_fft.py -p COM21 --plot
    python scripts/imu/accel_noise_fft.py -p COM21 --save accel_fft.png
"""

from __future__ import annotations

import argparse
import re
import sys

import numpy as np

FLOAT = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
LINE_RE = re.compile(rf"({FLOAT}),({FLOAT}),({FLOAT})")


def main() -> int:
    parser = argparse.ArgumentParser(description="加速度计三轴频域噪声分析")
    parser.add_argument("-p", "--port", default="COM21")
    parser.add_argument("-b", "--baud", type=int, default=921600)
    parser.add_argument("-t", "--time", type=float, default=30.0, help="采集秒数")
    parser.add_argument("--dt", type=float, default=0.005, help="采样间隔秒")
    parser.add_argument("--no-plot", action="store_true", help="不显示频域图")
    args = parser.parse_args()

    try:
        import serial
    except ImportError:
        print("ERROR: need pyserial")
        return 1

    freq = 1.0 / args.dt
    n_samples = int(args.time / args.dt)

    ax, ay, az = [], [], []
    print(f"capturing ({args.time}s @ ~{freq:.0f}Hz, ~{n_samples} samples)...")

    with serial.Serial(args.port, args.baud, timeout=0.1) as dev:
        while len(ax) < n_samples:
            line = dev.readline().decode(errors="replace").strip()
            m = LINE_RE.search(line)
            if not m:
                continue
            ax.append(float(m.group(1)))
            ay.append(float(m.group(2)))
            az.append(float(m.group(3)))
            if len(ax) % 5000 == 0:
                print(f"  {len(ax)}/{n_samples}")

    print(f"\ndone, {len(ax)} samples")

    arr_x = np.array(ax, dtype=float)
    arr_y = np.array(ay, dtype=float)
    arr_z = np.array(az, dtype=float)

    n = len(arr_x)
    win = np.hanning(n)
    fft_x = np.fft.rfft(arr_x * win)
    fft_y = np.fft.rfft(arr_y * win)
    fft_z = np.fft.rfft(arr_z * win)
    psd_x = np.abs(fft_x) ** 2 / (n * freq) * 2
    psd_y = np.abs(fft_y) ** 2 / (n * freq) * 2
    psd_z = np.abs(fft_z) ** 2 / (n * freq) * 2
    freqs = np.fft.rfftfreq(n, d=args.dt)

    rms_x = float(np.std(arr_x))
    rms_y = float(np.std(arr_y))
    rms_z = float(np.std(arr_z))

    print(f"\n{'='*55}")
    print("accelerometer noise analysis")
    print(f"{'='*55}")
    print(f"  sample rate: {freq:.1f} Hz")
    print(f"  duration:    {n * args.dt:.1f} s")
    print(f"  samples:     {n}")
    print()
    print(f"  RMS noise:")
    print(f"    X:  {rms_x:.6f} m/s2  ({rms_x/9.8*100:.3f}% g)")
    print(f"    Y:  {rms_y:.6f} m/s2  ({rms_y/9.8*100:.3f}% g)")
    print(f"    Z:  {rms_z:.6f} m/s2  ({rms_z/9.8*100:.3f}% g)")
    print()

    bands = [
        (0.1, 1, "0.1-1 Hz"),
        (1, 10, "1-10 Hz"),
        (10, 50, "10-50 Hz"),
        (50, freq / 2, f"50-{freq/2:.0f} Hz"),
    ]
    print(f"  PSD integral (m/s2 RMS/band):")
    print(f"  {'band':<16} {'X':<12} {'Y':<12} {'Z':<12}")
    for lo, hi, label in bands:
        mask = (freqs >= lo) & (freqs < hi)
        p_x = float(np.sqrt(np.trapezoid(psd_x[mask], freqs[mask])))
        p_y = float(np.sqrt(np.trapezoid(psd_y[mask], freqs[mask])))
        p_z = float(np.sqrt(np.trapezoid(psd_z[mask], freqs[mask])))
        print(f"  {label:<16} {p_x:<12.6f} {p_y:<12.6f} {p_z:<12.6f}")
    print(f"{'='*55}")

    if not args.no_plot:
        try:
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(10, 5))
            ax.loglog(freqs, np.sqrt(psd_x), label="X", lw=0.8)
            ax.loglog(freqs, np.sqrt(psd_y), label="Y", lw=0.8)
            ax.loglog(freqs, np.sqrt(psd_z), label="Z", lw=0.8)
            ax.set_xlabel("Frequency (Hz)")
            ax.set_ylabel(r"PSD $\sqrt{\mathrm{PSD}}$ (m/s²/√Hz)")
            ax.set_title("Accelerometer noise PSD")
            ax.legend()
            ax.grid(True, which="both", alpha=0.3)
            fig.tight_layout()
            plt.show()
        except ImportError:
            print("need matplotlib for plotting")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
