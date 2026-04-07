from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from _uci import default_engine_path


SCORE_RE = re.compile(r"Score of (.+?) vs (.+?):\s+([0-9.]+)\s+-\s+([0-9.]+)\s+-\s+([0-9.]+)")


def add_engine_args(args: list[str], name: str, cmd: Path, options: list[str]) -> None:
    args.extend(["-engine", f"name={name}", f"cmd={cmd}"])
    for option in options:
        if "=" not in option:
            raise ValueError(f"Engine option must use NAME=VALUE format: {option}")
        opt_name, opt_value = option.split("=", 1)
        args.append(f"option.{opt_name}={opt_value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a cutechess-cli UCI match between two engines.")
    parser.add_argument("--cutechess", default="cutechess-cli", help="cutechess-cli executable.")
    parser.add_argument("--engine-a", type=Path, default=default_engine_path(), help="Path to engine A.")
    parser.add_argument("--engine-b", type=Path, required=True, help="Path to engine B.")
    parser.add_argument("--name-a", default="DeadFish-A", help="Engine A display name.")
    parser.add_argument("--name-b", default="Engine-B", help="Engine B display name.")
    parser.add_argument("--option-a", action="append", default=[], help="Engine A UCI option in NAME=VALUE format.")
    parser.add_argument("--option-b", action="append", default=[], help="Engine B UCI option in NAME=VALUE format.")
    parser.add_argument("--games", type=int, default=20, help="Number of games.")
    parser.add_argument("--concurrency", type=int, default=1, help="Cutechess concurrency.")
    parser.add_argument("--tc", default="8+0.08", help="Time control passed to cutechess -each tc=...")
    parser.add_argument("--opening-file", type=Path, help="Optional PGN/EPD opening file.")
    parser.add_argument("--opening-format", default="pgn", help="Opening file format.")
    parser.add_argument("--opening-order", default="random", help="Opening order.")
    parser.add_argument("--opening-plies", type=int, default=8, help="Book plies.")
    parser.add_argument("--sprt", action="store_true", help="Enable cutechess SPRT mode.")
    parser.add_argument("--elo0", type=float, default=0.0, help="SPRT elo0.")
    parser.add_argument("--elo1", type=float, default=5.0, help="SPRT elo1.")
    parser.add_argument("--alpha", type=float, default=0.05, help="SPRT alpha.")
    parser.add_argument("--beta", type=float, default=0.05, help="SPRT beta.")
    args = parser.parse_args()

    engine_a = args.engine_a.resolve()
    engine_b = args.engine_b.resolve()
    for path in (engine_a, engine_b):
        if not path.exists():
            raise FileNotFoundError(f"Engine executable not found: {path}")

    command = [args.cutechess]
    add_engine_args(command, args.name_a, engine_a, args.option_a)
    add_engine_args(command, args.name_b, engine_b, args.option_b)
    command.extend(["-each", "proto=uci", f"tc={args.tc}"])
    command.extend(["-games", str(args.games), "-repeat", "-recover", "-concurrency", str(args.concurrency)])

    if args.opening_file:
        opening_file = args.opening_file.resolve()
        if not opening_file.exists():
            raise FileNotFoundError(f"Opening file not found: {opening_file}")
        command.extend(
            [
                "-openings",
                f"file={opening_file}",
                f"format={args.opening_format}",
                f"order={args.opening_order}",
                f"plies={args.opening_plies}",
            ]
        )

    if args.sprt:
        command.extend(
            [
                "-sprt",
                f"elo0={args.elo0}",
                f"elo1={args.elo1}",
                f"alpha={args.alpha}",
                f"beta={args.beta}",
            ]
        )

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert process.stdout is not None
    score_line = ""
    for line in process.stdout:
        print(line, end="")
        if "Score of " in line:
            score_line = line.strip()
    return_code = process.wait()
    if return_code != 0:
        return return_code

    if score_line:
        match = SCORE_RE.search(score_line)
        if match:
            print(
                f"\nParsed score: {match.group(1)} {match.group(3)} - "
                f"{match.group(4)} - {match.group(5)} {match.group(2)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
