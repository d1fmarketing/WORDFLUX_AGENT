"""Unit tests for SSE event system."""
import json
import time
from unittest.mock import MagicMock, patch, call

import pytest

from src.core.events import (
    BaseEvent,
    ChatMessageEvent,
    JobStartedEvent,
    JobSucceededEvent,
    JobFailedEvent,
    BoardUpdateEvent,
    WIPLimitExceededEvent,
    SSEEmitter,
    get_default_emitter,
    set_default_emitter,
    emit_chat_message,
    emit_job_started,
    emit_job_succeeded,
    emit_job_failed,
    emit_board_update,
    emit_wip_limit_exceeded,
    format_sse_event,
    format_sse_comment,
    format_sse_heartbeat,
)


class TestEventDataclasses:
    """Test event dataclass definitions and structure."""

    def test_base_event_provides_to_dict(self):
        """Test BaseEvent provides to_dict method for subclasses."""
        # BaseEvent is not a dataclass, so test via a concrete subclass
        event = ChatMessageEvent(role="user", text="Test")
        d = event.to_dict()
        assert "kind" in d
        assert "ts" in d
        assert "role" in d
        assert "text" in d

    def test_chat_message_event_structure(self):
        """Test ChatMessageEvent has correct fields and defaults."""
        event = ChatMessageEvent(role="user", text="Hello", session_id="sess-123")

        assert event.kind == "chat_message"
        assert event.role == "user"
        assert event.text == "Hello"
        assert event.session_id == "sess-123"
        assert isinstance(event.ts, int)

    def test_chat_message_event_without_session(self):
        """Test ChatMessageEvent with optional session_id."""
        event = ChatMessageEvent(role="assistant", text="Response")

        assert event.kind == "chat_message"
        assert event.role == "assistant"
        assert event.text == "Response"
        assert event.session_id is None

    def test_chat_message_event_to_dict(self):
        """Test ChatMessageEvent serialization."""
        event = ChatMessageEvent(role="user", text="Test", session_id="s1")
        d = event.to_dict()

        assert d["kind"] == "chat_message"
        assert d["role"] == "user"
        assert d["text"] == "Test"
        assert d["session_id"] == "s1"
        assert "ts" in d

    def test_job_started_event_structure(self):
        """Test JobStartedEvent has correct fields."""
        event = JobStartedEvent(job_id="job-1", action="test_agent", card_id="card-1")

        assert event.kind == "job_started"
        assert event.job_id == "job-1"
        assert event.action == "test_agent"
        assert event.card_id == "card-1"

    def test_job_started_event_without_card(self):
        """Test JobStartedEvent with optional card_id."""
        event = JobStartedEvent(job_id="job-2", action="agent")

        assert event.kind == "job_started"
        assert event.job_id == "job-2"
        assert event.action == "agent"
        assert event.card_id is None

    def test_job_succeeded_event_structure(self):
        """Test JobSucceededEvent has correct fields."""
        event = JobSucceededEvent(
            job_id="job-3",
            action="agent",
            card_id="card-2",
            data={"result": "ok"}
        )

        assert event.kind == "job_succeeded"
        assert event.job_id == "job-3"
        assert event.action == "agent"
        assert event.card_id == "card-2"
        assert event.data == {"result": "ok"}

    def test_job_succeeded_event_minimal(self):
        """Test JobSucceededEvent with only required fields."""
        event = JobSucceededEvent(job_id="job-4", action="agent")

        assert event.kind == "job_succeeded"
        assert event.card_id is None
        assert event.data is None

    def test_job_failed_event_structure(self):
        """Test JobFailedEvent has correct fields."""
        event = JobFailedEvent(
            job_id="job-5",
            action="agent",
            error="Something went wrong",
            card_id="card-3"
        )

        assert event.kind == "job_failed"
        assert event.job_id == "job-5"
        assert event.action == "agent"
        assert event.error == "Something went wrong"
        assert event.card_id == "card-3"

    def test_board_update_event_structure(self):
        """Test BoardUpdateEvent has correct fields."""
        cards = [
            {"id": "c1", "title": "Card 1", "list": "backlog"},
            {"id": "c2", "title": "Card 2", "list": "in_progress"}
        ]
        event = BoardUpdateEvent(cards=cards)

        assert event.kind == "board_update"
        assert event.cards == cards
        assert len(event.cards) == 2

    def test_board_update_event_empty_cards(self):
        """Test BoardUpdateEvent with empty card list."""
        event = BoardUpdateEvent(cards=[])

        assert event.kind == "board_update"
        assert event.cards == []

    def test_wip_limit_exceeded_event_structure(self):
        """Test WIPLimitExceededEvent has correct fields."""
        event = WIPLimitExceededEvent(
            column="in_progress",
            card_id="card-4",
            card_title="Test Card",
            current_count=2,
            limit=2
        )

        assert event.kind == "wip_limit_exceeded"
        assert event.column == "in_progress"
        assert event.card_id == "card-4"
        assert event.card_title == "Test Card"
        assert event.current_count == 2
        assert event.limit == 2


