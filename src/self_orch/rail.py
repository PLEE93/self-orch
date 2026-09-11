"""Blocking parallel substrate rail. One call, N seats, one terminal group."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from .doctrine import is_verification_role, maybe_append_falsification, mission_block
from .providers import ProviderError, auth_headers, chat_url, model_family, resolve_provider

MAX_SEATS = 8
MAX_BRIEF_CHARS = 8000
MAX_USER_MSG_CHARS = 12000
SCHEMA = "self_orch.substrate_group.v3"
ANTHROPIC_MAX_TOKENS = 8192


@dataclass
class SeatSpec:
    role: str
    model: str
    brief: str
    phase: str = ""
    query_angle: str = ""
    ultrathink: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SeatSpec":
        role = str(raw.get("role") or "").strip()
        model = str(raw.get("model") or "").strip()
        brief = str(raw.get("brief") or raw.get("role_brief") or "").strip()
        if not role or not model or not brief:
            raise ValueError("each seat needs role, model, and brief")
        if len(brief) > MAX_BRIEF_CHARS:
            raise ValueError(f"brief for {role!r} exceeds {MAX_BRIEF_CHARS} chars")
        return cls(
            role=role,
            model=model,
            brief=brief,
            phase=str(raw.get("phase") or "").strip(),
            query_angle=str(raw.get("query_angle") or "").strip(),
            ultrathink=bool(raw.get("ultrathink") or raw.get("extended_thinking")),
        )


@dataclass
class DispatchResult:
    dispatch_state: str
    status: str
    seats: list[dict[str, Any]] = field(default_factory=list)
    level: str = "standard"
    user_msg: str = ""
    elapsed_s: float = 0.0
    anomalies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_type": "substrate_group",
            "schema": SCHEMA,
            "ok": self.dispatch_state == "completed",
            "status": self.status,
            "dispatch_state": self.dispatch_state,
            "level": self.level,
            "elapsed_s": round(self.elapsed_s, 3),
            "anomalies": self.anomalies,
            "substrates": self.seats,
        }


def build_messages(role: str, brief: str, user_msg: str, history: list[dict] | None = None) -> list[dict]:
    brief = maybe_append_falsification(role, brief)
    msgs: list[dict] = [{"role": "system", "content": mission_block(role, brief)}]
    for m in history or []:
        r = m.get("role")
        c = m.get("content")
        if r in {"system", "user", "assistant"} and c:
            msgs.append({"role": r, "content": str(c)})
    current = (user_msg or "").strip()
    if current:
        last_user = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                last_user = str(m.get("content") or "").strip()
                break
        if last_user != current:
            msgs.append({"role": "user", "content": current})
    if not any(m.get("role") == "user" for m in msgs):
        msgs.append({
            "role": "user",
            "content": "Execute your substrate mission brief now and return your final answer.",
        })
    return msgs


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Anthropic's Messages API takes system prompt as a top-level field, not
    a message with role=system, and only accepts user/assistant in `messages`."""
    system_parts = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
    rest = [
        {"role": m["role"], "content": str(m.get("content") or "")}
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]
    if not rest:
        rest = [{"role": "user", "content": "Execute your substrate mission brief now and return your final answer."}]
    return "\n\n".join(p for p in system_parts if p), rest


def _parse_sse_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return {"done": True}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _delta_text(chunk: dict[str, Any]) -> tuple[str, str]:
    """Return (content, reasoning) from an OpenAI-style chunk."""
    ch = (chunk.get("choices") or [{}])[0]
    delta = ch.get("delta") or {}
    msg = ch.get("message") or {}
    content = delta.get("content") or msg.get("content") or ""
    reasoning = delta.get("reasoning_content") or msg.get("reasoning_content") or ""
    return str(content or ""), str(reasoning or "")


