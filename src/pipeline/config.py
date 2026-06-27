"""
Tunable constants for the reasoning engine. One place to adjust scoring so the
demo numbers and thresholds aren't scattered across modules.
"""
import os

# Claude model for all reasoning calls. docs.md picks sonnet-4-6 for pipeline
# speed (we need < 3s end-to-end). Override via env if a component needs more.
MODEL = os.getenv("PIPELINE_MODEL", "claude-sonnet-4-6")

# Timeouts. The proxy enforces a 4s hard cap and fail-safe BLOCKs on timeout,
# so the pipeline must finish comfortably under that.
PIPELINE_TIMEOUT_SECONDS = 3.5   # whole-pipeline soft budget
STAGE_TIMEOUT_SECONDS = 2.0      # per-component cap; exceeding it -> conservative default

# Risk weights (docs.md §Risk Combiner). Anomaly + threat are Phase 2/3 and are
# absent in Phase 1 — the combiner reweights across whatever signals are present,
# so a missing signal never silently drags the score down.
WEIGHTS = {
    "injection": 0.35,
    "drift": 0.30,
    "anomaly": 0.25,
    "threat": 0.10,
}

# Decision thresholds (docs.md §Decision thresholds).
ALLOW_MAX = 30.0   # 0–30  -> allow
HOLD_MAX = 70.0    # 31–70 -> hold ; 71–100 -> block

# Override: an unambiguous injection always BLOCKs regardless of the blended score.
INJECTION_OVERRIDE_THRESHOLD = 90.0

# Source trust (docs.md §Prompt Injection Detector, Layer 3). Lower trust ->
# we discount the injection signal less (external/unknown senders are the real
# attack surface); a trusted internal sender dampens it. confidence is divided
# by (trust + offset): internal /1.6, external_dm /1.0, unknown /0.8.
SOURCE_TRUST = {"internal": 0.9, "external_dm": 0.3, "unknown": 0.1}
TRUST_OFFSET = 0.7

# A regex signature hit is strong evidence on its own — floor the injection
# confidence so a known attack from an untrusted source clears the override.
REGEX_CONFIDENCE_FLOOR = 95.0

# Conservative defaults when a component fails or times out (graceful degradation).
DEFAULT_DRIFT_NO_INTENT = 50.0   # docs.md: missing intent object -> drift 50