class TestSSEEmitter:
    """Test SSEEmitter class functionality."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        mock = MagicMock()
        mock.ping.return_value = True
        mock.pipeline.return_value = mock
        mock.publish.return_value = 1
        mock.lpush.return_value = 1
        mock.ltrim.return_value = True
        mock.execute.return_value = [1, 1, True]
        return mock

    @pytest.fixture
    def emitter(self, mock_redis):
        """Create an SSEEmitter with mocked Redis."""
        emitter = SSEEmitter()
        emitter._redis_client = mock_redis
        return emitter

    def test_emitter_initialization(self):
        """Test SSEEmitter initializes with correct defaults."""
        emitter = SSEEmitter()

        assert emitter.redis_url == "redis://localhost:6379/0"
        assert emitter.channel == "wf:events"
        assert emitter.list_key == "wf:events:recent"
        assert emitter.history_size == 200
        assert emitter._redis_client is None

    def test_emitter_custom_configuration(self):
        """Test SSEEmitter accepts custom configuration."""
        emitter = SSEEmitter(
            redis_url="redis://custom:6380/1",
            channel="custom:channel",
            list_key="custom:list",
            history_size=100
        )

        assert emitter.redis_url == "redis://custom:6380/1"
        assert emitter.channel == "custom:channel"
        assert emitter.list_key == "custom:list"
        assert emitter.history_size == 100

    def test_emit_success(self, emitter, mock_redis):
        """Test successful event emission."""
        event = ChatMessageEvent(role="user", text="Test")
        result = emitter.emit(event)

        assert result is True
        assert mock_redis.pipeline.called
        assert mock_redis.publish.called
        assert mock_redis.lpush.called
        assert mock_redis.ltrim.called
        assert mock_redis.execute.called

    def test_emit_publishes_to_correct_channel(self, emitter, mock_redis):
        """Test event is published to correct Redis channel."""
        event = ChatMessageEvent(role="user", text="Test")
        emitter.emit(event)

        # Get the call arguments
        publish_call = mock_redis.publish.call_args
        assert publish_call is not None

        channel_arg = publish_call[0][0]
        assert channel_arg == "wf:events"

    def test_emit_serializes_event_correctly(self, emitter, mock_redis):
        """Test event is serialized to JSON correctly."""
        event = ChatMessageEvent(role="assistant", text="Hello", session_id="s1")
        emitter.emit(event)

        # Get the published data
        publish_call = mock_redis.publish.call_args
        json_data = publish_call[0][1]
        parsed = json.loads(json_data)

        assert parsed["kind"] == "chat_message"
        assert parsed["role"] == "assistant"
        assert parsed["text"] == "Hello"
        assert parsed["session_id"] == "s1"
        assert "ts" in parsed

    def test_emit_maintains_history_list(self, emitter, mock_redis):
        """Test event is added to history list and trimmed."""
        event = ChatMessageEvent(role="user", text="Test")
        emitter.emit(event)

        # Check lpush call
        lpush_call = mock_redis.lpush.call_args
        assert lpush_call is not None
        assert lpush_call[0][0] == "wf:events:recent"

        # Check ltrim call
        ltrim_call = mock_redis.ltrim.call_args
        assert ltrim_call is not None
        assert ltrim_call[0] == ("wf:events:recent", 0, 199)  # history_size - 1

    def test_emit_redis_unavailable(self):
        """Test emission when Redis is unavailable."""
        emitter = SSEEmitter(redis_url="redis://invalid:9999")
        event = ChatMessageEvent(role="user", text="Test")

        result = emitter.emit(event)
        assert result is False

    def test_emit_handles_redis_error(self, emitter, mock_redis):
        """Test emission handles Redis errors gracefully."""
        mock_redis.execute.side_effect = Exception("Redis error")
        event = ChatMessageEvent(role="user", text="Test")

        result = emitter.emit(event)
        assert result is False

    def test_emit_raw_event(self, emitter, mock_redis):
        """Test raw event emission."""
        result = emitter.emit_raw("custom_event", {"foo": "bar", "count": 42})

        assert result is True
        assert mock_redis.publish.called

        # Verify JSON structure
        publish_call = mock_redis.publish.call_args
        json_data = publish_call[0][1]
        parsed = json.loads(json_data)

        assert parsed["kind"] == "custom_event"
        assert parsed["foo"] == "bar"
        assert parsed["count"] == 42
        assert "ts" in parsed

    def test_emit_raw_redis_unavailable(self):
        """Test raw emission when Redis is unavailable."""
        emitter = SSEEmitter(redis_url="redis://invalid:9999")

        result = emitter.emit_raw("test", {"data": "value"})
        assert result is False

    def test_close_connection(self, emitter, mock_redis):
        """Test closing Redis connection."""
        emitter.close()

        assert mock_redis.close.called
        assert emitter._redis_client is None

    def test_close_handles_error(self, emitter, mock_redis):
        """Test close handles errors gracefully."""
        mock_redis.close.side_effect = Exception("Close error")

        emitter.close()  # Should not raise
        assert emitter._redis_client is None


class TestGlobalEmitter:
    """Test global emitter instance management."""

    def test_get_default_emitter_creates_instance(self):
        """Test get_default_emitter creates a singleton instance."""
        set_default_emitter(None)  # Reset

        emitter1 = get_default_emitter()
        emitter2 = get_default_emitter()

        assert emitter1 is emitter2
        assert isinstance(emitter1, SSEEmitter)

    def test_set_default_emitter(self):
        """Test setting custom default emitter."""
        custom_emitter = SSEEmitter(redis_url="redis://custom:6379")
        set_default_emitter(custom_emitter)

        retrieved = get_default_emitter()
        assert retrieved is custom_emitter

    def test_set_default_emitter_none(self):
        """Test resetting default emitter to None."""
        set_default_emitter(None)

        # Next call should create a new instance
        new_emitter = get_default_emitter()
        assert isinstance(new_emitter, SSEEmitter)


class TestConvenienceFunctions:
    """Test convenience emission functions."""

    @pytest.fixture(autouse=True)
    def setup_mock_emitter(self):
        """Set up a mock emitter for all tests."""
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis.pipeline.return_value = mock_redis
        mock_redis.execute.return_value = [1, 1, True]

        emitter = SSEEmitter()
        emitter._redis_client = mock_redis
        set_default_emitter(emitter)

        self.mock_redis = mock_redis

        yield

        set_default_emitter(None)

    def test_emit_chat_message_user(self):
        """Test emit_chat_message for user message."""
        result = emit_chat_message("user", "Hello", "sess-1")

        assert result is True

        # Verify correct event was published
        publish_call = self.mock_redis.publish.call_args
        json_data = publish_call[0][1]
        parsed = json.loads(json_data)

        assert parsed["kind"] == "chat_message"
        assert parsed["role"] == "user"
        assert parsed["text"] == "Hello"
        assert parsed["session_id"] == "sess-1"

    def test_emit_chat_message_assistant(self):
        """Test emit_chat_message for assistant message."""
        result = emit_chat_message("assistant", "Response")

        assert result is True

        publish_call = self.mock_redis.publish.call_args
        json_data = publish_call[0][1]
        parsed = json.loads(json_data)

        assert parsed["kind"] == "chat_message"
        assert parsed["role"] == "assistant"
        assert parsed["text"] == "Response"
        assert parsed["session_id"] is None

    def test_emit_job_started(self):
        """Test emit_job_started helper."""
        result = emit_job_started("job-1", "test_agent", "card-1")

        assert result is True

        publish_call = self.mock_redis.publish.call_args
        json_data = publish_call[0][1]
        parsed = json.loads(json_data)

        assert parsed["kind"] == "job_started"
        assert parsed["job_id"] == "job-1"
        assert parsed["action"] == "test_agent"
        assert parsed["card_id"] == "card-1"

    def test_emit_job_started_no_card(self):
        """Test emit_job_started without card_id."""
        result = emit_job_started("job-2", "agent")

        assert result is True

        publish_call = self.mock_redis.publish.call_args
        json_data = publish_call[0][1]
        parsed = json.loads(json_data)

        assert parsed["kind"] == "job_started"
        assert parsed["card_id"] is None

    def test_emit_job_succeeded(self):
        """Test emit_job_succeeded helper."""
        result = emit_job_succeeded("job-3", "agent", "card-2", {"result": "ok"})

        assert result is True

        publish_call = self.mock_redis.publish.call_args
        json_data = publish_call[0][1]
        parsed = json.loads(json_data)

        assert parsed["kind"] == "job_succeeded"
        assert parsed["job_id"] == "job-3"
        assert parsed["action"] == "agent"
        assert parsed["card_id"] == "card-2"
        assert parsed["data"] == {"result": "ok"}

    def test_emit_job_failed(self):
        """Test emit_job_failed helper."""
        result = emit_job_failed("job-4", "agent", "Error occurred", "card-3")

        assert result is True

        publish_call = self.mock_redis.publish.call_args
        json_data = publish_call[0][1]
        parsed = json.loads(json_data)

        assert parsed["kind"] == "job_failed"
        assert parsed["job_id"] == "job-4"
        assert parsed["error"] == "Error occurred"
        assert parsed["card_id"] == "card-3"

    def test_emit_board_update(self):
        """Test emit_board_update helper."""
        cards = [{"id": "c1", "title": "Test"}]
        result = emit_board_update(cards)

        assert result is True

        publish_call = self.mock_redis.publish.call_args
        json_data = publish_call[0][1]
        parsed = json.loads(json_data)

        assert parsed["kind"] == "board_update"
        assert parsed["cards"] == cards

    def test_emit_wip_limit_exceeded(self):
        """Test emit_wip_limit_exceeded helper."""
        result = emit_wip_limit_exceeded(
            "in_progress", "card-5", "Test Card", 2, 2
        )

        assert result is True

        publish_call = self.mock_redis.publish.call_args
        json_data = publish_call[0][1]
        parsed = json.loads(json_data)

        assert parsed["kind"] == "wip_limit_exceeded"
        assert parsed["column"] == "in_progress"
        assert parsed["card_id"] == "card-5"
        assert parsed["card_title"] == "Test Card"
        assert parsed["current_count"] == 2
        assert parsed["limit"] == 2


class TestSSEFormatters:
    """Test SSE formatting helper functions."""

    def test_format_sse_event_default(self):
        """Test format_sse_event with default event type."""
        data = {"kind": "test", "value": 123}
        formatted = format_sse_event(data)

        assert formatted.startswith("event: message\n")
        assert "data:" in formatted
        assert formatted.endswith("\n\n")

        # Extract and parse JSON
        lines = formatted.strip().split("\n")
        assert lines[0] == "event: message"
        assert lines[1].startswith("data: ")

        json_part = lines[1][6:]  # Remove "data: " prefix
        parsed = json.loads(json_part)
        assert parsed == {"kind": "test", "value": 123}

    def test_format_sse_event_custom_type(self):
        """Test format_sse_event with custom event type."""
        data = {"foo": "bar"}
        formatted = format_sse_event(data, event_type="custom")

        assert formatted.startswith("event: custom\n")
        assert "data:" in formatted

    def test_format_sse_event_complex_data(self):
        """Test format_sse_event with nested data."""
        data = {
            "kind": "test",
            "nested": {"a": 1, "b": [2, 3]},
            "list": [{"x": 1}, {"y": 2}]
        }
        formatted = format_sse_event(data)

        lines = formatted.strip().split("\n")
        json_part = lines[1][6:]
        parsed = json.loads(json_part)

        assert parsed == data

    def test_format_sse_comment(self):
        """Test format_sse_comment."""
        formatted = format_sse_comment("test comment")

        assert formatted == ": test comment\n\n"

    def test_format_sse_heartbeat(self):
        """Test format_sse_heartbeat."""
        formatted = format_sse_heartbeat()

        assert formatted == ": hb\n\n"

    def test_sse_event_protocol_compliance(self):
        """Test SSE events follow protocol specification."""
        # Event should have: event type, data, and double newline terminator
        data = {"test": "value"}
        formatted = format_sse_event(data, "myevent")

        # Must end with double newline
        assert formatted.endswith("\n\n")

        # Must have event and data fields
        assert "event: myevent\n" in formatted
        assert "data: " in formatted

        # Lines must be in correct order
        lines = formatted.split("\n")
        assert lines[0].startswith("event:")
        assert lines[1].startswith("data:")
        assert lines[2] == ""  # Empty line after data
        assert lines[3] == ""  # Second newline for termination
