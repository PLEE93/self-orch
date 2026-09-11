"""Fit a stage's composed context inside the rail's hard input ceiling.

The pipeline hands each stage the stages before it: organize receives the
original request plus the brief plus every gathering seat's output, and deliver
receives nearly the whole run. Three substantive gather outputs comfortably
exceed the rail's per-dispatch character ceiling, so an honest long run used to
die at the ceiling with a ValueError -- in the middle of the pipeline, after
paying for every stage before it.

Raising the ceiling only moves the cliff. The fix is a budget: compose the
sections, and when they do not fit, shrink them by declared priority, keeping
the head and tail of each and saying in the text exactly how much was dropped.
A truncated section is visible to the model reading it, which is the difference
between a shortened context and a silently corrupted one.
"""

from __future__ import annotations

MIN_SECTION_CHARS = 400

Section = tuple[str, str] | tuple[str, str, int]


def _header(title: str) -> str:
    return "===== %s =====\n" % title.upper()


def _shrink(body: str, budget: int) -> str:
    if len(body) <= budget:
        return body
    dropped = len(body) - budget
    keep = max(0, budget - 60)
    head = int(keep * 0.65)
    tail = keep - head
    marker = "\n...[%d chars dropped to fit the context budget]...\n" % dropped
    return body[:head] + marker + (body[-tail:] if tail else "")


def compose_within(sections: list[Section], limit: int) -> str:
    """Compose titled sections into one string of at most `limit` characters.

    A section is (title, body) or (title, body, priority); higher priority keeps
    more of its text when the budget bites. Empty bodies are dropped entirely.
    The original request always outranks derived material by convention -- give
    it the highest priority at the call site.
    """
    live: list[tuple[str, str, int]] = []
    for s in sections:
        title, body = s[0], (s[1] or "").strip()
        prio = int(s[2]) if len(s) > 2 else 1
        if body:
            live.append((title, body, prio))
    if not live:
        return ""

    overhead = sum(len(_header(t)) for t, _, _ in live) + 2 * (len(live) - 1)
    available = max(0, limit - overhead)
    total = sum(len(b) for _, b, _ in live)
    if total <= available:
        return "\n\n".join(_header(t) + b for t, b, _ in live)

    # Every section keeps a floor so nothing is silently dropped, then the
    # spare is handed out in priority BANDS: the highest priority sections are
    # satisfied first and only what they do not need cascades down. Sharing the
    # spare proportionally instead would let one enormous low-priority section
    # out-weigh the user's own words, which is the opposite of what priority is
    # for.
    budgets: dict[int, int] = {}
    floor = min(MIN_SECTION_CHARS, available // max(1, len(live)))
    for i, (_t, b, _p) in enumerate(live):
        budgets[i] = min(len(b), floor)
    spare = available - sum(budgets.values())
    bands: dict[int, list[int]] = {}
    for i, (_t, _b, prio) in enumerate(live):
        bands.setdefault(prio, []).append(i)
    for prio in sorted(bands, reverse=True):
        if spare <= 0:
            break
        members = bands[prio]
        want = {i: len(live[i][1]) - budgets[i] for i in members}
        total_want = sum(want.values())
        if total_want <= 0:
            continue
        grant = min(spare, total_want)
        for i, w in want.items():
            take = int(grant * (w / total_want))
            budgets[i] += take
            spare -= take

    out = [_header(t) + _shrink(b, budgets[i]) for i, (t, b, _p) in enumerate(live)]
    composed = "\n\n".join(out)
    # Rounding in the proportional split can leave a few characters over the
    # line; the ceiling is hard, so clip rather than hand the rail a value it
    # will reject.
    return composed if len(composed) <= limit else composed[:limit]
