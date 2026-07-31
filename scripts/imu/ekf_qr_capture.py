"""
EKF Q/R 采集与计算工具

用法:
    python ekf_qr_capture.py -p COM3 -b 921600 -n 60000

自动接收 IMU 串口输出的 gx,gy,gz,ax,ay,az,dt 数据，
样本数达到 -n 后自动计算 R_meas / Qq_meas 及工程起点并退出。
"""

import argparse
import queue
import re
import sys
import threading
import time

import numpy as np

# CSV 行正则：7 个 float，逗号分隔，前后可有任意字符（Zephyr 日志前缀）
LINE_RE = re.compile(
    r"([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?)"  # gx
    r",([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?)"  # gy
    r",([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?)"  # gz
    r",([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?)"  # ax
    r",([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?)"  # ay
    r",([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?)"  # az
    r",([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?)"  # dt
)


def parse_args():
    p = argparse.ArgumentParser(description="EKF Q/R 串口采集与计算")
    p.add_argument("-p", "--port", default="COM21", help="串口名，默认 COM21")
    p.add_argument("-b", "--baud", type=int, default=921600, help="波特率，默认 921600")
    p.add_argument("-n", "--samples", type=int, default=60000,
                    help="采集样本数，默认 60000（60s @ 1kHz）")
    p.add_argument("-o", "--output", help="保存原始 CSV 到文件（可选）")
    return p.parse_args()


def serial_reader(port, baud, q: queue.Queue, stop_event):
    """后台线程：读串口，每行放进队列"""
    try:
        import serial
    except ImportError:
        print("ERROR: 需要 pyserial，请运行: pip install pyserial numpy")
        sys.exit(1)

    try:
        ser = serial.Serial(port, baud, timeout=0.1)
    except serial.SerialException as e:
        print(f"ERROR: 打开串口失败: {e}")
        sys.exit(1)

    print(f"打开串口 {port} @ {baud}")

    buf = b""
    while not stop_event.is_set():
        try:
            chunk = ser.read(4096)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    q.put(line.decode(errors="replace").strip())
        except serial.SerialException:
            break

    ser.close()


