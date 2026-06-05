from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class Sample:
    t_ms: float
    duty: float
    temp_c: float


@dataclass
class FitResult:
    duty: float
    y0: float
    k: float
    tau_s: float
    delay_s: float
    rmse_c: float
    start_index: int
    points: int


def parse_groups(path: Path) -> list[list[Sample]]:
    text = path.read_text(encoding="utf-8")
    groups: list[list[Sample]] = []
    current: list[Sample] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                groups.append(current)
                current = []
            continue

        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            raise ValueError(f"bad line: {raw_line!r}")

        current.append(Sample(float(parts[0]), float(parts[1]), float(parts[2])))

    if current:
        groups.append(current)

    return groups


def auto_trim_group(samples: list[Sample]) -> tuple[list[Sample], int]:
    if len(samples) < 3:
        return samples, 0

    min_idx = min(range(len(samples)), key=lambda i: samples[i].temp_c)
    trimmed = samples[min_idx:]
    if len(trimmed) < 3:
        return samples, 0
    return trimmed, min_idx


def to_relative_seconds(samples: list[Sample]) -> tuple[list[float], list[float]]:
    t0 = samples[0].t_ms
    times = [(s.t_ms - t0) / 1000.0 for s in samples]
    temps = [s.temp_c for s in samples]
    return times, temps


def basis_value(t_s: float, tau_s: float, delay_s: float) -> float:
    if t_s <= delay_s:
        return 0.0
    return 1.0 - math.exp(-(t_s - delay_s) / tau_s)


def fit_group(samples: list[Sample], auto_trim: bool) -> FitResult:
    duty = samples[0].duty
    if duty <= 0.0:
        raise ValueError("duty must be > 0 for fitting")

    fit_samples = samples
    start_index = 0
    if auto_trim:
        fit_samples, start_index = auto_trim_group(samples)

    times, temps = to_relative_seconds(fit_samples)
    y0 = temps[0]

    total_time = max(times[-1], 1.0)
    best: FitResult | None = None

    tau_candidates = logspace(1.0, max(2.0, total_time * 1.5), 120)
    delay_candidates = linspace(0.0, min(total_time * 0.3, 20.0), 80)

    for tau_s in tau_candidates:
        for delay_s in delay_candidates:
            phi = [duty * basis_value(t_s, tau_s, delay_s) for t_s in times]
            denom = sum(v * v for v in phi)
            if denom <= 1e-12:
                continue

            numer = sum(v * (y - y0) for v, y in zip(phi, temps))
            k = numer / denom

            pred = [y0 + k * v for v in phi]
            err2 = sum((y - yp) ** 2 for y, yp in zip(temps, pred))
            rmse = math.sqrt(err2 / len(temps))

            if best is None or rmse < best.rmse_c:
                best = FitResult(
                    duty=duty,
                    y0=y0,
                    k=k,
                    tau_s=tau_s,
                    delay_s=delay_s,
                    rmse_c=rmse,
                    start_index=start_index,
                    points=len(fit_samples),
                )

    if best is None:
        raise RuntimeError("fit failed")
    return best


def logspace(start: float, stop: float, count: int) -> list[float]:
    if count <= 1:
        return [start]
    a = math.log(start)
    b = math.log(stop)
    return [math.exp(a + (b - a) * i / (count - 1)) for i in range(count)]


def linspace(start: float, stop: float, count: int) -> list[float]:
    if count <= 1:
        return [start]
    return [start + (stop - start) * i / (count - 1) for i in range(count)]


def summarize(results: Iterable[FitResult]) -> str:
    lines = []
    lines.append("model: T(t) = T0 + K * duty * (1 - exp(-(t - L)/tau)), t > L")
    lines.append("columns: duty, T0_degC, K_degC_per_duty, tau_s, delay_s, rmse_degC, start_idx, points")
    for r in results:
        lines.append(
            f"{r.duty:.3f}, {r.y0:.3f}, {r.k:.3f}, {r.tau_s:.3f}, "
            f"{r.delay_s:.3f}, {r.rmse_c:.4f}, {r.start_index}, {r.points}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit first-order-plus-dead-time heater models from imu_data logs."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="temp/imu_data.md",
        help="input data file, default: temp/imu_data.md",
    )
    parser.add_argument(
        "--no-auto-trim",
        action="store_true",
        help="disable auto-trim of the initial downward drift segment",
    )
    args = parser.parse_args()

    path = Path(args.input)
    groups = parse_groups(path)
    if not groups:
        raise RuntimeError(f"no groups found in {path}")

    results = [fit_group(g, auto_trim=not args.no_auto_trim) for g in groups]
    print(summarize(results))


if __name__ == "__main__":
    main()
