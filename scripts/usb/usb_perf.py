#!/usr/bin/env python3
"""USB TX throughput — receive only, no handshake."""

import argparse
import sys
import time

import serial
import serial.tools.list_ports


def list_ports():
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("no serial ports found")
        return
    for port in sorted(ports, key=lambda p: p.device):
        print(f"  {port.device}  -  {port.description}")


def main():
    if len(sys.argv) >= 2 and sys.argv[1] in ("-l", "--list"):
        list_ports()
        return

    parser = argparse.ArgumentParser(description="USB TX throughput test")
    parser.add_argument("port", nargs="?", default="COM26")
    parser.add_argument("-t", type=float, default=10.0, help="duration per round (s)")
    parser.add_argument("-n", type=int, default=10, help="number of rounds")
    args = parser.parse_args()

    ser = serial.Serial(args.port, timeout=3)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    # MCU 上电就开始发了，直接读
    print("=== TX (MCU -> PC) ===")
    hdr = f"{'round':>6s}  {'bytes':>10s}  {'bw':>14s}  {'time':>8s}"
    print(hdr)
    print("-" * len(hdr))

    for r in range(1, args.n + 1):
        start = time.monotonic()
        rx = 0
        while time.monotonic() - start < args.t:
            buf = ser.read(65536)
            if buf:
                rx += len(buf)
        elapsed = time.monotonic() - start

        bw = rx / elapsed
        print(f"  {r:>3d}   {rx:>10d}  {bw/1024/1024:>8.2f} MiB/s  {elapsed:>7.2f}s")

    ser.close()


if __name__ == "__main__":
    main()
