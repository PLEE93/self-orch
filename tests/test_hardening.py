"""Tests for the seven defects found in external review of this repo.

Each test names the failure it prevents. Nothing here touches the network: the
tool loop is exercised against a scripted transport, and the context-budget test
enforces the RAIL'S OWN ceiling rather than a fake one -- which is the point,
since the previous suite injected a fake dispatch and therefore never saw the
ceiling the real pipeline dies on.
"""

import json

import pytest

from self_orch import rail, stages
from self_orch.context_budget import compose_within
from self_orch.rail import MAX_USER_MSG_CHARS, SeatSpec
from self_orch.seat_tools import Toolbox, ToolRefusal, parse_tool_args
from self_orch.stages import (
    PipelineRefusal, assert_distinct_slices, assert_stage_order,
    default_execute_slices, evaluate_redteam, evaluate_review, named_attacks,
    run_pipeline,
)


class _StubProvider:
    chat_shape = "openai"
    family = "stub"


@pytest.fixture
def stub_provider(monkeypatch):
    """Exercise the rail without credentials: these tests are about the loop."""
    monkeypatch.setattr(rail, "resolve_provider", lambda model: _StubProvider())
    monkeypatch.setattr(rail, "chat_url", lambda p: "https://stub.invalid/v1/chat/completions")
    monkeypatch.setattr(rail, "auth_headers", lambda p: {"Authorization": "Bearer stub"})
    return _StubProvider()


GOOD_RT = "VERDICT: PASS\nATTACKS RUN\n- tried empty input, handled cleanly\n- tried stale state, handled\n"


class _Res:
    def __init__(self, subs):
        self._subs = subs

    def to_dict(self):
        return {"dispatch_state": "completed", "elapsed_s": 0.0,
                "anomalies": [], "substrates": self._subs}


# ── 1. execute can actually execute ────────────────────────────────────────

def test_toolbox_refuses_paths_outside_the_workspace(tmp_path):
    """Prevents a seat reading ~/.ssh by asking for '../../.ssh/id_rsa'."""
    box = Toolbox(root=tmp_path)
    (tmp_path / "inside.txt").write_text("ok")
    assert "ok" in box.read_file("inside.txt")
    with pytest.raises(ToolRefusal):
        box.resolve("../escape.txt")
    assert "REFUSED" in box.call("read_file", {"path": "../../etc/passwd"})


def test_toolbox_refuses_symlink_escape(tmp_path):
    """Prevents the subtler escape: a symlink planted inside the workspace."""
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("secret")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "door").symlink_to(outside)
    box = Toolbox(root=ws)
    assert "REFUSED" in box.call("read_file", {"path": "door"})


def test_write_and_shell_are_off_unless_granted(tmp_path):
    """Prevents a read-only seat (gather, red team) mutating the evidence."""
    ro = Toolbox(root=tmp_path)
    assert "REFUSED" in ro.call("write_file", {"path": "x", "content": "y"})
    assert "REFUSED" in ro.call("run", {"command": "echo hi"})
    rw = Toolbox(root=tmp_path, allow_write=True, allow_shell=True)
    assert "created" in rw.call("write_file", {"path": "x.txt", "content": "y"})
    assert "hi" in rw.call("run", {"command": "echo hi"})
    assert (tmp_path / "x.txt").read_text() == "y"


def test_toolbox_records_an_audit_trail(tmp_path):
    """Prevents an unverifiable claim that a seat 'checked' something."""
    box = Toolbox(root=tmp_path, allow_write=True)
    box.call("write_file", {"path": "a.txt", "content": "hello"})
    box.call("read_file", {"path": "a.txt"})
    box.call("read_file", {"path": "nope.txt"})
    audit = box.audit()
    assert audit["tool_calls"] == 3
    assert audit["failures"] == 1
    assert set(audit["tools_used"]) == {"write_file", "read_file"}


def test_tool_results_are_clipped(tmp_path):
    """Prevents one cat of a large file blowing the next stage's budget."""
    big = "x" * 50_000
    (tmp_path / "big.txt").write_text(big)
    box = Toolbox(root=tmp_path, max_result_chars=2000)
    out = box.call("read_file", {"path": "big.txt"})
    assert len(out) <= 2000
    assert "omitted" in out


def test_malformed_tool_arguments_do_not_crash_the_seat():
    """Prevents a model emitting broken JSON arguments killing the whole run."""
    assert parse_tool_args("{not json") == {}
    assert parse_tool_args(None) == {}
    assert parse_tool_args('{"path": "a"}') == {"path": "a"}


