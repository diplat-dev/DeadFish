from __future__ import annotations

import argparse
import statistics
import subprocess
from pathlib import Path

from _uci import default_engine_path
from bench_compare import run_bench


def default_native_engine_path() -> Path:
    return default_engine_path().with_name("deadfish_native.exe")


def maybe_build_all(root: Path) -> None:
    subprocess.run(
        [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts" / "build_native.ps1"),
            "-Target",
            "All",
        ],
        check=True,
        text=True,
    )


def sample_bench(engine_path: Path, depth: int, movetime: int, repeat: int) -> list[tuple[int, int]]:
    samples: list[tuple[int, int]] = []
    for _ in range(repeat):
        _, nodes, elapsed = run_bench(engine_path, depth, movetime)
        samples.append((nodes, elapsed))
    return samples


def summarize(label: str, samples: list[tuple[int, int]]) -> tuple[float, float]:
    times = [elapsed for _, elapsed in samples]
    nodes = [node_count for node_count, _ in samples]
    mean_time = statistics.fmean(times)
    mean_nodes = statistics.fmean(nodes)
    mean_nps = mean_nodes * 1000.0 / mean_time if mean_time > 0 else mean_nodes
    print(
        f"{label}: mean_time_ms={mean_time:.1f} "
        f"min_time_ms={min(times)} max_time_ms={max(times)} "
        f"mean_nodes={mean_nodes:.0f} mean_nps={mean_nps:.0f}"
    )
    return mean_time, mean_nps


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile DeadFish bench speed across generic and native builds.")
    parser.add_argument("--engine-generic", type=Path, default=default_engine_path(), help="Generic engine path.")
    parser.add_argument("--engine-native", type=Path, default=default_native_engine_path(), help="Native-tuned engine path.")
    parser.add_argument("--depth", type=int, default=6, help="Bench depth.")
    parser.add_argument("--movetime", type=int, default=0, help="Optional per-position movetime.")
    parser.add_argument("--repeat", type=int, default=5, help="Number of bench runs per binary.")
    parser.add_argument("--build", action="store_true", help="Rebuild both generic and native binaries before profiling.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    if args.build:
        maybe_build_all(root)

    generic_path = args.engine_generic.resolve()
    native_path = args.engine_native.resolve()
    for path in (generic_path, native_path):
        if not path.exists():
            raise FileNotFoundError(f"Engine executable not found: {path}")

    generic_samples = sample_bench(generic_path, args.depth, args.movetime, args.repeat)
    native_samples = sample_bench(native_path, args.depth, args.movetime, args.repeat)

    generic_time, generic_nps = summarize("generic", generic_samples)
    native_time, native_nps = summarize("native", native_samples)

    if native_time > 0:
        print(f"native speedup vs generic: {generic_time / native_time:.3f}x by mean elapsed time")
    if generic_nps > 0:
        print(f"native nps gain vs generic: {native_nps / generic_nps:.3f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
