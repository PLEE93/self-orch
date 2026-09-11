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

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MAX_RESULT_CHARS = 8000
DEFAULT_SHELL_TIMEOUT_S = 120

# `run` used to be `subprocess.run(command, shell=True, ...)`. That made every
# other permission in this file decorative: a seat with no write power could
# write with `echo x > f`, a seat with no network could fetch with `curl`, and a
# seat confined to the workspace could read `~/.ssh` with `cat`. A permission
# that one tool can walk around is not a permission.
#
# So `run` executes ONE argv with no shell at all. Operators are lexed out and
# refused rather than passed through, which is what makes the refusal reliable:
# `>` inside a quoted argument stays an ordinary character, `>` between
# arguments is a token made only of punctuation and is rejected.
SHELL_OPERATOR_CHARS = set("();<>|&")

# Without namespaces we cannot make "no network" true by construction, so the
# honest move is to refuse the binaries that are network access, and to say in
# the docs that a determined runtime can still open a socket. Naming the limit
# beats implying a containment we do not have.
NET_BINARIES = frozenset({
    "curl", "wget", "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp",
    "rsync", "ftp",
})

# A shell invoked as an argv is the shell again, one level down.
SHELL_BINARIES = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh"})


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
    # Set when the seat that owns this toolbox outlived its deadline. A Python
    # thread cannot be killed from outside, so a timed-out seat keeps running;
    # what this flag does is take its hands off the workspace, so a seat the
    # dispatch has already written off as stalled cannot keep editing files
    # that later stages are busy inspecting.
    cancelled: bool = False
    # True when this box is a throwaway copy whose contents are discarded. It is
    # what makes "read-only with a command tool" an honest configuration rather
    # than a claim the next command falsifies -- see __post_init__.
    disposable: bool = False

    def __post_init__(self) -> None:
        # Measured, not theorised: a live read-only seat with a command tool was
        # asked to escape and did it on the first try with `touch PWNED.txt`.
        # There is no shell involved and no operator to refuse -- running a
        # program that writes IS writing, and the list of programs that can write
        # is every program. Denylisting `touch`, `tee`, `sed -i`, `dd`, `cp`,
        # `make` and their thousand cousins is a game that cannot be won, and
        # each round of it makes the boundary look stronger while it stays open.
        #
        # So the boundary moved. A seat that may run commands and may not write
        # is only allowed to exist inside a copy that gets thrown away. Its
        # writes are real; they land nowhere that matters, and the pipeline never
        # integrates them. Containment by disposability, stated plainly, beats a
        # permission flag that the first command walks around.
        if self.allow_shell and not self.allow_write and not self.disposable:
            raise ValueError(
                "a seat that can run commands can write -- running a program that writes is "
                "writing, and no denylist closes that. Build this seat with disposable=True "
                "(a throwaway copy of the workspace, changes discarded) or grant it write."
            )

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
            disposable=bool(raw.get("disposable")),
        )

    def derive(self, *, allow_write: bool, allow_shell: bool) -> "Toolbox":
        """A box with narrower powers.

        When the narrowing asks for commands without write -- the red team's
        shape, and the gathering seats' -- this returns a disposable copy rather
        than a permission that the first command would walk around.
        """
        want_shell = self.allow_shell and allow_shell
        want_write = self.allow_write and allow_write
        if want_shell and not want_write:
            return self.snapshot("readonly", allow_write=False)
        return Toolbox(
            root=self.root,
            allow_write=self.allow_write and allow_write,
            allow_shell=self.allow_shell and allow_shell,
            allow_net=self.allow_net,
            max_result_chars=self.max_result_chars,
            shell_timeout_s=self.shell_timeout_s,
        )

    # ── isolation ───────────────────────────────────────────────────

    def snapshot(self, label: str, *, allow_write: bool | None = None) -> "Toolbox":
        """A private copy of the workspace with the same powers.

        Parallel execution seats used to share one directory, so two slices
        could edit the same file at once, overwrite each other, and run tests
        against a tree that was half-written by somebody else. Several agents
        poking one filesystem is a race, not a decomposition. Each seat now
        works in its own copy and the pipeline integrates afterwards, where a
        collision is visible and can be refused instead of silently winning.
        """
        dest = Path(tempfile.mkdtemp(prefix=f"self-orch-{label}-")) / "work"
        shutil.copytree(self.root, dest, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"))
        return Toolbox(
            root=dest.resolve(),
            allow_write=self.allow_write if allow_write is None else allow_write,
            allow_shell=self.allow_shell,
            allow_net=self.allow_net,
            max_result_chars=self.max_result_chars,
            shell_timeout_s=self.shell_timeout_s,
            disposable=True,
        )

    def manifest(self) -> dict[str, str]:
        """path -> content hash, for every file in the tree. The before/after
        pair is what turns 'the seat says it edited things' into a fact."""
        out: dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", "node_modules"}]
            for fn in filenames:
                fp = Path(dirpath) / fn
                try:
                    out[str(fp.relative_to(self.root))] = hashlib.sha256(fp.read_bytes()).hexdigest()
                except OSError:
                    continue
        return out

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
        if self.cancelled:
            raise ToolRefusal("this seat passed its deadline; its tools are closed")
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

    def _argv(self, command: str) -> list[str]:
        """Lex a seat-supplied command into one argv, or refuse it.

        Refusing is the point. There is no shell, so `a > b`, `a | b`, `a && b`
        and `$(a)` cannot mean what they mean in a shell -- and silently running
        `a` with the literal arguments `>` and `b` would be worse than refusing,
        because the seat would believe its redirection happened.
        """
        raw = str(command or "").strip()
        if not raw:
            raise ToolRefusal("empty command")
        try:
            lex = shlex.shlex(raw, posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            argv = list(lex)
        except ValueError as e:
            raise ToolRefusal(f"could not parse command ({e}); quote it properly") from e
        if not argv:
            raise ToolRefusal("empty command")
        for tok in argv:
            if tok and set(tok) <= SHELL_OPERATOR_CHARS:
                raise ToolRefusal(
                    f"{tok!r} is a shell operator and there is no shell here: run executes a "
                    "single command directly. Pipes, redirection, chaining and command "
                    "substitution are not available -- run one step per call and combine the "
                    "results yourself. To write a file, use write_file."
                )
        exe = Path(argv[0]).name
        if exe in SHELL_BINARIES:
            raise ToolRefusal(
                f"{exe!r} is a shell; invoking one would hand back exactly the operators this "
                "tool refuses. Run the real command directly."
            )
        if not self.allow_write and exe == "python3" and "-c" in argv:
            # Not a denylist of dangerous programs -- that game is unwinnable.
            # This is the one common case where a read-only seat would otherwise
            # get a general-purpose writer by accident.
            raise ToolRefusal(
                "this seat is read-only, and `python3 -c` is a general-purpose writer. "
                "Run a test or an inspection command instead."
            )
        if not self.allow_net and exe in NET_BINARIES:
            raise ToolRefusal(
                f"this seat has no network access and {exe!r} is network access. "
                "If the task genuinely needs the network, the operator must grant it."
            )
        return argv

    def _env(self) -> dict[str, str]:
        """A scrubbed environment: credentials in the operator's shell are not
        part of a seat's brief, and a seat that never sees a key cannot leak one
        into its own output."""
        keep = {"PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "SHELL", "USER", "PWD"}
        env = {k: v for k, v in os.environ.items() if k in keep}
        env["SELF_ORCH_SEAT"] = "1"
        return env

    def run(self, command: str) -> str:
        if self.cancelled:
            raise ToolRefusal(
                "this seat passed its dispatch deadline and was reported as stalled; "
                "its tools are closed so it cannot alter work later stages are reading"
            )
        if not self.allow_shell:
            raise ToolRefusal("this seat may not run commands; run is not available to it")
        argv = self._argv(command)
        try:
            proc = subprocess.run(
                argv, shell=False, cwd=str(self.root), capture_output=True,
                text=True, timeout=self.shell_timeout_s, env=self._env(),
            )
        except FileNotFoundError:
            raise ToolRefusal(f"{argv[0]!r} is not an executable on this machine") from None
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
                "Run ONE command (no shell: no pipes, redirection, chaining or substitution) with the workspace as the working directory. "
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
            "Paths are relative to that root and anything outside it is refused. %s"
            "run executes ONE command directly with no shell -- no pipes, redirection, "
            "chaining or substitution; run one step per call. "
            "Do not describe what you would do -- do it, then report what you "
            "actually observed, with the paths and command output you saw. A claim "
            "you did not check with a tool must be labelled as unchecked.\n"
            % (self.root, ", ".join(self.available()),
               ("This workspace is a DISPOSABLE COPY: your writes are real but are "
                "discarded afterwards, so inspect and test freely. " if self.disposable else ""))
        )

    def audit(self) -> dict[str, Any]:
        return {
            "workspace": str(self.root),
            "workspace_backed": True,
            "disposable": self.disposable,
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


def integrate_slices(base: "Toolbox", slices: list["Toolbox"],
                     before: dict[str, str]) -> dict[str, Any]:
    """Fold each execution slice's private copy back into the real workspace.

    This is the half of isolation that does the work. Giving every seat its own
    copy stops the race; without a merge it would also stop the result reaching
    the user. Integration is deliberately unforgiving: when two slices changed
    the same file to different content, neither is applied and the collision is
    reported. Picking a winner silently is how a parallel build loses half its
    work and nobody finds out until it ships.
    """
    claims: dict[str, list[tuple[int, str, Path]]] = {}
    deletions: dict[str, list[int]] = {}
    for i, box in enumerate(slices):
        after = box.manifest()
        for rel, digest in after.items():
            if before.get(rel) != digest:
                claims.setdefault(rel, []).append((i, digest, box.root / rel))
        for rel in before:
            if rel not in after:
                deletions.setdefault(rel, []).append(i)

    applied: list[str] = []
    conflicts: list[str] = []
    for rel, cl in sorted(claims.items()):
        digests = {d for _, d, _ in cl}
        if len(digests) > 1:
            conflicts.append(
                f"{rel}: slices {[i + 1 for i, _, _ in cl]} each changed it to different "
                f"content; neither was applied"
            )
            continue
        src = cl[0][2]
        dst = base.root / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            applied.append(rel)
        except OSError as e:                       # pragma: no cover - fs edge
            conflicts.append(f"{rel}: could not be applied ({e})")

    removed: list[str] = []
    for rel, owners in sorted(deletions.items()):
        if rel in claims:
            conflicts.append(f"{rel}: one slice deleted it while another edited it; left in place")
            continue
        try:
            (base.root / rel).unlink()
            removed.append(rel)
        except OSError:
            pass

    return {
        "applied": applied,
        "removed": removed,
        "conflicts": conflicts,
        "slice_roots": [str(b.root) for b in slices],
    }


def integration_report(result: dict[str, Any]) -> str:
    lines = [
        f"applied {len(result['applied'])} changed file(s) from {len(result['slice_roots'])} "
        f"isolated execution slices",
    ]
    if result["applied"]:
        lines.append("changed: " + ", ".join(result["applied"][:40]))
    if result["removed"]:
        lines.append("deleted: " + ", ".join(result["removed"][:40]))
    if result["conflicts"]:
        lines.append("COLLISIONS (not applied, and the work they carried is not in the tree):")
        lines += [f"  - {c}" for c in result["conflicts"]]
    else:
        lines.append("no collisions between slices")
    return "\n".join(lines)
