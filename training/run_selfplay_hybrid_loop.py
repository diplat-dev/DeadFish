from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRAINING_DIR = ROOT / "training"
RUNS_DIR = TRAINING_DIR / "runs"
STATE_DIR = TRAINING_DIR / "state"
ENGINE_PATH = ROOT / "build" / "deadfish_native.exe"
CHAMPION_CHECKPOINT = TRAINING_DIR / "checkpoints" / "deadfish_current.pt"
CHAMPION_NNUE = TRAINING_DIR / "output" / "deadfish_current.nnue"
CHAMPION_METADATA = TRAINING_DIR / "output" / "deadfish_current.nnue.json"
STATE_PATH = STATE_DIR / "hybrid_loop_state.json"
OPENING_SUITE = ROOT / "data" / "nnue_openings.pgn"
GATE_GAMES = 25
GATE_TC = "1+0.01"
GATE_CONCURRENCY = 2

FINISHED_GAME_RE = re.compile(r"^Finished game (\d+) of (\d+) ")
ANNOTATION_PROGRESS_RE = re.compile(r"^Annotated (\d+)/(\d+) positions\.\.\.$")
TRAIN_EPOCH_RE = re.compile(r"^epoch (\d+): train_loss=([0-9.]+) validation_loss=([0-9.]+)")
PARITY_CHECK_RE = re.compile(r"^(\d+)\s+(OK|FAIL)\b")
SPINNER_FRAMES = ("-", "\\", "|", "/")


@dataclass(slots=True)
class StepView:
    label: str
    status: str = "pending"
    progress: float | None = None
    detail: str = "Waiting."


def enable_virtual_terminal() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        if handle == 0:
            return False
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return False
        if kernel32.SetConsoleMode(handle, mode.value | 0x0004) == 0:
            return False
        return True
    except Exception:
        return False


def truncate_text(text: str, limit: int = 100) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def format_bar(progress: float, width: int = 32) -> str:
    clamped = max(0.0, min(1.0, progress))
    filled = min(width, max(0, int(round(clamped * width))))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


class LiveDashboard:
    def __init__(self, title: str, summary_lines: list[str], step_labels: list[str], logs_dir: Path) -> None:
        self.title = title
        self.summary_lines = summary_lines
        self.steps = [StepView(label=label) for label in step_labels]
        self.logs_dir = logs_dir
        self.enabled = sys.stdout.isatty()
        self.supports_ansi = enable_virtual_terminal() if self.enabled else False
        self.spinner_index = 0
        self.last_render = 0.0

    def start_step(self, index: int, detail: str, progress: float | None = None) -> None:
        step = self.steps[index]
        step.status = "running"
        step.detail = detail
        step.progress = progress
        self.render(force=True)

    def update_step(self, index: int, *, detail: str | None = None, progress: float | None = None) -> None:
        step = self.steps[index]
        if step.status != "running":
            step.status = "running"
        if detail is not None:
            step.detail = detail
        if progress is not None:
            step.progress = max(0.0, min(1.0, progress))
        self.render()

    def finish_step(self, index: int, status: str, detail: str | None = None) -> None:
        step = self.steps[index]
        step.status = status
        if detail is not None:
            step.detail = detail
        if status in {"done", "skipped"}:
            step.progress = 1.0 if status == "done" else step.progress
        self.render(force=True)

    def skip_step(self, index: int, detail: str) -> None:
        step = self.steps[index]
        step.status = "skipped"
        step.detail = detail
        step.progress = None
        self.render(force=True)

    def _overall_progress(self) -> float:
        if not self.steps:
            return 1.0
        complete = 0.0
        for step in self.steps:
            if step.status in {"done", "skipped"}:
                complete += 1.0
            elif step.status == "running" and step.progress is not None:
                complete += step.progress
        return complete / len(self.steps)

    def _current_step(self) -> StepView | None:
        for step in self.steps:
            if step.status == "running":
                return step
        return None

    def render(self, force: bool = False) -> None:
        if not self.enabled:
            return
        now = time.time()
        if not force and now - self.last_render < 0.05:
            return
        self.last_render = now
        self.spinner_index = (self.spinner_index + 1) % len(SPINNER_FRAMES)
        spinner = SPINNER_FRAMES[self.spinner_index]
        overall = self._overall_progress()
        current = self._current_step()

        lines = [self.title, ""]
        lines.extend(self.summary_lines)
        lines.append(f"Overall {format_bar(overall)} {overall * 100:5.1f}%")
        if current is not None:
            if current.progress is None:
                lines.append(f"Current {spinner} {current.label}: {truncate_text(current.detail, 110)}")
            else:
                lines.append(
                    f"Current {spinner} {current.label}: {format_bar(current.progress)} {current.progress * 100:5.1f}%"
                )
                lines.append(f"Status: {truncate_text(current.detail, 110)}")
        lines.append("")
        lines.append("Steps")
        for step in self.steps:
            icon = {
                "pending": "[ ]",
                "running": f"[{spinner}]",
                "done": "[x]",
                "failed": "[!]",
                "skipped": "[-]",
            }[step.status]
            suffix = ""
            if step.status == "running" and step.progress is not None:
                suffix = f" {step.progress * 100:5.1f}%"
            lines.append(f"{icon} {step.label}{suffix}")
            lines.append(f"    {truncate_text(step.detail, 120)}")
        lines.append("")
        lines.append(f"Logs: {self.logs_dir}")
        payload = "\n".join(lines)
        if self.supports_ansi:
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()
        else:
            os.system("cls" if os.name == "nt" else "clear")
            print(payload, flush=True)


