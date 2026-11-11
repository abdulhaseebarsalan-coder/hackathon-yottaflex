"""End-to-end generation pipeline. Each stage only reads what earlier stages produced."""

from __future__ import annotations

from orchestrator.config import GeneratorConfig
from orchestrator.generator.access import build_access
from orchestrator.generator.assets import build_assets
from orchestrator.generator.cases import build_cases
from orchestrator.generator.context import GenContext
from orchestrator.generator.derive import derive_all
from orchestrator.generator.lifecycle import plan_lifecycle
from orchestrator.generator.master import build_master


def generate(cfg: GeneratorConfig) -> GenContext:
    ctx = GenContext(cfg)
    build_master(ctx)          # 1  organization, role templates, workforce, hires & start dates
    plan_lifecycle(ctx)        # 2a exits, role changes, requested access, equipment kits
    build_cases(ctx)           # 2b onboarding / offboarding cases, tasks, dependencies
    build_access(ctx)          # 2c access grants: approval-gated provisioning, revocation
    build_assets(ctx)          # 2d inventory, assignments, acknowledgement, recovery
    # 3  labelled anomaly injection                (Step 4)
    _finalize_audit(ctx)
    derive_all(ctx)            # 4  derived statuses, totals, dates (always last)
    return ctx


def _finalize_audit(ctx: GenContext) -> None:
    events, ref_map = ctx.audit.finalize()
    ctx.tables["audit_event"] = events
    for g in ctx.tables["access_grant"]:
        if g["override_event_id"]:
            g["override_event_id"] = ref_map[g["override_event_id"]]
