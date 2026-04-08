from __future__ import annotations

import site
import sys
import time
from pathlib import Path

base = Path(__file__).resolve().parents[1]
for candidate_path in (
    base / "vendor" / "chess-1.11.2",
    base / ".gui_pydeps",
    base / ".tmp_pydeps",
):
    if candidate_path.exists():
        candidate_text = str(candidate_path)
        if candidate_text not in sys.path:
            sys.path.insert(0, candidate_text)

candidate = site.getusersitepackages()
if candidate and candidate not in sys.path and Path(candidate).exists():
    sys.path.append(candidate)

import chess

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui.controller import GameController
from gui.uci import discover_default_engine


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def pump(controller: GameController, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        controller.poll()
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError(f"Timed out while waiting for: {label_for(predicate)}")


def label_for(predicate) -> str:
    return getattr(predicate, "__name__", repr(predicate))


def main() -> int:
    default_engine = discover_default_engine()
    expect(default_engine is not None, "default engine discovery finds a DeadFish binary")

    fake_controller = GameController()
    fake_path = ROOT / "tests" / "fake_uci_engine.py"
    expect(
        fake_controller.connect_engine(fake_path, command=[sys.executable, str(fake_path)]),
        "fake UCI engine starts",
    )
    pump(fake_controller, lambda: fake_controller.engine_ready)
    expect("Style" in fake_controller.engine_options, "fake engine combo option is parsed")
    expect("Clear Hash" in fake_controller.engine_options, "fake engine button option is parsed")
    fake_controller.press_button_option("Clear Hash")
    pump(fake_controller, lambda: fake_controller.engine_ready and any("hash cleared" in line for line in fake_controller.logs))
    expect(any("hash cleared" in line for line in fake_controller.logs), "button option round-trip is surfaced")
    fake_controller.shutdown()

    controller = GameController()
    expect(controller.connect_engine(default_engine), "DeadFish engine starts")
    pump(controller, lambda: controller.engine_ready)
    expected_options = {
        "Hash",
        "Clear Hash",
        "UseNNUE",
        "EvalFile",
        "OwnBook",
        "BookPath",
        "SyzygyPath",
        "SyzygyProbeLimit",
        "MoveOverhead",
    }
    expect(expected_options.issubset(controller.engine_options.keys()), "DeadFish options are exposed through the controller")

    applied, _ = controller.apply_option_drafts({"UseNNUE": False, "EvalFile": ""})
    expect(applied, "controller queues DeadFish settings changes")
    pump(
        controller,
        lambda: controller.engine_ready and controller.applied_option_values.get("UseNNUE") is False,
    )
    expect(any("classical eval" in line for line in controller.logs), "DeadFish fallback status is surfaced in logs")

    invalid_path = "Z:/deadfish-missing/invalid.nnue"
    applied, _ = controller.apply_option_drafts({"UseNNUE": True, "EvalFile": invalid_path})
    expect(applied, "controller queues invalid EvalFile change")
    pump(
        controller,
        lambda: controller.engine_ready and controller.applied_option_values.get("EvalFile") == invalid_path,
    )
    expect(any("load failed" in line for line in controller.logs), "invalid EvalFile load failure is surfaced")

    applied, _ = controller.apply_option_drafts({"UseNNUE": False, "EvalFile": "", "OwnBook": False})
    if applied:
        pump(controller, lambda: controller.engine_ready and controller.applied_option_values.get("OwnBook") is False)

    ok, error = controller.load_fen("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1")
    expect(ok and not error, "controller accepts a non-book analysis FEN")
    controller.set_analysis_enabled(True)
    pump(controller, lambda: controller.analysis.depth > 0 and controller.analysis.pv != "")
    expect(controller.analysis.best_move != "", "analysis mode streams PV data")
    applied, _ = controller.apply_option_drafts({"MoveOverhead": 25})
    expect(applied, "controller applies settings while analysis is active")
    pump(
        controller,
        lambda: controller.engine_ready and controller.applied_option_values.get("MoveOverhead") == 25,
    )
    pump(controller, lambda: controller.analysis.depth > 0 and controller.analysis.pv != "")
    expect(controller.analysis.depth > 0, "analysis resumes after settings are applied")
    controller.set_analysis_enabled(False)
    pump(controller, lambda: controller.search_kind == "idle" and not controller.waiting_for_stop, timeout=15.0)
    expect(controller.search_kind == "idle", "analysis search stops cleanly")

    ok, error = controller.load_fen(chess.STARTING_FEN)
    expect(ok and not error, "controller reloads the starting position before play mode")

    applied, _ = controller.apply_option_drafts({"UseNNUE": False, "EvalFile": "", "OwnBook": False})
    if applied:
        pump(controller, lambda: controller.engine_ready and controller.applied_option_values.get("UseNNUE") is False)

    result = controller.make_user_move_uci("e2e4")
    expect(result.status == "applied", "legal human move is applied")
    pump(controller, lambda: len(controller.board.move_stack) >= 2, timeout=10.0)
    expect(len(controller.board.move_stack) >= 2, "engine reply is applied after a human move")

    controller.set_play_mode(False)
    controller.set_analysis_enabled(False)
    ok, error = controller.load_fen("7k/P7/8/8/8/8/8/K7 w - - 0 1")
    expect(ok and not error, "controller accepts a promotion test FEN")
    promotion = controller.attempt_human_move(chess.A7, chess.A8)
    expect(promotion.status == "needs_promotion", "promotion move requests a promotion choice")
    applied_promo = controller.attempt_human_move(chess.A7, chess.A8, chess.QUEEN)
    expect(applied_promo.status == "applied", "promotion move applies once a piece is chosen")

    ok, error = controller.load_fen(chess.STARTING_FEN)
    expect(ok and not error, "controller reloads the starting position")
    illegal = controller.make_user_move_uci("e2e5")
    expect(illegal.status == "illegal", "illegal move is rejected")
    controller.make_user_move_uci("e2e4")
    controller.reset_position()
    expect(controller.current_fen() == chess.STARTING_FEN, "reset position restores the root position")
    controller.shutdown()
    print("GUI smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
