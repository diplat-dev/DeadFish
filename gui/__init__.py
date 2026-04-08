"""Tkinter-based UCI chess GUI for DeadFish and other UCI engines."""

from __future__ import annotations

import site
import sys
from pathlib import Path


def ensure_user_site() -> None:
    base = Path(__file__).resolve().parents[1]
    for candidate_path in (
        base / "vendor" / "chess-1.11.2",
        base / ".gui_pydeps",
        base / ".tmp_pydeps",
    ):
        if candidate_path.exists():
            candidate_text = str(candidate_path)
            if candidate_text not in sys.path:
                sys.path.insert(0, candidate_text)
    candidate = site.getusersitepackages()
    if not candidate:
        return
    if candidate not in sys.path and Path(candidate).exists():
        sys.path.append(candidate)


ensure_user_site()
