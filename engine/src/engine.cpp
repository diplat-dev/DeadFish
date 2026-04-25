#include "deadfish/engine.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <initializer_list>
#include <limits>
#include <mutex>
#include <optional>
#include <random>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#ifdef DEADFISH_WITH_SYZYGY
extern "C" {
#include "tbprobe.h"
}
#endif

namespace deadfish {

namespace {

constexpr int kNoSquare = -1;
constexpr int kMateScore = 100000;
constexpr int kInfinity = 200000;
constexpr int kMaxPly = 128;
constexpr std::uint8_t kCastleWhiteKing = 1u << 0;
constexpr std::uint8_t kCastleWhiteQueen = 1u << 1;
constexpr std::uint8_t kCastleBlackKing = 1u << 2;
constexpr std::uint8_t kCastleBlackQueen = 1u << 3;
constexpr std::string_view kCanonicalStartFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

#include "deadfish/polyglot_randoms.inc"

constexpr Color opposite(Color color) {
    return color == Color::White ? Color::Black : Color::White;
}

constexpr bool is_white(Piece piece) {
    return piece >= Piece::WPawn && piece <= Piece::WKing;
}

constexpr bool is_black(Piece piece) {
    return piece >= Piece::BPawn && piece <= Piece::BKing;
}

constexpr Color piece_color(Piece piece) {
    return is_black(piece) ? Color::Black : Color::White;
}

constexpr PieceType piece_type(Piece piece) {
    switch (piece) {
        case Piece::WPawn:
        case Piece::BPawn:
            return PieceType::Pawn;
        case Piece::WKnight:
        case Piece::BKnight:
            return PieceType::Knight;
        case Piece::WBishop:
        case Piece::BBishop:
            return PieceType::Bishop;
        case Piece::WRook:
        case Piece::BRook:
            return PieceType::Rook;
        case Piece::WQueen:
        case Piece::BQueen:
            return PieceType::Queen;
        case Piece::WKing:
        case Piece::BKing:
            return PieceType::King;
        case Piece::None:
        default:
            return PieceType::None;
    }
}

constexpr Piece make_piece(Color color, PieceType type) {
    if (type == PieceType::None) {
        return Piece::None;
    }
    const int offset = color == Color::White ? 0 : 6;
    return static_cast<Piece>(static_cast<int>(type) + offset);
}

constexpr int square_file(int square) {
    return square & 7;
}

constexpr int square_rank(int square) {
    return square >> 3;
}

constexpr int make_square(int file, int rank) {
    return rank * 8 + file;
}

constexpr int mirror_square(int square) {
    return square ^ 56;
}

constexpr Bitboard bit_at(int square) {
    return 1ULL << square;
}

char piece_to_fen(Piece piece) {
    switch (piece) {
        case Piece::WPawn:
            return 'P';
        case Piece::WKnight:
            return 'N';
        case Piece::WBishop:
            return 'B';
        case Piece::WRook:
            return 'R';
        case Piece::WQueen:
            return 'Q';
        case Piece::WKing:
            return 'K';
        case Piece::BPawn:
            return 'p';
        case Piece::BKnight:
            return 'n';
        case Piece::BBishop:
            return 'b';
        case Piece::BRook:
            return 'r';
        case Piece::BQueen:
            return 'q';
        case Piece::BKing:
            return 'k';
        case Piece::None:
        default:
            return ' ';
    }
}

Piece fen_to_piece(char ch) {
    switch (ch) {
        case 'P':
            return Piece::WPawn;
        case 'N':
            return Piece::WKnight;
        case 'B':
            return Piece::WBishop;
        case 'R':
            return Piece::WRook;
        case 'Q':
            return Piece::WQueen;
        case 'K':
            return Piece::WKing;
        case 'p':
            return Piece::BPawn;
        case 'n':
            return Piece::BKnight;
        case 'b':
            return Piece::BBishop;
        case 'r':
            return Piece::BRook;
        case 'q':
            return Piece::BQueen;
        case 'k':
            return Piece::BKing;
        default:
            return Piece::None;
    }
}

char promotion_to_char(PieceType type) {
    switch (type) {
        case PieceType::Knight:
            return 'n';
        case PieceType::Bishop:
            return 'b';
        case PieceType::Rook:
            return 'r';
        case PieceType::Queen:
            return 'q';
        default:
            return '\0';
    }
}

std::string square_to_string(int square) {
    if (square < 0 || square >= 64) {
        return "-";
    }
    std::string out(2, ' ');
    out[0] = static_cast<char>('a' + square_file(square));
    out[1] = static_cast<char>('1' + square_rank(square));
    return out;
}

int string_to_square(std::string_view text) {
    if (text.size() != 2) {
        return kNoSquare;
    }
    const char file = static_cast<char>(std::tolower(static_cast<unsigned char>(text[0])));
    const char rank = text[1];
    if (file < 'a' || file > 'h' || rank < '1' || rank > '8') {
        return kNoSquare;
    }
    return make_square(file - 'a', rank - '1');
}

int pop_lsb(Bitboard& bb) {
    const int square = std::countr_zero(bb);
    bb &= bb - 1;
    return square;
}

constexpr std::array<int, 7> kPieceValues = {0, 100, 320, 330, 500, 900, 0};
constexpr std::array<int, 7> kPieceValuesEndgame = {0, 110, 310, 340, 510, 900, 0};
constexpr std::array<int, 7> kPiecePhaseWeights = {0, 0, 1, 1, 2, 4, 0};
constexpr std::array<int, 8> kPassedPawnBonus = {0, 10, 16, 28, 44, 66, 92, 0};

constexpr std::array<int, 64> kPawnTable = {
    0, 0, 0, 0, 0, 0, 0, 0,
    5, 10, 10, -20, -20, 10, 10, 5,
    5, -5, -10, 0, 0, -10, -5, 5,
    0, 0, 0, 20, 20, 0, 0, 0,
    5, 5, 10, 25, 25, 10, 5, 5,
    10, 10, 20, 30, 30, 20, 10, 10,
    50, 50, 50, 50, 50, 50, 50, 50,
    0, 0, 0, 0, 0, 0, 0, 0,
};

constexpr std::array<int, 64> kKnightTable = {
    -50, -40, -30, -30, -30, -30, -40, -50,
    -40, -20, 0, 5, 5, 0, -20, -40,
    -30, 5, 10, 15, 15, 10, 5, -30,
    -30, 0, 15, 20, 20, 15, 0, -30,
    -30, 5, 15, 20, 20, 15, 5, -30,
    -30, 0, 10, 15, 15, 10, 0, -30,
    -40, -20, 0, 0, 0, 0, -20, -40,
    -50, -40, -30, -30, -30, -30, -40, -50,
};

constexpr std::array<int, 64> kBishopTable = {
    -20, -10, -10, -10, -10, -10, -10, -20,
    -10, 5, 0, 0, 0, 0, 5, -10,
    -10, 10, 10, 10, 10, 10, 10, -10,
    -10, 0, 10, 10, 10, 10, 0, -10,
    -10, 5, 5, 10, 10, 5, 5, -10,
    -10, 0, 5, 10, 10, 5, 0, -10,
    -10, 0, 0, 0, 0, 0, 0, -10,
    -20, -10, -10, -10, -10, -10, -10, -20,
};

constexpr std::array<int, 64> kRookTable = {
    0, 0, 0, 5, 5, 0, 0, 0,
    -5, 0, 0, 0, 0, 0, 0, -5,
    -5, 0, 0, 0, 0, 0, 0, -5,
    -5, 0, 0, 0, 0, 0, 0, -5,
    -5, 0, 0, 0, 0, 0, 0, -5,
    -5, 0, 0, 0, 0, 0, 0, -5,
    5, 10, 10, 10, 10, 10, 10, 5,
    0, 0, 0, 0, 0, 0, 0, 0,
};

constexpr std::array<int, 64> kQueenTable = {
    -20, -10, -10, -5, -5, -10, -10, -20,
    -10, 0, 5, 0, 0, 0, 0, -10,
    -10, 5, 5, 5, 5, 5, 0, -10,
    0, 0, 5, 5, 5, 5, 0, -5,
    -5, 0, 5, 5, 5, 5, 0, -5,
    -10, 0, 5, 5, 5, 5, 0, -10,
    -10, 0, 0, 0, 0, 0, 0, -10,
    -20, -10, -10, -5, -5, -10, -10, -20,
};

constexpr std::array<int, 64> kKingMiddleTable = {
    20, 30, 10, 0, 0, 10, 30, 20,
    20, 20, 0, 0, 0, 0, 20, 20,
    -10, -20, -20, -20, -20, -20, -20, -10,
    -20, -30, -30, -40, -40, -30, -30, -20,
    -30, -40, -40, -50, -50, -40, -40, -30,
    -30, -40, -40, -50, -50, -40, -40, -30,
    -30, -40, -40, -50, -50, -40, -40, -30,
    -30, -40, -40, -50, -50, -40, -40, -30,
};

constexpr std::array<int, 64> kKingEndTable = {
    -20, -10, -4, 0, 0, -4, -10, -20,
    -10, 0, 8, 12, 12, 8, 0, -10,
    -4, 8, 18, 22, 22, 18, 8, -4,
    0, 12, 22, 28, 28, 22, 12, 0,
    0, 12, 22, 28, 28, 22, 12, 0,
    -4, 8, 18, 22, 22, 18, 8, -4,
    -10, 0, 8, 12, 12, 8, 0, -10,
    -20, -10, -4, 0, 0, -4, -10, -20,
};

std::array<Bitboard, 64> make_knight_attacks() {
    std::array<Bitboard, 64> table{};
    constexpr std::array<std::pair<int, int>, 8> jumps = {{
        {-2, -1}, {-2, 1}, {-1, -2}, {-1, 2},
        {1, -2}, {1, 2}, {2, -1}, {2, 1},
    }};
    for (int square = 0; square < 64; ++square) {
        Bitboard attacks = 0;
        const int file = square_file(square);
        const int rank = square_rank(square);
        for (const auto& [df, dr] : jumps) {
            const int next_file = file + df;
            const int next_rank = rank + dr;
            if (next_file >= 0 && next_file < 8 && next_rank >= 0 && next_rank < 8) {
                attacks |= bit_at(make_square(next_file, next_rank));
            }
        }
        table[square] = attacks;
    }
    return table;
}

std::array<Bitboard, 64> make_king_attacks() {
    std::array<Bitboard, 64> table{};
    for (int square = 0; square < 64; ++square) {
        Bitboard attacks = 0;
        const int file = square_file(square);
        const int rank = square_rank(square);
        for (int df = -1; df <= 1; ++df) {
            for (int dr = -1; dr <= 1; ++dr) {
                if (df == 0 && dr == 0) {
                    continue;
                }
                const int next_file = file + df;
                const int next_rank = rank + dr;
                if (next_file >= 0 && next_file < 8 && next_rank >= 0 && next_rank < 8) {
                    attacks |= bit_at(make_square(next_file, next_rank));
                }
            }
        }
        table[square] = attacks;
    }
    return table;
}

std::array<std::array<Bitboard, 64>, 2> make_pawn_attacks() {
    std::array<std::array<Bitboard, 64>, 2> table{};
    for (int square = 0; square < 64; ++square) {
        Bitboard white_attacks = 0;
        Bitboard black_attacks = 0;
        const int file = square_file(square);
        const int rank = square_rank(square);
        if (rank < 7) {
            if (file > 0) {
                white_attacks |= bit_at(make_square(file - 1, rank + 1));
            }
            if (file < 7) {
                white_attacks |= bit_at(make_square(file + 1, rank + 1));
            }
        }
        if (rank > 0) {
            if (file > 0) {
                black_attacks |= bit_at(make_square(file - 1, rank - 1));
            }
            if (file < 7) {
                black_attacks |= bit_at(make_square(file + 1, rank - 1));
            }
        }
        table[static_cast<std::size_t>(Color::White)][square] = white_attacks;
        table[static_cast<std::size_t>(Color::Black)][square] = black_attacks;
    }
    return table;
}

const auto kKnightAttacks = make_knight_attacks();
const auto kKingAttacks = make_king_attacks();
const auto kPawnAttacks = make_pawn_attacks();

struct ZobristTables {
    std::array<std::array<std::uint64_t, 64>, 13> pieces{};
    std::array<std::uint64_t, 16> castling{};
    std::array<std::uint64_t, 8> en_passant{};
    std::uint64_t side = 0;
};

ZobristTables make_zobrist_tables() {
    ZobristTables tables{};
    std::mt19937_64 rng(0xD34DF15ULL ^ 0x20260405ULL);
    for (auto& piece_row : tables.pieces) {
        for (auto& cell : piece_row) {
            cell = rng();
        }
    }
    for (auto& entry : tables.castling) {
        entry = rng();
    }
    for (auto& entry : tables.en_passant) {
        entry = rng();
    }
    tables.side = rng();
    return tables;
}

const ZobristTables kZobrist = make_zobrist_tables();

bool is_center_square(int square) {
    const int file = square_file(square);
    const int rank = square_rank(square);
    return (file == 3 || file == 4) && (rank == 3 || rank == 4);
}

bool is_extended_center_square(int square) {
    const int file = square_file(square);
    const int rank = square_rank(square);
    return file >= 2 && file <= 5 && rank >= 2 && rank <= 5;
}

int square_table_value(const std::array<int, 64>& table, int square, Color color) {
    return color == Color::White ? table[square] : table[mirror_square(square)];
}

}  // namespace

}  // namespace deadfish

namespace deadfish {

namespace {

constexpr int kHistoryMax = 32768;
constexpr int kDefaultAspirationWindow = 24;
constexpr int kWinningSeeBonus = 130000;
constexpr int kLosingCaptureBase = 45000;
constexpr int kSeeUnknown = std::numeric_limits<int>::min();
constexpr int kNoTtEval = std::numeric_limits<std::int16_t>::min();
constexpr std::size_t kMaxMoveListSize = 256;

template <typename T, std::size_t Capacity>
class FixedList {
public:
    using value_type = T;
    using iterator = typename std::array<T, Capacity>::iterator;
    using const_iterator = typename std::array<T, Capacity>::const_iterator;

    void clear() {
        size_ = 0;
    }

    void reserve(std::size_t) {}

    bool push_back(const T& value) {
        if (size_ >= Capacity) {
            return false;
        }
        items_[size_++] = value;
        return true;
    }

    bool empty() const {
        return size_ == 0;
    }

    std::size_t size() const {
        return size_;
    }

    T& operator[](std::size_t index) {
        return items_[index];
    }

    const T& operator[](std::size_t index) const {
        return items_[index];
    }

    iterator begin() {
        return items_.begin();
    }

    iterator end() {
        return items_.begin() + static_cast<std::ptrdiff_t>(size_);
    }

    const_iterator begin() const {
        return items_.begin();
    }

