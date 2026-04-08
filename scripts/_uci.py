from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_engine_path() -> Path:
    return repo_root() / "build" / "deadfish.exe"


def preferred_engine_path() -> Path:
    native = repo_root() / "build" / "deadfish_native.exe"
    if native.exists():
        return native
    return default_engine_path()


def run_cli(engine_path: Path, args: list[str]) -> str:
    result = subprocess.run(
        [str(engine_path), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Command failed: {' '.join(args)}")
    return result.stdout.strip()


def build_position_args(fen: str, moves: list[str] | None = None) -> list[str]:
    args = ["--fen", fen]
    if moves:
        args.extend(["--moves", ",".join(moves)])
    return args


def legal_moves(engine_path: Path, fen: str, moves: list[str] | None = None) -> set[str]:
    output = run_cli(engine_path, ["legal", *build_position_args(fen, moves)])
    return {move for move in output.split() if move}


def apply_move(engine_path: Path, fen: str, move: str) -> str:
    return run_cli(engine_path, ["fen", "--fen", fen, "--moves", move])


def status(engine_path: Path, fen: str) -> dict:
    output = run_cli(engine_path, ["status", "--fen", fen, "--json"])
    return json.loads(output)


def evaluate(
    engine_path: Path,
    fen: str,
    *,
    moves: list[str] | None = None,
    use_nnue: bool | None = None,
    eval_file: Path | str | None = None,
) -> dict:
    args = ["eval", "--fen", fen, "--json"]
    if moves:
        args.extend(["--moves", ",".join(moves)])
    if use_nnue is not None:
        args.extend(["--use-nnue", "true" if use_nnue else "false"])
    if eval_file is not None:
        args.extend(["--eval-file", str(eval_file)])
    output = run_cli(engine_path, args)
    return json.loads(output)


class UciEngine:
    def __init__(self, engine_path: Path) -> None:
        self.engine_path = engine_path
        self.process = subprocess.Popen(
            [str(engine_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("Failed to start engine process with pipes.")
        self._stdin = self.process.stdin
        self._stdout = self.process.stdout
        self._queue: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

    def _reader_loop(self) -> None:
        assert self._stdout is not None
        for raw_line in self._stdout:
            self._queue.put(raw_line.rstrip("\r\n"))

    def send(self, line: str) -> None:
        self._stdin.write(line + "\n")
        self._stdin.flush()

    def read_until(self, predicate: Callable[[str, list[str]], bool], timeout: float = 5.0) -> list[str]:
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                line = self._queue.get(timeout=remaining)
            except queue.Empty:
                continue
            lines.append(line)
            if predicate(line, lines):
                return lines
        raise TimeoutError(f"Timed out waiting for engine output. Lines seen: {lines}")

    def read_available(self) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._queue.get_nowait())
            except queue.Empty:
                return lines

    def quit(self) -> None:
        try:
            self.send("quit")
        except OSError:
            pass
        try:
            self.process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2.0)
