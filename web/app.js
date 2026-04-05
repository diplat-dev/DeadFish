const worker = new Worker("./worker.js");
const pieceGlyphs = {
  P: "♙",
  N: "♘",
  B: "♗",
  R: "♖",
  Q: "♕",
  K: "♔",
  p: "♟",
  n: "♞",
  b: "♝",
  r: "♜",
  q: "♛",
  k: "♚",
};

const boardEl = document.getElementById("board");
const statusTextEl = document.getElementById("statusText");
const detailTextEl = document.getElementById("detailText");
const engineStatsEl = document.getElementById("engineStats");
const moveListEl = document.getElementById("moveList");
const resetBtn = document.getElementById("resetBtn");
const flipBtn = document.getElementById("flipBtn");
const depthSelect = document.getElementById("depthSelect");
const timeSelect = document.getElementById("timeSelect");
const fenInput = document.getElementById("fenInput");
const loadFenBtn = document.getElementById("loadFenBtn");

let nextRequestId = 0;
const pending = new Map();

let currentState = null;
let legalMoves = [];
let moveHistory = [];
let selectedSquare = null;
let flipped = false;
let aiThinking = false;

worker.addEventListener("message", (event) => {
  const { type, requestId, payload, message } = event.data || {};
  const deferred = pending.get(requestId);
  if (!deferred) {
    return;
  }
  pending.delete(requestId);
  if (type === "ok") {
    deferred.resolve(payload);
  } else {
    deferred.reject(new Error(message || "Worker error."));
  }
});

function callWorker(type, payload = {}) {
  const requestId = ++nextRequestId;
  return new Promise((resolve, reject) => {
    pending.set(requestId, { resolve, reject });
    worker.postMessage({ type, requestId, ...payload });
  });
}

function parseFenBoard(fen) {
  const boardPart = fen.split(" ")[0];
  const rows = boardPart.split("/");
  return rows.map((row) => {
    const squares = [];
    for (const char of row) {
      if (/\d/.test(char)) {
        for (let i = 0; i < Number(char); i += 1) {
          squares.push("");
        }
      } else {
        squares.push(char);
      }
    }
    return squares;
  });
}

function squareName(rankIndex, fileIndex) {
  return `${String.fromCharCode(97 + fileIndex)}${8 - rankIndex}`;
}

function isHumanTurn() {
  return currentState && currentState.status.turn === "w" && !aiThinking;
}

function legalTargetsFor(square) {
  return legalMoves
    .filter((move) => move.startsWith(square))
    .map((move) => move.slice(2, 4));
}

function setStatus(message, detail = "") {
  statusTextEl.textContent = message;
  detailTextEl.textContent = detail;
}

function refreshMoveList() {
  moveListEl.innerHTML = "";
  for (let index = 0; index < moveHistory.length; index += 2) {
    const li = document.createElement("li");
    const moveNo = Math.floor(index / 2) + 1;
    li.textContent = `${moveNo}. ${moveHistory[index]}${moveHistory[index + 1] ? ` ${moveHistory[index + 1]}` : ""}`;
    moveListEl.appendChild(li);
  }
}

function renderBoard() {
  if (!currentState) {
    return;
  }
  const board = parseFenBoard(currentState.status.fen);
  boardEl.innerHTML = "";

  const rankOrder = flipped ? [7, 6, 5, 4, 3, 2, 1, 0] : [0, 1, 2, 3, 4, 5, 6, 7];
  const fileOrder = flipped ? [7, 6, 5, 4, 3, 2, 1, 0] : [0, 1, 2, 3, 4, 5, 6, 7];

  for (const rankIndex of rankOrder) {
    for (const fileIndex of fileOrder) {
      const square = document.createElement("button");
      const logicalSquare = squareName(rankIndex, fileIndex);
      square.className = `square ${(rankIndex + fileIndex) % 2 === 0 ? "light" : "dark"}`;
      square.dataset.square = logicalSquare;

      if (selectedSquare === logicalSquare) {
        square.classList.add("selected");
      }
      if (legalTargetsFor(selectedSquare || "").includes(logicalSquare)) {
        square.classList.add("target");
      }

      const piece = board[rankIndex][fileIndex];
      if (piece) {
        const span = document.createElement("span");
        span.className = `piece ${piece === piece.toUpperCase() ? "white" : "black"}`;
        span.textContent = pieceGlyphs[piece];
        square.appendChild(span);
      }

      square.addEventListener("click", () => handleSquareClick(logicalSquare, piece));
      boardEl.appendChild(square);
    }
  }
}

