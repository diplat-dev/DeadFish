import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const nativeExe = path.join(root, "build", "deadfish.exe");
const wasmJs = path.join(root, "web", "deadfish_wasm.js");

if (!existsSync(nativeExe)) {
  throw new Error(`Native CLI not found at ${nativeExe}. Build it first with scripts/build_native.ps1.`);
}
if (!existsSync(wasmJs)) {
  throw new Error(`WASM module not found at ${wasmJs}. Build it first with scripts/build_wasm.ps1.`);
}

const createModule = require(wasmJs);
const Module = await createModule();

const api = {
  reset: Module.cwrap("df_reset", null, []),
  setFen: Module.cwrap("df_set_fen", "number", ["string"]),
  getFen: Module.cwrap("df_get_fen", "string", []),
  legalMoves: Module.cwrap("df_legal_moves_csv", "string", []),
  applyMove: Module.cwrap("df_apply_move", "number", ["string"]),
  search: Module.cwrap("df_search_json", "string", ["number", "number"]),
  lastError: Module.cwrap("df_last_error", "string", []),
};

function runNative(args) {
  const result = spawnSync(nativeExe, args, { encoding: "utf8" });
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || `Native command failed: ${args.join(" ")}`);
  }
  return result.stdout.trim();
}

function setFenOrThrow(fen) {
  if (!api.setFen(fen)) {
    throw new Error(api.lastError() || `Failed to set FEN: ${fen}`);
  }
}

function applyMoveOrThrow(uci) {
  if (!api.applyMove(uci)) {
    throw new Error(api.lastError() || `Failed to apply move: ${uci}`);
  }
}

function wasmLegal(fen) {
  setFenOrThrow(fen);
  return api.legalMoves().split(",").filter(Boolean).sort();
}

function nativeLegal(fen) {
  return runNative(["legal", "--fen", fen]).split(/\s+/).filter(Boolean).sort();
}

function nativeSearch(fen, depth) {
  return JSON.parse(runNative(["search", "--fen", fen, "--depth", String(depth), "--json"]));
}

function wasmSearch(fen, depth) {
  setFenOrThrow(fen);
  return JSON.parse(api.search(depth, 0));
}

function nativeFenAfterMoves(fen, moves) {
  return runNative(["fen", "--fen", fen, "--moves", moves.join(",")]);
}

function wasmFenAfterMoves(fen, moves) {
  setFenOrThrow(fen);
  for (const move of moves) {
    applyMoveOrThrow(move);
  }
  return api.getFen();
}

function expect(condition, label) {
  if (!condition) {
    throw new Error(`Parity failure: ${label}`);
  }
  console.log(`ok - ${label}`);
}

const legalCases = [
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
];

for (const fen of legalCases) {
  expect(
    JSON.stringify(nativeLegal(fen)) === JSON.stringify(wasmLegal(fen)),
    `legal move parity for ${fen}`
  );
}

const transitionFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const transitionMoves = ["e2e4", "e7e5", "g1f3"];
expect(
  nativeFenAfterMoves(transitionFen, transitionMoves) === wasmFenAfterMoves(transitionFen, transitionMoves),
  "FEN transition parity after e2e4,e7e5,g1f3"
);

const searchCases = [
  { fen: "7k/6Q1/6K1/8/8/8/8/8 w - - 0 1", depth: 2 },
  { fen: "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", depth: 3 },
];

for (const test of searchCases) {
  const native = nativeSearch(test.fen, test.depth);
  const wasm = wasmSearch(test.fen, test.depth);
  expect(native.bestMove === wasm.bestMove, `search best-move parity for ${test.fen}`);
}

console.log("WASM parity checks passed.");