def test_openai_tool_loop_actually_runs_the_tool(tmp_path, monkeypatch, stub_provider):
    """The headline defect: EXECUTE was told to implement and verify work while
    the rail could only exchange prose. This proves a seat now reads a real file
    through a real tool call and answers from what it found."""
    (tmp_path / "target.py").write_text("def add(a, b):\n    return a - b\n")
    turns = []

    def fake_http_json(url, headers, payload, timeout):
        turns.append(payload)
        if len(turns) == 1:
            assert payload["tools"], "tools must be offered to the model"
            return {"choices": [{"message": {
                "content": "",
                "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "read_file",
                                 "arguments": json.dumps({"path": "target.py"})},
                }],
            }}]}
        sent = json.dumps(payload["messages"])
        assert "return a - b" in sent, "the tool result must reach the model"
        return {"choices": [{"message": {"content": "FOUND: add() subtracts."}}]}

    monkeypatch.setattr(rail, "_http_json", fake_http_json)
    box = Toolbox(root=tmp_path)
    spec = SeatSpec(role="execute", model="glm-4.6", brief="fix it", toolbox=box)
    out = rail.call_seat(spec, "check target.py")
    assert out["status"] == "ok"
    assert out["output"] == "FOUND: add() subtracts."
    assert out["tools"]["tool_calls"] == 1
    assert out["tools"]["tools_used"] == ["read_file"]


def test_tool_loop_is_bounded(tmp_path, monkeypatch, stub_provider):
    """Prevents a model that loops on tool calls forever burning the budget."""
    calls = {"n": 0}

    def always_tool(url, headers, payload, timeout):
        calls["n"] += 1
        if "tools" not in payload:          # final, tool-free request
            return {"choices": [{"message": {"content": "forced answer"}}]}
        return {"choices": [{"message": {"content": "", "tool_calls": [{
            "id": "c", "type": "function",
            "function": {"name": "list_dir", "arguments": "{}"}}]}}]}

    monkeypatch.setattr(rail, "_http_json", always_tool)
    spec = SeatSpec(role="execute", model="glm-4.6", brief="b",
                    toolbox=Toolbox(root=tmp_path))
    out = rail.call_seat(spec, "go")
    assert out["output"] == "forced answer"
    assert calls["n"] == rail.MAX_TOOL_ITERS + 1


def test_anthropic_tool_loop_speaks_the_other_shape(tmp_path, monkeypatch, stub_provider):
    """Prevents tools working on one provider family and silently not the other."""
    (tmp_path / "f.txt").write_text("content-here")
    seen = []

    def fake(url, headers, payload, timeout):
        seen.append(payload)
        if len(seen) == 1:
            assert "system" in payload and payload["tools"]
            return {"content": [{"type": "tool_use", "id": "t1",
                                 "name": "read_file", "input": {"path": "f.txt"}}]}
        assert "content-here" in json.dumps(payload["messages"])
        return {"content": [{"type": "text", "text": "read it"}]}

    monkeypatch.setattr(rail, "_http_json", fake)
    stub_provider.chat_shape = "anthropic"
    monkeypatch.setattr(rail, "resolve_provider", lambda model: stub_provider)
    spec = SeatSpec(role="redteam", model="claude-sonnet-4-5", brief="b",
                    toolbox=Toolbox(root=tmp_path))
    out = rail.call_seat(spec, "go")
    assert out["output"] == "read it"
    assert out["tools"]["tool_calls"] == 1


def test_pipeline_gives_acting_stages_tools_and_reasoning_stages_none(tmp_path):
    """Prevents paying reasoning-tier prices for tool plumbing, and prevents the
    red team being able to edit the artifact it is judging."""
    seen = {}

    def fake_dispatch(specs, user_msg="", level="standard", **kw):
        seen[specs[0].phase] = specs[0].toolbox
        stage = specs[0].phase
        text = GOOD_RT if stage == "redteam" else ("VERDICT: APPROVE" if stage == "review" else "x")
        return _Res([{"output": text} for _ in specs])

    run_pipeline("task", workspace=str(tmp_path), dispatch_fn=fake_dispatch)
    assert seen["orient"] is None and seen["heavy"] is None
    assert seen["execute"] is not None and seen["execute"].allow_write
    assert seen["redteam"] is not None and not seen["redteam"].allow_write
    assert seen["redteam"].allow_shell, "the red team must be able to run checks"
    assert seen["gather"] is not None and not seen["gather"].allow_write


