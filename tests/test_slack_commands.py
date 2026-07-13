"""Unit tests for /firewall log parsing and Slack formatting."""
from datetime import datetime, timedelta, timezone

import pytest

import src.slack.commands as commands
from src.slack.commands import (
    ExportArgumentError,
    LogArgumentError,
    _format_log_entry,
    _log_entry_block,
    _parse_log_args,
    _parse_export_args,
)


NOW = datetime(2026, 7, 12, 15, 30, tzinfo=timezone.utc)


def test_parse_export_date_ranges():
    assert _parse_export_args(["--today"], now=NOW) == {
        "since": datetime(2026, 7, 12, tzinfo=timezone.utc),
        "until": datetime(2026, 7, 13, tzinfo=timezone.utc),
    }
    assert _parse_export_args(["--week"], now=NOW) == {
        "since": datetime(2026, 7, 6, tzinfo=timezone.utc),
        "until": datetime(2026, 7, 13, tzinfo=timezone.utc),
    }
    assert _parse_export_args(
        ["--since", "monday", "--until", "today"], now=NOW
    ) == {
        "since": datetime(2026, 7, 6, tzinfo=timezone.utc),
        "until": datetime(2026, 7, 13, tzinfo=timezone.utc),
    }


@pytest.mark.parametrize("args", [[], ["--week", "extra"], ["--since", "bad", "--until", "today"], ["--since", "2026-07-13", "--until", "2026-07-12"]])
def test_invalid_export_args(args):
    with pytest.raises(ExportArgumentError):
        _parse_export_args(args, now=NOW)


def test_parse_log_args_preserves_defaults():
    parsed = _parse_log_args([], now=NOW)
    assert parsed == {
        "decision": None,
        "agent_id": None,
        "score_above": None,
        "since": None,
        "limit": 10,
        "offset": 0,
    }


def test_parse_all_value_filters():
    parsed = _parse_log_args(
        [
            "--agent", "support-agent", "--decision", "BLOCK",
            "--score-above", "71.5", "--since", "24h",
            "--limit", "50", "--offset", "10",
        ],
        now=NOW,
    )
    assert parsed["agent_id"] == "support-agent"
    assert parsed["decision"] == "block"
    assert parsed["score_above"] == 71.5
    assert parsed["since"] == NOW - timedelta(hours=24)
    assert parsed["limit"] == 50
    assert parsed["offset"] == 10


def test_parse_today_and_iso_date():
    assert _parse_log_args(["--today"], now=NOW)["since"] == datetime(
        2026, 7, 12, tzinfo=timezone.utc
    )
    assert _parse_log_args(["--since", "2026-07-01"], now=NOW)["since"] == datetime(
        2026, 7, 1, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    "args",
    [
        ["--decision", "maybe"],
        ["--limit", "51"],
        ["--offset", "-1"],
        ["--score-above", "101"],
        ["--since", "yesterday-ish"],
        ["--today", "--week"],
        ["--agent"],
        ["--unknown", "value"],
    ],
)
def test_invalid_log_args_raise_clear_error(args):
    with pytest.raises(LogArgumentError):
        _parse_log_args(args, now=NOW)


def test_format_entry_has_marker_fields_and_short_counterfactual():
    row = {
        "id": "record-1",
        "timestamp": "2026-07-12T14:22:00+00:00",
        "agent_id": "support-agent",
        "tool_name": "http_post",
        "final_risk_score": 94,
        "decision": "block",
        "counterfactual": "847 customer emails would have been sent externally.",
    }
    text = _format_log_entry(row)
    assert "🔴" in text
    assert "support-agent" in text
    assert "http_post" in text
    assert "94/100" in text
    assert "BLOCK" in text
    assert "847 customer emails" in text


def test_format_entry_truncates_long_counterfactual():
    text = _format_log_entry({
        "decision": "hold",
        "counterfactual": "x" * 200,
    })
    assert "🟡" in text
    assert text.endswith("…_")


def test_log_block_has_details_button_with_record_id():
    block = _log_entry_block({"id": "abc-123", "decision": "allow"})
    assert block["type"] == "section"
    assert block["accessory"]["action_id"] == "view_audit_detail"
    assert block["accessory"]["value"] == "abc-123"
    assert "🟢" in block["text"]["text"]