@dataclass(slots=True)
class LoopState:
    accepted_promotions: int = 0
    last_run_id: str = ""
    last_promoted_run_id: str = ""
    last_classical_audit_run_id: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PairingMode:
    code: str
    name_a: str
    name_b: str
    option_a: list[str]
    option_b: list[str]
    games: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the DeadFish residual-NNUE champion loop.")
    parser.add_argument("--games", type=int, default=500, help="Self-play game budget for the batch.")
    parser.add_argument("--workers", type=int, default=20, help="Total worker budget for the batch.")
    parser.add_argument("--teacher-nodes", type=int, default=50000, help="Fixed node budget for the classical teacher.")
    parser.add_argument("--epochs", type=int, default=8, help="Training epochs for the candidate net.")
    parser.add_argument("--selfplay-tc", default="1+0.01", help="Self-play time control.")
    parser.add_argument(
        "--gate-mode",
        choices=("promote", "none"),
        default="promote",
        help="Whether to run the 25-game promotion gate or skip benchmarking.",
    )
    parser.add_argument("--cutechess", default="cutechess-cli", help="cutechess-cli executable.")
    return parser.parse_args()


def run_id() -> str:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-Date -Format yyyyMMdd-HHmmss",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return completed.stdout.strip()


def ensure_engine_exists() -> None:
    if not ENGINE_PATH.exists():
        raise FileNotFoundError(f"Native engine build not found: {ENGINE_PATH}")


def load_state() -> LoopState:
    if not STATE_PATH.exists():
        return LoopState()
    raw = json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))
    return LoopState(
        accepted_promotions=int(raw.get("accepted_promotions", 0)),
        last_run_id=str(raw.get("last_run_id", "")),
        last_promoted_run_id=str(raw.get("last_promoted_run_id", "")),
        last_classical_audit_run_id=str(raw.get("last_classical_audit_run_id", "")),
    )


def save_state(state: LoopState) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def champion_available() -> bool:
    checkpoint_exists = CHAMPION_CHECKPOINT.exists()
    nnue_exists = CHAMPION_NNUE.exists()
    if checkpoint_exists != nnue_exists:
        raise RuntimeError(
            "Champion files are inconsistent. Expected both "
            f"{CHAMPION_CHECKPOINT} and {CHAMPION_NNUE} to exist or neither."
        )
    return checkpoint_exists and nnue_exists


