"""
Mock Omar pipeline — returns hardcoded scores so the full proxy flow works
on Day 1 before Omar's real AI pipeline exists.

Detection logic: simple keyword scan + tool name heuristics.
Omar will replace this with real AI. The interface (in/out) stays identical.
"""
import time
from src.models import ToolCallRequest, AnalysisResponse, AnomalySignals

INJECTION_KEYWORDS = [
    "ignore your previous instructions",
    "ignore previous instructions",
    "you are now",
    "maintenance mode",
    "developer mode",
    "forget your instructions",
    "new instructions",
    "disregard",
    "override",
]

DANGEROUS_TOOLS = {"http_post", "http_get", "external_request", "shell_exec", "file_delete"}
SAFE_TOOLS = {"database_read", "search", "message_send", "calendar_read"}


def analyze(request: ToolCallRequest) -> AnalysisResponse:
    start = time.time()

    message_lower = request.message_context.lower()

    # Injection detection
    injection_detected = any(kw in message_lower for kw in INJECTION_KEYWORDS)
    injection_score = 94.0 if injection_detected else 3.0

    # Source trust multiplier
    source_multipliers = {"internal": 0.9, "external_dm": 0.3, "unknown": 0.1}
    multiplier = source_multipliers.get(request.trigger_source, 0.5)
    injection_score = min(100.0, injection_score * (1 / multiplier) if injection_detected else injection_score)

    # Drift scoring — is tool consistent with safe operation?
    if request.tool_name in DANGEROUS_TOOLS and injection_detected:
        drift_score = 96.7
    elif request.tool_name in DANGEROUS_TOOLS:
        drift_score = 60.0
    elif request.tool_name in SAFE_TOOLS:
        drift_score = 8.0
    else:
        drift_score = 40.0

    # Anomaly (cold in mock — contribute 0)
    anomaly_score = 5.0

    # Risk combination (weights from docs)
    risk_score = (
        drift_score * 0.30
        + injection_score * 0.35
        + anomaly_score * 0.25
        + 0 * 0.10  # no threat intel in mock
    )

    # Override: injection > 90 always blocks
    if injection_score > 90:
        decision = "block"
    elif risk_score >= 71:
        decision = "block"
    elif risk_score >= 31:
        decision = "hold"
    else:
        decision = "allow"

    counterfactual = None
    if decision in ("block", "hold") and injection_detected:
        tool_input_str = str(request.tool_input)
        counterfactual = (
            f"The {request.agent_id} attempted to call {request.tool_name} "
            f"with input: {tool_input_str[:200]}. "
            f"Triggered by an external message containing prompt injection instructions "
            f'("{next(kw for kw in INJECTION_KEYWORDS if kw in message_lower)}" detected). '
            f"If not blocked, this action could expose sensitive data."
        )

    elapsed = int((time.time() - start) * 1000)

    return AnalysisResponse(
        risk_score=round(risk_score, 1),
        decision=decision,
        drift_score=drift_score,
        injection_score=round(injection_score, 1),
        injection_detected=injection_detected,
        suspicious_text=next((kw for kw in INJECTION_KEYWORDS if kw in message_lower), None),
        anomaly_score=anomaly_score,
        anomaly_signals=AnomalySignals(),
        threat_match=False,
        counterfactual=counterfactual,
        processing_time_ms=elapsed,
    )
