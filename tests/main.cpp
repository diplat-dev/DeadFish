#include "deadfish/engine.hpp"

#include <bit>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <string>
#include <thread>
#include <vector>

namespace {

using deadfish::Engine;
using deadfish::EngineOptions;
using deadfish::Position;
using deadfish::SearchLimits;
using deadfish::SearchResult;

struct TestContext {
    int passed = 0;
    int failed = 0;

    void expect(bool condition, const std::string& label) {
        if (condition) {
            passed += 1;
            std::cout << "[PASS] " << label << "\n";
        } else {
            failed += 1;
            std::cout << "[FAIL] " << label << "\n";
        }
    }
};

bool contains_move(const Position& position, const std::string& uci) {
    for (const auto& move : position.legal_moves()) {
        if (move.to_uci() == uci) {
            return true;
        }
    }
    return false;
}

Engine make_search_engine() {
    Engine engine;
    EngineOptions options = engine.options();
    options.own_book = false;
    options.book_path.clear();
    options.syzygy_path.clear();
    engine.set_options(options);
    return engine;
}

constexpr int kFixtureFeatureCount = 64 * 10 * 64;

constexpr int make_square(int file, int rank) {
    return rank * 8 + file;
}

constexpr int mirror_square(int square) {
    return square ^ 56;
}

deadfish::Color fixture_piece_color(deadfish::Piece piece) {
    switch (piece) {
        case deadfish::Piece::BPawn:
        case deadfish::Piece::BKnight:
        case deadfish::Piece::BBishop:
        case deadfish::Piece::BRook:
        case deadfish::Piece::BQueen:
        case deadfish::Piece::BKing:
            return deadfish::Color::Black;
        case deadfish::Piece::WPawn:
        case deadfish::Piece::WKnight:
        case deadfish::Piece::WBishop:
        case deadfish::Piece::WRook:
        case deadfish::Piece::WQueen:
        case deadfish::Piece::WKing:
        case deadfish::Piece::None:
        default:
            return deadfish::Color::White;
    }
}

deadfish::PieceType fixture_piece_type(deadfish::Piece piece) {
    switch (piece) {
        case deadfish::Piece::WPawn:
        case deadfish::Piece::BPawn:
            return deadfish::PieceType::Pawn;
        case deadfish::Piece::WKnight:
        case deadfish::Piece::BKnight:
            return deadfish::PieceType::Knight;
        case deadfish::Piece::WBishop:
        case deadfish::Piece::BBishop:
            return deadfish::PieceType::Bishop;
        case deadfish::Piece::WRook:
        case deadfish::Piece::BRook:
            return deadfish::PieceType::Rook;
        case deadfish::Piece::WQueen:
        case deadfish::Piece::BQueen:
            return deadfish::PieceType::Queen;
        case deadfish::Piece::WKing:
        case deadfish::Piece::BKing:
            return deadfish::PieceType::King;
        case deadfish::Piece::None:
        default:
            return deadfish::PieceType::None;
    }
}

int fixture_piece_bucket(deadfish::Piece piece, deadfish::Color perspective) {
    const deadfish::PieceType type = fixture_piece_type(piece);
    if (piece == deadfish::Piece::None || type == deadfish::PieceType::King || type == deadfish::PieceType::None) {
        return -1;
    }

    const int color_offset = fixture_piece_color(piece) == perspective ? 0 : 5;
    int piece_offset = 0;
    switch (type) {
        case deadfish::PieceType::Pawn:
            piece_offset = 0;
            break;
        case deadfish::PieceType::Knight:
            piece_offset = 1;
            break;
        case deadfish::PieceType::Bishop:
            piece_offset = 2;
            break;
        case deadfish::PieceType::Rook:
            piece_offset = 3;
            break;
        case deadfish::PieceType::Queen:
            piece_offset = 4;
            break;
        case deadfish::PieceType::King:
        case deadfish::PieceType::None:
        default:
            return -1;
    }
    return color_offset + piece_offset;
}

int orient_fixture_square(int square, deadfish::Color perspective) {
    return perspective == deadfish::Color::White ? square : mirror_square(square);
}

int fixture_feature_index(deadfish::Color perspective, int king_square, deadfish::Piece piece, int square) {
    return orient_fixture_square(king_square, perspective) * (10 * 64)
        + fixture_piece_bucket(piece, perspective) * 64
        + orient_fixture_square(square, perspective);
}

void write_le_u32(std::ofstream& out, std::uint32_t value) {
    const unsigned char bytes[4] = {
        static_cast<unsigned char>(value & 0xFFu),
        static_cast<unsigned char>((value >> 8) & 0xFFu),
        static_cast<unsigned char>((value >> 16) & 0xFFu),
        static_cast<unsigned char>((value >> 24) & 0xFFu),
    };
    out.write(reinterpret_cast<const char*>(bytes), sizeof(bytes));
}

void write_le_f32(std::ofstream& out, float value) {
    write_le_u32(out, std::bit_cast<std::uint32_t>(value));
}

std::filesystem::path fixture_path(const std::string& name) {
    static const std::string run_id = std::to_string(
        std::chrono::steady_clock::now().time_since_epoch().count());
    return std::filesystem::temp_directory_path() / ("deadfish-" + run_id + "-" + name);
}

std::filesystem::path write_valid_nnue_fixture() {
    const std::filesystem::path path = fixture_path("deadfish-valid-fixture.nnue");
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    const std::array<char, 8> magic = {'D', 'F', 'N', 'N', 'U', 'E', '1', '\0'};
    out.write(magic.data(), static_cast<std::streamsize>(magic.size()));
    write_le_u32(out, kFixtureFeatureCount);
    write_le_u32(out, 1);
    write_le_u32(out, 2);
    write_le_f32(out, 100.0f);

    std::vector<float> feature_weights(kFixtureFeatureCount, 0.0f);
    const int white_king = make_square(4, 0);
    const int black_king = make_square(4, 7);
    const int white_queen_d4 = make_square(3, 3);
    const int black_queen_d5 = make_square(3, 4);
    feature_weights[fixture_feature_index(deadfish::Color::White, white_king, deadfish::Piece::WQueen, white_queen_d4)] = 0.40f;
    feature_weights[fixture_feature_index(deadfish::Color::Black, black_king, deadfish::Piece::WQueen, white_queen_d4)] = 0.10f;
    feature_weights[fixture_feature_index(deadfish::Color::White, white_king, deadfish::Piece::BQueen, black_queen_d5)] = 0.05f;
    feature_weights[fixture_feature_index(deadfish::Color::Black, black_king, deadfish::Piece::BQueen, black_queen_d5)] = 0.35f;
    for (float weight : feature_weights) {
        write_le_f32(out, weight);
    }

    write_le_f32(out, 0.0f);
    write_le_f32(out, 1.0f);
    write_le_f32(out, -1.0f);
    write_le_f32(out, -1.0f);
    write_le_f32(out, 1.0f);
    write_le_f32(out, 0.0f);
    write_le_f32(out, 0.0f);
    write_le_f32(out, 1.0f);
    write_le_f32(out, -1.0f);
    write_le_f32(out, 0.0f);
    return path;
}

std::filesystem::path write_wrong_magic_nnue_fixture() {
    const std::filesystem::path path = fixture_path("deadfish-wrong-magic.nnue");
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    const std::array<char, 8> magic = {'B', 'A', 'D', 'N', 'N', 'U', 'E', '\0'};
    out.write(magic.data(), static_cast<std::streamsize>(magic.size()));
    write_le_u32(out, kFixtureFeatureCount);
    write_le_u32(out, 1);
    write_le_u32(out, 2);
    return path;
}

std::filesystem::path write_truncated_nnue_fixture() {
    const std::filesystem::path path = fixture_path("deadfish-truncated.nnue");
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    const std::array<char, 8> magic = {'D', 'F', 'N', 'N', 'U', 'E', '1', '\0'};
    out.write(magic.data(), static_cast<std::streamsize>(magic.size()));
    write_le_u32(out, kFixtureFeatureCount);
    write_le_u32(out, 1);
    write_le_u32(out, 2);
    write_le_f32(out, 100.0f);
    write_le_f32(out, 0.40f);
    return path;
}

std::filesystem::path write_bad_shape_nnue_fixture() {
    const std::filesystem::path path = fixture_path("deadfish-bad-shape.nnue");
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    const std::array<char, 8> magic = {'D', 'F', 'N', 'N', 'U', 'E', '1', '\0'};
    out.write(magic.data(), static_cast<std::streamsize>(magic.size()));
    write_le_u32(out, 123);
    write_le_u32(out, 1);
    write_le_u32(out, 1);
    write_le_f32(out, 100.0f);
    return path;
}

std::filesystem::path write_clipped_accumulator_nnue_fixture() {
    const std::filesystem::path path = fixture_path("deadfish-clipped-acc.nnue");
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    const std::array<char, 8> magic = {'D', 'F', 'N', 'N', 'U', 'E', '1', '\0'};
    out.write(magic.data(), static_cast<std::streamsize>(magic.size()));
    write_le_u32(out, kFixtureFeatureCount);
    write_le_u32(out, 1);
    write_le_u32(out, 1);
    write_le_f32(out, 100.0f);

    std::vector<float> feature_weights(kFixtureFeatureCount, 0.0f);
    const int white_king = make_square(4, 0);
    const int white_queen_d4 = make_square(3, 3);
    feature_weights[fixture_feature_index(deadfish::Color::White, white_king, deadfish::Piece::WQueen, white_queen_d4)] = 2.5f;
    for (float weight : feature_weights) {
        write_le_f32(out, weight);
    }

    write_le_f32(out, 0.0f);   // acc bias
    write_le_f32(out, 0.5f);   // hidden weight for first accumulator lane
    write_le_f32(out, 0.0f);   // hidden weight for second accumulator lane
    write_le_f32(out, 0.0f);   // hidden bias
    write_le_f32(out, 1.0f);   // output weight
    write_le_f32(out, 0.0f);   // output bias
    return path;
}

void test_fen_round_trip(TestContext& t) {
    const std::vector<std::string> fens = {
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQ1BPPP/R1B1K2R w KQkq - 0 1",
        "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1",
    };
    for (const std::string& fen : fens) {
        std::string error;
        Position position = Position::from_fen(fen, &error);
        t.expect(error.empty(), "FEN parsed: " + fen);
        t.expect(position.to_fen() == fen, "FEN round-trip: " + fen);
    }
}

void test_make_unmake(TestContext& t) {
    const std::vector<std::string> fens = {
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
    };
    for (const std::string& fen : fens) {
        Position position = Position::from_fen(fen);
        const std::string original_fen = position.to_fen();
        const std::uint64_t original_hash = position.hash();
        for (const auto& move : position.legal_moves()) {
            deadfish::UndoState undo;
            position.make_move(move, undo);
            position.unmake_move(undo);
            t.expect(position.to_fen() == original_fen, "make/unmake FEN restored for " + move.to_uci());
            t.expect(position.hash() == original_hash, "make/unmake hash restored for " + move.to_uci());
        }
    }
}

void test_null_move_unmake(TestContext& t) {
    Position position = Position::from_fen("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2");
    const std::string original_fen = position.to_fen();
    const std::uint64_t original_hash = position.hash();
    deadfish::UndoState undo;
    t.expect(position.make_null_move(undo), "null move succeeds outside check");
    position.unmake_move(undo);
    t.expect(position.to_fen() == original_fen, "null move restores FEN");
    t.expect(position.hash() == original_hash, "null move restores hash");
}

void test_special_moves(TestContext& t) {
    Position castling = Position::from_fen("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1");
    t.expect(contains_move(castling, "e1g1"), "white king-side castling generated");
    t.expect(contains_move(castling, "e1c1"), "white queen-side castling generated");

    Position en_passant = Position::from_fen("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1");
    t.expect(contains_move(en_passant, "e5d6"), "en-passant generated");

    Position promotion = Position::from_fen("7k/P7/8/8/8/8/8/K7 w - - 0 1");
    t.expect(contains_move(promotion, "a7a8q"), "queen promotion generated");
    t.expect(contains_move(promotion, "a7a8r"), "rook promotion generated");
    t.expect(contains_move(promotion, "a7a8b"), "bishop promotion generated");
    t.expect(contains_move(promotion, "a7a8n"), "knight promotion generated");
}

void test_game_states(TestContext& t) {
    Position mate = Position::from_fen("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1");
    t.expect(mate.is_checkmate(), "checkmate detected");

    Position stalemate = Position::from_fen("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1");
    t.expect(stalemate.is_stalemate(), "stalemate detected");

    Position insufficient = Position::from_fen("8/8/8/8/8/8/5k2/6K1 w - - 0 1");
    t.expect(insufficient.is_insufficient_material(), "insufficient material detected");

    Position fifty = Position::from_fen("8/8/8/8/8/8/5k2/6K1 w - - 100 75");
    t.expect(fifty.is_draw_by_fifty_move(), "fifty-move draw detected");

    Position repetition = Position::start_position();
    std::string error;
    for (const std::string& move : {"g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1", "f6g8"}) {
        t.expect(repetition.apply_uci_move(move, &error), "repetition move applied: " + move);
        t.expect(error.empty(), "repetition move valid: " + move);
    }
    t.expect(repetition.is_draw_by_repetition(), "repetition draw detected");
}

void test_perft(TestContext& t) {
    Engine engine;
    EngineOptions options = engine.options();
    options.own_book = false;
    engine.set_options(options);

    struct Case {
        std::string fen;
        int depth;
        std::uint64_t expected;
        std::string label;
    };
    const std::vector<Case> cases = {
        {"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 1, 20, "start perft depth 1"},
        {"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 2, 400, "start perft depth 2"},
        {"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 3, 8902, "start perft depth 3"},
        {"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 4, 197281, "start perft depth 4"},
        {"r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", 1, 48, "kiwipete depth 1"},
        {"r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", 2, 2039, "kiwipete depth 2"},
        {"r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", 3, 97862, "kiwipete depth 3"},
        {"8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", 1, 14, "position 3 depth 1"},
        {"8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", 2, 191, "position 3 depth 2"},
        {"8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", 3, 2812, "position 3 depth 3"},
        {"rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", 1, 44, "promotion/check perft depth 1"},
        {"rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", 2, 1486, "promotion/check perft depth 2"},
        {"r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10", 1, 46, "pin/evasion perft depth 1"},
        {"r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10", 2, 2079, "pin/evasion perft depth 2"},
    };
    for (const auto& test : cases) {
        Position position = Position::from_fen(test.fen);
        t.expect(engine.perft(position, test.depth) == test.expected, test.label);
    }
}

void test_see(TestContext& t) {
    {
        Position position = Position::from_fen("4k3/8/8/8/3r4/4Q3/8/4K3 w - - 0 1");
        deadfish::Move move = position.parse_uci_move("e3d4");
        t.expect(deadfish::static_exchange_eval(position, move) > 0, "SEE winning capture is positive");
    }
    {
        Position position = Position::from_fen("4k3/8/8/4p3/3r4/3R4/8/4K3 w - - 0 1");
        deadfish::Move move = position.parse_uci_move("d3d4");
        t.expect(deadfish::static_exchange_eval(position, move) == 0, "SEE equal exchange is zero");
    }
    {
        Position position = Position::from_fen("4r1k1/8/8/4p3/8/8/4Q3/4K3 w - - 0 1");
        deadfish::Move move = position.parse_uci_move("e2e5");
        t.expect(deadfish::static_exchange_eval(position, move) < 0, "SEE losing capture is negative");
    }
}

void test_search(TestContext& t) {
    Engine engine = make_search_engine();

    Position mate_in_one = Position::from_fen("7k/6Q1/6K1/8/8/8/8/8 w - - 0 1");
    SearchLimits mate_limits;
    mate_limits.max_depth = 3;
    SearchResult mate_result = engine.search(mate_in_one, mate_limits);
    t.expect(!mate_result.best_move.is_null(), "search finds a mate-in-one move");
    std::string mate_error;
    Position mate_after = mate_in_one;
    t.expect(mate_after.apply_uci_move(mate_result.best_move.to_uci(), &mate_error), "mate-in-one move applies");
    t.expect(mate_error.empty() && mate_after.is_checkmate() && mate_result.score > 90000, "search scores mate in one");

    Position start = Position::start_position();
    SearchLimits timed;
    timed.max_depth = 6;
    timed.time_limit_ms = 50;
    SearchResult timed_result = engine.search(start, timed);
    t.expect(!timed_result.best_move.is_null(), "time-limited search returns a move");
    t.expect(start.is_move_legal(timed_result.best_move), "time-limited search move is legal");

    SearchLimits clocked;
    clocked.max_depth = 8;
    clocked.white_time_ms = 100;
    clocked.black_time_ms = 100;
    clocked.white_increment_ms = 10;
    clocked.black_increment_ms = 10;
    clocked.moves_to_go = 10;
    SearchResult clocked_result = engine.search(start, clocked);
    t.expect(!clocked_result.best_move.is_null(), "clock-based search returns a move");
    t.expect(start.is_move_legal(clocked_result.best_move), "clock-based search move is legal");

    SearchLimits node_limited;
    node_limited.max_depth = 64;
    node_limited.max_nodes = 500;
    SearchResult node_result = engine.search(start, node_limited);
    t.expect(!node_result.best_move.is_null(), "node-limited search returns a move");
    t.expect(start.is_move_legal(node_result.best_move), "node-limited search move is legal");
    t.expect(node_result.nodes >= 1 && node_result.nodes <= 500 + 512, "node-limited search respects the node budget approximately");
    t.expect(start.evaluate_backbone_relative() + start.evaluate_positional_relative() == start.evaluate_relative(),
             "classical eval splits into backbone plus positional terms");

    Position repeated = Position::start_position();
    std::string repetition_error;
    for (const std::string& move : {"g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1", "f6g8"}) {
        t.expect(repeated.apply_uci_move(move, &repetition_error), "search repetition move applied: " + move);
        t.expect(repetition_error.empty(), "search repetition move valid: " + move);
    }
    t.expect(repeated.hash() == start.hash(), "repetition search position matches start hash");
    t.expect(repeated.is_draw_by_repetition(), "repetition search position is drawable");

    SearchLimits shallow;
    shallow.max_depth = 1;
    SearchResult baseline = engine.search(start, shallow);
    t.expect(!baseline.best_move.is_null(), "baseline root search returns a move");
    SearchResult repeated_result = engine.search(repeated, shallow);
    t.expect(repeated_result.score == 0, "repetition root search scores draw even with TT history");

    for (int threads : {2, 4}) {
        Engine threaded_engine = make_search_engine();
        EngineOptions threaded_options = threaded_engine.options();
        threaded_options.threads = threads;
        threaded_engine.set_options(threaded_options);

        SearchLimits threaded_limits;
        threaded_limits.max_depth = 5;
        SearchResult threaded_result = threaded_engine.search(mate_in_one, threaded_limits);
        t.expect(!threaded_result.best_move.is_null(), "threaded search returns a move at Threads=" + std::to_string(threads));
        t.expect(mate_in_one.is_move_legal(threaded_result.best_move),
                 "threaded search move is legal at Threads=" + std::to_string(threads));

        SearchLimits threaded_nodes = node_limited;
        SearchResult threaded_node_result = threaded_engine.search(start, threaded_nodes);
        t.expect(!threaded_node_result.best_move.is_null(),
                 "threaded node-limited search returns a move at Threads=" + std::to_string(threads));
        t.expect(start.is_move_legal(threaded_node_result.best_move),
                 "threaded node-limited move is legal at Threads=" + std::to_string(threads));
        t.expect(threaded_node_result.nodes >= 1 && threaded_node_result.nodes <= 500 + 2048,
                 "threaded node-limited search respects the global budget approximately at Threads=" + std::to_string(threads));
    }

    {
        Engine threaded_engine = make_search_engine();
        EngineOptions threaded_options = threaded_engine.options();
        threaded_options.threads = 2;
        threaded_engine.set_options(threaded_options);

        for (int iteration = 0; iteration < 2; ++iteration) {
            SearchLimits infinite;
            infinite.infinite = true;
            SearchResult async_result;
            std::thread search_thread([&] {
                async_result = threaded_engine.search(start, infinite);
            });
            std::this_thread::sleep_for(std::chrono::milliseconds(120));
            threaded_engine.request_stop();
            search_thread.join();
            t.expect(!async_result.best_move.is_null(),
                     "threaded infinite search stops with a move on iteration " + std::to_string(iteration + 1));
            t.expect(start.is_move_legal(async_result.best_move),
                     "threaded infinite search move is legal on iteration " + std::to_string(iteration + 1));
            threaded_engine.clear_stop_request();
        }
    }
}

void test_nnue_loader_and_eval(TestContext& t) {
    const std::filesystem::path valid_fixture = write_valid_nnue_fixture();
    const std::filesystem::path wrong_magic_fixture = write_wrong_magic_nnue_fixture();
    const std::filesystem::path truncated_fixture = write_truncated_nnue_fixture();
    const std::filesystem::path bad_shape_fixture = write_bad_shape_nnue_fixture();
    const std::filesystem::path clipped_acc_fixture = write_clipped_accumulator_nnue_fixture();

    auto cleanup = [&]() {
        std::error_code ignored;
        std::filesystem::remove(valid_fixture, ignored);
        std::filesystem::remove(wrong_magic_fixture, ignored);
        std::filesystem::remove(truncated_fixture, ignored);
        std::filesystem::remove(bad_shape_fixture, ignored);
        std::filesystem::remove(clipped_acc_fixture, ignored);
    };

    {
        Engine engine = make_search_engine();
        EngineOptions options = engine.options();
        options.use_nnue = true;
        options.eval_file = valid_fixture.string();
        engine.set_options(options);

        t.expect(engine.nnue_loaded(), "NNUE valid fixture loads");
        t.expect(engine.nnue_status().find("Loaded NNUE from") != std::string::npos, "NNUE load status is reported");

        Position white_advantage = Position::from_fen("4k3/8/8/8/3Q4/8/8/4K3 w - - 0 1");
        Position white_advantage_black_to_move = Position::from_fen("4k3/8/8/8/3Q4/8/8/4K3 b - - 0 1");
        Position black_advantage = Position::from_fen("4k3/8/8/3q4/8/8/8/4K3 w - - 0 1");
        Position balanced = Position::from_fen("4k3/8/8/3q4/3Q4/8/8/4K3 w - - 0 1");

        t.expect(engine.evaluate_nnue_residual(white_advantage) == 30, "NNUE residual evaluates white queen fixture as +30");
        t.expect(engine.evaluate_nnue_residual(white_advantage_black_to_move) == -30, "NNUE residual flips score by side to move");
        t.expect(engine.evaluate_nnue_residual(black_advantage) == -30, "NNUE residual evaluates black queen fixture as -30");
        t.expect(engine.evaluate_nnue_residual(balanced) == 0, "NNUE residual evaluates balanced fixture as 0");
        t.expect(engine.evaluate(white_advantage) == engine.evaluate_backbone(white_advantage) + 30,
                 "hybrid eval adds NNUE residual to the classical backbone");

        SearchLimits limits;
        limits.max_depth = 2;
        SearchResult result = engine.search(white_advantage, limits);
        t.expect(!result.best_move.is_null(), "NNUE-active search returns a move");
        t.expect(white_advantage.is_move_legal(result.best_move), "NNUE-active search move is legal");

        options.eval_file = "Z:/deadfish-missing/fixture.nnue";
        engine.set_options(options);
        t.expect(!engine.nnue_loaded(), "invalid NNUE path unloads active network");
        t.expect(engine.nnue_status().find("load failed") != std::string::npos, "invalid NNUE path reports fallback");
        t.expect(engine.evaluate(white_advantage) == white_advantage.evaluate_relative(),
                 "invalid NNUE path falls back to classical evaluation");
    }

    {
        Engine engine = make_search_engine();
        EngineOptions options = engine.options();
        options.eval_file = wrong_magic_fixture.string();
        engine.set_options(options);
        Position position = Position::from_fen("4k3/8/8/8/3Q4/8/8/4K3 w - - 0 1");
        t.expect(!engine.nnue_loaded(), "wrong-magic NNUE fixture is rejected");
        t.expect(engine.nnue_status().find("wrong magic") != std::string::npos, "wrong-magic rejection explains the failure");
        t.expect(engine.evaluate(position) == position.evaluate_relative(), "wrong-magic fixture falls back to classical eval");
    }

    {
        Engine engine = make_search_engine();
        EngineOptions options = engine.options();
        options.eval_file = truncated_fixture.string();
        engine.set_options(options);
        Position position = Position::from_fen("4k3/8/8/8/3Q4/8/8/4K3 w - - 0 1");
        t.expect(!engine.nnue_loaded(), "truncated NNUE fixture is rejected");
        t.expect(engine.nnue_status().find("truncated") != std::string::npos, "truncated NNUE rejection explains the failure");
        t.expect(engine.evaluate(position) == position.evaluate_relative(), "truncated NNUE fixture falls back to classical eval");
    }

    {
        Engine engine = make_search_engine();
        EngineOptions options = engine.options();
        options.eval_file = bad_shape_fixture.string();
        engine.set_options(options);
        Position position = Position::from_fen("4k3/8/8/8/3Q4/8/8/4K3 w - - 0 1");
        t.expect(!engine.nnue_loaded(), "bad-shape NNUE fixture is rejected");
        t.expect(engine.nnue_status().find("unsupported network dimensions") != std::string::npos,
                 "bad-shape NNUE rejection explains the failure");
        t.expect(engine.evaluate(position) == position.evaluate_relative(), "bad-shape NNUE fixture falls back to classical eval");
    }

    {
        Engine engine = make_search_engine();
        EngineOptions options = engine.options();
        options.eval_file = valid_fixture.string();
        options.use_nnue = false;
        engine.set_options(options);
        Position position = Position::from_fen("4k3/8/8/8/3Q4/8/8/4K3 w - - 0 1");
        t.expect(engine.nnue_loaded(), "UseNNUE=false still allows a network to load");
        t.expect(engine.nnue_status().find("NNUE inactive because UseNNUE=false") != std::string::npos,
                 "UseNNUE=false reports inactive NNUE");
        t.expect(engine.evaluate(position) == position.evaluate_relative(), "UseNNUE=false keeps classical evaluation active");
    }

    {
        Engine engine = make_search_engine();
        EngineOptions options = engine.options();
        options.use_nnue = true;
        options.eval_file = clipped_acc_fixture.string();
        engine.set_options(options);
        Position position = Position::from_fen("4k3/8/8/8/3Q4/8/8/4K3 w - - 0 1");
        t.expect(engine.nnue_loaded(), "clipped-accumulator NNUE fixture loads");
        t.expect(engine.evaluate_nnue_residual(position) == 50, "NNUE clips accumulator activations before the hidden layer");
        t.expect(engine.evaluate(position) == engine.evaluate_backbone(position) + 50,
                 "hybrid eval uses the clipped NNUE residual");
    }

    cleanup();
}

void test_book_and_tablebase_fallbacks(TestContext& t) {
    Position start = Position::start_position();
    SearchLimits limits;
    limits.max_depth = 1;

    {
        Engine engine;
        SearchResult result = engine.search(start, limits);
        t.expect(result.used_book, "default book is used from the start position");
        t.expect(start.is_move_legal(result.best_move), "book move is legal");
    }

    {
        Engine engine = make_search_engine();
        SearchResult result = engine.search(start, limits);
        t.expect(!result.used_book, "book can be disabled");
        t.expect(start.is_move_legal(result.best_move), "search move is legal when book is disabled");
    }

    {
        Engine engine = make_search_engine();
        EngineOptions options = engine.options();
        options.own_book = true;
        options.book_path = "Z:/deadfish-missing/book.bin";
        engine.set_options(options);
        SearchResult result = engine.search(start, limits);
        t.expect(!result.used_book, "missing book falls back cleanly");
        t.expect(start.is_move_legal(result.best_move), "fallback move is legal when book path is invalid");
    }

    {
        Engine engine = make_search_engine();
        EngineOptions options = engine.options();
        options.syzygy_path = "Z:/deadfish-missing/syzygy";
        options.syzygy_probe_limit = 6;
        engine.set_options(options);
        Position kqk = Position::from_fen("6k1/8/8/8/8/8/6K1/7Q w - - 0 1");
        SearchResult result = engine.search(kqk, limits);
        t.expect(!result.used_tablebase, "missing syzygy path falls back cleanly");
        t.expect(kqk.is_move_legal(result.best_move), "fallback move is legal when syzygy path is invalid");
    }
}

}  // namespace

int main() {
    TestContext t;
    test_fen_round_trip(t);
    test_make_unmake(t);
    test_null_move_unmake(t);
    test_special_moves(t);
    test_game_states(t);
    test_perft(t);
    test_see(t);
    test_search(t);
    test_nnue_loader_and_eval(t);
    test_book_and_tablebase_fallbacks(t);

    std::cout << "\nPassed: " << t.passed << "\n";
    std::cout << "Failed: " << t.failed << "\n";
    return t.failed == 0 ? 0 : 1;
}
