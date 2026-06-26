"""
Admin DM notifications via Slack Block Kit.
Fires when a call is HELD or BLOCKED.
"""
import os
from typing import Optional
from slack_sdk.web.async_client import AsyncWebClient
from src.models import ToolCallRequest, AnalysisResponse

_slack_client: Optional[AsyncWebClient] = None


def get_slack_client() -> AsyncWebClient:
    global _slack_client
    if _slack_client is None:
        _slack_client = AsyncWebClient(token=os.environ["SLACK_BOT_TOKEN"])
    return _slack_client


def _risk_emoji(score: float) -> str:
    if score >= 71:
        return "🔴"
    if score >= 31:
        return "🟡"
    return "🟢"


async def send_admin_alert(
    request: ToolCallRequest,
    analysis: AnalysisResponse,
    record_id: str,
) -> None:
    admin_id = os.getenv("SLACK_ADMIN_USER_ID")
    if not admin_id:
        return

    client = get_slack_client()
    emoji = _risk_emoji(analysis.risk_score)
    decision_label = analysis.decision.upper()

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} Agent Firewall — {decision_label}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Risk Score:*\n{analysis.risk_score}/100"},
                {"type": "mrkdwn", "text": f"*Agent:*\n{request.agent_id}"},
                {"type": "mrkdwn", "text": f"*Tool:*\n`{request.tool_name}`"},
                {"type": "mrkdwn", "text": f"*Source:*\n{request.trigger_source}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*What would have happened:*\n{analysis.counterfactual or '_No details available_'}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Drift Score:*\n{analysis.drift_score}/100"},
                {"type": "mrkdwn", "text": f"*Injection Score:*\n{analysis.injection_score}/100"},
                {"type": "mrkdwn", "text": f"*Anomaly Score:*\n{analysis.anomaly_score}/100"},
                {"type": "mrkdwn", "text": f"*Threat Match:*\n{'Yes ⚠️' if analysis.threat_match else 'No'}"},
            ],
        },
    ]

    # Only show approve/deny for HOLD — BLOCK is final
    if analysis.decision == "hold":
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve"},
                    "style": "primary",
                    "action_id": "approve_hold",
                    "value": record_id,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Approve this action?"},
                        "text": {"type": "mrkdwn", "text": "The agent will execute the tool call."},
                        "confirm": {"type": "plain_text", "text": "Yes, approve"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Deny"},
                    "style": "danger",
                    "action_id": "deny_hold",
                    "value": record_id,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Deny this action?"},
                        "text": {"type": "mrkdwn", "text": "The tool call will be permanently cancelled."},
                        "confirm": {"type": "plain_text", "text": "Yes, deny"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
            ],
        })

    await client.chat_postMessage(
        channel=admin_id,
        text=f"{emoji} Agent Firewall {decision_label}: {request.tool_name} — Risk {analysis.risk_score}/100",
        blocks=blocks,
    )
