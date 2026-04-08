from __future__ import annotations

import site
import sys
import threading
import time
from pathlib import Path

base = Path(__file__).resolve().parents[1]
for candidate_path in (
    base / ".gui_pydeps",
    base / ".tmp_pydeps",
):
    if candidate_path.exists():
        candidate_text = str(candidate_path)
        if candidate_text not in sys.path:
            sys.path.insert(0, candidate_text)

vendor_dir = base / "vendor"
if vendor_dir.exists():
    for candidate_path in sorted(vendor_dir.glob("chess-*"), reverse=True):
        candidate_text = str(candidate_path)
        if candidate_text not in sys.path:
            sys.path.insert(0, candidate_text)

candidate = site.getusersitepackages()
if candidate and candidate not in sys.path and Path(candidate).exists():
    sys.path.append(candidate)

import chess


class FakeUciEngine:
    def __init__(self) -> None:
        self.board = chess.Board()
        self.search_thread: threading.Thread | None = None
        self.stop_event = threading.Event()

    def send(self, line: str) -> None:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def handle_position(self, tokens: list[str]) -> None:
        if len(tokens) < 2:
            return
        index = 1
        if tokens[index] == "startpos":
            self.board = chess.Board()
            index += 1
        elif tokens[index] == "fen":
            index += 1
            fen_tokens: list[str] = []
            while index < len(tokens) and tokens[index] != "moves":
                fen_tokens.append(tokens[index])
                index += 1
            self.board = chess.Board(" ".join(fen_tokens))
        if index < len(tokens) and tokens[index] == "moves":
            index += 1
            for move_text in tokens[index:]:
                move = chess.Move.from_uci(move_text)
                if move in self.board.legal_moves:
                    self.board.push(move)

    def current_bestmove(self) -> str:
        legal_moves = list(self.board.legal_moves)
        if not legal_moves:
            return "0000"
        return legal_moves[0].uci()

    def launch_search(self, infinite: bool) -> None:
        self.stop_event.clear()
        bestmove = self.current_bestmove()

        def worker() -> None:
            for depth in range(1, 4):
                if self.stop_event.is_set():
                    break
                self.send(
                    f"info depth {depth} score cp {depth * 14} nodes {depth * 1200} "
                    f"nps {depth * 55000} time {depth * 10} pv {bestmove}"
                )
                time.sleep(0.05)
                if not infinite:
                    break
            self.send(f"bestmove {bestmove}")

        if infinite:
            self.search_thread = threading.Thread(target=worker, daemon=True)
            self.search_thread.start()
            return
        worker()

    def stop_search(self) -> None:
        if self.search_thread is None:
            return
        self.stop_event.set()
        self.search_thread.join(timeout=1.0)
        self.search_thread = None

    def run(self) -> int:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            tokens = line.split()
            command = tokens[0]
            if command == "uci":
                self.send("id name Fake UCI")
                self.send("id author DeadFish tests")
                self.send("option name Hash type spin default 32 min 1 max 1024")
                self.send("option name Clear Hash type button")
                self.send("option name UseNNUE type check default false")
                self.send("option name EvalFile type string default <empty>")
                self.send("option name Style type combo default Normal var Normal var Aggressive")
                self.send("uciok")
            elif command == "isready":
                self.send("readyok")
            elif command == "position":
                self.handle_position(tokens)
            elif command == "ucinewgame":
                self.stop_search()
                self.board = chess.Board()
            elif command == "setoption":
                if "name" in tokens:
                    name_index = tokens.index("name") + 1
                    value_index = tokens.index("value") if "value" in tokens else len(tokens)
                    name = " ".join(tokens[name_index:value_index])
                    if name == "EvalFile":
                        self.send("info string Fake engine accepted EvalFile.")
                    elif name == "Clear Hash":
                        self.send("info string Fake hash cleared.")
            elif command == "go":
                self.launch_search("infinite" in tokens)
            elif command == "stop":
                self.stop_search()
            elif command == "quit":
                self.stop_search()
                return 0
        return 0


if __name__ == "__main__":
    raise SystemExit(FakeUciEngine().run())
