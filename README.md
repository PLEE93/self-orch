# self-orch

Agent-agnostic **parallel self-orchestration** harness.

The coding agent (Claude Code, Cursor, Codex, OpenCode, Grok, …) is the **governor**. This package is the **rail**: it launches bounded substrate seats in parallel against OpenAI-compatible HTTP APIs, streams them in the terminal, and returns one terminal `substrate_group` JSON.

It is a portable extraction of the Aurelius self-orch kernel. Aurelius is not required.

## What this is

Two layers:

1. **Governor** — you. Orient, compose a process, pick seats, synthesize, decide delivery.
2. **Rail** — `self-orch dispatch`. Dumb fanout. 1–8 seats. Blocking. Final text only.

This is not a sequential phase machine. For that, see [agent-harness](https://github.com/PLEE93/agent-harness). Self-orch is the *same-turn parliament*: several models work at once, the governor reads the terminal group and continues.

## Install

Python 3.11+. No third-party dependencies.

```bash
git clone https://github.com/PLEE93/self-orch.git
cd self-orch
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

```bash
cp .env.example .env   # then edit; never commit .env
export ZAI_API_KEY=... # glm-5.3
export XAI_API_KEY=... # grok-4.6
```

## Usage

```bash
self-orch dispatch --spec examples/five-glm-one-grok.json
```

stderr: live parallel panels  
stdout: JSON the governor parses

```json
{
  "schema": "self_orch.substrate_group.v3",
  "dispatch_state": "completed",
  "substrates": [
    {"role": "gather-a", "model": "glm-5.3", "status": "ok", "output": "...", "elapsed_s": 4.2}
  ]
}
```

Read `dispatch_state`, not HTTP success.

One-off seats without a file (use `;` separators so briefs can contain commas):

```bash
self-orch dispatch --user-msg "What is 2+2?" \
  --seat 'role=worker;model=glm-5.3;brief=Answer in one integer.'
```

Print the governor loop for any agent:

```bash
self-orch doctrine
```

## Wire it into an agent

| Agent | What to add |
|---|---|
| Claude Code | `SKILL.md` → `~/.claude/skills/self-orch/SKILL.md` and/or project `CLAUDE.md` |
| Codex / OpenCode | project `AGENTS.md` (this repo already has it) |
| Cursor | `@SKILL.md` or a project rule pointing here |
| Grok / anything else | tell the agent to read `SKILL.md` |

The agent stays the governor. It writes a spec, runs the CLI, synthesizes.

## Providers

| Model prefix | Env | Default endpoint |
|---|---|---|
| `glm*` | `ZAI_API_KEY` or `GLM_API_KEY` | `https://api.z.ai/api/coding/paas/v4` |
| `grok*` | `XAI_API_KEY` or `GROK_API_KEY` | `https://api.x.ai/v1` |
| anything else | `OPENAI_API_KEY` | `OPENAI_BASE_URL` or api.openai.com |

Override bases with `ZAI_BASE_URL` / `XAI_BASE_URL` / `OPENAI_BASE_URL`.

## Doctrine (short)

- Orient before the first complex dispatch. If the end-result sentence cannot be written, ask.
- Compose the process. A seat that prevents no named failure does not fire.
- Verification seats try to **disprove** that the work is done.
- Substrates do not launch agents and do not recurse into self-orch.
- You synthesize. Seats are evidence, not the answer.

Full text: `SKILL.md`, or `self-orch doctrine`.

## Tests

```bash
pip install -e '.[dev]'
pytest -q
```

Offline unit tests only. Live multi-seat runs need keys in the environment.

## Limits

- Max 8 seats per dispatch
- Brief ≤ 8000 chars, user_msg ≤ 12000 chars
- No in-process wall-clock timeout on seat HTTP
- One parliament at a time (the governor enforces this)

## License

MIT
