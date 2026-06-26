"""
Agent Firewall — MCP Proxy Server
Entry point: FastAPI app serving both the intercept API and Slack Bolt handlers.
"""
import os
import uuid
import asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

from src.models import ToolCallRequest, InterceptDecision
from src.proxy.omar_client import analyze
from src.db.client import write_audit_record
from src.slack.notifications import send_admin_alert
from src.slack.bolt_app import app as bolt_app

handler = AsyncSlackRequestHandler(bolt_app)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Agent Firewall proxy starting...")
    yield
    print("Agent Firewall proxy shutting down.")


api = FastAPI(title="Agent Firewall Proxy", lifespan=lifespan)


@api.post("/intercept", response_model=InterceptDecision)
async def intercept_tool_call(request: ToolCallRequest):
    """
    Core interception endpoint.
    The Slack agent calls this before executing any tool.
    Returns allow / hold / block with the risk score.
    """
    record_id = str(uuid.uuid4())

    analysis = await analyze(request)

    # Write to audit log (fire and forget — don't block the response on DB write)
    asyncio.create_task(_write_and_notify(request, analysis, record_id))

    hold_id = record_id if analysis.decision == "hold" else None

    decision_messages = {
        "allow": "Tool call approved.",
        "hold": "Tool call suspended pending admin review. Admin has been notified.",
        "block": "Tool call blocked. Admin has been notified.",
    }

    return InterceptDecision(
        decision=analysis.decision,
        risk_score=analysis.risk_score,
        hold_id=hold_id,
        counterfactual=analysis.counterfactual,
        message=decision_messages[analysis.decision],
    )


async def _write_and_notify(request, analysis, record_id):
    try:
        write_audit_record(request, analysis, record_id)
    except Exception as e:
        print(f"[DB ERROR] Failed to write audit record: {e}")

    if analysis.decision in ("hold", "block"):
        try:
            await send_admin_alert(request, analysis, record_id)
        except Exception as e:
            print(f"[SLACK ERROR] Failed to send admin alert: {e}")


@api.get("/health")
async def health():
    return {"status": "ok", "service": "agent-firewall-proxy"}


# Slack Bolt endpoints
@api.post("/slack/events")
async def slack_events(req: Request):
    return await handler.handle(req)


@api.post("/slack/interactions")
async def slack_interactions(req: Request):
    return await handler.handle(req)


@api.post("/slack/commands")
async def slack_commands(req: Request):
    return await handler.handle(req)
