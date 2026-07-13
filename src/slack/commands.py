"""
/firewall slash command handlers.
Uses `respond` (response_url) instead of chat.postEphemeral — no channel
membership required, works wherever the slash command is invoked.

Usage:
  /firewall log [--decision allow|hold|block] [--agent NAME]
                [--score-above NUMBER] [--since DATE|RELATIVE]
                [--today|--week] [--limit 1..50] [--offset NUMBER]
  /firewall status
  /firewall help
"""
import json
import re
from datetime import datetime, timedelta, timezone

from src.db.client import (
    get_agent_baseline_summary,
    get_agent_health,
    get_audit_records,
    get_audit_records_for_export,
    get_audit_summary,
    get_pending_holds,
    get_recent_audit_records_for_conspiracy,
)
from src.visibility.conspiracy import detect_conspiracies
from src.visibility.compliance import build_compliance_export

DECISION_EMOJI = {"allow": "🟢", "hold": "🟡", "block": "🔴"}
VALID_DECISIONS = frozenset(DECISION_EMOJI)
DEFAULT_LOG_LIMIT = 10
MAX_LOG_LIMIT = 50
COUNTERFACTUAL_MAX_LENGTH = 140


class LogArgumentError(ValueError):
    """Raised when /firewall log receives invalid filter arguments."""


class ExportArgumentError(ValueError):
    """Raised when /firewall export receives an invalid time range."""


def _parse_export_date(value: str, now: datetime) -> datetime:
    """Parse an export date keyword or YYYY-MM-DD value at UTC midnight."""
    lowered = value.lower()
    today = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if lowered == "today":
        return today
    if lowered == "monday":
        return today - timedelta(days=today.weekday())
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ExportArgumentError(
            "Dates must use YYYY-MM-DD, `monday`, or `today`."
        ) from exc


