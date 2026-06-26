"""
Slack Bolt async app — events, slash commands, interactions.
"""
import os
from slack_bolt.async_app import AsyncApp
from src.slack.commands import handle_firewall_command
from src.slack.interactions import handle_approve, handle_deny

app = AsyncApp(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
)


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
