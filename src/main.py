"""
Agent Firewall — MCP Proxy Server
FastAPI app: /intercept endpoint + Slack Bolt handlers.
Health check has zero dependencies — always responds 200.
"""
import os
import uuid
import asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.models import ToolCallRequest, InterceptDecision
from src.proxy.omar_client import analyze
from src.db.client import write_audit_record
from src.slack.notifications import send_admin_alert


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Agent Firewall proxy starting...")
    yield
    print("Agent Firewall proxy shutting down.")


api = FastAPI(title="Agent Firewall Proxy", lifespan=lifespan)


@api.get("/health")
async def health():
    """No dependencies — always returns 200 so Railway healthcheck passes."""
    return {"status": "ok", "service": "agent-firewall-proxy"}


@api.post("/intercept", response_model=InterceptDecision)
async def intercept_tool_call(request: ToolCallRequest):
    record_id = str(uuid.uuid4())
    analysis = await analyze(request)
    asyncio.create_task(_write_and_notify(request, analysis, record_id))

    decision_messages = {
        "allow": "Tool call approved.",
        "hold": "Tool call suspended pending admin review. Admin has been notified.",
        "block": "Tool call blocked. Admin has been notified.",
    }
    return InterceptDecision(
        decision=analysis.decision,
        risk_score=analysis.risk_score,
        hold_id=record_id if analysis.decision == "hold" else None,
        counterfactual=analysis.counterfactual,
        message=decision_messages[analysis.decision],
    )


async def _write_and_notify(request, analysis, record_id):
    try:
        write_audit_record(request, analysis, record_id)
    except Exception as e:
        print(f"[DB ERROR] {e}")
    if analysis.decision in ("hold", "block"):
        try:
            await send_admin_alert(request, analysis, record_id)
        except Exception as e:
            print(f"[SLACK ERROR] {e}")


@api.post("/slack/events")
async def slack_events(req: Request):
    body = await req.json()
    # Slack sends this to verify the URL — must respond with challenge value immediately
    if body.get("type") == "url_verification":
        return JSONResponse({"challenge": body["challenge"]})
    from src.slack.bolt_app import get_handler
    return await get_handler().handle(req)


@api.post("/slack/interactions")
async def slack_interactions(req: Request):
    from src.slack.bolt_app import get_handler
    return await get_handler().handle(req)


@api.post("/slack/commands")
async def slack_commands(req: Request):
    from src.slack.bolt_app import get_handler
    return await get_handler().handle(req)