async def test_handle_log_passes_parsed_filters_to_db(monkeypatch):
    captured = {}

    def fake_get_audit_records(**kwargs):
        captured.update(kwargs)
        return [{"id": "record-1", "decision": "allow"}]

    responses = []

    async def respond(**payload):
        responses.append(payload)

    monkeypatch.setattr(commands, "get_audit_records", fake_get_audit_records)
    await commands._handle_log(
        ["--decision", "allow", "--score-above", "20", "--limit", "5", "--offset", "10"],
        respond,
    )

    assert captured["decision"] == "allow"
    assert captured["score_above"] == 20
    assert captured["limit"] == 5
    assert captured["offset"] == 10
    assert responses[0]["blocks"][0]["accessory"]["action_id"] == "view_audit_detail"


async def test_handle_export_returns_small_json_in_code_block(monkeypatch):
    responses = []
    captured = {}

    async def respond(**payload):
        responses.append(payload)

    def fake_records(**time_range):
        captured.update(time_range)
        return [{"decision": "allow", "final_risk_score": 10}]

    monkeypatch.setattr(commands, "_parse_export_args", lambda _args: {
        "since": datetime(2026, 7, 12, tzinfo=timezone.utc),
        "until": datetime(2026, 7, 13, tzinfo=timezone.utc),
    })
    monkeypatch.setattr(commands, "get_audit_records_for_export", fake_records)
    await commands._handle_export(["--today"], respond)

    assert captured["since"] == datetime(2026, 7, 12, tzinfo=timezone.utc)
    assert captured["until"] == datetime(2026, 7, 13, tzinfo=timezone.utc)
    assert "```" in responses[0]["text"]
    assert '"total_calls": 1' in responses[0]["text"]
    assert "tool_input_tokenized" not in responses[0]["text"]


async def test_status_uses_summary_helpers_and_formats_metrics(monkeypatch):
    summary = {
        "total_today": 4,
        "total_week": 12,
        "total_all_time": 30,
        "active_holds": 3,
        "by_decision": {"allow": 20, "hold": 6, "block": 4},
        "average_final_risk_score": 42.5,
        "average_processing_time_ms": 81.25,
        "slowest_recent": {
            "agent_id": "agent-1",
            "tool_name": "http_post",
            "processing_time_ms": 240,
        },
        "most_recent_timestamp": "2026-07-12T15:00:00+00:00",
    }

    responses = []

    async def respond(**payload):
        responses.append(payload)

    monkeypatch.setattr(commands, "get_audit_summary", lambda: summary)
    monkeypatch.setattr(commands, "get_pending_holds", lambda: [{"id": "hold-1"}])
    monkeypatch.setattr(
        commands,
        "get_agent_baseline_summary",
        lambda: {"available": False, "agents": []},
    )
    monkeypatch.setattr(commands, "get_recent_audit_records_for_conspiracy", lambda: [])
    await commands._handle_status(respond)

    rendered = str(responses[0]["blocks"])
    assert "Calls today" in rendered and "4" in rendered
    assert "Awaiting admin action" in rendered and "3" in rendered
    assert "Allow: 20" in rendered and "Hold: 6" in rendered and "Block: 4" in rendered
    assert "42.5/100" in rendered and "81.2 ms" in rendered
    assert "http_post" in rendered and "240.0 ms" in rendered
    assert "Baseline data unavailable" in rendered


async def test_agent_status_routes_and_formats_health(monkeypatch):
    responses = []

    async def respond(**payload):
        responses.append(payload)

    monkeypatch.setattr(commands, "get_agent_health", lambda agent_id: {
        "agent_id": agent_id, "has_records": True,
        "baseline_available": True, "baseline_phase": "active",
        "total_call_count": 20, "calls_last_7_days": 8,
        "average_risk_score_7_days": 35, "highest_risk_score_7_days": 90,
        "most_common_tools": [("http_post", 5)],
        "most_common_trigger_sources": [("external_dm", 4)],
        "average_anomaly_score": 22, "hold_rate": 0.15, "block_rate": 0.05,
    })
    await commands._handle_status(respond, "support-agent-1")
    rendered = str(responses[0]["blocks"])
    assert "support-agent-1" in rendered
    assert "Calls in last 7 days" in rendered and "8" in rendered
    assert "35.0/100" in rendered and "90.0/100" in rendered
    assert "http_post" in rendered and "external_dm" in rendered
    assert "15.0%" in rendered and "5.0%" in rendered


async def test_agent_status_returns_useful_empty_message(monkeypatch):
    responses = []

    async def respond(**payload):
        responses.append(payload)

    monkeypatch.setattr(commands, "get_agent_health", lambda _agent_id: {
        "agent_id": "missing-agent", "has_records": False,
    })
    await commands._handle_status(respond, "missing-agent")
    assert "No audit records found" in responses[0]["text"]