    const_iterator end() const {
        return items_.begin() + static_cast<std::ptrdiff_t>(size_);
    }

private:
    std::array<T, Capacity> items_{};
    std::size_t size_ = 0;
};

using MoveList = FixedList<Move, kMaxMoveListSize>;

constexpr std::array<std::pair<int, int>, 8> kRayDeltas = {{
    {0, 1}, {0, -1}, {1, 0}, {-1, 0},
    {1, 1}, {-1, 1}, {1, -1}, {-1, -1},
}};
constexpr std::array<bool, 8> kRayIncreasing = {{
    true, false, true, false, true, true, false, false,
}};

std::array<std::array<Bitboard, 8>, 64> make_ray_masks() {
    std::array<std::array<Bitboard, 8>, 64> masks{};
    for (int square = 0; square < 64; ++square) {
        const int file = square_file(square);
        const int rank = square_rank(square);
        for (std::size_t direction = 0; direction < kRayDeltas.size(); ++direction) {
            const auto [df, dr] = kRayDeltas[direction];
            int next_file = file + df;
            int next_rank = rank + dr;
            Bitboard mask = 0;
            while (next_file >= 0 && next_file < 8 && next_rank >= 0 && next_rank < 8) {
                mask |= bit_at(make_square(next_file, next_rank));
                next_file += df;
                next_rank += dr;
            }
            masks[static_cast<std::size_t>(square)][direction] = mask;
        }
    }
    return masks;
}

const auto kRayMasks = make_ray_masks();

int most_significant_square(Bitboard bb) {
    return 63 - std::countl_zero(bb);
}

Bitboard ray_attacks(int square, Bitboard occupied, std::size_t direction) {
    Bitboard attacks = kRayMasks[static_cast<std::size_t>(square)][direction];
    const Bitboard blockers = attacks & occupied;
    if (blockers == 0) {
        return attacks;
    }
    const int blocker = kRayIncreasing[direction] ? std::countr_zero(blockers) : most_significant_square(blockers);
    attacks &= ~kRayMasks[static_cast<std::size_t>(blocker)][direction];
    return attacks;
}

Bitboard bishop_attacks(int square, Bitboard occupied) {
    return ray_attacks(square, occupied, 4) |
           ray_attacks(square, occupied, 5) |
           ray_attacks(square, occupied, 6) |
           ray_attacks(square, occupied, 7);
}

Bitboard rook_attacks(int square, Bitboard occupied) {
    return ray_attacks(square, occupied, 0) |
           ray_attacks(square, occupied, 1) |
           ray_attacks(square, occupied, 2) |
           ray_attacks(square, occupied, 3);
}

Bitboard queen_attacks(int square, Bitboard occupied) {
    return bishop_attacks(square, occupied) | rook_attacks(square, occupied);
}

void push_move(MoveList& moves, int from, int to, MoveFlag flag, PieceType promotion = PieceType::None) {
    moves.push_back(Move{
        .from = static_cast<std::uint8_t>(from),
        .to = static_cast<std::uint8_t>(to),
        .flag = flag,
        .promotion = promotion,
    });
}

void add_promotions(MoveList& moves, int from, int to, bool capture) {
    const MoveFlag flag = capture ? MoveFlag::PromotionCapture : MoveFlag::Promotion;
    push_move(moves, from, to, flag, PieceType::Queen);
    push_move(moves, from, to, flag, PieceType::Rook);
    push_move(moves, from, to, flag, PieceType::Bishop);
    push_move(moves, from, to, flag, PieceType::Knight);
}

void append_piece_targets(MoveList& moves, int from, Bitboard attacks, Bitboard enemy, bool captures_only) {
    if (captures_only) {
        attacks &= enemy;
    }
    while (attacks) {
        const int to = pop_lsb(attacks);
        push_move(moves, from, to, (enemy & bit_at(to)) != 0 ? MoveFlag::Capture : MoveFlag::Quiet);
    }
}

void generate_pseudo_moves_fast(const Position& position, MoveList& moves, bool captures_only) {
    moves.clear();
    const Color us = position.side_to_move();
    const Color them = opposite(us);
    const Bitboard own = position.occupancy(us);
    const Bitboard enemy = position.occupancy(them);
    const Bitboard occupied = own | enemy;
    const Piece own_pawn = make_piece(us, PieceType::Pawn);
    Bitboard pawns = position.piece_bitboard(own_pawn);
    const int direction = us == Color::White ? 8 : -8;
    const int start_rank = us == Color::White ? 1 : 6;
    const int promotion_rank = us == Color::White ? 6 : 1;
    const int ep_square = position.en_passant_square();

    while (pawns) {
        const int from = pop_lsb(pawns);
        const int rank = square_rank(from);
        if (!captures_only) {
            const int to = from + direction;
            if (to >= 0 && to < 64 && position.piece_at(to) == Piece::None) {
                if (rank == promotion_rank) {
                    add_promotions(moves, from, to, false);
                } else {
                    push_move(moves, from, to, MoveFlag::Quiet);
                    const int jump = from + 2 * direction;
                    if (rank == start_rank && jump >= 0 && jump < 64 && position.piece_at(jump) == Piece::None) {
                        push_move(moves, from, jump, MoveFlag::DoublePawnPush);
                    }
                }
            }
        }

        Bitboard pawn_targets = kPawnAttacks[static_cast<std::size_t>(us)][static_cast<std::size_t>(from)] & enemy;
        while (pawn_targets) {
            const int to = pop_lsb(pawn_targets);
            if (rank == promotion_rank) {
                add_promotions(moves, from, to, true);
            } else {
                push_move(moves, from, to, MoveFlag::Capture);
            }
        }
        if (ep_square != kNoSquare &&
            (kPawnAttacks[static_cast<std::size_t>(us)][static_cast<std::size_t>(from)] & bit_at(ep_square)) != 0) {
            push_move(moves, from, ep_square, MoveFlag::EnPassant);
        }
    }

    Bitboard knights = position.piece_bitboard(make_piece(us, PieceType::Knight));
    while (knights) {
        const int from = pop_lsb(knights);
        append_piece_targets(moves, from, kKnightAttacks[static_cast<std::size_t>(from)] & ~own, enemy, captures_only);
    }

    Bitboard bishops = position.piece_bitboard(make_piece(us, PieceType::Bishop));
    while (bishops) {
        const int from = pop_lsb(bishops);
        append_piece_targets(moves, from, bishop_attacks(from, occupied) & ~own, enemy, captures_only);
    }

    Bitboard rooks = position.piece_bitboard(make_piece(us, PieceType::Rook));
    while (rooks) {
        const int from = pop_lsb(rooks);
        append_piece_targets(moves, from, rook_attacks(from, occupied) & ~own, enemy, captures_only);
    }

    Bitboard queens = position.piece_bitboard(make_piece(us, PieceType::Queen));
    while (queens) {
        const int from = pop_lsb(queens);
        append_piece_targets(moves, from, queen_attacks(from, occupied) & ~own, enemy, captures_only);
    }

    Bitboard kings = position.piece_bitboard(make_piece(us, PieceType::King));
    if (kings) {
        const int from = std::countr_zero(kings);
        append_piece_targets(moves, from, kKingAttacks[static_cast<std::size_t>(from)] & ~own, enemy, captures_only);
    }

    if (captures_only || position.in_check(us)) {
        return;
    }

    const std::uint8_t rights = position.castling_rights();
    if (us == Color::White) {
        if ((rights & kCastleWhiteKing) != 0 &&
            position.piece_at(4) == Piece::WKing && position.piece_at(7) == Piece::WRook &&
            position.piece_at(5) == Piece::None && position.piece_at(6) == Piece::None &&
            !position.is_square_attacked(5, them) && !position.is_square_attacked(6, them)) {
            push_move(moves, 4, 6, MoveFlag::KingCastle);
        }
        if ((rights & kCastleWhiteQueen) != 0 &&
            position.piece_at(4) == Piece::WKing && position.piece_at(0) == Piece::WRook &&
            position.piece_at(3) == Piece::None && position.piece_at(2) == Piece::None && position.piece_at(1) == Piece::None &&
            !position.is_square_attacked(3, them) && !position.is_square_attacked(2, them)) {
            push_move(moves, 4, 2, MoveFlag::QueenCastle);
        }
    } else {
        if ((rights & kCastleBlackKing) != 0 &&
            position.piece_at(60) == Piece::BKing && position.piece_at(63) == Piece::BRook &&
            position.piece_at(61) == Piece::None && position.piece_at(62) == Piece::None &&
            !position.is_square_attacked(61, them) && !position.is_square_attacked(62, them)) {
            push_move(moves, 60, 62, MoveFlag::KingCastle);
        }
        if ((rights & kCastleBlackQueen) != 0 &&
            position.piece_at(60) == Piece::BKing && position.piece_at(56) == Piece::BRook &&
            position.piece_at(59) == Piece::None && position.piece_at(58) == Piece::None && position.piece_at(57) == Piece::None &&
            !position.is_square_attacked(59, them) && !position.is_square_attacked(58, them)) {
            push_move(moves, 60, 58, MoveFlag::QueenCastle);
        }
    }
}

MoveList generate_legal_moves_fast(Position& position, bool captures_only) {
    const Color us = position.side_to_move();
    MoveList pseudo;
    MoveList legal;
    generate_pseudo_moves_fast(position, pseudo, captures_only);
    for (const Move& move : pseudo) {
        UndoState undo;
        if (!position.make_move(move, undo)) {
            continue;
        }
        if (!position.in_check(us)) {
            legal.push_back(move);
        }
        position.unmake_move(undo);
    }
    return legal;
}

enum class TTFlag : std::uint8_t {
    Exact = 0,
    Lower,
    Upper,
};

struct TTEntry {
    int depth = 0;
    int score = 0;
    TTFlag flag = TTFlag::Exact;
    Move best_move = Move::null();
    int static_eval = 0;
    bool has_static_eval = false;
};

constexpr int kTtDepthBits = 16;
constexpr int kTtScoreBits = 20;
constexpr int kTtFlagBits = 2;
constexpr int kTtAgeBits = 8;
constexpr int kTtMoveBits = 18;

constexpr std::uint64_t kTtDepthMask = (1ULL << kTtDepthBits) - 1;
constexpr std::uint64_t kTtScoreMask = (1ULL << kTtScoreBits) - 1;
constexpr std::uint64_t kTtFlagMask = (1ULL << kTtFlagBits) - 1;
constexpr std::uint64_t kTtAgeMask = (1ULL << kTtAgeBits) - 1;
constexpr std::uint64_t kTtMoveMask = (1ULL << kTtMoveBits) - 1;

constexpr int kTtScoreShift = kTtDepthBits;
constexpr int kTtFlagShift = kTtScoreShift + kTtScoreBits;
constexpr int kTtAgeShift = kTtFlagShift + kTtFlagBits;
constexpr int kTtMoveShift = kTtAgeShift + kTtAgeBits;

int score_to_tt(int score, int ply);
int score_from_tt(int score, int ply);

std::uint32_t encode_tt_move(const Move& move) {
    return static_cast<std::uint32_t>(move.from)
        | (static_cast<std::uint32_t>(move.to) << 6)
        | (static_cast<std::uint32_t>(move.flag) << 12)
        | (static_cast<std::uint32_t>(move.promotion) << 15);
}

Move decode_tt_move(std::uint32_t value) {
    Move move;
    move.from = static_cast<std::uint8_t>(value & 0x3Fu);
    move.to = static_cast<std::uint8_t>((value >> 6) & 0x3Fu);
    move.flag = static_cast<MoveFlag>((value >> 12) & 0x7u);
    move.promotion = static_cast<PieceType>((value >> 15) & 0x7u);
    return move;
}

std::uint64_t encode_tt_signed(int value, int bits) {
    const std::int64_t mask = (1LL << bits) - 1;
    return static_cast<std::uint64_t>(static_cast<std::int64_t>(value) & mask);
}

int decode_tt_signed(std::uint64_t value, int bits) {
    const std::int64_t sign = 1LL << (bits - 1);
    std::int64_t decoded = static_cast<std::int64_t>(value);
    if ((decoded & sign) != 0) {
        decoded -= (1LL << bits);
    }
    return static_cast<int>(decoded);
}

std::uint64_t pack_tt_slot(int depth, int score, TTFlag flag, Move best_move, std::uint8_t age, int ply) {
    const std::uint64_t depth_bits = static_cast<std::uint64_t>(std::clamp(depth + 1, 1, static_cast<int>(kTtDepthMask)));
    const std::uint64_t score_bits = encode_tt_signed(score_to_tt(score, ply), kTtScoreBits);
    const std::uint64_t flag_bits = static_cast<std::uint64_t>(flag) & kTtFlagMask;
    const std::uint64_t age_bits = static_cast<std::uint64_t>(age) & kTtAgeMask;
    const std::uint64_t move_bits = static_cast<std::uint64_t>(encode_tt_move(best_move)) & kTtMoveMask;
    return depth_bits
        | (score_bits << kTtScoreShift)
        | (flag_bits << kTtFlagShift)
        | (age_bits << kTtAgeShift)
        | (move_bits << kTtMoveShift);
}

struct DecodedTTSlot {
    bool occupied = false;
    std::uint64_t key = 0;
    int depth = std::numeric_limits<int>::min();
    int score = 0;
    TTFlag flag = TTFlag::Exact;
    Move best_move = Move::null();
    std::uint8_t age = 0;
};

DecodedTTSlot decode_tt_slot(const std::atomic<std::uint64_t>& key_atomic, const std::atomic<std::uint64_t>& packed_atomic, int ply) {
    for (int attempt = 0; attempt < 3; ++attempt) {
        const std::uint64_t packed_before = packed_atomic.load(std::memory_order_acquire);
        const std::uint64_t key = key_atomic.load(std::memory_order_acquire);
        const std::uint64_t packed_after = packed_atomic.load(std::memory_order_acquire);
        const std::uint64_t key_verify = key_atomic.load(std::memory_order_acquire);
        if (packed_before != packed_after || key != key_verify) {
            continue;
        }
        if (packed_after == 0) {
            return {};
        }
        const int depth = static_cast<int>(packed_after & kTtDepthMask) - 1;
        return DecodedTTSlot{
            .occupied = true,
            .key = key_verify,
            .depth = depth,
            .score = score_from_tt(decode_tt_signed((packed_after >> kTtScoreShift) & kTtScoreMask, kTtScoreBits), ply),
            .flag = static_cast<TTFlag>((packed_after >> kTtFlagShift) & kTtFlagMask),
            .best_move = decode_tt_move(static_cast<std::uint32_t>((packed_after >> kTtMoveShift) & kTtMoveMask)),
            .age = static_cast<std::uint8_t>((packed_after >> kTtAgeShift) & kTtAgeMask),
        };
    }
    return {};
}

struct TTSlot {
    std::atomic<std::uint64_t> key{0};
    std::atomic<std::uint64_t> packed{0};
    std::atomic<std::int16_t> static_eval{static_cast<std::int16_t>(kNoTtEval)};
};

constexpr std::size_t kTTClusterSize = 4;

struct TTCluster {
    std::array<TTSlot, kTTClusterSize> slots{};
};

int score_to_tt(int score, int ply) {
    if (score > kMateScore - kMaxPly) {
        return score + ply;
    }
    if (score < -kMateScore + kMaxPly) {
        return score - ply;
    }
    return score;
}

int score_from_tt(int score, int ply) {
    if (score > kMateScore - kMaxPly) {
        return score - ply;
    }
    if (score < -kMateScore + kMaxPly) {
        return score + ply;
    }
    return score;
}

class FixedHashTable {
public:
    void resize_mb(int hash_mb) {
        const std::uint64_t bytes = static_cast<std::uint64_t>(std::max(1, hash_mb)) * 1024ULL * 1024ULL;
        std::size_t count = static_cast<std::size_t>(bytes / sizeof(TTCluster));
        count = std::max<std::size_t>(1, std::bit_floor(count));
        clusters_ = std::make_unique<TTCluster[]>(count);
        cluster_count_ = count;
        mask_ = count - 1;
        age_.store(0, std::memory_order_relaxed);
        clear();
    }

    void clear() {
        if (!clusters_) {
            return;
        }
        for (std::size_t index = 0; index < cluster_count_; ++index) {
            for (TTSlot& slot : clusters_[index].slots) {
                slot.packed.store(0, std::memory_order_relaxed);
                slot.key.store(0, std::memory_order_relaxed);
                slot.static_eval.store(static_cast<std::int16_t>(kNoTtEval), std::memory_order_relaxed);
            }
        }
    }

    void new_search() {
        age_.fetch_add(1, std::memory_order_relaxed);
    }

    std::optional<TTEntry> probe(std::uint64_t key, int ply) const {
        if (!clusters_) {
            return std::nullopt;
        }
        const TTCluster& cluster = clusters_[static_cast<std::size_t>(key) & mask_];
        for (const TTSlot& slot : cluster.slots) {
            const DecodedTTSlot decoded = decode_tt_slot(slot.key, slot.packed, ply);
            if (!decoded.occupied || decoded.key != key) {
                continue;
            }
            const int stored_eval = slot.static_eval.load(std::memory_order_relaxed);
            return TTEntry{
                .depth = decoded.depth,
                .score = decoded.score,
                .flag = decoded.flag,
                .best_move = decoded.best_move,
                .static_eval = stored_eval,
                .has_static_eval = stored_eval != kNoTtEval,
            };
        }
        return std::nullopt;
    }

    void store(std::uint64_t key, int depth, int score, TTFlag flag, Move best_move, int ply, int static_eval = kNoTtEval) {
        if (!clusters_) {
            return;
        }
        TTCluster& cluster = clusters_[static_cast<std::size_t>(key) & mask_];
        TTSlot* replacement = &cluster.slots.front();
        int replacement_value = std::numeric_limits<int>::max();
        for (TTSlot& slot : cluster.slots) {
            const DecodedTTSlot decoded = decode_tt_slot(slot.key, slot.packed, ply);
            if (decoded.occupied && decoded.key == key) {
                replacement = &slot;
                replacement_value = std::numeric_limits<int>::min();
                break;
            }
            if (!decoded.occupied) {
                replacement = &slot;
                replacement_value = std::numeric_limits<int>::min();
                break;
            }
            const int age_penalty = decoded.age == age_.load(std::memory_order_relaxed) ? 0 : 64;
            const int value = decoded.depth - age_penalty;
            if (value < replacement_value) {
                replacement = &slot;
                replacement_value = value;
            }
        }
        const int clamped_eval = static_eval == kNoTtEval
            ? kNoTtEval
            : std::clamp(static_eval, static_cast<int>(std::numeric_limits<std::int16_t>::min()) + 1,
                         static_cast<int>(std::numeric_limits<std::int16_t>::max()));
        replacement->static_eval.store(static_cast<std::int16_t>(clamped_eval), std::memory_order_relaxed);
        replacement->packed.store(
            pack_tt_slot(depth, score, flag, best_move, age_.load(std::memory_order_relaxed), ply),
            std::memory_order_relaxed);
        replacement->key.store(key, std::memory_order_release);
    }

private:
    std::unique_ptr<TTCluster[]> clusters_{};
    std::size_t cluster_count_ = 0;
    std::size_t mask_ = 0;
    std::atomic<std::uint8_t> age_{0};
};

struct EvalCacheSlot {
    std::atomic<std::uint64_t> key{0};
    std::atomic<int> value{0};
};

class FixedEvalCache {
public:
    void resize(std::size_t entries) {
        entries = std::max<std::size_t>(1, std::bit_floor(entries));
        slots_ = std::make_unique<EvalCacheSlot[]>(entries);
        size_ = entries;
        mask_ = entries - 1;
        clear();
    }

    void clear() {
        if (!slots_) {
            return;
        }
        for (std::size_t index = 0; index < size_; ++index) {
            slots_[index].key.store(0, std::memory_order_relaxed);
            slots_[index].value.store(0, std::memory_order_relaxed);
        }
    }

    bool probe(std::uint64_t key, int& value) const {
        if (!slots_ || key == 0) {
            return false;
        }
        const EvalCacheSlot& slot = slots_[static_cast<std::size_t>(key) & mask_];
        if (slot.key.load(std::memory_order_acquire) != key) {
            return false;
        }
        value = slot.value.load(std::memory_order_relaxed);
        return true;
    }

    void store(std::uint64_t key, int value) {
        if (!slots_ || key == 0) {
            return;
        }
        EvalCacheSlot& slot = slots_[static_cast<std::size_t>(key) & mask_];
        slot.value.store(value, std::memory_order_relaxed);
        slot.key.store(key, std::memory_order_release);
    }

private:
    std::unique_ptr<EvalCacheSlot[]> slots_{};
    std::size_t size_ = 0;
    std::size_t mask_ = 0;
};

struct PolyglotEntry {
    std::uint64_t key = 0;
    std::uint16_t move = 0;
    std::uint16_t weight = 0;
    std::uint32_t learn = 0;
};

struct PolyglotBookCache {
    std::filesystem::path loaded_path{};
    std::vector<PolyglotEntry> entries{};
};

struct TablebaseRootResult {
    Move move = Move::null();
    int score = 0;
    bool ok = false;
};

constexpr int kNnueFeatureCount = 64 * 10 * 64;
constexpr std::array<char, 8> kNnueMagic = {'D', 'F', 'N', 'N', 'U', 'E', '1', '\0'};

struct NnueNetwork {
    int feature_count = 0;
    int accumulator_size = 0;
    int hidden_size = 0;
    float output_scale = 1.0f;
    std::vector<float> feature_weights{};
    std::vector<float> acc_bias{};
    std::vector<float> hidden_weight{};
    std::vector<float> hidden_bias{};
    std::vector<float> output_weight{};
    float output_bias = 0.0f;
};

struct NnueLoadResult {
    std::shared_ptr<const NnueNetwork> network{};
    std::string status{};
    bool loaded = false;
};

struct AccumulatorFrame {
    std::vector<float> white{};
    std::vector<float> black{};
    bool initialized = false;
};

struct SearchSharedState;
void shutdown_worker_pool(EngineState& state);
void resize_worker_pool(EngineState& state, int thread_count);

}  // namespace

struct EngineState {
    ~EngineState();

    EngineOptions options{};
    std::atomic<bool> stop_requested = false;
    FixedHashTable tt{};
    FixedEvalCache classical_eval_cache{};
    PolyglotBookCache book{};
    std::shared_ptr<const NnueNetwork> nnue{};
    std::filesystem::path nnue_loaded_path{};
    std::string nnue_status = "NNUE unavailable; using classical eval.";
    std::mutex worker_mutex{};
    std::condition_variable worker_cv{};
    bool workers_shutdown = false;
    std::uint64_t active_search_generation = 0;
    std::shared_ptr<SearchSharedState> active_search{};
    std::vector<std::thread> worker_threads{};
#ifdef DEADFISH_WITH_SYZYGY
    std::mutex syzygy_mutex{};
    std::string syzygy_loaded_path{};
    bool syzygy_initialized = false;
#endif
};

namespace {

std::filesystem::path normalize_path(const std::filesystem::path& path) {
    std::error_code error;
    const std::filesystem::path normalized = std::filesystem::weakly_canonical(path, error);
    return error ? path.lexically_normal() : normalized;
}

std::filesystem::path resolve_book_path(const EngineOptions& options) {
    auto resolve_if_exists = [](const std::filesystem::path& path) -> std::filesystem::path {
        std::error_code error;
        if (!path.empty() && std::filesystem::exists(path, error)) {
            return normalize_path(path);
        }
        return {};
    };

    if (!options.book_path.empty()) {
        return resolve_if_exists(options.book_path);
    }

    for (const std::filesystem::path& candidate : {
             std::filesystem::path("data/book.bin"),
             std::filesystem::path("../data/book.bin"),
         }) {
        const std::filesystem::path resolved = resolve_if_exists(candidate);
        if (!resolved.empty()) {
            return resolved;
        }
    }
    return {};
}

void clear_book_cache(EngineState& state) {
    state.book.loaded_path.clear();
    state.book.entries.clear();
}

std::uint64_t read_be_u64(const unsigned char* data) {
    std::uint64_t value = 0;
    for (int index = 0; index < 8; ++index) {
        value = (value << 8) | static_cast<std::uint64_t>(data[index]);
    }
    return value;
}

std::uint16_t read_be_u16(const unsigned char* data) {
    return static_cast<std::uint16_t>((static_cast<unsigned>(data[0]) << 8) | static_cast<unsigned>(data[1]));
}

std::uint32_t read_be_u32(const unsigned char* data) {
    std::uint32_t value = 0;
    for (int index = 0; index < 4; ++index) {
        value = (value << 8) | static_cast<std::uint32_t>(data[index]);
    }
    return value;
}

bool ensure_book_loaded(EngineState& state) {
    const std::filesystem::path path = resolve_book_path(state.options);
    if (path.empty()) {
        clear_book_cache(state);
        return false;
    }
    if (!state.book.loaded_path.empty() && normalize_path(state.book.loaded_path) == path) {
        return !state.book.entries.empty();
    }

    clear_book_cache(state);
    state.book.loaded_path = path;
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return false;
    }

