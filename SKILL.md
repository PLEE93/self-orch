---
name: self-orch
description: Parallel self-orchestration rail for any coding agent. Use when a task needs multiple model seats at once (gather/verify/implement in parallel), when the user asks for self-orch, parliament, substrate seats, or "run N models in parallel."
---

# self-orch

You are the **governor**. The `self-orch` CLI is a dumb blocking rail: it launches 1–8 substrate seats in parallel, streams them, and returns one terminal `substrate_group` JSON on stdout.

If `self-orch` is not on PATH, stop and follow `INSTALL.md` (pass `--agent` for whoever you are: `claude-code`, `codex`, `cursor`, `grok`, `opencode`). Do not ask the human to copy files.

Keys live in the environment, never in the repo:

```bash
export ZAI_API_KEY=...     # glm-* seats
export XAI_API_KEY=...     # grok-* seats
```

## When to dispatch

Complex turns only. Orient first. Compose a process. Then call the rail if parallelism helps. Do not dispatch because the tool exists.

## Orient (before first dispatch)

Write these, concretely, or ask — never guess:

1. Mission class: `build | research | reasoning | ui_delivery | capability_edit | debug | analysis`
2. Artifact + location + verification check
3. Sentence: `When this is done, the operator sees [X] at [Y] and can verify it by [Z].`

## Call

```bash
self-orch dispatch --spec spec.json
```

`spec.json`:

```json
{
  "user_msg": "<verbatim user message>",
  "level": "standard",
  "seats": [
    {"role": "gather-a", "model": "glm-5.3", "phase": "research", "brief": "..."},
    {"role": "synthesizer", "model": "grok-4.6", "phase": "synthesize", "brief": "..."}
  ]
}
```

Stdout is the result. Read `dispatch_state`: `completed | partial | failed | aborted`. The RPC succeeding is not the seats succeeding.

Print `self-orch doctrine` if you need the full governor loop injected.

## Rules

- Zero chatter before the dispatch. The tool call is the first emit.
- You synthesize. Never forward a seat verdict as the answer.
- Verification seats must try to **disprove** completion.
- Substrates must not launch agents or recurse into self-orch (enforced in the mission block).
- Max 8 seats. One parliament at a time.

Default routing: workers `glm-5.3`, synthesizer/verifier `grok-4.6`, judgment seats on the strongest model you actually have keys for.
