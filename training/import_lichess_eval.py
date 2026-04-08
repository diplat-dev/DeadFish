from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import zstandard
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "zstandard is required to import the Lichess evaluation dump. "
        "Install training/requirements.txt first."
    ) from exc


DEFAULT_URL = "https://database.lichess.org/lichess_db_eval.jsonl.zst"


@dataclass(slots=True)
class ImportStats:
    source_url: str
    download_bytes: int = 0
    max_download_bytes: int = 0
    raw_positions: int = 0
    written_positions: int = 0
    train_positions: int = 0
    validation_positions: int = 0
    cp_positions: int = 0
    mate_positions: int = 0
    skipped_missing_evals: int = 0
    skipped_missing_pv: int = 0
    skipped_min_depth: int = 0
    skipped_min_knodes: int = 0
    truncated_download: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class LimitedResponse:
    def __init__(self, response, limit: int, stats: ImportStats) -> None:
        self._response = response
        self._remaining = max(0, limit)
        self._stats = stats

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            self._stats.truncated_download = True
            return b""
        if size is None or size < 0 or size > self._remaining:
            size = self._remaining
        chunk = self._response.read(size)
        self._remaining -= len(chunk)
        self._stats.download_bytes += len(chunk)
        if not chunk and self._remaining > 0:
            self._remaining = 0
        if self._remaining <= 0:
            self._stats.truncated_download = True
        return chunk

    def close(self) -> None:
        self._response.close()


class PrefixedReader:
    def __init__(self, prefix: bytes, reader: LimitedResponse) -> None:
        self._prefix = prefix
        self._reader = reader

    def read(self, size: int = -1) -> bytes:
        if self._prefix:
            if size is None or size < 0 or size >= len(self._prefix):
                chunk = self._prefix
                self._prefix = b""
                if size is None or size < 0:
                    return chunk + self._reader.read(size)
                return chunk + self._reader.read(size - len(chunk))
            chunk = self._prefix[:size]
            self._prefix = self._prefix[size:]
            return chunk
        return self._reader.read(size)

    def close(self) -> None:
        self._reader.close()


def prepare_zstd_stream(response, limit: int, stats: ImportStats):
    limited = LimitedResponse(response, limit, stats)
    prefix = limited.read(8)
    if len(prefix) < 8:
        return PrefixedReader(prefix, limited)
    magic = int.from_bytes(prefix[:4], "little")
    if 0x184D2A50 <= magic <= 0x184D2A5F:
        skippable_size = int.from_bytes(prefix[4:8], "little")
        skipped = limited.read(skippable_size)
        if len(skipped) != skippable_size:
            return PrefixedReader(b"", limited)
        return PrefixedReader(b"", limited)
    return PrefixedReader(prefix, limited)


def normalize_fen(fen: str) -> str:
    parts = fen.strip().split()
    if len(parts) == 4:
        return f"{fen} 0 1"
    return fen


def select_best_eval(raw: dict[str, object]) -> dict[str, object] | None:
    evals = raw.get("evals")
    if not isinstance(evals, list) or not evals:
        return None
    best = None
    best_key = (-1, -1)
    for candidate in evals:
        if not isinstance(candidate, dict):
            continue
        depth = int(candidate.get("depth", 0) or 0)
        knodes = int(candidate.get("knodes", 0) or 0)
        key = (depth, knodes)
        if key > best_key:
            best_key = key
            best = candidate
    return best


def extract_record(raw: dict[str, object], *, min_depth: int, min_knodes: int, stats: ImportStats) -> dict[str, object] | None:
    fen = raw.get("fen")
    if not isinstance(fen, str) or not fen:
        stats.skipped_missing_evals += 1
        return None

    best_eval = select_best_eval(raw)
    if best_eval is None:
        stats.skipped_missing_evals += 1
        return None

    depth = int(best_eval.get("depth", 0) or 0)
    knodes = int(best_eval.get("knodes", 0) or 0)
    if depth < min_depth:
        stats.skipped_min_depth += 1
        return None
    if knodes < min_knodes:
        stats.skipped_min_knodes += 1
        return None

    pvs = best_eval.get("pvs")
    if not isinstance(pvs, list) or not pvs or not isinstance(pvs[0], dict):
        stats.skipped_missing_pv += 1
        return None
    pv0 = pvs[0]
    line = str(pv0.get("line", "") or "").strip()
    best_move = line.split()[0] if line else ""

    record = {
        "fen": normalize_fen(fen),
        "score_kind": "cp" if pv0.get("cp") is not None else "mate",
        "score_value": int(pv0.get("cp") if pv0.get("cp") is not None else pv0.get("mate")),
        "score_cp": int(pv0["cp"]) if pv0.get("cp") is not None else None,
        "annotated_depth": depth,
        "annotated_nodes": knodes * 1000,
        "best_move": best_move,
        "pv": line,
        "source": "lichess_db_eval",
    }
    if record["score_kind"] == "cp":
        stats.cp_positions += 1
    else:
        stats.mate_positions += 1
    return record