    std::array<unsigned char, 16> buffer{};
    while (input.read(reinterpret_cast<char*>(buffer.data()), static_cast<std::streamsize>(buffer.size()))) {
        state.book.entries.push_back(PolyglotEntry{
            .key = read_be_u64(buffer.data()),
            .move = read_be_u16(buffer.data() + 8),
            .weight = read_be_u16(buffer.data() + 10),
            .learn = read_be_u32(buffer.data() + 12),
        });
    }
    return !state.book.entries.empty();
}

int polyglot_piece_index(Piece piece) {
    switch (piece) {
        case Piece::BPawn:
            return 0;
        case Piece::WPawn:
            return 1;
        case Piece::BKnight:
            return 2;
        case Piece::WKnight:
            return 3;
        case Piece::BBishop:
            return 4;
        case Piece::WBishop:
            return 5;
        case Piece::BRook:
            return 6;
        case Piece::WRook:
            return 7;
        case Piece::BQueen:
            return 8;
        case Piece::WQueen:
            return 9;
        case Piece::BKing:
            return 10;
        case Piece::WKing:
            return 11;
        case Piece::None:
        default:
            return -1;
    }
}

bool has_polyglot_ep_capture(const Position& position) {
    const int ep = position.en_passant_square();
    if (ep == kNoSquare) {
        return false;
    }
    const int file = square_file(ep);
    if (position.side_to_move() == Color::White) {
        if (file > 0 && position.piece_at(ep - 9) == Piece::WPawn) {
            return true;
        }
        if (file < 7 && position.piece_at(ep - 7) == Piece::WPawn) {
            return true;
        }
    } else {
        if (file > 0 && position.piece_at(ep + 7) == Piece::BPawn) {
            return true;
        }
        if (file < 7 && position.piece_at(ep + 9) == Piece::BPawn) {
            return true;
        }
    }
    return false;
}

std::uint64_t polyglot_hash(const Position& position) {
    std::uint64_t hash = 0;
    for (int square = 0; square < 64; ++square) {
        const Piece piece = position.piece_at(square);
        const int index = polyglot_piece_index(piece);
        if (index >= 0) {
            hash ^= kPolyglotRandom[64 * index + square];
        }
    }
    if ((position.castling_rights() & kCastleWhiteKing) != 0) {
        hash ^= kPolyglotRandom[768];
    }
    if ((position.castling_rights() & kCastleWhiteQueen) != 0) {
        hash ^= kPolyglotRandom[769];
    }
    if ((position.castling_rights() & kCastleBlackKing) != 0) {
        hash ^= kPolyglotRandom[770];
    }
    if ((position.castling_rights() & kCastleBlackQueen) != 0) {
        hash ^= kPolyglotRandom[771];
    }
    if (has_polyglot_ep_capture(position) && position.en_passant_square() != kNoSquare) {
        hash ^= kPolyglotRandom[772 + square_file(position.en_passant_square())];
    }
    if (position.side_to_move() == Color::White) {
        hash ^= kPolyglotRandom[780];
    }
    return hash;
}

Move decode_polyglot_move(const Position& position, std::uint16_t raw_move) {
    const int from = (raw_move >> 6) & 0x3f;
    const int to = raw_move & 0x3f;
    PieceType promotion = PieceType::None;
    switch ((raw_move >> 12) & 0x7) {
        case 1:
            promotion = PieceType::Knight;
            break;
        case 2:
            promotion = PieceType::Bishop;
            break;
        case 3:
            promotion = PieceType::Rook;
            break;
        case 4:
            promotion = PieceType::Queen;
            break;
        default:
            break;
    }

    for (const Move& move : position.legal_moves(false)) {
        if (move.from == from && move.to == to && move.promotion == promotion) {
            return move;
        }
    }

    for (const Move& move : position.legal_moves(false)) {
        if (move.from != from || move.promotion != promotion) {
            continue;
        }
        if (move.flag == MoveFlag::KingCastle && (to == 7 || to == 63 || to == 6 || to == 62)) {
            return move;
        }
        if (move.flag == MoveFlag::QueenCastle && (to == 0 || to == 56 || to == 2 || to == 58)) {
            return move;
        }
    }
    return Move::null();
}

Move probe_book_move(const Position& position, EngineState& state) {
    if (!state.options.own_book || !ensure_book_loaded(state)) {
        return Move::null();
    }

    const std::uint64_t key = polyglot_hash(position);
    auto lower = std::lower_bound(state.book.entries.begin(), state.book.entries.end(), key,
        [](const PolyglotEntry& entry, std::uint64_t value) {
            return entry.key < value;
        });

    Move best_move = Move::null();
    std::uint16_t best_weight = 0;
    for (auto it = lower; it != state.book.entries.end() && it->key == key; ++it) {
        const Move candidate = decode_polyglot_move(position, it->move);
        if (!candidate.is_null() && it->weight >= best_weight) {
            best_weight = it->weight;
            best_move = candidate;
        }
    }
    return best_move;
}

std::uint32_t read_le_u32(const unsigned char* data) {
    return static_cast<std::uint32_t>(data[0])
        | (static_cast<std::uint32_t>(data[1]) << 8)
        | (static_cast<std::uint32_t>(data[2]) << 16)
        | (static_cast<std::uint32_t>(data[3]) << 24);
}

float read_le_f32(const unsigned char* data) {
    return std::bit_cast<float>(read_le_u32(data));
}

int position_king_square(const Position& position, Color color) {
    const Bitboard kings = position.piece_bitboard(make_piece(color, PieceType::King));
    if (kings == 0) {
        return kNoSquare;
    }
    return std::countr_zero(kings);
}

int nnue_piece_bucket(Piece piece, Color perspective) {
    const PieceType type = piece_type(piece);
    if (piece == Piece::None || type == PieceType::King || type == PieceType::None) {
        return -1;
    }
    const int color_offset = piece_color(piece) == perspective ? 0 : 5;
    int piece_offset = 0;
    switch (type) {
        case PieceType::Pawn:
            piece_offset = 0;
            break;
        case PieceType::Knight:
            piece_offset = 1;
            break;
        case PieceType::Bishop:
            piece_offset = 2;
            break;
        case PieceType::Rook:
            piece_offset = 3;
            break;
        case PieceType::Queen:
            piece_offset = 4;
            break;
        case PieceType::King:
        case PieceType::None:
        default:
            return -1;
    }
    return color_offset + piece_offset;
}

int nnue_orient_square(int square, Color perspective) {
    return perspective == Color::White ? square : mirror_square(square);
}

std::optional<int> nnue_feature_index(const Position& position, Color perspective, Piece piece, int square) {
    const int king_square = position_king_square(position, perspective);
    if (king_square == kNoSquare) {
        return std::nullopt;
    }
    const int bucket = nnue_piece_bucket(piece, perspective);
    if (bucket < 0) {
        return std::nullopt;
    }
    const int oriented_king = nnue_orient_square(king_square, perspective);
    const int oriented_square = nnue_orient_square(square, perspective);
    return oriented_king * (10 * 64) + bucket * 64 + oriented_square;
}

void ensure_accumulator_size(AccumulatorFrame& frame, int accumulator_size) {
    if (static_cast<int>(frame.white.size()) != accumulator_size) {
        frame.white.assign(accumulator_size, 0.0f);
    }
    if (static_cast<int>(frame.black.size()) != accumulator_size) {
        frame.black.assign(accumulator_size, 0.0f);
    }
}

void rebuild_accumulator(const Position& position, const NnueNetwork& network, Color perspective, std::vector<float>& accumulator) {
    accumulator = network.acc_bias;
    for (int square = 0; square < 64; ++square) {
        const Piece piece = position.piece_at(square);
        const auto feature = nnue_feature_index(position, perspective, piece, square);
        if (!feature) {
            continue;
        }
        const std::size_t offset = static_cast<std::size_t>(*feature) * static_cast<std::size_t>(network.accumulator_size);
        for (int index = 0; index < network.accumulator_size; ++index) {
            accumulator[static_cast<std::size_t>(index)] += network.feature_weights[offset + static_cast<std::size_t>(index)];
        }
    }
}

bool apply_feature_delta(const Position& position, const NnueNetwork& network, Color perspective,
                         std::vector<float>& accumulator, Piece piece, int square, float sign) {
    const auto feature = nnue_feature_index(position, perspective, piece, square);
    if (!feature) {
        return false;
    }
    const std::size_t offset = static_cast<std::size_t>(*feature) * static_cast<std::size_t>(network.accumulator_size);
    for (int index = 0; index < network.accumulator_size; ++index) {
        accumulator[static_cast<std::size_t>(index)] += sign * network.feature_weights[offset + static_cast<std::size_t>(index)];
    }
    return true;
}

void apply_move_deltas_to_accumulator(const Position& child_position, const NnueNetwork& network, Color perspective,
                                      std::vector<float>& accumulator, const UndoState& undo) {
    const Move& move = undo.move;
    const Piece mover = undo.moved_piece;
    const Color us = undo.side_to_move;

    auto remove_piece_delta = [&](Piece piece, int square) {
        if (piece != Piece::None && square != kNoSquare) {
            apply_feature_delta(child_position, network, perspective, accumulator, piece, square, -1.0f);
        }
    };
    auto add_piece_delta = [&](Piece piece, int square) {
        if (piece != Piece::None && square != kNoSquare) {
            apply_feature_delta(child_position, network, perspective, accumulator, piece, square, 1.0f);
        }
    };

    if (move.flag == MoveFlag::KingCastle) {
        remove_piece_delta(mover, move.from);
        add_piece_delta(mover, move.to);
        if (us == Color::White) {
            remove_piece_delta(Piece::WRook, 7);
            add_piece_delta(Piece::WRook, 5);
        } else {
            remove_piece_delta(Piece::BRook, 63);
            add_piece_delta(Piece::BRook, 61);
        }
    } else if (move.flag == MoveFlag::QueenCastle) {
        remove_piece_delta(mover, move.from);
        add_piece_delta(mover, move.to);
        if (us == Color::White) {
            remove_piece_delta(Piece::WRook, 0);
            add_piece_delta(Piece::WRook, 3);
        } else {
            remove_piece_delta(Piece::BRook, 56);
            add_piece_delta(Piece::BRook, 59);
        }
    } else {
        if (undo.captured_piece != Piece::None) {
            remove_piece_delta(undo.captured_piece, undo.captured_square);
        }
        remove_piece_delta(mover, move.from);
        if (move.is_promotion()) {
            add_piece_delta(make_piece(us, move.promotion), move.to);
        } else {
            add_piece_delta(mover, move.to);
        }
    }
}

bool rebuild_search_frame(AccumulatorFrame& frame, const Position& position, const NnueNetwork& network) {
    ensure_accumulator_size(frame, network.accumulator_size);
    rebuild_accumulator(position, network, Color::White, frame.white);
    rebuild_accumulator(position, network, Color::Black, frame.black);
    frame.initialized = true;
    return true;
}

bool update_search_frame_for_move(AccumulatorFrame& child, const AccumulatorFrame& parent, const Position& child_position,
                                  const NnueNetwork& network, const UndoState& undo) {
    ensure_accumulator_size(child, network.accumulator_size);
    child.white = parent.white;
    child.black = parent.black;
    child.initialized = true;

    const PieceType mover_type = piece_type(undo.moved_piece);
    if (mover_type == PieceType::King) {
        const Color us = undo.side_to_move;
        if (us == Color::White) {
            rebuild_accumulator(child_position, network, Color::White, child.white);
            apply_move_deltas_to_accumulator(child_position, network, Color::Black, child.black, undo);
        } else {
            rebuild_accumulator(child_position, network, Color::Black, child.black);
            apply_move_deltas_to_accumulator(child_position, network, Color::White, child.white, undo);
        }
        return true;
    }

    apply_move_deltas_to_accumulator(child_position, network, Color::White, child.white, undo);
    apply_move_deltas_to_accumulator(child_position, network, Color::Black, child.black, undo);
    return true;
}

int evaluate_with_nnue_accumulators(const Position& position, const NnueNetwork& network, const AccumulatorFrame& frame) {
    const std::vector<float>& first = position.side_to_move() == Color::White ? frame.white : frame.black;
    const std::vector<float>& second = position.side_to_move() == Color::White ? frame.black : frame.white;

    float output = network.output_bias;
    const int input_size = network.accumulator_size * 2;
    for (int hidden = 0; hidden < network.hidden_size; ++hidden) {
        float sum = network.hidden_bias[static_cast<std::size_t>(hidden)];
        const std::size_t weight_offset = static_cast<std::size_t>(hidden) * static_cast<std::size_t>(input_size);
        for (int index = 0; index < network.accumulator_size; ++index) {
            const float first_activated = std::clamp(first[static_cast<std::size_t>(index)], 0.0f, 1.0f);
            const float second_activated = std::clamp(second[static_cast<std::size_t>(index)], 0.0f, 1.0f);
            sum += first_activated * network.hidden_weight[weight_offset + static_cast<std::size_t>(index)];
            sum += second_activated *
                network.hidden_weight[weight_offset + static_cast<std::size_t>(network.accumulator_size + index)];
        }
        const float activated = std::clamp(sum, 0.0f, 1.0f);
        output += activated * network.output_weight[static_cast<std::size_t>(hidden)];
    }
    return static_cast<int>(std::lround(output * network.output_scale));
}

int evaluate_with_nnue_full(const Position& position, const NnueNetwork& network) {
    AccumulatorFrame frame;
    rebuild_search_frame(frame, position, network);
    return evaluate_with_nnue_accumulators(position, network, frame);
}

NnueLoadResult load_nnue_network(const std::filesystem::path& path) {
    NnueLoadResult result;
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        result.status = "NNUE load failed: could not open " + path.string() + "; using classical eval.";
        return result;
    }

    std::array<unsigned char, 20> header{};
    if (!input.read(reinterpret_cast<char*>(header.data()), static_cast<std::streamsize>(header.size()))) {
        result.status = "NNUE load failed: truncated header in " + path.string() + "; using classical eval.";
        return result;
    }
    if (!std::equal(kNnueMagic.begin(), kNnueMagic.end(), header.begin())) {
        result.status = "NNUE load failed: wrong magic in " + path.string() + "; using classical eval.";
        return result;
    }

    auto network = std::make_shared<NnueNetwork>();
    network->feature_count = static_cast<int>(read_le_u32(header.data() + 8));
    network->accumulator_size = static_cast<int>(read_le_u32(header.data() + 12));
    network->hidden_size = static_cast<int>(read_le_u32(header.data() + 16));

    std::array<unsigned char, 4> scale_bytes{};
    if (!input.read(reinterpret_cast<char*>(scale_bytes.data()), static_cast<std::streamsize>(scale_bytes.size()))) {
        result.status = "NNUE load failed: missing output scale in " + path.string() + "; using classical eval.";
        return result;
    }
    network->output_scale = read_le_f32(scale_bytes.data());

    if (network->feature_count != kNnueFeatureCount || network->accumulator_size <= 0 || network->hidden_size <= 0 ||
        !std::isfinite(network->output_scale) || network->output_scale <= 0.0f) {
        result.status = "NNUE load failed: unsupported network dimensions in " + path.string() + "; using classical eval.";
        return result;
    }

    auto read_tensor = [&](std::vector<float>& out, std::size_t count, std::string_view label) -> bool {
        std::vector<unsigned char> buffer(count * sizeof(float));
        if (!input.read(reinterpret_cast<char*>(buffer.data()), static_cast<std::streamsize>(buffer.size()))) {
            result.status = "NNUE load failed: truncated " + std::string(label) + " tensor in " + path.string() +
                "; using classical eval.";
            return false;
        }
        out.resize(count);
        for (std::size_t index = 0; index < count; ++index) {
            out[index] = read_le_f32(buffer.data() + index * sizeof(float));
        }
        return true;
    };

    const std::size_t feature_weights_count =
        static_cast<std::size_t>(network->feature_count) * static_cast<std::size_t>(network->accumulator_size);
    const std::size_t hidden_weight_count =
        static_cast<std::size_t>(network->hidden_size) * static_cast<std::size_t>(network->accumulator_size * 2);

    if (!read_tensor(network->feature_weights, feature_weights_count, "feature_weights") ||
        !read_tensor(network->acc_bias, static_cast<std::size_t>(network->accumulator_size), "acc_bias") ||
        !read_tensor(network->hidden_weight, hidden_weight_count, "hidden_weight") ||
        !read_tensor(network->hidden_bias, static_cast<std::size_t>(network->hidden_size), "hidden_bias") ||
        !read_tensor(network->output_weight, static_cast<std::size_t>(network->hidden_size), "output_weight")) {
        return result;
    }

    std::vector<float> output_bias_values;
    if (!read_tensor(output_bias_values, 1, "output_bias")) {
        return result;
    }
    network->output_bias = output_bias_values.front();

    std::array<char, 1> trailing{};
    if (input.read(trailing.data(), static_cast<std::streamsize>(trailing.size()))) {
        result.status = "NNUE load failed: trailing bytes in " + path.string() + "; using classical eval.";
        return result;
    }

    result.network = std::move(network);
    result.loaded = true;
    result.status = "Loaded NNUE from " + path.string() + ".";
    return result;
}

void refresh_nnue_runtime(EngineState& state) {
    state.nnue.reset();
    state.nnue_loaded_path.clear();

    if (state.options.eval_file.empty()) {
        state.nnue_status = state.options.use_nnue
            ? "NNUE eval file not set; using classical eval."
            : "UseNNUE=false; using classical eval.";
        return;
    }

    const std::filesystem::path requested = normalize_path(state.options.eval_file);
    NnueLoadResult load = load_nnue_network(requested);
    if (!load.loaded) {
        state.nnue_status = load.status;
        return;
    }

    state.nnue = std::move(load.network);
    state.nnue_loaded_path = requested;
    state.nnue_status = state.options.use_nnue
        ? load.status
        : load.status + " NNUE inactive because UseNNUE=false.";
}

std::shared_ptr<const NnueNetwork> active_nnue(const EngineState& state) {
    if (!state.options.use_nnue || !state.nnue) {
        return nullptr;
    }
    return state.nnue;
}

#ifdef DEADFISH_WITH_SYZYGY
struct TbPosition {
    std::uint64_t white = 0;
    std::uint64_t black = 0;
    std::uint64_t kings = 0;
    std::uint64_t queens = 0;
    std::uint64_t rooks = 0;
    std::uint64_t bishops = 0;
    std::uint64_t knights = 0;
    std::uint64_t pawns = 0;
    unsigned rule50 = 0;
    unsigned castling = 0;
    unsigned ep = 0;
    bool turn = false;
};

TbPosition make_tb_position(const Position& position) {
    return TbPosition{
        .white = position.occupancy(Color::White),
        .black = position.occupancy(Color::Black),
        .kings = position.piece_bitboard(Piece::WKing) | position.piece_bitboard(Piece::BKing),
        .queens = position.piece_bitboard(Piece::WQueen) | position.piece_bitboard(Piece::BQueen),
        .rooks = position.piece_bitboard(Piece::WRook) | position.piece_bitboard(Piece::BRook),
        .bishops = position.piece_bitboard(Piece::WBishop) | position.piece_bitboard(Piece::BBishop),
        .knights = position.piece_bitboard(Piece::WKnight) | position.piece_bitboard(Piece::BKnight),
        .pawns = position.piece_bitboard(Piece::WPawn) | position.piece_bitboard(Piece::BPawn),
        .rule50 = static_cast<unsigned>(position.halfmove_clock()),
        .castling = static_cast<unsigned>(position.castling_rights()),
        .ep = position.en_passant_square() == kNoSquare ? 0u : static_cast<unsigned>(position.en_passant_square()),
        .turn = position.side_to_move() == Color::White,
    };
}

bool ensure_syzygy_ready(EngineState& state) {
    std::lock_guard<std::mutex> guard(state.syzygy_mutex);
    if (state.options.syzygy_path.empty()) {
        if (state.syzygy_initialized) {
            tb_free();
            state.syzygy_initialized = false;
            state.syzygy_loaded_path.clear();
        }
        return false;
    }
    if (state.syzygy_initialized && state.syzygy_loaded_path == state.options.syzygy_path) {
        return TB_LARGEST > 0;
    }
    if (state.syzygy_initialized) {
        tb_free();
        state.syzygy_initialized = false;
        state.syzygy_loaded_path.clear();
    }
    const bool ok = tb_init(state.options.syzygy_path.c_str());
    state.syzygy_initialized = ok;
    state.syzygy_loaded_path = ok ? state.options.syzygy_path : std::string();
    return ok && TB_LARGEST > 0;
}

bool can_probe_tablebase(const Position& position, EngineState& state) {
    if (state.options.syzygy_probe_limit <= 0) {
        return false;
    }
    if (!ensure_syzygy_ready(state)) {
        return false;
    }
    const int limit = std::min<int>(state.options.syzygy_probe_limit, static_cast<int>(TB_LARGEST));
    return limit > 0 && position.piece_count() <= limit && position.castling_rights() == 0;
}

int tb_wdl_to_score(unsigned wdl) {
    switch (wdl) {
        case TB_WIN:
            return 20000;
        case TB_CURSED_WIN:
            return 15000;
        case TB_BLESSED_LOSS:
            return -15000;
        case TB_LOSS:
            return -20000;
        case TB_DRAW:
        default:
            return 0;
    }
}

std::optional<int> probe_tablebase_wdl(const Position& position, EngineState& state) {
    if (!can_probe_tablebase(position, state) || position.halfmove_clock() != 0) {
        return std::nullopt;
    }
    const TbPosition tb = make_tb_position(position);
    const unsigned result = tb_probe_wdl(
        tb.white, tb.black, tb.kings, tb.queens, tb.rooks, tb.bishops, tb.knights, tb.pawns,
        tb.rule50, tb.castling, tb.ep, tb.turn);
    if (result == TB_RESULT_FAILED) {
        return std::nullopt;
    }
    return tb_wdl_to_score(result);
}

TablebaseRootResult probe_tablebase_root(const Position& position, EngineState& state) {
    TablebaseRootResult result;
    if (!can_probe_tablebase(position, state)) {
        return result;
    }

    std::lock_guard<std::mutex> guard(state.syzygy_mutex);
    const TbPosition tb = make_tb_position(position);
    unsigned alternatives[TB_MAX_MOVES]{};
    const unsigned probe = tb_probe_root(
        tb.white, tb.black, tb.kings, tb.queens, tb.rooks, tb.bishops, tb.knights, tb.pawns,
        tb.rule50, tb.castling, tb.ep, tb.turn, alternatives);
    if (probe == TB_RESULT_FAILED) {
        return result;
    }

    const int from = static_cast<int>(TB_GET_FROM(probe));
    const int to = static_cast<int>(TB_GET_TO(probe));
    PieceType promotion = PieceType::None;
    switch (TB_GET_PROMOTES(probe)) {
        case TB_PROMOTES_KNIGHT:
            promotion = PieceType::Knight;
            break;
        case TB_PROMOTES_BISHOP:
            promotion = PieceType::Bishop;
            break;
        case TB_PROMOTES_ROOK:
            promotion = PieceType::Rook;
            break;
        case TB_PROMOTES_QUEEN:
            promotion = PieceType::Queen;
            break;
        default:
            break;
    }
    for (const Move& move : position.legal_moves(false)) {
        if (move.from == from && move.to == to && move.promotion == promotion) {
            result.move = move;
            break;
        }
    }
    if (result.move.is_null()) {
        return result;
    }

    const int dtz = static_cast<int>(TB_GET_DTZ(probe));
    const int base = tb_wdl_to_score(TB_GET_WDL(probe));
    result.score = base > 0 ? base - dtz : base < 0 ? base + dtz : 0;
    result.ok = true;
    return result;
}
#endif

struct SearchNodeResult {
    int score = 0;
    Move best_move = Move::null();
    std::vector<Move> pv;
    bool completed = true;
};

SearchNodeResult node_result(int score = 0, bool completed = true, Move best_move = Move::null(), std::vector<Move> pv = {}) {
    SearchNodeResult result;
    result.score = score;
    result.best_move = best_move;
    result.pv = std::move(pv);
    result.completed = completed;
    return result;
}

