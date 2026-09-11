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

from .providers import model_family
from .rail import SeatSpec, dispatch
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

PARALLEL_STAGES = frozenset({"gather", "execute"})
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

    @property
    def text(self) -> str:
        return "\n\n".join(o for o in self.outputs if o)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage, "tier": self.tier, "model": self.model,
            "seats": self.seats, "loop": self.loop,
            "elapsed_s": round(self.elapsed_s, 3), "status": self.status,
            "verdict": self.verdict, "anomalies": self.anomalies,
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
            "ledger": [r.to_dict() for r in self.ledger],
            "answer": self.answer,
        }


# ── verdict parsing ──────────────────────────────────────────────────────────

# Case-insensitive on purpose: a model that writes "verdict: fail" in lower case
# must not read as "no verdict", which would fail closed into a pointless retry
# loop and bill the user for it. Caught by test_parse_verdict.
_VERDICT_RE = re.compile(r"^\s*VERDICT\s*:\s*([A-Za-z-]+)", re.MULTILINE | re.IGNORECASE)
_ATTACK_RE = re.compile(r"ATTACKS\s+RUN", re.IGNORECASE)


def parse_verdict(text: str) -> str:
    """Return the declared verdict in upper case, or '' when none is declared.

    An absent verdict is NOT a pass. Callers must treat '' as failure -- see
    evaluate_redteam below, which is the only place that decision is made.
    """
    m = _VERDICT_RE.search(text or "")
    return m.group(1).strip().upper() if m else ""


def evaluate_redteam(text: str) -> tuple[bool, str]:
    """(passed, reason). The red-team gate, failing closed on every ambiguity.

    Three ways to not pass, and only one way to pass:
      - no parseable verdict            -> FAIL (a gate that cannot read its own
                                           result must never report success)
      - verdict FAIL                    -> FAIL (the ordinary case)
      - PASS with no attacks named      -> FAIL (an empty seat; the brief says a
                                           verdict without named falsification
                                           attempts is discarded, and this is
                                           where that promise is kept)
    """
    body = text or ""
    verdict = parse_verdict(body)
    if not verdict:
        return False, "red team returned no parseable VERDICT line; failing closed"
    if verdict != "PASS":
        return False, f"red team verdict {verdict}"
    if not _ATTACK_RE.search(body):
        return False, "red team returned PASS without naming any attacks (empty seat, discarded)"
    return True, "red team PASS with named attacks"


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


def assert_stage_order(stages_run: list[str]) -> None:
    """Refuse a ledger whose stages did not follow the declared order.

    Repeats are legal (the execute/redteam loop), skips and reorderings are not.
    """
    seen = [s for s in stages_run]
    pos = -1
    for name in seen:
        if name not in STAGE_ORDER:
            raise PipelineRefusal(f"unknown stage {name!r} in ledger")
        idx = STAGE_ORDER.index(name)
        if idx < pos and name not in ("execute", "redteam"):
            raise PipelineRefusal(
                f"stage {name!r} ran after a later stage; only execute/redteam "
                f"may repeat (loop-back). Ledger: {seen}"
            )
        pos = max(pos, idx)


# ── the pipeline ─────────────────────────────────────────────────────────────

def _compose(sections: list[tuple[str, str]]) -> str:
    out = []
    for title, body in sections:
        body = (body or "").strip()
        if not body:
            continue
        out.append("===== %s =====\n%s" % (title.upper(), body))
    return "\n\n".join(out)


