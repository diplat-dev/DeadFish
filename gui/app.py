from __future__ import annotations

import argparse
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import chess

from .controller import GameController
from .uci import UciOption, discover_default_engine, option_default_value


PIECE_GLYPHS = {
    "P": "♙",
    "N": "♘",
    "B": "♗",
    "R": "♖",
    "Q": "♕",
    "K": "♔",
    "p": "♟",
    "n": "♞",
    "b": "♝",
    "r": "♜",
    "q": "♛",
    "k": "♚",
}

LIGHT_SQUARE = "#efe4d1"
DARK_SQUARE = "#b3835a"
LAST_MOVE_HIGHLIGHT = "#f0cb6b"
SELECTED_HIGHLIGHT = "#76b5a6"
CHECK_HIGHLIGHT = "#e87f6d"
LEGAL_HINT = "#285f57"
BOARD_EDGE = "#28313a"
APP_BG = "#182028"
PANEL_BG = "#f7f3ea"
TEXT_PRIMARY = "#17212a"
TEXT_MUTED = "#536270"


class BoardCanvas(tk.Canvas):
    def __init__(self, master: tk.Misc, app: "GuiApp") -> None:
        super().__init__(
            master,
            background=BOARD_EDGE,
            highlightthickness=0,
            width=640,
            height=640,
            cursor="hand2",
        )
        self.app = app
        self.flipped = False
        self.selected_square: int | None = None
        self.press_square: int | None = None
        self.drag_origin: int | None = None
        self.drag_piece: chess.Piece | None = None
        self.drag_started = False
        self.press_pointer: tuple[float, float] = (0.0, 0.0)
        self.drag_pointer: tuple[float, float] = (0.0, 0.0)
        self._last_redraw_signature: tuple[object, ...] | None = None
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_flipped(self, flipped: bool) -> None:
        self.flipped = flipped
        self.selected_square = None
        self.redraw()

    def clear_selection(self) -> None:
        self.selected_square = None
        self.press_square = None
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        board_px = min(width, height)
        square = board_px / 8.0
        offset_x = (width - board_px) / 2.0
        offset_y = (height - board_px) / 2.0
        controller = self.app.controller
        legal_targets = set()
        if self.selected_square is not None:
            legal_targets = set(controller.legal_targets(self.selected_square))
        check_square = controller.board.king(controller.board.turn) if controller.board.is_check() else None
        last_move = controller.last_move

        for row in range(8):
            for col in range(8):
                square_index = self._display_to_square(row, col)
                x1 = offset_x + col * square
                y1 = offset_y + row * square
                x2 = x1 + square
                y2 = y1 + square
                fill = LIGHT_SQUARE if (row + col) % 2 == 0 else DARK_SQUARE
                if last_move and square_index in {last_move.from_square, last_move.to_square}:
                    fill = LAST_MOVE_HIGHLIGHT
                if square_index == self.selected_square:
                    fill = SELECTED_HIGHLIGHT
                if square_index == check_square:
                    fill = CHECK_HIGHLIGHT
                self.create_rectangle(x1, y1, x2, y2, fill=fill, outline=BOARD_EDGE, width=1)

                if square_index in legal_targets:
                    pad = square * 0.38
                    self.create_oval(
                        x1 + pad,
                        y1 + pad,
                        x2 - pad,
                        y2 - pad,
                        fill=LEGAL_HINT,
                        outline="",
                    )

                piece = controller.board.piece_at(square_index)
                if piece is None:
                    self._draw_coordinate(square_index, row, col, x1, y1, x2, y2, square)
                    continue
                if self.drag_started and square_index == self.drag_origin:
                    self._draw_coordinate(square_index, row, col, x1, y1, x2, y2, square)
                    continue
                glyph = PIECE_GLYPHS[piece.symbol()]
                self.create_text(
                    (x1 + x2) / 2.0,
                    (y1 + y2) / 2.0,
                    text=glyph,
                    fill=TEXT_PRIMARY if piece.color == chess.WHITE else "#1d2430",
                    font=("Segoe UI Symbol", max(16, int(square * 0.68)), "normal"),
                )
                self._draw_coordinate(square_index, row, col, x1, y1, x2, y2, square)

        if self.drag_started and self.drag_piece is not None:
            glyph = PIECE_GLYPHS[self.drag_piece.symbol()]
            self.create_text(
                self.drag_pointer[0],
                self.drag_pointer[1],
                text=glyph,
                fill=TEXT_PRIMARY if self.drag_piece.color == chess.WHITE else "#1d2430",
                font=("Segoe UI Symbol", max(16, int(square * 0.72)), "normal"),
            )
        self._last_redraw_signature = self._redraw_signature()

    def sync_redraw(self) -> None:
        signature = self._redraw_signature()
        if signature != self._last_redraw_signature:
            self.redraw()

    def _redraw_signature(self) -> tuple[object, ...]:
        controller = self.app.controller
        return (
            max(self.winfo_width(), 1),
            max(self.winfo_height(), 1),
            self.flipped,
            controller.current_fen(),
            controller.last_move.uci() if controller.last_move is not None else "",
            self.selected_square,
            self.drag_started,
            self.drag_origin,
            round(self.drag_pointer[0]),
            round(self.drag_pointer[1]),
            controller.search_kind,
        )

    def _draw_coordinate(
        self,
        square_index: int,
        row: int,
        col: int,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        square: float,
    ) -> None:
        file_name = chess.square_file(square_index)
        rank_name = chess.square_rank(square_index)
        if col == 0:
            self.create_text(
                x1 + square * 0.14,
                y1 + square * 0.14,
                text=str(rank_name + 1),
                fill=TEXT_MUTED,
                font=("Segoe UI", max(8, int(square * 0.11))),
                anchor="nw",
            )
        if row == 7:
            self.create_text(
                x2 - square * 0.14,
                y2 - square * 0.14,
                text=chr(ord("a") + file_name),
                fill=TEXT_MUTED,
                font=("Segoe UI", max(8, int(square * 0.11))),
                anchor="se",
            )

    def _display_to_square(self, row: int, col: int) -> int:
        if self.flipped:
            file_index = 7 - col
            rank_index = row
        else:
            file_index = col
            rank_index = 7 - row
        return chess.square(file_index, rank_index)

    def _square_from_xy(self, x: float, y: float) -> int | None:
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        board_px = min(width, height)
        square = board_px / 8.0
        offset_x = (width - board_px) / 2.0
        offset_y = (height - board_px) / 2.0
        if x < offset_x or y < offset_y or x > offset_x + board_px or y > offset_y + board_px:
            return None
        col = int((x - offset_x) // square)
        row = int((y - offset_y) // square)
        if not (0 <= row < 8 and 0 <= col < 8):
            return None
        return self._display_to_square(row, col)

    def _on_press(self, event: tk.Event[tk.Misc]) -> None:
        square = self._square_from_xy(event.x, event.y)
        self.press_square = square
        self.press_pointer = (event.x, event.y)
        self.drag_pointer = (event.x, event.y)
        self.drag_started = False
        if square is None or not self.app.controller.can_user_move_piece(square):
            self.drag_origin = None
            self.drag_piece = None
            return
        self.drag_origin = square
        self.drag_piece = self.app.controller.board.piece_at(square)

    def _on_motion(self, event: tk.Event[tk.Misc]) -> None:
        if self.drag_origin is None or self.drag_piece is None:
            return
        dx = event.x - self.press_pointer[0]
        dy = event.y - self.press_pointer[1]
        if not self.drag_started and abs(dx) + abs(dy) > 6:
            self.drag_started = True
            self.selected_square = self.drag_origin
        self.drag_pointer = (event.x, event.y)
        if self.drag_started:
            self.redraw()

    def _on_release(self, event: tk.Event[tk.Misc]) -> None:
        release_square = self._square_from_xy(event.x, event.y)
        origin = self.drag_origin
        dragged = self.drag_started
        pressed_square = self.press_square
        self.press_square = None
        self.drag_origin = None
        self.drag_piece = None
        self.drag_started = False
        if dragged and origin is not None and release_square is not None:
            self.selected_square = origin
            self.redraw()
            if self._attempt_move(origin, release_square):
                self.selected_square = None
            self.redraw()
            return
        if dragged:
            self.selected_square = origin
            self.redraw()
            return
        self._handle_click(release_square if release_square is not None else pressed_square)

    def _handle_click(self, square: int | None) -> None:
        controller = self.app.controller
        if square is None:
            self.selected_square = None
            self.redraw()
            return
        if self.selected_square is None:
            if controller.can_user_move_piece(square):
                self.selected_square = square
            self.redraw()
            return
        if square == self.selected_square:
            self.selected_square = None
            self.redraw()
            return
        if square in controller.legal_targets(self.selected_square):
            if self._attempt_move(self.selected_square, square):
                self.selected_square = None
        elif controller.can_user_move_piece(square):
            self.selected_square = square
        else:
            self.selected_square = None
        self.redraw()

    def _attempt_move(self, from_square: int, to_square: int) -> bool:
        result = self.app.controller.attempt_human_move(from_square, to_square)
        if result.status == "needs_promotion":
            promotion = self.app.ask_promotion()
            if promotion is None:
                return False
            result = self.app.controller.attempt_human_move(from_square, to_square, promotion)
        return result.status == "applied"


class GuiApp:
    def __init__(self, engine_path: Path | None) -> None:
        self.controller = GameController()
        self.root = tk.Tk()
        self.root.title("DeadFish UCI Chess GUI")
        self.root.configure(background=APP_BG)
        self.root.minsize(1180, 760)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._configure_style()

        self.engine_path_var = tk.StringVar(value=str(engine_path) if engine_path else "")
        self.status_var = tk.StringVar(value=self.controller.status_text)
        self.engine_summary_var = tk.StringVar(value="No engine connected")
        self.turn_var = tk.StringVar(value="White to move")
        self.analysis_depth_var = tk.StringVar(value="-")
        self.analysis_score_var = tk.StringVar(value="-")
        self.analysis_nodes_var = tk.StringVar(value="-")
        self.analysis_nps_var = tk.StringVar(value="-")
        self.analysis_time_var = tk.StringVar(value="-")
        self.analysis_best_var = tk.StringVar(value="-")
        self.fen_var = tk.StringVar(value=self.controller.current_fen())
        self.play_mode_var = tk.BooleanVar(value=True)
        self.think_on_opponent_turn_var = tk.BooleanVar(value=self.controller.think_on_opponent_turn)
        self.analysis_enabled_var = tk.BooleanVar(value=False)
        self.flipped_var = tk.BooleanVar(value=False)
        self.play_limit_mode_var = tk.StringVar(value=self.controller.play_search_mode)
        self.play_limit_label_var = tk.StringVar(value=self._play_limit_label())
        self.play_limit_value_var = tk.StringVar(value=self._play_limit_value())

        self.option_vars: dict[str, tk.Variable] = {}
        self.rendered_settings_version = -1
        self.last_log_text = ""
        self.last_pv_text = ""
        self.last_moves_text = ""

        self._build_layout()
        if engine_path is not None:
            self.controller.connect_engine(engine_path)
        self._tick()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=PANEL_BG, foreground=TEXT_PRIMARY, font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 10), foreground=TEXT_MUTED)
        style.configure("Value.TLabel", font=("Segoe UI Semibold", 12), foreground=TEXT_PRIMARY)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("Toolbar.TFrame", background=APP_BG)
        style.configure("Toolbar.TLabel", background=APP_BG, foreground="#f0f4f7")
        style.configure("Toolbar.TButton", font=("Segoe UI Semibold", 10))
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10))
        style.configure("InfoHeader.TLabel", font=("Georgia", 12, "bold"), foreground=TEXT_PRIMARY)
        style.configure("Status.TLabel", background=APP_BG, foreground="#f0f4f7", font=("Segoe UI", 10))
        style.configure("Notebook.TNotebook", background=PANEL_BG)
        style.configure("Notebook.TNotebook.Tab", padding=(12, 6))

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, style="Toolbar.TFrame", padding=14)
        outer.pack(fill="both", expand=True)

        toolbar = ttk.Frame(outer, style="Toolbar.TFrame")
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Engine", style="Toolbar.TLabel").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.engine_path_var, width=72).pack(side="left", fill="x", expand=True, padx=(10, 8))
        ttk.Button(toolbar, text="Browse", command=self._browse_engine).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Load Engine", style="Accent.TButton", command=self._load_engine).pack(side="left")

        body = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        body.pack(fill="both", expand=True, pady=(12, 0))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(1, weight=1)

        controls = ttk.Frame(body, style="Panel.TFrame")
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        for label, command in (
            ("New Game", self._new_game),
            ("Reset Position", self._reset_position),
            ("Load FEN", self._load_fen_dialog),
            ("Copy FEN", self._copy_fen),
            ("Flip Board", self._toggle_flip),
        ):
            ttk.Button(controls, text=label, command=command).pack(side="left", padx=(0, 8))

        self.board_canvas = BoardCanvas(body, self)
        self.board_canvas.grid(row=1, column=0, sticky="nsew", padx=(0, 14))

        right = ttk.Frame(body, style="Panel.TFrame")
        right.grid(row=1, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        status_card = ttk.Frame(right, style="Panel.TFrame")
        status_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        status_card.columnconfigure(1, weight=1)
        ttk.Label(status_card, text="Engine", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(status_card, textvariable=self.engine_summary_var, style="Value.TLabel", wraplength=360).grid(
            row=0,
            column=1,
            sticky="w",
        )
        ttk.Label(status_card, text="Turn", style="Title.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(status_card, textvariable=self.turn_var, style="Value.TLabel").grid(row=1, column=1, sticky="w", pady=(8, 0))

        notebook = ttk.Notebook(right, style="Notebook.TNotebook")
        notebook.grid(row=1, column=0, sticky="nsew")

        info_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=10)
        settings_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=10)
        log_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=10)
        notebook.add(info_tab, text="Info")
        notebook.add(settings_tab, text="Settings")
        notebook.add(log_tab, text="Log")

        self._build_info_tab(info_tab)
        self._build_settings_tab(settings_tab)
        self._build_log_tab(log_tab)

        statusbar = ttk.Frame(outer, style="Toolbar.TFrame")
        statusbar.pack(fill="x", pady=(12, 0))
        ttk.Label(statusbar, textvariable=self.status_var, style="Status.TLabel").pack(side="left")

    def _build_info_tab(self, parent: ttk.Frame) -> None:
        control_row = ttk.Frame(parent, style="Panel.TFrame")
        control_row.pack(fill="x")
        ttk.Checkbutton(control_row, text="Engine Replies", variable=self.play_mode_var, command=self._toggle_play_mode).pack(
            side="left"
        )
        ttk.Checkbutton(
            control_row,
            text="Live Analysis",
            variable=self.analysis_enabled_var,
            command=self._toggle_analysis,
        ).pack(side="left", padx=(14, 0))
        ttk.Label(control_row, text="Reply Limit").pack(side="left", padx=(18, 6))
        limit_mode = ttk.Combobox(
            control_row,
            textvariable=self.play_limit_mode_var,
            values=("movetime", "depth"),
            state="readonly",
            width=10,
        )
        self.play_limit_mode_combo = limit_mode
        limit_mode.pack(side="left")
        limit_mode.bind("<<ComboboxSelected>>", lambda _event: self._apply_play_limit_mode())
        ttk.Label(control_row, textvariable=self.play_limit_label_var).pack(side="left", padx=(12, 6))
        self.play_limit_spinbox = ttk.Spinbox(
            control_row,
            width=8,
            textvariable=self.play_limit_value_var,
            command=self._apply_play_limit_value,
        )
        self.play_limit_spinbox.pack(side="left")
        self.play_limit_spinbox.bind("<FocusOut>", lambda _event: self._apply_play_limit_value())
        self.play_limit_spinbox.bind("<Return>", lambda _event: self._apply_play_limit_value())
        self._sync_play_limit_widget()

        metrics = ttk.Frame(parent, style="Panel.TFrame")
        metrics.pack(fill="x", pady=(14, 10))
        fields = [
            ("Depth", self.analysis_depth_var),
            ("Score", self.analysis_score_var),
            ("Nodes", self.analysis_nodes_var),
            ("NPS", self.analysis_nps_var),
            ("Time", self.analysis_time_var),
            ("Best", self.analysis_best_var),
        ]
        for column, (label, variable) in enumerate(fields):
            panel = ttk.Frame(metrics, style="Panel.TFrame", padding=(4, 0))
            panel.grid(row=0, column=column, sticky="w")
            ttk.Label(panel, text=label, style="Title.TLabel").pack(anchor="w")
            ttk.Label(panel, textvariable=variable, style="Value.TLabel").pack(anchor="w")

        ttk.Label(parent, text="Principal Variation", style="InfoHeader.TLabel").pack(anchor="w")
        self.pv_text = tk.Text(
            parent,
            height=4,
            wrap="word",
            background="#fffdf8",
            foreground=TEXT_PRIMARY,
            relief="flat",
            font=("Consolas", 10),
        )
        self.pv_text.pack(fill="x", pady=(6, 12))
        self.pv_text.configure(state="disabled")

        ttk.Label(parent, text="Move History", style="InfoHeader.TLabel").pack(anchor="w")
        self.moves_text = tk.Text(
            parent,
            height=5,
            wrap="word",
            background="#fffdf8",
            foreground=TEXT_PRIMARY,
            relief="flat",
            font=("Consolas", 10),
        )
        self.moves_text.pack(fill="both", expand=False, pady=(6, 12))
        self.moves_text.configure(state="disabled")

        ttk.Label(parent, text="Current FEN", style="InfoHeader.TLabel").pack(anchor="w")
        ttk.Entry(parent, textvariable=self.fen_var).pack(fill="x", pady=(6, 0))

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.settings_canvas = tk.Canvas(parent, background=PANEL_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.settings_canvas.yview)
        self.settings_canvas.configure(yscrollcommand=scrollbar.set)
        self.settings_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.settings_inner = ttk.Frame(self.settings_canvas, style="Panel.TFrame")
        self.settings_window = self.settings_canvas.create_window((0, 0), window=self.settings_inner, anchor="nw")
        self.settings_inner.bind("<Configure>", self._on_settings_configure)
        self.settings_canvas.bind("<Configure>", self._on_settings_canvas_configure)

    def _build_log_tab(self, parent: ttk.Frame) -> None:
        self.log_text = tk.Text(
            parent,
            wrap="word",
            background="#fffdf8",
            foreground=TEXT_PRIMARY,
            relief="flat",
            font=("Consolas", 10),
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def _on_settings_configure(self, _event: tk.Event[tk.Misc]) -> None:
        self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all"))

    def _on_settings_canvas_configure(self, event: tk.Event[tk.Misc]) -> None:
        self.settings_canvas.itemconfigure(self.settings_window, width=event.width)

    def _browse_engine(self) -> None:
        filename = filedialog.askopenfilename(
            title="Select a UCI engine executable",
            filetypes=[("Windows executables", "*.exe"), ("All files", "*.*")],
        )
        if filename:
            self.engine_path_var.set(filename)

    def _load_engine(self) -> None:
        text = self.engine_path_var.get().strip()
        if not text:
            default_engine = discover_default_engine()
            if default_engine is not None:
                text = str(default_engine)
                self.engine_path_var.set(text)
        if not self.controller.connect_engine(text or None):
            messagebox.showerror("Engine Load Failed", self.controller.status_text, parent=self.root)

    def _toggle_play_mode(self) -> None:
        self.controller.set_play_mode(self.play_mode_var.get())

    def _toggle_think_on_opponent_turn(self) -> None:
        self.controller.set_think_on_opponent_turn(self.think_on_opponent_turn_var.get())

    def _toggle_analysis(self) -> None:
        self.controller.set_analysis_enabled(self.analysis_enabled_var.get())

    def _play_limit_label(self) -> str:
        return "Movetime (ms)" if self.controller.play_search_mode == "movetime" else "Depth"

    def _play_limit_value(self) -> str:
        if self.controller.play_search_mode == "movetime":
            return str(self.controller.move_time_ms)
        return str(self.controller.search_depth)

    def _sync_play_limit_widget(self) -> None:
        if not hasattr(self, "play_limit_spinbox"):
            return
        if self.controller.play_search_mode == "movetime":
            self.play_limit_spinbox.configure(from_=1, to=600000)
        else:
            self.play_limit_spinbox.configure(from_=1, to=100)
        self.play_limit_label_var.set(self._play_limit_label())
        self.play_limit_value_var.set(self._play_limit_value())

    def _apply_play_limit_mode(self) -> None:
        self.controller.set_play_search_mode(self.play_limit_mode_var.get())
        self.play_limit_mode_var.set(self.controller.play_search_mode)
        self._sync_play_limit_widget()

    def _apply_play_limit_value(self) -> None:
        if self.controller.play_search_mode == "movetime":
            self.controller.set_move_time_ms(self.play_limit_value_var.get())
        else:
            self.controller.set_search_depth(self.play_limit_value_var.get())
        self._sync_play_limit_widget()

    def _toggle_flip(self) -> None:
        self.flipped_var.set(not self.flipped_var.get())
        self.board_canvas.set_flipped(self.flipped_var.get())

    def _new_game(self) -> None:
        self.controller.new_game()
        self.board_canvas.clear_selection()

    def _reset_position(self) -> None:
        self.controller.reset_position()
        self.board_canvas.clear_selection()

    def _load_fen_dialog(self) -> None:
        fen = simpledialog.askstring(
            "Load FEN",
            "Enter a FEN string:",
            initialvalue=self.controller.current_fen(),
            parent=self.root,
        )
        if fen is None:
            return
        ok, error = self.controller.load_fen(fen)
        if not ok:
            messagebox.showerror("Invalid FEN", error, parent=self.root)
            return
        self.board_canvas.clear_selection()

    def _copy_fen(self) -> None:
        fen = self.controller.current_fen()
        self.root.clipboard_clear()
        self.root.clipboard_append(fen)
        self.controller.status_text = "Copied current FEN to the clipboard."

    def ask_promotion(self) -> int | None:
        result: dict[str, int | None] = {"value": None}
        dialog = tk.Toplevel(self.root)
        dialog.title("Choose Promotion")
        dialog.configure(background=PANEL_BG)
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="Promote to:", style="InfoHeader.TLabel").pack(padx=14, pady=(14, 10))
        row = ttk.Frame(dialog, style="Panel.TFrame", padding=(8, 0, 8, 12))
        row.pack()
        options = [
            ("Queen", chess.QUEEN, "♕"),
            ("Rook", chess.ROOK, "♖"),
            ("Bishop", chess.BISHOP, "♗"),
            ("Knight", chess.KNIGHT, "♘"),
        ]
        for label, piece_type, glyph in options:
            button = tk.Button(
                row,
                text=f"{glyph}\n{label}",
                width=8,
                font=("Segoe UI Symbol", 14),
                command=lambda value=piece_type: self._choose_promotion(dialog, result, value),
            )
            button.pack(side="left", padx=4)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        self.root.wait_window(dialog)
        return result["value"]

    def _choose_promotion(self, dialog: tk.Toplevel, result: dict[str, int | None], value: int) -> None:
        result["value"] = value
        dialog.destroy()

    def _rebuild_settings(self) -> None:
        for child in self.settings_inner.winfo_children():
            child.destroy()
        self.option_vars.clear()

        gui_row = ttk.Frame(self.settings_inner, style="Panel.TFrame", padding=(0, 4, 0, 10))
        gui_row.pack(fill="x")
        ttk.Label(gui_row, text="GUI Behavior", style="Title.TLabel").pack(anchor="w")
        ttk.Checkbutton(
            gui_row,
            text="Think On Opponent Turn",
            variable=self.think_on_opponent_turn_var,
            command=self._toggle_think_on_opponent_turn,
        ).pack(anchor="w", pady=(6, 0))
        ttk.Separator(self.settings_inner, orient="horizontal").pack(fill="x", pady=(0, 10))

        if not self.controller.engine_options:
            ttk.Label(
                self.settings_inner,
                text="Load an engine to view its advertised UCI options.",
                style="Title.TLabel",
            ).pack(anchor="w", pady=(4, 0))
            self.rendered_settings_version = self.controller.settings_version
            return

        for option in self.controller.engine_options.values():
            row = ttk.Frame(self.settings_inner, style="Panel.TFrame", padding=(0, 6))
            row.pack(fill="x")
            ttk.Label(row, text=option.name, style="Title.TLabel").pack(anchor="w")
            if option.kind == "button":
                ttk.Button(
                    row,
                    text=option.name,
                    command=lambda name=option.name: self.controller.press_button_option(name),
                ).pack(anchor="w", pady=(4, 0))
                continue

            current_value = self.controller.draft_option_values.get(option.name, option_default_value(option))
            widget_frame = ttk.Frame(row, style="Panel.TFrame")
            widget_frame.pack(fill="x", pady=(4, 0))
            variable: tk.Variable

            if option.kind == "check":
                variable = tk.BooleanVar(value=bool(current_value))
                ttk.Checkbutton(widget_frame, variable=variable).pack(side="left")
            elif option.kind == "spin":
                variable = tk.StringVar(value=str(current_value))
                ttk.Spinbox(
                    widget_frame,
                    from_=option.minimum if option.minimum is not None else -999999,
                    to=option.maximum if option.maximum is not None else 999999,
                    textvariable=variable,
                    width=10,
                ).pack(side="left")
                ttk.Label(
                    widget_frame,
                    text=f"{option.minimum if option.minimum is not None else '-inf'} to {option.maximum if option.maximum is not None else '+inf'}",
                    style="Title.TLabel",
                ).pack(side="left", padx=(8, 0))
            elif option.kind == "combo":
                variable = tk.StringVar(value=str(current_value))
                ttk.Combobox(widget_frame, textvariable=variable, values=option.vars, state="readonly").pack(
                    side="left",
                    fill="x",
                    expand=True,
                )
            else:
                variable = tk.StringVar(value=str(current_value))
                ttk.Entry(widget_frame, textvariable=variable).pack(side="left", fill="x", expand=True)
                picker = self._picker_callback(option)
                if picker is not None:
                    ttk.Button(widget_frame, text="Browse", command=picker).pack(side="left", padx=(8, 0))
            self.option_vars[option.name] = variable

        ttk.Separator(self.settings_inner, orient="horizontal").pack(fill="x", pady=10)
        ttk.Button(self.settings_inner, text="Apply Settings", style="Accent.TButton", command=self._apply_settings).pack(
            anchor="e"
        )
        self.rendered_settings_version = self.controller.settings_version

    def _picker_callback(self, option: UciOption):
        name = option.name.casefold()
        variable_name = option.name

        def choose_file() -> None:
            filename = filedialog.askopenfilename(title=f"Select {option.name}")
            if filename:
                var = self.option_vars.get(variable_name)
                if isinstance(var, tk.StringVar):
                    var.set(filename)

        def choose_dir() -> None:
            directory = filedialog.askdirectory(title=f"Select {option.name}")
            if directory:
                var = self.option_vars.get(variable_name)
                if isinstance(var, tk.StringVar):
                    var.set(directory)

        if "file" in name:
            return choose_file
        if "path" in name or "dir" in name:
            return choose_dir
        return None

    def _apply_settings(self) -> None:
        payload = {name: variable.get() for name, variable in self.option_vars.items()}
        self.controller.apply_option_drafts(payload)

    def _set_readonly_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _focused_widget(self) -> tk.Misc | None:
        try:
            return self.root.focus_get()
        except (KeyError, tk.TclError):
            return None

    def _turn_text(self) -> str:
        side = "White" if self.controller.board.turn == chess.WHITE else "Black"
        if self.controller.search_kind == "play":
            return f"{side} to move (engine thinking)"
        if self.controller.search_kind == "ponder":
            return f"{side} to move (background thinking)"
        if self.controller.play_mode and self.controller.board.turn != self.controller.human_color:
            return f"{side} to move (waiting for engine)"
        if not self.controller.play_mode and self.controller.board.turn != self.controller.human_color:
            return f"{side} to move (engine replies paused)"
        return f"{side} to move"

    def _refresh_ui(self) -> None:
        focus_widget = self._focused_widget()
        self.status_var.set(self.controller.status_text)
        engine_name = self.controller.engine_identity.name or (
            self.controller.engine_path.name if self.controller.engine_path else "None"
        )
        engine_author = self.controller.engine_identity.author
        self.engine_summary_var.set(f"{engine_name} by {engine_author}" if engine_author else engine_name)
        self.turn_var.set(self._turn_text())
        self.analysis_depth_var.set(str(self.controller.analysis.depth or "-"))
        self.analysis_score_var.set(self.controller.analysis.score_text or "-")
        self.analysis_nodes_var.set(f"{self.controller.analysis.nodes:,}" if self.controller.analysis.nodes else "-")
        self.analysis_nps_var.set(f"{self.controller.analysis.nps:,}" if self.controller.analysis.nps else "-")
        self.analysis_time_var.set(f"{self.controller.analysis.time_ms} ms" if self.controller.analysis.time_ms else "-")
        self.analysis_best_var.set(self.controller.analysis.best_move or "-")
        self.fen_var.set(self.controller.current_fen())
        self.play_mode_var.set(self.controller.play_mode)
        self.think_on_opponent_turn_var.set(self.controller.think_on_opponent_turn)
        self.analysis_enabled_var.set(self.controller.analysis_enabled)
        self.play_limit_label_var.set(self._play_limit_label())
        if focus_widget is not getattr(self, "play_limit_mode_combo", None):
            self.play_limit_mode_var.set(self.controller.play_search_mode)
        if focus_widget is not getattr(self, "play_limit_spinbox", None):
            self.play_limit_value_var.set(self._play_limit_value())

        moves_text = self.controller.move_history_text()
        if moves_text != self.last_moves_text:
            self.last_moves_text = moves_text
            self._set_readonly_text(self.moves_text, moves_text)

        pv_text = self.controller.analysis.pv or "No principal variation yet."
        if pv_text != self.last_pv_text:
            self.last_pv_text = pv_text
            self._set_readonly_text(self.pv_text, pv_text)

        log_text = "\n".join(self.controller.logs)
        if log_text != self.last_log_text:
            self.last_log_text = log_text
            self._set_readonly_text(self.log_text, log_text)

        if self.controller.settings_version != self.rendered_settings_version:
            self._rebuild_settings()
        self.board_canvas.sync_redraw()

    def _tick(self) -> None:
        self.controller.poll()
        self._refresh_ui()
        self.root.after(50, self._tick)

    def run(self) -> int:
        self.root.mainloop()
        return 0

    def _on_close(self) -> None:
        self.controller.shutdown()
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tkinter UCI GUI for DeadFish and other UCI engines.")
    parser.add_argument("--engine", type=Path, default=None, help="Path to a UCI engine executable.")
    args = parser.parse_args(argv)
    engine_path = args.engine.resolve() if args.engine is not None else discover_default_engine()
    app = GuiApp(engine_path)
    return app.run()
