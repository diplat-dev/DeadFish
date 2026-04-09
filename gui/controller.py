from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import chess

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


class GameController:
    def __init__(self) -> None:
        self.client: UciClient | None = None
        self.engine_path: Path | None = None
        self.engine_identity = EngineIdentity()
        self.engine_options: OrderedDict[str, UciOption] = OrderedDict()
        self.applied_option_values: dict[str, Any] = {}
        self.draft_option_values: dict[str, Any] = {}
        self.logs: deque[str] = deque(maxlen=400)
        self.board = chess.Board()
        self.root_fen = chess.STARTING_FEN
        self.last_move: chess.Move | None = None
        self.analysis = AnalysisSnapshot()
        self.status_text = "No engine loaded."
        self.move_time_ms = 500
        self.search_depth = 5
        self.play_search_mode = "movetime"
        self.play_mode = True
        self.think_on_opponent_turn = True
        self.analysis_enabled = False
        self.human_color = chess.WHITE
        self.engine_ready = False
        self.search_kind = "idle"
        self.waiting_for_stop = False
        self.desired_search_kind: str | None = None
        self.pending_ready_reason: str | None = None
        self.pending_option_apply: dict[str, Any] | None = None
        self.inflight_option_apply: dict[str, Any] | None = None
        self.pending_button_option: str | None = None
        self.inflight_button_option: str | None = None
        self.engine_generation = 0
        self.settings_version = 0
        self._needs_ucinewgame = True

    def shutdown(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    def append_log(self, line: str) -> None:
        text = line.strip()
        if text:
            self.logs.append(text)

    def connect_engine(self, path: Path | str | None, *, command: list[str] | None = None) -> bool:
        self.shutdown()
        self.engine_generation += 1
        self.engine_ready = False
        self.search_kind = "idle"
        self.waiting_for_stop = False
        self.desired_search_kind = None
        self.pending_ready_reason = None
        self.pending_option_apply = None
        self.inflight_option_apply = None
        self.pending_button_option = None
        self.inflight_button_option = None
        self.engine_identity = EngineIdentity()
        self.engine_options = OrderedDict()
        self.applied_option_values = {}
        self.draft_option_values = {}
        self.analysis = AnalysisSnapshot()
        self.settings_version += 1

        if path is None:
            self.status_text = "No engine executable selected."
            return False

        self.engine_path = Path(path).resolve()
        if command is None and not self.engine_path.exists():
            self.status_text = f"Engine not found: {self.engine_path}"
            self.append_log(self.status_text)
            return False

        try:
            self.client = UciClient(self.engine_path, command=command)
            self.client.start()
            self.client.send("uci")
        except Exception as exc:  # noqa: BLE001
            self.client = None
            self.status_text = f"Failed to start engine: {exc}"
            self.append_log(self.status_text)
            return False

        self.status_text = f"Connecting to {self.engine_path.name}..."
        self.append_log(f"Launching engine: {self.engine_path}")
        self._needs_ucinewgame = True
        return True

    def default_engine(self) -> Path | None:
        return discover_default_engine()

    def default_nnue(self) -> Path | None:
        return discover_default_nnue()

    def current_fen(self) -> str:
        return self.board.fen(en_passant="fen")

    def move_history_text(self) -> str:
        replay = chess.Board(self.root_fen)
        chunks: list[str] = []
        for move in self.board.move_stack:
            san = replay.san(move)
            if replay.turn == chess.WHITE:
                chunks.append(f"{replay.fullmove_number}. {san}")
            else:
                chunks.append(f"{replay.fullmove_number}... {san}")
            replay.push(move)
        return " ".join(chunks)

    def load_fen(self, fen: str) -> tuple[bool, str]:
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            return False, str(exc)
        self.root_fen = board.fen(en_passant="fen")
        self.board = chess.Board(self.root_fen)
        self.last_move = None
        self.analysis = AnalysisSnapshot()
        self._needs_ucinewgame = True
        self.status_text = "Loaded custom FEN."
        self.append_log(f"Loaded FEN: {self.root_fen}")
        self._sync_search_state(force_restart=True)
        return True, ""

    def new_game(self) -> None:
        self.root_fen = chess.STARTING_FEN
        self.board = chess.Board()
        self.last_move = None
        self.analysis = AnalysisSnapshot()
        self._needs_ucinewgame = True
        self.status_text = "New game ready."
        self._sync_search_state(force_restart=True)

    def reset_position(self) -> None:
        self.board = chess.Board(self.root_fen)
        self.last_move = None
        self.analysis = AnalysisSnapshot()
        self._needs_ucinewgame = True
        self.status_text = "Position reset."
        self._sync_search_state(force_restart=True)

    def set_play_mode(self, enabled: bool) -> None:
        self.play_mode = bool(enabled)
        self.status_text = "Engine replies enabled." if self.play_mode else "Engine replies paused."
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

    def set_search_depth(self, value: Any) -> None:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return
        updated = max(1, number)
        if updated == self.search_depth:
            return
        self.search_depth = updated
        self._restart_play_search_if_needed()

    def set_play_search_mode(self, mode: str) -> None:
        normalized = mode.strip().casefold()
        if normalized not in {"movetime", "depth"}:
            return
        if normalized == self.play_search_mode:
            return
        self.play_search_mode = normalized
        self.status_text = (
            "Engine reply limit set to movetime."
            if normalized == "movetime"
            else "Engine reply limit set to depth."
        )
        self._restart_play_search_if_needed()

    def set_think_on_opponent_turn(self, enabled: bool) -> None:
        updated = bool(enabled)
        if updated == self.think_on_opponent_turn:
            return
        self.think_on_opponent_turn = updated
        self.status_text = (
            "Background thinking on your turn is enabled."
            if updated
            else "Background thinking on your turn is disabled."
        )
        self._sync_search_state(force_restart=True)

    def press_button_option(self, name: str) -> None:
        if name not in self.engine_options or self.engine_options[name].kind != "button":
            return
        self.pending_button_option = name
        self.status_text = f"Queued engine command: {name}"
        self._stop_for_pending_action()
        self._send_pending_protocol_action()

    def apply_option_drafts(self, draft_values: Mapping[str, Any]) -> tuple[bool, str]:
        if not self.engine_options:
            return False, "No engine options are available yet."

        sanitized: dict[str, Any] = {}
        for name, option in self.engine_options.items():
            if option.kind == "button":
                continue
            raw_value = draft_values.get(name, self.draft_option_values.get(name, option_default_value(option)))
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
        for name, option in self.engine_options.items():
            if option.kind == "button":
                continue
            value = sanitized[name]
            if self.applied_option_values.get(name, option_default_value(option)) != value:
                changed[name] = value

        self.draft_option_values.update(sanitized)
        self.settings_version += 1
        if not changed:
            self.status_text = "No engine setting changes to apply."
            return False, self.status_text

        if auto_nnue_path is not None:
            self.append_log(f"Auto-selected champion NNUE: {auto_nnue_path}")

        self.pending_option_apply = changed
        self.status_text = "Applying engine settings..."
        self._stop_for_pending_action()
        self._send_pending_protocol_action()
        return True, self.status_text

    def can_user_move_piece(self, square: int) -> bool:
        piece = self.board.piece_at(square)
        if piece is None or piece.color != self.board.turn:
            return False
        if not self.play_mode:
            return True
        return self.board.turn == self.human_color

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
            self.board.push(candidate)
            self.last_move = candidate
            self.status_text = f"Played {candidate.uci()}."
            self.analysis = AnalysisSnapshot()
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
        if self.client is None:
            return
        for event in self.client.poll_events():
            if isinstance(event, IdEvent):
                if event.field == "name":
                    self.engine_identity.name = event.value
                elif event.field == "author":
                    self.engine_identity.author = event.value
                continue
            if isinstance(event, OptionEvent):
                self.engine_options[event.option.name] = event.option
                continue
            if isinstance(event, UciOkEvent):
                self._initialize_option_models()
                self.pending_ready_reason = "connect"
                self.engine_ready = False
                self.client.send("isready")
                self.status_text = "Waiting for engine readyok..."
                continue
            if isinstance(event, ReadyOkEvent):
                reason = self.pending_ready_reason
                self.pending_ready_reason = None
                self.engine_ready = True
                if self.inflight_option_apply:
                    self.applied_option_values.update(self.inflight_option_apply)
                    self.inflight_option_apply = None
                    self.settings_version += 1
                if self.inflight_button_option is not None:
                    self.inflight_button_option = None
                if reason == "connect":
                    self.status_text = f"{self.engine_identity.name or self.engine_path.name} is ready."
                elif reason == "apply_settings":
                    self.status_text = "Engine settings applied."
                elif reason == "button":
                    self.status_text = "Engine command completed."
                self._after_protocol_checkpoint(force_restart=reason in {"connect", "apply_settings", "button"})
                continue
            if isinstance(event, InfoEvent):
                if event.message:
                    self.append_log(event.message)
                if event.info is not None:
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
                previous_search = self.search_kind
                self.search_kind = "idle"
                self.waiting_for_stop = False
                if previous_search == "play":
                    self._apply_engine_move(event.bestmove)
                elif previous_search in {"analysis", "ponder"} and event.bestmove and event.bestmove != "0000":
                    self.analysis.best_move = event.bestmove
                    self.status_text = "Analysis completed." if previous_search == "analysis" else "Background analysis updated."
                if self._send_pending_protocol_action():
                    continue
                if previous_search in {"play", "analysis", "ponder"} or self.desired_search_kind is not None:
                    self._sync_search_state(force_restart=False)
                continue
            if isinstance(event, ProcessExitedEvent):
                self.engine_ready = False
                self.search_kind = "idle"
                self.waiting_for_stop = False
                self.status_text = f"Engine process exited with code {event.returncode}."
                self.append_log(self.status_text)
                self.client = None
                continue
            self.append_log(getattr(event, "raw_line", ""))

    def _initialize_option_models(self) -> None:
        self.applied_option_values = {
            name: option_default_value(option)
            for name, option in self.engine_options.items()
            if option.kind != "button"
        }
        self.draft_option_values = dict(self.applied_option_values)
        self.settings_version += 1

    def _after_protocol_checkpoint(self, *, force_restart: bool) -> None:
        if self._send_pending_protocol_action():
            return
        self._sync_search_state(force_restart=force_restart)

    def _send_pending_protocol_action(self) -> bool:
        if self.client is None or not self.engine_ready or self.search_kind != "idle" or self.waiting_for_stop:
            return False
        if self.pending_button_option is not None:
            name = self.pending_button_option
            self.pending_button_option = None
            self.inflight_button_option = name
            self.client.send(f"setoption name {name}")
            self.client.send("isready")
            self.pending_ready_reason = "button"
            self.engine_ready = False
            return True
        if self.pending_option_apply:
            changes = self.pending_option_apply
            self.pending_option_apply = None
            self.inflight_option_apply = changes
            for name, value in changes.items():
                option = self.engine_options[name]
                value_text = uci_option_value_text(option, value)
                self.client.send(f"setoption name {name} value {value_text}")
            self.client.send("isready")
            self.pending_ready_reason = "apply_settings"
            self.engine_ready = False
            return True
        return False

    def _stop_for_pending_action(self) -> None:
        if self.client is None or self.search_kind == "idle" or self.waiting_for_stop:
            return
        self.client.send("stop")
        self.waiting_for_stop = True

    def _desired_search_kind(self) -> str | None:
        if self.play_mode and self.board.turn != self.human_color:
            return "play"
        if self.play_mode and self.think_on_opponent_turn and self.board.turn == self.human_color:
            return "ponder"
        if self.analysis_enabled:
            return "analysis"
        return None

    def _restart_play_search_if_needed(self) -> None:
        if self.play_mode and self.board.turn != self.human_color:
            self._sync_search_state(force_restart=True)

    def _sync_search_state(self, *, force_restart: bool) -> None:
        desired = self._desired_search_kind()
        if self.client is None:
            self.desired_search_kind = desired
            return
        if not self.engine_ready:
            self.desired_search_kind = desired
            return
        if desired is None:
            self.desired_search_kind = None
            if self.search_kind != "idle" and not self.waiting_for_stop:
                self.client.send("stop")
                self.waiting_for_stop = True
            return
        if self.search_kind == "idle" and not self.waiting_for_stop:
            self.desired_search_kind = None
            self._start_search(desired)
            return
        if self.search_kind == desired and not force_restart and not self.waiting_for_stop:
            return
        self.desired_search_kind = desired
        if not self.waiting_for_stop:
            self.client.send("stop")
            self.waiting_for_stop = True

    def _send_position_command(self) -> None:
        assert self.client is not None
        if self._needs_ucinewgame:
            self.client.send("ucinewgame")
            self._needs_ucinewgame = False
        moves = [move.uci() for move in self.board.move_stack]
        if self.root_fen == chess.STARTING_FEN:
            command = "position startpos"
        else:
            command = f"position fen {self.root_fen}"
        if moves:
            command += " moves " + " ".join(moves)
        self.client.send(command)

    def _start_search(self, kind: str) -> None:
        if self.client is None or not self.engine_ready:
            self.desired_search_kind = kind
            return
        self._send_position_command()
        if kind == "analysis":
            self.analysis = AnalysisSnapshot()
            self.client.send("go infinite")
            self.search_kind = "analysis"
            self.status_text = "Analyzing current position..."
            return
        if kind == "ponder":
            self.client.send(f"go movetime {min(self.move_time_ms, 250)}")
            self.search_kind = "ponder"
            self.status_text = "Thinking on your turn..."
            return
        if self.play_search_mode == "depth":
            self.client.send(f"go depth {self.search_depth}")
            self.status_text = f"Engine thinking to depth {self.search_depth}..."
        else:
            self.client.send(f"go movetime {self.move_time_ms}")
            self.status_text = f"Engine thinking for {self.move_time_ms} ms..."
        self.search_kind = "play"

    def _apply_engine_move(self, bestmove: str) -> None:
        if bestmove in {"", "0000"}:
            self.status_text = "Engine did not return a legal move."
            return
        try:
            move = chess.Move.from_uci(bestmove)
        except ValueError:
            self.status_text = f"Engine returned an invalid move: {bestmove}"
            self.append_log(self.status_text)
            return
        if move not in self.board.legal_moves:
            self.status_text = f"Engine returned an illegal move: {bestmove}"
            self.append_log(self.status_text)
            return
        self.board.push(move)
        self.last_move = move
        self.analysis.best_move = bestmove
        if not self.analysis.pv:
            self.analysis.pv = bestmove
        self.status_text = f"Engine played {bestmove}."