struct SharedRootResult {
    Move best_move = Move::null();
    int score = 0;
    int depth_reached = 0;
    std::vector<Move> pv{};
    bool completed = false;
    int worker_index = std::numeric_limits<int>::max();
};

struct SearchSharedState {
    Position root = Position::start_position();
    SearchLimits limits{};
    std::chrono::steady_clock::time_point start_time{};
    int soft_time_ms = 0;
    int hard_time_ms = 0;
    std::shared_ptr<const NnueNetwork> nnue{};
    std::atomic<bool> stop = false;
    std::atomic<std::uint64_t> nodes = 0;
    std::atomic<int> helpers_remaining = 0;
    std::mutex result_mutex{};
    SharedRootResult best_completed{};
    std::mutex done_mutex{};
    std::condition_variable done_cv{};
};

struct SearchStackEntry {
    Move current_move = Move::null();
    int static_eval = 0;
    bool has_static_eval = false;
};

struct SearchContext {
    EngineState* state = nullptr;
    SearchSharedState* shared = nullptr;
    SearchLimits limits{};
    SearchCallback callback{};
    std::chrono::steady_clock::time_point start_time{};
    std::uint64_t nodes = 0;
    std::uint64_t pending_nodes = 0;
    bool stop = false;
    int soft_time_ms = 0;
    int hard_time_ms = 0;
    int worker_index = 0;
    int root_move_rotation = 0;
    std::array<std::array<Move, 2>, kMaxPly> killers{};
    std::array<std::array<std::array<int, 64>, 64>, 2> history{};
    std::array<std::array<std::array<int, 64>, 64>, 2> capture_history{};
    std::array<std::array<int, 64>, 64> continuation_history{};
    std::array<std::array<Move, 64>, 64> counter_moves{};
    std::array<SearchStackEntry, kMaxPly + 1> stack{};
    std::shared_ptr<const NnueNetwork> nnue{};
    std::array<AccumulatorFrame, kMaxPly + 1> nnue_frames{};
};

int elapsed_ms(const SearchContext& context) {
    return static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - context.start_time).count());
}

void flush_pending_nodes(SearchContext& context) {
    if (!context.shared || context.pending_nodes == 0) {
        return;
    }
    context.shared->nodes.fetch_add(context.pending_nodes, std::memory_order_relaxed);
    context.pending_nodes = 0;
}

std::uint64_t visible_nodes(const SearchContext& context) {
    if (!context.shared) {
        return context.nodes;
    }
    return context.shared->nodes.load(std::memory_order_relaxed) + context.pending_nodes;
}

void record_node(SearchContext& context, std::uint64_t count = 1) {
    context.nodes += count;
    if (!context.shared) {
        return;
    }
    context.pending_nodes += count;
    if (context.pending_nodes >= 256) {
        const std::uint64_t total =
            context.shared->nodes.fetch_add(context.pending_nodes, std::memory_order_relaxed) + context.pending_nodes;
        context.pending_nodes = 0;
        if (context.limits.max_nodes > 0 && total >= context.limits.max_nodes) {
            context.shared->stop.store(true, std::memory_order_relaxed);
        }
    }
}

bool should_stop(SearchContext& context) {
    if (context.stop
        || (context.shared && context.shared->stop.load(std::memory_order_relaxed))
        || (context.state && context.state->stop_requested.load(std::memory_order_relaxed))) {
        context.stop = true;
        if (context.shared) {
            context.shared->stop.store(true, std::memory_order_relaxed);
        }
        return true;
    }
    if (context.limits.max_nodes > 0 && visible_nodes(context) >= context.limits.max_nodes) {
        context.stop = true;
        if (context.shared) {
            context.shared->stop.store(true, std::memory_order_relaxed);
        }
        return true;
    }
    if (context.hard_time_ms > 0 && (context.nodes & 2047ULL) == 0 && elapsed_ms(context) >= context.hard_time_ms) {
        context.stop = true;
        if (context.shared) {
            context.shared->stop.store(true, std::memory_order_relaxed);
        }
    }
    return context.stop;
}

bool soft_limit_reached(const SearchContext& context) {
    return context.soft_time_ms > 0 && elapsed_ms(context) >= context.soft_time_ms;
}

bool hard_limit_reached(const SearchContext& context) {
    return context.hard_time_ms > 0 && elapsed_ms(context) >= context.hard_time_ms;
}

bool nnue_active(const SearchContext& context) {
    return context.nnue != nullptr;
}

void copy_null_move_frame(AccumulatorFrame& child, const AccumulatorFrame& parent, int accumulator_size) {
    ensure_accumulator_size(child, accumulator_size);
    child.white = parent.white;
    child.black = parent.black;
    child.initialized = parent.initialized;
}

bool ensure_root_nnue_frame(SearchContext& context, const Position& position) {
    if (!nnue_active(context)) {
        return false;
    }
    return rebuild_search_frame(context.nnue_frames[0], position, *context.nnue);
}

int evaluate_position(const Position& position, SearchContext& context, int ply) {
    if (!nnue_active(context) || ply < 0 || ply > kMaxPly) {
        int cached = 0;
        if (context.state && context.state->classical_eval_cache.probe(position.hash(), cached)) {
            return cached;
        }
        const int evaluated = position.evaluate_relative();
        if (context.state) {
            context.state->classical_eval_cache.store(position.hash(), evaluated);
        }
        return evaluated;
    }
    AccumulatorFrame& frame = context.nnue_frames[static_cast<std::size_t>(ply)];
    if (!frame.initialized) {
        if (!rebuild_search_frame(frame, position, *context.nnue)) {
            return position.evaluate_relative();
        }
    }
    return position.evaluate_backbone_relative() + evaluate_with_nnue_accumulators(position, *context.nnue, frame);
}

Piece captured_piece_for_move(const Position& position, const Move& move) {
    if (move.flag == MoveFlag::EnPassant) {
        return make_piece(opposite(position.side_to_move()), PieceType::Pawn);
    }
    return position.piece_at(move.to);
}

int move_history_bonus(int depth) {
    return depth * depth + 4 * depth;
}

int move_history_penalty(int depth) {
    return -(depth * depth + 2 * depth);
}

void update_history_value(int& entry, int delta) {
    entry = std::clamp(entry + delta, -kHistoryMax, kHistoryMax);
}

int piece_value(PieceType type) {
    return kPieceValues[static_cast<std::size_t>(type)];
}

int move_attacker_value(const Position& position, const Move& move) {
    const Piece mover = position.piece_at(move.from);
    if (mover == Piece::None) {
        return 0;
    }
    return piece_value(piece_type(mover));
}

Bitboard attacks_to(const Position& position, int square, Bitboard occ, Color by_color) {
    const Bitboard side_occ = position.occupancy(by_color) & occ;
    Bitboard attackers = kPawnAttacks[static_cast<std::size_t>(opposite(by_color))][static_cast<std::size_t>(square)] &
                         position.piece_bitboard(make_piece(by_color, PieceType::Pawn)) & side_occ;
    attackers |= kKnightAttacks[static_cast<std::size_t>(square)] &
                 position.piece_bitboard(make_piece(by_color, PieceType::Knight)) & side_occ;
    attackers |= kKingAttacks[static_cast<std::size_t>(square)] &
                 position.piece_bitboard(make_piece(by_color, PieceType::King)) & side_occ;
    attackers |= rook_attacks(square, occ) &
                 (position.piece_bitboard(make_piece(by_color, PieceType::Rook)) |
                  position.piece_bitboard(make_piece(by_color, PieceType::Queen))) & side_occ;
    attackers |= bishop_attacks(square, occ) &
                 (position.piece_bitboard(make_piece(by_color, PieceType::Bishop)) |
                  position.piece_bitboard(make_piece(by_color, PieceType::Queen))) & side_occ;
    return attackers;
}

std::optional<std::pair<int, Piece>> least_valuable_attacker(const Position& position, Bitboard attackers, Color color) {
    for (PieceType type : {PieceType::Pawn, PieceType::Knight, PieceType::Bishop, PieceType::Rook, PieceType::Queen, PieceType::King}) {
        Bitboard candidates = attackers;
        while (candidates) {
            const int from = pop_lsb(candidates);
            const Piece piece = position.piece_at(from);
            if (piece != Piece::None && piece_color(piece) == color && piece_type(piece) == type) {
                return std::make_pair(from, piece);
            }
        }
    }
    return std::nullopt;
}

int see_value(const Position& position, const Move& move) {
    const Piece victim = captured_piece_for_move(position, move);
    if (victim == Piece::None && !move.is_promotion()) {
        return 0;
    }

    constexpr int kMaxSeeDepth = 32;
    int gain[kMaxSeeDepth]{};
    Bitboard occ = position.occupancy();
    const Color us = position.side_to_move();
    const Color them = opposite(us);
    const int target = move.to;
    const int capture_square = move.flag == MoveFlag::EnPassant
        ? (us == Color::White ? target - 8 : target + 8)
        : target;

    PieceType attacker_type = move.is_promotion() ? move.promotion : piece_type(position.piece_at(move.from));
    gain[0] = kPieceValues[static_cast<std::size_t>(piece_type(victim))];
    if (move.is_promotion()) {
        gain[0] += kPieceValues[static_cast<std::size_t>(move.promotion)] - kPieceValues[static_cast<std::size_t>(PieceType::Pawn)];
    }

    occ &= ~bit_at(move.from);
    occ &= ~bit_at(capture_square);
    occ |= bit_at(target);

    Color side = them;
    int depth = 0;
    while (depth + 1 < kMaxSeeDepth) {
        const Bitboard attackers = attacks_to(position, target, occ, side) & position.occupancy(side);
        const auto attacker = least_valuable_attacker(position, attackers, side);
        if (!attacker) {
            break;
        }
        ++depth;
        gain[depth] = kPieceValues[static_cast<std::size_t>(attacker_type)] - gain[depth - 1];
        attacker_type = piece_type(attacker->second);
        occ &= ~bit_at(attacker->first);
        side = opposite(side);
    }

    while (depth > 0) {
        gain[depth - 1] = -std::max(-gain[depth - 1], gain[depth]);
        --depth;
    }
    return gain[0];
}

struct OrderedMove {
    Move move = Move::null();
    int score = 0;
    int see = kSeeUnknown;
    bool quiet = true;
};

using OrderedMoveList = FixedList<OrderedMove, kMaxMoveListSize>;

int ensure_see(const Position& position, OrderedMove& ordered) {
    if (ordered.see == kSeeUnknown && ordered.move.is_capture()) {
        ordered.see = see_value(position, ordered.move);
    }
    return ordered.see == kSeeUnknown ? 0 : ordered.see;
}

OrderedMove classify_move(const Position& position, const Move& move, const SearchContext& context, int ply) {
    OrderedMove ordered;
    ordered.move = move;
    ordered.quiet = !move.is_capture() && !move.is_promotion();
    const Color us = position.side_to_move();

    const Piece victim = captured_piece_for_move(position, move);
    if (move.is_capture()) {
        const int victim_value = piece_value(piece_type(victim));
        const int attacker_value = move_attacker_value(position, move);
        if (!move.is_promotion() && victim_value < attacker_value) {
            ordered.see = see_value(position, move);
        }
        const bool likely_good = ordered.see == kSeeUnknown || ordered.see >= 0;
        ordered.score += likely_good ? kWinningSeeBonus : kLosingCaptureBase + ordered.see;
        ordered.score += victim_value * 16 - attacker_value / 2;
        ordered.score += context.capture_history[static_cast<std::size_t>(us)][move.from][move.to] / 2;
    }
    if (move.is_promotion()) {
        ordered.score += 95'000 + piece_value(move.promotion);
    }
    if (ordered.quiet && ply < kMaxPly) {
        if (move == context.killers[ply][0]) {
            ordered.score += 80'000;
        } else if (move == context.killers[ply][1]) {
            ordered.score += 79'000;
        }
        ordered.score += context.history[static_cast<std::size_t>(us)][move.from][move.to];
        ordered.score += context.continuation_history[move.from][move.to] / 2;
        if (ply > 0) {
            const Move previous = context.stack[static_cast<std::size_t>(ply - 1)].current_move;
            if (!previous.is_null() && context.counter_moves[previous.from][previous.to] == move) {
                ordered.score += 22'000;
            }
        }
    }
    if (move.flag == MoveFlag::KingCastle || move.flag == MoveFlag::QueenCastle) {
        ordered.score += 150;
    }
    return ordered;
}

OrderedMove take_best_scored_move(OrderedMoveList& moves, std::size_t& index) {
    std::size_t best = index;
    for (std::size_t current = index + 1; current < moves.size(); ++current) {
        if (moves[current].score > moves[best].score) {
            best = current;
        }
    }
    std::swap(moves[index], moves[best]);
    return moves[index++];
}

class MovePicker {
public:
    MovePicker(const Position& position, const MoveList& moves, const Move& tt_move,
               const SearchContext& context, int ply) {
        good_tacticals_.reserve(moves.size());
        quiets_.reserve(moves.size());
        bad_tacticals_.reserve(moves.size());
        for (const Move& move : moves) {
            if (!tt_move.is_null() && move == tt_move) {
                tt_move_ = move;
                has_tt_move_ = true;
                continue;
            }
            OrderedMove ordered = classify_move(position, move, context, ply);
            if (ordered.quiet) {
                quiets_.push_back(ordered);
            } else if (move.is_promotion() || ordered.see == kSeeUnknown || ordered.see >= 0) {
                good_tacticals_.push_back(ordered);
            } else {
                bad_tacticals_.push_back(ordered);
            }
        }
    }

    bool next(OrderedMove& move) {
        if (has_tt_move_ && !tt_used_) {
            tt_used_ = true;
            move.move = tt_move_;
            move.score = 1'000'000;
            move.see = kSeeUnknown;
            move.quiet = !tt_move_.is_capture() && !tt_move_.is_promotion();
            return true;
        }
        if (good_index_ < good_tacticals_.size()) {
            move = take_best_scored_move(good_tacticals_, good_index_);
            return true;
        }
        if (quiet_index_ < quiets_.size()) {
            move = take_best_scored_move(quiets_, quiet_index_);
            return true;
        }
        if (bad_index_ < bad_tacticals_.size()) {
            move = take_best_scored_move(bad_tacticals_, bad_index_);
            return true;
        }
        return false;
    }

private:
    Move tt_move_ = Move::null();
    bool has_tt_move_ = false;
    bool tt_used_ = false;
    OrderedMoveList good_tacticals_{};
    OrderedMoveList quiets_{};
    OrderedMoveList bad_tacticals_{};
    std::size_t good_index_ = 0;
    std::size_t quiet_index_ = 0;
    std::size_t bad_index_ = 0;
};

void append_legal_quiet_queen_promotions(Position& position, MoveList& moves) {
    const Color us = position.side_to_move();
    const Piece pawn = make_piece(us, PieceType::Pawn);
    const int promotion_from_rank = us == Color::White ? 6 : 1;
    const int direction = us == Color::White ? 8 : -8;
    for (int file = 0; file < 8; ++file) {
        const int from = make_square(file, promotion_from_rank);
        const int to = from + direction;
        if (position.piece_at(from) != pawn || position.piece_at(to) != Piece::None) {
            continue;
        }
        Move move{
            .from = static_cast<std::uint8_t>(from),
            .to = static_cast<std::uint8_t>(to),
            .flag = MoveFlag::Promotion,
            .promotion = PieceType::Queen,
        };
        UndoState undo;
        if (!position.make_move(move, undo)) {
            continue;
        }
        const bool legal = !position.in_check(us);
        position.unmake_move(undo);
        if (legal) {
            moves.push_back(move);
        }
    }
}

Move first_legal_move(const Position& position, const std::vector<Move>& moves) {
    for (const Move& move : moves) {
        if (position.is_move_legal(move)) {
            return move;
        }
    }
    return Move::null();
}

void compute_time_budgets(const Position& root, const SearchLimits& limits, const EngineOptions& options,
                          int& soft_time_ms, int& hard_time_ms) {
    soft_time_ms = 0;
    hard_time_ms = 0;
    if (limits.infinite) {
        return;
    }

    const int overhead = std::max(0, options.move_overhead_ms);
    if (limits.time_limit_ms > 0) {
        const int available = std::max(1, limits.time_limit_ms - overhead);
        soft_time_ms = available;
        hard_time_ms = available;
        return;
    }

    const bool white = root.side_to_move() == Color::White;
    const int clock = white ? limits.white_time_ms : limits.black_time_ms;
    const int increment = white ? limits.white_increment_ms : limits.black_increment_ms;
    if (clock <= 0) {
        return;
    }

    const int available = std::max(1, clock - overhead);
    const int moves_to_go = std::max(1, limits.moves_to_go > 0 ? limits.moves_to_go : 30);
    const int base = available / moves_to_go;
    const int increment_share = increment * 3 / 4;
    soft_time_ms = std::clamp(base + increment_share, 1, available);
    hard_time_ms = std::clamp(std::max(soft_time_ms + 25, soft_time_ms + soft_time_ms / 3), soft_time_ms, available);
}

bool is_zugzwang_prone(const Position& position) {
    return !position.has_non_pawn_material(position.side_to_move()) || position.piece_count() <= 6;
}

void update_quiet_cutoff_stats(SearchContext& context, Color color, const Move& best_move,
                               const MoveList& quiets_tried, int depth, int ply) {
    if (ply >= kMaxPly) {
        return;
    }
    if (context.killers[ply][0] != best_move) {
        context.killers[ply][1] = context.killers[ply][0];
        context.killers[ply][0] = best_move;
    }

    update_history_value(context.history[static_cast<std::size_t>(color)][best_move.from][best_move.to], move_history_bonus(depth));
    update_history_value(context.continuation_history[best_move.from][best_move.to], move_history_bonus(depth) / 2);
    if (ply > 0) {
        const Move previous = context.stack[static_cast<std::size_t>(ply - 1)].current_move;
        if (!previous.is_null()) {
            context.counter_moves[previous.from][previous.to] = best_move;
        }
    }
    for (const Move& quiet : quiets_tried) {
        if (quiet != best_move) {
            update_history_value(context.history[static_cast<std::size_t>(color)][quiet.from][quiet.to], move_history_penalty(depth));
            update_history_value(context.continuation_history[quiet.from][quiet.to], move_history_penalty(depth) / 2);
        }
    }
}

void update_capture_cutoff_stats(SearchContext& context, Color color, const Move& best_move, int depth) {
    update_history_value(
        context.capture_history[static_cast<std::size_t>(color)][best_move.from][best_move.to],
        move_history_bonus(depth));
}

bool prefer_shared_root_result(const SharedRootResult& candidate, const SharedRootResult& current) {
    if (!current.completed) {
        return true;
    }
    if (candidate.depth_reached != current.depth_reached) {
        return candidate.depth_reached > current.depth_reached;
    }
    if (candidate.score != current.score) {
        return candidate.score > current.score;
    }
    if (candidate.worker_index != current.worker_index) {
        return candidate.worker_index < current.worker_index;
    }
    return candidate.pv.size() > current.pv.size();
}

void publish_shared_root_result(SearchContext& context, const SearchNodeResult& node, int depth) {
    if (!context.shared || !node.completed || node.best_move.is_null()) {
        return;
    }
    SharedRootResult candidate;
    candidate.best_move = node.best_move;
    candidate.score = node.score;
    candidate.depth_reached = depth;
    candidate.pv = node.pv;
    candidate.completed = true;
    candidate.worker_index = context.worker_index;

    std::lock_guard<std::mutex> guard(context.shared->result_mutex);
    if (prefer_shared_root_result(candidate, context.shared->best_completed)) {
        context.shared->best_completed = std::move(candidate);
    }
}

std::vector<OrderedMove> build_root_move_order(const Position& position, const MoveList& moves, const Move& tt_move,
                                               const SearchContext& context, int root_rotation) {
    std::vector<OrderedMove> ordered{};
    std::optional<OrderedMove> tt_ordered{};
    ordered.reserve(moves.size());
    for (const Move& move : moves) {
        OrderedMove scored = classify_move(position, move, context, 0);
        if (!tt_move.is_null() && move == tt_move) {
            tt_ordered = scored;
        } else {
            ordered.push_back(scored);
        }
    }
    std::stable_sort(ordered.begin(), ordered.end(), [](const OrderedMove& lhs, const OrderedMove& rhs) {
        return lhs.score > rhs.score;
    });
    if (ordered.size() > 1 && root_rotation > 0) {
        const std::size_t offset = static_cast<std::size_t>(root_rotation) % ordered.size();
        std::rotate(ordered.begin(), ordered.begin() + offset, ordered.end());
    }
    if (tt_ordered) {
        ordered.insert(ordered.begin(), *tt_ordered);
    }
    return ordered;
}

int reverse_futility_margin(int depth) {
    return 70 * depth;
}

int futility_margin(int depth) {
    return 60 + 90 * depth;
}

int razor_margin(int depth) {
    return 180 + 120 * depth;
}

int late_quiet_threshold(int depth) {
    switch (depth) {
        case 1:
            return 4;
        case 2:
            return 6;
        case 3:
            return 10;
        default:
            return 14;
    }
}

