import uuid
from unittest.mock import MagicMock, patch


@patch("app.session_store.get_supabase")
def test_save_message_inserts(mock_sb):
    from app.session_store import save_message
    sb = MagicMock()
    mock_sb.return_value = sb
    save_message(str(uuid.uuid4()), "user", "Hello")
    sb.table.assert_called_with("messages")
    sb.table().insert.assert_called_once()
    call_args = sb.table().insert.call_args[0][0]
    assert call_args["role"] == "user"
    assert call_args["content"] == "Hello"


@patch("app.session_store.get_supabase")
def test_get_history_returns_chronological(mock_sb):
    from app.session_store import get_history
    sb = MagicMock()
    mock_sb.return_value = sb
    # Simulate Supabase returning newest-first
    sb.table().select().eq().order().limit().execute.return_value.data = [
        {"role": "assistant", "content": "B"},
        {"role": "user", "content": "A"},
    ]
    result = get_history(str(uuid.uuid4()), limit=5)
    assert result == [{"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}]


@patch("app.session_store.get_supabase")
def test_get_history_returns_empty_on_error(mock_sb):
    from app.session_store import get_history
    sb = MagicMock()
    mock_sb.return_value = sb
    sb.table().select().eq().order().limit().execute.side_effect = Exception("table missing")
    result = get_history(str(uuid.uuid4()), limit=5)
    assert result == []


@patch("app.session_store.get_supabase")
def test_trim_messages_deletes_old(mock_sb):
    from app.session_store import trim_messages
    sb = MagicMock()
    mock_sb.return_value = sb
    sb.table().select().eq().order().range().execute.return_value.data = [
        {"id": "old-1"}, {"id": "old-2"}
    ]
    trim_messages(str(uuid.uuid4()), keep=50)
    sb.table().delete.assert_called_once()
