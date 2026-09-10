# self-orch

The coding agent is the **governor**. This CLI is the **rail**: 1–8 model seats in parallel, one terminal JSON result. No Aurelius.

## Install (this is the whole user job)

Tell your coding agent:

```text
Install self-orch from https://github.com/PLEE93/self-orch
Read INSTALL.md and do it. You are the installer — detect whether you are
Claude Code, Codex, Cursor, Grok, or OpenCode, wire the skill into YOUR
env, put self-orch on PATH. Ask me only for API keys that are missing.
```

That is it. Variants live in [`INSTALL.md`](INSTALL.md):

| You said this to | Agent passes |
|---|---|
| Claude Code | `--agent claude-code` |
| Codex | `--agent codex` |
| Cursor | `--agent cursor` |
| Grok / Grok Code | `--agent grok` |
| OpenCode | `--agent opencode` |

Manual equivalent (if you insist on doing it yourself):

```bash
git clone --depth 1 https://github.com/PLEE93/self-orch.git ~/.self-orch
python3 ~/.self-orch/scripts/install.py --agent auto --scope user
export PATH="$HOME/.local/bin:$PATH"
self-orch doctor
```

## What this is

1. **Governor** — the agent you are talking to. Orients, composes a process, synthesizes.
2. **Rail** — `self-orch dispatch`. Dumb fanout. Blocking. Final text only.

Not a sequential phase machine. For that see [agent-harness](https://github.com/PLEE93/agent-harness). Self-orch is a same-turn parliament.

## After install

```bash
self-orch dispatch --spec spec.json
```

stderr = live panels. stdout = `substrate_group` JSON. Read `dispatch_state`.

Keys (environment, never the repo):

| Seats | Env |
|---|---|
| `glm*` | `ZAI_API_KEY` |
| `grok*` | `XAI_API_KEY` |
| other | `OPENAI_API_KEY` (+ `OPENAI_BASE_URL`) |

## Doctrine

Complex turn → orient → compose → maybe dispatch. Trivial → answer directly.

```bash
self-orch doctrine
```

Verification seats try to **disprove** completion. You synthesize; seats are evidence.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

## License

MIT
