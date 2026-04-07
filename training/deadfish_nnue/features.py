from __future__ import annotations

from dataclasses import dataclass

try:
    import chess
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "python-chess is required for the DeadFish NNUE training tools. "
        "Install training/requirements.txt first."
    ) from exc


_PIECE_BUCKET = {
    chess.PAWN: 0,
    chess.KNIGHT: 1,
    chess.BISHOP: 2,
    chess.ROOK: 3,
    chess.QUEEN: 4,
}

_FEATURES_PER_SQUARE = 64
_BUCKETS_PER_PERSPECTIVE = 10
_FEATURES_PER_KING = _BUCKETS_PER_PERSPECTIVE * _FEATURES_PER_SQUARE
HALFKP_FEATURE_COUNT = 64 * _FEATURES_PER_KING


@dataclass(frozen=True, slots=True)
class EncodedPosition:
    white_indices: list[int]
    black_indices: list[int]
    stm_is_white: bool


def orient_square(square: int, perspective: chess.Color) -> int:
    return square if perspective == chess.WHITE else chess.square_mirror(square)


def piece_bucket(piece: chess.Piece, perspective: chess.Color) -> int:
    if piece.piece_type == chess.KING:
        raise ValueError("Kings are not encoded as HalfKP piece features.")
    color_offset = 0 if piece.color == perspective else 5
    return color_offset + _PIECE_BUCKET[piece.piece_type]


def halfkp_indices(board: chess.Board, perspective: chess.Color) -> list[int]:
    king_square = board.king(perspective)
    if king_square is None:
        raise ValueError("HalfKP encoding requires both kings to be present.")

    king_index = orient_square(king_square, perspective) * _FEATURES_PER_KING
    indices: list[int] = []
    for square, piece in board.piece_map().items():
        if piece.piece_type == chess.KING:
            continue
        bucket = piece_bucket(piece, perspective)
        indices.append(king_index + bucket * _FEATURES_PER_SQUARE + orient_square(square, perspective))
    indices.sort()
    return indices


def encode_board(board: chess.Board) -> EncodedPosition:
    return EncodedPosition(
        white_indices=halfkp_indices(board, chess.WHITE),
        black_indices=halfkp_indices(board, chess.BLACK),
        stm_is_white=board.turn == chess.WHITE,
    )


def encode_fen(fen: str) -> EncodedPosition:
    return encode_board(chess.Board(fen))
