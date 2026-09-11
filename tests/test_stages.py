"""Tests for the enforced seven-stage pipeline.

No network: dispatch is injected. Each test names the failure it prevents.
"""

import pytest

from self_orch import stages
from self_orch.stages import (
    STAGE_ORDER, PipelineRefusal, assert_stage_order, evaluate_redteam,
    parse_verdict, run_pipeline,
)

GOOD_RT = "VERDICT: PASS\nATTACKS RUN\n- tried empty input, handled\n- tried stale state, handled\n"
BAD_RT = "VERDICT: FAIL\nATTACKS RUN\n- broke it with empty input\nWHAT WOULD HAVE TO CHANGE\n- guard the empty case\n"


class FakeDispatch:
    """Records every stage dispatch and returns scripted text per stage."""

    def __init__(self, script=None):
        self.calls = []
        self.script = script or {}

    def __call__(self, specs, user_msg="", level="standard", **kw):
        stage = specs[0].phase
        self.calls.append({
            "stage": stage, "seats": len(specs), "model": specs[0].model,
            "context": user_msg, "brief": specs[0].brief,
        })
        val = self.script.get(stage, "output-for-" + stage)
        text = val.pop(0) if isinstance(val, list) else val
        return _Res([{"output": text} for _ in specs])

    @property
    def order(self):
        return [c["stage"] for c in self.calls]

    def context_for(self, stage):
        for c in self.calls:
            if c["stage"] == stage:
                return c["context"]
        return ""


class _Res:
    def __init__(self, subs):
        self._subs = subs

    def to_dict(self):
        return {"dispatch_state": "completed", "elapsed_s": 0.0,
                "anomalies": [], "substrates": self._subs}


# ── verdict parsing / the gate ───────────────────────────────────────────────

def test_missing_verdict_fails_closed():
    """Prevents the worst possible gate bug: a red team whose output cannot be
    read being counted as approval."""
    ok, why = evaluate_redteam("I looked at it and it seems fine honestly")
    assert ok is False
    assert "no parseable" in why.lower()


def test_pass_without_named_attacks_is_discarded():
    """Prevents the empty seat: agreement with no falsification attempted. The
    brief promises such a verdict is discarded; this is where that is kept."""
    ok, why = evaluate_redteam("VERDICT: PASS\nLooks good to me.")
    assert ok is False
    assert "empty seat" in why.lower()


def test_real_pass_is_accepted():
    ok, why = evaluate_redteam(GOOD_RT)
    assert ok is True and "PASS" in why


def test_fail_verdict_is_a_fail():
    ok, _ = evaluate_redteam(BAD_RT)
    assert ok is False


@pytest.mark.parametrize("text,expected", [
    ("VERDICT: PASS", "PASS"),
    ("verdict:   fail  ", "FAIL"),
    ("VERDICT: APPROVE-WITH-CHANGES", "APPROVE-WITH-CHANGES"),
    ("no verdict here", ""),
])
def test_parse_verdict(text, expected):
    assert parse_verdict(text) == expected


# ── order enforcement ────────────────────────────────────────────────────────

def test_happy_path_runs_every_stage_in_order(monkeypatch):
    """Prevents the stage names being decoration: the ledger must show all eight
    stages actually ran, in the declared sequence."""
    monkeypatch.setattr(stages, "resolve_tier", lambda t: {
        "tier1": "glm-5.3", "tier2": "claude-sonnet-4-5",
        "tier3": "gpt-5.1", "tier4": "claude-opus-4-5"}[t])
    fake = FakeDispatch({"redteam": GOOD_RT, "review": "VERDICT: APPROVE\nATTACKS RUN\n- none held"})
    r = run_pipeline("do the thing", dispatch_fn=fake)
    assert r.state == "completed", r.reason
    assert fake.order == list(STAGE_ORDER)
    assert [s.stage for s in r.ledger] == list(STAGE_ORDER)
    assert r.answer == "output-for-deliver"


