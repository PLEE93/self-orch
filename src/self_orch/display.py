"""Live parallel seat panel on stderr. stdout stays JSON for the governor."""

from __future__ import annotations

import sys
import threading
from typing import Any


_LOCK = threading.Lock()


def _tty() -> bool:
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def _truncate(text: str, n: int = 72) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


class Dashboard:
    def __init__(self, seats: list[dict[str, Any]], enabled: bool | None = None):
        self.seats = seats
        self.enabled = _tty() if enabled is None else enabled
        self._drawn = False
        self.lines = len(seats) + 2

    def _render(self) -> str:
        rows = ["self-orch  parallel parliament", ""]
        for i, s in enumerate(self.seats, 1):
            status = s.get("ui_status") or s.get("status") or "pending"
            model = s.get("model") or "?"
            role = s.get("role") or "?"
            elapsed = s.get("elapsed_s")
            el = f"{elapsed:5.1f}s" if isinstance(elapsed, (int, float)) else "  ... "
            preview = _truncate(s.get("preview") or s.get("output") or s.get("error") or "")
            rows.append(f"[{i}] {role:<18.18} {model:<12.12} {el} {status:<8} {preview}")
        return "\n".join(rows)

    def refresh(self) -> None:
        if not self.enabled:
            return
        body = self._render()
        with _LOCK:
            if self._drawn:
                sys.stderr.write(f"\033[{self.lines}A\033[J")
            sys.stderr.write(body + "\n")
            sys.stderr.flush()
            self._drawn = True

    def finish(self) -> None:
        if not self.enabled:
            return
        self.refresh()
        sys.stderr.write("\n")
        sys.stderr.flush()
