#include "deadfish/engine.hpp"

#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

using deadfish::Engine;
using deadfish::Move;
using deadfish::Position;
using deadfish::SearchInfo;
using deadfish::SearchLimits;
using deadfish::SearchResult;

struct CommandOptions {
    std::string fen = Position::start_position().to_fen();
    std::string moves;
    int depth = 5;
    int movetime_ms = 0;
    bool json = false;
    bool divide = false;
};

std::string json_escape(const std::string& input) {
    std::string escaped;
    for (char ch : input) {
        switch (ch) {
            case '\\':
                escaped += "\\\\";
                break;
            case '"':
                escaped += "\\\"";
                break;
            case '\n':
                escaped += "\\n";
                break;
            default:
                escaped += ch;
                break;
        }
    }
    return escaped;
}

void print_usage() {
    std::cout
        << "DeadFish CLI\n"
        << "Commands:\n"
        << "  play   [--fen FEN] [--depth N] [--movetime MS]\n"
        << "  search [--fen FEN] [--depth N] [--movetime MS] [--json]\n"
        << "  perft  [--fen FEN] --depth N [--divide]\n"
        << "  legal  [--fen FEN]\n"
        << "  fen    [--fen FEN] [--moves uci,uci,...]\n"
        << "  bench  [--depth N] [--movetime MS]\n";
}

bool parse_options(const std::vector<std::string>& args, CommandOptions& options, std::string& error) {
    for (std::size_t i = 0; i < args.size(); ++i) {
        const std::string& arg = args[i];
        if (arg == "--fen") {
            if (i + 1 >= args.size()) {
                error = "--fen requires a value.";
                return false;
            }
            options.fen = args[++i];
        } else if (arg == "--moves") {
            if (i + 1 >= args.size()) {
                error = "--moves requires a value.";
                return false;
            }
            options.moves = args[++i];
        } else if (arg == "--depth") {
            if (i + 1 >= args.size()) {
                error = "--depth requires a value.";
                return false;
            }
            options.depth = std::atoi(args[++i].c_str());
        } else if (arg == "--movetime") {
            if (i + 1 >= args.size()) {
                error = "--movetime requires a value.";
                return false;
            }
            options.movetime_ms = std::atoi(args[++i].c_str());
        } else if (arg == "--json") {
            options.json = true;
        } else if (arg == "--divide") {
            options.divide = true;
        } else {
            error = "Unknown option: " + arg;
            return false;
        }
    }
    return true;
}

bool load_position(const CommandOptions& options, Position& position) {
    std::string error;
    position = Position::from_fen(options.fen, &error);
    if (!error.empty()) {
        std::cerr << "FEN error: " << error << "\n";
        return false;
    }
    return true;
}

bool apply_option_moves(const CommandOptions& options, Position& position) {
    if (options.moves.empty()) {
        return true;
    }
    std::stringstream stream(options.moves);
    std::string move;
    while (std::getline(stream, move, ',')) {
        if (move.empty()) {
            continue;
        }
        std::string error;
        if (!position.apply_uci_move(move, &error)) {
            std::cerr << "Move error: " << error << "\n";
            return false;
        }
    }
    return true;
}

deadfish::SearchLimits make_limits(const CommandOptions& options) {
    SearchLimits limits;
    limits.max_depth = options.depth;
    limits.time_limit_ms = options.movetime_ms;
    return limits;
}

std::string describe_result(const Position& position) {
    if (position.is_checkmate()) {
        return position.side_to_move() == deadfish::Color::White ? "Black wins by checkmate." : "White wins by checkmate.";
    }
    if (position.is_stalemate()) {
        return "Draw by stalemate.";
    }
    if (position.is_draw_by_repetition()) {
        return "Draw by repetition.";
    }
    if (position.is_draw_by_fifty_move()) {
        return "Draw by fifty-move rule.";
    }
    if (position.is_insufficient_material()) {
        return "Draw by insufficient material.";
    }
    return "";
}

void print_search_json(const SearchResult& result) {
    std::cout
        << "{"
        << "\"bestMove\":\"" << json_escape(result.best_move.to_uci()) << "\","
        << "\"score\":" << result.score << ","
        << "\"scoreText\":\"" << json_escape(deadfish::score_to_string(result.score)) << "\","
        << "\"depth\":" << result.depth_reached << ","
        << "\"nodes\":" << result.nodes << ","
        << "\"nps\":" << result.nps << ","
        << "\"elapsedMs\":" << result.elapsed_ms << ","
        << "\"pv\":\"" << json_escape(deadfish::join_moves(result.pv)) << "\""
        << "}\n";
}

