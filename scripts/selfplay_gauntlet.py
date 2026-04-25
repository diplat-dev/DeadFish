from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from _uci import UciEngine, apply_move, default_engine_path, legal_moves, status


START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
OPENINGS = [
    ("Open Game", ["e2e4", "e7e5"]),
    ("Queen Pawn", ["d2d4", "d7d5"]),
    ("English", ["c2c4", "e7e5"]),
    ("Reti", ["g1f3", "d7d5"]),
]


@dataclass
class Score:
    wins: int = 0
    losses: int = 0
    draws: int = 0

    def add_result(self, result: str) -> None:
        if result == "win":
            self.wins += 1
        elif result == "loss":
            self.losses += 1
        else:
            self.draws += 1

    @property
    def points(self) -> float:
        return self.wins + self.draws * 0.5


def parse_bestmove(lines: list[str]) -> str:
    for line in reversed(lines):
        if line.startswith("bestmove "):
            return line.split()[1]
    raise RuntimeError(f"Missing bestmove in output: {lines}")


def configure_engine(engine: UciEngine, hash_mb: int, threads: int) -> None:
    engine.send("uci")
    engine.read_until(lambda line, _: line == "uciok")
    engine.send(f"setoption name Hash value {hash_mb}")
    engine.send(f"setoption name Threads value {threads}")
    engine.send("setoption name OwnBook value false")
    engine.send("ucinewgame")
    engine.send("isready")
    engine.read_until(lambda line, _: line == "readyok")


def play_game(
    white_engine: UciEngine,
    black_engine: UciEngine,
    arbiter_path: Path,
    movetime_ms: int,
    opening_moves: list[str],
    max_plies: int,
) -> tuple[str, str, int]:
    fen = START_FEN
    for move in opening_moves:
        fen = apply_move(arbiter_path, fen, move)

    for ply in range(len(opening_moves), max_plies):
        state = status(arbiter_path, fen)
        if state["checkmate"]:
            winner = "black" if state["turn"] == "w" else "white"
            return winner, "checkmate", ply
        if state["draw"] or state["stalemate"] or state["legalCount"] == 0:
            return "draw", "draw", ply

        side = state["turn"]
        engine = white_engine if side == "w" else black_engine
        engine.send(f"position fen {fen}")
        engine.send(f"go movetime {movetime_ms}")
        lines = engine.read_until(lambda line, _: line.startswith("bestmove "), timeout=15.0)
        move = parse_bestmove(lines)
        if move not in legal_moves(arbiter_path, fen):
            raise RuntimeError(f"Illegal move from engine on {fen}: {move}")
        fen = apply_move(arbiter_path, fen, move)

    return "draw", "max-plies", max_plies


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a small UCI self-play gauntlet.")
    parser.add_argument("--engine-a", type=Path, default=default_engine_path(), help="Path to engine A.")
    parser.add_argument("--engine-b", type=Path, required=True, help="Path to engine B.")
    parser.add_argument("--arbiter", type=Path, default=default_engine_path(), help="Path to the arbiter CLI.")
    parser.add_argument("--movetime", type=int, default=75, help="Per-move movetime in milliseconds.")
    parser.add_argument("--max-plies", type=int, default=120, help="Maximum plies before adjudicating a draw.")
    parser.add_argument("--hash", type=int, default=64, help="Hash size for both engines in MB.")
    parser.add_argument("--threads-a", type=int, default=1, help="Threads option for engine A.")
    parser.add_argument("--threads-b", type=int, default=1, help="Threads option for engine B.")
    args = parser.parse_args()

    engine_a_path = args.engine_a.resolve()
    engine_b_path = args.engine_b.resolve()
    arbiter_path = args.arbiter.resolve()
    for path in (engine_a_path, engine_b_path, arbiter_path):
        if not path.exists():
            raise FileNotFoundError(f"Required executable not found: {path}")

    engine_a = UciEngine(engine_a_path)
    engine_b = UciEngine(engine_b_path)
    score_a = Score()
    score_b = Score()
    try:
        configure_engine(engine_a, args.hash, args.threads_a)
        configure_engine(engine_b, args.hash, args.threads_b)

        pairings = [
            ("A", "B", engine_a, engine_b),
            ("B", "A", engine_b, engine_a),
        ]

        game_index = 1
        for opening_name, opening_moves in OPENINGS:
            for white_name, black_name, white_engine, black_engine in pairings:
                white_engine.send("ucinewgame")
                black_engine.send("ucinewgame")
                result, reason, plies = play_game(
                    white_engine=white_engine,
                    black_engine=black_engine,
                    arbiter_path=arbiter_path,
                    movetime_ms=args.movetime,
                    opening_moves=opening_moves,
                    max_plies=args.max_plies,
                )

                if result == "white":
                    if white_name == "A":
                        score_a.add_result("win")
                        score_b.add_result("loss")
                    else:
                        score_a.add_result("loss")
                        score_b.add_result("win")
                    score_text = "1-0"
                elif result == "black":
                    if black_name == "A":
                        score_a.add_result("win")
                        score_b.add_result("loss")
                    else:
                        score_a.add_result("loss")
                        score_b.add_result("win")
                    score_text = "0-1"
                else:
                    score_a.add_result("draw")
                    score_b.add_result("draw")
                    score_text = "1/2-1/2"

                print(
                    f"game {game_index}: {opening_name} | "
                    f"white={white_name} black={black_name} | "
                    f"result={score_text} reason={reason} plies={plies}"
                )
                game_index += 1

        print(
            f"\nA score: {score_a.points:.1f}/"
            f"{score_a.wins + score_a.losses + score_a.draws} "
            f"(W {score_a.wins} D {score_a.draws} L {score_a.losses})"
        )
        print(
            f"B score: {score_b.points:.1f}/"
            f"{score_b.wins + score_b.losses + score_b.draws} "
            f"(W {score_b.wins} D {score_b.draws} L {score_b.losses})"
        )
        return 0
    finally:
        engine_a.quit()
        engine_b.quit()


if __name__ == "__main__":
    raise SystemExit(main())
