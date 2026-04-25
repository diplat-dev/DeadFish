from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

from _uci import default_cutechess_path, default_engine_path, default_match_dir, safe_slug, timestamp_slug


def anchored_rating_estimate(results: list[dict]) -> dict | None:
    anchors = []
    for result in results:
        rating = result.get("opponent_rating")
        if rating is None or result.get("total", 0) <= 0:
            continue
        score = float(result["wins"]) + 0.5 * float(result["draws"])
        anchors.append((float(rating), float(result["total"]), score))
    if not anchors:
        return None

    def expected_score(engine_rating: float, opponent_rating: float) -> float:
        exponent = (opponent_rating - engine_rating) / 400.0
        if exponent > 50.0:
            return 0.0
        if exponent < -50.0:
            return 1.0
        return 1.0 / (1.0 + 10.0 ** exponent)

    def residual(engine_rating: float) -> float:
        return sum(score - games * expected_score(engine_rating, rating) for rating, games, score in anchors)

    low = min(rating for rating, _, _ in anchors) - 1000.0
    high = max(rating for rating, _, _ in anchors) + 1000.0
    bounded = None
    if residual(low) <= 0.0:
        rating = low
        bounded = "below"
    elif residual(high) >= 0.0:
        rating = high
        bounded = "above"
    else:
        for _ in range(80):
            mid = (low + high) / 2.0
            if residual(mid) > 0.0:
                low = mid
            else:
                high = mid
        rating = (low + high) / 2.0
    if bounded is None:
        scale = math.log(10.0) / 400.0
        info = sum(
            games * scale * scale * expected_score(rating, opponent_rating) * (1.0 - expected_score(rating, opponent_rating))
            for opponent_rating, games, _ in anchors
        )
        standard_error = 1.0 / math.sqrt(info) if info > 0.0 else None
    else:
        standard_error = None
    return {
        "rating": rating,
        "standard_error": standard_error,
        "ci95_low": rating - 1.96 * standard_error if standard_error is not None else None,
        "ci95_high": rating + 1.96 * standard_error if standard_error is not None else None,
        "anchors": len(anchors),
        "games": sum(games for _, games, _ in anchors),
        "bounded": bounded,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DeadFish through a cutechess-based external gauntlet.")
    parser.add_argument("--engine", type=Path, default=default_engine_path(), help="Path to the engine under test.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "external_gauntlet.example.json",
        help="JSON config describing cutechess settings and opponents.",
    )
    parser.add_argument("--games", type=int, help="Override games per opponent from the config.")
    parser.add_argument("--concurrency", type=int, help="Override cutechess concurrency from the config.")
    parser.add_argument("--tc", help="Override time control from the config.")
    parser.add_argument("--artifact-dir", type=Path, help="Override artifact directory from the config.")
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    config_path = args.config.resolve()
    if not engine_path.exists():
        raise FileNotFoundError(f"Engine executable not found: {engine_path}")
    if not config_path.exists():
        raise FileNotFoundError(f"Gauntlet config not found: {config_path}")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    configured_cutechess = str(config.get("cutechess", "")).strip()
    cutechess = default_cutechess_path() if configured_cutechess in {"", "cutechess-cli"} else configured_cutechess
    games = args.games if args.games is not None else int(config.get("games", 20))
    concurrency = args.concurrency if args.concurrency is not None else int(config.get("concurrency", 1))
    tc = args.tc if args.tc is not None else str(config.get("tc", "8+0.08"))
    engine_options = list(config.get("engine_options", []))
    artifact_dir = (args.artifact_dir or Path(config.get("artifact_dir", default_match_dir()))).expanduser()
    if not artifact_dir.is_absolute():
        artifact_dir = (Path(__file__).resolve().parents[1] / artifact_dir).resolve()
    run_slug = f"{timestamp_slug()}_{safe_slug(config.get('name', config_path.stem))}"
    run_dir = artifact_dir / run_slug
    run_dir.mkdir(parents=True, exist_ok=True)
    opening_file = str(config.get("opening_file", "")).strip()
    opening_format = str(config.get("opening_format", "pgn"))
    opening_order = str(config.get("opening_order", "random"))
    opening_plies = int(config.get("opening_plies", 8))

    results: list[dict] = []
    for opponent in config.get("opponents", []):
        opponent_name = opponent["name"]
        opponent_cmd = Path(opponent["cmd"]).expanduser()
        if not opponent_cmd.is_absolute():
            opponent_cmd = (config_path.parent / opponent_cmd).resolve()
        if not opponent_cmd.exists():
            raise FileNotFoundError(f"Opponent executable not found: {opponent_cmd}")

        result_path = run_dir / f"{safe_slug(opponent_name)}.json"
        command = [
            sys.executable,
            str(Path(__file__).resolve().parent / "cutechess_match.py"),
            "--cutechess",
            cutechess,
            "--engine-a",
            str(engine_path),
            "--engine-b",
            str(opponent_cmd),
            "--name-a",
            "DeadFish",
            "--name-b",
            opponent_name,
            "--games",
            str(games),
            "--concurrency",
            str(concurrency),
            "--tc",
            tc,
            "--artifact-dir",
            str(run_dir),
            "--result-json",
            str(result_path),
        ]
        for option in engine_options:
            command.extend(["--option-a", option])
        for option in opponent.get("options", []):
            command.extend(["--option-b", option])
        if opening_file:
            opening_path = Path(opening_file).expanduser()
            if not opening_path.is_absolute():
                opening_path = (config_path.parent / opening_path).resolve()
            command.extend(
                [
                    "--opening-file",
                    str(opening_path),
                    "--opening-format",
                    opening_format,
                    "--opening-order",
                    opening_order,
                    "--opening-plies",
                    str(opening_plies),
                ]
            )

        print(f"\n=== Versus {opponent_name} ===", flush=True)
        subprocess.run(command, check=True)
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if "rating" in opponent:
                result["opponent_rating"] = float(opponent["rating"])
            results.append(result)

    estimate = anchored_rating_estimate(results)
    summary = {
        "config": str(config_path),
        "engine": str(engine_path),
        "run_dir": str(run_dir),
        "results": results,
        "rating_estimate": estimate,
    }
    summary_path = run_dir / "gauntlet_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if estimate:
        if estimate.get("bounded") == "below":
            print(f"\nAnchored rating estimate: <= {estimate['rating']:.0f} ({int(estimate['games'])} games)")
        elif estimate.get("bounded") == "above":
            print(f"\nAnchored rating estimate: >= {estimate['rating']:.0f} ({int(estimate['games'])} games)")
        else:
            print(
                "\nAnchored rating estimate: "
                f"{estimate['rating']:.0f} +/- {1.96 * estimate['standard_error']:.0f} "
                f"(95% CI {estimate['ci95_low']:.0f}..{estimate['ci95_high']:.0f}, "
                f"{int(estimate['games'])} games)"
            )
    print(f"Summary: {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