def choose_validation(record: dict[str, object], validation_ratio: float) -> bool:
    digest = hashlib.blake2b(str(record["fen"]).encode("utf-8"), digest_size=8).digest()
    bucket = int.from_bytes(digest, "little") / 2**64
    return bucket < validation_ratio


def stream_import(args) -> ImportStats:
    stats = ImportStats(source_url=args.url, max_download_bytes=args.max_download_bytes)
    train_path = args.output_train.resolve()
    validation_path = args.output_validation.resolve()
    stats_path = args.stats_output.resolve()
    train_path.parent.mkdir(parents=True, exist_ok=True)
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.parent.mkdir(parents=True, exist_ok=True)

    with urllib.request.urlopen(args.url, timeout=60) as response, train_path.open("w", encoding="utf-8") as train_handle, validation_path.open("w", encoding="utf-8") as validation_handle:
        limited = prepare_zstd_stream(response, args.max_download_bytes, stats)
        dctx = zstandard.ZstdDecompressor()
        try:
            reader = dctx.stream_reader(limited, read_across_frames=True)
            text = io.TextIOWrapper(reader, encoding="utf-8")
            for raw_line in text:
                line = raw_line.strip()
                if not line:
                    continue
                stats.raw_positions += 1
                raw = json.loads(line)
                record = extract_record(raw, min_depth=args.min_depth, min_knodes=args.min_knodes, stats=stats)
                if record is None:
                    continue
                destination = validation_handle if choose_validation(record, args.validation_ratio) else train_handle
                destination.write(json.dumps(record) + "\n")
                stats.written_positions += 1
                if destination is validation_handle:
                    stats.validation_positions += 1
                else:
                    stats.train_positions += 1
                if args.progress_every > 0 and stats.written_positions % args.progress_every == 0:
                    print(
                        f"Imported {stats.written_positions} positions "
                        f"(train={stats.train_positions} validation={stats.validation_positions} bytes={stats.download_bytes})"
                    )
                if args.max_positions > 0 and stats.written_positions >= args.max_positions:
                    limited.close()
                    stats.truncated_download = True
                    break
        except zstandard.ZstdError:
            stats.truncated_download = True

    stats_path.write_text(json.dumps(stats.to_dict(), indent=2), encoding="utf-8")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a capped slice of the official Lichess eval dump into DeadFish JSONL files.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Source URL for the Lichess evaluation dump.")
    parser.add_argument(
        "--output-train",
        type=Path,
        default=Path(__file__).resolve().parent / "output" / "lichess_eval_train.jsonl",
        help="Output JSONL for the training split.",
    )
    parser.add_argument(
        "--output-validation",
        type=Path,
        default=Path(__file__).resolve().parent / "output" / "lichess_eval_validation.jsonl",
        help="Output JSONL for the validation split.",
    )
    parser.add_argument(
        "--stats-output",
        type=Path,
        default=Path(__file__).resolve().parent / "output" / "lichess_eval_import_stats.json",
        help="Output JSON summary for the import.",
    )
    parser.add_argument("--max-download-bytes", type=int, default=1_500_000_000, help="Hard download cap. Keep this under your storage/network budget.")
    parser.add_argument("--max-positions", type=int, default=5_000_000, help="Maximum number of formatted positions to write across both splits.")
    parser.add_argument("--validation-ratio", type=float, default=0.05, help="Deterministic validation split ratio.")
    parser.add_argument("--min-depth", type=int, default=0, help="Skip evaluations below this depth.")
    parser.add_argument("--min-knodes", type=int, default=0, help="Skip evaluations below this knodes count.")
    parser.add_argument("--progress-every", type=int, default=100_000, help="Print progress every N written positions.")
    args = parser.parse_args()

    if args.max_download_bytes <= 0:
        raise ValueError("--max-download-bytes must be positive.")
    if args.max_download_bytes > 8_000_000_000:
        raise ValueError("--max-download-bytes must stay under 8 GB.")
    if args.max_positions <= 0:
        raise ValueError("--max-positions must be positive.")
    if not (0.0 < args.validation_ratio < 1.0):
        raise ValueError("--validation-ratio must be between 0 and 1.")

    stats = stream_import(args)
    print(json.dumps(stats.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
