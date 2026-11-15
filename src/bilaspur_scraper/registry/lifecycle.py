"""Source lifecycle state machine (`ARCHITECTURE.md` §4).

Strict, enforced transitions. Importing a registry row never advances a
source beyond `candidate` (and search stubs/aggregators start at
`excluded`). Nothing in this module touches the network.
"""

from __future__ import annotations

from ..errors import LifecycleError
from ..schemas.enums import LifecycleState

S = LifecycleState

#: Allowed structural transitions (`ARCHITECTURE.md` §4.2).
ALLOWED_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    S.CANDIDATE: frozenset({S.DISCOVERED, S.EXCLUDED}),
    S.DISCOVERED: frozenset({S.VERIFICATION_PENDING, S.EXCLUDED}),
    # `verification_pending -> discovered` is the recovery path for a
    # verification interrupted mid-flight: the operator re-queues the source
    # instead of it being stuck in a state nothing can leave.
    S.VERIFICATION_PENDING: frozenset({S.VERIFIED, S.REJECTED, S.DISABLED,
                                       S.DISCOVERED}),
    S.VERIFIED: frozenset({S.ENABLED, S.VERIFICATION_PENDING, S.EXCLUDED}),
    S.ENABLED: frozenset({S.DISABLED, S.EXCLUDED}),
    S.DISABLED: frozenset({S.VERIFICATION_PENDING, S.ENABLED}),
    S.REJECTED: frozenset({S.DISCOVERED}),
    S.EXCLUDED: frozenset(),
}

#: Transitions that require an operator-supplied audit reason.
REASON_REQUIRED = frozenset({(S.REJECTED, S.DISCOVERED), (S.DISABLED, S.ENABLED)})


def assert_transition(current: LifecycleState, target: LifecycleState, *,
                      reason: str | None = None) -> None:
    """Raise `LifecycleError` unless `current -> target` is legal.

    `reason` is mandatory for `rejected -> discovered` (operator-authorized
    reopening with an audit trail) and for `disabled -> enabled`.
    """
    if current == target:
        raise LifecycleError(f"source is already {current.value!r}")
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise LifecycleError(
            f"forbidden transition {current.value!r} -> {target.value!r}"
        )
    if (current, target) in REASON_REQUIRED and not (reason and reason.strip()):
        raise LifecycleError(
            f"transition {current.value!r} -> {target.value!r} requires an audit reason"
        )
