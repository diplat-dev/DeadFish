from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from pathlib import Path

from _uci import default_cutechess_path, default_engine_path, default_match_dir, safe_slug, timestamp_slug


SCORE_RE = re.compile(r"Score of (.+?) vs (.+?):\s+([0-9.]+)\s+-\s+([0-9.]+)\s+-\s+([0-9.]+)")


def add_engine_args(args: list[str], name: str, cmd: Path, options: list[str]) -> None:
    args.extend(["-engine", f"name={name}", f"cmd={cmd}"])
    for option in options:
        if "=" not in option:
            raise ValueError(f"Engine option must use NAME=VALUE format: {option}")
        opt_name, opt_value = option.split("=", 1)
        args.append(f"option.{opt_name}={opt_value}")


def elo_from_score(score_fraction: float) -> float:
    score_fraction = min(0.999, max(0.001, score_fraction))
    return 400.0 * math.log10(score_fraction / (1.0 - score_fraction))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a cutechess-cli UCI match between two engines.")
    parser.add_argument("--cutechess", default=default_cutechess_path(), help="cutechess-cli executable.")
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
    parser.add_argument("--artifact-dir", type=Path, default=default_match_dir(), help="Directory for PGN/log/result artifacts.")
    parser.add_argument("--no-artifacts", action="store_true", help="Do not write PGN, stdout log, or JSON result artifacts.")
    parser.add_argument("--pgnout", type=Path, help="Optional explicit PGN output path.")
    parser.add_argument("--log-file", type=Path, help="Optional explicit stdout log path.")
    parser.add_argument("--result-json", type=Path, help="Optional explicit JSON summary path.")
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

    artifact_dir = args.artifact_dir.resolve()
    pgnout = args.pgnout
    log_file = args.log_file
    result_json = args.result_json
    if not args.no_artifacts:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        base_name = (
            f"{timestamp_slug()}_{safe_slug(args.name_a)}_vs_{safe_slug(args.name_b)}_"
            f"{safe_slug(str(args.tc))}_{args.games}g"
        )
        pgnout = pgnout or artifact_dir / f"{base_name}.pgn"
        log_file = log_file or artifact_dir / f"{base_name}.log"
        result_json = result_json or artifact_dir / f"{base_name}.json"
        command.extend(["-pgnout", str(pgnout)])

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert process.stdout is not None
    score_line = ""
    output_lines: list[str] = []
    for line in process.stdout:
        print(line, end="")
        output_lines.append(line)
        if "Score of " in line:
            score_line = line.strip()
    return_code = process.wait()
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("".join(output_lines), encoding="utf-8")
    if return_code != 0:
        return return_code

    summary = {
        "engine_a": args.name_a,
        "engine_b": args.name_b,
        "games": args.games,
        "tc": args.tc,
        "command": command,
        "pgn": str(pgnout) if pgnout is not None else None,
        "log": str(log_file) if log_file is not None else None,
    }
    if score_line:
        match = SCORE_RE.search(score_line)
        if match:
            wins = float(match.group(3))
            losses = float(match.group(4))
            draws = float(match.group(5))
            total = wins + draws + losses
            score_fraction = (wins + draws * 0.5) / total if total > 0 else 0.0
            elo_diff = elo_from_score(score_fraction) if total > 0 else 0.0
            summary.update(
                {
                    "score_line": score_line,
                    "wins": wins,
                    "draws": draws,
                    "losses": losses,
                    "total": total,
                    "score_fraction": score_fraction,
                    "elo_diff": elo_diff,
                }
            )
            print(
                f"\nParsed score (W-L-D): {match.group(1)} {match.group(3)} - "
                f"{match.group(4)} - {match.group(5)} {match.group(2)}"
            )
            print(f"Score: {score_fraction * 100:.1f}% | Elo diff estimate: {elo_diff:+.1f}")
    if result_json is not None:
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
