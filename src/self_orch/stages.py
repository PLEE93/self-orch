"""The seven-stage pipeline, enforced.

    orient -> gather -> organize -> heavy -> review -> execute -> redteam
           -> deliver, or loop back to execute

The rail on its own is a same-turn parliament: it runs N seats at once and
returns. That is the right primitive, and it is NOT a process. This module is
the process, and everything in it exists so the stage names mean something a
user can rely on rather than describing what a governor is merely encouraged to
do.

Four things are enforced in code here, not suggested in a prompt:

1. ORDER. Stages run in the declared sequence. The ledger records what actually
   ran, and assert_stage_order() refuses a ledger that skipped or reordered.
2. FAMILY SPLIT. The reviewer must not share a model family with the planner it
   reviews, and the red team must not share one with the seats that built the
   work. Otherwise the check is a model grading its own family's instincts.
3. A REAL VERDICT. The red team's output is parsed. No parseable verdict is a
   FAIL, never a pass -- this gate fails closed.
4. NO EMPTY SEATS. A red team that returns PASS without naming the attacks it
   ran is discarded and treated as FAIL. Agreement is not verification.

The loop is bounded: on FAIL the pipeline returns to execute carrying the red
team's required changes, up to max_loops times, then stops and says so.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .context_budget import compose_within
from .providers import model_family
from .rail import MAX_USER_MSG_CHARS, SeatSpec, dispatch
from .seat_tools import Toolbox, integrate_slices, integration_report
from .stage_prompts import STAGE_BRIEFS
from .tiers import resolve_tier

STAGE_ORDER = (
    "orient", "gather", "organize", "heavy",
    "review", "execute", "redteam", "deliver",
)

# Which difficulty tier each stage runs on by default. Every one is overridable;
# the tier itself is already an indirection to whatever model the user owns.
#   orient/organize/deliver -> real understanding, not the cheapest seat
#   gather/execute          -> mass parallel work
#   review/redteam          -> adversarial, deliberately a different family
#   heavy                   -> the single most expensive step in the run
STAGE_TIERS = {
    "orient": "tier2",
    "gather": "tier1",
    "organize": "tier2",
    "heavy": "tier4",
    "review": "tier3",
    "execute": "tier1",
    "redteam": "tier3",
    "deliver": "tier2",
}

# Which stages get real tools when a workspace is configured, and how much
# power each one gets. The shape is deliberate: builders may write, the red team
# may look and run checks but must not touch the evidence it is judging, and the
# reasoning stages stay text-only so their cost goes into thinking.
#   (allow_write, allow_shell)
STAGE_TOOL_POWERS = {
    "gather":  (False, True),
    "execute": (True, True),
    "redteam": (False, True),
}

# Two execution seats with the same brief and the same model are an ensemble,
# not a decomposition -- they do the same job twice and the pipeline pretends it
# parallelised. When the caller supplies no slices these do, and they are
# distinct by construction.
DEFAULT_EXECUTE_SLICES = (
    "the primary change itself: the smallest edit that makes the intended behaviour real",
    "the seams: every caller, callee, interface and config the primary change touches, "
    "including the failure paths",
    "the checks: the tests or verification steps that would fail if the primary change "
    "were wrong, run them and report real output",
    "the edges: empty, missing, malformed, concurrent, and rollback behaviour",
)

PARALLEL_STAGES = frozenset({"gather", "execute"})
# Every stage is mandatory. A run that reaches deliver without gathering or
# organizing is not a cheaper run of this pipeline, it is a different one.
MANDATORY_STAGES = frozenset(STAGE_ORDER)
ADVERSARIAL_STAGES = frozenset({"review", "redteam"})


class PipelineRefusal(RuntimeError):
    """Raised when the pipeline cannot run honestly -- it refuses rather than
    running a shape that would report a meaningless result."""


@dataclass
class StageRun:
    stage: str
    tier: str
    model: str
    seats: int
    loop: int = 0
    elapsed_s: float = 0.0
    status: str = "ok"
    verdict: str = ""
    outputs: list[str] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    tool_calls: int = 0
    tools_used: list[str] = field(default_factory=list)
    # True when this stage's seats actually held a workspace. The red-team gate
    # reads it to decide whether 'you named attacks' is enough or whether the
    # audit must show the attacks were run.
    tools_backed: bool = False

    @property
    def text(self) -> str:
        return "\n\n".join(o for o in self.outputs if o)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage, "tier": self.tier, "model": self.model,
            "seats": self.seats, "loop": self.loop,
            "elapsed_s": round(self.elapsed_s, 3), "status": self.status,
            "verdict": self.verdict, "anomalies": self.anomalies,
            "tool_calls": self.tool_calls, "tools_used": self.tools_used,
            "tools_backed": self.tools_backed,
            "output_chars": len(self.text),
        }


@dataclass
class PipelineResult:
    state: str                      # completed | failed | refused
    reason: str = ""
    loops: int = 0
    elapsed_s: float = 0.0
    ledger: list[StageRun] = field(default_factory=list)
    answer: str = ""

    def stage(self, name: str) -> StageRun | None:
        for run in reversed(self.ledger):
            if run.stage == name:
                return run
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_type": "pipeline_result",
            "schema": "self_orch.pipeline.v1",
            "state": self.state,
            "ok": self.state == "completed",
            "reason": self.reason,
            "loops": self.loops,
            "elapsed_s": round(self.elapsed_s, 3),
            "stages_run": [r.stage for r in self.ledger],
            "tool_calls": sum(r.tool_calls for r in self.ledger),
            "ledger": [r.to_dict() for r in self.ledger],
            "answer": self.answer,
        }


# ── verdict parsing ──────────────────────────────────────────────────────────

# Case-insensitive on purpose: a model that writes "verdict: fail" in lower case
# must not read as "no verdict", which would fail closed into a pointless retry
# loop and bill the user for it. Caught by test_parse_verdict.
_VERDICT_RE = re.compile(r"^\s*VERDICT\s*:\s*([A-Za-z-]+)", re.MULTILINE | re.IGNORECASE)
_ATTACK_RE = re.compile(r"ATTACKS\s+RUN", re.IGNORECASE)
# A heading is not evidence. "ATTACKS RUN\nnone" matched the old check and
# passed, which made the no-empty-seats promise unenforced in exactly the case
# it existed for. These are the words that mean "I did not attack anything".
_NO_ATTACK_WORDS = frozenset({
    "none", "n/a", "na", "nil", "nothing", "no attacks", "not applicable",
    "-", "--", "(none)", "[none]", "none.", "n/a.",
})


def parse_verdict(text: str) -> str:
    """Return the declared verdict in upper case, or '' when none is declared.

    An absent verdict is NOT a pass. Callers must treat '' as failure -- see
    evaluate_redteam below, which is the only place that decision is made.
    """
    m = _VERDICT_RE.search(text or "")
    return m.group(1).strip().upper() if m else ""


def named_attacks(text: str) -> list[str]:
    """The attacks the red team says it actually ran.

    Reads the lines under the ATTACKS RUN heading, stops at the next heading,
    and drops bullet punctuation and the words that mean "I ran none". What is
    left is the evidence; an empty list means the seat claimed a verdict it did
    not earn.
    """
    body = text or ""
    m = _ATTACK_RE.search(body)
    if not m:
        return []
    attacks: list[str] = []
    for raw in body[m.end():].splitlines():
        line = raw.strip().lstrip("-*•–—").strip()
        if not line:
            continue
        # A following ALL-CAPS heading (VERDICT:, FINDINGS, RESIDUAL RISK) ends
        # the list -- otherwise the rest of the report reads as attacks.
        if re.match(r"^[A-Z][A-Z \t/&-]{3,}:?\s*$", line) or re.match(r"^VERDICT\s*:", line, re.I):
            break
        if line.lower().rstrip(".:") in _NO_ATTACK_WORDS:
            continue
        if len(line) < 8:          # "ok", "1.", "yes" are not attacks
            continue
        attacks.append(line)
    return attacks


def evaluate_redteam(text: str, audit: dict[str, Any] | None = None) -> tuple[bool, str]:
    """(passed, reason). The red-team gate, failing closed on every ambiguity.

    Five ways to not pass, and only one way to pass:
      - no parseable verdict            -> FAIL (a gate that cannot read its own
                                           result must never report success)
      - verdict FAIL                    -> FAIL (the ordinary case)
      - PASS with no ATTACKS RUN block  -> FAIL (an empty seat)
      - PASS with the block present but
        nothing named under it          -> FAIL (the same empty seat, dressed)
      - PASS naming attacks, from a seat
        that had tools and never used
        them                            -> FAIL (the attacks were written, not run)

    That last one is the difference between reading a claim and checking it. When
    the red team holds a workspace it can open the artifact and execute checks, so
    a prose list of attacks is verifiable against what it actually did. A verdict
    whose named attacks left no trace in the tool audit is a story about testing.
    """
    body = text or ""
    verdict = parse_verdict(body)
    if not verdict:
        return False, "red team returned no parseable VERDICT line; failing closed"
    if verdict != "PASS":
        return False, f"red team verdict {verdict}"
    if not _ATTACK_RE.search(body):
        return False, "red team returned PASS without naming any attacks (empty seat, discarded)"
    attacks = named_attacks(body)
    if not attacks:
        return False, ("red team returned PASS with an ATTACKS RUN heading but nothing "
                       "named under it (empty seat, discarded)")
    if audit and audit.get("workspace_backed"):
        used = set(audit.get("tools_used") or [])
        inspected = used & {"run", "read_file", "search_files", "list_dir"}
        if int(audit.get("tool_calls") or 0) < 1 or not inspected:
            return False, (
                f"red team returned PASS naming {len(attacks)} attack(s) but made no tool "
                "call against the workspace it was given -- the attacks were written, not "
                "run. Discarded."
            )
    return True, f"red team PASS with {len(attacks)} named attack(s)"


# The verdicts a pre-execution reviewer may use to let the work proceed. Anything
# else -- REJECT, FAIL, a malformed line, or no line at all -- stops the run.
REVIEW_PASS_VERDICTS = frozenset({
    "APPROVE", "APPROVED", "APPROVE-WITH-CHANGES", "PASS", "ACCEPT",
})


def evaluate_review(text: str) -> tuple[bool, str]:
    """(passed, reason) for the pre-execution review, failing closed.

    The old gate only stopped on the exact word REJECT, so an UNDECLARED
    verdict, a malformed line, a truncated answer or a plain FAIL all sailed
    into execution -- the gate read as protection while approving anything that
    was not one specific string. A review that cannot be read is not an
    approval.
    """
    verdict = parse_verdict(text or "")
    if not verdict:
        return False, ("pre-execution review returned no parseable VERDICT line; failing "
                       "closed rather than executing an unreviewed plan")
    if verdict not in REVIEW_PASS_VERDICTS:
        return False, f"pre-execution review verdict {verdict}"
    return True, f"pre-execution review {verdict}"


# ── enforcement ──────────────────────────────────────────────────────────────

def assert_family_split(adversary_model: str, subject_model: str, what: str) -> None:
    """Refuse an adversarial stage that shares a model family with its subject."""
    a, b = model_family(adversary_model), model_family(subject_model)
    if a == b:
        raise PipelineRefusal(
            f"{what}: adversary model {adversary_model!r} and subject model "
            f"{subject_model!r} are both family {a!r}. An independent check cannot "
            f"be run by the same family that produced the work. Point the "
            f"adversarial tier at a different provider family and re-run."
        )


# The pipeline's legal transitions, written out. The previous validator compared
# stage indices and exempted execute/redteam from moving backwards, which let a
# ledger like orient..review, redteam, execute, deliver pass -- red team running
# before the thing it is supposed to attack. An order invariant that accepts the
# one ordering it exists to forbid is worse than none, because it is quoted as
# proof. A transition table cannot drift from the order it describes.
STAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    "orient":   frozenset({"gather"}),
    "gather":   frozenset({"organize"}),
    "organize": frozenset({"heavy"}),
    "heavy":    frozenset({"review"}),
    "review":   frozenset({"execute"}),
    "execute":  frozenset({"redteam"}),
    "redteam":  frozenset({"execute", "deliver"}),
    "deliver":  frozenset(),
}


def assert_stage_order(stages_run: list[str]) -> None:
    """Refuse a ledger that is not a walk through STAGE_TRANSITIONS.

    Three ways to fail: an unknown stage, a start that is not orient, or a step
    the table does not allow. Mandatory presence is checked too, because a walk
    that stops early is legal as a walk and still is not a run of this pipeline.
    """
    seen = list(stages_run)
    if not seen:
        raise PipelineRefusal("empty ledger: no stage ran")
    for name in seen:
        if name not in STAGE_ORDER:
            raise PipelineRefusal(f"unknown stage {name!r} in ledger")
    if seen[0] != "orient":
        raise PipelineRefusal(
            f"ledger starts at {seen[0]!r}; every run starts at orient. Ledger: {seen}"
        )
    for a, b in zip(seen, seen[1:]):
        if b not in STAGE_TRANSITIONS[a]:
            raise PipelineRefusal(
                f"illegal transition {a!r} -> {b!r}; the only steps allowed after {a!r} are "
                f"{sorted(STAGE_TRANSITIONS[a]) or 'none (it is terminal)'}. Ledger: {seen}"
            )
    missing = [name for name in STAGE_ORDER if name in MANDATORY_STAGES and name not in seen]
    if missing:
        raise PipelineRefusal(
            f"ledger is missing mandatory stage(s) {missing}; ordering alone is not the "
            f"contract -- a run that skipped them is not a run of this pipeline. "
            f"Ledger: {seen}"
        )


def default_execute_slices(seats: int) -> list[str]:
    """Distinct default slices for `seats` execution seats."""
    out = list(DEFAULT_EXECUTE_SLICES[:max(0, seats)])
    while len(out) < seats:
        out.append(f"remaining work, part {len(out) + 1}: whatever the slices above do not "
                   f"cover; state explicitly what you took and what you left")
    return out


def assert_distinct_slices(slices: list[str], seats: int) -> None:
    """Refuse parallel execution seats that were never given different jobs."""
    if seats <= 1:
        return
    real = [s.strip() for s in (slices or []) if s and s.strip()]
    if len(set(real)) < seats:
        raise PipelineRefusal(
            f"{seats} execution seats were given {len(set(real))} distinct slice(s). "
            f"Seats with the same brief and the same model are an ensemble, not a "
            f"decomposition -- pass one --execute-slice per seat, or let the pipeline "
            f"supply its defaults."
        )


# ── the pipeline ─────────────────────────────────────────────────────────────

def _compose(sections: list[tuple[str, str]] | list[tuple[str, str, int]],
             limit: int = MAX_USER_MSG_CHARS) -> str:
    """Compose stage context, guaranteed to fit the rail's input ceiling.

    This used to be a plain join. The pipeline feeds each stage the stages
    before it, so organize receives three gather outputs at once and deliver
    receives most of the run; past a few substantive stages the join exceeded
    the rail's hard ceiling and the whole pipeline died with a ValueError after
    paying for every stage before the failure. Sections are now shrunk by
    declared priority, head and tail kept, with the drop stated in the text.
    """
    return compose_within(list(sections), limit)


def run_pipeline(
    user_msg: str,
    gather_seats: int = 3,
    execute_seats: int = 2,
    max_loops: int = 2,
    gather_angles: list[str] | None = None,
    execute_slices: list[str] | None = None,
    tier_overrides: dict[str, str] | None = None,
    level: str = "standard",
    workspace: str | None = None,
    allow_shell: bool = True,
    allow_net: bool = False,
    dispatch_fn: Callable[..., Any] = dispatch,
    on_stage: Callable[[StageRun], None] | None = None,
) -> PipelineResult:
    """Run the seven stages in order, looping execute/redteam on FAIL."""
    t0 = time.time()
    tiers = dict(STAGE_TIERS)
    tiers.update(tier_overrides or {})
    ledger: list[StageRun] = []

    # With a workspace the acting stages get real tools; without one they stay
    # text-only and the pipeline says so rather than pretending execute executed.
    base_box = Toolbox.from_dict(
        {"workspace": workspace, "allow_write": True,
         "allow_shell": allow_shell, "allow_net": allow_net}
    ) if workspace else None

    def toolbox_for(stage: str) -> Toolbox | None:
        if not base_box or stage not in STAGE_TOOL_POWERS:
            return None
        write, shell = STAGE_TOOL_POWERS[stage]
        # derive() hands the read-only-with-commands stages (gather, redteam) a
        # disposable copy instead of a permission their first command would walk
        # around. For redteam the copy is taken here, at call time, which is
        # after the execution slices have been integrated -- so it judges the
        # tree that actually exists, and cannot alter it.
        return base_box.derive(allow_write=write, allow_shell=shell)

    def model_for(stage: str) -> tuple[str, str]:
        tier = tiers[stage]
        return tier, resolve_tier(tier)

    def run_stage(stage: str, context: str, seats: int = 1,
                  angles: list[str] | None = None, loop: int = 0,
                  boxes: list[Toolbox] | None = None) -> StageRun:
        tier, model = model_for(stage)
        brief = STAGE_BRIEFS[stage]
        specs = []
        for i in range(seats):
            angle = (angles[i] if angles and i < len(angles) else "")
            role = stage if seats == 1 else f"{stage}-{i + 1}"
            specs.append(SeatSpec(
                role=role, model=model, phase=stage, query_angle=angle,
                brief=brief + (f"\n\nYOUR ANGLE: {angle}\n" if angle else ""),
                toolbox=(boxes[i] if boxes and i < len(boxes) else toolbox_for(stage)),
            ))
        r = dispatch_fn(specs, user_msg=context, level=level)
        data = r.to_dict() if hasattr(r, "to_dict") else r
        subs = data.get("substrates", []) or []
        outs = [s.get("output", "") for s in subs]
        audits = [s.get("tools") or {} for s in subs]
        run = StageRun(
            stage=stage, tier=tier, model=model, seats=seats, loop=loop,
            elapsed_s=data.get("elapsed_s", 0.0),
            status="ok" if data.get("dispatch_state") == "completed" else "degraded",
            outputs=outs, anomalies=list(data.get("anomalies") or []),
            tool_calls=sum(int(a.get("tool_calls") or 0) for a in audits),
            tools_used=sorted({t for a in audits for t in (a.get("tools_used") or [])}),
            tools_backed=any(a.get("workspace_backed") for a in audits),
        )
        ledger.append(run)
        if on_stage:
            on_stage(run)
        return run

    def finish(state: str, reason: str, answer: str = "") -> PipelineResult:
        return PipelineResult(
            state=state, reason=reason, loops=loops_done,
            elapsed_s=time.time() - t0, ledger=ledger, answer=answer,
        )

    loops_done = 0

    # Refuse BEFORE spending anything if the adversarial seats cannot be
    # independent. Discovering this after the heavy stage has run would mean
    # paying for the most expensive step and then throwing the check away.
    assert_family_split(resolve_tier(tiers["review"]), resolve_tier(tiers["heavy"]),
                        "review stage")
    assert_family_split(resolve_tier(tiers["redteam"]), resolve_tier(tiers["execute"]),
                        "red team stage")

    execute_seats = max(1, execute_seats)
    slices = list(execute_slices) if execute_slices else default_execute_slices(execute_seats)
    assert_distinct_slices(slices, execute_seats)

    # 1. orient
    # Composed, not raw: a long request must be budgeted on stage one too,
    # or the pipeline dies at the ceiling before it has composed anything.
    orient = run_stage("orient", _compose([("original request", user_msg, 5)]))

    # 2. gather (parallel)
    angles = gather_angles or [
        "the facts and current state this task depends on",
        "prior art, existing solutions, and what has already been tried",
        "constraints, risks, and the ways this is known to go wrong",
    ][:gather_seats]
    gather_ctx = _compose([("original request", user_msg, 5),
                           ("working brief", orient.text, 4)])
    gather = run_stage("gather", gather_ctx, seats=max(1, gather_seats), angles=angles)

    # 3. organize
    organize = run_stage("organize", _compose([
        ("original request", user_msg, 5),
        ("working brief", orient.text, 4),
        ("raw findings from the gathering seats", gather.text),
    ]))

    # 4. heavy
    heavy = run_stage("heavy", _compose([
        ("original request", user_msg, 5),
        ("working brief", orient.text, 4),
        ("organized context", organize.text),
    ]))

    # 5. review -- gets the BRIEF as well as the plan, deliberately
    review = run_stage("review", _compose([
        ("original request", user_msg, 5),
        ("working brief", orient.text, 4),
        ("the plan under review", heavy.text, 3),
    ]))
    review.verdict = parse_verdict(review.text) or "UNDECLARED"
    review_ok, review_reason = evaluate_review(review.text)
    if not review_ok:
        return finish("failed", f"{review_reason}; nothing was executed")

    # 6/7. execute -> redteam, looping on FAIL
    required_changes = ""
    integration = ""
    while True:
        # Each parallel slice works in its own copy of the workspace. Sharing one
        # directory across concurrent seats meant two slices could edit the same
        # file at the same time and run their tests against each other's
        # half-written state; the winner was whoever wrote last. With one seat
        # there is no race, so it works in the real tree directly.
        exec_boxes: list[Toolbox] | None = None
        before: dict[str, str] = {}
        if base_box and execute_seats > 1:
            before = base_box.manifest()
            exec_boxes = [toolbox_for("execute").snapshot(f"exec{loops_done}-{i + 1}")
                          for i in range(execute_seats)]

        execute = run_stage("execute", _compose([
            ("original request", user_msg, 5),
            ("working brief", orient.text, 4),
            ("approved plan", heavy.text, 3),
            ("review findings to honour", review.text),
            ("required changes from the previous red-team pass", required_changes),
        ]), seats=execute_seats, angles=slices, loop=loops_done, boxes=exec_boxes)

        if exec_boxes and base_box:
            merged = integrate_slices(base_box, exec_boxes, before)
            integration = integration_report(merged)
            if merged["conflicts"]:
                execute.status = "degraded"
                execute.anomalies.extend(merged["conflicts"])

        redteam = run_stage("redteam", _compose([
            ("original request", user_msg, 5),
            ("working brief and its test criteria", orient.text, 4),
            ("the plan", heavy.text),
            ("what execution produced", execute.text, 3),
            ("how the parallel slices were integrated", integration),
        ]), loop=loops_done)

        passed, reason = evaluate_redteam(redteam.text, redteam.to_dict())
        redteam.verdict = "PASS" if passed else "FAIL"
        if passed and integration and "COLLISIONS" in integration:
            passed, reason = False, ("slices collided during integration, so the tree the red "
                                     "team passed is missing work: " + integration)
            redteam.verdict = "FAIL"
        if passed:
            break
        if loops_done >= max_loops:
            return finish("failed",
                          f"red team did not pass within {max_loops} loop(s): {reason}")
        loops_done += 1
        required_changes = redteam.text

    # 8. deliver
    deliver = run_stage("deliver", _compose([
        ("original request", user_msg, 5),
        ("working brief and its test criteria", orient.text, 4),
        ("organized context", organize.text),
        ("the plan", heavy.text),
        ("what was built", execute.text, 3),
        ("red team verdict and attacks", redteam.text),
    ]))

    assert_stage_order([r.stage for r in ledger])
    return finish("completed", "red team passed", answer=deliver.text)
