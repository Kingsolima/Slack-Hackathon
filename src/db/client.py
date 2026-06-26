"""
Supabase client — audit logging and hold state management.
Uses service role key for full table access (never expose this client-side).
"""
import os
import json
from datetime import datetime
from typing import Optional
from supabase import create_client, Client
from src.models import AuditRecord, AnalysisResponse, ToolCallRequest

_client: Optional[Client] = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        _client = create_client(url, key)
    return _client


def write_audit_record(
    request: ToolCallRequest,
    analysis: AnalysisResponse,
    record_id: str,
) -> None:
    db = get_client()
    row = {
        "id": record_id,
        "timestamp": datetime.utcnow().isoformat(),
        "session_id": request.session_id,
        "agent_id": request.agent_id,
        "workspace_id": request.workspace_id,
        "tool_name": request.tool_name,
        "tool_input_tokenized": json.dumps(request.tool_input),
        "trigger_source": request.trigger_source,
        "trigger_user_id": request.trigger_user_id,
        "drift_score": analysis.drift_score,
        "injection_score": analysis.injection_score,
        "injection_detected": analysis.injection_detected,
        "suspicious_text": analysis.suspicious_text,
        "anomaly_score": analysis.anomaly_score,
        "anomaly_signals": analysis.anomaly_signals.model_dump(),
        "threat_match": analysis.threat_match,
        "threat_pattern": analysis.threat_pattern,
        "final_risk_score": analysis.risk_score,
        "decision": analysis.decision,
        "counterfactual": analysis.counterfactual,
        "tokens_used": analysis.tokens_used,
        "processing_time_ms": analysis.processing_time_ms,
    }
    db.table("audit_log").insert(row).execute()


def update_admin_action(
    record_id: str,
    action: str,  # approved | denied
    admin_user_id: str,
) -> None:
    db = get_client()
    db.table("audit_log").update({
        "admin_action": action,
        "admin_user_id": admin_user_id,
        "admin_action_timestamp": datetime.utcnow().isoformat(),
    }).eq("id", record_id).execute()


def get_audit_records(
    decision: Optional[str] = None,
    agent_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    db = get_client()
    query = db.table("audit_log").select("*").order("timestamp", desc=True).limit(limit).offset(offset)
    if decision:
        query = query.eq("decision", decision)
    if agent_id:
        query = query.eq("agent_id", agent_id)
    result = query.execute()
    return result.data or []


def get_audit_record_by_id(record_id: str) -> Optional[dict]:
    db = get_client()
    result = db.table("audit_log").select("*").eq("id", record_id).single().execute()
    return result.data
