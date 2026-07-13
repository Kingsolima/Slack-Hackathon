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
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from src.models import ToolCallRequest, InterceptDecision
from src.proxy.omar_client import analyze
from src.db.client import write_audit_record
from src.slack.notifications import send_admin_alert
from src.slack.reports import post_weekly_threat_report


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Agent Firewall proxy starting...")
    try:
        from src.slack.bolt_app import get_handler
        get_handler()
        print("Slack Bolt initialized OK")
    except Exception as e:
        print(f"[WARN] Slack Bolt init skipped: {e}")
    scheduler = None
    try:
        missing_report_config = [
            name for name in (
                "SLACK_BOT_TOKEN",
                "SECURITY_CHANNEL_ID",
                "SUPABASE_URL",
                "SUPABASE_SERVICE_ROLE_KEY",
            )
            if not os.getenv(name)
        ]
        if missing_report_config:
            print(
                "[WARN] Weekly threat report configuration incomplete; "
                "scheduled runs will skip until configured: "
                + ", ".join(missing_report_config)
            )
        report_timezone = ZoneInfo(
            os.getenv("FIREWALL_REPORT_TIMEZONE", "America/Toronto")
        )
        scheduler = AsyncIOScheduler(timezone=report_timezone)
        scheduler.add_job(
            post_weekly_threat_report,
            CronTrigger(
                day_of_week="mon", hour=9, minute=0, timezone=report_timezone
            ),
            id="weekly_threat_report",
            replace_existing=True,
        )
        scheduler.start()
        print(f"Weekly threat report scheduled for Mondays at 09:00 {report_timezone}")
    except Exception as e:
        print(f"[WARN] Weekly threat report scheduler skipped: {e}")
    yield
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception as e:
            print(f"[WARN] Weekly threat report scheduler shutdown failed: {e}")
    print("Agent Firewall proxy shutting down.")


api = FastAPI(title="Agent Firewall Proxy", lifespan=lifespan)


@api.get("/health")
async def health():
    """No dependencies — always returns 200 so Railway healthcheck passes."""
    return {"status": "ok", "service": "agent-firewall-proxy"}


@api.get("/debug/env")
async def debug_env():
    """Shows which env vars the live process can see (no values, just presence)."""
    return {
        "SLACK_BOT_TOKEN": bool(os.environ.get("SLACK_BOT_TOKEN")),
        "SLACK_SIGNING_SECRET": bool(os.environ.get("SLACK_SIGNING_SECRET")),
        "SUPABASE_URL": bool(os.environ.get("SUPABASE_URL")),
        "SUPABASE_SERVICE_ROLE_KEY": bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY")),
        "OMAR_PIPELINE_URL": os.environ.get("OMAR_PIPELINE_URL"),
        "PORT": os.environ.get("PORT"),
    }


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
    try:
        from src.slack.bolt_app import get_handler
        return await get_handler().handle(req)
    except Exception as e:
        print(f"[SLACK INTERACTIONS ERROR] {e}")
        return JSONResponse(status_code=200, content={})


@api.post("/slack/commands")
async def slack_commands(req: Request):
    try:
        from src.slack.bolt_app import get_handler
        return await get_handler().handle(req)
    except Exception as e:
        print(f"[SLACK COMMANDS ERROR] {e}")
        return JSONResponse(status_code=200, content={"text": f"Internal error: {e}"})
