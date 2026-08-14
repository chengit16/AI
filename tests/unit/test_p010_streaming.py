"""验证 P0-10 流式 Run、游标、保留期和回放预算。"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.streaming.application.service import StreamService
from ai_platform_api.modules.streaming.domain.errors import (
    ConversationBusyError,
    StreamCursorNotFoundError,
    StreamEventExpiredError,
    StreamReplayLimitExceededError,
    StreamRunClosedError,
    StreamRunNotFoundError,
)
from ai_platform_api.modules.streaming.domain.models import (
    SseEventType,
    StreamEvent,
    StreamPolicy,
    StreamReplay,
    StreamRun,
)

WORKSPACE_ID = UUID(int=1)
OTHER_WORKSPACE_ID = UUID(int=2)
CONVERSATION_ID = UUID(int=3)
MESSAGE_ID = UUID(int=4)
RUN_ID = UUID(int=5)
TRACE_ID = "0123456789abcdef0123456789abcdef"
TRACEPARENT = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


@dataclass
class FakeStreamStore:
    runs: dict[UUID, StreamRun] = field(default_factory=dict)
    events: list[StreamEvent] = field(default_factory=list)
    start_calls: int = 0

    def start_run(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        run_id: UUID,
        now: datetime,
        policy: StreamPolicy,
    ) -> StreamRun:
        self.start_calls += 1
        if any(
            run.conversation_id == conversation_id and run.status == "active"
            for run in self.runs.values()
        ):
            raise ConversationBusyError
        run = StreamRun(
            run_id=run_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=message_id,
            status="active",
            last_sequence_no=0,
            expires_at=now + timedelta(seconds=policy.retention_seconds),
            final_payload=None,
        )
        self.runs[run_id] = run
        return run

    def append_event(
        self,
        run_id: UUID,
        event_id: UUID,
        event_type: SseEventType,
        trace_id: str,
        traceparent: str,
        payload: dict[str, object],
        now: datetime,
    ) -> StreamEvent:
        run = self.runs.get(run_id)
        if run is None:
            raise StreamRunNotFoundError
        if run.status != "active":
            raise StreamRunClosedError
        existing = next(
            (
                event
                for event in self.events
                if event.run_id == run_id and event.event_id == event_id
            ),
            None,
        )
        if existing is not None:
            return existing
        event = StreamEvent(
            event_id=event_id,
            event_type=event_type,
            workspace_id=run.workspace_id,
            conversation_id=run.conversation_id,
            message_id=run.message_id,
            run_id=run.run_id,
            sequence_no=run.last_sequence_no + 1,
            occurred_at=now,
            expires_at=run.expires_at,
            trace_id=trace_id,
            traceparent=traceparent,
            payload=payload,
        )
        self.events.append(event)
        self.runs[run_id] = StreamRun(**{**run.__dict__, "last_sequence_no": event.sequence_no})
        return event

    def finish_run(
        self,
        run_id: UUID,
        status: str,
        final_payload: dict[str, object] | None,
        now: datetime,
    ) -> StreamRun:
        del now
        run = self.runs.get(run_id)
        if run is None:
            raise StreamRunNotFoundError
        if run.status != "active":
            raise StreamRunClosedError
        finished = StreamRun(**{**run.__dict__, "status": status, "final_payload": final_payload})
        self.runs[run_id] = finished
        return finished

    def replay(
        self,
        workspace_id: UUID,
        run_id: UUID,
        last_event_id: UUID | None,
        now: datetime,
        policy: StreamPolicy,
    ) -> StreamReplay:
        run = self.runs.get(run_id)
        if run is None or run.workspace_id != workspace_id:
            raise StreamRunNotFoundError
        if run.expires_at <= now:
            raise StreamEventExpiredError
        start = 0
        if last_event_id is not None:
            cursor = next((event for event in self.events if event.event_id == last_event_id), None)
            if cursor is None:
                raise StreamCursorNotFoundError
            start = cursor.sequence_no
        events = tuple(
            event
            for event in self.events
            if event.run_id == run_id and event.sequence_no > start and event.expires_at > now
        )
        if (
            len(events) > policy.replay_limit_events
            or sum(event.encoded_size() for event in events) > policy.replay_limit_bytes
        ):
            raise StreamReplayLimitExceededError
        return StreamReplay(events, run, not events and run.final_payload is not None)


def service(store: FakeStreamStore, **policy_kwargs: int) -> StreamService:
    return StreamService(store, StreamPolicy(**policy_kwargs))


def test_run_events_have_strict_sequence_and_replay_after_last_event() -> None:
    store = FakeStreamStore()
    stream = service(store)
    run = stream.start_run(WORKSPACE_ID, CONVERSATION_ID, MESSAGE_ID, run_id=RUN_ID, now=NOW)
    first = stream.append(
        RUN_ID,
        "message.delta",
        TRACE_ID,
        TRACEPARENT,
        {"delta": "第一段"},
        event_id=UUID(int=6),
        now=NOW,
    )
    second = stream.append(
        RUN_ID,
        "message.completed",
        TRACE_ID,
        TRACEPARENT,
        {"text": "完成"},
        event_id=UUID(int=7),
        now=NOW,
    )

    replay = stream.replay(WORKSPACE_ID, run.run_id, first.event_id, now=NOW)

    assert [event.sequence_no for event in replay.events] == [2]
    assert replay.events[0].event_id == second.event_id
    assert replay.events[0].traceparent == TRACEPARENT
    assert store.start_calls == 1


def test_repeating_same_event_id_returns_existing_event_without_advancing_sequence() -> None:
    store = FakeStreamStore()
    stream = service(store)
    stream.start_run(WORKSPACE_ID, CONVERSATION_ID, MESSAGE_ID, run_id=RUN_ID, now=NOW)
    event_id = UUID(int=8)
    first = stream.append(
        RUN_ID,
        "message.delta",
        TRACE_ID,
        TRACEPARENT,
        {"delta": "幂等"},
        event_id=event_id,
        now=NOW,
    )
    duplicate = stream.append(
        RUN_ID,
        "message.delta",
        TRACE_ID,
        TRACEPARENT,
        {"delta": "重复载荷"},
        event_id=event_id,
        now=NOW,
    )

    assert duplicate == first
    assert store.runs[RUN_ID].last_sequence_no == 1


def test_reconnect_only_replays_and_never_creates_another_run() -> None:
    store = FakeStreamStore()
    stream = service(store)
    run = stream.start_run(WORKSPACE_ID, CONVERSATION_ID, MESSAGE_ID, run_id=RUN_ID, now=NOW)
    stream.append(RUN_ID, "message.delta", TRACE_ID, TRACEPARENT, {"delta": "保留"}, now=NOW)
    replay = stream.replay(WORKSPACE_ID, run.run_id, None, now=NOW)

    assert replay.final_run.run_id == RUN_ID
    assert store.start_calls == 1
    with pytest.raises(ConversationBusyError):
        stream.start_run(WORKSPACE_ID, CONVERSATION_ID, MESSAGE_ID, run_id=uuid4(), now=NOW)
    assert store.start_calls == 2


def test_same_conversation_can_start_after_previous_run_finishes() -> None:
    store = FakeStreamStore()
    stream = service(store)
    stream.start_run(WORKSPACE_ID, CONVERSATION_ID, MESSAGE_ID, run_id=RUN_ID, now=NOW)
    stream.finish(RUN_ID, "completed", {"text": "最终结果"}, now=NOW)
    next_run = stream.start_run(WORKSPACE_ID, CONVERSATION_ID, MESSAGE_ID, run_id=uuid4(), now=NOW)

    assert next_run.status == "active"


def test_closed_run_rejects_new_events_and_second_finish() -> None:
    store = FakeStreamStore()
    stream = service(store)
    stream.start_run(WORKSPACE_ID, CONVERSATION_ID, MESSAGE_ID, run_id=RUN_ID, now=NOW)
    stream.finish(RUN_ID, "failed", {"error": "合成失败"}, now=NOW)

    with pytest.raises(StreamRunClosedError):
        stream.append(RUN_ID, "message.delta", TRACE_ID, TRACEPARENT, {}, now=NOW)
    with pytest.raises(StreamRunClosedError):
        stream.finish(RUN_ID, "completed", None, now=NOW)


def test_replay_workspace_cursor_expiration_and_limits_fail_closed() -> None:
    store = FakeStreamStore()
    stream = service(store, retention_seconds=10, replay_limit_events=1)
    stream.start_run(WORKSPACE_ID, CONVERSATION_ID, MESSAGE_ID, run_id=RUN_ID, now=NOW)
    first = stream.append(RUN_ID, "message.delta", TRACE_ID, TRACEPARENT, {}, now=NOW)
    stream.append(RUN_ID, "message.delta", TRACE_ID, TRACEPARENT, {}, now=NOW)

    with pytest.raises(StreamReplayLimitExceededError):
        stream.replay(WORKSPACE_ID, RUN_ID, None, now=NOW)
    with pytest.raises(StreamRunNotFoundError):
        stream.replay(OTHER_WORKSPACE_ID, RUN_ID, None, now=NOW)
    with pytest.raises(StreamCursorNotFoundError):
        stream.replay(WORKSPACE_ID, RUN_ID, uuid4(), now=NOW)
    with pytest.raises(StreamEventExpiredError):
        stream.replay(WORKSPACE_ID, RUN_ID, first.event_id, now=NOW + timedelta(seconds=11))


def test_completed_run_returns_snapshot_when_events_are_acknowledged() -> None:
    store = FakeStreamStore()
    stream = service(store)
    run = stream.start_run(WORKSPACE_ID, CONVERSATION_ID, MESSAGE_ID, run_id=RUN_ID, now=NOW)
    event = stream.append(RUN_ID, "message.completed", TRACE_ID, TRACEPARENT, {}, now=NOW)
    stream.finish(RUN_ID, "completed", {"text": "最终结果"}, now=NOW)

    replay = stream.replay(WORKSPACE_ID, run.run_id, event.event_id, now=NOW)

    assert replay.events == ()
    assert replay.snapshot_required is True
    assert replay.final_run.final_payload == {"text": "最终结果"}
