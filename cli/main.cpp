#include "deadfish/engine.hpp"

#include <algorithm>
#include <atomic>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {

using deadfish::Engine;
using deadfish::EngineOptions;
using deadfish::Move;
using deadfish::Position;
using deadfish::SearchInfo;
using deadfish::SearchLimits;
using deadfish::SearchResult;

constexpr int kMateScoreBase = 100000;

struct CommandOptions {
    std::string fen = Position::start_position().to_fen();
    std::string moves;
    int depth = 5;
    bool has_depth = false;
    int movetime_ms = 0;
    bool json = false;
    bool divide = false;
    bool has_use_nnue = false;
    bool use_nnue = true;
    bool has_eval_file = false;
    std::string eval_file;
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

std::string join_tokens(const std::vector<std::string>& tokens, std::size_t start = 0) {
    std::ostringstream out;
    for (std::size_t index = start; index < tokens.size(); ++index) {
        if (index > start) {
            out << ' ';
        }
        out << tokens[index];
    }
    return out.str();
}

std::vector<std::string> split_words(const std::string& line) {
    std::vector<std::string> tokens;
    std::istringstream stream(line);
    std::string token;
    while (stream >> token) {
        tokens.push_back(token);
    }
    return tokens;
}

std::string lower_copy(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

bool iequals(const std::string& lhs, const std::string& rhs) {
    return lower_copy(lhs) == lower_copy(rhs);
}

bool parse_int(const std::string& text, int& value) {
    if (text.empty()) {
        return false;
    }
    char* end = nullptr;
    const long parsed = std::strtol(text.c_str(), &end, 10);
    if (end == nullptr || *end != '\0') {
        return false;
    }
    value = static_cast<int>(parsed);
    return true;
}

bool parse_bool(const std::string& text, bool& value) {
    if (iequals(text, "true") || iequals(text, "1") || iequals(text, "yes") || iequals(text, "on")) {
        value = true;
        return true;
    }
    if (iequals(text, "false") || iequals(text, "0") || iequals(text, "no") || iequals(text, "off")) {
        value = false;
        return true;
    }
    return false;
}

std::string uci_score(const int score) {
    if (std::abs(score) >= kMateScoreBase - 256) {
        const int mate_in = std::max(1, (kMateScoreBase - std::abs(score) + 1) / 2);
        return "mate " + std::to_string(score > 0 ? mate_in : -mate_in);
    }
    return "cp " + std::to_string(score);
}

void print_usage() {
    std::cout
        << "DeadFish\n"
        << "Run without arguments to start the UCI protocol loop.\n"
        << "Commands:\n"
        << "  play   [--fen FEN] [--depth N] [--movetime MS]\n"
        << "  search [--fen FEN] [--depth N] [--movetime MS] [--json]\n"
        << "  eval   [--fen FEN] [--moves uci,uci,...] [--json] [--use-nnue BOOL] [--eval-file PATH]\n"
        << "  perft  [--fen FEN] --depth N [--divide]\n"
        << "  legal  [--fen FEN]\n"
        << "  status [--fen FEN] [--moves uci,uci,...] [--json]\n"
        << "  fen    [--fen FEN] [--moves uci,uci,...]\n"
        << "  bench  [--depth N] [--movetime MS]\n"
        << "  uci    Start the UCI protocol loop explicitly\n";
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
            options.has_depth = true;
        } else if (arg == "--movetime") {
            if (i + 1 >= args.size()) {
                error = "--movetime requires a value.";
                return false;
            }
            options.movetime_ms = std::atoi(args[++i].c_str());
        } else if (arg == "--use-nnue") {
            if (i + 1 >= args.size()) {
                error = "--use-nnue requires a value.";
                return false;
            }
            bool parsed = false;
            if (!parse_bool(args[++i], parsed)) {
                error = "--use-nnue requires true or false.";
                return false;
            }
            options.has_use_nnue = true;
            options.use_nnue = parsed;
        } else if (arg == "--eval-file") {
            if (i + 1 >= args.size()) {
                error = "--eval-file requires a value.";
                return false;
            }
            options.has_eval_file = true;
            options.eval_file = args[++i];
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

SearchLimits make_limits(const CommandOptions& options) {
    SearchLimits limits;
    limits.max_depth = options.has_depth ? options.depth : (options.movetime_ms > 0 ? 0 : options.depth);
    limits.time_limit_ms = options.movetime_ms;
    return limits;
}

void apply_engine_overrides(const CommandOptions& options, Engine& engine) {
    if (!options.has_use_nnue && !options.has_eval_file) {
        return;
    }

    EngineOptions engine_options = engine.options();
    if (options.has_use_nnue) {
        engine_options.use_nnue = options.use_nnue;
    }
    if (options.has_eval_file) {
        engine_options.eval_file = options.eval_file;
    }
    engine.set_options(engine_options);
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
        << "\"usedBook\":" << (result.used_book ? "true" : "false") << ","
        << "\"usedTablebase\":" << (result.used_tablebase ? "true" : "false") << ","
        << "\"pv\":\"" << json_escape(deadfish::join_moves(result.pv)) << "\""
        << "}\n";
}

void print_status_json(const Position& position) {
    std::cout
        << "{"
        << "\"fen\":\"" << json_escape(position.to_fen()) << "\","
        << "\"turn\":\"" << (position.side_to_move() == deadfish::Color::White ? "w" : "b") << "\","
        << "\"inCheck\":" << (position.in_check(position.side_to_move()) ? "true" : "false") << ","
        << "\"checkmate\":" << (position.is_checkmate() ? "true" : "false") << ","
        << "\"stalemate\":" << (position.is_stalemate() ? "true" : "false") << ","
        << "\"draw\":" << (position.is_draw() ? "true" : "false") << ","
        << "\"repetition\":" << (position.is_draw_by_repetition() ? "true" : "false") << ","
        << "\"fiftyMove\":" << (position.is_draw_by_fifty_move() ? "true" : "false") << ","
        << "\"insufficientMaterial\":" << (position.is_insufficient_material() ? "true" : "false") << ","
        << "\"legalCount\":" << position.legal_moves().size()
        << "}\n";
}

void print_eval_json(
    const Position& position,
    const Engine& engine,
    int score,
    int classical_score,
    int backbone_score,
    int positional_score,
    int nnue_residual_score
) {
    const bool nnue_active = engine.options().use_nnue && engine.nnue_loaded();
    std::cout
        << "{"
        << "\"fen\":\"" << json_escape(position.to_fen()) << "\","
        << "\"score\":" << score << ","
        << "\"scoreText\":\"" << json_escape(deadfish::score_to_string(score)) << "\","
        << "\"mode\":\"" << (nnue_active ? "hybrid" : "classical") << "\","
        << "\"classicalFullScore\":" << classical_score << ","
        << "\"classicalBackboneScore\":" << backbone_score << ","
        << "\"classicalPositionalScore\":" << positional_score << ","
        << "\"nnueResidualScore\":" << nnue_residual_score << ","
        << "\"nnueLoaded\":" << (engine.nnue_loaded() ? "true" : "false") << ","
        << "\"nnueActive\":" << (nnue_active ? "true" : "false") << ","
        << "\"nnueStatus\":\"" << json_escape(engine.nnue_status()) << "\""
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
    apply_engine_overrides(options, engine);
    const SearchLimits limits = make_limits(options);
    SearchResult result = engine.search(position, limits, [&](const SearchInfo& info) {
        if (!options.json) {
            std::cout << "info depth " << info.depth
                      << " score " << deadfish::score_to_string(info.score)
                      << " nodes " << info.nodes
                      << " nps " << info.nps
                      << " time " << info.elapsed_ms
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
        if (result.used_book) {
            std::cout << "source   book\n";
        } else if (result.used_tablebase) {
            std::cout << "source   tablebase\n";
        }
    }
    return 0;
}

int command_eval(const CommandOptions& options) {
    Position position;
    if (!load_position(options, position)) {
        return 1;
    }
    if (!apply_option_moves(options, position)) {
        return 1;
    }

    Engine engine;
    apply_engine_overrides(options, engine);
    const int score = engine.evaluate(position);
    const int classical_score = engine.evaluate_classical(position);
    const int backbone_score = engine.evaluate_backbone(position);
    const int positional_score = classical_score - backbone_score;
    const int nnue_residual_score = engine.evaluate_nnue_residual(position);
    const bool nnue_active = engine.options().use_nnue && engine.nnue_loaded();

    if (options.json) {
        print_eval_json(position, engine, score, classical_score, backbone_score, positional_score, nnue_residual_score);
    } else {
        std::cout << "score    " << deadfish::score_to_string(score) << "\n";
        std::cout << "mode     " << (nnue_active ? "hybrid" : "classical") << "\n";
        std::cout << "classic  " << deadfish::score_to_string(classical_score) << "\n";
        std::cout << "backbone " << deadfish::score_to_string(backbone_score) << "\n";
        std::cout << "c-pos    " << deadfish::score_to_string(positional_score) << "\n";
        std::cout << "nnue     " << deadfish::score_to_string(nnue_residual_score) << "\n";
        std::cout << "loaded   " << (engine.nnue_loaded() ? "yes" : "no") << "\n";
        std::cout << "status   " << engine.nnue_status() << "\n";
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

int command_status(const CommandOptions& options) {
    Position position;
    if (!load_position(options, position)) {
        return 1;
    }
    if (!apply_option_moves(options, position)) {
        return 1;
    }
    if (options.json) {
        print_status_json(position);
    } else {
        std::cout << "fen       " << position.to_fen() << "\n";
        std::cout << "turn      " << (position.side_to_move() == deadfish::Color::White ? "white" : "black") << "\n";
        std::cout << "in check  " << (position.in_check(position.side_to_move()) ? "yes" : "no") << "\n";
        std::cout << "legal     " << position.legal_moves().size() << "\n";
        const std::string result = describe_result(position);
        if (!result.empty()) {
            std::cout << "result    " << result << "\n";
        }
    }
    return 0;
}

int command_bench(const CommandOptions& options) {
    Engine engine;
    apply_engine_overrides(options, engine);
    EngineOptions bench_options = engine.options();
    bench_options.own_book = false;
    engine.set_options(bench_options);
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
                  << " nps " << result.nps;
        if (result.used_book) {
            std::cout << " source book";
        } else if (result.used_tablebase) {
            std::cout << " source tablebase";
        }
        std::cout << "\n";
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
    apply_engine_overrides(options, engine);
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

class UciSession {
public:
    ~UciSession() {
        stop_and_join();
    }

    int run() {
        std::string line;
        while (std::getline(std::cin, line)) {
            std::vector<std::string> tokens = split_words(line);
            if (tokens.empty()) {
                continue;
            }

            const std::string command = lower_copy(tokens.front());
            if (command == "uci") {
                handle_uci();
            } else if (command == "isready") {
                output_line("readyok");
            } else if (command == "ucinewgame") {
                stop_and_join();
                engine_.reset_search_state();
            } else if (command == "position") {
                handle_position(tokens);
            } else if (command == "setoption") {
                handle_setoption(tokens);
            } else if (command == "go") {
                handle_go(tokens);
            } else if (command == "stop") {
                stop_search();
            } else if (command == "quit") {
                stop_and_join();
                break;
            } else if (command == "debug" || command == "ponderhit") {
                continue;
            } else {
                output_line("info string unknown command: " + tokens.front());
            }
        }
        return 0;
    }

private:
    void output_line(const std::string& line) {
        std::lock_guard<std::mutex> guard(output_mutex_);
        std::cout << line << "\n";
        std::cout.flush();
    }

    void output_info(const SearchInfo& info) {
        std::ostringstream out;
        out << "info depth " << info.depth
            << " score " << uci_score(info.score)
            << " nodes " << info.nodes
            << " nps " << info.nps
            << " time " << info.elapsed_ms;
        if (!info.pv.empty()) {
            out << " pv " << deadfish::join_moves(info.pv);
        }
        output_line(out.str());
    }

    void stop_search() {
        engine_.request_stop();
    }

    void stop_and_join() {
        stop_search();
        if (search_thread_.joinable()) {
            search_thread_.join();
        }
        searching_.store(false, std::memory_order_relaxed);
    }

    void handle_uci() {
        const EngineOptions& options = engine_.options();
        output_line("id name DeadFish");
        output_line("id author DeadFish contributors");
        output_line("option name Hash type spin default " + std::to_string(options.hash_mb) + " min 1 max 4096");
        output_line("option name Clear Hash type button");
        output_line(std::string("option name UseNNUE type check default ") + (options.use_nnue ? "true" : "false"));
        output_line("option name EvalFile type string default <empty>");
        output_line(std::string("option name OwnBook type check default ") + (options.own_book ? "true" : "false"));
        output_line("option name BookPath type string default <empty>");
        output_line("option name SyzygyPath type string default <empty>");
        output_line("option name SyzygyProbeLimit type spin default " + std::to_string(options.syzygy_probe_limit) + " min 0 max 7");
        output_line("option name MoveOverhead type spin default " + std::to_string(options.move_overhead_ms) + " min 0 max 10000");
        output_line("uciok");
    }

    void handle_position(const std::vector<std::string>& tokens) {
        if (tokens.size() < 2) {
            output_line("info string position requires startpos or fen");
            return;
        }

        stop_and_join();

        Position next_position;
        std::size_t index = 1;
        if (iequals(tokens[index], "startpos")) {
            next_position = Position::start_position();
            ++index;
        } else if (iequals(tokens[index], "fen")) {
            ++index;
            std::vector<std::string> fen_tokens;
            while (index < tokens.size() && !iequals(tokens[index], "moves")) {
                fen_tokens.push_back(tokens[index]);
                ++index;
            }
            std::string error;
            next_position = Position::from_fen(join_tokens(fen_tokens), &error);
            if (!error.empty()) {
                output_line("info string FEN error: " + error);
                return;
            }
        } else {
            output_line("info string unsupported position format");
            return;
        }

        if (index < tokens.size() && iequals(tokens[index], "moves")) {
            ++index;
            for (; index < tokens.size(); ++index) {
                std::string error;
                if (!next_position.apply_uci_move(tokens[index], &error)) {
                    output_line("info string move error: " + error);
                    return;
                }
            }
        }

        position_ = std::move(next_position);
    }

    void handle_setoption(const std::vector<std::string>& tokens) {
        const auto name_it = std::find_if(tokens.begin(), tokens.end(), [](const std::string& token) {
            return iequals(token, "name");
        });
        if (name_it == tokens.end()) {
            output_line("info string setoption missing name");
            return;
        }

        auto value_it = std::find_if(name_it + 1, tokens.end(), [](const std::string& token) {
            return iequals(token, "value");
        });

        const std::string name = join_tokens(std::vector<std::string>(name_it + 1, value_it));
        const std::string value = value_it == tokens.end() ? "" : join_tokens(std::vector<std::string>(value_it + 1, tokens.end()));

        stop_and_join();

        if (iequals(name, "Clear Hash")) {
            engine_.reset_search_state();
            return;
        }

        EngineOptions options = engine_.options();
        int parsed_int = 0;
        bool parsed_bool = false;

        if (iequals(name, "Hash")) {
            if (!parse_int(value, parsed_int)) {
                output_line("info string invalid Hash value");
                return;
            }
            options.hash_mb = parsed_int;
        } else if (iequals(name, "UseNNUE")) {
            if (!parse_bool(value, parsed_bool)) {
                output_line("info string invalid UseNNUE value");
                return;
            }
            options.use_nnue = parsed_bool;
        } else if (iequals(name, "EvalFile")) {
            options.eval_file = value;
        } else if (iequals(name, "OwnBook")) {
            if (!parse_bool(value, parsed_bool)) {
                output_line("info string invalid OwnBook value");
                return;
            }
            options.own_book = parsed_bool;
        } else if (iequals(name, "BookPath")) {
            options.book_path = value;
        } else if (iequals(name, "SyzygyPath")) {
            options.syzygy_path = value;
        } else if (iequals(name, "SyzygyProbeLimit")) {
            if (!parse_int(value, parsed_int)) {
                output_line("info string invalid SyzygyProbeLimit value");
                return;
            }
            options.syzygy_probe_limit = parsed_int;
        } else if (iequals(name, "MoveOverhead")) {
            if (!parse_int(value, parsed_int)) {
                output_line("info string invalid MoveOverhead value");
                return;
            }
            options.move_overhead_ms = parsed_int;
        } else {
            output_line("info string unknown option: " + name);
            return;
        }

        engine_.set_options(options);
        if (iequals(name, "UseNNUE") || iequals(name, "EvalFile")) {
            output_line("info string " + engine_.nnue_status());
        }
    }

    void handle_go(const std::vector<std::string>& tokens) {
        SearchLimits limits;
        limits.max_depth = 0;

        for (std::size_t index = 1; index < tokens.size(); ++index) {
            const std::string token = lower_copy(tokens[index]);
            auto read_int = [&](int& target) {
                if (index + 1 >= tokens.size() || !parse_int(tokens[index + 1], target)) {
                    output_line("info string invalid go value for " + tokens[index]);
                    return false;
                }
                ++index;
                return true;
            };

            if (token == "depth") {
                if (!read_int(limits.max_depth)) {
                    return;
                }
            } else if (token == "movetime") {
                if (!read_int(limits.time_limit_ms)) {
                    return;
                }
            } else if (token == "wtime") {
                if (!read_int(limits.white_time_ms)) {
                    return;
                }
            } else if (token == "btime") {
                if (!read_int(limits.black_time_ms)) {
                    return;
                }
            } else if (token == "winc") {
                if (!read_int(limits.white_increment_ms)) {
                    return;
                }
            } else if (token == "binc") {
                if (!read_int(limits.black_increment_ms)) {
                    return;
                }
            } else if (token == "movestogo") {
                if (!read_int(limits.moves_to_go)) {
                    return;
                }
            } else if (token == "infinite") {
                limits.infinite = true;
            }
        }

        stop_and_join();
        engine_.clear_stop_request();

        const Position root = position_;
        searching_.store(true, std::memory_order_relaxed);
        search_thread_ = std::thread([this, root, limits]() {
            SearchResult result = engine_.search(root, limits, [this](const SearchInfo& info) {
                output_info(info);
            });

            if (result.used_book) {
                output_line("info string using opening book");
            } else if (result.used_tablebase) {
                output_line("info string using syzygy tablebase");
            }

            std::ostringstream out;
            out << "bestmove " << result.best_move.to_uci();
            if (result.pv.size() > 1) {
                out << " ponder " << result.pv[1].to_uci();
            }
            output_line(out.str());
            searching_.store(false, std::memory_order_relaxed);
        });
    }

    Engine engine_{};
    Position position_ = Position::start_position();
    std::thread search_thread_{};
    std::atomic<bool> searching_{false};
    std::mutex output_mutex_{};
};

int command_uci() {
    UciSession session;
    return session.run();
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        return command_uci();
    }

    const std::string command = argv[1];
    if (command == "uci") {
        return command_uci();
    }

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
    if (command == "eval") {
        return command_eval(options);
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
    if (command == "status") {
        return command_status(options);
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
