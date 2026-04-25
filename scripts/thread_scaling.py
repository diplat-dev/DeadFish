from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._uci import UciEngine, preferred_engine_path  # noqa: E402


INFO_RE = re.compile(r"\b(depth|nodes|nps|time)\s+(-?\d+)")


def wait_for(engine: UciEngine, token: str, timeout: float = 10.0) -> list[str]:
    return engine.read_until(lambda line, _lines: line.strip() == token, timeout=timeout)


def configure(engine: UciEngine, threads: int, hash_mb: int) -> None:
    wait_for(engine, "uciok")
    engine.send(f"setoption name Threads value {threads}")
    engine.send(f"setoption name Hash value {hash_mb}")
    engine.send("setoption name OwnBook value false")
    engine.send("setoption name UseNNUE value false")
    engine.send("isready")
    wait_for(engine, "readyok")


def parse_info(line: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for key, value in INFO_RE.findall(line):
        values[key] = int(value)
    return values


def run_once(engine_path: Path, threads: int, hash_mb: int, go_command: str, fen: str) -> dict[str, object]:
    engine = UciEngine(engine_path)
    try:
        engine.send("uci")
        configure(engine, threads, hash_mb)
        engine.send(f"position fen {fen}" if fen != "startpos" else "position startpos")
        start = time.monotonic()
        engine.send(go_command)
        lines = engine.read_until(lambda line, _lines: line.startswith("bestmove "), timeout=120.0)
        elapsed_ms = int((time.monotonic() - start) * 1000)
    finally:
        engine.quit()

    bestmove = "0000"
    last_info: dict[str, int] = {}
    for line in lines:
        if line.startswith("info "):
            parsed = parse_info(line)
            if parsed:
                last_info.update(parsed)
        elif line.startswith("bestmove "):
            parts = line.split()
            if len(parts) > 1:
                bestmove = parts[1]

    nodes = int(last_info.get("nodes", 0))
    nps = int(last_info.get("nps", 0))
    if nps == 0 and elapsed_ms > 0 and nodes > 0:
        nps = nodes * 1000 // elapsed_ms
    return {
        "threads": threads,
        "depth": int(last_info.get("depth", 0)),
        "nodes": nodes,
        "nps": nps,
        "time_ms": int(last_info.get("time", elapsed_ms)),
        "wall_ms": elapsed_ms,
        "bestmove": bestmove,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure DeadFish UCI scaling across thread counts.")
    parser.add_argument("--engine", type=Path, default=preferred_engine_path(), help="Engine executable to measure.")
    parser.add_argument("--threads", default="1,2,4,8,12,16,20", help="Comma-separated thread counts.")
    parser.add_argument("--hash", type=int, default=64, help="Hash size in MB.")
    parser.add_argument("--movetime", type=int, default=1000, help="Per-run movetime in ms; ignored with --nodes.")
    parser.add_argument("--nodes", type=int, default=0, help="Optional node budget per run.")
    parser.add_argument("--fen", default="startpos", help="FEN to search, or startpos.")
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    if not engine_path.exists():
        raise FileNotFoundError(f"Engine executable not found: {engine_path}")

    thread_counts = [int(part.strip()) for part in args.threads.split(",") if part.strip()]
    go_command = f"go nodes {args.nodes}" if args.nodes > 0 else f"go movetime {args.movetime}"
    print("threads,depth,nodes,nps,time_ms,wall_ms,bestmove")
    for threads in thread_counts:
        result = run_once(engine_path, threads, args.hash, go_command, args.fen)
        print(
            f"{result['threads']},{result['depth']},{result['nodes']},{result['nps']},"
            f"{result['time_ms']},{result['wall_ms']},{result['bestmove']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