def test_assert_stage_order_rejects_a_reordering():
    """Prevents a caller assembling a ledger that skipped the adversarial stages
    and still calling the run a pipeline run."""
    with pytest.raises(PipelineRefusal):
        assert_stage_order(["orient", "heavy", "organize"])


def test_execute_and_redteam_may_repeat():
    assert_stage_order(["orient", "gather", "organize", "heavy", "review",
                        "execute", "redteam", "execute", "redteam", "deliver"])


# ── the loop ─────────────────────────────────────────────────────────────────

def _tiers(monkeypatch):
    monkeypatch.setattr(stages, "resolve_tier", lambda t: {
        "tier1": "glm-5.3", "tier2": "claude-sonnet-4-5",
        "tier3": "gpt-5.1", "tier4": "claude-opus-4-5"}[t])


def test_redteam_fail_loops_back_to_execute(monkeypatch):
    """Prevents the 'submit or loop depending on the test' claim being a lie:
    a FAIL must actually send the work back through execute."""
    _tiers(monkeypatch)
    fake = FakeDispatch({"redteam": [BAD_RT, GOOD_RT],
                         "review": "VERDICT: APPROVE\nATTACKS RUN\n- none"})
    r = run_pipeline("x", dispatch_fn=fake)
    assert r.state == "completed"
    assert r.loops == 1
    assert fake.order.count("execute") == 2
    assert fake.order.count("redteam") == 2


def test_second_execute_carries_the_required_changes(monkeypatch):
    """Prevents a loop that re-runs blind: the retry must receive what the red
    team said had to change, or it will reproduce the same defect."""
    _tiers(monkeypatch)
    fake = FakeDispatch({"redteam": [BAD_RT, GOOD_RT],
                         "review": "VERDICT: APPROVE\nATTACKS RUN\n- none"})
    run_pipeline("x", dispatch_fn=fake)
    second = [c for c in fake.calls if c["stage"] == "execute"][1]["context"]
    assert "guard the empty case" in second


def test_loop_budget_is_enforced(monkeypatch):
    """Prevents an unbounded retry loop burning a user's tokens forever."""
    _tiers(monkeypatch)
    fake = FakeDispatch({"redteam": BAD_RT,
                         "review": "VERDICT: APPROVE\nATTACKS RUN\n- none"})
    r = run_pipeline("x", max_loops=2, dispatch_fn=fake)
    assert r.state == "failed"
    assert fake.order.count("execute") == 3      # initial + 2 retries
    assert "deliver" not in fake.order           # never delivered on a FAIL
    assert "did not pass" in r.reason


def test_unreadable_redteam_never_delivers(monkeypatch):
    """Prevents fail-open: an unparseable gate result must not reach delivery."""
    _tiers(monkeypatch)
    fake = FakeDispatch({"redteam": "it all looks fine",
                         "review": "VERDICT: APPROVE\nATTACKS RUN\n- none"})
    r = run_pipeline("x", max_loops=0, dispatch_fn=fake)
    assert r.state == "failed"
    assert "deliver" not in fake.order


def test_review_reject_stops_before_any_execution(monkeypatch):
    """Prevents paying for execution on a plan the reviewer already rejected."""
    _tiers(monkeypatch)
    fake = FakeDispatch({"review": "VERDICT: REJECT\nATTACKS RUN\n- fatal gap"})
    r = run_pipeline("x", dispatch_fn=fake)
    assert r.state == "failed"
    assert "execute" not in fake.order
    assert "REJECT" in r.reason


# ── independence enforcement ─────────────────────────────────────────────────

def test_reviewer_sharing_a_family_with_the_planner_is_refused(monkeypatch):
    """Prevents the headline claim collapsing: a check run by the same model
    family that produced the work is not an independent check."""
    monkeypatch.setattr(stages, "resolve_tier", lambda t: {
        "tier1": "glm-5.3", "tier2": "claude-sonnet-4-5",
        "tier3": "claude-opus-4-5",      # same family as the planner
        "tier4": "claude-opus-4-5"}[t])
    fake = FakeDispatch()
    with pytest.raises(PipelineRefusal) as e:
        run_pipeline("x", dispatch_fn=fake)
    assert "independent check" in str(e.value)


