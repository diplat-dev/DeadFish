#pragma once

#include <array>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace deadfish {

using Bitboard = std::uint64_t;

enum class Color : std::uint8_t {
    White = 0,
    Black = 1,
};

enum class PieceType : std::uint8_t {
    None = 0,
    Pawn,
    Knight,
    Bishop,
    Rook,
    Queen,
    King,
};

enum class Piece : std::uint8_t {
    None = 0,
    WPawn,
    WKnight,
    WBishop,
    WRook,
    WQueen,
    WKing,
    BPawn,
    BKnight,
    BBishop,
    BRook,
    BQueen,
    BKing,
};

enum class MoveFlag : std::uint8_t {
    Quiet = 0,
    Capture,
    DoublePawnPush,
    KingCastle,
    QueenCastle,
    EnPassant,
    Promotion,
    PromotionCapture,
};

struct Move {
    std::uint8_t from = 0;
    std::uint8_t to = 0;
    MoveFlag flag = MoveFlag::Quiet;
    PieceType promotion = PieceType::None;

    static Move null();
    bool is_null() const;
    bool is_capture() const;
    bool is_promotion() const;
    std::string to_uci() const;

    friend bool operator==(const Move& lhs, const Move& rhs);
    friend bool operator!=(const Move& lhs, const Move& rhs);
};

struct SearchLimits {
    int max_depth = 5;
    int time_limit_ms = 0;
    std::uint64_t max_nodes = 0;
    int white_time_ms = 0;
    int black_time_ms = 0;
    int white_increment_ms = 0;
    int black_increment_ms = 0;
    int moves_to_go = 0;
    bool infinite = false;
};

struct SearchInfo {
    int depth = 0;
    int score = 0;
    std::uint64_t nodes = 0;
    std::uint64_t nps = 0;
    int elapsed_ms = 0;
    std::vector<Move> pv;
};

struct EngineOptions {
    int hash_mb = 32;
    int threads = 1;
    bool use_nnue = false;
    std::string eval_file;
    bool own_book = true;
    std::string book_path;
    std::string syzygy_path;
    int syzygy_probe_limit = 6;
    int move_overhead_ms = 20;
};

struct SearchResult {
    Move best_move = Move::null();
    int score = 0;
    int depth_reached = 0;
    std::uint64_t nodes = 0;
    std::uint64_t nps = 0;
    int elapsed_ms = 0;
    std::vector<Move> pv;
    bool completed = false;
    bool timed_out = false;
    bool used_book = false;
    bool used_tablebase = false;
};

struct UndoState {
    Move move = Move::null();
    Piece moved_piece = Piece::None;
    Piece captured_piece = Piece::None;
    Color side_to_move = Color::White;
    int captured_square = -1;
    std::uint8_t castling_rights = 0;
    int en_passant_square = -1;
    int halfmove_clock = 0;
    int fullmove_number = 1;
    std::uint64_t hash = 0;
    std::size_t repetition_size = 0;
    bool was_null_move = false;
};

class Position {
public:
    Position();

    static Position start_position();
    static Position from_fen(const std::string& fen, std::string* error = nullptr);

    std::string to_fen() const;
    std::string pretty() const;

    Color side_to_move() const;
    std::uint8_t castling_rights() const;
    int en_passant_square() const;
    int halfmove_clock() const;
    int fullmove_number() const;
    std::uint64_t hash() const;

    const std::array<Piece, 64>& board() const;
    Piece piece_at(int square) const;

    std::vector<Move> legal_moves(bool captures_only = false) const;
    std::vector<Move> legal_moves_fast(bool captures_only = false);
    std::vector<std::string> legal_moves_uci(bool captures_only = false) const;

    Move parse_uci_move(const std::string& uci) const;
    bool is_move_legal(const Move& move) const;
    bool apply_uci_move(const std::string& uci, std::string* error = nullptr);
    bool make_move(const Move& move, UndoState& undo);
    bool make_null_move(UndoState& undo);
    void unmake_move(const UndoState& undo);

    bool is_square_attacked(int square, Color by_color) const;
    bool in_check(Color color) const;
    bool is_checkmate() const;
    bool is_stalemate() const;
    bool is_draw() const;
    bool is_draw_by_repetition() const;
    bool is_draw_by_fifty_move() const;
    bool is_insufficient_material() const;

    int evaluate_absolute() const;
    int evaluate_relative() const;
    int evaluate_backbone_absolute() const;
    int evaluate_backbone_relative() const;
    int evaluate_positional_absolute() const;
    int evaluate_positional_relative() const;
    Bitboard occupancy() const;
    Bitboard occupancy(Color color) const;
    Bitboard piece_bitboard(Piece piece) const;
    int piece_count() const;
    bool has_non_pawn_material(Color color) const;

private:
    struct RawInitTag {};

    std::array<Piece, 64> board_{};
    std::array<Bitboard, 13> piece_bitboards_{};
    std::array<Bitboard, 2> color_bitboards_{};
    Color side_to_move_ = Color::White;
    std::uint8_t castling_rights_ = 0;
    int en_passant_square_ = -1;
    int halfmove_clock_ = 0;
    int fullmove_number_ = 1;
    std::uint64_t hash_ = 0;
    std::vector<std::uint64_t> repetition_history_{};

    explicit Position(RawInitTag);
    void clear();
    void place_piece(int square, Piece piece, bool update_hash = false);
    void remove_piece(int square, bool update_hash = false);
    void move_piece(int from, int to, bool update_hash = false);
    void refresh_color_bitboards();
    std::uint64_t compute_hash() const;
    void xor_castling_hash(std::uint8_t castling_rights);
    void xor_en_passant_hash(int en_passant_square);
    void xor_side_hash();
    int king_square(Color color) const;
    std::vector<Move> generate_pseudo_moves(bool captures_only) const;
};

using SearchCallback = std::function<void(const SearchInfo&)>;

struct EngineState;

class Engine {
public:
    Engine();
    ~Engine();
    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;
    Engine(Engine&&) noexcept;
    Engine& operator=(Engine&&) noexcept;

    const EngineOptions& options() const;
    void set_options(const EngineOptions& options);
    void reset_search_state();
    void request_stop();
    void clear_stop_request();
    bool nnue_loaded() const;
    std::string nnue_status() const;
    int evaluate(const Position& position) const;
    int evaluate_classical(const Position& position) const;
    int evaluate_backbone(const Position& position) const;
    int evaluate_nnue_residual(const Position& position) const;

    SearchResult search(const Position& root, const SearchLimits& limits, SearchCallback callback = {});
    std::uint64_t perft(const Position& root, int depth);
    std::vector<std::pair<Move, std::uint64_t>> divide(const Position& root, int depth);

    static std::vector<std::string> benchmark_positions();

private:
    std::unique_ptr<EngineState> state_;
};

std::string score_to_string(int score);
std::string join_moves(const std::vector<Move>& moves, const std::string& delimiter = " ");
int static_exchange_eval(const Position& position, const Move& move);

}  // namespace deadfish
