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
3. **Pipeline** — `self-orch pipeline`. The rail run as an ordered process with
   the adversarial gates enforced.

The rail on its own is a **same-turn parliament**: N seats at once, one group
back, you steer. The pipeline is the process built on top of it:

```
orient → gather → organize → heavy → review → execute → redteam
       → deliver, or back to execute
```

Four things there are enforced in code, not asked for in a prompt:

- **Order.** Stages run in that sequence and the result carries a ledger of what
  actually ran. `assert_stage_order` refuses a ledger that skipped or reordered.
- **Independence.** The reviewer may not share a model family with the planner it
  reviews, and the red team may not share one with the seats that built the work.
  If your tier configuration makes that impossible, the pipeline **refuses before
  spending anything** rather than running a check that cannot be independent.
- **A real verdict.** The red team's output is parsed. No readable verdict is a
  FAIL, never a pass — the gate fails closed.
- **No empty seats.** A red team that returns PASS without naming the attacks it
  ran is discarded and treated as FAIL. Agreement is not verification.

On FAIL the pipeline loops back to execute carrying the red team's required
changes, bounded by `--max-loops`, then stops and says why.

```bash
self-orch stages                      # the enforced order + the model per stage
self-orch pipeline --user-msg "..."   # run the whole process
```

Use `pipeline` when the task deserves a process. Use `dispatch` when you just
want several seats at once and you are steering yourself. If you want a general
sequential phase machine for other shapes of work, see
[agent-harness](https://github.com/PLEE93/agent-harness).

## After install

```bash
self-orch dispatch --spec spec.json
```

stderr = live panels. stdout = `substrate_group` JSON. Read `dispatch_state`.

Credentials — **an API key is not required**. If you are already logged in to a
coding agent CLI, self-orch reuses that session:

| Family | API key | or account login |
|---|---|---|
| `glm*` | `ZAI_API_KEY` | — |
| `grok*` | `XAI_API_KEY` | Grok CLI |
| `claude*` | `ANTHROPIC_API_KEY` | Claude Code (`claude setup-token`) |
| other (`gpt*`, …) | `OPENAI_API_KEY` (+ `OPENAI_BASE_URL`) | Codex CLI |

Run `self-orch doctor` to see which credential each family resolved, and where
it came from.

### Difficulty tiers

Write `"model": "tier1".."tier4"` in a seat spec instead of a literal model id,
and point each tier at a model you actually have. Literal ids keep working.

| Tier | For | Default | Override |
|---|---|---|---|
| `tier1` | mechanical seats: mass work, cheap, wide, many in parallel | `glm-5.3` | `SELF_ORCH_TIER1_MODEL` |
| `tier2` | seats needing real understanding: analysis, judgment, synthesis | `claude-sonnet-4-5` | `SELF_ORCH_TIER2_MODEL` |
| `tier3` | falsification: review and red team — keep this a **different family** from the builders | `gpt-5.1` | `SELF_ORCH_TIER3_MODEL` |
| `tier4` | the single heaviest step: plan, architecture, diagnosis | `claude-opus-4-5` | `SELF_ORCH_TIER4_MODEL` |

Which stage runs on which tier: `orient`/`organize`/`deliver` on tier2,
`gather`/`execute` on tier1, `review`/`redteam` on tier3, `heavy` on tier4.
Defaults are starting points, not a claim about which model is best — check your
provider's current model list before shipping.

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
