"""End-to-end generation pipeline. Each stage only reads what earlier stages produced."""

from __future__ import annotations

from orchestrator.config import GeneratorConfig
from orchestrator.generator.context import GenContext
from orchestrator.generator.master import build_master


def generate(cfg: GeneratorConfig) -> GenContext:
    ctx = GenContext(cfg)
    build_master(ctx)          # stage 1: organization, role templates, workforce
    # stage 2: onboarding/offboarding transactions   (Step 3)
    # stage 3: labelled anomaly injection           (Step 4)
    # stage 4: derived values                        (Step 4)
    events, _ref_map = ctx.audit.finalize()
    ctx.tables["audit_event"] = events
    return ctx