# ── 2. the context ceiling ─────────────────────────────────────────────────

def test_compose_never_exceeds_the_limit():
    """Prevents the pipeline dying mid-run on the rail's input ceiling after
    every earlier stage has already been paid for."""
    sections = [("original request", "r" * 500, 5)] + [
        (f"finding {i}", "f" * 40_000, 1) for i in range(4)
    ]
    out = compose_within(sections, MAX_USER_MSG_CHARS)
    assert len(out) <= MAX_USER_MSG_CHARS
    for i in range(4):
        assert f"FINDING {i}" in out, "no section may be silently dropped"
    assert "dropped to fit the context budget" in out


def test_priority_protects_the_users_own_words():
    """Prevents the original request being squeezed out by derived text."""
    out = compose_within(
        [("original request", "R" * 4000, 9), ("notes", "N" * 60_000, 1)], 8000)
    assert out.count("R") > 3000


def test_pipeline_context_stays_inside_the_real_rail_ceiling():
    """The integration flaw the old suite could not see: its fake dispatch
    bypassed the real input validation, so oversized composed context passed
    the tests and failed in production. This fake enforces the real ceiling."""
    huge = "h" * 30_000

    def strict_dispatch(specs, user_msg="", level="standard", **kw):
        assert len(user_msg) <= MAX_USER_MSG_CHARS, (
            f"stage {specs[0].phase} was handed {len(user_msg)} chars, over the "
            f"rail's {MAX_USER_MSG_CHARS} ceiling")
        stage = specs[0].phase
        if stage == "review":
            return _Res([{"output": "VERDICT: APPROVE\n" + huge}])
        if stage == "redteam":
            return _Res([{"output": GOOD_RT + huge}])
        return _Res([{"output": huge} for _ in specs])

    res = run_pipeline(huge, dispatch_fn=strict_dispatch)
    assert res.state == "completed"


# ── 3. review fails closed ─────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "", "looks good to me", "VERDICT: FAIL", "VERDICT: REJECT",
    "VERDICT: maybe-ish", "the plan is fine, ship it",
])
def test_review_fails_closed_on_anything_but_approval(text):
    """Prevents the old hole: only the exact word REJECT stopped the run, so an
    unreadable, truncated or failing review still authorised execution."""
    ok, _ = evaluate_review(text)
    assert ok is False


@pytest.mark.parametrize("text", [
    "VERDICT: APPROVE", "verdict: approve", "VERDICT: APPROVE-WITH-CHANGES",
])
def test_review_approves_when_it_actually_approves(text):
    assert evaluate_review(text)[0] is True


def test_pipeline_stops_when_review_is_unreadable():
    calls = []

    def fake(specs, user_msg="", level="standard", **kw):
        calls.append(specs[0].phase)
        return _Res([{"output": "no verdict here"} for _ in specs])

    res = run_pipeline("task", dispatch_fn=fake)
    assert res.state == "failed"
    assert "execute" not in calls, "nothing may be executed on an unread review"


# ── 4. named attacks, not a heading ────────────────────────────────────────

@pytest.mark.parametrize("body", [
    "VERDICT: PASS\nATTACKS RUN\nnone\n",
    "VERDICT: PASS\nATTACKS RUN\n- none\n",
    "VERDICT: PASS\nATTACKS RUN\nN/A\n",
    "VERDICT: PASS\nATTACKS RUN\n\nVERDICT: PASS\n",
])
def test_pass_with_an_empty_attacks_block_is_discarded(body):
    """Prevents the exact string that defeated the old regex: the heading was
    checked, the content under it never was."""
    ok, why = evaluate_redteam(body)
    assert ok is False
    assert "empty seat" in why


def test_real_attacks_still_pass():
    ok, why = evaluate_redteam(GOOD_RT)
    assert ok is True
    assert "2 named attack" in why


def test_named_attacks_stops_at_the_next_heading():
    body = ("VERDICT: PASS\nATTACKS RUN\n- fed it a malformed config, it refused cleanly\n"
            "RESIDUAL RISK\n- unknown behaviour under concurrency\n")
    assert named_attacks(body) == ["fed it a malformed config, it refused cleanly"]


# ── 5. stage presence, not just order ──────────────────────────────────────

def test_a_skipped_stage_is_refused():
    """Prevents the advertised contract being false: the old check only looked
    for backward transitions, so dropping gather and organize passed."""
    with pytest.raises(PipelineRefusal) as e:
        assert_stage_order(["orient", "heavy", "review", "execute", "redteam", "deliver"])
    assert "missing mandatory stage" in str(e.value)


