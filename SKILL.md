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

## Two ways to run this

**`self-orch dispatch`** is the primitive: N seats in parallel, once, returning
one group. You are the governor and you decide what happens next.

**`self-orch pipeline`** is the full process, and it enforces its own order:

    orient -> gather -> organize -> heavy -> review -> execute -> redteam
           -> deliver, or back to execute

Four things are enforced in code rather than asked for in a prompt:

- Stages run in that order. The result carries a ledger of what actually ran.
- The reviewer may not share a model family with the planner it reviews, and the
  red team may not share one with the seats that built the work. If the
  configured tiers make that impossible the pipeline refuses before spending
  anything.
- The red team's verdict is parsed. No readable verdict is a FAIL, never a pass.
- A red team that returns PASS without naming the attacks it ran is discarded
  and treated as FAIL. Agreement is not verification.

On FAIL the pipeline returns to execute carrying the red team's required
changes, up to `--max-loops` times, then stops and says why.

Use `pipeline` when the task deserves a process. Use `dispatch` when you only
need several seats at once and you are steering yourself.

## Model tiers

A seat's `model` may be a literal model id (`glm-5.3`, `claude-sonnet-4-5`) or
a difficulty tier: `tier1`, `tier2`, `tier3`, `tier4` (aliases: `t1`, `tier:1`,
`tier 1`). A tier expands to whatever model the user configured for it, so the
same spec runs on whatever models that user actually has.

- `tier1` - mechanical worker seats: mass reads, extraction, wide parallel work.
- `tier2` - seats needing real understanding: analysis, judgment, synthesis.
- `tier3` - falsification / verification seats. Keep this a different model
  family from the builders, so a verifier never grades its own family's work.
- `tier4` - the single heaviest step in a mission: plan, architecture, diagnosis.

Prefer tiers over literal ids when you do not know which models this user has.
Run `self-orch doctor` to see the current tier table and which credential each
vendor resolved.

## Rules

- Zero chatter before the dispatch. The tool call is the first emit.
- You synthesize. Never forward a seat verdict as the answer.
- Verification seats must try to **disprove** completion.
- Substrates must not launch agents or recurse into self-orch (enforced in the mission block).
- Max 8 seats. One parliament at a time.

Default routing: workers `glm-5.3`, synthesizer/verifier `grok-4.6`, judgment seats on the strongest model you actually have keys for.