def split_evenly(total: int, buckets: int) -> list[int]:
    base, remainder = divmod(total, buckets)
    return [base + (1 if index < remainder else 0) for index in range(buckets)]


def build_pairings(total_games: int, champion_exists: bool) -> list[PairingMode]:
    if not champion_exists:
        return [
            PairingMode(
                code="C-C",
                name_a="DeadFish-Classical",
                name_b="DeadFish-Classical",
                option_a=["UseNNUE=false"],
                option_b=["UseNNUE=false"],
                games=total_games,
            )
        ]

    cc_games, cn_games, nn_games = split_evenly(total_games, 3)
    candidate_modes = [
        PairingMode(
            code="C-C",
            name_a="DeadFish-Classical",
            name_b="DeadFish-Classical",
            option_a=["UseNNUE=false"],
            option_b=["UseNNUE=false"],
            games=cc_games,
        ),
        PairingMode(
            code="C-N",
            name_a="DeadFish-Classical",
            name_b="DeadFish-Champion",
            option_a=["UseNNUE=false"],
            option_b=[f"EvalFile={CHAMPION_NNUE}", "UseNNUE=true"],
            games=cn_games,
        ),
        PairingMode(
            code="N-N",
            name_a="DeadFish-Champion",
            name_b="DeadFish-Champion",
            option_a=[f"EvalFile={CHAMPION_NNUE}", "UseNNUE=true"],
            option_b=[f"EvalFile={CHAMPION_NNUE}", "UseNNUE=true"],
            games=nn_games,
        ),
    ]
    return [mode for mode in candidate_modes if mode.games > 0]


