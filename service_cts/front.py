from enum import StrEnum

from service_cts.core import Edge, FiniteCTS, NoEdgePolicy


class FrontState(StrEnum):
    SIGNED_OUT = "signed_out"
    CHOOSING = "choosing"
    READY = "ready"
    SENDING = "sending"
    PROCESSING = "processing"
    UNKNOWN = "unknown"
    FAILED = "failed"


class FrontAction(StrEnum):
    LOGIN_OK = "login_ok"
    LOGIN_ERROR = "login_error"
    LOGOUT_OK = "logout_ok"
    LOGOUT_ERROR = "logout_error"
    AUTH_EXPIRED = "auth_expired"
    CREATE_OK = "create_ok"
    CREATE_ERROR = "create_error"
    OPEN = "open"
    CONVERSATION_LOST = "conversation_lost"
    SEND = "send"
    SEND_OK = "send_ok"
    SEND_ERROR = "send_error"
    RECORD_PROCESSING = "record_processing"
    RECORD_COMPLETED = "record_completed"
    RECORD_FAILED = "record_failed"
    RECORD_MISSING = "record_missing"
    RETRY_UNKNOWN = "retry_unknown"
    RETRY_FAILED = "retry_failed"


def project(session):
    if not session.token:
        if session.conversation_id or session.pending_id or session.sending:
            raise ValueError("signed-out session retained conversation state")
        return FrontState.SIGNED_OUT
    if not session.conversation_id:
        if session.pending_id or session.sending:
            raise ValueError("pending question has no conversation")
        return FrontState.CHOOSING
    if session.sending:
        if not session.pending_id:
            raise ValueError("sending without a question ID")
        return FrontState.SENDING
    if not session.pending_id:
        if session.pending_status:
            raise ValueError("pending status has no question ID")
        return FrontState.READY
    states = {
        "processing": FrontState.PROCESSING,
        "unknown": FrontState.UNKNOWN,
        "failed": FrontState.FAILED,
    }
    if session.pending_status not in states:
        raise ValueError("unknown pending status")
    return states[session.pending_status]


EDGES = (
    *(Edge(state, FrontAction.LOGIN_OK, FrontState.CHOOSING) for state in FrontState),
    *(Edge(state, FrontAction.LOGIN_ERROR, FrontState.SIGNED_OUT) for state in FrontState),
    *(Edge(state, FrontAction.LOGOUT_OK, FrontState.SIGNED_OUT) for state in FrontState),
    *(Edge(state, FrontAction.LOGOUT_ERROR, state) for state in FrontState),
    *(Edge(state, FrontAction.AUTH_EXPIRED, FrontState.SIGNED_OUT) for state in FrontState),
    *(
        Edge(state, FrontAction.CREATE_OK, FrontState.READY)
        for state in (FrontState.CHOOSING, FrontState.READY, FrontState.FAILED)
    ),
    *(
        Edge(state, FrontAction.CREATE_ERROR, state)
        for state in (FrontState.CHOOSING, FrontState.READY, FrontState.FAILED)
    ),
    *(
        Edge(state, FrontAction.OPEN, FrontState.READY)
        for state in (
            FrontState.CHOOSING,
            FrontState.READY,
            FrontState.PROCESSING,
            FrontState.UNKNOWN,
            FrontState.FAILED,
        )
    ),
    *(
        Edge(state, FrontAction.CONVERSATION_LOST, FrontState.CHOOSING)
        for state in (
            FrontState.READY,
            FrontState.SENDING,
            FrontState.PROCESSING,
            FrontState.UNKNOWN,
            FrontState.FAILED,
        )
    ),
    Edge(FrontState.READY, FrontAction.SEND, FrontState.SENDING),
    Edge(FrontState.FAILED, FrontAction.SEND, FrontState.SENDING),
    Edge(FrontState.SENDING, FrontAction.SEND_OK, FrontState.READY),
    Edge(FrontState.SENDING, FrontAction.SEND_ERROR, FrontState.UNKNOWN),
    *(
        Edge(state, FrontAction.RECORD_PROCESSING, FrontState.PROCESSING)
        for state in (FrontState.READY, FrontState.UNKNOWN, FrontState.PROCESSING)
    ),
    *(
        Edge(state, FrontAction.RECORD_COMPLETED, FrontState.READY)
        for state in (FrontState.UNKNOWN, FrontState.PROCESSING)
    ),
    *(
        Edge(state, FrontAction.RECORD_FAILED, FrontState.FAILED)
        for state in (FrontState.UNKNOWN, FrontState.PROCESSING)
    ),
    *(
        Edge(state, FrontAction.RECORD_MISSING, FrontState.UNKNOWN)
        for state in (FrontState.UNKNOWN, FrontState.PROCESSING)
    ),
    Edge(FrontState.UNKNOWN, FrontAction.RETRY_UNKNOWN, FrontState.SENDING),
    Edge(FrontState.FAILED, FrontAction.RETRY_FAILED, FrontState.SENDING),
)

SPECIAL_NO_EDGES = {
    (FrontState.SIGNED_OUT, FrontAction.SEND): NoEdgePolicy.AUTH_REQUIRED,
    (FrontState.CHOOSING, FrontAction.SEND): NoEdgePolicy.NEEDS_CONVERSATION,
    **{
        (state, FrontAction.SEND): NoEdgePolicy.BUSY
        for state in (FrontState.SENDING, FrontState.PROCESSING, FrontState.UNKNOWN)
    },
    **{
        (state, action): NoEdgePolicy.BUSY
        for state in (FrontState.SENDING, FrontState.PROCESSING, FrontState.UNKNOWN)
        for action in (FrontAction.CREATE_OK, FrontAction.CREATE_ERROR)
    },
    **{
        (FrontState.SIGNED_OUT, action): NoEdgePolicy.AUTH_REQUIRED
        for action in (FrontAction.CREATE_OK, FrontAction.CREATE_ERROR, FrontAction.OPEN)
    },
    (FrontState.SENDING, FrontAction.OPEN): NoEdgePolicy.BUSY,
    **{
        (state, action): NoEdgePolicy.STALE
        for state in FrontState
        if state != FrontState.SENDING
        for action in (FrontAction.SEND_OK, FrontAction.SEND_ERROR)
    },
    **{
        (state, FrontAction.CONVERSATION_LOST): NoEdgePolicy.STALE
        for state in (FrontState.SIGNED_OUT, FrontState.CHOOSING)
    },
    **{
        (state, FrontAction.RECORD_PROCESSING): NoEdgePolicy.STALE
        for state in (FrontState.SIGNED_OUT, FrontState.CHOOSING, FrontState.SENDING)
    },
    (FrontState.READY, FrontAction.RECORD_MISSING): NoEdgePolicy.STALE,
    **{
        (state, action): NoEdgePolicy.BUSY
        for state in (FrontState.SENDING, FrontState.PROCESSING)
        for action in (FrontAction.RETRY_UNKNOWN, FrontAction.RETRY_FAILED)
    },
}

FRONT_CTS = FiniteCTS("front", FrontState, FrontAction, EDGES, SPECIAL_NO_EDGES)
