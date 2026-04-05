let modulePromise = null;
let api = null;

async function ensureApi() {
  if (api) {
    return api;
  }
  if (!modulePromise) {
    importScripts("./deadfish_wasm.js");
    modulePromise = self.DeadFishModule();
  }
  const Module = await modulePromise;
  api = {
    reset: Module.cwrap("df_reset", null, []),
    setFen: Module.cwrap("df_set_fen", "number", ["string"]),
    getFen: Module.cwrap("df_get_fen", "string", []),
    legalMoves: Module.cwrap("df_legal_moves_csv", "string", []),
    applyMove: Module.cwrap("df_apply_move", "number", ["string"]),
    status: Module.cwrap("df_status_json", "string", []),
    search: Module.cwrap("df_search_json", "string", ["number", "number"]),
    lastError: Module.cwrap("df_last_error", "string", []),
  };
  return api;
}

function parseLegalMoves(csv) {
  return csv ? csv.split(",").filter(Boolean) : [];
}

async function buildStatePayload() {
  const bindings = await ensureApi();
  return {
    status: JSON.parse(bindings.status()),
    legalMoves: parseLegalMoves(bindings.legalMoves()),
  };
}

self.onmessage = async (event) => {
  const { type, requestId, fen, uci, depth, movetime } = event.data || {};
  try {
    const bindings = await ensureApi();
    let payload = null;

    switch (type) {
      case "init":
        payload = await buildStatePayload();
        break;
      case "reset":
        bindings.reset();
        payload = await buildStatePayload();
        break;
      case "state":
        payload = await buildStatePayload();
        break;
      case "setFen":
        if (!bindings.setFen(fen || "")) {
          throw new Error(bindings.lastError() || "Failed to set FEN.");
        }
        payload = await buildStatePayload();
        break;
      case "applyMove":
        if (!bindings.applyMove(uci || "")) {
          throw new Error(bindings.lastError() || "Illegal move.");
        }
        payload = await buildStatePayload();
        break;
      case "searchAndApply": {
        const result = JSON.parse(bindings.search(Number(depth) || 4, Number(movetime) || 0));
        if (result.bestMove) {
          if (!bindings.applyMove(result.bestMove)) {
            throw new Error(bindings.lastError() || "Failed to apply engine move.");
          }
        }
        payload = {
          result,
          ...(await buildStatePayload()),
        };
        break;
      }
      default:
        throw new Error(`Unknown worker command: ${type}`);
    }

    self.postMessage({ type: "ok", requestId, payload });
  } catch (error) {
    self.postMessage({
      type: "error",
      requestId,
      message: error instanceof Error ? error.message : String(error),
    });
  }
};
