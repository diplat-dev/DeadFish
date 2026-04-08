from __future__ import annotations

try:
    import chess
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "python-chess is required for the DeadFish NNUE training tools. "
        "Install training/requirements.txt first."
    ) from exc


_PIECE_VALUES_MG = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

_PIECE_VALUES_EG = {
    chess.PAWN: 110,
    chess.KNIGHT: 310,
    chess.BISHOP: 340,
    chess.ROOK: 510,
    chess.QUEEN: 900,
    chess.KING: 0,
}

_PHASE_WEIGHTS = {
    chess.PAWN: 0,
    chess.KNIGHT: 1,
    chess.BISHOP: 1,
    chess.ROOK: 2,
    chess.QUEEN: 4,
    chess.KING: 0,
}


def phase_value(board: chess.Board) -> int:
    phase = 0
    for piece in board.piece_map().values():
        phase += _PHASE_WEIGHTS[piece.piece_type]
    return min(24, phase)


def _side_material(board: chess.Board, color: chess.Color) -> tuple[int, int, int]:
    material = 0
    middle = 0
    endgame = 0
    for piece in board.piece_map().values():
        if piece.color != color:
            continue
        material += _PIECE_VALUES_MG[piece.piece_type]
        middle += _PIECE_VALUES_MG[piece.piece_type]
        endgame += _PIECE_VALUES_EG[piece.piece_type]
    return material, middle, endgame


def evaluate_backbone_absolute(board: chess.Board) -> int:
    white_material, white_middle, white_endgame = _side_material(board, chess.WHITE)
    black_material, black_middle, black_endgame = _side_material(board, chess.BLACK)

    phase = phase_value(board)
    white_tapered = (white_middle * phase + white_endgame * (24 - phase)) // 24
    black_tapered = (black_middle * phase + black_endgame * (24 - phase)) // 24

    white_simplification = 0
    black_simplification = 0
    if phase <= 10:
        white_edge = white_material - black_material
        black_edge = black_material - white_material
        if white_edge > 0:
            white_simplification += white_edge // 20
        if black_edge > 0:
            black_simplification += black_edge // 20

    score = white_tapered - black_tapered
    score += white_simplification - black_simplification
    score += 10 if board.turn == chess.WHITE else -10
    return score


def evaluate_backbone_relative(board: chess.Board) -> int:
    absolute = evaluate_backbone_absolute(board)
    return absolute if board.turn == chess.WHITE else -absolute


def evaluate_backbone_fen(fen: str) -> int:
    return evaluate_backbone_relative(chess.Board(fen))