int late_move_reduction(int depth, int move_index, bool pv_node, bool quiet, bool gives_check, int history_score, bool improving) {
    if (!quiet || gives_check || depth < 3 || move_index < 3) {
        return 0;
    }
    if (pv_node && move_index < 6) {
        return 0;
    }
    int reduction = 1;
    if (depth >= 5 && move_index >= 6) {
        reduction += 1;
    }
    if (depth >= 8 && move_index >= 12) {
        reduction += 1;
    }
    if (pv_node) {
        reduction -= 1;
    }
    if (!improving && depth >= 4 && move_index >= 4) {
        reduction += 1;
    }
    if (history_score > 8000) {
        reduction -= 1;
    } else if (history_score < -8000 && depth >= 5) {
        reduction += 1;
    }
    return std::clamp(reduction, 0, std::max(0, depth - 2));
}

SearchNodeResult quiescence(Position& position, SearchContext& context, int alpha, int beta, int ply) {
    if (should_stop(context)) {
        return node_result(0, false);
    }
    record_node(context);

    if (position.is_draw_by_repetition() || position.is_draw_by_fifty_move() || position.is_insufficient_material()) {
        return node_result(0, true);
    }
#ifdef DEADFISH_WITH_SYZYGY
    if (const auto tb_score = probe_tablebase_wdl(position, *context.state)) {
        return node_result(*tb_score, true);
    }
#endif

    int stand_pat = evaluate_position(position, context, ply);
    if (stand_pat >= beta) {
        return node_result(beta, true);
    }
    if (stand_pat > alpha) {
        alpha = stand_pat;
    }

    const bool in_check = position.in_check(position.side_to_move());
    MoveList moves;
    generate_pseudo_moves_fast(position, moves, !in_check);
    if (!in_check) {
        append_legal_quiet_queen_promotions(position, moves);
    }
    if (moves.empty()) {
        if (in_check) {
            return node_result(-kMateScore + ply, true);
        }
        return node_result(alpha, true);
    }

    SearchNodeResult best = node_result(alpha, true);
    MovePicker picker(position, moves, Move::null(), context, ply);
    OrderedMove ordered;
    const Color us = position.side_to_move();
    bool searched_move = false;
    while (picker.next(ordered)) {
        const Move move = ordered.move;
        const int see = (!in_check && move.is_capture() && !move.is_promotion())
            ? ensure_see(position, ordered)
            : 0;
        UndoState undo;
        if (!position.make_move(move, undo)) {
            continue;
        }
        if (position.in_check(us)) {
            position.unmake_move(undo);
            continue;
        }
        searched_move = true;
        const bool gives_check = position.in_check(position.side_to_move());
        if (!in_check && move.is_capture() && !move.is_promotion() && see < 0 && !gives_check) {
            position.unmake_move(undo);
            continue;
        }
        if (nnue_active(context)) {
            update_search_frame_for_move(
                context.nnue_frames[static_cast<std::size_t>(ply + 1)],
                context.nnue_frames[static_cast<std::size_t>(ply)],
                position,
                *context.nnue,
                undo);
        }
        SearchNodeResult child = quiescence(position, context, -beta, -alpha, ply + 1);
        position.unmake_move(undo);
        if (!child.completed) {
            return node_result(0, false);
        }

        const int score = -child.score;
        if (score >= beta) {
            return node_result(beta, true, move, {move});
        }
        if (score > alpha) {
            alpha = score;
            best.score = score;
            best.best_move = move;
            best.pv = child.pv;
            best.pv.insert(best.pv.begin(), move);
        }
    }
    if (!searched_move && in_check) {
        return node_result(-kMateScore + ply, true);
    }
    if (best.pv.empty()) {
        best.score = alpha;
    }
    return best;
}

SearchNodeResult negamax(Position& position, SearchContext& context, int depth, int alpha, int beta, int ply,
                         bool pv_node, bool allow_null, Move excluded_move = Move::null()) {
    if (should_stop(context)) {
        return node_result(0, false);
    }

    // Repetition and fifty-move status depend on the current path, not only the board hash,
    // so they must be resolved before probing the transposition table.
    if (position.is_draw_by_repetition() || position.is_draw_by_fifty_move() || position.is_insufficient_material()) {
        return node_result(0, true);
    }

    const int alpha_original = alpha;
    const std::uint64_t hash = position.hash();
    Move tt_move = Move::null();
    const bool excluded_search = !excluded_move.is_null();
    std::optional<TTEntry> tt_entry{};
    if (!excluded_search) {
        tt_entry = context.state->tt.probe(hash, ply);
    }

    if (tt_entry) {
        tt_move = tt_entry->best_move;
        if (tt_entry->depth >= depth) {
            if (tt_entry->flag == TTFlag::Exact) {
                return node_result(tt_entry->score, true, tt_entry->best_move, {tt_entry->best_move});
            }
            if (tt_entry->flag == TTFlag::Lower) {
                alpha = std::max(alpha, tt_entry->score);
            } else if (tt_entry->flag == TTFlag::Upper) {
                beta = std::min(beta, tt_entry->score);
            }
            if (alpha >= beta) {
                return node_result(tt_entry->score, true, tt_entry->best_move, {tt_entry->best_move});
            }
        }
    }
#ifdef DEADFISH_WITH_SYZYGY
    if (const auto tb_score = probe_tablebase_wdl(position, *context.state)) {
        return node_result(*tb_score, true);
    }
#endif
    if (depth <= 0) {
        return quiescence(position, context, alpha, beta, ply);
    }
    if (ply >= kMaxPly - 2) {
        return node_result(evaluate_position(position, context, ply), true);
    }

    const bool in_check = position.in_check(position.side_to_move());
    int static_eval = tt_entry && tt_entry->has_static_eval ? tt_entry->static_eval : 0;
    bool has_static_eval = tt_entry && tt_entry->has_static_eval;
    auto current_static_eval = [&]() {
        if (!has_static_eval) {
            static_eval = evaluate_position(position, context, ply);
            has_static_eval = true;
        }
        return static_eval;
    };
    context.stack[static_cast<std::size_t>(ply)].has_static_eval = false;
    if (!in_check) {
        current_static_eval();
        context.stack[static_cast<std::size_t>(ply)].static_eval = static_eval;
        context.stack[static_cast<std::size_t>(ply)].has_static_eval = true;
    }
    const bool improving = !in_check && ply >= 2 &&
        context.stack[static_cast<std::size_t>(ply - 2)].has_static_eval &&
        static_eval > context.stack[static_cast<std::size_t>(ply - 2)].static_eval;

    if (!pv_node && !excluded_search && !in_check && depth <= 2 && static_eval + razor_margin(depth) <= alpha) {
        return quiescence(position, context, alpha, beta, ply);
    }

    if (!pv_node && !excluded_search && !in_check && depth <= 3 &&
        static_eval - reverse_futility_margin(depth) + (improving ? 20 : 0) >= beta) {
        return node_result(static_eval, true);
    }

    if (!pv_node && !excluded_search && allow_null && depth >= 3 && !in_check && !is_zugzwang_prone(position) &&
        static_eval >= beta) {
        UndoState undo;
        if (position.make_null_move(undo)) {
            if (nnue_active(context)) {
                copy_null_move_frame(
                    context.nnue_frames[static_cast<std::size_t>(ply + 1)],
                    context.nnue_frames[static_cast<std::size_t>(ply)],
                    context.nnue->accumulator_size);
            }
            const int eval_margin = std::clamp((static_eval - beta) / 200, 0, 2);
            const int reduction = std::clamp(2 + depth / 5 + eval_margin, 2, std::max(2, depth - 1));
            SearchNodeResult child = negamax(position, context, depth - 1 - reduction, -beta, -beta + 1, ply + 1, false, false);
            position.unmake_move(undo);
            if (!child.completed) {
                return node_result(0, false);
            }
            if (-child.score >= beta) {
                bool verified = true;
                if (depth >= 8) {
                    SearchNodeResult verify = negamax(position, context, depth - reduction, -beta, -beta + 1, ply, false, false);
                    if (!verify.completed) {
                        return node_result(0, false);
                    }
                    verified = verify.score >= beta;
                }
                if (verified) {
                    return node_result(beta, true);
                }
            }
        }
    }

    if (!pv_node && !excluded_search && !in_check && depth >= 5 && std::abs(beta) < kMateScore - kMaxPly) {
        const int prob_beta = beta + 160 + depth * 8;
        MoveList captures;
        generate_pseudo_moves_fast(position, captures, true);
        MovePicker capture_picker(position, captures, Move::null(), context, ply);
        OrderedMove capture_ordered;
        const Color probcut_us = position.side_to_move();
        while (capture_picker.next(capture_ordered)) {
            const Move move = capture_ordered.move;
            if (!move.is_capture() && !move.is_promotion()) {
                continue;
            }
            if (move.is_capture() && !move.is_promotion() && ensure_see(position, capture_ordered) < 0) {
                continue;
            }
            UndoState undo;
            if (!position.make_move(move, undo)) {
                continue;
            }
            if (position.in_check(probcut_us)) {
                position.unmake_move(undo);
                continue;
            }
            context.stack[static_cast<std::size_t>(ply)].current_move = move;
            if (nnue_active(context)) {
                update_search_frame_for_move(
                    context.nnue_frames[static_cast<std::size_t>(ply + 1)],
                    context.nnue_frames[static_cast<std::size_t>(ply)],
                    position,
                    *context.nnue,
                    undo);
            }
            SearchNodeResult child = negamax(position, context, depth - 4, -prob_beta, -prob_beta + 1, ply + 1, false, false);
            context.stack[static_cast<std::size_t>(ply)].current_move = Move::null();
            position.unmake_move(undo);
            if (!child.completed) {
                return node_result(0, false);
            }
            if (-child.score >= prob_beta) {
                return node_result(beta, true, move, {move});
            }
        }
    }

    record_node(context);
    MoveList moves;
    generate_pseudo_moves_fast(position, moves, false);

    SearchNodeResult best = node_result(-kInfinity, true);
    MoveList quiets_tried;
    quiets_tried.reserve(moves.size());
    int move_index = 0;
    const Color us = position.side_to_move();
    std::vector<OrderedMove> root_moves{};
    MovePicker picker(position, moves, tt_move, context, ply);
    OrderedMove ordered;
    if (ply == 0) {
        root_moves = build_root_move_order(position, moves, tt_move, context, context.root_move_rotation);
    }
    auto next_root_move = [&](OrderedMove& candidate, std::size_t& root_index) -> bool {
        if (ply != 0) {
            return picker.next(candidate);
        }
        if (root_index >= root_moves.size()) {
            return false;
        }
        candidate = root_moves[root_index++];
        return true;
    };

    std::size_t root_index = 0;
    bool searched_move = false;
    while (next_root_move(ordered, root_index)) {
        const Move move = ordered.move;
        if (excluded_search && move == excluded_move) {
            ++move_index;
            continue;
        }
        const int see = (!pv_node && depth >= 3 && !in_check && move.is_capture() && !move.is_promotion())
            ? ensure_see(position, ordered)
            : 0;
        const bool quiet = ordered.quiet;
        const int history_score = quiet
            ? context.history[static_cast<std::size_t>(us)][move.from][move.to]
            : 0;
        const int parent_eval = (!pv_node && ply > 0 && !in_check && quiet && !move.is_promotion())
            ? current_static_eval()
            : 0;

        int extension = 0;
        if (!excluded_search && !pv_node && ply > 0 && depth >= 7 && move == tt_move && tt_entry &&
            tt_entry->depth >= depth - 3 && tt_entry->flag != TTFlag::Upper &&
            std::abs(tt_entry->score) < kMateScore - kMaxPly) {
            const int singular_beta = tt_entry->score - 2 * depth;
            SearchNodeResult singular = negamax(
                position, context, (depth - 1) / 2, singular_beta - 1, singular_beta, ply, false, false, move);
            if (!singular.completed) {
                return node_result(0, false);
            }
            if (singular.score < singular_beta) {
                extension = 1;
            }
        }

        UndoState undo;
        if (!position.make_move(move, undo)) {
            ++move_index;
            continue;
        }
        if (position.in_check(us)) {
            position.unmake_move(undo);
            ++move_index;
            continue;
        }
        const bool gives_check = position.in_check(position.side_to_move());
        if (!pv_node && depth >= 3 && !in_check && move.is_capture() && !move.is_promotion() &&
            see < 0 && !gives_check) {
            position.unmake_move(undo);
            ++move_index;
            continue;
        }

        if (!pv_node && ply > 0 && !in_check && quiet && !gives_check && !move.is_promotion()) {
            if (depth <= 3 && move_index > 0 && parent_eval + futility_margin(depth) <= alpha) {
                position.unmake_move(undo);
                ++move_index;
                continue;
            }
            if (depth <= 4 && move_index >= late_quiet_threshold(depth) && history_score <= 0) {
                position.unmake_move(undo);
                ++move_index;
                continue;
            }
        }
        if (nnue_active(context)) {
            update_search_frame_for_move(
                context.nnue_frames[static_cast<std::size_t>(ply + 1)],
                context.nnue_frames[static_cast<std::size_t>(ply)],
                position,
                *context.nnue,
                undo);
        }

        const int child_depth = depth - 1 + extension;
        const int reduction = std::min(
            child_depth,
            late_move_reduction(depth, move_index, pv_node, quiet, gives_check, history_score, improving));

        SearchNodeResult child;
        searched_move = true;
        context.stack[static_cast<std::size_t>(ply)].current_move = move;
        if (move_index == 0) {
            child = negamax(position, context, child_depth, -beta, -alpha, ply + 1, pv_node, true);
        } else {
            child = negamax(position, context, child_depth - reduction, -alpha - 1, -alpha, ply + 1, false, true);
            if (child.completed) {
                const int reduced_score = -child.score;
                if (reduction > 0 && reduced_score > alpha) {
                    child = negamax(position, context, child_depth, -alpha - 1, -alpha, ply + 1, false, true);
                }
                if (child.completed) {
                    const int pvs_score = -child.score;
                    if (pvs_score > alpha && pvs_score < beta) {
                        child = negamax(position, context, child_depth, -beta, -alpha, ply + 1, true, true);
                    }
                }
            }
        }
        context.stack[static_cast<std::size_t>(ply)].current_move = Move::null();
        position.unmake_move(undo);
        if (!child.completed) {
            return node_result(0, false);
        }

        int score = -child.score;
        if (ply == 0 && !tt_move.is_null() && move == tt_move && std::abs(score) < kMateScore - kMaxPly) {
            score += 8;
        }
        if (score > best.score) {
            best.score = score;
            best.best_move = move;
            best.pv = child.pv;
            best.pv.insert(best.pv.begin(), move);
        }
        if (quiet) {
            quiets_tried.push_back(move);
        }

        alpha = std::max(alpha, score);
        if (alpha >= beta) {
            if (quiet) {
                update_quiet_cutoff_stats(context, us, move, quiets_tried, depth, ply);
            } else if (move.is_capture()) {
                update_capture_cutoff_stats(context, us, move, depth);
            }
            break;
        }

        ++move_index;
    }

    if (!searched_move && excluded_search) {
        return node_result(alpha_original, true);
    }
    if (!searched_move) {
        if (in_check) {
            return node_result(-kMateScore + ply, true);
        }
        return node_result(0, true);
    }

    if (!context.stop && !excluded_search && !best.best_move.is_null()) {
        TTFlag flag = TTFlag::Exact;
        if (best.score <= alpha_original) {
            flag = TTFlag::Upper;
        } else if (best.score >= beta) {
            flag = TTFlag::Lower;
        }
        context.state->tt.store(hash, depth, best.score, flag, best.best_move, ply, has_static_eval ? static_eval : kNoTtEval);
    }

    return best;
}

std::uint64_t perft_recursive(Position& position, int depth) {
    if (depth <= 0) {
        return 1;
    }
    MoveList moves = generate_legal_moves_fast(position, false);
    if (depth == 1) {
        return moves.size();
    }
    std::uint64_t nodes = 0;
    for (const Move& move : moves) {
        UndoState undo;
        position.make_move(move, undo);
        nodes += perft_recursive(position, depth - 1);
        position.unmake_move(undo);
    }
    return nodes;
}

SearchResult run_search_worker(const Position& root, SearchContext& context) {
    SearchResult result;
    result.best_move = Move::null();
    result.completed = true;

    Position position = root;
    if (context.nnue) {
        ensure_root_nnue_frame(context, position);
    }

    const int default_depth = context.limits.infinite ? (kMaxPly - 1) : 64;
    const int max_depth = std::max(1, context.limits.max_depth > 0 ? context.limits.max_depth : default_depth);
    int previous_score = 0;
    int root_stability = 0;
    for (int depth = 1; depth <= max_depth; ++depth) {
        SearchNodeResult node;
        if (depth >= 4 && result.completed) {
            int window = kDefaultAspirationWindow;
            while (true) {
                const int aspiration_alpha = std::max(-kInfinity, previous_score - window);
                const int aspiration_beta = std::min(kInfinity, previous_score + window);
                node = negamax(position, context, depth, aspiration_alpha, aspiration_beta, 0, true, true);
                if (!node.completed) {
                    break;
                }
                if (node.score <= aspiration_alpha || node.score >= aspiration_beta) {
                    window *= 2;
                    if (window > kInfinity / 2) {
                        node = negamax(position, context, depth, -kInfinity, kInfinity, 0, true, true);
                        break;
                    }
                    continue;
                }
                break;
            }
        } else {
            node = negamax(position, context, depth, -kInfinity, kInfinity, 0, true, true);
        }
        if (!node.completed) {
            result.timed_out = context.stop;
            break;
        }
        const Move previous_best = result.best_move;
        const int previous_completed_score = result.score;
        const int previous_completed_depth = result.depth_reached;
        bool root_unstable = false;
        bool score_dropped = false;
        if (!node.best_move.is_null()) {
            root_unstable = !previous_best.is_null() && node.best_move != previous_best;
            root_stability = (!previous_best.is_null() && node.best_move == previous_best) ? root_stability + 1 : 0;
            score_dropped = previous_completed_depth >= 3 && node.score + 80 < previous_completed_score;
            result.best_move = node.best_move;
            result.score = node.score;
            result.depth_reached = depth;
            result.pv = node.pv;
            result.completed = true;
            previous_score = node.score;
            publish_shared_root_result(context, node, depth);
        }

        flush_pending_nodes(context);
        const int elapsed = elapsed_ms(context);
        const std::uint64_t nodes = visible_nodes(context);
        const std::uint64_t nps = elapsed > 0 ? (nodes * 1000ULL) / static_cast<std::uint64_t>(elapsed) : nodes;
        if (context.callback) {
            context.callback(SearchInfo{
                .depth = depth,
                .score = result.score,
                .nodes = nodes,
                .nps = nps,
                .elapsed_ms = elapsed,
                .pv = result.pv,
            });
        }
        if (std::abs(result.score) >= kMateScore - 128) {
            break;
        }
        const bool soft_reached = soft_limit_reached(context);
        const bool can_extend_time = soft_reached && context.hard_time_ms > context.soft_time_ms &&
            context.hard_time_ms - context.soft_time_ms >= 30 && depth >= 4 &&
            elapsed + 5 < context.hard_time_ms && (root_unstable || score_dropped || root_stability < 2);
        if (context.stop || hard_limit_reached(context) || (soft_reached && !can_extend_time)) {
            result.timed_out = true;
            context.stop = true;
            if (context.shared) {
                context.shared->stop.store(true, std::memory_order_relaxed);
            }
            break;
        }
    }

    flush_pending_nodes(context);
    result.elapsed_ms = elapsed_ms(context);
    result.nodes = visible_nodes(context);
    result.nps = result.elapsed_ms > 0 ? (result.nodes * 1000ULL) / static_cast<std::uint64_t>(result.elapsed_ms) : result.nodes;
    if (result.best_move.is_null()) {
        std::vector<Move> legal = root.legal_moves();
        if (!legal.empty()) {
            result.best_move = first_legal_move(root, legal);
            result.pv = {result.best_move};
        }
    }
    return result;
}

SearchContext make_worker_context(EngineState& state, SearchSharedState* shared, SearchCallback callback, int worker_index) {
    SearchContext context;
    context.state = &state;
    context.shared = shared;
    context.limits = shared->limits;
    context.callback = std::move(callback);
    context.start_time = shared->start_time;
    context.soft_time_ms = shared->soft_time_ms;
    context.hard_time_ms = shared->hard_time_ms;
    context.nnue = shared->nnue;
    context.worker_index = worker_index;
    context.root_move_rotation = std::max(0, worker_index);
    return context;
}

void worker_loop(EngineState* state, int worker_index) {
    std::uint64_t observed_generation = 0;
    while (true) {
        std::shared_ptr<SearchSharedState> search{};
        {
            std::unique_lock<std::mutex> lock(state->worker_mutex);
            state->worker_cv.wait(lock, [&] {
                return state->workers_shutdown || state->active_search_generation != observed_generation;
            });
            if (state->workers_shutdown) {
                return;
            }
            observed_generation = state->active_search_generation;
            search = state->active_search;
        }
        if (!search) {
            continue;
        }

        SearchContext context = make_worker_context(*state, search.get(), {}, worker_index);
        run_search_worker(search->root, context);
        flush_pending_nodes(context);

        if (search->helpers_remaining.fetch_sub(1, std::memory_order_acq_rel) == 1) {
            std::lock_guard<std::mutex> done_guard(search->done_mutex);
            search->done_cv.notify_all();
        }
    }
}