int command_search(const CommandOptions& options) {
    Position position;
    if (!load_position(options, position)) {
        return 1;
    }
    if (!apply_option_moves(options, position)) {
        return 1;
    }

    Engine engine;
    const SearchLimits limits = make_limits(options);
    SearchResult result = engine.search(position, limits, [&](const SearchInfo& info) {
        if (!options.json) {
            std::cout << "info depth " << info.depth
                      << " score " << deadfish::score_to_string(info.score)
                      << " nodes " << info.nodes
                      << " nps " << info.nps
                      << " pv " << deadfish::join_moves(info.pv)
                      << "\n";
        }
    });

    if (options.json) {
        print_search_json(result);
    } else {
        std::cout << "bestmove " << result.best_move.to_uci() << "\n";
        std::cout << "score    " << deadfish::score_to_string(result.score) << "\n";
        std::cout << "depth    " << result.depth_reached << "\n";
        std::cout << "nodes    " << result.nodes << "\n";
        std::cout << "nps      " << result.nps << "\n";
        std::cout << "elapsed  " << result.elapsed_ms << " ms\n";
        std::cout << "pv       " << deadfish::join_moves(result.pv) << "\n";
    }
    return 0;
}

int command_perft(const CommandOptions& options) {
    Position position;
    if (!load_position(options, position)) {
        return 1;
    }
    if (!apply_option_moves(options, position)) {
        return 1;
    }
    Engine engine;
    if (options.divide) {
        for (const auto& [move, nodes] : engine.divide(position, options.depth)) {
            std::cout << move.to_uci() << ": " << nodes << "\n";
        }
    }
    std::cout << "nodes " << engine.perft(position, options.depth) << "\n";
    return 0;
}

int command_legal(const CommandOptions& options) {
    Position position;
    if (!load_position(options, position)) {
        return 1;
    }
    if (!apply_option_moves(options, position)) {
        return 1;
    }
    std::cout << deadfish::join_moves(position.legal_moves()) << "\n";
    return 0;
}

int command_fen(const CommandOptions& options) {
    Position position;
    if (!load_position(options, position)) {
        return 1;
    }
    if (!apply_option_moves(options, position)) {
        return 1;
    }
    std::cout << position.to_fen() << "\n";
    return 0;
}

int command_bench(const CommandOptions& options) {
    Engine engine;
    std::uint64_t total_nodes = 0;
    std::uint64_t total_ms = 0;
    int index = 1;
    for (const std::string& fen : Engine::benchmark_positions()) {
        Position position = Position::from_fen(fen);
        SearchLimits limits = make_limits(options);
        SearchResult result = engine.search(position, limits);
        total_nodes += result.nodes;
        total_ms += result.elapsed_ms;
        std::cout << "bench[" << index++ << "] best " << result.best_move.to_uci()
                  << " score " << deadfish::score_to_string(result.score)
                  << " nodes " << result.nodes
                  << " nps " << result.nps << "\n";
    }
    std::cout << "total nodes " << total_nodes << "\n";
    std::cout << "total time  " << total_ms << " ms\n";
    return 0;
}

int command_play(const CommandOptions& options) {
    Position position;
    if (!load_position(options, position)) {
        return 1;
    }

    Engine engine;
    const deadfish::Color human = position.side_to_move();

    std::cout << position.pretty();
    std::cout << "Enter UCI moves, or commands: fen, moves, quit\n";

    while (true) {
        const std::string status = describe_result(position);
        if (!status.empty()) {
            std::cout << status << "\n";
            break;
        }

        if (position.side_to_move() == human) {
            std::cout << "you> ";
            std::string input;
            if (!std::getline(std::cin, input)) {
                break;
            }
            if (input == "quit") {
                break;
            }
            if (input == "fen") {
                std::cout << position.to_fen() << "\n";
                continue;
            }
            if (input == "moves") {
                std::cout << deadfish::join_moves(position.legal_moves()) << "\n";
                continue;
            }
            std::string error;
            if (!position.apply_uci_move(input, &error)) {
                std::cout << error << "\n";
                continue;
            }
            std::cout << position.pretty();
            continue;
        }

        std::cout << "engine thinking...\n";
        SearchResult result = engine.search(position, make_limits(options));
        if (result.best_move.is_null()) {
            std::cout << "engine had no legal move\n";
            break;
        }
        std::cout << "engine> " << result.best_move.to_uci()
                  << "  score " << deadfish::score_to_string(result.score)
                  << "  pv " << deadfish::join_moves(result.pv) << "\n";
        std::string error;
        if (!position.apply_uci_move(result.best_move.to_uci(), &error)) {
            std::cout << "failed to apply engine move: " << error << "\n";
            break;
        }
        std::cout << position.pretty();
    }
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        print_usage();
        return 0;
    }

    const std::string command = argv[1];
    std::vector<std::string> args;
    for (int i = 2; i < argc; ++i) {
        args.emplace_back(argv[i]);
    }

    CommandOptions options;
    std::string error;
    if (!parse_options(args, options, error)) {
        std::cerr << error << "\n";
        return 1;
    }

    if (command == "search") {
        return command_search(options);
    }
    if (command == "perft") {
        return command_perft(options);
    }
    if (command == "legal") {
        return command_legal(options);
    }
    if (command == "fen") {
        return command_fen(options);
    }
    if (command == "bench") {
        return command_bench(options);
    }
    if (command == "play") {
        return command_play(options);
    }

    print_usage();
    return 1;
}
