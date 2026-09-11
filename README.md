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
- **Evidence, not prose.** When the red team holds a workspace, a PASS whose
  audit shows it never opened the artifact is discarded: the attacks were
  written, not run.
- **No empty seats.** A red team that returns PASS without naming the attacks it
  ran is discarded and treated as FAIL -- and the check reads what is written
  under `ATTACKS RUN`, not merely that the heading exists, so `ATTACKS RUN /
  none` is an empty seat too. Agreement is not verification.
- **Review fails closed.** The pre-execution review must declare an approval to
  let the run proceed. A missing, malformed or failing verdict stops it; an
  unreadable review is not an approval.
- **Presence, not just order.** `assert_stage_order` refuses a ledger that is
  missing any mandatory stage, not only one that ran them out of order.

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


### Seats with real tools

Without a workspace every seat is a text-only model call: it can reason about
work and describe it, but it cannot open a file, run a test, or check a claim.
That makes two of the pipeline's own instructions impossible to follow --
execute is told to implement and verify, and the red team is told to check the
artifact rather than the claim.

Give the run a workspace and the acting stages get real tools:

```bash
self-orch pipeline --user-msg "fix the failing test in calc.py" --workspace ./myrepo
```

| Stage | reads | runs commands | writes the real workspace |
|---|---|---|---|
| `gather` | yes | yes, in a throwaway copy | no |
| `execute` | yes | yes | **yes**, and parallel slices are merged (below) |
| `redteam` | yes | yes, in a throwaway copy of the integrated result | no |
| orient / organize / heavy / review / deliver | no | no | no |

Tools are `read_file`, `list_dir`, `search_files`, `write_file`, `run`, and
`http_get` (only with `--allow-net`). Every path is resolved **after symlinks**
and refused if it leaves the workspace, every result is clipped so one large
file cannot blow the next stage's context, and every call is recorded: the
result JSON carries `tool_calls` per stage and the audit trail per seat. Use
`--no-shell` for a workspace the seats may read and write but not execute in.

**`run` is not a shell.** It executes one command directly. Pipes, redirection,
chaining and command substitution are lexed out and refused rather than passed
through, because through a real shell every other permission here was
decorative: `echo x > f` was a write, `curl` was the network, `cat ~/.ssh/id_rsa`
was the world.

**A seat that can run commands can write.** Refusing `>` does not change that --
`touch f` needs no operator, and denylisting the programs that can write is a
game with no last move. A live read-only seat was asked to escape and did it on
its first try. So the boundary is not a permission, it is disposability: a stage
that may run commands and may not write gets its **own throwaway copy** of the
workspace. Its writes are real and land nowhere that matters, and nothing
integrates them. Anything stronger than that needs a container, and this repo
does not pretend to be one.

**Parallel execution slices do not share a tree.** Each execute seat works in
its own copy, and the pipeline integrates them afterwards by comparing content
hashes. Two slices that changed the same file to different content is a
**collision**: neither is applied, the run is marked degraded, the collision is
handed to the red team, and a PASS on a collided tree is turned into a FAIL --
because the tree the red team approved is missing work. Several agents editing
one directory at once is a race whose winner is whoever wrote last.

### Nothing waits forever

A provider that accepts the connection and then goes quiet used to hang the
whole run, because the parent waited on every seat. Two deadlines now bound it,
both overridable, and `0` restores unbounded waiting deliberately:

| Variable | Default | What it bounds |
|---|---|---|
| `SELF_ORCH_SEAT_TIMEOUT_S` | `600` | each socket read, so a silent stream dies too |
| `SELF_ORCH_DISPATCH_TIMEOUT_S` | `1800` | the whole parallel round |

A round that outlives its deadline returns **partial**: every seat that finished
keeps its output, the stalled ones are reported as stalled with an anomaly, and
the governor gets an answer instead of a process that never returns.

### Real decomposition, not an ensemble

`--execute` seats each get a **different** slice of the work. Pass your own with
repeated `--execute-slice`, or let the pipeline supply its defaults (primary
change / seams / checks / edges). Two seats with the same brief and the same
model are an ensemble doing one job twice, so identical slices are refused.

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
