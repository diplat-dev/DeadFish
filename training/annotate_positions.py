from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
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
NODES_RE = re.compile(r"\bnodes (\d+)")
PV_RE = re.compile(r"\bpv (.+)$")
BESTMOVE_RE = re.compile(r"^bestmove (\S+)")
OPTION_RE = re.compile(r"^option name (.+?) type\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SearchAnnotation:
    score_kind: str
    score_value: int
    depth: int
    nodes: int
    best_move: str
    pv: str


def parse_option_name(line: str) -> str | None:
    match = OPTION_RE.match(line.strip())
    if not match:
        return None
    return match.group(1).strip()


def normalize_option_name(name: str) -> str:
    return name.strip().casefold()


def parse_annotation(lines: list[str]) -> SearchAnnotation:
    score_kind = "cp"
    score_value = 0
    depth = 0
    nodes = 0
    pv = ""
    best_move = ""
    for line in lines:
        depth_match = DEPTH_RE.search(line)
        if depth_match:
            depth = int(depth_match.group(1))
        nodes_match = NODES_RE.search(line)
        if nodes_match:
            nodes = int(nodes_match.group(1))
        score_match = SCORE_RE.search(line)
        if score_match:
            score_kind, value_text = score_match.groups()
            score_value = int(value_text)
        pv_match = PV_RE.search(line)
        if pv_match:
            pv = pv_match.group(1).strip()
        bestmove_match = BESTMOVE_RE.match(line)
        if bestmove_match:
            best_move = bestmove_match.group(1)
    if not best_move:
        raise RuntimeError(f"Missing bestmove while annotating position. Output was: {lines}")
    return SearchAnnotation(score_kind=score_kind, score_value=score_value, depth=depth, nodes=nodes, best_move=best_move, pv=pv)


def resolve_workers(requested: int) -> int:
    if requested > 0:
        return requested
    cpu_count = os.cpu_count() or 1
    return max(1, cpu_count // 2)


def parse_option_assignment(text: str) -> tuple[str, str]:
    if "=" not in text:
        raise ValueError(f"Engine option must use NAME=VALUE format: {text}")
    name, value = text.split("=", 1)
    name = name.strip()
    if not name:
        raise ValueError(f"Engine option must include a name: {text}")
    return name, value.strip()


def configure_engine(engine: UciEngine, hash_mb: int, extra_options: list[str]) -> None:
    engine.send("uci")
    lines = engine.read_until(lambda line, _: line == "uciok")
    supported_options = {
        normalize_option_name(name): name
        for line in lines
        if (name := parse_option_name(line)) is not None
    }

    requested: dict[str, tuple[str, str]] = {}

    def set_if_supported(name: str, value: str) -> None:
        normalized = normalize_option_name(name)
        advertised_name = supported_options.get(normalized)
        if advertised_name is None:
            return
        requested[normalized] = (advertised_name, value)

    set_if_supported("Hash", str(hash_mb))
    if normalize_option_name("Threads") not in requested:
        set_if_supported("Threads", "1")
    set_if_supported("OwnBook", "false")
    set_if_supported("UCI_AnalyseMode", "true")

    for raw_option in extra_options:
        name, value = parse_option_assignment(raw_option)
        normalized = normalize_option_name(name)
        advertised_name = supported_options.get(normalized)
        if advertised_name is None:
            print(f"Skipping unsupported engine option: {name}")
            continue
        requested[normalized] = (advertised_name, value)

    for advertised_name, value in requested.values():
        engine.send(f"setoption name {advertised_name} value {value}")
    engine.send("isready")
    engine.read_until(lambda line, _: line == "readyok")


def build_go_request(depth: int | None, movetime: int | None, nodes: int | None) -> tuple[str, float]:
    if movetime is not None and movetime > 0:
        return f"go movetime {movetime}", max(5.0, movetime / 1000.0 + 2.0)
    if nodes is not None and nodes > 0:
        return f"go nodes {nodes}", max(10.0, nodes / 75000.0 + 5.0)
    effective_depth = depth if depth is not None and depth > 0 else 6
    return f"go depth {effective_depth}", max(5.0, effective_depth * 1.5)


def annotate_position(
    engine: UciEngine,
    fen: str,
    depth: int | None,
    movetime: int | None,
    nodes: int | None,
) -> SearchAnnotation:
    command, timeout = build_go_request(depth, movetime, nodes)
    engine.read_available()
    engine.send(f"position fen {fen}")
    engine.send(command)
    lines = engine.read_until(lambda text, _: text.startswith("bestmove "), timeout=timeout)
    return parse_annotation(lines)


def annotate_record(
    engine: UciEngine,
    record: dict[str, object],
    depth: int | None,
    movetime: int | None,
    nodes: int | None,
) -> dict[str, object]:
    fen = str(record["fen"])
    annotation = annotate_position(engine, fen, depth, movetime, nodes)
    annotated = dict(record)
    annotated["score_kind"] = annotation.score_kind
    annotated["score_value"] = annotation.score_value
    annotated["score_cp"] = annotation.score_value if annotation.score_kind == "cp" else None
    annotated["annotated_depth"] = annotation.depth
    annotated["annotated_nodes"] = annotation.nodes if annotation.nodes > 0 else (nodes if nodes is not None and nodes > 0 else None)
    annotated["best_move"] = annotation.best_move
    annotated["pv"] = annotation.pv
    return annotated


def annotate_chunk(
    records: list[tuple[int, dict[str, object]]],
    engine_path: str,
    hash_mb: int,
    depth: int | None,
    movetime: int | None,
    nodes: int | None,
    extra_options: list[str],
) -> list[tuple[int, dict[str, object]]]:
    engine = UciEngine(Path(engine_path))
    try:
        configure_engine(engine, hash_mb, extra_options)
        return [(index, annotate_record(engine, record, depth, movetime, nodes)) for index, record in records]
    finally:
        engine.quit()


def progress_interval(total: int) -> int:
    return max(1, min(100, total // 20 or 1))


def main() -> int:
    parser = argparse.ArgumentParser(description="Annotate JSONL training positions with scores from a UCI engine.")
    parser.add_argument("--engine", type=Path, default=default_engine_path(), help="Path to the teacher UCI engine executable.")
    parser.add_argument("--input", type=Path, required=True, help="Input JSONL positions.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "output" / "positions_annotated.jsonl",
        help="Output JSONL path.",
    )
    budget_group = parser.add_mutually_exclusive_group()
    budget_group.add_argument("--depth", type=int, default=None, help="Search depth.")
    budget_group.add_argument("--movetime", type=int, default=None, help="Fixed movetime instead of depth.")
    budget_group.add_argument("--nodes", type=int, default=None, help="Fixed node budget instead of depth.")
    parser.add_argument("--hash", type=int, default=64, help="Hash size in MB.")
    parser.add_argument("--limit", type=int, default=0, help="Optional maximum number of positions to annotate.")
    parser.add_argument(
        "--option",
        action="append",
        default=[],
        help="Extra UCI option in NAME=VALUE format. Useful for generic teachers such as Stockfish.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel engine workers. Use 0 for auto based on CPU count.",
    )
    args = parser.parse_args()
    if args.depth is None and args.movetime is None and args.nodes is None:
        args.depth = 6
    if args.depth is not None and args.depth <= 0:
        parser.error("--depth must be positive.")
    if args.movetime is not None and args.movetime <= 0:
        parser.error("--movetime must be positive.")
    if args.nodes is not None and args.nodes <= 0:
        parser.error("--nodes must be positive.")

    engine_path = args.engine.resolve()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if not engine_path.exists():
        raise FileNotFoundError(f"Engine executable not found: {engine_path}")
    if not input_path.exists():
        raise FileNotFoundError(f"Input JSONL not found: {input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    indexed_records: list[tuple[int, dict[str, object]]] = []
    with input_path.open("r", encoding="utf-8") as in_handle:
        for raw_line in in_handle:
            line = raw_line.strip()
            if not line:
                continue
            indexed_records.append((len(indexed_records), json.loads(line)))
            if args.limit > 0 and len(indexed_records) >= args.limit:
                break

    worker_count = min(resolve_workers(args.workers), max(1, len(indexed_records)))
    print(f"Annotating {len(indexed_records)} positions with {worker_count} worker(s).")

    if worker_count == 1:
        engine = UciEngine(engine_path)
        try:
            configure_engine(engine, args.hash, args.option)
            annotated_results = []
            interval = progress_interval(len(indexed_records))
            for completed, (index, record) in enumerate(indexed_records, start=1):
                annotated_results.append(
                    (
                        index,
                        annotate_record(
                            engine,
                            record,
                            args.depth,
                            args.movetime,
                            args.nodes,
                        ),
                    )
                )
                if completed % interval == 0 or completed == len(indexed_records):
                    print(f"Annotated {completed}/{len(indexed_records)} positions...")
        finally:
            engine.quit()
    else:
        chunks: list[list[tuple[int, dict[str, object]]]] = [[] for _ in range(worker_count)]
        for position, item in enumerate(indexed_records):
            chunks[position % worker_count].append(item)

        annotated_results: list[tuple[int, dict[str, object]]] = []
        completed = 0
        interval = progress_interval(len(indexed_records))
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    annotate_chunk,
                    chunk,
                    str(engine_path),
                    args.hash,
                    args.depth,
                    args.movetime,
                    args.nodes,
                    args.option,
                )
                for chunk in chunks
                if chunk
            ]
            for future in concurrent.futures.as_completed(futures):
                chunk_results = future.result()
                annotated_results.extend(chunk_results)
                completed += len(chunk_results)
                if completed % interval == 0 or completed == len(indexed_records):
                    print(f"Annotated {completed}/{len(indexed_records)} positions...")

    annotated_results.sort(key=lambda item: item[0])

    cp_count = 0
    mate_count = 0
    with output_path.open("w", encoding="utf-8") as out_handle:
        for _, record in annotated_results:
            if record.get("score_kind") == "mate":
                mate_count += 1
            else:
                cp_count += 1
            out_handle.write(json.dumps(record) + "\n")

    print(f"Annotated {len(indexed_records)} positions to {output_path}")
    print(f"Annotation summary: cp={cp_count} mate={mate_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