def main():
    args = parse_args()

    q = queue.Queue()
    stop_event = threading.Event()

    reader = threading.Thread(target=serial_reader,
                              args=(args.port, args.baud, q, stop_event),
                              daemon=True)
    reader.start()

    gx, gy, gz = [], [], []
    ax, ay, az = [], [], []
    dts = []
    count = 0
    target = args.samples
    out_fp = open(args.output, "w") if args.output else None

    if out_fp:
        out_fp.write("gx,gy,gz,ax,ay,az,dt\n")

    print(f"采集中，目标 {target} 样本 ... (按 Ctrl+C 提前结束)")
    print()

    try:
        while count < target:
            try:
                line = q.get(timeout=3)
            except queue.Empty:
                continue

            m = LINE_RE.search(line)
            if not m:
                continue

            vals = [float(m.group(i)) for i in range(1, 8)]

            gx.append(vals[0])
            gy.append(vals[1])
            gz.append(vals[2])
            ax.append(vals[3])
            ay.append(vals[4])
            az.append(vals[5])
            dts.append(vals[6])

            count += 1

            if out_fp:
                out_fp.write(f"{vals[0]},{vals[1]},{vals[2]},"
                             f"{vals[3]},{vals[4]},{vals[5]},"
                             f"{vals[6]}\n")

            if count % 1000 == 0 or count == target:
                print(f"\r  已接收 {count}/{target}", end="", flush=True)
    except KeyboardInterrupt:
        print(f"\n用户中断，已采集 {count} 样本")
    finally:
        stop_event.set()
        if out_fp:
            out_fp.close()

    if count < 100:
        print("ERROR: 样本太少，无法计算")
        sys.exit(1)

    # === 计算 ===
    gx_a = np.array(gx)
    gy_a = np.array(gy)
    gz_a = np.array(gz)
    ax_a = np.array(ax)
    ay_a = np.array(ay)
    az_a = np.array(az)
    dt_a = np.array(dts)

    # 加速度模长与归一化方向
    a_norm = np.sqrt(ax_a**2 + ay_a**2 + az_a**2)
    nx = ax_a / a_norm
    ny = ay_a / a_norm
    nz = az_a / a_norm

    # R
    R_meas = float(np.var(nx) + np.var(ny) + np.var(nz))

    # gyro 噪声
    sigma_g2 = float((np.var(gx_a) + np.var(gy_a) + np.var(gz_a)) / 3.0)
    sigma_g = np.sqrt(sigma_g2)

    # sigma_g2 already is variance (mean of variances of each axis)
    # Qq_meas = sigma_g^2 * dt (per step)
    # But since Qq in code gets multiplied by dt: P_noise = Qq * dt
    # Qq_meas should be the value that after *dt gives sigma_g^2 * dt
    # Wait, let me re-read the formula.
    #
    # From the doc:
    # Qq = σ_g² · dt   (single step)
    # cfg.Qq is set as a raw value, then in Update:
    #   P_noise = cfg.Qq * dt
    #
    # So Qq_meas = σ_g² / mean_dt gives us cfg.Qq_start  (to undo the *dt in Update,
    # we divide by dt, so that Qq_start * dt = σ_g² in the time domain)
    #
    # Actually let me reconsider. The formula says:
    # Qq = σ_g² · dt   (single step)
    # Then it says: Qq_meas = σ_g² / dt
    #
    # This is confusing. Let me think again.
    # The physical process noise per step is Q = σ_g² * dt
    # But the code stores a "raw" Qq in Config, and then does:
    #   Q(0,0) = Qq * dt   (line 368-369 in doc)
    #
    # So Qq_config * dt should equal σ_g² * dt   (the physical noise)
    # Therefore Qq_config = σ_g²   (in units of (rad/s)² / s = rad²/s³)
    # Wait that doesn't make sense either.
    #
    # Let me look at the doc formulas more carefully:
    # Line 362-368 from doc:
    #   Qq = σ_g² · dt   (single step)
    #   cfg.Qq = Qq... stored as raw
    #   Q(0,0) = Qq * dt   (in Update)
    #
    # So if Qq = σ_g² · dt, and then Q(0,0) = Qq * dt = σ_g² · dt²
    # That gives the noise covariance in units of (rad)² which doesn't seem right.
    #
    # Actually from the doc Qq formula section:
    # Qq_meas = σ_g² / dt
    # So for σ_g = 0.0005 rad/s, dt = 0.001: Qq_meas = 0.0005² / 0.001 = 2.5e-4
    #
    # Then in code: Q(0,0) = Qq * dt = 2.5e-4 * 0.001 = 2.5e-7
    # This is the discrete-time process noise covariance for the quaternion.
    #
    # Qq * dt = σ_g² * dt の形式にするには...
    # Actually σ_g² = 2.5e-7 (rad/s)², and Qq * dt should give σ_g² * dt
    # So Qq * dt = σ_g² * dt → Qq = σ_g²
    # That gives Qq = 2.5e-7, which is not 2.5e-4.
    #
    # I'm overcomplicating this. Let me just follow the doc formula literally:
    #   Qq_meas = sigma_g2 / mean(dt)
    # where sigma_g2 = (var(gx) + var(gy) + var(gz)) / 3

    mean_dt = float(np.mean(dt_a))
    if mean_dt <= 0:
        print("ERROR: dt <= 0")
        sys.exit(1)

    Qq_meas = sigma_g2 / mean_dt

    # === 输出 ===
    print()
    print("=" * 50)
    print("采集完成")
    print(f"  样本数:   {count}")
    print(f"  时长:     {count * mean_dt:.1f} s")
    print(f"  平均 dt:  {mean_dt:.6f} s")
    print()

    # Gyro 检查（确认 AutoCalib 后均值是否接近零）
    gx_mean = float(np.mean(gx_a))
    gy_mean = float(np.mean(gy_a))
    gz_mean = float(np.mean(gz_a))
    print(f"  gyro mean: {gx_mean:.5f}, {gy_mean:.5f}, {gz_mean:.5f} rad/s  (校准后应接近零)")

    a_mean = float(np.mean(a_norm))
    print(f"  acc norm mean: {a_mean:.3f} m/s²  (应接近 9.8)")
    print()

    print(f"  σ_g:       {sigma_g:.6f} rad/s")
    print(f"  σ_g²:      {sigma_g2:.8e} (rad/s)²")
    print(f"  R_meas:    {R_meas:.6e}")
    print(f"  Qq_meas:   {Qq_meas:.6e}")
    print()
    print(f"--- 工程起点 (×10 ~ ×100) ---")
    print(f"  R_start:   {R_meas * 10:.6e} ~ {R_meas * 100:.6e}")
    print(f"  Qq_start:  {Qq_meas * 10:.6e} ~ {Qq_meas * 100:.6e}")
    print()
    print(f"可写入 processor.cpp:")
    print(f"  cfg.Qq = {Qq_meas * 10:.6e}f;  // ~ {Qq_meas * 100:.6e}f")
    print(f"  cfg.R  = {R_meas * 10:.6e}f;   // ~ {R_meas * 100:.6e}f")
    print(f"  cfg.Qb      = 1e-5f;")
    print(f"  cfg.alpha   = 0.02f;")
    print(f"  cfg.chi2_th = 1e-2f;")
    print("=" * 50)

    if args.output:
        print(f"原始 CSV 已保存: {args.output}")


if __name__ == "__main__":
    main()
