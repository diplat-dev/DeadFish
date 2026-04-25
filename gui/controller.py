from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Mapping

import chess
import chess.pgn

from .uci import (
    BestMoveEvent,
    EngineIdentity,
    IdEvent,
    InfoEvent,
    OptionEvent,
    ProcessExitedEvent,
    ReadyOkEvent,
    UciClient,
    UciOkEvent,
    UciOption,
    coerce_option_value,
    discover_default_engine,
    discover_default_nnue,
    format_score,
    option_default_value,
    uci_option_value_text,
)


@dataclass(slots=True)
class AnalysisSnapshot:
    depth: int = 0
    score_kind: str | None = None
    score_value: int | None = None
    score_text: str = ""
    nodes: int = 0
    nps: int = 0
    time_ms: int = 0
    best_move: str = ""
    pv: str = ""


@dataclass(slots=True)
class MoveAttemptResult:
    status: str
    message: str = ""
    promotion_options: tuple[int, ...] = ()


@dataclass(slots=True)
class EngineSlot:
    slot_id: int
    path: Path | None = None
    command: list[str] | None = None
    client: UciClient | None = None
    identity: EngineIdentity = field(default_factory=EngineIdentity)
    options: OrderedDict[str, UciOption] = field(default_factory=OrderedDict)
    applied_option_values: dict[str, Any] = field(default_factory=dict)
    draft_option_values: dict[str, Any] = field(default_factory=dict)
    ready: bool = False
    search_kind: str = "idle"
    waiting_for_stop: bool = False
    desired_search_kind: str | None = None
    pending_ready_reason: str | None = None
    pending_option_apply: dict[str, Any] | None = None
    inflight_option_apply: dict[str, Any] | None = None
    pending_button_option: str | None = None
    inflight_button_option: str | None = None
    needs_ucinewgame: bool = True

    def display_name(self) -> str:
        if self.identity.name:
            return self.identity.name
        if self.path is not None:
            return self.path.name
        return f"Engine {self.slot_id}"


