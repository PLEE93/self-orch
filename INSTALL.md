# INSTALL — instructions for the coding agent

You are installing **self-orch** into **your own** environment. The human should not copy files.

Repo: `https://github.com/PLEE93/self-orch`

Do this now. Do not ask the human to run install commands. Ask them only for API keys that are actually missing.

## 0. Who you are

Pick **one** `--agent` value from your identity. Do not guess a different product.

| If you are | Pass |
|---|---|
| Claude Code / Claude in the terminal | `--agent claude-code` |
| Codex CLI / Codex in VS Code or Cursor | `--agent codex` |
| Cursor agent (Cursor Composer / Cursor CLI) | `--agent cursor` |
| Grok / Grok Build / Grok Code | `--agent grok` |
| OpenCode | `--agent opencode` |
| Anything else (Aider, Windsurf, a raw shell, unknown) | `--agent generic` |

If unsure, `--agent auto` then continue with the recipe that matches your name.

## 1. Get the repo and run the installer

```bash
if [ ! -d "$HOME/.self-orch/.git" ]; then
  git clone --depth 1 https://github.com/PLEE93/self-orch.git "$HOME/.self-orch"
fi
python3 "$HOME/.self-orch/scripts/install.py" --agent <YOUR_AGENT> --scope user
```

If you are already inside a checkout of this repo, run that instead:

```bash
python3 scripts/install.py --agent <YOUR_AGENT> --scope user
```

The installer:

1. Creates a venv and `pip install -e`s the CLI
2. Symlinks `self-orch` to `~/.local/bin/self-orch`
3. Copies `SKILL.md` into **your** skill directory
4. Writes a short always-on snippet into your rules file when that product has one

## 2. Per-agent landing paths (what “installed” means)

### claude-code

- Skill: `~/.claude/skills/self-orch/SKILL.md`
- Snippet: `~/.claude/CLAUDE.md` (marked `<!-- self-orch:start -->`)
- After install, `/self-orch` or automatic skill load should see it

### codex

- Skill: `~/.codex/skills/self-orch/SKILL.md`
- Snippet: `~/.codex/AGENTS.md`
- If you are working in a git project, also `--scope project` so `AGENTS.md` in the project root gets the same snippet

### cursor

- Skill: `~/.cursor/skills/self-orch/SKILL.md`
- Optional project rule: `python3 scripts/install.py --agent cursor --scope project` writes `.cursor/skills/self-orch/SKILL.md` and `.cursor/rules/self-orch.mdc`

### grok

- Skill: `$GROK_HOME/skills/self-orch/SKILL.md` (default `~/.grok/skills/self-orch/SKILL.md`)
- Rule: `~/.grok/rules/self-orch.md`
- Slash: `/self-orch` after skills reload

### opencode

- Skill: `~/.config/opencode/skills/self-orch/SKILL.md`
- Project: `--scope project` writes `.opencode/skills/self-orch/SKILL.md` and an `AGENTS.md` snippet

### generic

- CLI on PATH still. Skill copy: `~/.local/share/self-orch/skill/SKILL.md`
- Put `AGENTS.md` in the project (`--scope project`) so the next agent in that repo inherits the governor rules

## 3. PATH

If `command -v self-orch` fails:

```bash
mkdir -p "$HOME/.local/bin"
export PATH="$HOME/.local/bin:$PATH"
```

Persist PATH in the user’s shell rc only if it is not already there. Do not overwrite their rc file.

## 4. Keys — ask, do not invent

```bash
self-orch doctor
```

- GLM seats need `ZAI_API_KEY` (or `GLM_API_KEY`)
- Grok seats need `XAI_API_KEY` (or `GROK_API_KEY`)
- Other OpenAI-compatible models need `OPENAI_API_KEY` (+ optional `OPENAI_BASE_URL`)

If a key is missing, ask the human for that one key. Write it to `~/.self-orch/.env` (mode 0600) **or** their existing secret store. Never commit it. Never put it in the git repo.

## 5. Prove it

```bash
self-orch --version
self-orch doctrine | head
self-orch doctor
```

Then tell the human, in three lines:

1. Agent detected / used
2. Path to `self-orch`
3. Which keys are set vs missing

You are now the governor. For complex parallel work, follow the skill and run `self-orch dispatch`.
