"""
Slack Bolt async app — lazy initialized so missing env vars don't crash the server.
The health check endpoint works even if Slack credentials aren't configured yet.
"""
import os
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

_app: AsyncApp | None = None
_handler: AsyncSlackRequestHandler | None = None


def get_handler() -> AsyncSlackRequestHandler:
    global _app, _handler
    if _handler is None:
        token = os.environ.get("SLACK_BOT_TOKEN")
        secret = os.environ.get("SLACK_SIGNING_SECRET")
        if not token or not secret:
            raise RuntimeError("SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET must be set")

        _app = AsyncApp(token=token, signing_secret=secret)
        _register_handlers(_app)
        _handler = AsyncSlackRequestHandler(_app)
    return _handler


def _register_handlers(app: AsyncApp) -> None:
    from src.slack.commands import handle_firewall_command
    from src.slack.interactions import handle_approve, handle_deny

    @app.command("/firewall")
    async def firewall_command(ack, body, say, client):
        await ack()
        await handle_firewall_command(body, say, client)

    @app.action("approve_hold")
    async def approve_hold(body, ack, client):
        await handle_approve(body, ack, client)

    @app.action("deny_hold")
    async def deny_hold(body, ack, client):
        await handle_deny(body, ack, client)

    @app.event("app_mention")
    async def handle_mention(body, say):
        await say("Agent Firewall is active. Use `/firewall help` for commands.")
