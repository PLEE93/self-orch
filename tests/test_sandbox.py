"""The boundary tests.

Each of these exists because a documented boundary was, at some point, only
documented. They are written from the attacker's side: not "does the happy path
work" but "can a seat get the thing the README says it cannot have".
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from self_orch.seat_tools import Toolbox, ToolRefusal, integrate_slices, integration_report
from self_orch.stages import (
    PipelineRefusal,
    assert_stage_order,
    evaluate_redteam,
)


def box(tmp_path, **kw) -> Toolbox:
    return Toolbox(root=Path(tmp_path).resolve(), **kw)


# ── 1. the shell was a hole in every other permission ────────────────────────

def test_a_read_only_seat_cannot_write_through_redirection(tmp_path):
    """Prevents: allow_write=False being decorative. `echo x > f` used to run
    through a real shell, so a red team could edit the artifact it was judging."""
    b = box(tmp_path, allow_shell=True, allow_write=False, disposable=True)
    out = b.call("run", {"command": "echo pwned > owned.txt"})
    assert "REFUSED" in out
    assert "shell operator" in out
    assert not (Path(tmp_path) / "owned.txt").exists()


def test_chaining_and_substitution_are_refused(tmp_path):
    b = box(tmp_path, allow_shell=True, allow_write=True)
    for cmd in ["true && touch a", "true; touch b", "echo $(whoami)", "cat x | tee c"]:
        out = b.call("run", {"command": cmd})
        assert "REFUSED" in out, cmd
    assert not list(Path(tmp_path).glob("[abc]"))


def test_a_shell_binary_cannot_be_used_to_get_the_shell_back(tmp_path):
    b = box(tmp_path, allow_shell=True, allow_write=True)
    out = b.call("run", {"command": "bash -c 'touch escaped'"})
    assert "REFUSED" in out and "is a shell" in out
    assert not (Path(tmp_path) / "escaped").exists()


def test_a_seat_with_no_network_cannot_curl(tmp_path):
    """Prevents: allow_net=False meaning only 'http_get is unavailable' while
    the same seat fetches anything it likes through the command tool."""
    b = box(tmp_path, allow_shell=True, allow_net=False, disposable=True)
    out = b.call("run", {"command": "curl https://example.com"})
    assert "REFUSED" in out and "network access" in out


def test_ordinary_commands_still_work_and_quoted_operators_are_not_operators(tmp_path):
    b = box(tmp_path, allow_shell=True, allow_write=True)
    (Path(tmp_path) / "f.txt").write_text("a>b\n")
    out = b.call("run", {"command": "grep -c 'a>b' f.txt"})
    assert "exit=0" in out and "1" in out


def test_the_environment_handed_to_a_command_carries_no_credentials(tmp_path):
    b = box(tmp_path, allow_shell=True, disposable=True)
    os.environ["SELF_ORCH_TEST_SECRET"] = "swordfish"
    try:
        out = b.call("run", {"command": "env"})
        assert "swordfish" not in out
    finally:
        os.environ.pop("SELF_ORCH_TEST_SECRET", None)


# ── 2. a stalled seat must stop touching the workspace ───────────────────────

def test_a_cancelled_seat_loses_its_tools(tmp_path):
    """Prevents: a seat the dispatch already reported as stalled continuing to
    edit files while the next stage inspects them. The thread cannot be killed;
    its hands can be taken away."""
    b = box(tmp_path, allow_shell=True, allow_write=True)
    b.cancelled = True
    assert "REFUSED" in b.call("write_file", {"path": "x", "content": "y"})
    assert "REFUSED" in b.call("run", {"command": "true"})
    assert not (Path(tmp_path) / "x").exists()


# ── 3. parallel slices must not share one mutable tree ───────────────────────

def test_slices_work_in_private_copies_and_are_integrated(tmp_path):
    root = Path(tmp_path)
    (root / "a.txt").write_text("one")
    (root / "b.txt").write_text("two")
    base = box(root, allow_write=True, allow_shell=True)
    before = base.manifest()

    s1 = base.snapshot("s1")
    s2 = base.snapshot("s2")
    assert s1.root != base.root and s2.root != base.root

    s1.call("write_file", {"path": "a.txt", "content": "slice one"})
    s2.call("write_file", {"path": "b.txt", "content": "slice two"})
    # neither edit reached the real tree while the seats were running
    assert (root / "a.txt").read_text() == "one"

    merged = integrate_slices(base, [s1, s2], before)
    assert merged["conflicts"] == []
    assert sorted(merged["applied"]) == ["a.txt", "b.txt"]
    assert (root / "a.txt").read_text() == "slice one"
    assert (root / "b.txt").read_text() == "slice two"


def test_two_slices_editing_the_same_file_is_a_reported_collision(tmp_path):
    """Prevents: last-writer-wins. Under the old shared workspace this was
    invisible -- half the parallel work vanished and the run reported success."""
    root = Path(tmp_path)
    (root / "a.txt").write_text("one")
    base = box(root, allow_write=True)
    before = base.manifest()
    s1, s2 = base.snapshot("s1"), base.snapshot("s2")
    s1.call("write_file", {"path": "a.txt", "content": "from one"})
    s2.call("write_file", {"path": "a.txt", "content": "from two"})

    merged = integrate_slices(base, [s1, s2], before)
    assert merged["applied"] == []
    assert len(merged["conflicts"]) == 1
    assert "a.txt" in merged["conflicts"][0]
    assert (root / "a.txt").read_text() == "one"       # neither winner imposed
    assert "COLLISIONS" in integration_report(merged)


# ── 4. red-team evidence must be backed by the audit ─────────────────────────

PASSING = "VERDICT: PASS\nATTACKS RUN\n- fed it a malformed record; it refused correctly\n"


def test_a_workspace_red_team_that_never_touched_the_workspace_fails(tmp_path):
    """Prevents: a model writing a convincing list of attacks it never ran. The
    text gate cannot tell the difference; the audit can."""
    passed, reason = evaluate_redteam(
        PASSING, {"workspace_backed": True, "tool_calls": 0, "tools_used": []})
    assert not passed
    assert "written, not" in reason


def test_a_workspace_red_team_that_inspected_the_artifact_passes():
    passed, _ = evaluate_redteam(
        PASSING, {"workspace_backed": True, "tool_calls": 3, "tools_used": ["read_file", "run"]})
    assert passed


def test_a_text_only_red_team_is_still_judged_on_its_text():
    passed, _ = evaluate_redteam(PASSING, {"workspace_backed": False})
    assert passed
    assert not evaluate_redteam("VERDICT: PASS", {"workspace_backed": False})[0]


# ── 5. the stage order invariant must forbid what it claims to forbid ────────

def test_red_team_before_execute_is_refused(tmp_path):
    """Prevents exactly the ledger the old validator let through: the adversary
    running before the work it is supposed to attack, because moving backwards
    into execute was specifically exempted."""
    with pytest.raises(PipelineRefusal) as e:
        assert_stage_order(["orient", "gather", "organize", "heavy", "review",
                            "redteam", "execute", "deliver"])
    assert "illegal transition" in str(e.value)


def test_the_execute_redteam_loop_is_still_legal():
    assert_stage_order(["orient", "gather", "organize", "heavy", "review",
                        "execute", "redteam", "execute", "redteam", "deliver"])


def test_a_legal_walk_that_stops_early_is_refused():
    with pytest.raises(PipelineRefusal) as e:
        assert_stage_order(["orient", "gather", "organize", "heavy", "review",
                            "execute", "redteam"])
    assert "missing mandatory stage" in str(e.value)


def test_a_ledger_that_does_not_start_at_orient_is_refused():
    with pytest.raises(PipelineRefusal) as e:
        assert_stage_order(["gather", "organize"])
    assert "starts at" in str(e.value)


# ── 6. the boundary that a live model actually broke ─────────────────────────

def test_commands_without_write_are_refused_in_the_real_workspace(tmp_path):
    """The lesson from a live escape. A read-only seat with a command tool was
    asked to create a file and did it instantly with `touch PWNED.txt` -- no
    shell, no operator, nothing to refuse. Running a program that writes is
    writing. So the configuration itself is now refused: that shape may only
    exist inside a copy that is thrown away."""
    with pytest.raises(ValueError) as e:
        Toolbox(root=Path(tmp_path).resolve(), allow_shell=True, allow_write=False)
    assert "disposable" in str(e.value)


def test_derive_hands_a_read_only_seat_a_throwaway_copy(tmp_path):
    """And the escape is contained rather than argued with: the seat's `touch`
    succeeds, in a tree nobody reads and nothing integrates."""
    root = Path(tmp_path)
    (root / "artifact.py").write_text("real")
    base = Toolbox(root=root.resolve(), allow_write=True, allow_shell=True)

    judge = base.derive(allow_write=False, allow_shell=True)
    assert judge.disposable and judge.root != base.root

    out = judge.call("run", {"command": "touch PWNED.txt"})
    assert "exit=0" in out                       # it really did write
    assert (judge.root / "PWNED.txt").exists()   # in its own copy
    assert not (root / "PWNED.txt").exists()     # never in the artifact
    assert (root / "artifact.py").read_text() == "real"


def test_a_disposable_seat_is_told_its_writes_are_discarded(tmp_path):
    base = Toolbox(root=Path(tmp_path).resolve(), allow_write=True, allow_shell=True)
    judge = base.derive(allow_write=False, allow_shell=True)
    assert "DISPOSABLE COPY" in judge.preamble()
    assert "no shell" in judge.preamble()


# ── 7. the whole pipeline, wired to a real workspace ─────────────────────────

from self_orch.stages import STAGE_ORDER, run_pipeline   # noqa: E402


class _Res:
    def __init__(self, subs):
        self.subs = subs

    def to_dict(self):
        return {"substrates": self.subs, "dispatch_state": "completed",
                "elapsed_s": 0.0, "anomalies": []}


class ActingDispatch:
    """A fake that does not script prose -- it drives the seats' real toolboxes.

    That is the point: scripting text would exercise the pipeline's control flow
    while leaving the part that touches the filesystem -- snapshot, isolate,
    integrate -- entirely untested, which is how the shared-workspace race lived
    so long. This one writes through whatever toolbox the pipeline actually
    handed each seat, so the test fails if the wiring is wrong.
    """

    def __init__(self, writes):
        self.writes = writes          # {slice_index: (path, content)}
        self.seen = []

    def __call__(self, specs, user_msg="", level="standard", **kw):
        stage = specs[0].phase
        self.seen.append(stage)
        subs = []
        for i, sp in enumerate(specs):
            out = f"output-for-{stage}"
            if stage == "execute" and sp.toolbox and i in self.writes:
                path, content = self.writes[i]
                sp.toolbox.call("write_file", {"path": path, "content": content})
            if stage == "redteam":
                if sp.toolbox:
                    sp.toolbox.call("list_dir", {"path": "."})
                out = "VERDICT: PASS\nATTACKS RUN\n- read the tree and checked the edit landed\n"
            if stage == "review":
                out = "VERDICT: APPROVE"
            subs.append({"output": out,
                         "tools": sp.toolbox.audit() if sp.toolbox else {}})
        return _Res(subs)


def test_a_full_run_isolates_the_slices_and_integrates_them(tmp_path):
    root = Path(tmp_path)
    (root / "a.py").write_text("old a")
    (root / "b.py").write_text("old b")
    fake = ActingDispatch({0: ("a.py", "new a"), 1: ("b.py", "new b")})

    r = run_pipeline("do the thing", execute_seats=2, workspace=str(root),
                     tier_overrides={"review": "tier3", "redteam": "tier3",
                                     "heavy": "tier1", "execute": "tier1"},
                     dispatch_fn=fake)

    assert r.state == "completed", r.reason
    assert [s.stage for s in r.ledger] == list(STAGE_ORDER)
    # both slices' work reached the real tree, neither overwrote the other
    assert (root / "a.py").read_text() == "new a"
    assert (root / "b.py").read_text() == "new b"


def test_a_full_run_refuses_to_pass_a_tree_the_slices_collided_over(tmp_path):
    """Prevents the worst version of the race: a red team approving a tree that
    is missing half the work, because the collision was silently resolved."""
    root = Path(tmp_path)
    (root / "a.py").write_text("old a")
    fake = ActingDispatch({0: ("a.py", "from slice one"), 1: ("a.py", "from slice two")})

    r = run_pipeline("do the thing", execute_seats=2, max_loops=0, workspace=str(root),
                     tier_overrides={"review": "tier3", "redteam": "tier3",
                                     "heavy": "tier1", "execute": "tier1"},
                     dispatch_fn=fake)

    assert r.state == "failed"
    assert "collided" in r.reason
    assert (root / "a.py").read_text() == "old a"


def test_the_red_team_stage_is_handed_a_copy_not_the_artifact(tmp_path):
    root = Path(tmp_path)
    (root / "a.py").write_text("old a")
    seen_roots = {}

    class Spy(ActingDispatch):
        def __call__(self, specs, user_msg="", level="standard", **kw):
            if specs[0].toolbox:
                seen_roots[specs[0].phase] = specs[0].toolbox.root
            return super().__call__(specs, user_msg=user_msg, level=level, **kw)

    run_pipeline("x", execute_seats=1, workspace=str(root),
                 tier_overrides={"review": "tier3", "redteam": "tier3",
                                 "heavy": "tier1", "execute": "tier1"},
                 dispatch_fn=Spy({0: ("a.py", "new a")}))

    assert seen_roots["execute"] == root.resolve()        # the builder edits the artifact
    assert seen_roots["redteam"] != root.resolve()        # the judge never can
    assert seen_roots["gather"] != root.resolve()