void shutdown_worker_pool(EngineState& state) {
    {
        std::lock_guard<std::mutex> lock(state.worker_mutex);
        state.workers_shutdown = true;
        state.active_search.reset();
        state.active_search_generation += 1;
    }
    state.worker_cv.notify_all();
    for (std::thread& worker : state.worker_threads) {
        if (worker.joinable()) {
            worker.join();
        }
    }
    state.worker_threads.clear();
    state.workers_shutdown = false;
}

void resize_worker_pool(EngineState& state, int thread_count) {
    shutdown_worker_pool(state);
    const int helper_count = std::max(0, thread_count - 1);
    state.worker_threads.reserve(static_cast<std::size_t>(helper_count));
    for (int index = 0; index < helper_count; ++index) {
        state.worker_threads.emplace_back(worker_loop, &state, index + 1);
    }
}

}  // namespace

EngineState::~EngineState() {
    shutdown_worker_pool(*this);
}

Engine::Engine() : state_(std::make_unique<EngineState>()) {
    state_->tt.resize_mb(state_->options.hash_mb);
    state_->classical_eval_cache.resize(1u << 16);
    refresh_nnue_runtime(*state_);
    resize_worker_pool(*state_, state_->options.threads);
}

Engine::~Engine() = default;

Engine::Engine(Engine&&) noexcept = default;

Engine& Engine::operator=(Engine&&) noexcept = default;

const EngineOptions& Engine::options() const {
    return state_->options;
}

void Engine::set_options(const EngineOptions& incoming) {
    EngineOptions updated = incoming;
    updated.hash_mb = std::max(1, updated.hash_mb);
    updated.threads = std::clamp(updated.threads, 1, 64);
    updated.syzygy_probe_limit = std::clamp(updated.syzygy_probe_limit, 0, 7);
    updated.move_overhead_ms = std::max(0, updated.move_overhead_ms);

    const bool resize_hash = updated.hash_mb != state_->options.hash_mb;
    const bool resize_threads = updated.threads != state_->options.threads;
    const bool reset_book = updated.book_path != state_->options.book_path || updated.own_book != state_->options.own_book;
    const bool reset_nnue = updated.eval_file != state_->options.eval_file || updated.use_nnue != state_->options.use_nnue;
    state_->options = std::move(updated);

    if (resize_hash) {
        state_->tt.resize_mb(state_->options.hash_mb);
    }
    if (resize_threads) {
        resize_worker_pool(*state_, state_->options.threads);
    }
    if (reset_book) {
        clear_book_cache(*state_);
    }
    if (reset_nnue) {
        refresh_nnue_runtime(*state_);
    }
    if ((reset_nnue || resize_threads) && !resize_hash) {
        state_->tt.clear();
    }
}

void Engine::reset_search_state() {
    state_->stop_requested.store(false, std::memory_order_relaxed);
    state_->tt.clear();
    state_->classical_eval_cache.clear();
}

void Engine::request_stop() {
    state_->stop_requested.store(true, std::memory_order_relaxed);
}

void Engine::clear_stop_request() {
    state_->stop_requested.store(false, std::memory_order_relaxed);
}

bool Engine::nnue_loaded() const {
    return state_->nnue != nullptr;
}

std::string Engine::nnue_status() const {
    return state_->nnue_status;
}

int Engine::evaluate(const Position& position) const {
    if (std::shared_ptr<const NnueNetwork> network = active_nnue(*state_)) {
        return position.evaluate_backbone_relative() + evaluate_with_nnue_full(position, *network);
    }
    return position.evaluate_relative();
}

int Engine::evaluate_classical(const Position& position) const {
    return position.evaluate_relative();
}

int Engine::evaluate_backbone(const Position& position) const {
    return position.evaluate_backbone_relative();
}

int Engine::evaluate_nnue_residual(const Position& position) const {
    if (!state_->nnue) {
        return 0;
    }
    return evaluate_with_nnue_full(position, *state_->nnue);
}

SearchResult Engine::search(const Position& root, const SearchLimits& limits, SearchCallback callback) {
    clear_stop_request();
    SearchResult result;
    result.best_move = Move::null();
    result.completed = true;

    if (!root.is_draw()) {
        if (Move book_move = probe_book_move(root, *state_); !book_move.is_null()) {
            result.best_move = book_move;
            result.pv = {book_move};
            result.used_book = true;
            return result;
        }
#ifdef DEADFISH_WITH_SYZYGY
        if (TablebaseRootResult tablebase = probe_tablebase_root(root, *state_); tablebase.ok) {
            result.best_move = tablebase.move;
            result.score = tablebase.score;
            result.pv = {tablebase.move};
            result.used_tablebase = true;
            return result;
        }
#endif
    }

    state_->tt.new_search();

    if (state_->options.threads <= 1 || state_->worker_threads.empty()) {
        SearchContext context;
        context.state = state_.get();
        context.limits = limits;
        context.callback = std::move(callback);
        context.start_time = std::chrono::steady_clock::now();
        context.nnue = active_nnue(*state_);
        context.worker_index = 0;
        compute_time_budgets(root, limits, state_->options, context.soft_time_ms, context.hard_time_ms);
        return run_search_worker(root, context);
    }

    auto shared = std::make_shared<SearchSharedState>();
    shared->root = root;
    shared->limits = limits;
    shared->start_time = std::chrono::steady_clock::now();
    shared->nnue = active_nnue(*state_);
    shared->helpers_remaining.store(static_cast<int>(state_->worker_threads.size()), std::memory_order_relaxed);
    compute_time_budgets(root, limits, state_->options, shared->soft_time_ms, shared->hard_time_ms);

    {
        std::lock_guard<std::mutex> lock(state_->worker_mutex);
        state_->active_search = shared;
        state_->active_search_generation += 1;
    }
    state_->worker_cv.notify_all();

    SearchContext leader = make_worker_context(*state_, shared.get(), std::move(callback), 0);
    SearchResult leader_result = run_search_worker(root, leader);
    flush_pending_nodes(leader);

    shared->stop.store(true, std::memory_order_relaxed);
    {
        std::unique_lock<std::mutex> done_lock(shared->done_mutex);
        shared->done_cv.wait(done_lock, [&] {
            return shared->helpers_remaining.load(std::memory_order_acquire) == 0;
        });
    }
    {
        std::lock_guard<std::mutex> lock(state_->worker_mutex);
        if (state_->active_search == shared) {
            state_->active_search.reset();
        }
    }

    SharedRootResult best_completed;
    {
        std::lock_guard<std::mutex> guard(shared->result_mutex);
        best_completed = shared->best_completed;
    }
    if (best_completed.completed && !best_completed.best_move.is_null()) {
        leader_result.best_move = best_completed.best_move;
        leader_result.score = best_completed.score;
        leader_result.depth_reached = best_completed.depth_reached;
        leader_result.pv = best_completed.pv;
        leader_result.completed = true;
    }
    leader_result.elapsed_ms = elapsed_ms(leader);
    leader_result.nodes = shared->nodes.load(std::memory_order_relaxed);
    leader_result.nps = leader_result.elapsed_ms > 0
        ? (leader_result.nodes * 1000ULL) / static_cast<std::uint64_t>(leader_result.elapsed_ms)
        : leader_result.nodes;
    if (leader_result.best_move.is_null()) {
        std::vector<Move> legal = root.legal_moves();
        if (!legal.empty()) {
            leader_result.best_move = first_legal_move(root, legal);
            leader_result.pv = {leader_result.best_move};
        }
    }
    return leader_result;
}

std::uint64_t Engine::perft(const Position& root, int depth) {
    Position position = root;
    return perft_recursive(position, depth);
}

std::vector<std::pair<Move, std::uint64_t>> Engine::divide(const Position& root, int depth) {
    Position position = root;
    std::vector<std::pair<Move, std::uint64_t>> result;
    MoveList legal = generate_legal_moves_fast(position, false);
    for (const Move& move : legal) {
        UndoState undo;
        position.make_move(move, undo);
        result.push_back({move, perft_recursive(position, depth - 1)});
        position.unmake_move(undo);
    }
    return result;
}

std::vector<std::string> Engine::benchmark_positions() {
    return {
        std::string(kCanonicalStartFen),
        "r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQ1BPPP/R1B1K2R w KQkq - 0 1",
        "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
        "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",
        "6k1/5ppp/8/8/8/8/5PPP/6KQ w - - 0 1",
    };
}

std::string score_to_string(int score) {
    if (std::abs(score) >= kMateScore - 256) {
        const int mate_in = std::max(1, (kMateScore - std::abs(score) + 1) / 2);
        return score > 0 ? "M" + std::to_string(mate_in) : "-M" + std::to_string(mate_in);
    }
    std::ostringstream out;
    out.setf(std::ios::fixed);
    out.precision(2);
    out << static_cast<double>(score) / 100.0;
    return out.str();
}

std::string join_moves(const std::vector<Move>& moves, const std::string& delimiter) {
    std::ostringstream out;
    bool first = true;
    for (const Move& move : moves) {
        if (!first) {
            out << delimiter;
        }
        first = false;
        out << move.to_uci();
    }
    return out.str();
}

int static_exchange_eval(const Position& position, const Move& move) {
    return see_value(position, move);
}

}  // namespace deadfish

namespace deadfish {

Move Move::null() {
    return Move{};
}

bool Move::is_null() const {
    return from == 0 && to == 0 && flag == MoveFlag::Quiet && promotion == PieceType::None;
}

bool Move::is_capture() const {
    return flag == MoveFlag::Capture || flag == MoveFlag::PromotionCapture || flag == MoveFlag::EnPassant;
}

bool Move::is_promotion() const {
    return flag == MoveFlag::Promotion || flag == MoveFlag::PromotionCapture;
}

std::string Move::to_uci() const {
    if (is_null()) {
        return "0000";
    }
    std::string text = square_to_string(from) + square_to_string(to);
    if (promotion != PieceType::None) {
        text += promotion_to_char(promotion);
    }
    return text;
}

bool operator==(const Move& lhs, const Move& rhs) {
    return lhs.from == rhs.from && lhs.to == rhs.to &&
           lhs.flag == rhs.flag && lhs.promotion == rhs.promotion;
}

bool operator!=(const Move& lhs, const Move& rhs) {
    return !(lhs == rhs);
}

Position::Position() {
    *this = Position::start_position();
}

Position::Position(RawInitTag) {
    clear();
}

Position Position::start_position() {
    return Position::from_fen(std::string(kCanonicalStartFen));
}

Position Position::from_fen(const std::string& fen, std::string* error) {
    Position position(RawInitTag{});

    std::istringstream stream(fen);
    std::string board_part;
    std::string side_part;
    std::string castling_part;
    std::string ep_part;
    std::string halfmove_part;
    std::string fullmove_part;
    if (!(stream >> board_part >> side_part >> castling_part >> ep_part >> halfmove_part >> fullmove_part)) {
        if (error) {
            *error = "FEN must contain six fields.";
        }
        return Position::start_position();
    }

    int rank = 7;
    int file = 0;
    for (char ch : board_part) {
        if (ch == '/') {
            if (file != 8) {
                if (error) {
                    *error = "Invalid board rows in FEN.";
                }
                return Position::start_position();
            }
            rank -= 1;
            file = 0;
            continue;
        }
        if (std::isdigit(static_cast<unsigned char>(ch))) {
            file += ch - '0';
            continue;
        }
        if (rank < 0 || file >= 8) {
            if (error) {
                *error = "FEN board overflowed the board.";
            }
            return Position::start_position();
        }
        const Piece piece = fen_to_piece(ch);
        if (piece == Piece::None) {
            if (error) {
                *error = "Unknown FEN piece.";
            }
            return Position::start_position();
        }
        position.place_piece(make_square(file, rank), piece);
        file += 1;
    }
    if (rank != 0 || file != 8) {
        if (error) {
            *error = "FEN board did not finish on h1.";
        }
        return Position::start_position();
    }

    if (side_part == "w") {
        position.side_to_move_ = Color::White;
    } else if (side_part == "b") {
        position.side_to_move_ = Color::Black;
    } else {
        if (error) {
            *error = "Invalid side-to-move field.";
        }
        return Position::start_position();
    }

    position.castling_rights_ = 0;
    if (castling_part != "-") {
        for (char ch : castling_part) {
            switch (ch) {
                case 'K':
                    position.castling_rights_ |= kCastleWhiteKing;
                    break;
                case 'Q':
                    position.castling_rights_ |= kCastleWhiteQueen;
                    break;
                case 'k':
                    position.castling_rights_ |= kCastleBlackKing;
                    break;
                case 'q':
                    position.castling_rights_ |= kCastleBlackQueen;
                    break;
                default:
                    if (error) {
                        *error = "Invalid castling-rights field.";
                    }
                    return Position::start_position();
            }
        }
    }

    position.en_passant_square_ = ep_part == "-" ? kNoSquare : string_to_square(ep_part);
    if (ep_part != "-" && position.en_passant_square_ == kNoSquare) {
        if (error) {
            *error = "Invalid en-passant square.";
        }
        return Position::start_position();
    }

    try {
        position.halfmove_clock_ = std::stoi(halfmove_part);
        position.fullmove_number_ = std::stoi(fullmove_part);
    } catch (const std::exception&) {
        if (error) {
            *error = "Halfmove/fullmove fields must be integers.";
        }
        return Position::start_position();
    }

    if (std::popcount(position.piece_bitboards_[static_cast<std::size_t>(Piece::WKing)]) != 1 ||
        std::popcount(position.piece_bitboards_[static_cast<std::size_t>(Piece::BKing)]) != 1) {
        if (error) {
            *error = "FEN must contain exactly one king for each side.";
        }
        return Position::start_position();
    }

    position.hash_ = position.compute_hash();
    position.repetition_history_.push_back(position.hash_);
    if (error) {
        *error = "";
    }
    return position;
}

std::string Position::to_fen() const {
    std::ostringstream out;
    for (int rank = 7; rank >= 0; --rank) {
        int empty_count = 0;
        for (int file = 0; file < 8; ++file) {
            const Piece piece = board_[make_square(file, rank)];
            if (piece == Piece::None) {
                empty_count += 1;
                continue;
            }
            if (empty_count > 0) {
                out << empty_count;
                empty_count = 0;
            }
            out << piece_to_fen(piece);
        }
        if (empty_count > 0) {
            out << empty_count;
        }
        if (rank > 0) {
            out << '/';
        }
    }
    out << ' ' << (side_to_move_ == Color::White ? 'w' : 'b') << ' ';
    if (castling_rights_ == 0) {
        out << '-';
    } else {
        if ((castling_rights_ & kCastleWhiteKing) != 0) {
            out << 'K';
        }
        if ((castling_rights_ & kCastleWhiteQueen) != 0) {
            out << 'Q';
        }
        if ((castling_rights_ & kCastleBlackKing) != 0) {
            out << 'k';
        }
        if ((castling_rights_ & kCastleBlackQueen) != 0) {
            out << 'q';
        }
    }
    out << ' ';
    out << (en_passant_square_ == kNoSquare ? "-" : square_to_string(en_passant_square_));
    out << ' ' << halfmove_clock_ << ' ' << fullmove_number_;
    return out.str();
}

std::string Position::pretty() const {
    std::ostringstream out;
    out << "\n  +-----------------+\n";
    for (int rank = 7; rank >= 0; --rank) {
        out << (rank + 1) << " | ";
        for (int file = 0; file < 8; ++file) {
            const Piece piece = board_[make_square(file, rank)];
            out << (piece == Piece::None ? '.' : piece_to_fen(piece)) << ' ';
        }
        out << "|\n";
    }
    out << "  +-----------------+\n";
    out << "    a b c d e f g h\n";
    out << "Turn: " << (side_to_move_ == Color::White ? "White" : "Black") << "\n";
    out << "FEN:  " << to_fen() << "\n";
    return out.str();
}

Color Position::side_to_move() const {
    return side_to_move_;
}

std::uint8_t Position::castling_rights() const {
    return castling_rights_;
}

int Position::en_passant_square() const {
    return en_passant_square_;
}

int Position::halfmove_clock() const {
    return halfmove_clock_;
}

int Position::fullmove_number() const {
    return fullmove_number_;
}

std::uint64_t Position::hash() const {
    return hash_;
}

const std::array<Piece, 64>& Position::board() const {
    return board_;
}

Piece Position::piece_at(int square) const {
    return square >= 0 && square < 64 ? board_[square] : Piece::None;
}

Bitboard Position::occupancy() const {
    return color_bitboards_[static_cast<std::size_t>(Color::White)] |
           color_bitboards_[static_cast<std::size_t>(Color::Black)];
}

Bitboard Position::occupancy(Color color) const {
    return color_bitboards_[static_cast<std::size_t>(color)];
}

Bitboard Position::piece_bitboard(Piece piece) const {
    return piece_bitboards_[static_cast<std::size_t>(piece)];
}

int Position::piece_count() const {
    return static_cast<int>(std::popcount(occupancy()));
}

bool Position::has_non_pawn_material(Color color) const {
    for (int square = 0; square < 64; ++square) {
        const Piece piece = board_[square];
        if (piece == Piece::None || piece_color(piece) != color) {
            continue;
        }
        const PieceType type = piece_type(piece);
        if (type != PieceType::King && type != PieceType::Pawn) {
            return true;
        }
    }
    return false;
}

void Position::clear() {
    board_.fill(Piece::None);
    piece_bitboards_.fill(0);
    color_bitboards_.fill(0);
    side_to_move_ = Color::White;
    castling_rights_ = 0;
    en_passant_square_ = kNoSquare;
    halfmove_clock_ = 0;
    fullmove_number_ = 1;
    hash_ = 0;
    repetition_history_.clear();
}

void Position::place_piece(int square, Piece piece, bool update_hash) {
    if (square < 0 || square >= 64 || piece == Piece::None) {
        return;
    }
    board_[square] = piece;
    piece_bitboards_[static_cast<std::size_t>(piece)] |= bit_at(square);
    color_bitboards_[static_cast<std::size_t>(piece_color(piece))] |= bit_at(square);
    if (update_hash) {
        hash_ ^= kZobrist.pieces[static_cast<std::size_t>(piece)][square];
    }
}

void Position::remove_piece(int square, bool update_hash) {
    if (square < 0 || square >= 64) {
        return;
    }
    const Piece piece = board_[square];
    if (piece == Piece::None) {
        return;
    }
    if (update_hash) {
        hash_ ^= kZobrist.pieces[static_cast<std::size_t>(piece)][square];
    }
    piece_bitboards_[static_cast<std::size_t>(piece)] &= ~bit_at(square);
    color_bitboards_[static_cast<std::size_t>(piece_color(piece))] &= ~bit_at(square);
    board_[square] = Piece::None;
}

void Position::move_piece(int from, int to, bool update_hash) {
    const Piece piece = board_[from];
    remove_piece(from, update_hash);
    remove_piece(to, update_hash);
    place_piece(to, piece, update_hash);
}

void Position::refresh_color_bitboards() {
    color_bitboards_.fill(0);
    for (std::size_t piece = static_cast<std::size_t>(Piece::WPawn); piece <= static_cast<std::size_t>(Piece::WKing); ++piece) {
        color_bitboards_[static_cast<std::size_t>(Color::White)] |= piece_bitboards_[piece];
    }
    for (std::size_t piece = static_cast<std::size_t>(Piece::BPawn); piece <= static_cast<std::size_t>(Piece::BKing); ++piece) {
        color_bitboards_[static_cast<std::size_t>(Color::Black)] |= piece_bitboards_[piece];
    }
}

std::uint64_t Position::compute_hash() const {
    std::uint64_t value = 0;
    for (int square = 0; square < 64; ++square) {
        const Piece piece = board_[square];
        if (piece != Piece::None) {
            value ^= kZobrist.pieces[static_cast<std::size_t>(piece)][square];
        }
    }
    value ^= kZobrist.castling[castling_rights_ & 0x0F];
    if (en_passant_square_ != kNoSquare) {
        value ^= kZobrist.en_passant[square_file(en_passant_square_)];
    }
    if (side_to_move_ == Color::Black) {
        value ^= kZobrist.side;
    }
    return value;
}

void Position::xor_castling_hash(std::uint8_t castling_rights) {
    hash_ ^= kZobrist.castling[castling_rights & 0x0F];
}

void Position::xor_en_passant_hash(int en_passant_square) {
    if (en_passant_square != kNoSquare) {
        hash_ ^= kZobrist.en_passant[square_file(en_passant_square)];
    }
}

void Position::xor_side_hash() {
    hash_ ^= kZobrist.side;
}

int Position::king_square(Color color) const {
    const Piece king = color == Color::White ? Piece::WKing : Piece::BKing;
    const Bitboard bb = piece_bitboards_[static_cast<std::size_t>(king)];
    return bb == 0 ? kNoSquare : std::countr_zero(bb);
}

bool Position::is_square_attacked(int square, Color by_color) const {
    if (square < 0 || square >= 64) {
        return false;
    }
    const Bitboard occupied = occupancy();
    if ((kPawnAttacks[static_cast<std::size_t>(opposite(by_color))][static_cast<std::size_t>(square)] &
         piece_bitboards_[static_cast<std::size_t>(make_piece(by_color, PieceType::Pawn))]) != 0) {
        return true;
    }
    if ((kKnightAttacks[static_cast<std::size_t>(square)] &
         piece_bitboards_[static_cast<std::size_t>(make_piece(by_color, PieceType::Knight))]) != 0) {
        return true;
    }
    if ((kKingAttacks[static_cast<std::size_t>(square)] &
         piece_bitboards_[static_cast<std::size_t>(make_piece(by_color, PieceType::King))]) != 0) {
        return true;
    }
    const Bitboard rook_like = piece_bitboards_[static_cast<std::size_t>(make_piece(by_color, PieceType::Rook))] |
                               piece_bitboards_[static_cast<std::size_t>(make_piece(by_color, PieceType::Queen))];
    if ((rook_attacks(square, occupied) & rook_like) != 0) {
        return true;
    }
    const Bitboard bishop_like = piece_bitboards_[static_cast<std::size_t>(make_piece(by_color, PieceType::Bishop))] |
                                 piece_bitboards_[static_cast<std::size_t>(make_piece(by_color, PieceType::Queen))];
    return (bishop_attacks(square, occupied) & bishop_like) != 0;
}

bool Position::in_check(Color color) const {
    const int square = king_square(color);
    return square != kNoSquare && is_square_attacked(square, opposite(color));
}

std::vector<Move> Position::generate_pseudo_moves(bool captures_only) const {
    std::vector<Move> moves;
    moves.reserve(captures_only ? 32 : 96);
    const Color us = side_to_move_;
    const Color them = opposite(us);
    auto push_move = [&](int from, int to, MoveFlag flag, PieceType promotion = PieceType::None) {
        moves.push_back(Move{
            .from = static_cast<std::uint8_t>(from),
            .to = static_cast<std::uint8_t>(to),
            .flag = flag,
            .promotion = promotion,
        });
    };
    auto add_promotions = [&](int from, int to, bool capture) {
        push_move(from, to, capture ? MoveFlag::PromotionCapture : MoveFlag::Promotion, PieceType::Queen);
        push_move(from, to, capture ? MoveFlag::PromotionCapture : MoveFlag::Promotion, PieceType::Rook);
        push_move(from, to, capture ? MoveFlag::PromotionCapture : MoveFlag::Promotion, PieceType::Bishop);
        push_move(from, to, capture ? MoveFlag::PromotionCapture : MoveFlag::Promotion, PieceType::Knight);
    };

    for (int from = 0; from < 64; ++from) {
        const Piece piece = board_[from];
        if (piece == Piece::None || piece_color(piece) != us) {
            continue;
        }
        const PieceType type = piece_type(piece);
        const int file = square_file(from);
        const int rank = square_rank(from);

        if (type == PieceType::Pawn) {
            const int direction = us == Color::White ? 8 : -8;
            const int start_rank = us == Color::White ? 1 : 6;
            const int promotion_rank = us == Color::White ? 6 : 1;
            const int next = from + direction;
            if (!captures_only && next >= 0 && next < 64 && board_[next] == Piece::None) {
                if (rank == promotion_rank) {
                    add_promotions(from, next, false);
                } else {
                    push_move(from, next, MoveFlag::Quiet);
                    const int jump = from + 2 * direction;
                    if (rank == start_rank && board_[jump] == Piece::None) {
                        push_move(from, jump, MoveFlag::DoublePawnPush);
                    }
                }
            }
            const std::array<int, 2> targets = us == Color::White ? std::array<int, 2>{from + 7, from + 9}
                                                                   : std::array<int, 2>{from - 9, from - 7};
            for (int to : targets) {
                if (to < 0 || to >= 64 || std::abs(square_file(to) - file) != 1) {
                    continue;
                }
                const Piece target = board_[to];
                if (target != Piece::None && piece_color(target) == them) {
                    if (rank == promotion_rank) {
                        add_promotions(from, to, true);
                    } else {
                        push_move(from, to, MoveFlag::Capture);
                    }
                } else if (to == en_passant_square_) {
                    push_move(from, to, MoveFlag::EnPassant);
                }
            }
            continue;
        }

        if (type == PieceType::Knight || type == PieceType::King) {
            Bitboard attacks = type == PieceType::Knight ? kKnightAttacks[from] : kKingAttacks[from];
            while (attacks) {
                const int to = pop_lsb(attacks);
                const Piece target = board_[to];
                if (target == Piece::None) {
                    if (!captures_only) {
                        push_move(from, to, MoveFlag::Quiet);
                    }
                } else if (piece_color(target) == them) {
                    push_move(from, to, MoveFlag::Capture);
                }
            }
            if (type == PieceType::King && !captures_only && !in_check(us)) {
                if (us == Color::White) {
                    if ((castling_rights_ & kCastleWhiteKing) != 0 &&
                        board_[5] == Piece::None && board_[6] == Piece::None &&
                        board_[7] == Piece::WRook &&
                        !is_square_attacked(5, them) && !is_square_attacked(6, them)) {
                        push_move(4, 6, MoveFlag::KingCastle);
                    }
                    if ((castling_rights_ & kCastleWhiteQueen) != 0 &&
                        board_[3] == Piece::None && board_[2] == Piece::None && board_[1] == Piece::None &&
                        board_[0] == Piece::WRook &&
                        !is_square_attacked(3, them) && !is_square_attacked(2, them)) {
                        push_move(4, 2, MoveFlag::QueenCastle);
                    }
                } else {
                    if ((castling_rights_ & kCastleBlackKing) != 0 &&
                        board_[61] == Piece::None && board_[62] == Piece::None &&
                        board_[63] == Piece::BRook &&
                        !is_square_attacked(61, them) && !is_square_attacked(62, them)) {
                        push_move(60, 62, MoveFlag::KingCastle);
                    }
                    if ((castling_rights_ & kCastleBlackQueen) != 0 &&
                        board_[59] == Piece::None && board_[58] == Piece::None && board_[57] == Piece::None &&
                        board_[56] == Piece::BRook &&
                        !is_square_attacked(59, them) && !is_square_attacked(58, them)) {
                        push_move(60, 58, MoveFlag::QueenCastle);
                    }
                }
            }
            continue;
        }

        auto walk = [&](std::initializer_list<std::pair<int, int>> directions) {
            for (const auto& [df, dr] : directions) {
                int next_file = file + df;
                int next_rank = rank + dr;
                while (next_file >= 0 && next_file < 8 && next_rank >= 0 && next_rank < 8) {
                    const int to = make_square(next_file, next_rank);
                    const Piece target = board_[to];
                    if (target == Piece::None) {
                        if (!captures_only) {
                            push_move(from, to, MoveFlag::Quiet);
                        }
                    } else {
                        if (piece_color(target) == them) {
                            push_move(from, to, MoveFlag::Capture);
                        }
                        break;
                    }
                    next_file += df;
                    next_rank += dr;
                }
            }
        };

        if (type == PieceType::Bishop) {
            walk({{1, 1}, {1, -1}, {-1, 1}, {-1, -1}});
        } else if (type == PieceType::Rook) {
            walk({{1, 0}, {-1, 0}, {0, 1}, {0, -1}});
        } else if (type == PieceType::Queen) {
            walk({{1, 1}, {1, -1}, {-1, 1}, {-1, -1}, {1, 0}, {-1, 0}, {0, 1}, {0, -1}});
        }
    }

    return moves;
}

std::vector<Move> Position::legal_moves_fast(bool captures_only) {
    MoveList generated = generate_legal_moves_fast(*this, captures_only);
    std::vector<Move> legal;
    legal.reserve(generated.size());
    for (const Move& move : generated) {
        legal.push_back(move);
    }
    return legal;
}

std::vector<Move> Position::legal_moves(bool captures_only) const {
    Position copy = *this;
    return copy.legal_moves_fast(captures_only);
}

std::vector<std::string> Position::legal_moves_uci(bool captures_only) const {
    std::vector<std::string> list;
    for (const Move& move : legal_moves(captures_only)) {
        list.push_back(move.to_uci());
    }
    return list;
}

Move Position::parse_uci_move(const std::string& uci) const {
    std::string normalized;
    normalized.reserve(uci.size());
    for (char ch : uci) {
        normalized.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(ch))));
    }
    for (const Move& move : legal_moves(false)) {
        if (move.to_uci() == normalized) {
            return move;
        }
    }
    return Move::null();
}

