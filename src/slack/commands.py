"""
/firewall slash command handlers.
Usage:
  /firewall log [--decision allow|hold|block] [--agent NAME]
  /firewall status
  /firewall help
"""
from src.db.client import get_audit_records

DECISION_EMOJI = {"allow": "🟢", "hold": "🟡", "block": "🔴"}


def _format_log_entry(row: dict) -> str:
    emoji = DECISION_EMOJI.get(row.get("decision", ""), "⚪")
    ts = row.get("timestamp", "")[:16].replace("T", " ")
    return (
        f"{emoji} `{ts}` | *{row.get('agent_id', '?')}* | "
        f"`{row.get('tool_name', '?')}` | "
        f"{row.get('final_risk_score', 0):.0f}/100 | "
        f"{row.get('decision', '?').upper()}"
    )


async def handle_firewall_command(command: dict, say, client) -> None:
    text = (command.get("text") or "").strip()
    parts = text.split()
    subcommand = parts[0].lower() if parts else "help"

    if subcommand == "log":
        await _handle_log(parts[1:], command, client)
    elif subcommand == "status":
        await _handle_status(command, client)
    else:
        await _handle_help(command, client)


async def _handle_log(args: list[str], command: dict, client) -> None:
    decision_filter = None
    agent_filter = None

    i = 0
    while i < len(args):
        if args[i] == "--decision" and i + 1 < len(args):
            decision_filter = args[i + 1]
            i += 2
        elif args[i] == "--agent" and i + 1 < len(args):
            agent_filter = args[i + 1]
            i += 2
        else:
            i += 1

    records = get_audit_records(decision=decision_filter, agent_id=agent_filter, limit=10)

    if not records:
        await client.chat_postEphemeral(
            channel=command["channel_id"],
            user=command["user_id"],
            text="No audit log entries found matching your filters.",
        )
        return

    lines = [_format_log_entry(r) for r in records]
    header = "*Agent Firewall — Audit Log*"
    if decision_filter:
        header += f" (filter: {decision_filter})"

    await client.chat_postEphemeral(
        channel=command["channel_id"],
        user=command["user_id"],
        text=header,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": header}},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        ],
    )


async def _handle_status(command: dict, client) -> None:
    all_records = get_audit_records(limit=500)
    total = len(all_records)
    by_decision = {"allow": 0, "hold": 0, "block": 0}
    for r in all_records:
        d = r.get("decision", "")
        if d in by_decision:
            by_decision[d] += 1

    pending_holds = [r for r in all_records if r.get("decision") == "hold" and not r.get("admin_action")]

    status_text = (
        f"*Agent Firewall — System Status*\n\n"
        f"🟢 Allowed: {by_decision['allow']}  "
        f"🟡 Held: {by_decision['hold']}  "
        f"🔴 Blocked: {by_decision['block']}  "
        f"Total: {total}\n\n"
        f"*Pending holds awaiting review:* {len(pending_holds)}"
    )

    await client.chat_postEphemeral(
        channel=command["channel_id"],
        user=command["user_id"],
        text=status_text,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": status_text}}],
    )


async def _handle_help(command: dict, client) -> None:
    help_text = (
        "*Agent Firewall Commands*\n\n"
        "`/firewall log` — Show recent audit log entries\n"
        "`/firewall log --decision block` — Filter by decision (allow|hold|block)\n"
        "`/firewall log --agent support-agent-1` — Filter by agent name\n"
        "`/firewall status` — System health and summary stats\n"
        "`/firewall help` — Show this message"
    )
    await client.chat_postEphemeral(
        channel=command["channel_id"],
        user=command["user_id"],
        text=help_text,
    )