def _nonstream_text(body: dict[str, Any]) -> str:
    ch = (body.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    return str(msg.get("content") or "")


def _anthropic_delta_text(chunk: dict[str, Any]) -> tuple[str, bool]:
    """Return (text, done) from one Anthropic SSE `data:` payload.
    Anthropic streams `content_block_delta` events with a `text_delta`
    payload and signals completion with a `message_stop` event -- there is
    no OpenAI-style [DONE] sentinel."""
    kind = chunk.get("type")
    if kind == "content_block_delta":
        delta = chunk.get("delta") or {}
        if delta.get("type") == "text_delta":
            return str(delta.get("text") or ""), False
        return "", False
    if kind in ("message_stop", "error"):
        return "", True
    return "", False


def _anthropic_nonstream_text(body: dict[str, Any]) -> str:
    blocks = body.get("content") or []
    return "".join(str(b.get("text") or "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")


def call_seat(
    spec: SeatSpec,
    user_msg: str,
    history: list[dict] | None = None,
    on_token: Callable[[str, str], None] | None = None,
    stream: bool = True,
) -> dict[str, Any]:
    t0 = time.time()
    brief = spec.brief
    if spec.query_angle:
        brief = f"ANGLE: {spec.query_angle}\n\n{brief}"
    try:
        provider = resolve_provider(spec.model)
    except ProviderError as e:
        return _error_seat(spec, str(e), time.time() - t0)

    messages = build_messages(spec.role, brief, user_msg, history)
    is_anthropic = provider.chat_shape == "anthropic"

    if is_anthropic:
        system_text, turns = _split_system(messages)
        payload: dict[str, Any] = {
            "model": spec.model,
            "system": system_text,
            "messages": turns,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "stream": stream,
        }
    else:
        payload = {
            "model": spec.model,
            "messages": messages,
            "stream": stream,
        }

    data = json.dumps(payload).encode("utf-8")
    headers = auth_headers(provider)
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "text/event-stream" if stream else "application/json"
    req = urllib.request.Request(chat_url(provider), data=data, method="POST", headers=headers)

    content = ""
    try:
        # No wall-clock timeout: stalls belong to the operator/infra, not this rail.
        with urllib.request.urlopen(req, timeout=None) as resp:
            if stream:
                for raw in resp:
                    parsed = _parse_sse_line(raw.decode("utf-8", "replace"))
                    if not parsed:
                        continue
                    if is_anthropic:
                        tok, done = _anthropic_delta_text(parsed)
                        if done:
                            break
                    else:
                        if parsed.get("done"):
                            break
                        tok, _reason = _delta_text(parsed)
                    if tok:
                        content += tok
                        if on_token:
                            on_token(spec.role, content)
            else:
                body = json.loads(resp.read().decode("utf-8", "replace"))
                content = _anthropic_nonstream_text(body) if is_anthropic else _nonstream_text(body)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", "replace")[:500]
        return _error_seat(spec, f"HTTP {e.code}: {err}", time.time() - t0)
    except Exception as e:
        return _error_seat(spec, f"{type(e).__name__}: {e}", time.time() - t0)

    elapsed = time.time() - t0
    text = content.strip()
    status = "ok" if text else "empty"
    return {
        "role": spec.role,
        "model": spec.model,
        "phase": spec.phase,
        "status": status,
        "output": text,
        "elapsed_s": round(elapsed, 3),
        "error": None if text else "empty final content (reasoning-only or blank)",
    }


def _error_seat(spec: SeatSpec, error: str, elapsed: float) -> dict[str, Any]:
    return {
        "role": spec.role,
        "model": spec.model,
        "phase": spec.phase,
        "status": "error",
        "output": "",
        "elapsed_s": round(elapsed, 3),
        "error": error[:500],
    }


def _model_diversity_anomalies(specs: list[SeatSpec]) -> list[str]:
    """Falsification-mandate text is code-enforced on verification roles
    (doctrine.maybe_append_falsification); model-family diversity between a
    verifier and the builder(s) it is checking was not. This does not block
    the dispatch (a single-key setup may only have one family available) --
    it surfaces the gap as an anomaly so the governor sees it and can choose
    a different tier next time."""
    verifier_families = {model_family(s.model) for s in specs if is_verification_role(s.role)}
    builder_families = {model_family(s.model) for s in specs if not is_verification_role(s.role)}
    overlap = verifier_families & builder_families
    if overlap and verifier_families and builder_families:
        return [
            "model-diversity: verification seat(s) share a model family (%s) with builder seat(s) "
            "in this dispatch -- a verifier auditing its own family is a weaker check than routing "
            "it to a different tier. Not blocked; consider SELF_ORCH_TIER3_MODEL for verification."
            % ", ".join(sorted(overlap))
        ]
    return []


def dispatch(
    seats: list[SeatSpec | dict[str, Any]],
    user_msg: str,
    level: str = "standard",
    history: list[dict] | None = None,
    on_update: Callable[[list[dict[str, Any]]], None] | None = None,
) -> DispatchResult:
    t0 = time.time()
    specs = [s if isinstance(s, SeatSpec) else SeatSpec.from_dict(s) for s in seats]
    if not specs:
        raise ValueError("need at least one seat")
    if len(specs) > MAX_SEATS:
        raise ValueError(f"max {MAX_SEATS} seats, got {len(specs)}")
    user_msg = (user_msg or "").strip()
    if len(user_msg) > MAX_USER_MSG_CHARS:
        raise ValueError(f"user_msg exceeds {MAX_USER_MSG_CHARS} chars")

    anomalies = _model_diversity_anomalies(specs)

    live: list[dict[str, Any]] = [
        {
            "role": s.role,
            "model": s.model,
            "phase": s.phase,
            "status": "pending",
            "ui_status": "pending",
            "output": "",
            "preview": "",
            "elapsed_s": None,
            "error": None,
        }
        for s in specs
    ]
    by_role = {s.role: i for i, s in enumerate(specs)}
    lock = __import__("threading").Lock()

    def touch(role: str, **kw: Any) -> None:
        with lock:
            i = by_role[role]
            live[i].update(kw)
            if on_update:
                on_update(live)

    def run(spec: SeatSpec) -> dict[str, Any]:
        touch(spec.role, ui_status="stream", status="running")

        def on_token(_role: str, acc: str) -> None:
            touch(spec.role, preview=acc[-80:], ui_status="stream")

        result = call_seat(spec, user_msg, history=history, on_token=on_token)
        touch(
            spec.role,
            status=result["status"],
            ui_status=result["status"],
            output=result.get("output") or "",
            preview=(result.get("output") or result.get("error") or "")[-80:],
            elapsed_s=result.get("elapsed_s"),
            error=result.get("error"),
        )
        return result

    results: list[dict[str, Any] | None] = [None] * len(specs)
    with ThreadPoolExecutor(max_workers=len(specs), thread_name_prefix="seat") as pool:
        futs = {pool.submit(run, spec): idx for idx, spec in enumerate(specs)}
        for fut in as_completed(futs):
            idx = futs[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                results[idx] = _error_seat(specs[idx], f"{type(e).__name__}: {e}", 0.0)

    seats_out = [r or _error_seat(specs[i], "missing result", 0.0) for i, r in enumerate(results)]
    statuses = [s["status"] for s in seats_out]
    if all(st == "ok" for st in statuses):
        state = "completed"
    elif all(st == "error" for st in statuses):
        state = "failed"
    elif any(st == "ok" for st in statuses):
        state = "partial"
    else:
        state = "failed"
    return DispatchResult(
        dispatch_state=state,
        status=state,
        seats=seats_out,
        level=level,
        user_msg=user_msg,
        elapsed_s=time.time() - t0,
        anomalies=anomalies,
    )
