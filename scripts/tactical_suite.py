from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from _uci import default_engine_path, run_cli


@dataclass
class TacticalCase:
    label: str
    fen: str
    best_moves: list[str]
    depth: int


def load_suite(path: Path) -> list[TacticalCase]:
    cases: list[TacticalCase] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        label, fen, best_moves, depth = [part.strip() for part in line.split("|")]
        cases.append(
            TacticalCase(
                label=label,
                fen=fen,
                best_moves=[move.strip() for move in best_moves.split(",") if move.strip()],
                depth=int(depth),
            )
        )
    return cases


def search_bestmove(engine_path: Path, fen: str, depth: int, movetime: int) -> str:
    args = ["search", "--fen", fen, "--json"]
    if movetime > 0:
        args.extend(["--movetime", str(movetime)])
    else:
        args.extend(["--depth", str(depth)])
    output = run_cli(engine_path, args)
    import json

    return json.loads(output)["bestMove"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DeadFish against a fixed tactical regression suite.")
    parser.add_argument("--engine", type=Path, default=default_engine_path(), help="Path to the engine executable.")
    parser.add_argument("--suite", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "tactical_suite.txt")
    parser.add_argument("--movetime", type=int, default=0, help="Use movetime instead of per-position depth.")
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    suite_path = args.suite.resolve()
    if not engine_path.exists():
        raise FileNotFoundError(f"Engine executable not found: {engine_path}")
    if not suite_path.exists():
        raise FileNotFoundError(f"Tactical suite not found: {suite_path}")

    cases = load_suite(suite_path)
    passed = 0
    for case in cases:
        best_move = search_bestmove(engine_path, case.fen, case.depth, args.movetime)
        ok = best_move in case.best_moves
        status = "ok" if ok else "fail"
        print(f"{status} - {case.label}: got {best_move}, expected one of {', '.join(case.best_moves)}")
        passed += int(ok)

    total = len(cases)
    print(f"\nTactical score: {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
