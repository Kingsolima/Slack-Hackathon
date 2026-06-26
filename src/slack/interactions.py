"""
Approve / Deny button handlers for HOLD decisions.
"""
from src.db.client import update_admin_action, get_audit_record_by_id


async def handle_approve(body: dict, ack, client) -> None:
    await ack()
    action = body["actions"][0]
    record_id = action["value"]
    admin_user = body["user"]["id"]

    update_admin_action(record_id, "approved", admin_user)

    record = get_audit_record_by_id(record_id)
    agent = record.get("agent_id", "unknown") if record else "unknown"
    tool = record.get("tool_name", "unknown") if record else "unknown"

    await client.chat_postMessage(
        channel=admin_user,
        text=f"✅ Approved — `{tool}` call from *{agent}* will execute. Audit log updated.",
    )


async def handle_deny(body: dict, ack, client) -> None:
    await ack()
    action = body["actions"][0]
    record_id = action["value"]
    admin_user = body["user"]["id"]

    update_admin_action(record_id, "denied", admin_user)

    record = get_audit_record_by_id(record_id)
    agent = record.get("agent_id", "unknown") if record else "unknown"
    tool = record.get("tool_name", "unknown") if record else "unknown"

    await client.chat_postMessage(
        channel=admin_user,
        text=f"❌ Denied — `{tool}` call from *{agent}* permanently cancelled. Audit log updated.",
    )
