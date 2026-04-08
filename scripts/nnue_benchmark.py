from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from _uci import preferred_engine_path


SCORE_RE = re.compile(r"Score of (.+?) vs (.+?):\s+([0-9.]+)\s+-\s+([0-9.]+)\s+-\s+([0-9.]+)")
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class MatchPreset:
    games: int
    tc: str
    concurrency: int


PRESETS = {
    "quick": MatchPreset(games=24, tc="1+0.01", concurrency=2),
    "strength": MatchPreset(games=100, tc="1+0.01", concurrency=2),
    "acceptance": MatchPreset(games=200, tc="8+0.08", concurrency=1),
}


def resolve_engine_path(requested: Path) -> Path:
    path = requested.resolve()
    if path.exists():
        return path
    raise FileNotFoundError(f"Engine executable not found: {path}")


def parse_score(output: str) -> tuple[str, str, float, float, float]:
    score_line = ""
    for line in output.splitlines():
        if "Score of " in line:
            score_line = line.strip()
    match = SCORE_RE.search(score_line)
    if not match:
        raise RuntimeError("Could not parse the final cutechess score line.")
    return (
        match.group(1),
        match.group(2),
        float(match.group(3)),
        float(match.group(4)),
        float(match.group(5)),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the standardized DeadFish classical-vs-NNUE benchmark gate.")
    parser.add_argument("--cutechess", default="cutechess-cli", help="cutechess-cli executable.")
    parser.add_argument("--engine", type=Path, default=preferred_engine_path(), help="Path to the DeadFish executable.")
    parser.add_argument("--eval-file", type=Path, required=True, help="Exported NNUE file for the NNUE side.")
    parser.add_argument("--mode", choices=sorted(PRESETS), default="quick", help="Benchmark preset.")
    parser.add_argument("--games", type=int, default=0, help="Optional override for the preset game count.")
    parser.add_argument("--tc", default="", help="Optional override for the preset time control.")
    parser.add_argument("--concurrency", type=int, default=0, help="Optional override for the preset concurrency.")
    parser.add_argument("--hash", type=int, default=64, help="Hash size for both engine instances in MB.")
    parser.add_argument(
        "--opening-file",
        type=Path,
        default=ROOT / "data" / "nnue_openings.pgn",
        help="Fixed balanced opening suite.",
    )
    parser.add_argument("--opening-order", default="sequential", help="Cutechess opening order.")
    parser.add_argument("--opening-plies", type=int, default=8, help="Cutechess opening plies.")
    parser.add_argument(
        "--require-positive",
        action="store_true",
        help="Exit non-zero if the NNUE side does not outscore the classical side.",
    )
    args = parser.parse_args()

    preset = PRESETS[args.mode]
    games = args.games if args.games > 0 else preset.games
    tc = args.tc or preset.tc
    concurrency = args.concurrency if args.concurrency > 0 else preset.concurrency

    engine_path = resolve_engine_path(args.engine)
    eval_file = args.eval_file.resolve()
    opening_file = args.opening_file.resolve()
    if not eval_file.exists():
        raise FileNotFoundError(f"NNUE file not found: {eval_file}")
    if not opening_file.exists():
        raise FileNotFoundError(f"Opening suite not found: {opening_file}")

    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "cutechess_match.py"),
        "--cutechess",
        args.cutechess,
        "--engine-a",
        str(engine_path),
        "--engine-b",
        str(engine_path),
        "--name-a",
        "DeadFish-Classical",
        "--name-b",
        "DeadFish-NNUE",
        "--option-a",
        f"Hash={args.hash}",
        "--option-a",
        "OwnBook=false",
        "--option-a",
        "UseNNUE=false",
        "--option-b",
        f"Hash={args.hash}",
        "--option-b",
        "OwnBook=false",
        "--option-b",
        "UseNNUE=true",
        "--option-b",
        f"EvalFile={eval_file}",
        "--games",
        str(games),
        "--concurrency",
        str(concurrency),
        "--tc",
        tc,
        "--opening-file",
        str(opening_file),
        "--opening-format",
        "pgn",
        "--opening-order",
        args.opening_order,
        "--opening-plies",
        str(args.opening_plies),
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        return result.returncode

    name_a, name_b, score_a, score_b, draws = parse_score(result.stdout)
    passed = score_b > score_a
    print(
        f"\nGate summary: {name_b} {'PASS' if passed else 'FAIL'} "
        f"({score_b:.1f} vs {score_a:.1f}, draws={draws:.1f}, mode={args.mode}, tc={tc}, games={games})"
    )
    if args.require_positive and not passed:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