def _parse_export_args(args: list[str], now: datetime | None = None) -> dict:
    """Parse export flags into a UTC half-open [since, until) range."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    today = current.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if args == ["--today"]:
        return {"since": today, "until": today + timedelta(days=1)}
    if args == ["--week"]:
        return {
            "since": today - timedelta(days=today.weekday()),
            "until": today + timedelta(days=1),
        }
    if len(args) != 4 or set(args[::2]) != {"--since", "--until"}:
        raise ExportArgumentError(
            "Use `--today`, `--week`, or `--since DATE --until DATE`."
        )
    values = dict(zip(args[::2], args[1::2]))
    since = _parse_export_date(values["--since"], current)
    until_day = _parse_export_date(values["--until"], current)
    until = until_day + timedelta(days=1)
    if since >= until:
        raise ExportArgumentError("The --since date must not be after --until.")
    return {"since": since, "until": until}


def _parse_since(value: str, now: datetime | None = None) -> datetime:
    """Parse an ISO date/time or a compact relative duration such as 24h/7d."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    relative = re.fullmatch(r"(\d+)([mhdw])", value.lower())
    if relative:
        amount = int(relative.group(1))
        if amount <= 0:
            raise LogArgumentError("Relative time must be greater than zero.")
        units = {
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
        }
        return current - units[relative.group(2)]

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LogArgumentError(
            "Invalid --since value. Use an ISO date/time or relative value like 24h or 7d."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_log_args(args: list[str], now: datetime | None = None) -> dict:
    """Parse and validate /firewall log flags into DB-helper keyword arguments."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    options = {
        "decision": None,
        "agent_id": None,
        "score_above": None,
        "since": None,
        "limit": DEFAULT_LOG_LIMIT,
        "offset": 0,
    }
    time_filter_seen = False
    value_flags = {
        "--agent", "--decision", "--score-above", "--since", "--limit", "--offset"
    }

    i = 0
    while i < len(args):
        flag = args[i]
        if flag in ("--today", "--week"):
            if time_filter_seen:
                raise LogArgumentError("Use only one of --since, --today, or --week.")
            time_filter_seen = True
            if flag == "--today":
                options["since"] = current.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                options["since"] = current - timedelta(days=7)
            i += 1
            continue

        if flag not in value_flags:
            raise LogArgumentError(f"Unknown log option: {flag}")
        if i + 1 >= len(args) or args[i + 1].startswith("--"):
            raise LogArgumentError(f"Missing value for {flag}.")
        value = args[i + 1]

        if flag == "--agent":
            options["agent_id"] = value
        elif flag == "--decision":
            decision = value.lower()
            if decision not in VALID_DECISIONS:
                raise LogArgumentError("--decision must be allow, hold, or block.")
            options["decision"] = decision
        elif flag == "--score-above":
            try:
                score = float(value)
            except ValueError as exc:
                raise LogArgumentError("--score-above must be a number from 0 to 100.") from exc
            if not 0 <= score <= 100:
                raise LogArgumentError("--score-above must be a number from 0 to 100.")
            options["score_above"] = score
        elif flag == "--since":
            if time_filter_seen:
                raise LogArgumentError("Use only one of --since, --today, or --week.")
            time_filter_seen = True
            options["since"] = _parse_since(value, current)
        elif flag == "--limit":
            try:
                limit = int(value)
            except ValueError as exc:
                raise LogArgumentError("--limit must be an integer from 1 to 50.") from exc
            if not 1 <= limit <= MAX_LOG_LIMIT:
                raise LogArgumentError("--limit must be an integer from 1 to 50.")
            options["limit"] = limit
        elif flag == "--offset":
            try:
                offset = int(value)
            except ValueError as exc:
                raise LogArgumentError("--offset must be a non-negative integer.") from exc
            if offset < 0:
                raise LogArgumentError("--offset must be a non-negative integer.")
            options["offset"] = offset
        i += 2

    return options


def _format_log_entry(row: dict) -> str:
    emoji = DECISION_EMOJI.get(row.get("decision", ""), "⚪")
    ts = row.get("timestamp", "")[:16].replace("T", " ")
    entry = (
        f"{emoji} `{ts}` | *{row.get('agent_id', '?')}* | "
        f"`{row.get('tool_name', '?')}` | "
        f"{row.get('final_risk_score', 0):.0f}/100 | "
        f"{row.get('decision', '?').upper()}"
    )
    counterfactual = " ".join((row.get("counterfactual") or "").split())
    if counterfactual:
        if len(counterfactual) > COUNTERFACTUAL_MAX_LENGTH:
            counterfactual = counterfactual[: COUNTERFACTUAL_MAX_LENGTH - 1].rstrip() + "…"
        entry += f"\n_{counterfactual}_"
    return entry


def _log_entry_block(row: dict, *, include_header: bool = False) -> dict:
    text = _format_log_entry(row)
    if include_header:
        text = f"*Agent Firewall — Audit Log*\n{text}"
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text},
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": "Details"},
            "action_id": "view_audit_detail",
            "value": str(row.get("id", "")),
        },
    }


async def handle_firewall_command(command: dict, respond, client) -> None:
    text = (command.get("text") or "").strip()
    parts = text.split()
    subcommand = parts[0].lower() if parts else "help"

    if subcommand == "log":
        await _handle_log(parts[1:], respond)
    elif subcommand == "status":
        await _handle_status(respond, " ".join(parts[1:]).strip() or None)
    elif subcommand == "export":
        await _handle_export(parts[1:], respond)
    else:
        await _handle_help(respond)


async def _handle_log(args: list, respond) -> None:
    try:
        filters = _parse_log_args(args)
    except LogArgumentError as e:
        await respond(text=f"❌ {e}\nUse `/firewall help` for usage.")
        return

    try:
        records = get_audit_records(**filters)
    except Exception as e:
        await respond(text=f"❌ DB error: {e}")
        return

    if not records:
        await respond(text="No audit log entries found matching your filters.")
        return

    header = "*Agent Firewall — Audit Log*"
    summary_parts = []
    if filters["decision"]:
        summary_parts.append(f"decision: {filters['decision']}")
    if filters["agent_id"]:
        summary_parts.append(f"agent: {filters['agent_id']}")
    if filters["offset"]:
        summary_parts.append(f"offset: {filters['offset']}")
    if summary_parts:
        header += f" ({', '.join(summary_parts)})"

    blocks = [_log_entry_block(row, include_header=index == 0) for index, row in enumerate(records)]
    if header != "*Agent Firewall — Audit Log*":
        blocks[0]["text"]["text"] = blocks[0]["text"]["text"].replace(
            "*Agent Firewall — Audit Log*", header, 1
        )

    await respond(
        text=header,
        blocks=blocks,
    )


async def _handle_export(args: list[str], respond) -> None:
    try:
        time_range = _parse_export_args(args)
    except ExportArgumentError as exc:
        await respond(text=f"❌ {exc}\nUse `/firewall help` for usage.")
        return
    try:
        records = get_audit_records_for_export(**time_range)
        export = build_compliance_export(records, **time_range)
    except Exception as exc:
        await respond(text=f"❌ DB error: {exc}")
        return

    rendered = json.dumps(export, indent=2, ensure_ascii=False)
    if len(rendered) <= 2800:
        safe_rendered = rendered.replace("```", "` ` `")
        await respond(text=f"*Agent Firewall — Compliance Export*\n```{safe_rendered}```")
        return

    summary = export["summary"]
    await respond(text=(
        "*Agent Firewall — Compliance Export*\n"
        "The full JSON is too large to display safely in Slack; no local file was written.\n"
        f"*Range:* {export['time_range']['since']} to {export['time_range']['until']} (exclusive)\n"
        f"*Total calls:* {summary['total_calls']} | *Allow:* {summary['allow_count']} | "
        f"*Hold:* {summary['hold_count']} | *Block:* {summary['block_count']}\n"
        f"*Average risk:* {summary['average_risk_score']} | "
        f"*Average processing:* {summary['average_processing_time_ms']} ms"
    ))


async def _handle_status(respond, agent_id: str | None = None) -> None:
    if agent_id:
        try:
            health = get_agent_health(agent_id)
        except Exception as e:
            await respond(text=f"DB error: {e}")
            return
        if not health.get("has_records"):
            await respond(
                text=f"No audit records found for agent `{agent_id}`.",
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": (
                    f"*Agent Health - {agent_id}*\n"
                    "No audit records were found for this agent. Check the agent name and try again."
                )}}],
            )
            return
        await respond(
            text=f"Agent Firewall - Health - {agent_id}",
            blocks=_format_agent_health_blocks(health),
        )
        return

    try:
        summary = get_audit_summary()
        pending_holds = get_pending_holds()
        baselines = get_agent_baseline_summary()
    except Exception as e:
        await respond(text=f"DB error: {e}")
        return

    try:
        conspiracy_alerts = detect_conspiracies(
            get_recent_audit_records_for_conspiracy()
        )
    except Exception:
        conspiracy_alerts = []

    await respond(
        text="Agent Firewall - System Status",
        blocks=_format_status_blocks(
            summary, pending_holds, baselines, conspiracy_alerts
        ),
    )


def _format_agent_health_blocks(health: dict) -> list[dict]:
    """Format calculated per-agent health data as Slack Block Kit."""
    def score(value) -> str:
        return f"{float(value):.1f}/100" if value is not None else "Unavailable"

    def ranked(items: list[tuple]) -> str:
        return ", ".join(f"`{name}` ({count})" for name, count in items) or "No data"

    baseline = health.get("baseline_phase") if health.get("baseline_available") else None
    baseline_text = baseline or "Baseline data unavailable"
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "Agent Firewall - Agent Health"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Agent*\n`{health.get('agent_id', 'unknown')}`"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Baseline phase*\n{baseline_text}"},
            {"type": "mrkdwn", "text": f"*Total calls*\n{health.get('total_call_count', 0)}"},
            {"type": "mrkdwn", "text": f"*Calls in last 7 days*\n{health.get('calls_last_7_days', 0)}"},
            {"type": "mrkdwn", "text": f"*Average risk (7 days)*\n{score(health.get('average_risk_score_7_days'))}"},
            {"type": "mrkdwn", "text": f"*Highest risk (7 days)*\n{score(health.get('highest_risk_score_7_days'))}"},
            {"type": "mrkdwn", "text": f"*Average anomaly (7 days)*\n{score(health.get('average_anomaly_score'))}"},
            {"type": "mrkdwn", "text": f"*Hold rate*\n{health.get('hold_rate', 0):.1%}"},
            {"type": "mrkdwn", "text": f"*Block rate*\n{health.get('block_rate', 0):.1%}"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"*Most common tools (7 days)*\n{ranked(health.get('most_common_tools', []))}"
        )}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"*Most common trigger sources (7 days)*\n{ranked(health.get('most_common_trigger_sources', []))}"
        )}},
    ]


def _format_status_blocks(
    summary: dict,
    pending_holds: list[dict],
    baselines: dict,
    conspiracy_alerts: list[dict] | None = None,
) -> list[dict]:
    """Build the Slack Block Kit status view from DB-helper results."""
    decisions = summary.get("by_decision", {})
    average_risk = summary.get("average_final_risk_score")
    average_latency = summary.get("average_processing_time_ms")
    latest = summary.get("most_recent_timestamp") or "No audit events"
    slowest = summary.get("slowest_recent")
    slowest_text = "Unavailable"
    if slowest:
        slowest_text = (
            f"`{slowest.get('tool_name', 'unknown')}` by *{slowest.get('agent_id', 'unknown')}* "
            f"({float(slowest.get('processing_time_ms', 0)):.1f} ms)"
        )

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Agent Firewall - System Status"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Calls today*\n{summary.get('total_today', 0)}"},
            {"type": "mrkdwn", "text": f"*Calls this week*\n{summary.get('total_week', 0)}"},
            {"type": "mrkdwn", "text": f"*Calls all time*\n{summary.get('total_all_time', 0)}"},
            {"type": "mrkdwn", "text": f"*Awaiting admin action*\n{summary.get('active_holds', len(pending_holds))}"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            "*Decision breakdown (all time)*\n"
            f":large_green_circle: Allow: {decisions.get('allow', 0)}   "
            f":large_yellow_circle: Hold: {decisions.get('hold', 0)}   "
            f":red_circle: Block: {decisions.get('block', 0)}"
        )}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Average risk score*\n{average_risk:.1f}/100" if average_risk is not None else "*Average risk score*\nUnavailable"},
            {"type": "mrkdwn", "text": f"*Average processing time*\n{average_latency:.1f} ms" if average_latency is not None else "*Average processing time*\nUnavailable"},
            {"type": "mrkdwn", "text": f"*Slowest recent record*\n{slowest_text}"},
            {"type": "mrkdwn", "text": f"*Most recent audit event*\n{latest}"},
        ]},
    ]

    if pending_holds:
        hold_lines = [
            f"• *{row.get('agent_id', 'unknown')}* - `{row.get('tool_name', 'unknown')}` - "
            f"{float(row.get('final_risk_score') or 0):.0f}/100 - {row.get('timestamp', 'unknown time')}"
            for row in pending_holds[:10]
        ]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Active holds*\n" + "\n".join(hold_lines)}})

    if not baselines.get("available"):
        baseline_text = "*Agent baselines*\nBaseline data unavailable"
    else:
        agents = baselines.get("agents", [])
        baseline_text = "*Agent baselines*\n" + ("\n".join(
            f"• *{row.get('agent_id', 'unknown')}*: {row.get('baseline_phase', 'unknown')} "
            f"({row.get('call_count', 0)} calls)" for row in agents[:10]
        ) or "No baseline records found")
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": baseline_text}})
    if conspiracy_alerts:
        alert_lines = [
            f"• *{alert['source_agent']}* → *{alert['target_agent']}* "
            f"({float(alert['conspiracy_score']):.1f}/100) — {alert['reason']}"
            for alert in conspiracy_alerts[:5]
        ]
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Cross-agent alerts*\n" + "\n".join(alert_lines),
            },
        })
    return blocks


async def _handle_help(respond) -> None:
    help_text = (
        "*Agent Firewall Commands*\n\n"
        "`/firewall log` — Show recent audit log entries\n"
        "`/firewall log --decision block` — Filter by decision (allow|hold|block)\n"
        "`/firewall log --agent support-agent-1` — Filter by agent name\n"
        "`/firewall log --score-above 70` — Filter by minimum risk score\n"
        "`/firewall log --since 24h` — ISO date/time or relative time (`30m`, `24h`, `7d`)\n"
        "`/firewall log --today` / `--week` — Recent time filters\n"
        "`/firewall log --limit 25 --offset 25` — Paginate results (maximum 50)\n"
        "`/firewall status` — System health and summary stats\n"
        "`/firewall status support-agent-1` — Per-agent health dashboard\n"
        "`/firewall export --today` / `--week` — Compliance JSON export\n"
        "`/firewall export --since monday --until today` — Export a date range\n"
        "`/firewall help` — Show this message"
    )
    await respond(text=help_text)
