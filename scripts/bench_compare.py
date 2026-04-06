from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from _uci import default_engine_path


TOTAL_NODES_RE = re.compile(r"^total nodes (\d+)$", re.MULTILINE)
TOTAL_TIME_RE = re.compile(r"^total time\s+(\d+) ms$", re.MULTILINE)


def run_bench(engine_path: Path, depth: int, movetime: int) -> tuple[str, int, int]:
    args = [str(engine_path), "bench"]
    if depth > 0:
        args.extend(["--depth", str(depth)])
    if movetime > 0:
        args.extend(["--movetime", str(movetime)])

    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Bench failed: {engine_path}")

    output = result.stdout
    nodes_match = TOTAL_NODES_RE.search(output)
    time_match = TOTAL_TIME_RE.search(output)
    if not nodes_match or not time_match:
        raise RuntimeError(f"Failed to parse bench output for {engine_path}.\n{output}")
    return output, int(nodes_match.group(1)), int(time_match.group(1))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare DeadFish bench output across two executables.")
    parser.add_argument("--engine-a", type=Path, default=default_engine_path(), help="Path to engine A.")
    parser.add_argument("--engine-b", type=Path, required=True, help="Path to engine B.")
    parser.add_argument("--depth", type=int, default=6, help="Fixed search depth for bench.")
    parser.add_argument("--movetime", type=int, default=0, help="Optional per-position movetime.")
    args = parser.parse_args()

    engine_a_path = args.engine_a.resolve()
    engine_b_path = args.engine_b.resolve()
    for path in (engine_a_path, engine_b_path):
        if not path.exists():
            raise FileNotFoundError(f"Engine executable not found: {path}")

    _, nodes_a, time_a = run_bench(engine_a_path, args.depth, args.movetime)
    _, nodes_b, time_b = run_bench(engine_b_path, args.depth, args.movetime)

    nps_a = nodes_a * 1000 / time_a if time_a > 0 else float(nodes_a)
    nps_b = nodes_b * 1000 / time_b if time_b > 0 else float(nodes_b)

    print(f"A: nodes={nodes_a} time_ms={time_a} nps={nps_a:.0f}")
    print(f"B: nodes={nodes_b} time_ms={time_b} nps={nps_b:.0f}")
    if time_a > 0 and time_b > 0:
        print(f"B vs A speed ratio: {time_a / time_b:.3f}x faster by elapsed time")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
