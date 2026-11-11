"""Stage 2d - equipment inventory and assignments.

Allocation is simulated chronologically so inventory always reconciles:
  * A request is filled from in-stock assets of the same type/class (returned assets re-enter
    stock 3 days after return); otherwise a new asset is purchased 5-30 days before assignment.
  * New hire: assigned when ONB_EQUIPMENT_SHIP starts, delivered when it completes, acknowledged
    when ONB_EQUIPMENT_ACK completes.  Existing staff: issued around their start date.
  * Exit: recovered when OFF_RECOVER_EQUIPMENT completes; a small share is reported LOST.
  * Withdrawn offer: anything already shipped is returned 2-7 days later. If that return date is
    after the snapshot, the assignment stays open (pending return) - returns are never fabricated.
  * Purchase dates never precede 2017-01-01 (start of the HRIS baseline).
  * Laptops older than 5 years are RETIRED on return instead of re-entering stock.
  * Remaining inventory up to the configured asset count are spare IN_STOCK units.
  * One asset is never assigned to two people at once (the anomaly stage breaks this deliberately).
"""

from __future__ import annotations

from datetime import date, timedelta

from orchestrator import ids, reference as ref
from orchestrator.generator.cases import ONE_MIN, ts
from orchestrator.generator.context import GenContext, rand_date

REFURBISH_DAYS = 3
EARLIEST_PURCHASE = date(2017, 1, 1)
RETIRE_AFTER_DAYS = 5 * 365


def _model(rng, asset_type: str, cls: str | None) -> tuple[str, float]:
    catalog = ref.ASSET_MODELS[asset_type]
    return rng.choice(catalog[cls] if isinstance(catalog, dict) else catalog)