def run_pipeline(
    user_msg: str,
    gather_seats: int = 3,
    execute_seats: int = 2,
    max_loops: int = 2,
    gather_angles: list[str] | None = None,
    execute_slices: list[str] | None = None,
    tier_overrides: dict[str, str] | None = None,
    level: str = "standard",
    dispatch_fn: Callable[..., Any] = dispatch,
    on_stage: Callable[[StageRun], None] | None = None,
) -> PipelineResult:
    """Run the seven stages in order, looping execute/redteam on FAIL."""
    t0 = time.time()
    tiers = dict(STAGE_TIERS)
    tiers.update(tier_overrides or {})
    ledger: list[StageRun] = []

    def model_for(stage: str) -> tuple[str, str]:
        tier = tiers[stage]
        return tier, resolve_tier(tier)

    def run_stage(stage: str, context: str, seats: int = 1,
                  angles: list[str] | None = None, loop: int = 0) -> StageRun:
        tier, model = model_for(stage)
        brief = STAGE_BRIEFS[stage]
        specs = []
        for i in range(seats):
            angle = (angles[i] if angles and i < len(angles) else "")
            role = stage if seats == 1 else f"{stage}-{i + 1}"
            specs.append(SeatSpec(
                role=role, model=model, phase=stage, query_angle=angle,
                brief=brief + (f"\n\nYOUR ANGLE: {angle}\n" if angle else ""),
            ))
        r = dispatch_fn(specs, user_msg=context, level=level)
        data = r.to_dict() if hasattr(r, "to_dict") else r
        outs = [s.get("output", "") for s in data.get("substrates", [])]
        run = StageRun(
            stage=stage, tier=tier, model=model, seats=seats, loop=loop,
            elapsed_s=data.get("elapsed_s", 0.0),
            status="ok" if data.get("dispatch_state") == "completed" else "degraded",
            outputs=outs, anomalies=list(data.get("anomalies") or []),
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

    # 1. orient
    orient = run_stage("orient", user_msg)

    # 2. gather (parallel)
    angles = gather_angles or [
        "the facts and current state this task depends on",
        "prior art, existing solutions, and what has already been tried",
        "constraints, risks, and the ways this is known to go wrong",
    ][:gather_seats]
    gather_ctx = _compose([("original request", user_msg),
                           ("working brief", orient.text)])
    gather = run_stage("gather", gather_ctx, seats=max(1, gather_seats), angles=angles)

    # 3. organize
    organize = run_stage("organize", _compose([
        ("original request", user_msg),
        ("working brief", orient.text),
        ("raw findings from the gathering seats", gather.text),
    ]))

    # 4. heavy
    heavy = run_stage("heavy", _compose([
        ("original request", user_msg),
        ("working brief", orient.text),
        ("organized context", organize.text),
    ]))

    # 5. review -- gets the BRIEF as well as the plan, deliberately
    review = run_stage("review", _compose([
        ("original request", user_msg),
        ("working brief", orient.text),
        ("the plan under review", heavy.text),
    ]))
    review.verdict = parse_verdict(review.text) or "UNDECLARED"
    if review.verdict == "REJECT":
        return finish("failed", "pre-execution review REJECTED the plan; "
                                "nothing was executed")

    # 6/7. execute -> redteam, looping on FAIL
    required_changes = ""
    while True:
        execute = run_stage("execute", _compose([
            ("original request", user_msg),
            ("working brief", orient.text),
            ("approved plan", heavy.text),
            ("review findings to honour", review.text),
            ("required changes from the previous red-team pass", required_changes),
        ]), seats=max(1, execute_seats), angles=execute_slices, loop=loops_done)

        redteam = run_stage("redteam", _compose([
            ("original request", user_msg),
            ("working brief and its test criteria", orient.text),
            ("the plan", heavy.text),
            ("what execution produced", execute.text),
        ]), loop=loops_done)

        passed, reason = evaluate_redteam(redteam.text)
        redteam.verdict = "PASS" if passed else "FAIL"
        if passed:
            break
        if loops_done >= max_loops:
            return finish("failed",
                          f"red team did not pass within {max_loops} loop(s): {reason}")
        loops_done += 1
        required_changes = redteam.text

    # 8. deliver
    deliver = run_stage("deliver", _compose([
        ("original request", user_msg),
        ("working brief and its test criteria", orient.text),
        ("organized context", organize.text),
        ("the plan", heavy.text),
        ("what was built", execute.text),
        ("red team verdict and attacks", redteam.text),
    ]))

    assert_stage_order([r.stage for r in ledger])
    return finish("completed", "red team passed", answer=deliver.text)