def test_a_full_ledger_with_loops_is_accepted():
    assert_stage_order(["orient", "gather", "organize", "heavy", "review",
                        "execute", "redteam", "execute", "redteam", "deliver"]) is None


# ── 6. real decomposition, not an ensemble ─────────────────────────────────

def test_default_execute_slices_are_distinct():
    """Prevents two seats being handed the same job and called parallelism."""
    for n in (2, 3, 4, 6):
        s = default_execute_slices(n)
        assert len(s) == n and len(set(s)) == n


def test_identical_slices_are_refused():
    with pytest.raises(PipelineRefusal):
        assert_distinct_slices(["same", "same"], 2)
    with pytest.raises(PipelineRefusal):
        assert_distinct_slices([], 2)
    assert_distinct_slices(["a", "b"], 2)


def test_pipeline_gives_each_execution_seat_a_different_brief():
    briefs = {}

    def fake(specs, user_msg="", level="standard", **kw):
        if specs[0].phase == "execute":
            briefs["all"] = [s.brief for s in specs]
        stage = specs[0].phase
        text = GOOD_RT if stage == "redteam" else ("VERDICT: APPROVE" if stage == "review" else "x")
        return _Res([{"output": text} for _ in specs])

    run_pipeline("task", execute_seats=3, dispatch_fn=fake)
    assert len(set(briefs["all"])) == 3


# ── 7. nothing waits forever ───────────────────────────────────────────────

def test_timeouts_are_real_and_overridable(monkeypatch):
    """Prevents one stalled provider hanging the whole parliament, which is what
    timeout=None plus 'wait for every future' produced."""
    monkeypatch.delenv("SELF_ORCH_SEAT_TIMEOUT_S", raising=False)
    assert rail.seat_timeout() == rail.DEFAULT_SEAT_TIMEOUT_S
    monkeypatch.setenv("SELF_ORCH_SEAT_TIMEOUT_S", "5")
    assert rail.seat_timeout() == 5
    monkeypatch.setenv("SELF_ORCH_SEAT_TIMEOUT_S", "0")
    assert rail.seat_timeout() is None, "0 must restore unbounded waiting on purpose"
    monkeypatch.setenv("SELF_ORCH_SEAT_TIMEOUT_S", "nonsense")
    assert rail.seat_timeout() == rail.DEFAULT_SEAT_TIMEOUT_S


def test_seat_timeout_reaches_the_socket(monkeypatch, stub_provider):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"hi"}}]}'

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setenv("SELF_ORCH_SEAT_TIMEOUT_S", "7")
    monkeypatch.setattr(rail.urllib.request, "urlopen", fake_urlopen)
    spec = SeatSpec(role="worker", model="glm-4.6", brief="b")
    rail.call_seat(spec, "go", stream=False)
    assert seen["timeout"] == 7


def test_a_stalled_seat_is_reported_not_waited_on(monkeypatch):
    """A seat that never returns must degrade the round, not freeze it."""
    import threading
    release = threading.Event()

    def slow(spec, user_msg, history=None, on_token=None, stream=True):
        if spec.role == "slow":
            release.wait(30)
        return {"role": spec.role, "model": spec.model, "phase": spec.phase,
                "status": "ok", "output": "done", "elapsed_s": 0.0, "error": None}

    monkeypatch.setattr(rail, "call_seat", slow)
    monkeypatch.setenv("SELF_ORCH_DISPATCH_TIMEOUT_S", "1")
    specs = [SeatSpec(role="fast", model="glm-4.6", brief="b"),
             SeatSpec(role="slow", model="glm-4.6", brief="b")]
    try:
        res = rail.dispatch(specs, user_msg="go").to_dict()
    finally:
        release.set()
    assert res["dispatch_state"] == "partial"
    by_role = {s["role"]: s for s in res["substrates"]}
    assert by_role["fast"]["status"] == "ok"
    assert by_role["slow"]["status"] == "error"
    assert "deadline" in by_role["slow"]["error"]
    assert any("stalled seat" in a for a in res["anomalies"])


def test_a_missing_workspace_fails_at_construction(tmp_path):
    """Prevents every tool call in a run being refused for what looks like a
    model mistake, when the real cause is a workspace path that does not exist."""
    with pytest.raises(ValueError):
        Toolbox.from_dict({"workspace": str(tmp_path / "nope")})
    assert Toolbox.from_dict(None) is None
    assert Toolbox.from_dict({"workspace": str(tmp_path)}).root == tmp_path.resolve()
