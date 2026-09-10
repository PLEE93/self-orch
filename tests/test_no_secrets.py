"""Guard: this repo must never contain live credentials."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "dist", "build"}
SKIP_NAMES = {".env"}

# Patterns that mean a real secret leaked into source.
NEEDLES = (
    "ghp_",
    "gho_",
    "github_pat_",
    "xai-",
    "sk-or-",
    "Authorization: Bearer b30",
)


def test_repo_has_no_secret_needles():
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(p in SKIP_DIRS for p in path.parts):
            continue
        if path.name in SKIP_NAMES or path.name.endswith(".key"):
            continue
        if path.name == "test_no_secrets.py":
            continue
        if path.suffix in {".png", ".jpg", ".woff", ".pyc"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for needle in NEEDLES:
            if needle in text:
                hits.append(f"{path.relative_to(ROOT)}: {needle}")
    assert hits == [], "secret-like needles in repo:\n" + "\n".join(hits)