def test_refusal_spends_nothing(monkeypatch):
    """Prevents discovering the independence problem only AFTER paying for the
    most expensive stage in the run."""
    monkeypatch.setattr(stages, "resolve_tier", lambda t: {
        "tier1": "glm-5.3", "tier2": "claude-sonnet-4-5",
        "tier3": "claude-opus-4-5", "tier4": "claude-opus-4-5"}[t])
    fake = FakeDispatch()
    with pytest.raises(PipelineRefusal):
        run_pipeline("x", dispatch_fn=fake)
    assert fake.calls == []


def test_redteam_sharing_a_family_with_execution_is_refused(monkeypatch):
    _tiers2 = {"tier1": "glm-5.3", "tier2": "claude-sonnet-4-5",
               "tier3": "glm-5.3", "tier4": "claude-opus-4-5"}
    monkeypatch.setattr(stages, "resolve_tier", lambda t: _tiers2[t])
    with pytest.raises(PipelineRefusal) as e:
        run_pipeline("x", dispatch_fn=FakeDispatch())
    assert "red team" in str(e.value)


# ── context chaining ─────────────────────────────────────────────────────────

def test_reviewer_receives_the_brief_not_only_the_plan(monkeypatch):
    """Prevents the reviewer attacking something internally consistent: without
    the original brief, every defect that lived in the brief is unreachable."""
    _tiers(monkeypatch)
    fake = FakeDispatch({"redteam": GOOD_RT,
                         "review": "VERDICT: APPROVE\nATTACKS RUN\n- none"})
    run_pipeline("the original ask", dispatch_fn=fake)
    ctx = fake.context_for("review")
    assert "output-for-orient" in ctx, "reviewer must get the working brief"
    assert "output-for-heavy" in ctx, "reviewer must get the plan"
    assert "the original ask" in ctx, "reviewer must get the user's own words"


def test_redteam_receives_the_test_criteria(monkeypatch):
    """Prevents the red team grading against whatever got built instead of the
    criteria written before work started."""
    _tiers(monkeypatch)
    fake = FakeDispatch({"redteam": GOOD_RT,
                         "review": "VERDICT: APPROVE\nATTACKS RUN\n- none"})
    run_pipeline("x", dispatch_fn=fake)
    ctx = fake.context_for("redteam")
    assert "output-for-orient" in ctx
    assert "output-for-execute" in ctx


def test_gather_and_execute_run_multiple_seats(monkeypatch):
    _tiers(monkeypatch)
    fake = FakeDispatch({"redteam": GOOD_RT,
                         "review": "VERDICT: APPROVE\nATTACKS RUN\n- none"})
    run_pipeline("x", gather_seats=3, execute_seats=2, dispatch_fn=fake)
    assert [c for c in fake.calls if c["stage"] == "gather"][0]["seats"] == 3
    assert [c for c in fake.calls if c["stage"] == "execute"][0]["seats"] == 2


def test_each_stage_gets_its_own_brief(monkeypatch):
    """Prevents a stage silently running with the wrong instructions."""
    _tiers(monkeypatch)
    fake = FakeDispatch({"redteam": GOOD_RT,
                         "review": "VERDICT: APPROVE\nATTACKS RUN\n- none"})
    run_pipeline("x", dispatch_fn=fake)
    seen = {c["stage"]: c["brief"] for c in fake.calls}
    assert "ORIENTATION stage" in seen["orient"]
    assert "CONTEXT GATHERING" in seen["gather"]
    assert "CONTEXT ORGANIZATION" in seen["organize"]
    assert "HEAVY stage" in seen["heavy"]
    assert "PRE-EXECUTION REVIEW" in seen["review"]
    assert "EXECUTION stage" in seen["execute"]
    assert "RED TEAM stage" in seen["redteam"]
    assert "DELIVERY stage" in seen["deliver"]