bool Position::is_move_legal(const Move& move) const {
    for (const Move& candidate : legal_moves(false)) {
        if (candidate == move) {
            return true;
        }
    }
    return false;
}

bool Position::apply_uci_move(const std::string& uci, std::string* error) {
    const Move move = parse_uci_move(uci);
    if (move.is_null()) {
        if (error) {
            *error = "Illegal move: " + uci;
        }
        return false;
    }
    UndoState undo;
    if (!make_move(move, undo)) {
        if (error) {
            *error = "Failed to apply move.";
        }
        return false;
    }
    if (error) {
        *error = "";
    }
    return true;
}

bool Position::make_move(const Move& move, UndoState& undo) {
    if (move.from >= 64 || move.to >= 64) {
        return false;
    }
    undo.move = move;
    undo.was_null_move = false;
    undo.side_to_move = side_to_move_;
    const Piece mover = board_[move.from];
    if (mover == Piece::None || piece_color(mover) != side_to_move_) {
        return false;
    }
    undo.moved_piece = mover;
    undo.captured_piece = Piece::None;
    undo.captured_square = kNoSquare;
    undo.castling_rights = castling_rights_;
    undo.en_passant_square = en_passant_square_;
    undo.halfmove_clock = halfmove_clock_;
    undo.fullmove_number = fullmove_number_;
    undo.hash = hash_;
    undo.repetition_size = repetition_history_.size();

    const Color us = side_to_move_;
    const Color them = opposite(us);
    const PieceType mover_type = piece_type(mover);
    if (!move.is_capture() && board_[move.to] != Piece::None) {
        return false;
    }

    xor_castling_hash(castling_rights_);
    xor_en_passant_hash(en_passant_square_);

    auto clear_rook_rights = [&](int square) {
        switch (square) {
            case 0:
                castling_rights_ &= ~kCastleWhiteQueen;
                break;
            case 7:
                castling_rights_ &= ~kCastleWhiteKing;
                break;
            case 56:
                castling_rights_ &= ~kCastleBlackQueen;
                break;
            case 63:
                castling_rights_ &= ~kCastleBlackKing;
                break;
            default:
                break;
        }
    };

    if (mover_type == PieceType::King) {
        castling_rights_ &= us == Color::White
            ? static_cast<std::uint8_t>(~(kCastleWhiteKing | kCastleWhiteQueen))
            : static_cast<std::uint8_t>(~(kCastleBlackKing | kCastleBlackQueen));
    } else if (mover_type == PieceType::Rook) {
        clear_rook_rights(move.from);
    }

    if (move.is_capture()) {
        undo.captured_square = move.flag == MoveFlag::EnPassant
            ? (us == Color::White ? move.to - 8 : move.to + 8)
            : move.to;
        clear_rook_rights(undo.captured_square);
        undo.captured_piece = board_[undo.captured_square];
        if (undo.captured_piece == Piece::None) {
            hash_ = undo.hash;
            castling_rights_ = undo.castling_rights;
            en_passant_square_ = undo.en_passant_square;
            return false;
        }
        if (piece_color(undo.captured_piece) == us) {
            hash_ = undo.hash;
            castling_rights_ = undo.castling_rights;
            en_passant_square_ = undo.en_passant_square;
            return false;
        }
        remove_piece(undo.captured_square, true);
    }

    remove_piece(move.from, true);

    if (move.flag == MoveFlag::KingCastle) {
        place_piece(move.to, mover, true);
        if (us == Color::White) {
            remove_piece(7, true);
            place_piece(5, Piece::WRook, true);
        } else {
            remove_piece(63, true);
            place_piece(61, Piece::BRook, true);
        }
    } else if (move.flag == MoveFlag::QueenCastle) {
        place_piece(move.to, mover, true);
        if (us == Color::White) {
            remove_piece(0, true);
            place_piece(3, Piece::WRook, true);
        } else {
            remove_piece(56, true);
            place_piece(59, Piece::BRook, true);
        }
    } else if (move.is_promotion()) {
        place_piece(move.to, make_piece(us, move.promotion), true);
    } else {
        place_piece(move.to, mover, true);
    }

    en_passant_square_ = kNoSquare;
    if (move.flag == MoveFlag::DoublePawnPush) {
        en_passant_square_ = us == Color::White ? move.from + 8 : move.from - 8;
    }

    if (mover_type == PieceType::Pawn || move.is_capture()) {
        halfmove_clock_ = 0;
    } else {
        halfmove_clock_ += 1;
    }

    if (us == Color::Black) {
        fullmove_number_ += 1;
    }

    xor_castling_hash(castling_rights_);
    xor_en_passant_hash(en_passant_square_);
    side_to_move_ = them;
    xor_side_hash();
    repetition_history_.push_back(hash_);
    return true;
}

bool Position::make_null_move(UndoState& undo) {
    undo.move = Move::null();
    undo.moved_piece = Piece::None;
    undo.captured_piece = Piece::None;
    undo.side_to_move = side_to_move_;
    undo.captured_square = kNoSquare;
    undo.castling_rights = castling_rights_;
    undo.en_passant_square = en_passant_square_;
    undo.halfmove_clock = halfmove_clock_;
    undo.fullmove_number = fullmove_number_;
    undo.hash = hash_;
    undo.repetition_size = repetition_history_.size();
    undo.was_null_move = true;

    if (in_check(side_to_move_)) {
        return false;
    }

    xor_en_passant_hash(en_passant_square_);
    en_passant_square_ = kNoSquare;
    halfmove_clock_ += 1;
    if (side_to_move_ == Color::Black) {
        fullmove_number_ += 1;
    }
    side_to_move_ = opposite(side_to_move_);
    xor_side_hash();
    repetition_history_.push_back(hash_);
    return true;
}

void Position::unmake_move(const UndoState& undo) {
    repetition_history_.resize(undo.repetition_size);
    if (undo.was_null_move) {
        side_to_move_ = undo.side_to_move;
        castling_rights_ = undo.castling_rights;
        en_passant_square_ = undo.en_passant_square;
        halfmove_clock_ = undo.halfmove_clock;
        fullmove_number_ = undo.fullmove_number;
        hash_ = undo.hash;
        return;
    }

    side_to_move_ = undo.side_to_move;
    castling_rights_ = undo.castling_rights;
    en_passant_square_ = undo.en_passant_square;
    halfmove_clock_ = undo.halfmove_clock;
    fullmove_number_ = undo.fullmove_number;

    const Move& move = undo.move;
    if (move.flag == MoveFlag::KingCastle) {
        remove_piece(move.to);
        place_piece(move.from, undo.moved_piece);
        if (side_to_move_ == Color::White) {
            remove_piece(5);
            place_piece(7, Piece::WRook);
        } else {
            remove_piece(61);
            place_piece(63, Piece::BRook);
        }
    } else if (move.flag == MoveFlag::QueenCastle) {
        remove_piece(move.to);
        place_piece(move.from, undo.moved_piece);
        if (side_to_move_ == Color::White) {
            remove_piece(3);
            place_piece(0, Piece::WRook);
        } else {
            remove_piece(59);
            place_piece(56, Piece::BRook);
        }
    } else if (move.is_promotion()) {
        remove_piece(move.to);
        place_piece(move.from, undo.moved_piece);
    } else {
        remove_piece(move.to);
        place_piece(move.from, undo.moved_piece);
    }

    if (undo.captured_piece != Piece::None && undo.captured_square != kNoSquare) {
        place_piece(undo.captured_square, undo.captured_piece);
    }
    hash_ = undo.hash;
}

bool Position::is_checkmate() const {
    Position copy = *this;
    return copy.in_check(copy.side_to_move_) && copy.legal_moves_fast(false).empty();
}

bool Position::is_stalemate() const {
    Position copy = *this;
    return !copy.in_check(copy.side_to_move_) && copy.legal_moves_fast(false).empty();
}

bool Position::is_draw_by_repetition() const {
    if (repetition_history_.size() < 3) {
        return false;
    }
    int matches = 1;
    const int max_back = std::min<int>(halfmove_clock_, static_cast<int>(repetition_history_.size()) - 1);
    for (int plies = 2; plies <= max_back; plies += 2) {
        const std::size_t index = repetition_history_.size() - 1 - plies;
        if (repetition_history_[index] == hash_) {
            matches += 1;
            if (matches >= 3) {
                return true;
            }
        }
    }
    return false;
}

bool Position::is_draw_by_fifty_move() const {
    return halfmove_clock_ >= 100;
}

bool Position::is_insufficient_material() const {
    int white_minor = 0;
    int black_minor = 0;
    int white_dark_bishop = 0;
    int white_light_bishop = 0;
    int black_dark_bishop = 0;
    int black_light_bishop = 0;

    for (int square = 0; square < 64; ++square) {
        const Piece piece = board_[square];
        if (piece == Piece::None || piece == Piece::WKing || piece == Piece::BKing) {
            continue;
        }
        const PieceType type = piece_type(piece);
        if (type == PieceType::Pawn || type == PieceType::Rook || type == PieceType::Queen) {
            return false;
        }
        if (is_white(piece)) {
            white_minor += 1;
            if (type == PieceType::Bishop) {
                if (((square_file(square) + square_rank(square)) & 1) == 0) {
                    white_dark_bishop += 1;
                } else {
                    white_light_bishop += 1;
                }
            }
        } else {
            black_minor += 1;
            if (type == PieceType::Bishop) {
                if (((square_file(square) + square_rank(square)) & 1) == 0) {
                    black_dark_bishop += 1;
                } else {
                    black_light_bishop += 1;
                }
            }
        }
    }

    if (white_minor == 0 && black_minor == 0) {
        return true;
    }
    if ((white_minor == 1 && black_minor == 0) || (white_minor == 0 && black_minor == 1)) {
        return true;
    }
    if (white_minor == 1 && black_minor == 1 &&
        (white_dark_bishop + white_light_bishop) == 1 &&
        (black_dark_bishop + black_light_bishop) == 1) {
        return (white_dark_bishop == 1 && black_dark_bishop == 1) ||
               (white_light_bishop == 1 && black_light_bishop == 1);
    }
    return false;
}

bool Position::is_draw() const {
    return is_stalemate() || is_draw_by_repetition() || is_draw_by_fifty_move() || is_insufficient_material();
}

}  // namespace deadfish

