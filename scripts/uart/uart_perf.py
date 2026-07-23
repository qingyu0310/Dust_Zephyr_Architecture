#!/usr/bin/env python3
import argparse
import re
import sys
import time

import serial
import serial.tools.list_ports


BAUD = 115200
REPORT_TIMEOUT = 6.0
IDLE_GUARD = 0.8


RE_BENCH = re.compile(
    r"bytes=(?P<bytes>\d+)\s+"
    r"reads=(?P<reads>\d+)\s+"
    r"min=(?P<min>\d+)\s+"
    r"max=(?P<max>\d+)\s+"
    r"avg=(?P<avg>\d+)\s+"
    r"rd_cyc=(?P<rd_cyc>\d+)\s+"
    r"avg_cyc=(?P<avg_cyc>\d+)\s+"
    r"time=(?P<time_ms>-?\d+)ms\s+"
    r"bw=(?P<bw>\d+)B/s"
)


def list_ports():
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("no serial ports found")
        return
    print("serial ports:")
    for port in sorted(ports, key=lambda p: p.device):
        print(f"  {port.device}  -  {port.description}")


def open_serial(port, baud):
    ser = serial.Serial(port, baud, timeout=3, write_timeout=5)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    return ser


def wait_for_boot(ser, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = ser.readline().decode(errors="ignore").strip()
        if "uart3 ready" in line:
            return True
    return False


def drain(ser, duration=IDLE_GUARD):
    time.sleep(duration)
    ser.reset_input_buffer()


def send_pattern(ser, size, count, interval=0):
    for i in range(count):
        ser.write(bytes([i & 0xFF]) * size)
        if interval > 0:
            time.sleep(interval)
    ser.flush()


def send_stream(ser, total, chunk=1024):
    pattern = bytes(range(256)) * ((chunk // 256) + 1)
    sent = 0
    while sent < total:
        n = min(chunk, total - sent)
        ser.write(pattern[:n])
        sent += n
    ser.flush()


def parse_report(line):
    match = RE_BENCH.search(line)
    if not match:
        return None
    report = {key: int(value) for key, value in match.groupdict().items()}
    report["bw_kib_s"] = report["bw"] / 1024
    report["time_s"] = report["time_ms"] / 1000
    return report


def wait_report(ser, timeout=REPORT_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = ser.readline().decode(errors="ignore").strip()
        if "[BENCH]" in line:
            return parse_report(line)
    return None


def run_case(ser, name, fn):
    print(f"\n--- {name} --- ", end="", flush=True)
    drain(ser)
    fn(ser)
    report = wait_report(ser)
    print("OK" if report else "NO REPORT")
    return name, report


def case_small_packets(ser):
    send_pattern(ser, 16, 500)


def case_medium_packets(ser):
    send_pattern(ser, 64, 500)


def case_large_packets(ser):
    send_pattern(ser, 256, 500)


def case_max_packets(ser):
    send_pattern(ser, 512, 200)


def case_throughput(ser):
    send_stream(ser, 50 * 1024)


def case_heavy(ser):
    send_stream(ser, 200 * 1024)


def case_spaced_burst(ser):
    send_pattern(ser, 256, 200, interval=0.005)


def case_mixed_sizes(ser):
    for i in range(500):
        size = (i % 256) + 1
        ser.write(bytes([i & 0xFF]) * size)
    ser.flush()


def case_read_latency(ser):
    for i in range(200):
        ser.write(bytes([i & 0xFF]))
    ser.flush()


CASES = (
    ("16B x500 small", case_small_packets),
    ("64B x500 medium", case_medium_packets),
    ("256B x500 large", case_large_packets),
    ("512B x200 max", case_max_packets),
    ("50KB stream", case_throughput),
    ("200KB stream", case_heavy),
    ("256B x200 5ms", case_spaced_burst),
    ("1..256B x500 mixed", case_mixed_sizes),
    ("1B x200 byte", case_read_latency),
)


def print_summary(results):
    print("\n" + "=" * 92)
    print(
        f"{'case':28s} {'bytes':>8s} {'reads':>6s} {'min':>4s} {'max':>4s} "
        f"{'avg':>4s} {'avg_cyc':>8s} {'time':>8s} {'bw':>10s}"
    )
    print("-" * 92)

    for name, report in results:
        if report is None:
            print(f"{name:28s}  NO REPORT")
            continue
        print(
            f"{name:28s} {report['bytes']:>8d} {report['reads']:>6d} "
            f"{report['min']:>4d} {report['max']:>4d} {report['avg']:>4d} "
            f"{report['avg_cyc']:>8d} {report['time_s']:>7.2f}s "
            f"{report['bw_kib_s']:>8.1f} KiB/s"
        )


def main():
    if len(sys.argv) >= 2 and sys.argv[1] in ("-l", "--list"):
        list_ports()
        return

    parser = argparse.ArgumentParser(description="UART RX benchmark")
    parser.add_argument("port", nargs="?", default="COM21")
    parser.add_argument("baud", nargs="?", type=int, default=BAUD)
    args = parser.parse_args()

    print(f"port: {args.port} @ {args.baud} baud")
    ser = open_serial(args.port, args.baud)

    try:
        print("wait firmware...", end=" ", flush=True)
        print("OK" if wait_for_boot(ser) else "TIMEOUT, continue")
        drain(ser, 1.0)

        results = [run_case(ser, name, fn) for name, fn in CASES]
        print_summary(results)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
