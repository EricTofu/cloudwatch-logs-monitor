from unittest.mock import MagicMock, patch

import pytest

from log_monitor.context import _context_cache, get_context_lines


@pytest.fixture(autouse=True)
def clear_cache():
    _context_cache.clear()


@patch("log_monitor.context.get_logs_client")
def test_get_context_lines_exact_match(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    # Setup mock responses
    mock_client.get_log_events.side_effect = [
        # resp_before
        {
            "events": [
                {"timestamp": 100, "message": "msg 100"},
                {"timestamp": 101, "message": "msg 101"},
                {"timestamp": 102, "message": "msg 102"},
                {"timestamp": 103, "message": "msg 103"},
                {"timestamp": 104, "message": "msg 104"},  # target
            ]
        },
        # resp_after
        {
            "events": [
                {"timestamp": 104, "message": "msg 104"},  # target
                {"timestamp": 105, "message": "msg 105"},
                {"timestamp": 106, "message": "msg 106"},
                {"timestamp": 107, "message": "msg 107"},
                {"timestamp": 108, "message": "msg 108"},
            ]
        },
    ]

    # Request num_lines=2 around timestamp=104
    result = get_context_lines("test_group", "test_stream", 104, num_lines=2)

    # Expecting 2 before (102, 103), target (104), 2 after (105, 106)
    assert result == [
        "[1970-01-01T09:00:00.102+09:00] msg 102",
        "[1970-01-01T09:00:00.103+09:00] msg 103",
        "[1970-01-01T09:00:00.104+09:00] msg 104",
        "[1970-01-01T09:00:00.105+09:00] msg 105",
        "[1970-01-01T09:00:00.106+09:00] msg 106",
    ]


@patch("log_monitor.context.get_logs_client")
def test_get_context_lines_multiple_targets(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    # Setup mock responses
    mock_client.get_log_events.side_effect = [
        # resp_before
        {
            "events": [
                {"timestamp": 100, "message": "msg 100"},
                {"timestamp": 104, "message": "msg 104-a"},  # target 1
                {"timestamp": 104, "message": "msg 104-b"},  # target 2
            ]
        },
        # resp_after
        {
            "events": [
                {"timestamp": 104, "message": "msg 104-a"},  # target 1
                {"timestamp": 104, "message": "msg 104-b"},  # target 2
                {"timestamp": 105, "message": "msg 105"},
                {"timestamp": 106, "message": "msg 106"},
            ]
        },
    ]

    # Request num_lines=1 around timestamp=104
    # Request num_lines=1 around timestamp=104
    result = get_context_lines("test_group", "test_stream", 104, num_lines=1)

    # Expecting 1 before (100), targets (104-a, 104-b), 1 after (105)
    assert result == [
        "[1970-01-01T09:00:00.100+09:00] msg 100",
        "[1970-01-01T09:00:00.104+09:00] msg 104-a",
        "[1970-01-01T09:00:00.104+09:00] msg 104-b",
        "[1970-01-01T09:00:00.105+09:00] msg 105",
    ]

    @patch("log_monitor.context.get_logs_client")
    def test_get_context_lines_fallback(mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Setup mock responses with no event at exactly 104
        mock_client.get_log_events.side_effect = [
            # resp_before
            {
                "events": [
                    {"timestamp": 100, "message": "msg 100"},
                    {"timestamp": 101, "message": "msg 101"},
                ]
            },
            # resp_after
            {
                "events": [
                    {"timestamp": 105, "message": "msg 105"},
                    {"timestamp": 106, "message": "msg 106"},
                ]
            },
        ]

        # Request num_lines=1 around timestamp=104
        result = get_context_lines("test_group", "test_stream", 104, num_lines=1)

        # Closest is 105. Expecting 1 before (101), target (105), 1 after (106)
        assert result == [
            "[1970-01-01T09:00:00.101+09:00] msg 101",
            "[1970-01-01T09:00:00.105+09:00] msg 105",
            "[1970-01-01T09:00:00.106+09:00] msg 106",
        ]


@patch("log_monitor.context.get_logs_client")
def test_get_context_lines_fallback(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    # Setup mock responses with no event at exactly 104
    mock_client.get_log_events.side_effect = [
        # resp_before
        {
            "events": [
                {"timestamp": 100, "message": "msg 100"},
                {"timestamp": 101, "message": "msg 101"},
            ]
        },
        # resp_after
        {
            "events": [
                {"timestamp": 105, "message": "msg 105"},
                {"timestamp": 106, "message": "msg 106"},
            ]
        },
    ]

    # Request num_lines=1 around timestamp=104
    result = get_context_lines("test_group", "test_stream", 104, num_lines=1)

    # Closest is 105. Expecting 1 before (101), target (105), 1 after (106)
    assert result == [
        "[1970-01-01T09:00:00.101+09:00] msg 101",
        "[1970-01-01T09:00:00.105+09:00] msg 105",
        "[1970-01-01T09:00:00.106+09:00] msg 106",
    ]
