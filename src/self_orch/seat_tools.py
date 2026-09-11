"""Real capability for substrate seats.

Before this module every seat was a text-only model call: the rail sent messages
and collected prose back. That made two of the pipeline's own promises
unkeepable. EXECUTE was told to implement work, run checks and report artifact
paths; it could only describe doing so. RED TEAM was told to "check the
artifact, not the claim"; it had no way to open the artifact. A stage whose
instructions cannot be carried out is not a stage, it is a wish.

A Toolbox gives a seat a bounded, auditable way to touch the world:

  read_file / list_dir / search_files   always available
  write_file                            only with allow_write
  run                                   only with allow_shell
  http_get                              only with allow_net

Every path is resolved against the workspace root and refused if it escapes it,
after symlinks are followed -- so a seat cannot read ~/.ssh by asking nicely.
Every call is recorded in `calls` so the run can be audited afterwards, and
every result is truncated to a fixed ceiling so one `cat` of a large file cannot
blow the context budget of the stage that follows.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MAX_RESULT_CHARS = 8000
DEFAULT_SHELL_TIMEOUT_S = 120


class ToolRefusal(RuntimeError):
    """A tool call the toolbox will not perform. Returned to the model as text
    rather than raised at the operator, so the seat can correct itself."""


@dataclass
class Toolbox:
    root: Path
    allow_write: bool = False
    allow_shell: bool = False
    allow_net: bool = False
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS
    shell_timeout_s: int = DEFAULT_SHELL_TIMEOUT_S
    calls: list[dict[str, Any]] = field(default_factory=list)

    # ── construction ──────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "Toolbox | None":
        if not raw:
            return None
        root = raw.get("workspace") or raw.get("root") or raw.get("path")
        if not root:
            raise ValueError("a toolbox needs a workspace root")
        ws = Path(str(root)).expanduser().resolve()
        # Fail here, loudly, rather than handing every seat a toolbox whose every
        # call will be refused for a reason that looks like a model mistake.
        if not ws.is_dir():
            raise ValueError(f"workspace {ws} does not exist or is not a directory")
        return cls(
            root=ws,
            allow_write=bool(raw.get("allow_write")),
            allow_shell=bool(raw.get("allow_shell")),
            allow_net=bool(raw.get("allow_net")),
            max_result_chars=int(raw.get("max_result_chars") or DEFAULT_MAX_RESULT_CHARS),
            shell_timeout_s=int(raw.get("shell_timeout_s") or DEFAULT_SHELL_TIMEOUT_S),
        )

    def derive(self, *, allow_write: bool, allow_shell: bool) -> "Toolbox":
        """A copy with narrower powers -- used to hand a red team the same
        workspace the builders used, without letting it edit the evidence."""
        return Toolbox(
            root=self.root,
            allow_write=self.allow_write and allow_write,
            allow_shell=self.allow_shell and allow_shell,
            allow_net=self.allow_net,
            max_result_chars=self.max_result_chars,
            shell_timeout_s=self.shell_timeout_s,
        )

    # ── safety ────────────────────────────────────────────────────────────

    def resolve(self, rel: str) -> Path:
        """Resolve a seat-supplied path inside the workspace, or refuse.

        Resolution happens after symlinks, which is the point: a symlink
        planted inside the workspace must not become a door out of it.
        """
        raw = str(rel or "").strip()
        if not raw:
            raise ToolRefusal("empty path")
        p = (self.root / raw).expanduser()
        try:
            resolved = p.resolve()
        except OSError as e:                      # pragma: no cover - exotic fs
            raise ToolRefusal(f"cannot resolve {raw!r}: {e}") from e
        if resolved != self.root and self.root not in resolved.parents:
            raise ToolRefusal(
                f"path {raw!r} resolves outside the workspace ({self.root}) and was refused"
            )
        return resolved

    def _clip(self, text: str) -> str:
        if len(text) <= self.max_result_chars:
            return text
        keep = self.max_result_chars - 80
        head = int(keep * 0.7)
        return (
            text[:head]
            + f"\n...[{len(text) - keep} chars omitted: result exceeded the tool ceiling]...\n"
            + text[-(keep - head):]
        )

    # ── the tools ─────────────────────────────────────────────────────────

    def read_file(self, path: str, start_line: int = 0, end_line: int = 0) -> str:
        f = self.resolve(path)
        if not f.is_file():
            raise ToolRefusal(f"{path!r} is not a file")
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        a = max(0, int(start_line or 1) - 1)
        b = int(end_line) if end_line else len(lines)
        chunk = lines[a:b]
        return "\n".join(f"{a + i + 1:6d}\t{ln}" for i, ln in enumerate(chunk))

    def write_file(self, path: str, content: str) -> str:
        if not self.allow_write:
            raise ToolRefusal("this seat is read-only; write_file is not available to it")
        f = self.resolve(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        existed = f.exists()
        f.write_text(content or "", encoding="utf-8")
        return f"{'updated' if existed else 'created'} {f.relative_to(self.root)} ({len(content or '')} chars)"

    def list_dir(self, path: str = ".") -> str:
        d = self.resolve(path or ".")
        if not d.is_dir():
            raise ToolRefusal(f"{path!r} is not a directory")
        rows = []
        for entry in sorted(d.iterdir(), key=lambda e: (not e.is_dir(), e.name)):
            if entry.name.startswith(".git"):
                continue
            kind = "dir " if entry.is_dir() else "file"
            size = "" if entry.is_dir() else f"  {entry.stat().st_size}B"
            rows.append(f"{kind}  {entry.name}{size}")
        return "\n".join(rows) or "(empty)"

    def search_files(self, pattern: str, path: str = ".", max_hits: int = 80) -> str:
        root = self.resolve(path or ".")
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolRefusal(f"bad regex {pattern!r}: {e}") from e
        hits: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", "node_modules"}]
            for fn in filenames:
                fp = Path(dirpath) / fn
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{fp.relative_to(self.root)}:{i}: {line.strip()[:200]}")
                        if len(hits) >= max_hits:
                            return "\n".join(hits) + "\n...[hit ceiling]"
        return "\n".join(hits) or "(no matches)"

    def run(self, command: str) -> str:
        if not self.allow_shell:
            raise ToolRefusal("this seat may not run commands; run is not available to it")
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(self.root), capture_output=True,
                text=True, timeout=self.shell_timeout_s,
            )
        except subprocess.TimeoutExpired:
            raise ToolRefusal(
                f"command exceeded {self.shell_timeout_s}s and was killed: {command[:200]}"
            ) from None
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        return f"exit={proc.returncode}\n{out}".strip()

    def http_get(self, url: str) -> str:
        if not self.allow_net:
            raise ToolRefusal("this seat has no network access; http_get is not available to it")
        if not str(url).startswith(("http://", "https://")):
            raise ToolRefusal("http_get takes an http(s) URL")
        req = urllib.request.Request(url, headers={"User-Agent": "self-orch/seat"})
        with urllib.request.urlopen(req, timeout=60) as resp:   # noqa: S310 - explicit opt-in
            return resp.read().decode("utf-8", "replace")

    # ── dispatch ──────────────────────────────────────────────────────────

    def available(self) -> list[str]:
        names = ["read_file", "list_dir", "search_files"]
        if self.allow_write:
            names.append("write_file")
        if self.allow_shell:
            names.append("run")
        if self.allow_net:
            names.append("http_get")
        return names

    def call(self, name: str, args: dict[str, Any]) -> str:
        t0 = time.time()
        args = args or {}
        try:
            if name not in self.available():
                raise ToolRefusal(f"unknown or unavailable tool {name!r}; you have: {self.available()}")
            fn = getattr(self, name)
            result = self._clip(str(fn(**args)))
            ok = True
        except ToolRefusal as e:
            result, ok = f"REFUSED: {e}", False
        except TypeError as e:
            result, ok = f"ERROR: bad arguments for {name}: {e}", False
        except Exception as e:                       # noqa: BLE001 - report, never crash the seat
            result, ok = f"ERROR: {type(e).__name__}: {e}", False
        self.calls.append({
            "tool": name,
            "args": {k: (str(v)[:200]) for k, v in args.items()},
            "ok": ok,
            "elapsed_s": round(time.time() - t0, 3),
            "result_chars": len(result),
        })
        return result

    # ── schemas ───────────────────────────────────────────────────────────

    def _schemas(self) -> list[dict[str, Any]]:
        s = {
            "read_file": (
                "Read a text file inside the workspace. Returns numbered lines.",
                {"type": "object",
                 "properties": {"path": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"}},
                 "required": ["path"]},
            ),
            "list_dir": (
                "List a directory inside the workspace.",
                {"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
            ),
            "search_files": (
                "Regex-search file contents under a workspace directory.",
                {"type": "object",
                 "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                 "required": ["pattern"]},
            ),
            "write_file": (
                "Create or overwrite a file inside the workspace with exact content.",
                {"type": "object",
                 "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                 "required": ["path", "content"]},
            ),
            "run": (
                "Run a shell command with the workspace as the working directory. "
                "Use it to build, test and inspect -- this is how you verify rather than assert.",
                {"type": "object", "properties": {"command": {"type": "string"}},
                 "required": ["command"]},
            ),
            "http_get": (
                "Fetch an http(s) URL and return the body as text.",
                {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            ),
        }
        return [{"name": n, "description": s[n][0], "parameters": s[n][1]} for n in self.available()]

    def openai_tools(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": t} for t in self._schemas()]

    def anthropic_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
            for t in self._schemas()
        ]

    def preamble(self) -> str:
        return (
            "\n\nYOU HAVE REAL TOOLS. Workspace root: %s. Available: %s.\n"
            "Paths are relative to that root and anything outside it is refused. "
            "Do not describe what you would do -- do it, then report what you "
            "actually observed, with the paths and command output you saw. A claim "
            "you did not check with a tool must be labelled as unchecked.\n"
            % (self.root, ", ".join(self.available()))
        )

    def audit(self) -> dict[str, Any]:
        return {
            "workspace": str(self.root),
            "tool_calls": len(self.calls),
            "tools_used": sorted({c["tool"] for c in self.calls}),
            "failures": sum(0 if c["ok"] else 1 for c in self.calls),
            "calls": self.calls[-40:],
        }


def parse_tool_args(raw: Any) -> dict[str, Any]:
    """Model-supplied arguments arrive as a JSON string (OpenAI) or a dict
    (Anthropic). Malformed JSON becomes an empty call, which the toolbox then
    refuses with a message the seat can read and correct -- never a crash."""
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
