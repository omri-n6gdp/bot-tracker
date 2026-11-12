"""Detect the bot's rebalances from Trading 212 data alone.

Each pie holding has an `expectedShare` (target weight). The bot changes those
targets when it updates the pie, so any change in targets between two snapshots is
a rebalance. No changes to the bot are needed.
"""
from __future__ import annotations

TOLERANCE = 0.0005  # 0.05 percentage points; T212 rounds shares


def diff_targets(before: dict[str, float] | None, after: dict[str, float], tol: float = TOLERANCE):
    """Return a list of changes, or [] if the targets are effectively the same.

    `before is None` means there is no baseline yet (first run): not a rebalance.
    """
    if before is None:
        return []
    changes = []
    for t in sorted(set(before) | set(after)):
        b, a = before.get(t, 0.0), after.get(t, 0.0)
        if abs(a - b) <= tol:
            continue
        if t not in before:
            kind = "added"
        elif t not in after or a == 0:
            kind = "removed"
        else:
            kind = "increased" if a > b else "decreased"
        changes.append({"ticker": t, "before": b, "after": a, "delta": a - b, "kind": kind})
    return changes