namespace deadfish {

namespace {

int mobility_weight(PieceType type) {
    switch (type) {
        case PieceType::Knight:
            return 4;
        case PieceType::Bishop:
            return 5;
        case PieceType::Rook:
            return 3;
        case PieceType::Queen:
            return 2;
        case PieceType::King:
            return 1;
        case PieceType::Pawn:
            return 1;
        case PieceType::None:
        default:
            return 0;
    }
}

int count_piece_mobility(const Position& position, int square, Piece piece) {
    const Color color = piece_color(piece);
    const PieceType type = piece_type(piece);
    const Bitboard own = position.occupancy(color);
    const Bitboard occupied = position.occupancy();

    if (type == PieceType::Knight) {
        return std::popcount(kKnightAttacks[static_cast<std::size_t>(square)] & ~own);
    }
    if (type == PieceType::King) {
        return std::popcount(kKingAttacks[static_cast<std::size_t>(square)] & ~own);
    }
    if (type == PieceType::Pawn) {
        return std::popcount(kPawnAttacks[static_cast<std::size_t>(color)][square]);
    }

    if (type == PieceType::Bishop || type == PieceType::Queen) {
        Bitboard attacks = bishop_attacks(square, occupied);
        if (type == PieceType::Queen) {
            attacks |= rook_attacks(square, occupied);
        }
        return std::popcount(attacks & ~own);
    }
    if (type == PieceType::Rook) {
        return std::popcount(rook_attacks(square, occupied) & ~own);
    }
    return 0;
}

Bitboard attacks_from_for_eval(const Position& position, int square, Piece piece);

int count_center_control(const Position& position, int square, Piece piece) {
    const PieceType type = piece_type(piece);
    const Color color = piece_color(piece);
    int score = is_center_square(square) ? 14 : is_extended_center_square(square) ? 6 : 0;
    auto add_if_center = [&](int target) {
        if (is_center_square(target)) {
            score += 4;
        } else if (is_extended_center_square(target)) {
            score += 2;
        }
    };

    if (type == PieceType::Knight) {
        Bitboard attacks = kKnightAttacks[square];
        while (attacks) {
            add_if_center(pop_lsb(attacks));
        }
        return score;
    }
    if (type == PieceType::King) {
        Bitboard attacks = kKingAttacks[square];
        while (attacks) {
            add_if_center(pop_lsb(attacks));
        }
        return score;
    }
    if (type == PieceType::Pawn) {
        Bitboard attacks = kPawnAttacks[static_cast<std::size_t>(color)][square];
        while (attacks) {
            add_if_center(pop_lsb(attacks));
        }
        return score;
    }

    Bitboard attacks = attacks_from_for_eval(position, square, piece);
    while (attacks) {
        add_if_center(pop_lsb(attacks));
    }
    return score;
}

Bitboard attacks_from_for_eval(const Position& position, int square, Piece piece) {
    const PieceType type = piece_type(piece);
    const Color color = piece_color(piece);
    if (type == PieceType::Knight) {
        return kKnightAttacks[square];
    }
    if (type == PieceType::King) {
        return kKingAttacks[square];
    }
    if (type == PieceType::Pawn) {
        return kPawnAttacks[static_cast<std::size_t>(color)][square];
    }
    if (type == PieceType::Bishop) {
        return bishop_attacks(square, position.occupancy());
    }
    if (type == PieceType::Rook) {
        return rook_attacks(square, position.occupancy());
    }
    if (type == PieceType::Queen) {
        return queen_attacks(square, position.occupancy());
    }
    return 0;
}

int king_attack_weight(PieceType type) {
    switch (type) {
        case PieceType::Pawn:
            return 6;
        case PieceType::Knight:
            return 16;
        case PieceType::Bishop:
            return 14;
        case PieceType::Rook:
            return 18;
        case PieceType::Queen:
            return 28;
        default:
            return 0;
    }
}

int count_king_ring_pressure(const Position& position, int king_square, Color attacker) {
    if (king_square == kNoSquare) {
        return 0;
    }
    int pressure = 0;
    int attackers = 0;
    Bitboard ring = kKingAttacks[king_square] | bit_at(king_square);
    const auto& board = position.board();
    for (int square = 0; square < 64; ++square) {
        const Piece piece = board[square];
        if (piece == Piece::None || piece_color(piece) != attacker) {
            continue;
        }
        const PieceType type = piece_type(piece);
        if (type == PieceType::King) {
            continue;
        }
        const int hits = std::popcount(attacks_from_for_eval(position, square, piece) & ring);
        if (hits > 0) {
            attackers += 1;
            pressure += hits * king_attack_weight(type);
        }
    }
    return std::min(140, pressure + attackers * attackers * 3);
}

int king_distance(int a, int b) {
    if (a == kNoSquare || b == kNoSquare) {
        return 4;
    }
    return std::max(std::abs(square_file(a) - square_file(b)), std::abs(square_rank(a) - square_rank(b)));
}

bool pawn_protected_by_color(const Position& position, int square, Color color) {
    const int file = square_file(square);
    if (color == Color::White) {
        return (file > 0 && square >= 9 && position.piece_at(square - 9) == Piece::WPawn) ||
               (file < 7 && square >= 7 && position.piece_at(square - 7) == Piece::WPawn);
    }
    return (file > 0 && square <= 55 && position.piece_at(square + 7) == Piece::BPawn) ||
           (file < 7 && square <= 54 && position.piece_at(square + 9) == Piece::BPawn);
}

bool connected_friendly_pawn(const Position& position, int square, Color color) {
    const Piece pawn = make_piece(color, PieceType::Pawn);
    const int file = square_file(square);
    const int rank = square_rank(square);
    for (int df : {-1, 1}) {
        const int next_file = file + df;
        if (next_file < 0 || next_file >= 8) {
            continue;
        }
        for (int dr = -1; dr <= 1; ++dr) {
            const int next_rank = rank + dr;
            if (next_rank >= 0 && next_rank < 8 && position.piece_at(make_square(next_file, next_rank)) == pawn) {
                return true;
            }
        }
    }
    return false;
}

bool enemy_pawn_can_challenge(const Position& position, int square, Color color) {
    const Piece enemy_pawn = make_piece(opposite(color), PieceType::Pawn);
    const int file = square_file(square);
    const int rank = square_rank(square);
    for (int scan_file = std::max(0, file - 1); scan_file <= std::min(7, file + 1); ++scan_file) {
        if (scan_file == file) {
            continue;
        }
        if (color == Color::White) {
            for (int scan_rank = rank + 1; scan_rank < 8; ++scan_rank) {
                if (position.piece_at(make_square(scan_file, scan_rank)) == enemy_pawn) {
                    return true;
                }
            }
        } else {
            for (int scan_rank = rank - 1; scan_rank >= 0; --scan_rank) {
                if (position.piece_at(make_square(scan_file, scan_rank)) == enemy_pawn) {
                    return true;
                }
            }
        }
    }
    return false;
}

struct ClassicalEvalBreakdown {
    int backbone = 0;
    int positional = 0;
};

ClassicalEvalBreakdown evaluate_classical_breakdown(const Position& position) {
    struct SideEval {
        int material_middle = 0;
        int material_endgame = 0;
        int material = 0;
        int positional_middle = 0;
        int positional_endgame = 0;
        int center = 0;
        int mobility = 0;
        int king_safety = 0;
        int pawn_structure = 0;
        int passed_pawns = 0;
        int rook_files = 0;
        int bishop_pair = 0;
        int outposts = 0;
        int threats = 0;
        int space = 0;
        int activity = 0;
        int trapped = 0;
        int simplification = 0;
        int king_square = kNoSquare;
        int bishops = 0;
        std::array<int, 8> pawn_files{};
        std::vector<int> pawns;
        std::vector<int> passed_pawn_squares;
        std::vector<int> knights;
        std::vector<int> bishop_squares;
        std::vector<int> rooks;
        std::vector<int> queens;
    };

    SideEval white;
    SideEval black;
    const auto& board = position.board();
    int raw_phase = 0;

    for (int square = 0; square < 64; ++square) {
        const Piece piece = board[square];
        if (piece == Piece::None) {
            continue;
        }
        const Color color = piece_color(piece);
        SideEval& side = color == Color::White ? white : black;
        const PieceType type = piece_type(piece);
        raw_phase += kPiecePhaseWeights[static_cast<std::size_t>(type)];
        side.material += kPieceValues[static_cast<std::size_t>(type)];
        side.material_middle += kPieceValues[static_cast<std::size_t>(type)];
        side.material_endgame += kPieceValuesEndgame[static_cast<std::size_t>(type)];

        switch (type) {
            case PieceType::Pawn:
                side.positional_middle += square_table_value(kPawnTable, square, color);
                side.positional_endgame += square_table_value(kPawnTable, square, color);
                side.pawn_files[square_file(square)] += 1;
                side.pawns.push_back(square);
                break;
            case PieceType::Knight:
                side.positional_middle += square_table_value(kKnightTable, square, color);
                side.positional_endgame += square_table_value(kKnightTable, square, color) / 2;
                side.knights.push_back(square);
                break;
            case PieceType::Bishop:
                side.positional_middle += square_table_value(kBishopTable, square, color);
                side.positional_endgame += square_table_value(kBishopTable, square, color);
                side.bishops += 1;
                side.bishop_squares.push_back(square);
                break;
            case PieceType::Rook:
                side.positional_middle += square_table_value(kRookTable, square, color);
                side.positional_endgame += square_table_value(kRookTable, square, color);
                side.rooks.push_back(square);
                break;
            case PieceType::Queen:
                side.positional_middle += square_table_value(kQueenTable, square, color);
                side.positional_endgame += square_table_value(kQueenTable, square, color);
                side.queens.push_back(square);
                break;
            case PieceType::King:
                side.positional_middle += square_table_value(kKingMiddleTable, square, color);
                side.positional_endgame += square_table_value(kKingEndTable, square, color);
                side.king_square = square;
                break;
            case PieceType::None:
            default:
                break;
        }

        side.mobility += count_piece_mobility(position, square, piece) * mobility_weight(type);
        side.center += count_center_control(position, square, piece);
    }

    if (white.bishops >= 2) {
        white.bishop_pair += 30;
    }
    if (black.bishops >= 2) {
        black.bishop_pair += 30;
    }

    auto evaluate_pawns = [&](Color color, SideEval& side, const SideEval& enemy) {
        for (int square : side.pawns) {
            const int file = square_file(square);
            const int rank = square_rank(square);
            const int advance = color == Color::White ? rank : 7 - rank;
            if (side.pawn_files[file] > 1) {
                side.pawn_structure -= 12 * (side.pawn_files[file] - 1);
            }
            const bool has_left = file > 0 && side.pawn_files[file - 1] > 0;
            const bool has_right = file < 7 && side.pawn_files[file + 1] > 0;
            if (!has_left && !has_right) {
                side.pawn_structure -= 14;
            }

            bool passed = true;
            for (int scan_file = std::max(0, file - 1); scan_file <= std::min(7, file + 1); ++scan_file) {
                if (enemy.pawn_files[scan_file] == 0) {
                    continue;
                }
                if (color == Color::White) {
                    for (int scan_rank = rank + 1; scan_rank < 8; ++scan_rank) {
                        if (board[make_square(scan_file, scan_rank)] == Piece::BPawn) {
                            passed = false;
                            break;
                        }
                    }
                } else {
                    for (int scan_rank = rank - 1; scan_rank >= 0; --scan_rank) {
                        if (board[make_square(scan_file, scan_rank)] == Piece::WPawn) {
                            passed = false;
                            break;
                        }
                    }
                }
                if (!passed) {
                    break;
                }
            }
            if (passed) {
                int bonus = kPassedPawnBonus[advance];
                const int forward = color == Color::White ? square + 8 : square - 8;
                const int promotion_square = make_square(file, color == Color::White ? 7 : 0);
                if (pawn_protected_by_color(position, square, color)) {
                    bonus += advance * 3;
                }
                if (connected_friendly_pawn(position, square, color)) {
                    bonus += advance * 2;
                }
                if (forward >= 0 && forward < 64 && board[forward] != Piece::None) {
                    bonus -= advance * 5;
                }
                bool path_clear = true;
                for (int scan = forward; scan >= 0 && scan < 64; scan += color == Color::White ? 8 : -8) {
                    if (board[scan] != Piece::None) {
                        path_clear = false;
                        break;
                    }
                }
                const int friendly_distance = king_distance(side.king_square, square);
                const int enemy_distance = king_distance(enemy.king_square, promotion_square);
                bonus += (enemy_distance - friendly_distance) * (advance >= 4 ? 4 : 2);
                if (path_clear) {
                    const int steps = color == Color::White ? 7 - rank : rank;
                    bonus += advance * 4;
                    if (enemy_distance > steps && friendly_distance <= steps + 1) {
                        bonus += 18 + advance * 5;
                    }
                }
                side.passed_pawns += bonus;
                side.passed_pawn_squares.push_back(square);
            }
        }
    };

    evaluate_pawns(Color::White, white, black);
    evaluate_pawns(Color::Black, black, white);

    auto evaluate_outposts = [&](Color color, SideEval& side) {
        auto score_minor = [&](int square, PieceType type) {
            const int rank = square_rank(square);
            const int advance = color == Color::White ? rank : 7 - rank;
            if (advance < 3 || !pawn_protected_by_color(position, square, color) ||
                enemy_pawn_can_challenge(position, square, color)) {
                return 0;
            }
            int bonus = type == PieceType::Knight ? 22 : 12;
            if (is_center_square(square)) {
                bonus += 8;
            } else if (is_extended_center_square(square)) {
                bonus += 4;
            }
            return bonus + std::max(0, advance - 3) * 3;
        };
        for (int square : side.knights) {
            side.outposts += score_minor(square, PieceType::Knight);
        }
        for (int square : side.bishop_squares) {
            side.outposts += score_minor(square, PieceType::Bishop);
        }
    };

    evaluate_outposts(Color::White, white);
    evaluate_outposts(Color::Black, black);

    auto evaluate_rooks = [&](Color color, SideEval& side, const SideEval& enemy) {
        for (int square : side.rooks) {
            const int file = square_file(square);
            const int rank = square_rank(square);
            const bool friendly_pawn = side.pawn_files[file] > 0;
            const bool enemy_pawn = enemy.pawn_files[file] > 0;
            if (!friendly_pawn && !enemy_pawn) {
                side.rook_files += 26;
            } else if (!friendly_pawn) {
                side.rook_files += 14;
            }
            if (is_center_square(square) || is_extended_center_square(square)) {
                side.rook_files += 4;
            }
            if ((color == Color::White && rank == 6) || (color == Color::Black && rank == 1)) {
                side.rook_files += 18;
                const int enemy_back_rank = color == Color::White ? 7 : 0;
                if (square_rank(enemy.king_square) == enemy_back_rank) {
                    side.rook_files += 8;
                }
            }
            for (int pawn_square : side.passed_pawn_squares) {
                if (square_file(pawn_square) != file) {
                    continue;
                }
                const int pawn_rank = square_rank(pawn_square);
                if ((color == Color::White && rank < pawn_rank) ||
                    (color == Color::Black && rank > pawn_rank)) {
                    side.rook_files += 14;
                    break;
                }
            }
        }
    };

    evaluate_rooks(Color::White, white, black);
    evaluate_rooks(Color::Black, black, white);

    auto evaluate_activity = [&](Color color, SideEval& side, const SideEval& enemy) {
        const Bitboard enemy_occupancy = position.occupancy(opposite(color));
        const Bitboard own_occupancy = position.occupancy(color);
        auto score_threats_from = [&](int square, PieceType attacker_type) {
            const int attacker_value = kPieceValues[static_cast<std::size_t>(attacker_type)];
            Bitboard attacks = attacks_from_for_eval(position, square, make_piece(color, attacker_type)) & enemy_occupancy;
            while (attacks) {
                const int target = pop_lsb(attacks);
                const Piece victim = board[target];
                if (victim == Piece::None || piece_type(victim) == PieceType::King) {
                    continue;
                }
                const int victim_value = kPieceValues[static_cast<std::size_t>(piece_type(victim))];
                int bonus = 3 + victim_value / 80;
                if (attacker_value < victim_value) {
                    bonus += 8;
                }
                if (pawn_protected_by_color(position, square, color)) {
                    bonus += 3;
                }
                side.threats += bonus;
            }
        };

        for (int square : side.pawns) {
            Bitboard attacks = kPawnAttacks[static_cast<std::size_t>(color)][static_cast<std::size_t>(square)] & enemy_occupancy;
            while (attacks) {
                const Piece victim = board[pop_lsb(attacks)];
                if (victim != Piece::None && piece_type(victim) != PieceType::Pawn && piece_type(victim) != PieceType::King) {
                    side.threats += 10 + kPieceValues[static_cast<std::size_t>(piece_type(victim))] / 100;
                }
            }
        }
        for (int square : side.knights) {
            score_threats_from(square, PieceType::Knight);
            if (std::popcount(kKnightAttacks[static_cast<std::size_t>(square)] & ~own_occupancy) <= 2) {
                side.trapped -= 12;
            }
        }
        for (int square : side.bishop_squares) {
            score_threats_from(square, PieceType::Bishop);
            if (std::popcount(bishop_attacks(square, position.occupancy()) & ~own_occupancy) <= 3) {
                side.trapped -= 10;
            }
        }
        for (int square : side.rooks) {
            score_threats_from(square, PieceType::Rook);
        }
        for (int square : side.queens) {
            score_threats_from(square, PieceType::Queen);
            const int file = square_file(square);
            if (side.pawn_files[file] == 0) {
                side.activity += enemy.pawn_files[file] == 0 ? 10 : 5;
            }
            const Bitboard enemy_king_ring = enemy.king_square == kNoSquare ? 0 : kKingAttacks[enemy.king_square] | bit_at(enemy.king_square);
            side.activity += std::popcount(queen_attacks(square, position.occupancy()) & enemy_king_ring) * 5;
        }

        for (int square = 16; square < 48; ++square) {
            if (!is_extended_center_square(square) || board[square] != Piece::None) {
                continue;
            }
            const int rank = square_rank(square);
            if ((color == Color::White && rank < 2) || (color == Color::Black && rank > 5)) {
                continue;
            }
            if ((kPawnAttacks[static_cast<std::size_t>(opposite(color))][static_cast<std::size_t>(square)] &
                 position.piece_bitboard(make_piece(color, PieceType::Pawn))) != 0 ||
                (kKnightAttacks[static_cast<std::size_t>(square)] &
                 position.piece_bitboard(make_piece(color, PieceType::Knight))) != 0 ||
                (bishop_attacks(square, position.occupancy()) &
                 (position.piece_bitboard(make_piece(color, PieceType::Bishop)) |
                  position.piece_bitboard(make_piece(color, PieceType::Queen)))) != 0) {
                side.space += 2;
            }
        }

        side.threats = std::min(side.threats, 160);
        side.space = std::min(side.space, 40);
    };

    evaluate_activity(Color::White, white, black);
    evaluate_activity(Color::Black, black, white);

    const int phase = std::min(24, raw_phase);
    auto evaluate_king = [&](Color color, SideEval& side, const SideEval& enemy) {
        if (side.king_square == kNoSquare) {
            return;
        }
        const int rank = square_rank(side.king_square);
        const int file = square_file(side.king_square);
        const int home_rank = color == Color::White ? 0 : 7;
        if (rank == home_rank && (file == 6 || file == 2)) {
            side.king_safety += 28;
        }
        const int shield_rank = color == Color::White ? rank + 1 : rank - 1;
        for (int delta = -1; delta <= 1; ++delta) {
            const int shield_file = file + delta;
            if (shield_rank < 0 || shield_rank >= 8 || shield_file < 0 || shield_file >= 8) {
                continue;
            }
            if (board[make_square(shield_file, shield_rank)] == make_piece(color, PieceType::Pawn)) {
                side.king_safety += 10;
            } else if (phase > 8) {
                side.king_safety -= 8;
            }
            const int far_shield_rank = color == Color::White ? shield_rank + 1 : shield_rank - 1;
            if (far_shield_rank >= 0 && far_shield_rank < 8 &&
                board[make_square(shield_file, far_shield_rank)] == make_piece(color, PieceType::Pawn)) {
                side.king_safety += 3;
            }
        }
        for (int open_file = std::max(0, file - 1); open_file <= std::min(7, file + 1); ++open_file) {
            const bool friendly_pawn = side.pawn_files[open_file] > 0;
            const bool enemy_pawn = enemy.pawn_files[open_file] > 0;
            if (!friendly_pawn && !enemy_pawn) {
                side.king_safety -= phase > 8 ? 20 : 6;
            } else if (!friendly_pawn) {
                side.king_safety -= phase > 8 ? 10 : 3;
            }
        }
        const int pressure = count_king_ring_pressure(position, side.king_square, opposite(color));
        side.king_safety -= (pressure * std::max(8, phase)) / 24;
        if (enemy.material > side.material && phase > 10) {
            side.king_safety -= 10;
        }
    };

    evaluate_king(Color::White, white, black);
    evaluate_king(Color::Black, black, white);

    if (phase <= 10) {
        const int white_edge = white.material - black.material;
        const int black_edge = black.material - white.material;
        if (white_edge > 0) {
            white.simplification += white_edge / 20;
        }
        if (black_edge > 0) {
            black.simplification += black_edge / 20;
        }
    }

    const int white_backbone_tapered = (white.material_middle * phase + white.material_endgame * (24 - phase)) / 24;
    const int black_backbone_tapered = (black.material_middle * phase + black.material_endgame * (24 - phase)) / 24;
    const int white_positional_tapered = (white.positional_middle * phase + white.positional_endgame * (24 - phase)) / 24;
    const int black_positional_tapered = (black.positional_middle * phase + black.positional_endgame * (24 - phase)) / 24;

    ClassicalEvalBreakdown breakdown;
    breakdown.backbone = white_backbone_tapered - black_backbone_tapered;
    breakdown.backbone += white.simplification - black.simplification;
    breakdown.backbone += position.side_to_move() == Color::White ? 10 : -10;

    breakdown.positional = white_positional_tapered - black_positional_tapered;
    breakdown.positional += white.mobility - black.mobility;
    breakdown.positional += white.center - black.center;
    breakdown.positional += white.king_safety - black.king_safety;
    breakdown.positional += white.pawn_structure - black.pawn_structure;
    breakdown.positional += white.passed_pawns - black.passed_pawns;
    breakdown.positional += white.rook_files - black.rook_files;
    breakdown.positional += white.bishop_pair - black.bishop_pair;
    breakdown.positional += white.outposts - black.outposts;
    breakdown.positional += white.threats - black.threats;
    breakdown.positional += white.space - black.space;
    breakdown.positional += white.activity - black.activity;
    breakdown.positional += white.trapped - black.trapped;
    const int pawn_count = static_cast<int>(white.pawns.size() + black.pawns.size());
    if (phase <= 6 && pawn_count == 0) {
        breakdown.backbone = breakdown.backbone * 70 / 100;
        breakdown.positional = breakdown.positional * 70 / 100;
    } else if (phase <= 4 && pawn_count <= 1 && std::abs(white.material - black.material) <= 330) {
        breakdown.backbone = breakdown.backbone * 80 / 100;
        breakdown.positional = breakdown.positional * 80 / 100;
    }
    return breakdown;
}

}  // namespace

int Position::evaluate_absolute() const {
    const ClassicalEvalBreakdown breakdown = evaluate_classical_breakdown(*this);
    return breakdown.backbone + breakdown.positional;
}

int Position::evaluate_relative() const {
    const int absolute = evaluate_absolute();
    return side_to_move_ == Color::White ? absolute : -absolute;
}

int Position::evaluate_backbone_absolute() const {
    return evaluate_classical_breakdown(*this).backbone;
}

int Position::evaluate_backbone_relative() const {
    const int absolute = evaluate_backbone_absolute();
    return side_to_move_ == Color::White ? absolute : -absolute;
}

int Position::evaluate_positional_absolute() const {
    return evaluate_classical_breakdown(*this).positional;
}

int Position::evaluate_positional_relative() const {
    const int absolute = evaluate_positional_absolute();
    return side_to_move_ == Color::White ? absolute : -absolute;
}

}  // namespace deadfish
