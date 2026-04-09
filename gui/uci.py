from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import queue
import subprocess
import threading
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def discover_default_engine(root: Path | None = None) -> Path | None:
    base = root or repo_root()
    build_dir = base / "build"
    for candidate in ("deadfish_native.exe", "deadfish.exe"):
        path = build_dir / candidate
        if path.exists():
            return path
    return None


def discover_default_nnue(root: Path | None = None) -> Path | None:
    base = root or repo_root()
    candidates = (
        base / "training" / "output" / "deadfish_current.nnue",
        base / "training" / "output" / "deadfish.nnue",
    )
    for path in candidates:
        if path.exists():
            return path
    return None


def normalize_option_name(name: str) -> str:
    return name.strip().casefold()


@dataclass(slots=True)
class EngineIdentity:
    name: str = ""
    author: str = ""


@dataclass(slots=True)
class UciOption:
    name: str
    kind: str
    default: Any = None
    minimum: int | None = None
    maximum: int | None = None
    vars: tuple[str, ...] = ()
    raw_default: str = ""


@dataclass(slots=True)
class SearchInfo:
    depth: int | None = None
    nodes: int | None = None
    nps: int | None = None
    time_ms: int | None = None
    score_kind: str | None = None
    score_value: int | None = None
    pv: tuple[str, ...] = ()


@dataclass(slots=True)
class RawLineEvent:
    raw_line: str


@dataclass(slots=True)
class IdEvent:
    field: str
    value: str
    raw_line: str


@dataclass(slots=True)
class OptionEvent:
    option: UciOption
    raw_line: str


@dataclass(slots=True)
class UciOkEvent:
    raw_line: str


@dataclass(slots=True)
class ReadyOkEvent:
    raw_line: str


@dataclass(slots=True)
class InfoEvent:
    info: SearchInfo | None
    message: str | None
    raw_line: str


@dataclass(slots=True)
class BestMoveEvent:
    bestmove: str
    ponder: str | None
    raw_line: str


@dataclass(slots=True)
class ProcessExitedEvent:
    returncode: int
    raw_line: str = ""


UciEvent = (
    RawLineEvent
    | IdEvent
    | OptionEvent
    | UciOkEvent
    | ReadyOkEvent
    | InfoEvent
    | BestMoveEvent
    | ProcessExitedEvent
)


def option_default_value(option: UciOption) -> Any:
    if option.kind == "button":
        return None
    if option.default is not None:
        return option.default
    if option.kind == "check":
        return False
    if option.kind == "spin":
        return option.minimum if option.minimum is not None else 0
    if option.kind == "combo" and option.vars:
        return option.vars[0]
    return ""


def coerce_option_value(option: UciOption, value: Any) -> Any:
    if option.kind == "button":
        return None
    if option.kind == "check":
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() in {"1", "true", "yes", "on"}
    if option.kind == "spin":
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            number = int(option_default_value(option))
        if option.minimum is not None:
            number = max(option.minimum, number)
        if option.maximum is not None:
            number = min(option.maximum, number)
        return number
    text = "" if value is None else str(value)
    if option.kind == "combo" and option.vars and text not in option.vars:
        return option_default_value(option)
    return text


def uci_option_value_text(option: UciOption, value: Any) -> str:
    coerced = coerce_option_value(option, value)
    if option.kind == "check":
        return "true" if coerced else "false"
    if option.kind == "button":
        return ""
    return str(coerced)


def format_score(score_kind: str | None, score_value: int | None) -> str:
    if score_kind is None or score_value is None:
        return ""
    if score_kind == "mate":
        return f"#{score_value}"
    return f"{score_value / 100.0:+.2f}"


