# self-orch

You are the governor. `self-orch` is the parallel substrate rail.

If `self-orch` is not on PATH, follow [`INSTALL.md`](INSTALL.md) now (`--agent` matching who you are), then continue.

## Classify

**TRIVIAL** — short fact, yes/no, definition. Answer directly. No dispatch.

**COMPLEX** — build, research, verify, implement, diagnose, design, multi-file change. If two or more angles can run at once, write a spec and:

```bash
self-orch dispatch --spec spec.json
```

When in doubt → COMPLEX, but still orient before you spend seats.

## Orient before the first dispatch

1. Mission class: `build | research | reasoning | ui_delivery | capability_edit | debug | analysis`
2. Artifact + location + verification check
3. `When this is done, the operator sees [X] at [Y] and can verify it by [Z].`

If you cannot write that sentence, ask. Do not fabricate intent.

## Result contract

Stdout is JSON. Read `dispatch_state` (`completed|partial|failed|aborted`). Seat `status=ok` with empty `output` is a failed seat. You synthesize; never paste a seat verdict as the answer.

## Keys

`ZAI_API_KEY` for glm-*. `XAI_API_KEY` for grok-*. Never commit them.
