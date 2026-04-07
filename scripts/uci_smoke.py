from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from _uci import UciEngine, default_engine_path, legal_moves


BESTMOVE_RE = re.compile(r"^bestmove\s+(\S+)")


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def parse_bestmove(lines: list[str]) -> str:
    for line in reversed(lines):
        match = BESTMOVE_RE.match(line)
        if match:
            return match.group(1)
    raise AssertionError(f"No bestmove line found in output: {lines}")


def expect_legal(engine_path: Path, fen: str, moves: list[str], bestmove: str, label: str) -> None:
    expect(bestmove in legal_moves(engine_path, fen, moves), label)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a smoke test against DeadFish's UCI loop.")
    parser.add_argument("--engine", type=Path, default=default_engine_path(), help="Path to the engine executable.")
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    if not engine_path.exists():
        raise FileNotFoundError(f"Engine executable not found: {engine_path}")

    engine = UciEngine(engine_path)
    try:
        engine.send("uci")
        lines = engine.read_until(lambda line, _: line == "uciok")
        for option in ("Hash", "Clear Hash", "OwnBook", "BookPath", "SyzygyPath", "SyzygyProbeLimit", "MoveOverhead"):
            expect(any(f"option name {option} " in line for line in lines), f"UCI advertises {option}")
        expect(any(line == "id name DeadFish" for line in lines), "UCI id name is reported")

        engine.send("isready")
        engine.read_until(lambda line, _: line == "readyok")
        print("ok - readyok after startup")

        engine.send("setoption name Hash value 64")
        engine.send("setoption name OwnBook value false")
        engine.send("setoption name MoveOverhead value 15")
        engine.send("setoption name BookPath value Z:/deadfish-missing/book.bin")
        engine.send("setoption name SyzygyPath value Z:/deadfish-missing/syzygy")
        engine.send("setoption name SyzygyProbeLimit value 6")
        engine.send("isready")
        engine.read_until(lambda line, _: line == "readyok")
        print("ok - readyok after option updates")

        start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        opening_moves = ["e2e4", "e7e5"]
        engine.send("ucinewgame")
        engine.send("position startpos moves e2e4 e7e5")
        engine.send("go depth 3")
        lines = engine.read_until(lambda line, _: line.startswith("bestmove "))
        expect(any(line.startswith("info depth ") for line in lines), "depth search reports info lines")
        depth_bestmove = parse_bestmove(lines)
        expect_legal(engine_path, start_fen, opening_moves, depth_bestmove, "depth search bestmove is legal")

        fen = "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"
        engine.send(f"position fen {fen}")
        engine.send("go movetime 50")
        lines = engine.read_until(lambda line, _: line.startswith("bestmove "))
        movetime_bestmove = parse_bestmove(lines)
        expect_legal(engine_path, fen, [], movetime_bestmove, "movetime search bestmove is legal")

        engine.send("position startpos")
        engine.send("go infinite")
        time.sleep(0.15)
        engine.send("stop")
        lines = engine.read_until(lambda line, _: line.startswith("bestmove "), timeout=10.0)
        infinite_bestmove = parse_bestmove(lines)
        expect_legal(engine_path, start_fen, [], infinite_bestmove, "infinite search stops with a legal bestmove")

        print("UCI smoke checks passed.")
        return 0
    finally:
        engine.quit()


if __name__ == "__main__":
    raise SystemExit(main())