def derive_selfplay_concurrency(worker_budget: int) -> int:
    return max(1, (worker_budget + 1) // 2)


def print_step(command: list[str]) -> None:
    print()
    print(">", " ".join(str(part) for part in command))


def selfplay_monitor(
    dashboard: LiveDashboard,
    step_index: int,
    pairing_code: str,
    completed_before: int,
    total_games: int,
):
    def handle(line: str) -> None:
        if match := FINISHED_GAME_RE.match(line):
            finished = completed_before + int(match.group(1))
            dashboard.update_step(
                step_index,
                progress=finished / max(1, total_games),
                detail=f"{pairing_code}: {finished}/{total_games} games finished",
            )
        elif line.startswith("Started game "):
            dashboard.update_step(step_index, detail=f"{pairing_code}: {truncate_text(line, 110)}")
        elif line.startswith("Score of "):
            dashboard.update_step(step_index, detail=f"{pairing_code}: {truncate_text(line, 110)}")
        elif line.startswith("Self-play summary:"):
            dashboard.update_step(step_index, detail=f"{pairing_code}: {truncate_text(line, 110)}")

    return handle


def annotation_monitor(dashboard: LiveDashboard, step_index: int):
    def handle(line: str) -> None:
        if match := ANNOTATION_PROGRESS_RE.match(line):
            completed = int(match.group(1))
            total = int(match.group(2))
            dashboard.update_step(
                step_index,
                progress=completed / max(1, total),
                detail=f"Annotated {completed}/{total} positions",
            )
        elif line.startswith("Annotating "):
            dashboard.update_step(step_index, detail=truncate_text(line, 110))
        elif line.startswith("Annotation summary:"):
            dashboard.update_step(step_index, progress=1.0, detail=truncate_text(line, 110))

    return handle


def training_monitor(dashboard: LiveDashboard, step_index: int, total_epochs: int):
    def handle(line: str) -> None:
        if match := TRAIN_EPOCH_RE.match(line):
            epoch = int(match.group(1))
            train_loss = match.group(2)
            validation_loss = match.group(3)
            dashboard.update_step(
                step_index,
                progress=epoch / max(1, total_epochs),
                detail=f"Epoch {epoch}/{total_epochs}: train={train_loss} val={validation_loss}",
            )
        elif line.startswith("{") or line.startswith("  ") or line.startswith("}"):
            return
        elif line:
            dashboard.update_step(step_index, detail=truncate_text(line, 110))

    return handle


def parity_monitor(dashboard: LiveDashboard, step_index: int):
    counts = {"checked": 0}

    def handle(line: str) -> None:
        if match := PARITY_CHECK_RE.match(line):
            counts["checked"] = max(counts["checked"], int(match.group(1)))
            dashboard.update_step(step_index, detail=f"Checked {counts['checked']} FENs for parity")
        elif line.startswith("{") or line.startswith("  ") or line.startswith("}"):
            return
        elif line:
            dashboard.update_step(step_index, detail=truncate_text(line, 110))

    return handle


def match_monitor(dashboard: LiveDashboard, step_index: int, label: str):
    def handle(line: str) -> None:
        if match := FINISHED_GAME_RE.match(line):
            finished = int(match.group(1))
            total = int(match.group(2))
            dashboard.update_step(
                step_index,
                progress=finished / max(1, total),
                detail=f"{label}: {finished}/{total} games finished",
            )
        elif line.startswith("Score of "):
            dashboard.update_step(step_index, detail=truncate_text(line, 110))
        elif line.startswith("Parsed score:") or line.startswith("Gate summary:"):
            dashboard.update_step(step_index, progress=1.0, detail=truncate_text(line, 110))

    return handle


def generic_monitor(dashboard: LiveDashboard, step_index: int):
    def handle(line: str) -> None:
        if line and not line.startswith("{") and not line.startswith("  ") and line != "}":
            dashboard.update_step(step_index, detail=truncate_text(line, 110))

    return handle


def run_and_tee(
    command: list[str],
    log_path: Path,
    dashboard: LiveDashboard | None = None,
    step_index: int | None = None,
    monitor=None,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if dashboard is None or not dashboard.enabled:
        print_step(command)
    with log_path.open("w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert process.stdout is not None
        for line in process.stdout:
            log_handle.write(line)
            cleaned = line.rstrip()
            if dashboard is None or not dashboard.enabled:
                print(line, end="")
            elif monitor is not None:
                monitor(cleaned)
        return process.wait()


def run_python(
    script: Path,
    *args: str,
    log_path: Path,
    dashboard: LiveDashboard | None = None,
    step_index: int | None = None,
    monitor=None,
) -> int:
    command = [sys.executable, str(script), *args]
    return run_and_tee(command, log_path, dashboard=dashboard, step_index=step_index, monitor=monitor)


def copy_if_exists(source: Path, destination: Path) -> None:
    if source.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def write_metadata(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    ensure_engine_exists()
    if not OPENING_SUITE.exists():
        raise FileNotFoundError(f"Opening suite not found: {OPENING_SUITE}")

    state = load_state()
    champion_exists = champion_available()
    current_run_id = run_id()
    run_dir = RUNS_DIR / current_run_id
    logs_dir = run_dir / "logs"
    run_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    pgn_path = run_dir / "selfplay.pgn"
    positions_path = run_dir / "positions.jsonl"
    annotated_path = run_dir / "positions_annotated.jsonl"
    checkpoint_path = run_dir / "deadfish_candidate.pt"
    nnue_path = run_dir / "deadfish_candidate.nnue"
    metadata_path = run_dir / "run_metadata.json"

    worker_budget = max(1, args.workers)
    selfplay_concurrency = derive_selfplay_concurrency(worker_budget)
    pairings = build_pairings(args.games, champion_exists)

    metadata: dict[str, object] = {
        "run_id": current_run_id,
        "games": args.games,
        "worker_budget": worker_budget,
        "selfplay_concurrency": selfplay_concurrency,
        "annotation_workers": worker_budget,
        "teacher_mode": "classical",
        "teacher_nodes": args.teacher_nodes,
        "epochs": args.epochs,
        "selfplay_tc": args.selfplay_tc,
        "gate_mode": args.gate_mode,
        "baseline_champion_checkpoint": str(CHAMPION_CHECKPOINT) if champion_exists else None,
        "baseline_champion_eval": str(CHAMPION_NNUE) if champion_exists else None,
        "accepted_promotions_before": state.accepted_promotions,
        "generation_mix": [asdict(mode) for mode in pairings],
        "promoted": False,
        "bootstrap": not champion_exists,
        "promotion_gate_log": None,
        "classical_audit_log": None,
    }
    write_metadata(metadata_path, metadata)

    dashboard = LiveDashboard(
        title="DeadFish Classical-Teacher Residual NNUE Champion Loop",
        summary_lines=[
            f"Run: {current_run_id}",
            f"Games: {args.games}  Workers: {worker_budget}  Self-play concurrency: {selfplay_concurrency}",
            f"Teacher: classical  Nodes: {args.teacher_nodes}  Epochs: {args.epochs}",
            f"Gate: {args.gate_mode}  Champion baseline: {'yes' if champion_exists else 'no'}",
            f"Run dir: {run_dir}",
        ],
        step_labels=[
            "Self-play generation",
            "Extract positions",
            "Classical annotation",
            "Train residual NNUE",
            "Export candidate",
            "Parity check",
            "Promotion gate",
            "Classical audit",
        ],
        logs_dir=logs_dir,
    )

    if dashboard.enabled:
        dashboard.render(force=True)
    else:
        print()
        print("DeadFish Classical-Teacher Residual NNUE Champion Loop")
        print(f"  run id: {current_run_id}")
        print(f"  games: {args.games}")
        print(f"  worker budget: {worker_budget}")
        print(f"  self-play concurrency: {selfplay_concurrency}")
        print(f"  annotation workers: {worker_budget}")
        print(f"  teacher nodes: {args.teacher_nodes}")
        print(f"  epochs: {args.epochs}")
        print(f"  self-play tc: {args.selfplay_tc}")
        print(f"  gate mode: {args.gate_mode}")
        print(f"  champion baseline: {'yes' if champion_exists else 'no'}")
        print(f"  run dir: {run_dir}")

    append = False
    finished_selfplay_games = 0
    dashboard.start_step(0, detail="Preparing self-play pool...", progress=0.0)
    for index, pairing in enumerate(pairings, start=1):
        mode_log = logs_dir / f"{index:02d}_selfplay_{pairing.code}.log"
        generate_args = [
            str(TRAINING_DIR / "generate_selfplay_pgn.py"),
            "--cutechess",
            args.cutechess,
            "--engine",
            str(ENGINE_PATH),
            "--name-a",
            pairing.name_a,
            "--name-b",
            pairing.name_b,
            "--games",
            str(pairing.games),
            "--concurrency",
            str(selfplay_concurrency),
            "--tc",
            args.selfplay_tc,
            "--opening-file",
            str(OPENING_SUITE),
            "--opening-format",
            "pgn",
            "--opening-order",
            "random",
            "--opening-plies",
            "8",
            "--output-pgn",
            str(pgn_path),
        ]
        if append:
            generate_args.append("--append")
        for option in pairing.option_a:
            generate_args.extend(["--option-a", option])
        for option in pairing.option_b:
            generate_args.extend(["--option-b", option])
        dashboard.update_step(
            0,
            detail=f"{pairing.code}: queued {pairing.games} games ({pairing.name_a} vs {pairing.name_b})",
            progress=finished_selfplay_games / max(1, args.games),
        )
        rc = run_python(
            Path(generate_args[0]),
            *generate_args[1:],
            log_path=mode_log,
            dashboard=dashboard,
            step_index=0,
            monitor=selfplay_monitor(dashboard, 0, pairing.code, finished_selfplay_games, args.games),
        )
        if rc != 0:
            dashboard.finish_step(0, "failed", detail=f"{pairing.code}: self-play failed")
            return rc
        append = True
        finished_selfplay_games += pairing.games
        dashboard.update_step(0, progress=finished_selfplay_games / max(1, args.games))
    dashboard.finish_step(0, "done", detail=f"Generated {args.games} self-play games")

    dashboard.start_step(1, detail="Extracting positions from PGN...")
    rc = run_python(
        TRAINING_DIR / "extract_positions.py",
        "--input-pgn",
        str(pgn_path),
        "--output",
        str(positions_path),
        log_path=logs_dir / "10_extract_positions.log",
        dashboard=dashboard,
        step_index=1,
        monitor=generic_monitor(dashboard, 1),
    )
    if rc != 0:
        dashboard.finish_step(1, "failed", detail="Position extraction failed")
        return rc
    dashboard.finish_step(1, "done", detail="Position extraction complete")

    dashboard.start_step(2, detail="Annotating positions with the classical teacher...", progress=0.0)
    rc = run_python(
        TRAINING_DIR / "annotate_positions.py",
        "--engine",
        str(ENGINE_PATH),
        "--input",
        str(positions_path),
        "--output",
        str(annotated_path),
        "--nodes",
        str(args.teacher_nodes),
        "--workers",
        str(worker_budget),
        "--option",
        "UseNNUE=false",
        log_path=logs_dir / "20_annotate_positions.log",
        dashboard=dashboard,
        step_index=2,
        monitor=annotation_monitor(dashboard, 2),
    )
    if rc != 0:
        dashboard.finish_step(2, "failed", detail="Classical annotation failed")
        return rc
    dashboard.finish_step(2, "done", detail="Classical annotation complete")

    train_args = [
        "--input",
        str(annotated_path),
        "--target-mode",
        "classical-residual",
        "--epochs",
        str(args.epochs),
        "--output-checkpoint",
        str(checkpoint_path),
    ]
    if champion_exists:
        train_args.extend(["--initialize-from", str(CHAMPION_CHECKPOINT)])
    dashboard.start_step(3, detail="Training residual NNUE...", progress=0.0)
    rc = run_python(
        TRAINING_DIR / "train_nnue.py",
        *train_args,
        log_path=logs_dir / "30_train_nnue.log",
        dashboard=dashboard,
        step_index=3,
        monitor=training_monitor(dashboard, 3, args.epochs),
    )
    if rc != 0:
        dashboard.finish_step(3, "failed", detail="Training failed")
        return rc
    dashboard.finish_step(3, "done", detail="Residual NNUE training complete")

    dashboard.start_step(4, detail="Exporting candidate NNUE...")
    rc = run_python(
        TRAINING_DIR / "export_nnue.py",
        "--checkpoint",
        str(checkpoint_path),
        "--output",
        str(nnue_path),
        "--write-metadata",
        "--inspect",
        log_path=logs_dir / "40_export_nnue.log",
        dashboard=dashboard,
        step_index=4,
        monitor=generic_monitor(dashboard, 4),
    )
    if rc != 0:
        dashboard.finish_step(4, "failed", detail="Export failed")
        return rc
    dashboard.finish_step(4, "done", detail="Candidate export complete")

    dashboard.start_step(5, detail="Checking checkpoint/export/engine parity...")
    rc = run_python(
        ROOT / "scripts" / "nnue_parity.py",
        "--checkpoint",
        str(checkpoint_path),
        "--eval-file",
        str(nnue_path),
        "--sample-jsonl",
        str(annotated_path),
        log_path=logs_dir / "50_nnue_parity.log",
        dashboard=dashboard,
        step_index=5,
        monitor=parity_monitor(dashboard, 5),
    )
    if rc != 0:
        dashboard.finish_step(5, "failed", detail="Parity check failed")
        return rc
    dashboard.finish_step(5, "done", detail="Parity check passed")

    promoted = False
    if args.gate_mode == "none":
        dashboard.skip_step(6, "Promotion gate disabled for this run")
        dashboard.skip_step(7, "Classical audit skipped because the gate was disabled")
        print("Promotion gate skipped. Champion unchanged.")
    elif not champion_exists:
        dashboard.finish_step(6, "done", detail="Bootstrap candidate promoted provisionally")
        print("No champion baseline found. Promoting bootstrap candidate provisionally.")
        promoted = True
    else:
        gate_log = logs_dir / "60_promotion_gate.log"
        metadata["promotion_gate_log"] = str(gate_log)
        dashboard.start_step(6, detail="Running 25-game candidate-vs-champion gate...", progress=0.0)
        rc = run_python(
            ROOT / "scripts" / "nnue_benchmark.py",
            "--cutechess",
            args.cutechess,
            "--engine",
            str(ENGINE_PATH),
            "--eval-file",
            str(nnue_path),
            "--baseline-eval-file",
            str(CHAMPION_NNUE),
            "--mode",
            "quick",
            "--games",
            str(GATE_GAMES),
            "--tc",
            GATE_TC,
            "--concurrency",
            str(GATE_CONCURRENCY),
            "--require-positive",
            log_path=gate_log,
            dashboard=dashboard,
            step_index=6,
            monitor=match_monitor(dashboard, 6, "Promotion gate"),
        )
        if rc == 0:
            promoted = True
            dashboard.finish_step(6, "done", detail="Candidate beat the current champion")
        elif rc == 2:
            dashboard.finish_step(6, "done", detail="Candidate did not beat the current champion")
            print("Candidate did not beat the current champion. Champion unchanged.")
        else:
            dashboard.finish_step(6, "failed", detail="Promotion gate failed")
            return rc

    if promoted:
        copy_if_exists(checkpoint_path, CHAMPION_CHECKPOINT)
        copy_if_exists(nnue_path, CHAMPION_NNUE)
        copy_if_exists(nnue_path.with_suffix(nnue_path.suffix + ".json"), CHAMPION_METADATA)
        state.accepted_promotions += 1
        state.last_promoted_run_id = current_run_id
        metadata["promoted"] = True

        if state.accepted_promotions % 10 == 0:
            audit_log = logs_dir / "70_classical_audit.log"
            metadata["classical_audit_log"] = str(audit_log)
            dashboard.start_step(7, detail="Running periodic classical audit...", progress=0.0)
            rc = run_python(
                ROOT / "scripts" / "nnue_benchmark.py",
                "--cutechess",
                args.cutechess,
                "--engine",
                str(ENGINE_PATH),
                "--eval-file",
                str(CHAMPION_NNUE),
                "--mode",
                "quick",
                "--games",
                str(GATE_GAMES),
                "--tc",
                GATE_TC,
                "--concurrency",
                str(GATE_CONCURRENCY),
                log_path=audit_log,
                dashboard=dashboard,
                step_index=7,
                monitor=match_monitor(dashboard, 7, "Classical audit"),
            )
            if rc != 0:
                dashboard.finish_step(7, "failed", detail="Classical audit failed")
                return rc
            state.last_classical_audit_run_id = current_run_id
            dashboard.finish_step(7, "done", detail="Periodic classical audit recorded")
        else:
            dashboard.skip_step(7, "Classical audit not scheduled on this promotion")
    else:
        dashboard.skip_step(7, "Classical audit skipped because no promotion was accepted")

    state.last_run_id = current_run_id
    save_state(state)

    metadata["accepted_promotions_after"] = state.accepted_promotions
    metadata["current_champion_checkpoint"] = str(CHAMPION_CHECKPOINT) if CHAMPION_CHECKPOINT.exists() else None
    metadata["current_champion_eval"] = str(CHAMPION_NNUE) if CHAMPION_NNUE.exists() else None
    write_metadata(metadata_path, metadata)

    print()
    print("Champion loop run complete.")
    print(f"  promoted: {'yes' if promoted else 'no'}")
    print(f"  accepted promotions: {state.accepted_promotions}")
    print(f"  run dir: {run_dir}")
    if dashboard.enabled:
        dashboard.render(force=True)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
