from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _uci import UciEngine, default_engine_path  # noqa: E402


SCORE_RE = re.compile(r"\bscore (cp|mate) (-?\d+)")
DEPTH_RE = re.compile(r"\bdepth (\d+)")
PV_RE = re.compile(r"\bpv (.+)$")
BESTMOVE_RE = re.compile(r"^bestmove (\S+)")


@dataclass(frozen=True, slots=True)
class SearchAnnotation:
    score_cp: int
    depth: int
    best_move: str
    pv: str


def parse_annotation(lines: list[str]) -> SearchAnnotation:
    score_cp = 0
    depth = 0
    pv = ""
    best_move = ""
    for line in lines:
        depth_match = DEPTH_RE.search(line)
        if depth_match:
            depth = int(depth_match.group(1))
        score_match = SCORE_RE.search(line)
        if score_match:
            score_kind, score_value = score_match.groups()
            value = int(score_value)
            if score_kind == "mate":
                score_cp = (32000 - abs(value)) * (1 if value >= 0 else -1)
            else:
                score_cp = value
        pv_match = PV_RE.search(line)
        if pv_match:
            pv = pv_match.group(1).strip()
        bestmove_match = BESTMOVE_RE.match(line)
        if bestmove_match:
            best_move = bestmove_match.group(1)
    if not best_move:
        raise RuntimeError(f"Missing bestmove while annotating position. Output was: {lines}")
    return SearchAnnotation(score_cp=score_cp, depth=depth, best_move=best_move, pv=pv)


def main() -> int:
    parser = argparse.ArgumentParser(description="Annotate JSONL training positions with DeadFish search scores.")
    parser.add_argument("--engine", type=Path, default=default_engine_path(), help="Path to the DeadFish executable.")
    parser.add_argument("--input", type=Path, required=True, help="Input JSONL positions.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "output" / "positions_annotated.jsonl",
        help="Output JSONL path.",
    )
    parser.add_argument("--depth", type=int, default=6, help="Search depth.")
    parser.add_argument("--movetime", type=int, default=0, help="Optional fixed movetime instead of depth.")
    parser.add_argument("--hash", type=int, default=64, help="Hash size in MB.")
    parser.add_argument("--limit", type=int, default=0, help="Optional maximum number of positions to annotate.")
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if not engine_path.exists():
        raise FileNotFoundError(f"Engine executable not found: {engine_path}")
    if not input_path.exists():
        raise FileNotFoundError(f"Input JSONL not found: {input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    engine = UciEngine(engine_path)
    count = 0
    try:
        engine.send("uci")
        engine.read_until(lambda line, _: line == "uciok")
        engine.send(f"setoption name Hash value {args.hash}")
        engine.send("setoption name OwnBook value false")
        engine.send("isready")
        engine.read_until(lambda line, _: line == "readyok")

        with input_path.open("r", encoding="utf-8") as in_handle, output_path.open("w", encoding="utf-8") as out_handle:
            for raw_line in in_handle:
                line = raw_line.strip()
                if not line:
                    continue
                record = json.loads(line)
                fen = str(record["fen"])
                engine.read_available()
                engine.send(f"position fen {fen}")
                if args.movetime > 0:
                    engine.send(f"go movetime {args.movetime}")
                    timeout = max(5.0, args.movetime / 1000.0 + 2.0)
                else:
                    engine.send(f"go depth {args.depth}")
                    timeout = max(5.0, args.depth * 1.5)
                lines = engine.read_until(lambda text, _: text.startswith("bestmove "), timeout=timeout)
                annotation = parse_annotation(lines)
                record["score_cp"] = annotation.score_cp
                record["annotated_depth"] = annotation.depth
                record["best_move"] = annotation.best_move
                record["pv"] = annotation.pv
                out_handle.write(json.dumps(record) + "\n")
                count += 1
                if count % 100 == 0:
                    print(f"Annotated {count} positions...")
                if args.limit > 0 and count >= args.limit:
                    break
    finally:
        engine.quit()

    print(f"Annotated {count} positions to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