def _parse_int(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


def _parse_option_default(kind: str, text: str) -> Any:
    if kind == "check":
        return text.casefold() == "true"
    if kind == "spin":
        value = _parse_int(text)
        return 0 if value is None else value
    if text == "<empty>":
        return ""
    return text


def parse_option_line(line: str) -> UciOption | None:
    tokens = line.strip().split()
    if len(tokens) < 5 or tokens[0].casefold() != "option" or tokens[1].casefold() != "name":
        return None

    index = 2
    name_tokens: list[str] = []
    while index < len(tokens) and tokens[index].casefold() != "type":
        name_tokens.append(tokens[index])
        index += 1
    if index >= len(tokens) - 1:
        return None

    name = " ".join(name_tokens).strip()
    kind = tokens[index + 1].casefold()
    index += 2

    default_text = ""
    minimum: int | None = None
    maximum: int | None = None
    variants: list[str] = []
    keywords = {"default", "min", "max", "var"}

    while index < len(tokens):
        keyword = tokens[index].casefold()
        index += 1
        value_tokens: list[str] = []
        while index < len(tokens) and tokens[index].casefold() not in keywords:
            value_tokens.append(tokens[index])
            index += 1
        value_text = " ".join(value_tokens).strip()
        if keyword == "default":
            default_text = value_text
        elif keyword == "min":
            minimum = _parse_int(value_text)
        elif keyword == "max":
            maximum = _parse_int(value_text)
        elif keyword == "var" and value_text:
            variants.append(value_text)

    default_value = None if kind == "button" and not default_text else _parse_option_default(kind, default_text)
    return UciOption(
        name=name,
        kind=kind,
        default=default_value,
        minimum=minimum,
        maximum=maximum,
        vars=tuple(variants),
        raw_default=default_text,
    )


def parse_info_line(line: str) -> InfoEvent:
    tokens = line.strip().split()
    if len(tokens) >= 2 and tokens[1].casefold() == "string":
        return InfoEvent(info=None, message=" ".join(tokens[2:]).strip(), raw_line=line)

    info = SearchInfo()
    index = 1
    one_value_fields = {
        "seldepth",
        "hashfull",
        "currmove",
        "currmovenumber",
        "multipv",
        "tbhits",
        "cpuload",
    }
    while index < len(tokens):
        token = tokens[index].casefold()
        if token == "depth" and index + 1 < len(tokens):
            info.depth = _parse_int(tokens[index + 1])
            index += 2
            continue
        if token == "nodes" and index + 1 < len(tokens):
            info.nodes = _parse_int(tokens[index + 1])
            index += 2
            continue
        if token == "nps" and index + 1 < len(tokens):
            info.nps = _parse_int(tokens[index + 1])
            index += 2
            continue
        if token == "time" and index + 1 < len(tokens):
            info.time_ms = _parse_int(tokens[index + 1])
            index += 2
            continue
        if token == "score" and index + 2 < len(tokens):
            info.score_kind = tokens[index + 1].casefold()
            info.score_value = _parse_int(tokens[index + 2])
            index += 3
            continue
        if token == "pv":
            info.pv = tuple(tokens[index + 1 :])
            break
        if token == "string":
            return InfoEvent(info=info, message=" ".join(tokens[index + 1 :]).strip(), raw_line=line)
        if token in one_value_fields:
            index += 2
            continue
        index += 1
    return InfoEvent(info=info, message=None, raw_line=line)


def parse_line(line: str) -> UciEvent:
    stripped = line.strip()
    if stripped == "uciok":
        return UciOkEvent(raw_line=line)
    if stripped == "readyok":
        return ReadyOkEvent(raw_line=line)
    if stripped.startswith("id "):
        tokens = stripped.split(maxsplit=2)
        if len(tokens) == 3:
            return IdEvent(field=tokens[1].casefold(), value=tokens[2], raw_line=line)
    if stripped.startswith("option "):
        option = parse_option_line(stripped)
        if option is not None:
            return OptionEvent(option=option, raw_line=line)
    if stripped.startswith("info "):
        return parse_info_line(stripped)
    if stripped.startswith("bestmove "):
        tokens = stripped.split()
        bestmove = tokens[1] if len(tokens) > 1 else "0000"
        ponder = None
        if len(tokens) >= 4 and tokens[2].casefold() == "ponder":
            ponder = tokens[3]
        return BestMoveEvent(bestmove=bestmove, ponder=ponder, raw_line=line)
    return RawLineEvent(raw_line=line)


class UciClient:
    def __init__(self, engine_path: Path, *, command: list[str] | None = None) -> None:
        self.engine_path = engine_path
        self.command = command or [str(engine_path)]
        self.identity = EngineIdentity()
        self.options: OrderedDict[str, UciOption] = OrderedDict()
        self._process: subprocess.Popen[str] | None = None
        self._event_queue: queue.Queue[UciEvent] = queue.Queue()
        self._send_lock = threading.Lock()
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        if self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("Failed to start engine process with pipes.")
        self._reader = threading.Thread(target=self._reader_loop, name="uci-reader", daemon=True)
        self._reader.start()

    def send(self, line: str) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("Engine process is not running.")
        with self._send_lock:
            process.stdin.write(line + "\n")
            process.stdin.flush()

    def poll_events(self, *, max_events: int = 200) -> list[UciEvent]:
        events: list[UciEvent] = []
        while len(events) < max_events:
            try:
                events.append(self._event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            self.send("quit")
        except (OSError, RuntimeError):
            pass
        try:
            process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.5)
        if self._reader is not None:
            self._reader.join(timeout=1.0)
        self._process = None

    def _reader_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        for raw_line in self._process.stdout:
            line = raw_line.rstrip("\r\n")
            event = parse_line(line)
            if isinstance(event, IdEvent):
                if event.field == "name":
                    self.identity.name = event.value
                elif event.field == "author":
                    self.identity.author = event.value
            elif isinstance(event, OptionEvent):
                self.options[event.option.name] = event.option
            self._event_queue.put(event)
        returncode = self._process.poll()
        if returncode is None:
            returncode = 0
        self._event_queue.put(ProcessExitedEvent(returncode=returncode))
