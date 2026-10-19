from datetime import UTC, datetime
from enum import StrEnum

from service_cts.core import Edge, FiniteCTS, NoEdgePolicy


class AuthState(StrEnum):
    ABSENT = "absent"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class AuthAction(StrEnum):
    ISSUE = "issue"
    USE = "use"
    LOGOUT = "logout"
    CLOCK_EXPIRE = "clock_expire"


AUTH_EDGES = (
    Edge(AuthState.ABSENT, AuthAction.ISSUE, AuthState.ACTIVE),
    Edge(AuthState.ACTIVE, AuthAction.USE, AuthState.ACTIVE),
    Edge(AuthState.ACTIVE, AuthAction.LOGOUT, AuthState.REVOKED),
    Edge(AuthState.ACTIVE, AuthAction.CLOCK_EXPIRE, AuthState.EXPIRED),
)
AUTH_SPECIAL = {
    **{
        (state, AuthAction.USE): NoEdgePolicy.AUTH_REQUIRED
        for state in (AuthState.ABSENT, AuthState.EXPIRED, AuthState.REVOKED)
    },
    **{
        (state, AuthAction.LOGOUT): NoEdgePolicy.AUTH_REQUIRED
        for state in (AuthState.ABSENT, AuthState.EXPIRED, AuthState.REVOKED)
    },
    **{
        (state, AuthAction.CLOCK_EXPIRE): NoEdgePolicy.STALE
        for state in (AuthState.ABSENT, AuthState.EXPIRED, AuthState.REVOKED)
    },
}
AUTH_CTS = FiniteCTS("backend_auth", AuthState, AuthAction, AUTH_EDGES, AUTH_SPECIAL)


def project_auth(row, instant=None):
    if row is None:
        return AuthState.ABSENT
    if row["revoked_at"] is not None:
        return AuthState.REVOKED
    instant = instant or datetime.now(UTC)
    expiry = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    if expiry <= instant:
        return AuthState.EXPIRED
    return AuthState.ACTIVE


class TurnState(StrEnum):
    ABSENT = "absent"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TurnAction(StrEnum):
    BEGIN_NEW = "begin_new"
    REPLAY_SAME = "replay_same"
    REPLAY_CHANGED = "replay_changed"
    OTHER_BUSY = "other_busy"
    AI_SUCCESS = "ai_success"
    AI_FAILURE = "ai_failure"
    CLEANUP_STALE = "cleanup_stale"
    LATE_RESULT = "late_result"


TURN_EDGES = (
    Edge(TurnState.ABSENT, TurnAction.BEGIN_NEW, TurnState.PROCESSING),
    Edge(TurnState.PROCESSING, TurnAction.AI_SUCCESS, TurnState.COMPLETED),
    Edge(TurnState.PROCESSING, TurnAction.AI_FAILURE, TurnState.FAILED),
    Edge(TurnState.PROCESSING, TurnAction.CLEANUP_STALE, TurnState.FAILED),
    Edge(TurnState.COMPLETED, TurnAction.REPLAY_SAME, TurnState.COMPLETED),
    Edge(TurnState.FAILED, TurnAction.REPLAY_SAME, TurnState.FAILED),
    Edge(TurnState.COMPLETED, TurnAction.LATE_RESULT, TurnState.COMPLETED),
    Edge(TurnState.FAILED, TurnAction.LATE_RESULT, TurnState.FAILED),
)
TURN_SPECIAL = {
    (TurnState.PROCESSING, TurnAction.REPLAY_SAME): NoEdgePolicy.IN_PROGRESS,
    (TurnState.ABSENT, TurnAction.OTHER_BUSY): NoEdgePolicy.BUSY,
    **{
        (state, TurnAction.REPLAY_CHANGED): NoEdgePolicy.CONFLICT
        for state in (TurnState.PROCESSING, TurnState.COMPLETED, TurnState.FAILED)
    },
    **{
        (state, TurnAction.CLEANUP_STALE): NoEdgePolicy.STALE
        for state in (TurnState.COMPLETED, TurnState.FAILED)
    },
}
TURN_CTS = FiniteCTS("backend_turn", TurnState, TurnAction, TURN_EDGES, TURN_SPECIAL)


def project_turn(row):
    if row is None:
        return TurnState.ABSENT
    state = TurnState(row["status"])
    answer, error, completed = row["answer"], row["error_code"], row["completed_at"]
    valid = {
        TurnState.PROCESSING: answer is None and error is None and completed is None,
        TurnState.COMPLETED: isinstance(answer, str)
        and bool(answer.strip())
        and error is None
        and completed is not None,
        TurnState.FAILED: answer is None and error is not None and completed is not None,
    }
    if not valid[state]:
        raise ValueError("invalid persisted turn state")
    return state