class GameController:
    def __init__(self) -> None:
        self.engine_slots: list[EngineSlot] = []
        self.active_slot_id: int | None = None
        self._next_slot_id = 1
        self.side_players: dict[bool, int | None] = {chess.WHITE: None, chess.BLACK: None}
        self.logs: deque[str] = deque(maxlen=600)
        self.board = chess.Board()
        self.root_fen = chess.STARTING_FEN
        self.last_move: chess.Move | None = None
        self.analysis = AnalysisSnapshot()
        self.status_text = "No engine loaded."
        self.move_time_ms = 500
        self.node_limit = 10_000
        self.search_depth = 5
        self.play_search_mode = "clock"
        self.play_mode = True
        self.think_on_opponent_turn = True
        self.analysis_enabled = False
        self.human_color = chess.WHITE
        self.search_kind = "idle"
        self.waiting_for_stop = False
        self.desired_search_kind: str | None = None
        self.engine_generation = 0
        self.settings_version = 0
        self.clock_base_ms = 10 * 60 * 1000
        self.clock_increment_ms = 0
        self.clock_remaining_ms: dict[bool, int] = {
            chess.WHITE: self.clock_base_ms,
            chess.BLACK: self.clock_base_ms,
        }
        self.clock_running_color: bool | None = None
        self.clock_running_since: float | None = None
        self.game_result_override: str | None = None

    @property
    def active_slot(self) -> EngineSlot | None:
        return self._slot_by_id(self.active_slot_id)

    @property
    def client(self) -> UciClient | None:
        slot = self.active_slot
        return slot.client if slot is not None else None

    @property
    def engine_path(self) -> Path | None:
        slot = self.active_slot
        return slot.path if slot is not None else None

    @property
    def engine_identity(self) -> EngineIdentity:
        slot = self.active_slot
        return slot.identity if slot is not None else EngineIdentity()

    @property
    def engine_options(self) -> OrderedDict[str, UciOption]:
        slot = self.active_slot
        return slot.options if slot is not None else OrderedDict()

    @property
    def applied_option_values(self) -> dict[str, Any]:
        slot = self.active_slot
        return slot.applied_option_values if slot is not None else {}

    @property
    def draft_option_values(self) -> dict[str, Any]:
        slot = self.active_slot
        return slot.draft_option_values if slot is not None else {}

    @property
    def engine_ready(self) -> bool:
        slot = self.active_slot
        return bool(slot and slot.ready)

    def shutdown(self) -> None:
        for slot in self.engine_slots:
            if slot.client is not None:
                slot.client.close()
                slot.client = None
        self.engine_slots.clear()
        self.active_slot_id = None
        self._refresh_global_search_state()

    def append_log(self, line: str) -> None:
        text = line.strip()
        if text:
            self.logs.append(text)

    def connect_engine(self, path: Path | str | None, *, command: list[str] | None = None) -> bool:
        self.shutdown()
        slot = self.add_engine_slot(path, command=command)
        if slot is None:
            return False
        self.side_players[chess.WHITE] = None
        self.side_players[chess.BLACK] = slot.slot_id
        self.human_color = chess.WHITE
        return True

    def add_engine_slot(self, path: Path | str | None, *, command: list[str] | None = None) -> EngineSlot | None:
        if path is None:
            self.status_text = "No engine executable selected."
            return None

        engine_path = Path(path).resolve()
        if command is None and not engine_path.exists():
            self.status_text = f"Engine not found: {engine_path}"
            self.append_log(self.status_text)
            return None

        slot = EngineSlot(slot_id=self._next_slot_id, path=engine_path, command=command)
        self._next_slot_id += 1
        try:
            slot.client = UciClient(engine_path, command=command)
            slot.client.start()
            slot.client.send("uci")
        except Exception as exc:  # noqa: BLE001
            if slot.client is not None:
                slot.client.close()
            self.status_text = f"Failed to start engine: {exc}"
            self.append_log(self.status_text)
            return None

        self.engine_slots.append(slot)
        self.engine_generation += 1
        if self.active_slot_id is None:
            self.active_slot_id = slot.slot_id
        if self.side_players[chess.BLACK] is None and self.side_players[chess.WHITE] != slot.slot_id:
            self.side_players[chess.BLACK] = slot.slot_id
        self.status_text = f"Connecting to {engine_path.name}..."
        self.append_log(f"Launching engine {slot.slot_id}: {engine_path}")
        self.settings_version += 1
        self._sync_search_state(force_restart=True)
        return slot

    def default_engine(self) -> Path | None:
        return discover_default_engine()

    def default_nnue(self) -> Path | None:
        return discover_default_nnue()

    def current_fen(self) -> str:
        return self.board.fen(en_passant="fen")

    def move_history_text(self) -> str:
        game = chess.pgn.Game()
        game.headers["Event"] = "DeadFish GUI Game"
        game.headers["Site"] = "Local"
        game.headers["Date"] = "????.??.??"
        game.headers["Round"] = "-"
        game.headers["White"] = self._player_name(chess.WHITE)
        game.headers["Black"] = self._player_name(chess.BLACK)
        game.headers["Result"] = self.result_text()
        if self.root_fen != chess.STARTING_FEN:
            game.setup(chess.Board(self.root_fen))

        node = game
        for move in self.board.move_stack:
            node = node.add_variation(move)
        exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
        text = game.accept(exporter).strip()
        return text or "*"

    def result_text(self) -> str:
        if self.game_result_override is not None:
            return self.game_result_override
        if self.board.is_game_over(claim_draw=True):
            return self.board.result(claim_draw=True)
        return "*"

    def set_active_engine_slot(self, slot_id: int | None) -> None:
        if slot_id == self.active_slot_id:
            return
        if slot_id is not None and self._slot_by_id(slot_id) is None:
            return
        self.active_slot_id = slot_id
        self.analysis = AnalysisSnapshot()
        self.status_text = f"Active engine set to {self.engine_identity.name or 'none'}."
        self.settings_version += 1
        self._sync_search_state(force_restart=True)

    def engine_slot_choices(self) -> tuple[str, ...]:
        return tuple(self._slot_choice(slot) for slot in self.engine_slots)

    def player_choices(self) -> tuple[str, ...]:
        return ("Human", *self.engine_slot_choices())

    def active_slot_choice(self) -> str:
        slot = self.active_slot
        return self._slot_choice(slot) if slot is not None else ""

    def side_player_choice(self, color: bool) -> str:
        slot_id = self.side_players[color]
        slot = self._slot_by_id(slot_id)
        return self._slot_choice(slot) if slot is not None else "Human"

    def set_active_slot_from_choice(self, choice: str) -> None:
        slot_id = self._slot_id_from_choice(choice)
        self.set_active_engine_slot(slot_id)

    def set_side_player_from_choice(self, color: bool, choice: str) -> None:
        slot_id = self._slot_id_from_choice(choice)
        self.set_side_player(color, slot_id)

    def set_side_player(self, color: bool, slot_id: int | None) -> None:
        if slot_id is not None and self._slot_by_id(slot_id) is None:
            return
        if self.side_players[color] == slot_id:
            return
        self.side_players[color] = slot_id
        if slot_id is None and self.side_players[not color] is not None:
            self.human_color = color
        self.status_text = f"{'White' if color == chess.WHITE else 'Black'} set to {self._player_name(color)}."
        self.settings_version += 1
        self._sync_search_state(force_restart=True)

    def switch_sides(self) -> None:
        self.side_players[chess.WHITE], self.side_players[chess.BLACK] = (
            self.side_players[chess.BLACK],
            self.side_players[chess.WHITE],
        )
        if self.side_players[chess.WHITE] is None and self.side_players[chess.BLACK] is not None:
            self.human_color = chess.WHITE
        elif self.side_players[chess.BLACK] is None and self.side_players[chess.WHITE] is not None:
            self.human_color = chess.BLACK
        self.status_text = "Swapped White and Black players."
        self.settings_version += 1
        self._sync_search_state(force_restart=True)

    def is_human_turn(self) -> bool:
        return not self.play_mode or self.side_players[self.board.turn] is None

    def set_clock(self, base_minutes: Any | None = None, increment_seconds: Any | None = None) -> None:
        if base_minutes is not None:
            try:
                minutes = float(str(base_minutes).strip())
            except (TypeError, ValueError):
                minutes = self.clock_base_ms / 60_000
            self.clock_base_ms = max(1, int(minutes * 60_000))
        if increment_seconds is not None:
            try:
                seconds = float(str(increment_seconds).strip())
            except (TypeError, ValueError):
                seconds = self.clock_increment_ms / 1000
            self.clock_increment_ms = max(0, int(seconds * 1000))
        self.clock_remaining_ms = {chess.WHITE: self.clock_base_ms, chess.BLACK: self.clock_base_ms}
        self.clock_running_color = None
        self.clock_running_since = None
        self.game_result_override = None
        self.status_text = "Clock reset."
        self._sync_clock_state()
        self._sync_search_state(force_restart=True)

    def clock_ms(self, color: bool) -> int:
        remaining = self.clock_remaining_ms[color]
        if self.clock_running_color == color and self.clock_running_since is not None:
            elapsed = int((time.monotonic() - self.clock_running_since) * 1000)
            remaining -= elapsed
        return max(0, remaining)

    def clock_text(self, color: bool) -> str:
        total = self.clock_ms(color)
        minutes, remainder = divmod(total // 1000, 60)
        tenths = (total % 1000) // 100
        if minutes == 0:
            return f"{remainder}.{tenths:01d}"
        return f"{minutes}:{remainder:02d}"

    def load_fen(self, fen: str) -> tuple[bool, str]:
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            return False, str(exc)
        self.root_fen = board.fen(en_passant="fen")
        self.board = chess.Board(self.root_fen)
        self.last_move = None
        self.analysis = AnalysisSnapshot()
        self.game_result_override = None
        self._mark_new_game_for_slots()
        self.status_text = "Loaded custom FEN."
        self.append_log(f"Loaded FEN: {self.root_fen}")
        self._sync_clock_state(reset_running=True)
        self._sync_search_state(force_restart=True)
        return True, ""

    def new_game(self) -> None:
        self.root_fen = chess.STARTING_FEN
        self.board = chess.Board()
        self.last_move = None
        self.analysis = AnalysisSnapshot()
        self.clock_remaining_ms = {chess.WHITE: self.clock_base_ms, chess.BLACK: self.clock_base_ms}
        self.clock_running_color = None
        self.clock_running_since = None
        self.game_result_override = None
        self._mark_new_game_for_slots()
        self.status_text = "New game ready."
        self._sync_clock_state()
        self._sync_search_state(force_restart=True)

    def reset_position(self) -> None:
        self.board = chess.Board(self.root_fen)
        self.last_move = None
        self.analysis = AnalysisSnapshot()
        self.clock_remaining_ms = {chess.WHITE: self.clock_base_ms, chess.BLACK: self.clock_base_ms}
        self.clock_running_color = None
        self.clock_running_since = None
        self.game_result_override = None
        self._mark_new_game_for_slots()
        self.status_text = "Position reset."
        self._sync_clock_state()
        self._sync_search_state(force_restart=True)

    def set_play_mode(self, enabled: bool) -> None:
        self.play_mode = bool(enabled)
        self.status_text = "Engine players enabled." if self.play_mode else "Manual play enabled for both sides."
        self._sync_clock_state()
        self._sync_search_state(force_restart=True)

    def set_analysis_enabled(self, enabled: bool) -> None:
        self.analysis_enabled = bool(enabled)
        self.status_text = "Live analysis enabled." if self.analysis_enabled else "Live analysis paused."
        self._sync_search_state(force_restart=True)

    def set_move_time_ms(self, value: Any) -> None:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return
        updated = max(1, number)
        if updated == self.move_time_ms:
            return
        self.move_time_ms = updated
        self._restart_play_search_if_needed()

    def set_node_limit(self, value: Any) -> None:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return
        updated = max(1, number)
        if updated == self.node_limit:
            return
        self.node_limit = updated
        self._restart_play_search_if_needed()

    def set_search_depth(self, value: Any) -> None:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return
        self.search_depth = max(1, number)
        self.set_node_limit(self.search_depth * 10_000)

    def set_play_search_mode(self, mode: str) -> None:
        normalized = mode.strip().casefold()
        if normalized == "depth":
            normalized = "nodes"
        if normalized not in {"clock", "movetime", "nodes"}:
            return
        if normalized == self.play_search_mode:
            return
        self.play_search_mode = normalized
        labels = {
            "clock": "clock",
            "movetime": "fixed movetime",
            "nodes": "node limit",
        }
        self.status_text = f"Engine reply limit set to {labels[normalized]}."
        self._sync_clock_state()
        self._restart_play_search_if_needed()

    def set_think_on_opponent_turn(self, enabled: bool) -> None:
        updated = bool(enabled)
        if updated == self.think_on_opponent_turn:
            return
        self.think_on_opponent_turn = updated
        self.status_text = (
            "Background thinking on human turns is enabled."
            if updated
            else "Background thinking on human turns is disabled."
        )
        self._sync_search_state(force_restart=True)

    def press_button_option(self, name: str) -> None:
        slot = self.active_slot
        if slot is None or name not in slot.options or slot.options[name].kind != "button":
            return
        slot.pending_button_option = name
        self.status_text = f"Queued engine command: {name}"
        self._stop_for_pending_action(slot)
        self._send_pending_protocol_action(slot)

    def apply_option_drafts(self, draft_values: Mapping[str, Any]) -> tuple[bool, str]:
        slot = self.active_slot
        if slot is None or not slot.options:
            return False, "No engine options are available yet."

        sanitized: dict[str, Any] = {}
        for name, option in slot.options.items():
            if option.kind == "button":
                continue
            raw_value = draft_values.get(name, slot.draft_option_values.get(name, option_default_value(option)))
            value = coerce_option_value(option, raw_value)
            sanitized[name] = value

        auto_nnue_path: Path | None = None
        use_nnue_value = bool(sanitized.get("UseNNUE")) if "UseNNUE" in sanitized else False
        eval_file_value = str(sanitized.get("EvalFile", "")).strip() if "EvalFile" in sanitized else ""
        if use_nnue_value and "EvalFile" in sanitized and not eval_file_value:
            auto_nnue_path = self.default_nnue()
            if auto_nnue_path is not None:
                sanitized["EvalFile"] = str(auto_nnue_path)

        changed: dict[str, Any] = {}
        for name, option in slot.options.items():
            if option.kind == "button":
                continue
            value = sanitized[name]
            if slot.applied_option_values.get(name, option_default_value(option)) != value:
                changed[name] = value

        slot.draft_option_values.update(sanitized)
        self.settings_version += 1
        if not changed:
            self.status_text = "No engine setting changes to apply."
            return False, self.status_text

        if auto_nnue_path is not None:
            self.append_log(f"Auto-selected champion NNUE: {auto_nnue_path}")

        slot.pending_option_apply = changed
        self.status_text = "Applying engine settings..."
        self._stop_for_pending_action(slot)
        self._send_pending_protocol_action(slot)
        return True, self.status_text

    def can_user_move_piece(self, square: int) -> bool:
        piece = self.board.piece_at(square)
        if piece is None or piece.color != self.board.turn:
            return False
        if not self.play_mode:
            return True
        return self.side_players[self.board.turn] is None

    def legal_targets(self, square: int) -> list[int]:
        if not self.can_user_move_piece(square):
            return []
        return [move.to_square for move in self.board.legal_moves if move.from_square == square]

    def attempt_human_move(
        self,
        from_square: int,
        to_square: int,
        promotion: int | None = None,
    ) -> MoveAttemptResult:
        piece = self.board.piece_at(from_square)
        if piece is None:
            self.status_text = "No piece on that square."
            return MoveAttemptResult(status="illegal", message=self.status_text)
        if not self.can_user_move_piece(from_square):
            self.status_text = "That side is not available for manual moves right now."
            return MoveAttemptResult(status="illegal", message=self.status_text)

        candidate = chess.Move(from_square, to_square, promotion=promotion)
        legal_moves = list(self.board.legal_moves)
        if candidate in legal_moves:
            moving_color = self.board.turn
            self._finish_clock_for_move(moving_color)
            self.board.push(candidate)
            self.last_move = candidate
            self.analysis = AnalysisSnapshot()
            self.status_text = f"Played {candidate.uci()}."
            self._sync_clock_state()
            self._sync_search_state(force_restart=True)
            return MoveAttemptResult(status="applied", message=self.status_text)

        promotion_options = tuple(
            move.promotion
            for move in legal_moves
            if move.from_square == from_square and move.to_square == to_square and move.promotion is not None
        )
        if promotion is None and promotion_options:
            return MoveAttemptResult(status="needs_promotion", promotion_options=promotion_options)

        self.status_text = "Illegal move."
        return MoveAttemptResult(status="illegal", message=self.status_text)

    def make_user_move_uci(self, uci: str) -> MoveAttemptResult:
        move = chess.Move.from_uci(uci)
        return self.attempt_human_move(move.from_square, move.to_square, move.promotion)

    def poll(self) -> None:
        self._check_clock_timeout()
        for slot in list(self.engine_slots):
            if slot.client is None:
                continue
            for event in slot.client.poll_events():
                if isinstance(event, IdEvent):
                    if event.field == "name":
                        slot.identity.name = event.value
                    elif event.field == "author":
                        slot.identity.author = event.value
                    self.settings_version += 1
                    continue
                if isinstance(event, OptionEvent):
                    slot.options[event.option.name] = event.option
                    continue
                if isinstance(event, UciOkEvent):
                    self._initialize_option_models(slot)
                    slot.pending_ready_reason = "connect"
                    slot.ready = False
                    slot.client.send("isready")
                    self.status_text = "Waiting for engine readyok..."
                    continue
                if isinstance(event, ReadyOkEvent):
                    reason = slot.pending_ready_reason
                    slot.pending_ready_reason = None
                    slot.ready = True
                    if slot.inflight_option_apply:
                        slot.applied_option_values.update(slot.inflight_option_apply)
                        slot.inflight_option_apply = None
                        self.settings_version += 1
                    if slot.inflight_button_option is not None:
                        slot.inflight_button_option = None
                    if reason == "connect":
                        self.status_text = f"{slot.display_name()} is ready."
                    elif reason == "apply_settings":
                        self.status_text = "Engine settings applied."
                    elif reason == "button":
                        self.status_text = "Engine command completed."
                    self._after_protocol_checkpoint(slot, force_restart=reason in {"connect", "apply_settings", "button"})
                    continue
                if isinstance(event, InfoEvent):
                    if event.message:
                        self.append_log(f"{slot.display_name()}: {event.message}")
                    if event.info is not None and (slot.slot_id == self.active_slot_id or slot.search_kind == "play"):
                        info = event.info
                        if info.depth is not None:
                            self.analysis.depth = info.depth
                        if info.score_kind is not None:
                            self.analysis.score_kind = info.score_kind
                            self.analysis.score_value = info.score_value
                            self.analysis.score_text = format_score(info.score_kind, info.score_value)
                        if info.nodes is not None:
                            self.analysis.nodes = info.nodes
                        if info.nps is not None:
                            self.analysis.nps = info.nps
                        if info.time_ms is not None:
                            self.analysis.time_ms = info.time_ms
                        if info.pv:
                            self.analysis.best_move = info.pv[0]
                            self.analysis.pv = " ".join(info.pv)
                    continue
                if isinstance(event, BestMoveEvent):
                    previous_search = slot.search_kind
                    slot.search_kind = "idle"
                    slot.waiting_for_stop = False
                    if previous_search == "play":
                        self._apply_engine_move(slot, event.bestmove)
                    elif previous_search in {"analysis", "ponder"} and event.bestmove and event.bestmove != "0000":
                        self.analysis.best_move = event.bestmove
                        self.status_text = (
                            "Analysis completed." if previous_search == "analysis" else "Background analysis updated."
                        )
                    if self._send_pending_protocol_action(slot):
                        continue
                    if previous_search in {"play", "analysis", "ponder"} or slot.desired_search_kind is not None:
                        self._sync_search_state(force_restart=False)
                    continue
                if isinstance(event, ProcessExitedEvent):
                    slot.ready = False
                    slot.search_kind = "idle"
                    slot.waiting_for_stop = False
                    self.status_text = f"{slot.display_name()} exited with code {event.returncode}."
                    self.append_log(self.status_text)
                    slot.client = None
                    continue
                self.append_log(getattr(event, "raw_line", ""))
        self._sync_clock_state()
        self._refresh_global_search_state()

    def _slot_by_id(self, slot_id: int | None) -> EngineSlot | None:
        if slot_id is None:
            return None
        for slot in self.engine_slots:
            if slot.slot_id == slot_id:
                return slot
        return None

    def _slot_choice(self, slot: EngineSlot | None) -> str:
        if slot is None:
            return ""
        return f"{slot.slot_id}: {slot.display_name()}"

    def _slot_id_from_choice(self, choice: str) -> int | None:
        text = choice.strip()
        if not text or text.casefold() == "human":
            return None
        prefix = text.split(":", 1)[0].strip()
        try:
            slot_id = int(prefix)
        except ValueError:
            return None
        return slot_id if self._slot_by_id(slot_id) is not None else None

    def _player_name(self, color: bool) -> str:
        slot = self._slot_by_id(self.side_players[color])
        return slot.display_name() if slot is not None else "Human"

    def _mark_new_game_for_slots(self) -> None:
        for slot in self.engine_slots:
            slot.needs_ucinewgame = True

    def _initialize_option_models(self, slot: EngineSlot) -> None:
        slot.applied_option_values = {
            name: option_default_value(option)
            for name, option in slot.options.items()
            if option.kind != "button"
        }
        slot.draft_option_values = dict(slot.applied_option_values)
        self.settings_version += 1

    def _after_protocol_checkpoint(self, slot: EngineSlot, *, force_restart: bool) -> None:
        if self._send_pending_protocol_action(slot):
            return
        self._sync_search_state(force_restart=force_restart)

    def _send_pending_protocol_action(self, slot: EngineSlot) -> bool:
        if slot.client is None or not slot.ready or slot.search_kind != "idle" or slot.waiting_for_stop:
            return False
        if slot.pending_button_option is not None:
            name = slot.pending_button_option
            slot.pending_button_option = None
            slot.inflight_button_option = name
            slot.client.send(f"setoption name {name}")
            slot.client.send("isready")
            slot.pending_ready_reason = "button"
            slot.ready = False
            return True
        if slot.pending_option_apply:
            changes = slot.pending_option_apply
            slot.pending_option_apply = None
            slot.inflight_option_apply = changes
            for name, value in changes.items():
                option = slot.options[name]
                value_text = uci_option_value_text(option, value)
                slot.client.send(f"setoption name {name} value {value_text}")
            slot.client.send("isready")
            slot.pending_ready_reason = "apply_settings"
            slot.ready = False
            return True
        return False

    def _stop_for_pending_action(self, slot: EngineSlot) -> None:
        if slot.client is None or slot.search_kind == "idle" or slot.waiting_for_stop:
            return
        slot.client.send("stop")
        slot.waiting_for_stop = True

    def _desired_search_kind(self, slot: EngineSlot) -> str | None:
        if self.game_result_override is not None or self.board.is_game_over(claim_draw=True):
            return None
        play_slot = self.side_players[self.board.turn]
        if self.play_mode and play_slot is not None:
            return "play" if slot.slot_id == play_slot else None
        if self.analysis_enabled and slot.slot_id == self.active_slot_id:
            return "analysis"
        if self.play_mode and self.think_on_opponent_turn and play_slot is None and slot.slot_id == self.active_slot_id:
            return "ponder"
        return None

    def _restart_play_search_if_needed(self) -> None:
        if self.play_mode and self.side_players[self.board.turn] is not None:
            self._sync_search_state(force_restart=True)

    def _sync_search_state(self, *, force_restart: bool) -> None:
        for slot in self.engine_slots:
            self._sync_slot_search_state(slot, force_restart=force_restart)
        self._refresh_global_search_state()

    def _sync_slot_search_state(self, slot: EngineSlot, *, force_restart: bool) -> None:
        desired = self._desired_search_kind(slot)
        if slot.client is None or not slot.ready:
            slot.desired_search_kind = desired
            return
        if desired is None:
            slot.desired_search_kind = None
            if slot.search_kind != "idle" and not slot.waiting_for_stop:
                slot.client.send("stop")
                slot.waiting_for_stop = True
            return
        if slot.search_kind == "idle" and not slot.waiting_for_stop:
            slot.desired_search_kind = None
            self._start_search(slot, desired)
            return
        if slot.search_kind == desired and not force_restart and not slot.waiting_for_stop:
            return
        slot.desired_search_kind = desired
        if not slot.waiting_for_stop:
            slot.client.send("stop")
            slot.waiting_for_stop = True

    def _send_position_command(self, slot: EngineSlot) -> None:
        assert slot.client is not None
        if slot.needs_ucinewgame:
            slot.client.send("ucinewgame")
            slot.needs_ucinewgame = False
        moves = [move.uci() for move in self.board.move_stack]
        if self.root_fen == chess.STARTING_FEN:
            command = "position startpos"
        else:
            command = f"position fen {self.root_fen}"
        if moves:
            command += " moves " + " ".join(moves)
        slot.client.send(command)

    def _start_search(self, slot: EngineSlot, kind: str) -> None:
        if slot.client is None or not slot.ready:
            slot.desired_search_kind = kind
            return
        self._send_position_command(slot)
        if kind == "analysis":
            self.analysis = AnalysisSnapshot()
            slot.client.send("go infinite")
            slot.search_kind = "analysis"
            self.status_text = f"Analyzing with {slot.display_name()}..."
            return
        if kind == "ponder":
            slot.client.send(f"go movetime {min(self.move_time_ms, 250)}")
            slot.search_kind = "ponder"
            self.status_text = f"{slot.display_name()} is thinking on the human turn..."
            return
        if self.play_search_mode == "clock":
            command = (
                f"go wtime {self.clock_ms(chess.WHITE)} btime {self.clock_ms(chess.BLACK)} "
                f"winc {self.clock_increment_ms} binc {self.clock_increment_ms}"
            )
            slot.client.send(command)
            self.status_text = f"{slot.display_name()} is managing the clock..."
        elif self.play_search_mode == "nodes":
            slot.client.send(f"go nodes {self.node_limit}")
            self.status_text = f"{slot.display_name()} is searching {self.node_limit:,} nodes..."
        else:
            slot.client.send(f"go movetime {self.move_time_ms}")
            self.status_text = f"{slot.display_name()} is thinking for {self.move_time_ms} ms..."
        slot.search_kind = "play"

    def _apply_engine_move(self, slot: EngineSlot, bestmove: str) -> None:
        if bestmove in {"", "0000"}:
            self.status_text = f"{slot.display_name()} did not return a legal move."
            return
        try:
            move = chess.Move.from_uci(bestmove)
        except ValueError:
            self.status_text = f"{slot.display_name()} returned an invalid move: {bestmove}"
            self.append_log(self.status_text)
            return
        if move not in self.board.legal_moves:
            self.status_text = f"{slot.display_name()} returned an illegal move: {bestmove}"
            self.append_log(self.status_text)
            return
        moving_color = self.board.turn
        self._finish_clock_for_move(moving_color)
        self.board.push(move)
        self.last_move = move
        self.analysis.best_move = bestmove
        if not self.analysis.pv:
            self.analysis.pv = bestmove
        self.status_text = f"{slot.display_name()} played {bestmove}."
        self._sync_clock_state()

    def _refresh_global_search_state(self) -> None:
        kinds = [slot.search_kind for slot in self.engine_slots if slot.search_kind != "idle"]
        if "play" in kinds:
            self.search_kind = "play"
        elif "analysis" in kinds:
            self.search_kind = "analysis"
        elif "ponder" in kinds:
            self.search_kind = "ponder"
        else:
            self.search_kind = "idle"
        self.waiting_for_stop = any(slot.waiting_for_stop for slot in self.engine_slots)
        desired = [slot.desired_search_kind for slot in self.engine_slots if slot.desired_search_kind is not None]
        self.desired_search_kind = desired[0] if desired else None

    def _sync_clock_state(self, *, reset_running: bool = False) -> None:
        if reset_running:
            self.clock_running_color = None
            self.clock_running_since = None
        if self.play_search_mode != "clock" or not self.play_mode or self.result_text() != "*":
            self.clock_running_color = None
            self.clock_running_since = None
            return
        if self.clock_running_color != self.board.turn or self.clock_running_since is None:
            self.clock_running_color = self.board.turn
            self.clock_running_since = time.monotonic()

    def _finish_clock_for_move(self, color: bool) -> None:
        if self.play_search_mode != "clock":
            return
        remaining = self.clock_ms(color)
        self.clock_remaining_ms[color] = remaining + self.clock_increment_ms
        self.clock_running_color = None
        self.clock_running_since = None

    def _check_clock_timeout(self) -> None:
        if self.play_search_mode != "clock" or self.game_result_override is not None:
            return
        if self.clock_running_color is None:
            return
        color = self.clock_running_color
        if self.clock_ms(color) > 0:
            return
        self.clock_remaining_ms[color] = 0
        self.clock_running_color = None
        self.clock_running_since = None
        self.game_result_override = "0-1" if color == chess.WHITE else "1-0"
        self.status_text = f"{'White' if color == chess.WHITE else 'Black'} lost on time."
        for slot in self.engine_slots:
            if slot.client is not None and slot.search_kind != "idle" and not slot.waiting_for_stop:
                slot.client.send("stop")
                slot.waiting_for_stop = True
