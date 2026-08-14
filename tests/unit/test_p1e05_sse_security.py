"""复用 p0-11-v1 安全集验证 P1E-05 重连和回放预算。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from ai_platform_api.app.errors import ErrorCatalog
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.assistant.api import routes as assistant_routes
from ai_platform_api.modules.assistant.application.service import (
    AssistantConversationService,
    AssistantRun,
)
from ai_platform_api.modules.streaming.application.service import (
    StreamService,
    TransactionalStreamService,
)
from ai_platform_api.modules.streaming.domain.errors import (
    ConversationBusyError,
    StreamReplayLimitExceededError,
)
from ai_platform_api.modules.streaming.domain.models import StreamPolicy, StreamReplay, StreamRun

from tests.unit.test_p010_streaming import (
    CONVERSATION_ID,
    MESSAGE_ID,
    NOW,
    RUN_ID,
    TRACE_ID,
    TRACEPARENT,
    WORKSPACE_ID,
    FakeStreamStore,
)

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "security" / "p0-11-v1.json"


def _case(case_id: str) -> dict[str, Any]:
    """按稳定编号读取阶段 0 SSE 样本，避免复制安全阈值和预期。"""

    fixture = cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))
    return next(
        case for case in cast(list[dict[str, Any]], fixture["cases"]) if case["case_id"] == case_id
    )


def test_p011_reconnect_case_replays_existing_run_without_duplicate_generation() -> None:
    """多次重连只读取同一 Run；尝试创建第二个活动 Run 必须失败关闭。"""

    case = _case("p011-sse-replay-budget-001")
    store = FakeStreamStore()
    streams = StreamService(store, StreamPolicy())
    run = streams.start_run(
        WORKSPACE_ID,
        CONVERSATION_ID,
        MESSAGE_ID,
        run_id=RUN_ID,
        now=NOW,
    )
    event = streams.append(
        RUN_ID,
        "message.delta",
        TRACE_ID,
        TRACEPARENT,
        {"delta": "合成事件"},
        now=NOW,
    )

    # 样本中的每次 reconnect 都从持久化游标回放，不能调用 start_run。
    for _ in cast(dict[str, Any], case["input"])["reconnects"]:
        replay = streams.replay(WORKSPACE_ID, run.run_id, event.event_id, now=NOW)
        assert replay.final_run.run_id == RUN_ID
    with pytest.raises(ConversationBusyError) as captured:
        streams.start_run(
            WORKSPACE_ID,
            CONVERSATION_ID,
            MESSAGE_ID,
            run_id=uuid4(),
            now=NOW,
        )

    assert case["expected_security_result"] == "deny"
    assert case["expected_error_code"] == captured.value.error_code
    assert store.start_calls == 2


def test_p011_replay_budget_case_matches_policy_and_fails_closed() -> None:
    """固定数据集必须与 5000 事件、10 MiB 的正式回放预算保持一致。"""

    case = _case("p011-sse-replay-budget-002")
    sample = cast(dict[str, Any], case["input"])
    policy = StreamPolicy()
    assert sample["event_count"] == policy.replay_limit_events + 1
    assert sample["payload_bytes"] == policy.replay_limit_bytes + 1

    # 用缩小但同构的预算执行边界回归，避免单元测试构造 5001 条大事件。
    store = FakeStreamStore()
    streams = StreamService(store, StreamPolicy(replay_limit_events=1))
    streams.start_run(
        WORKSPACE_ID,
        CONVERSATION_ID,
        MESSAGE_ID,
        run_id=RUN_ID,
        now=NOW,
    )
    for _ in range(2):
        streams.append(
            RUN_ID,
            "message.delta",
            TRACE_ID,
            TRACEPARENT,
            {"delta": "合成事件"},
            now=NOW,
        )

    with pytest.raises(StreamReplayLimitExceededError) as captured:
        streams.replay(WORKSPACE_ID, RUN_ID, None, now=NOW)

    assert case["expected_security_result"] == "deny"
    assert case["expected_error_code"] == captured.value.error_code
    catalog = ErrorCatalog.load(ROOT / "contracts" / "errors" / "catalog.v1.json")
    assert case["expected_status"] == catalog.resolve(captured.value.error_code).http_status


def test_active_http_stream_emits_heartbeat_without_persisting_an_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """活动 Run 没有新事件时发送注释心跳，心跳不能占用事件序号或回放游标。"""

    run = cast(
        AssistantRun,
        SimpleNamespace(
            run_id=RUN_ID,
            conversation_id=CONVERSATION_ID,
            trace_id=TRACE_ID,
            traceparent=TRACEPARENT,
        ),
    )
    active = StreamRun(
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
        status="active",
        last_sequence_no=0,
        expires_at=NOW.replace(year=2027),
        final_payload=None,
    )
    context = RequestContext.trusted(
        actor_id=uuid4(),
        user_id=uuid4(),
        workspace_id=WORKSPACE_ID,
        trace=TraceContext(TRACE_ID, TRACEPARENT.split("-")[2]),
        authentication_method="browser_session",
    )
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))

    frames = assistant_routes._stream_frames(
        cast(AssistantConversationService, object()),
        cast(TransactionalStreamService, object()),
        context,
        run,
        None,
        StreamReplay((), active, False),
        heartbeat_seconds=1,
        poll_interval_ms=50,
    )

    assert next(frames) == ": heartbeat\n\n"
    assert active.last_sequence_no == 0