function render() {
  if (!currentState) {
    return;
  }
  renderBoard();
  refreshMoveList();
  fenInput.value = currentState.status.fen;

  if (currentState.status.checkmate) {
    setStatus("Checkmate", currentState.status.turn === "w" ? "Black delivered mate." : "White delivered mate.");
  } else if (currentState.status.stalemate) {
    setStatus("Stalemate", "No legal moves remain.");
  } else if (currentState.status.draw) {
    setStatus("Draw", "A draw condition is active.");
  } else if (aiThinking) {
    setStatus("DeadFish is thinking", `Depth ${depthSelect.value}, movetime ${timeSelect.value} ms`);
  } else {
    setStatus(
      currentState.status.turn === "w" ? "White to move" : "Black to move",
      currentState.status.inCheck ? "Check." : `${currentState.legalMoves.length} legal moves.`
    );
  }
}

async function syncState(payload) {
  currentState = payload;
  legalMoves = payload.legalMoves;
  selectedSquare = null;
  render();
}

async function handleSquareClick(square, piece) {
  if (!currentState || !isHumanTurn() || currentState.status.checkmate || currentState.status.stalemate || currentState.status.draw) {
    return;
  }

  const isWhitePiece = piece && piece === piece.toUpperCase();
  if (selectedSquare) {
    const matchingMove = legalMoves.find((move) => move.startsWith(selectedSquare) && move.slice(2, 4) === square);
    if (matchingMove) {
      moveHistory.push(matchingMove);
      await syncState(await callWorker("applyMove", { uci: matchingMove }));
      if (currentState.status.turn === "b" && !currentState.status.checkmate && !currentState.status.stalemate && !currentState.status.draw) {
        await triggerAi();
      }
      return;
    }
  }

  if (isWhitePiece) {
    selectedSquare = square;
    renderBoard();
  } else {
    selectedSquare = null;
    renderBoard();
  }
}

async function triggerAi() {
  aiThinking = true;
  engineStatsEl.textContent = "Searching...";
  render();
  try {
    const payload = await callWorker("searchAndApply", {
      depth: Number(depthSelect.value),
      movetime: Number(timeSelect.value),
    });
    aiThinking = false;
    currentState = { status: payload.status, legalMoves: payload.legalMoves };
    legalMoves = payload.legalMoves;
    if (payload.result.bestMove) {
      moveHistory.push(payload.result.bestMove);
    }
    engineStatsEl.textContent = `Best ${payload.result.bestMove || "(none)"} | Eval ${payload.result.scoreText} | Nodes ${payload.result.nodes.toLocaleString()} | NPS ${payload.result.nps.toLocaleString()}`;
    render();
  } catch (error) {
    aiThinking = false;
    engineStatsEl.textContent = `Engine error: ${error.message}`;
    render();
  }
}

resetBtn.addEventListener("click", async () => {
  moveHistory = [];
  engineStatsEl.textContent = "Idle";
  await syncState(await callWorker("reset"));
});

flipBtn.addEventListener("click", () => {
  flipped = !flipped;
  renderBoard();
});

loadFenBtn.addEventListener("click", async () => {
  moveHistory = [];
  engineStatsEl.textContent = "Idle";
  try {
    await syncState(await callWorker("setFen", { fen: fenInput.value.trim() }));
    if (currentState.status.turn === "b" && !currentState.status.checkmate && !currentState.status.stalemate && !currentState.status.draw) {
      await triggerAi();
    }
  } catch (error) {
    setStatus("Invalid FEN", error.message);
  }
});

async function init() {
  try {
    await syncState(await callWorker("init"));
    engineStatsEl.textContent = "Ready";
  } catch (error) {
    setStatus("Worker failed", error.message);
    engineStatsEl.textContent = "Build the WASM target with scripts/build_wasm.ps1";
  }
}

init();
