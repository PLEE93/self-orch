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

## Giving seats real tools

Seats are text-only unless you give the run a workspace. With one, `gather`,
`execute` and `redteam` can read, search and run commands, and `execute` can
also write:

```bash
self-orch pipeline --user-msg "..." --workspace /path/to/repo
```

For an ad-hoc fanout, `self-orch dispatch --workspace /path/to/repo` gives every
seat the same tools, or put a `tools` block on an individual seat in the spec:

```json
{"role": "implementer", "model": "tier1", "brief": "...",
 "tools": {"workspace": "/path/to/repo", "allow_write": true, "allow_shell": true}}
```

Paths that leave the workspace are refused, results are clipped, and every call
is audited into the result JSON (`tool_calls`, `tools_used`). Tell a tool-having
seat to **do the work and report what it observed**, not to describe what it
would do -- and treat any claim it did not check with a tool as unchecked.

Three things about that workspace are worth knowing before you brief a seat:

- `run` executes ONE command with no shell. No pipes, redirection, chaining or
  substitution -- run one step per call. To write a file, use `write_file`.
- A seat that runs commands can write, whatever its flags say, so `gather` and
  `redteam` are given a **disposable copy** instead: their writes are real and
  are discarded. `redteam`'s copy is taken after the execution slices have been
  integrated, so it judges the tree that actually exists and cannot alter it.
- Parallel `execute` seats each get their own copy and are merged afterwards.
  Two slices changing the same file to different content collides: neither lands,
  and a red-team PASS over a collided tree is downgraded to FAIL.
- A workspace-holding red team that returns PASS without a single tool call is
  discarded -- named attacks with no audit behind them are a story about testing.

Deadlines: `SELF_ORCH_SEAT_TIMEOUT_S` (default 600) bounds each socket read and
`SELF_ORCH_DISPATCH_TIMEOUT_S` (default 1800) bounds the whole round. A stalled
seat comes back as an error inside a `partial` group -- read them, do not assume
`partial` means the work failed.

## Rules

- Zero chatter before the dispatch. The tool call is the first emit.
- You synthesize. Never forward a seat verdict as the answer.
- Verification seats must try to **disprove** completion.
- Substrates must not launch agents or recurse into self-orch (enforced in the mission block).
- Max 8 seats. One parliament at a time.

Default routing: workers `glm-5.3`, synthesizer/verifier `grok-4.6`, judgment seats on the strongest model you actually have keys for.
