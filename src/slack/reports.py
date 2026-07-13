"""Scheduled Slack threat reports built from bounded audit-log windows."""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.db.client import get_audit_records_between
from src.slack.notifications import get_slack_client


def _average(records: list[dict], field: str) -> float | None:
    values = [float(row[field]) for row in records if row.get(field) is not None]
    return sum(values) / len(values) if values else None


def _change(current: float | int | None, previous: float | int | None) -> float:
    return round(float(current or 0) - float(previous or 0), 1)


def _signed(value: float | int) -> str:
    return f"{value:+g}"


def _brief(value: object, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_weekly_threat_report(
    current_week_records: list[dict],
    previous_week_records: list[dict],
) -> dict:
    """Build Slack-ready text and blocks without database or Slack I/O."""
    decisions = Counter(str(row.get("decision") or "").lower() for row in current_week_records)
    previous_decisions = Counter(
        str(row.get("decision") or "").lower() for row in previous_week_records
    )
    average_risk = _average(current_week_records, "final_risk_score")
    previous_average_risk = _average(previous_week_records, "final_risk_score")

    blocked = [row for row in current_week_records if str(row.get("decision", "")).lower() == "block"]
    blocked_lines = []
    for row in blocked[:8]:
        description = (
            row.get("counterfactual") or row.get("suspicious_text")
            or row.get("threat_pattern") or "No description available"
        )
        blocked_lines.append(
            f"• *{row.get('agent_id', 'unknown')}* — `{row.get('tool_name', 'unknown')}`: "
            f"{_brief(description)}"
        )

    suspicious = Counter(
        _brief(value, 80)
        for row in current_week_records
        for value in (row.get("suspicious_text"), row.get("threat_pattern"))
        if value
    )
    suspicious_text = ", ".join(
        f"{value} ({count})" for value, count in suspicious.most_common(5)
    ) or "No repeated suspicious indicators"

    anomaly_by_agent: dict[str, list[float]] = defaultdict(list)
    for row in current_week_records:
        if row.get("agent_id") and row.get("anomaly_score") is not None:
            anomaly_by_agent[str(row["agent_id"])].append(float(row["anomaly_score"]))
    top_agents = sorted(
        ((agent, sum(values) / len(values)) for agent, values in anomaly_by_agent.items()),
        key=lambda item: item[1],
        reverse=True,
    )[:5]
    agent_text = ", ".join(f"*{agent}* ({score:.1f})" for agent, score in top_agents) or "No anomaly data"

    total_change = len(current_week_records) - len(previous_week_records)
    block_change = decisions["block"] - previous_decisions["block"]
    risk_change = _change(average_risk, previous_average_risk)
    risk_text = f"{average_risk:.1f}/100" if average_risk is not None else "Unavailable"
    fallback = (
        f"Weekly Threat Report: {len(current_week_records)} calls, "
        f"{decisions['block']} blocked, average risk {risk_text}."
    )
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Agent Firewall — Weekly Threat Report"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Calls intercepted*\n{len(current_week_records)}"},
            {"type": "mrkdwn", "text": f"*Average risk*\n{risk_text}"},
            {"type": "mrkdwn", "text": f"*:large_green_circle: Allow*\n{decisions['allow']}"},
            {"type": "mrkdwn", "text": f"*:large_yellow_circle: Hold*\n{decisions['hold']}"},
            {"type": "mrkdwn", "text": f"*:red_circle: Block*\n{decisions['block']}"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            "*Change from prior week*\n"
            f"Calls: {_signed(total_change)} | Blocks: {_signed(block_change)} | "
            f"Average risk: {_signed(risk_change)} points"
        )}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            "*Top suspicious indicators*\n" + suspicious_text
        )}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            "*Highest average anomaly scores*\n" + agent_text
        )}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            "*Blocked calls*\n" + ("\n".join(blocked_lines) or "No blocked calls last week")
        )}},
    ]
    return {
        "text": fallback,
        "blocks": blocks,
        "summary": {
            "total_calls": len(current_week_records),
            "allow_count": decisions["allow"],
            "hold_count": decisions["hold"],
            "block_count": decisions["block"],
            "average_risk_score": average_risk,
            "total_call_change": total_change,
            "block_count_change": block_change,
            "average_risk_score_change": risk_change,
        },
    }


async def post_weekly_threat_report() -> None:
    """Query the two completed weeks and post the report to Slack."""
    channel = os.getenv("SECURITY_CHANNEL_ID")
    if not channel or not os.getenv("SLACK_BOT_TOKEN"):
        print("[WARN] Weekly threat report skipped: Slack configuration missing")
        return
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("[WARN] Weekly threat report skipped: Supabase configuration missing")
        return
    try:
        report_timezone = ZoneInfo(os.getenv("FIREWALL_REPORT_TIMEZONE", "America/Toronto"))
        now = datetime.now(report_timezone)
        this_monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        last_week_start = this_monday - timedelta(days=7)
        previous_week_start = last_week_start - timedelta(days=7)
        current_records = get_audit_records_between(
            last_week_start.astimezone(timezone.utc), this_monday.astimezone(timezone.utc)
        )
        previous_records = get_audit_records_between(
            previous_week_start.astimezone(timezone.utc), last_week_start.astimezone(timezone.utc)
        )
        report = build_weekly_threat_report(current_records, previous_records)
        await get_slack_client().chat_postMessage(
            channel=channel, text=report["text"], blocks=report["blocks"]
        )
    except Exception as exc:
        print(f"[WARN] Weekly threat report failed: {exc}")
