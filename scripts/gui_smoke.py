from __future__ import annotations

import site
import struct
import sys
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui.controller import GameController
from gui.uci import discover_default_engine, discover_default_nnue


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


def make_square(file: int, rank: int) -> int:
    return rank * 8 + file


def mirror_square(square: int) -> int:
    return square ^ 56


def piece_bucket(piece: str, perspective: str) -> int:
    color_offset = 0 if piece[0] == perspective else 5
    piece_offset = {"P": 0, "N": 1, "B": 2, "R": 3, "Q": 4}[piece[1]]
    return color_offset + piece_offset


def orient_square(square: int, perspective: str) -> int:
    return square if perspective == "w" else mirror_square(square)


def feature_index(perspective: str, king_square: int, piece: str, square: int) -> int:
    return orient_square(king_square, perspective) * (10 * 64) + piece_bucket(piece, perspective) * 64 + orient_square(square, perspective)


def write_valid_nnue_fixture(path: Path) -> None:
    feature_count = 64 * 10 * 64
    accumulator_size = 1
    hidden_size = 2

    weights = [0.0] * feature_count
    white_king = make_square(4, 0)
    black_king = make_square(4, 7)
    white_queen_d4 = make_square(3, 3)
    black_queen_d5 = make_square(3, 4)
    weights[feature_index("w", white_king, "wQ", white_queen_d4)] = 0.40
    weights[feature_index("b", black_king, "wQ", white_queen_d4)] = 0.10
    weights[feature_index("w", white_king, "bQ", black_queen_d5)] = 0.05
    weights[feature_index("b", black_king, "bQ", black_queen_d5)] = 0.35

    with path.open("wb") as handle:
        handle.write(struct.pack("<8sIIIf", b"DFNNUE1\0", feature_count, accumulator_size, hidden_size, 100.0))
        handle.write(struct.pack(f"<{feature_count}f", *weights))
        handle.write(struct.pack("<f", 0.0))
        handle.write(struct.pack("<4f", 1.0, -1.0, -1.0, 1.0))
        handle.write(struct.pack("<2f", 0.0, 0.0))
        handle.write(struct.pack("<2f", 1.0, -1.0))
        handle.write(struct.pack("<f", 0.0))


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
        "Threads",
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
    expect(controller.applied_option_values.get("UseNNUE") is False, "DeadFish defaults to classical evaluation in the GUI controller")
    expect(controller.engine_options["UseNNUE"].default is False, "DeadFish advertises UseNNUE=false by default")
    expect(controller.think_on_opponent_turn, "background thinking is enabled by default")
    pump(controller, lambda: controller.search_kind == "ponder")
    expect(controller.search_kind == "ponder", "controller starts background thinking on the user's turn")
    controller.set_think_on_opponent_turn(False)
    pump(controller, lambda: controller.search_kind == "idle" and not controller.waiting_for_stop, timeout=15.0)
    expect(controller.search_kind == "idle", "disabling background thinking returns the controller to idle")

    champion_eval = discover_default_nnue()
    created_champion_fixture = False
    fallback_dir = ROOT / "training" / "output"
    fallback_eval = fallback_dir / "deadfish_current.nnue"
    fallback_meta = fallback_dir / "deadfish_current.nnue.json"
    if champion_eval is None:
        fallback_dir.mkdir(parents=True, exist_ok=True)
        write_valid_nnue_fixture(fallback_eval)
        fallback_meta.write_text('{"fixture":"gui-smoke"}\n', encoding="utf-8")
        champion_eval = fallback_eval
        created_champion_fixture = True

    applied, _ = controller.apply_option_drafts({"UseNNUE": True, "EvalFile": ""})
    expect(applied, "controller queues auto-selected champion NNUE")
    pump(
        controller,
        lambda: controller.engine_ready and controller.applied_option_values.get("UseNNUE") is True,
    )
    expect(
        controller.applied_option_values.get("EvalFile") == str(champion_eval.resolve()),
        "controller auto-selects the champion NNUE when enabling NNUE with an empty path",
    )

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
    applied, _ = controller.apply_option_drafts({"Threads": 2})
    expect(applied, "controller applies Threads while analysis is active")
    pump(
        controller,
        lambda: controller.engine_ready and controller.applied_option_values.get("Threads") == 2,
    )
    pump(controller, lambda: controller.analysis.depth > 0 and controller.analysis.pv != "")
    expect(controller.analysis.depth > 0, "analysis resumes after Threads changes")
    controller.set_analysis_enabled(False)
    pump(controller, lambda: controller.search_kind == "idle" and not controller.waiting_for_stop, timeout=15.0)
    expect(controller.search_kind == "idle", "analysis search stops cleanly")

    ok, error = controller.load_fen(chess.STARTING_FEN)
    expect(ok and not error, "controller reloads the starting position before play mode")

    applied, _ = controller.apply_option_drafts({"UseNNUE": False, "EvalFile": "", "OwnBook": False})
    if applied:
        pump(controller, lambda: controller.engine_ready and controller.applied_option_values.get("UseNNUE") is False)

    controller.set_think_on_opponent_turn(True)
    pump(controller, lambda: controller.search_kind == "ponder", timeout=15.0)
    expect(controller.search_kind == "ponder", "background thinking can be re-enabled before play mode")
    controller.set_play_search_mode("depth")
    controller.set_search_depth(2)
    expect(controller.play_search_mode == "depth", "controller switches engine reply mode to depth")
    expect(controller.search_depth == 2, "controller stores the configured play depth")
    result = controller.make_user_move_uci("e2e4")
    expect(result.status == "applied", "legal human move is applied")
    pump(controller, lambda: len(controller.board.move_stack) >= 2, timeout=10.0)
    expect(len(controller.board.move_stack) >= 2, "engine reply is applied after a human move in depth mode")

    controller.new_game()
    controller.set_play_search_mode("movetime")
    controller.set_move_time_ms(150)
    expect(controller.play_search_mode == "movetime", "controller switches engine reply mode to movetime")
    expect(controller.move_time_ms == 150, "controller stores the configured movetime")
    result = controller.make_user_move_uci("d2d4")
    expect(result.status == "applied", "legal human move is applied in movetime mode")
    pump(controller, lambda: len(controller.board.move_stack) >= 2, timeout=10.0)
    expect(len(controller.board.move_stack) >= 2, "engine reply is applied after a human move in movetime mode")

    controller.set_play_mode(False)
    controller.set_think_on_opponent_turn(False)
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
    if created_champion_fixture:
        fallback_eval.unlink(missing_ok=True)
        fallback_meta.unlink(missing_ok=True)
    print("GUI smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
