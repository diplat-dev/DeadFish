#include "deadfish/engine.hpp"

#include <sstream>
#include <string>

namespace {

deadfish::Engine g_engine;
deadfish::Position g_position = deadfish::Position::start_position();
std::string g_last_error;
std::string g_buffer;

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

const char* store(std::string value) {
    g_buffer = std::move(value);
    return g_buffer.c_str();
}

std::string status_json() {
    std::ostringstream out;
    out << "{"
        << "\"fen\":\"" << json_escape(g_position.to_fen()) << "\","
        << "\"turn\":\"" << (g_position.side_to_move() == deadfish::Color::White ? "w" : "b") << "\","
        << "\"inCheck\":" << (g_position.in_check(g_position.side_to_move()) ? "true" : "false") << ","
        << "\"checkmate\":" << (g_position.is_checkmate() ? "true" : "false") << ","
        << "\"stalemate\":" << (g_position.is_stalemate() ? "true" : "false") << ","
        << "\"draw\":" << (g_position.is_draw() ? "true" : "false") << ","
        << "\"legalCount\":" << g_position.legal_moves().size()
        << "}";
    return out.str();
}

}  // namespace

extern "C" {

void df_reset() {
    g_position = deadfish::Position::start_position();
    g_last_error.clear();
}

int df_set_fen(const char* fen) {
    std::string error;
    deadfish::Position position = deadfish::Position::from_fen(fen ? fen : "", &error);
    if (!error.empty()) {
        g_last_error = error;
        return 0;
    }
    g_position = position;
    g_last_error.clear();
    return 1;
}

const char* df_get_fen() {
    return store(g_position.to_fen());
}

const char* df_legal_moves_csv() {
    return store(deadfish::join_moves(g_position.legal_moves(), ","));
}

int df_apply_move(const char* uci) {
    std::string error;
    if (!g_position.apply_uci_move(uci ? uci : "", &error)) {
        g_last_error = error;
        return 0;
    }
    g_last_error.clear();
    return 1;
}

const char* df_status_json() {
    return store(status_json());
}

const char* df_search_json(int max_depth, int time_limit_ms) {
    deadfish::SearchLimits limits;
    limits.max_depth = max_depth;
    limits.time_limit_ms = time_limit_ms;
    deadfish::SearchResult result = g_engine.search(g_position, limits);

    std::ostringstream out;
    out << "{"
        << "\"bestMove\":\"" << json_escape(result.best_move.to_uci()) << "\","
        << "\"score\":" << result.score << ","
        << "\"scoreText\":\"" << json_escape(deadfish::score_to_string(result.score)) << "\","
        << "\"depth\":" << result.depth_reached << ","
        << "\"nodes\":" << result.nodes << ","
        << "\"nps\":" << result.nps << ","
        << "\"elapsedMs\":" << result.elapsed_ms << ","
        << "\"pv\":\"" << json_escape(deadfish::join_moves(result.pv)) << "\""
        << "}";
    return store(out.str());
}

const char* df_last_error() {
    return store(g_last_error);
}

}  // extern "C"
