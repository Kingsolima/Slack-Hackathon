"""
HTTP client for Omar's AI analysis pipeline.
Falls back to mock if USE_MOCK_PIPELINE=true or if Omar's endpoint is unreachable.
"""
import os
import time
import httpx
from src.models import ToolCallRequest, AnalysisResponse

OMAR_URL = os.getenv("OMAR_PIPELINE_URL", "http://localhost:8001")
USE_MOCK = os.getenv("USE_MOCK_PIPELINE", "true").lower() == "true"
TIMEOUT_SECONDS = 4.0
RETRY_TIMEOUT_SECONDS = 2.0


async def analyze(request: ToolCallRequest) -> AnalysisResponse:
    if USE_MOCK:
        from src.mock.pipeline import analyze as mock_analyze
        return mock_analyze(request)

    payload = request.model_dump(mode="json")

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        try:
            resp = await client.post(f"{OMAR_URL}/analyze", json=payload)
            resp.raise_for_status()
            return AnalysisResponse(**resp.json())
        except (httpx.TimeoutException, httpx.RequestError):
            # Retry once with shorter timeout
            try:
                async with httpx.AsyncClient(timeout=RETRY_TIMEOUT_SECONDS) as retry_client:
                    resp = await retry_client.post(f"{OMAR_URL}/analyze", json=payload)
                    resp.raise_for_status()
                    return AnalysisResponse(**resp.json())
            except Exception:
                # Fail-safe: if Omar is down, BLOCK — never default to allow
                return _fail_safe_block(request)
        except Exception:
            return _fail_safe_block(request)


def _fail_safe_block(request: ToolCallRequest) -> AnalysisResponse:
    """Analysis pipeline unreachable — default to block. A broken brain is a red flag."""
    return AnalysisResponse(
        risk_score=100.0,
        decision="block",
        drift_score=100.0,
        injection_score=100.0,
        injection_detected=False,
        anomaly_score=100.0,
        counterfactual=(
            f"Analysis pipeline unreachable. Tool call {request.tool_name} blocked as a precaution. "
            "Investigate pipeline health before resuming agent operations."
        ),
        processing_time_ms=0,
    )
