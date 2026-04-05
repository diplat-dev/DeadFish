#include "deadfish/engine.hpp"

#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

namespace {

using deadfish::Engine;
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
}

void test_perft(TestContext& t) {
    Engine engine;
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
    };
    for (const auto& test : cases) {
        Position position = Position::from_fen(test.fen);
        t.expect(engine.perft(position, test.depth) == test.expected, test.label);
    }
}

void test_search(TestContext& t) {
    Engine engine;
    Position mate_in_one = Position::from_fen("7k/6Q1/6K1/8/8/8/8/8 w - - 0 1");
    SearchLimits mate_limits;
    mate_limits.max_depth = 3;
    SearchResult mate_result = engine.search(mate_in_one, mate_limits);
    t.expect(mate_result.best_move.to_uci() == "g6f6", "search finds mate in one");

    Position start = Position::start_position();
    SearchLimits timed;
    timed.max_depth = 6;
    timed.time_limit_ms = 50;
    SearchResult timed_result = engine.search(start, timed);
    t.expect(!timed_result.best_move.is_null(), "time-limited search returns a move");
    t.expect(start.is_move_legal(timed_result.best_move), "time-limited search move is legal");
}

}  // namespace

int main() {
    TestContext t;
    test_fen_round_trip(t);
    test_make_unmake(t);
    test_special_moves(t);
    test_game_states(t);
    test_perft(t);
    test_search(t);

    std::cout << "\nPassed: " << t.passed << "\n";
    std::cout << "Failed: " << t.failed << "\n";
    return t.failed == 0 ? 0 : 1;
}