def build_assets(ctx: GenContext) -> None:
    rng, as_of = ctx.rng("assets"), ctx.as_of
    sc = ctx.cfg.scenarios
    t, audit = ctx.tables, ctx.audit
    itops = ctx.plan["partners"]["ITOPS"]

    # ---- 1. collect assignment requests with their (snapshot-visible) lifecycle dates
    requests = []
    for emp_id, p in ctx.plan["people"].items():
        kit = ctx.plan["equipment"][emp_id]
        if not kit:
            continue
        onb = ctx.plan["onb"].get(emp_id)
        off = ctx.plan["off"].get(emp_id)
        if p["cohort"] == "EXISTING_STAFF":
            assigned, delivered = p["start_date"] - timedelta(days=2), p["start_date"] - timedelta(days=1)
            ack, onb_case, it = p["start_date"], None, rng.choice(itops)
            assigned_at, delivered_at, ack_at = ts(rng, assigned, 10), ts(rng, delivered, 13), ts(rng, ack, 15)
        else:
            s = onb["sched"]
            assigned, delivered, ack = s.started("ONB_EQUIPMENT_SHIP"), s.done("ONB_EQUIPMENT_SHIP"), s.done("ONB_EQUIPMENT_ACK")
            onb_case, it = onb["case_id"], onb["it"]
            # assigned when shipping starts; delivered / acknowledged just before those tasks close
            assigned_at = s.started_ts["ONB_EQUIPMENT_SHIP"] + ONE_MIN if assigned else None
            delivered_at = s.completed_ts["ONB_EQUIPMENT_SHIP"] - ONE_MIN if delivered else None
            ack_at = s.completed_ts["ONB_EQUIPMENT_ACK"] - ONE_MIN if ack else None
        if assigned is None:
            continue
        ret_date, ret_at, off_case = None, None, None
        if p["withdrawn_on"]:
            when = p["withdrawn_on"] + timedelta(days=rng.randint(2, 7))
            if when <= as_of:  # otherwise still pending return at the snapshot
                ret_date, ret_at = when, ts(rng, when, 12)
        if off:
            off_case = off["case_id"]
            ret_date = off["sched"].done("OFF_RECOVER_EQUIPMENT")
            ret_at = off["sched"].completed_ts["OFF_RECOVER_EQUIPMENT"] - ONE_MIN if ret_date else None
        for i, (atype, cls) in enumerate(kit):
            lost = bool(off) and ret_date is not None and rng.random() < sc["asset_lost_rate"]
            requests.append({
                "emp_id": emp_id, "i": i, "type": atype, "cls": cls, "assigned": assigned, "delivered": delivered,
                "ack": ack, "returned": None if lost else ret_date, "lost": ret_date if lost else None,
                "onb_case": onb_case, "off_case": off_case, "it": it,
                "assigned_at": assigned_at, "delivered_at": delivered_at, "ack_at": ack_at, "ret_at": ret_at,
            })
    requests.sort(key=lambda r: (r["assigned"], r["emp_id"], r["i"]))

    # ---- 2. chronological allocation
    pool: list[dict] = []          # in-stock assets: {asset, available_from}
    serials: set[str] = set()

    def purchase(atype, cls, on: date) -> dict:
        aid = ids.asset_id(ctx.counter("asset").next())
        model, cost = _model(rng, atype, cls)
        serial = f"SN{rng.randrange(16 ** 10):010X}"
        while serial in serials:
            serial = f"SN{rng.randrange(16 ** 10):010X}"
        serials.add(serial)
        on = max(on, EARLIEST_PURCHASE)
        a = {"asset_id": aid, "asset_type": atype, "model": model, "serial_number": serial,
             "purchase_date": on, "purchase_cost": cost, "asset_status": None, "_cls": cls}
        t["asset"].append(a)
        audit.add(ts(rng, on, 8), "asset", aid, "ASSET_PURCHASED", "SYSTEM", to_state="IN_STOCK", reason="Procurement")
        return a

    for r in requests:
        candidates = [e for e in pool if e["asset"]["asset_type"] == r["type"] and e["asset"]["_cls"] == r["cls"]
                      and e["available_from"] <= r["assigned"]]
        if candidates:
            entry = min(candidates, key=lambda e: e["asset"]["asset_id"])
            pool.remove(entry)
            asset = entry["asset"]
        else:
            asset = purchase(r["type"], r["cls"], r["assigned"] - timedelta(days=rng.randint(5, 30)))

        asg_id = ids.assignment_id(ctx.counter("assignment").next())
        case = r["onb_case"]
        t["asset_assignment"].append({
            "assignment_id": asg_id, "asset_id": asset["asset_id"], "employee_id": r["emp_id"],
            "onboarding_case_id": case, "offboarding_case_id": r["off_case"], "assigned_date": r["assigned"],
            "delivered_date": r["delivered"], "acknowledged_date": r["ack"], "returned_date": r["returned"],
            "lost_reported_date": r["lost"], "assignment_status": None,
        })
        audit.add(r["assigned_at"], "asset", asset["asset_id"], "ASSET_ASSIGNED", "IT_OPS_PARTNER",
                  actor_id=r["it"], from_state="IN_STOCK", to_state="ASSIGNED", related_case_id=case,
                  reason=f"Issued to {r['emp_id']} ({asg_id})")
        if r["delivered"]:
            audit.add(r["delivered_at"], "asset_assignment", asg_id, "ASSET_DELIVERED", "IT_OPS_PARTNER",
                      actor_id=r["it"], from_state="ASSIGNED", to_state="DELIVERED", related_case_id=case)
        if r["ack"]:
            audit.add(r["ack_at"], "asset_assignment", asg_id, "ASSET_ACKNOWLEDGED", "EMPLOYEE",
                      actor_id=r["emp_id"], from_state="DELIVERED", to_state="ACKNOWLEDGED", related_case_id=case)
        back_case = r["off_case"] or case
        if r["lost"]:
            audit.add(r["ret_at"], "asset", asset["asset_id"], "ASSET_LOST", "IT_OPS_PARTNER",
                      actor_id=r["it"], from_state="ASSIGNED", to_state="LOST", related_case_id=back_case,
                      reason=f"Not returned by {r['emp_id']} ({asg_id})")
        elif r["returned"]:
            audit.add(r["ret_at"], "asset", asset["asset_id"], "ASSET_RETURNED", "IT_OPS_PARTNER",
                      actor_id=r["it"], from_state="ASSIGNED", to_state="IN_STOCK", related_case_id=back_case,
                      reason=f"Recovered from {r['emp_id']} ({asg_id})")
            back_in_stock = r["returned"] + timedelta(days=REFURBISH_DAYS)
            if r["type"] == "LAPTOP" and (r["returned"] - asset["purchase_date"]).days > RETIRE_AFTER_DAYS:
                if back_in_stock <= as_of:
                    audit.add(ts(rng, back_in_stock, 17), "asset", asset["asset_id"], "ASSET_RETIRED", "IT_OPS_PARTNER",
                              actor_id=r["it"], from_state="IN_STOCK", to_state="RETIRED",
                              reason="End of life (> 5 years)")
            else:
                pool.append({"asset": asset, "available_from": back_in_stock})

    # ---- 3. spare stock up to the configured inventory size
    weights = {"LAPTOP": 0.6, "MONITOR": 0.2, "PHONE": 0.1, "SECURITY_KEY": 0.1}
    while len(t["asset"]) < ctx.cfg.assets:
        atype = rng.choices(list(weights), weights=list(weights.values()))[0]
        cls = rng.choice(["STANDARD", "ENGINEERING"]) if atype == "LAPTOP" else None
        purchase(atype, cls, rand_date(rng, date(2026, 1, 5), as_of - timedelta(days=7)))

    for a in t["asset"]:
        a.pop("_cls", None)
