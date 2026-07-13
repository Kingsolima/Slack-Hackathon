"""Pure weekly threat report calculation and Block Kit tests."""
from src.slack.reports import build_weekly_threat_report


def test_build_weekly_threat_report_calculates_metrics_and_comparison():
    current = [
        {
            "agent_id": "agent-a", "tool_name": "db_read", "decision": "allow",
            "final_risk_score": 20, "anomaly_score": 10,
            "suspicious_text": "customer data access",
        },
        {
            "agent_id": "agent-b", "tool_name": "email_send", "decision": "block",
            "final_risk_score": 90, "anomaly_score": 80,
            "threat_pattern": "bulk exfiltration",
            "counterfactual": "Customer data would have been emailed externally.",
        },
        {
            "agent_id": "agent-b", "tool_name": "http_post", "decision": "hold",
            "final_risk_score": 70, "anomaly_score": 60,
            "threat_pattern": "bulk exfiltration",
        },
    ]
    previous = [
        {"decision": "allow", "final_risk_score": 10},
        {"decision": "allow", "final_risk_score": 30},
    ]

    report = build_weekly_threat_report(current, previous)

    assert report["summary"]["total_calls"] == 3
    assert report["summary"]["allow_count"] == 1
    assert report["summary"]["hold_count"] == 1
    assert report["summary"]["block_count"] == 1
    assert report["summary"]["average_risk_score"] == 60
    assert report["summary"]["total_call_change"] == 1
    assert report["summary"]["block_count_change"] == 1
    assert report["summary"]["average_risk_score_change"] == 40
    rendered = str(report["blocks"])
    assert "Customer data would have been emailed externally" in rendered
    assert "bulk exfiltration (2)" in rendered
    assert "agent-b" in rendered and "70.0" in rendered
    assert "Calls: +1" in rendered and "Blocks: +1" in rendered


def test_build_weekly_threat_report_handles_empty_records():
    report = build_weekly_threat_report([], [])

    assert report["summary"]["total_calls"] == 0
    assert report["summary"]["average_risk_score"] is None
    rendered = str(report["blocks"])
    assert "No blocked calls last week" in rendered
    assert "No anomaly data" in rendered
