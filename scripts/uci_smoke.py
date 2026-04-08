from __future__ import annotations

import argparse
import re
import struct
import tempfile
import time
from pathlib import Path

from _uci import UciEngine, default_engine_path, evaluate, legal_moves


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


def make_square(file: int, rank: int) -> int:
    return rank * 8 + file


def mirror_square(square: int) -> int:
    return square ^ 56


def piece_bucket(piece: str, perspective: str) -> int:
    color_offset = 0 if piece[0] == perspective else 5
    piece_offset = {"P": 0, "N": 1, "B": 2, "R": 3, "Q": 4}[piece[1]]
    return color_offset + piece_offset


def orient_square(square: int, perspective: str) -> int:
    return square if perspective == "w" else mirror_square(square)


def feature_index(perspective: str, king_square: int, piece: str, square: int) -> int:
    return orient_square(king_square, perspective) * (10 * 64) + piece_bucket(piece, perspective) * 64 + orient_square(square, perspective)


def write_valid_nnue_fixture(path: Path) -> None:
    feature_count = 64 * 10 * 64
    accumulator_size = 1
    hidden_size = 2

    weights = [0.0] * feature_count
    white_king = make_square(4, 0)
    black_king = make_square(4, 7)
    white_queen_d4 = make_square(3, 3)
    black_queen_d5 = make_square(3, 4)
    weights[feature_index("w", white_king, "wQ", white_queen_d4)] = 0.40
    weights[feature_index("b", black_king, "wQ", white_queen_d4)] = 0.10
    weights[feature_index("w", white_king, "bQ", black_queen_d5)] = 0.05
    weights[feature_index("b", black_king, "bQ", black_queen_d5)] = 0.35

    with path.open("wb") as handle:
        handle.write(struct.pack("<8sIIIf", b"DFNNUE1\0", feature_count, accumulator_size, hidden_size, 100.0))
        handle.write(struct.pack(f"<{feature_count}f", *weights))
        handle.write(struct.pack("<f", 0.0))
        handle.write(struct.pack("<4f", 1.0, -1.0, -1.0, 1.0))
        handle.write(struct.pack("<2f", 0.0, 0.0))
        handle.write(struct.pack("<2f", 1.0, -1.0))
        handle.write(struct.pack("<f", 0.0))


def write_wrong_magic_fixture(path: Path) -> None:
    with path.open("wb") as handle:
        handle.write(struct.pack("<8sIIIf", b"BADNNUE\0", 64 * 10 * 64, 1, 2, 100.0))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a smoke test against DeadFish's UCI loop.")
    parser.add_argument("--engine", type=Path, default=default_engine_path(), help="Path to the engine executable.")
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    if not engine_path.exists():
        raise FileNotFoundError(f"Engine executable not found: {engine_path}")

    temp_dir = Path(tempfile.gettempdir())
    valid_fixture = temp_dir / "deadfish-uci-valid-fixture.nnue"
    wrong_magic_fixture = temp_dir / "deadfish-uci-wrong-magic.nnue"
    write_valid_nnue_fixture(valid_fixture)
    write_wrong_magic_fixture(wrong_magic_fixture)

    nnue_fen = "4k3/8/8/8/3Q4/8/8/4K3 w - - 0 1"
    nnue_eval = evaluate(engine_path, nnue_fen, use_nnue=True, eval_file=valid_fixture)
    expect(nnue_eval["nnueResidualScore"] == 30, "CLI eval returns the expected NNUE residual score")
    expect(nnue_eval["score"] == nnue_eval["classicalBackboneScore"] + nnue_eval["nnueResidualScore"],
           "CLI eval combines backbone and NNUE residual into the final score")
    expect(nnue_eval["mode"] == "hybrid", "CLI eval reports hybrid mode when a valid network is active")
    expect(nnue_eval["nnueActive"] is True, "CLI eval marks NNUE active for a valid network")

    classical_eval = evaluate(engine_path, nnue_fen, use_nnue=False, eval_file=valid_fixture)
    expect(classical_eval["mode"] == "classical", "CLI eval respects --use-nnue false")
    expect(classical_eval["nnueActive"] is False, "CLI eval reports NNUE inactive when disabled")

    fallback_eval = evaluate(engine_path, nnue_fen, use_nnue=True, eval_file=wrong_magic_fixture)
    expect(fallback_eval["mode"] == "classical", "CLI eval falls back to classical mode for an invalid network")
    expect(fallback_eval["nnueLoaded"] is False, "CLI eval reports invalid NNUE loads as unloaded")

    engine = UciEngine(engine_path)
    try:
        engine.send("uci")
        lines = engine.read_until(lambda line, _: line == "uciok")
        for option in ("Hash", "Clear Hash", "UseNNUE", "EvalFile", "OwnBook", "BookPath", "SyzygyPath", "SyzygyProbeLimit", "MoveOverhead"):
            expect(any(f"option name {option} " in line for line in lines), f"UCI advertises {option}")
        expect(any("option name UseNNUE type check default false" in line for line in lines), "UseNNUE defaults to false")
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

        engine.send(f"setoption name EvalFile value {valid_fixture}")
        engine.send("setoption name UseNNUE value true")
        engine.send("isready")
        lines = engine.read_until(lambda line, _: line == "readyok")
        expect(any("info string Loaded NNUE from" in line for line in lines), "valid EvalFile reports successful NNUE load")

        engine.send(f"position fen {nnue_fen}")
        engine.send("go depth 1")
        lines = engine.read_until(lambda line, _: line.startswith("bestmove "))
        nnue_bestmove = parse_bestmove(lines)
        expect_legal(engine_path, nnue_fen, [], nnue_bestmove, "NNUE-active search bestmove is legal")

        engine.send("setoption name UseNNUE value false")
        engine.send("isready")
        lines = engine.read_until(lambda line, _: line == "readyok")
        expect(any("info string Loaded NNUE from" in line and "inactive because UseNNUE=false" in line for line in lines),
               "UseNNUE=false reports classical fallback while keeping the network loaded")

        engine.send("setoption name UseNNUE value true")
        engine.send("setoption name EvalFile value")
        engine.send("isready")
        lines = engine.read_until(lambda line, _: line == "readyok")
        expect(any("info string NNUE eval file not set; using classical eval." in line for line in lines),
               "clearing EvalFile reports classical fallback")

        engine.send(f"setoption name EvalFile value {wrong_magic_fixture}")
        engine.send("isready")
        lines = engine.read_until(lambda line, _: line == "readyok")
        expect(any("info string NNUE load failed: wrong magic" in line for line in lines),
               "invalid EvalFile reports load failure and fallback")

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
        valid_fixture.unlink(missing_ok=True)
        wrong_magic_fixture.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
