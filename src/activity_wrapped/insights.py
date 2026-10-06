"""The training report: numbers, charts' data, findings and recommendations.

Each finding states what was measured, what it suggests and what to do,
with the evidence attached, so a recommendation is never a black box.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np

from . import training as tr
from .forecast import plan_projection, trend_projection
from .planner import Context, PlanRequest, build_plan
from .stats import fmt_clock, local_start, personal_bests

STATUS_ORDER = {"warning": 0, "watch": 1, "good": 2, "info": 3}


def finding(id_: str, status: str, title: str, detail: str, advice: str | None = None, **evidence) -> dict:
    return {"id": id_, "status": status, "title": title, "detail": detail, "advice": advice,
            "evidence": evidence}


def _pct(a: float, b: float) -> float | None:
    return 100 * (a - b) / b if b else None


def _recent(acts: list[dict], now: datetime, days: int) -> list[dict]:
    since = now - timedelta(days=days)
    return [a for a in acts if (local_start(a) or since) > since]


def run_efforts(details: dict[int, dict]) -> list[dict]:
    """Scanned best efforts, flattened, each with the run's date."""
    out = []
    for d in details.values():
        for e in d.get("best_efforts") or []:
            out.append({**e, "start_date_local": d.get("start_date_local"), "activity_id": d.get("id")})
    return out


def context(activities: list[dict], details: dict[int, dict], group: str, now: datetime) -> dict:
    """Thresholds and fitness state shared by the report and the planner."""
    acts = [a for a in activities if tr.in_group(a, group)]
    hr_max = tr.estimate_hr_max(activities)
    runs = [a for a in activities if tr.group_of(a) == "run"]
    rides = [a for a in activities if tr.group_of(a) == "ride"]
    cs = tr.critical_speed(runs, run_efforts(details), now)
    ftp = tr.ftp_estimate(rides, now)
    kw = {"cs": cs.cs if cs else None, "ftp": ftp, "hr_max": hr_max}
    first = min((local_start(a).date() for a in acts if local_start(a)), default=now.date())
    loads = tr.daily_loads(acts, max(first, now.date() - timedelta(days=730)), now.date(), **kw)
    fit = tr.fitness_series(loads)
    return {"acts": acts, "hr_max": hr_max, "cs": cs, "ftp": ftp, "kw": kw, "fitness": fit}


def current_performance(points: list[tuple[datetime, float]], proxy: str, now: datetime) -> float | None:
    recent = [v for dt, v in points if dt >= now - timedelta(days=90)]
    if not recent:
        return None
    return min(recent) if tr.lower_is_better(proxy) else max(recent)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def report(activities: list[dict], details: dict[int, dict], group: str, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    ctx = context(activities, details, group, now)
    acts, kw, fit = ctx["acts"], ctx["kw"], ctx["fitness"]
    out: dict = {"group": group, "label": tr.GROUP_LABELS.get(group, group), "count": len(acts)}
    if len(acts) < 5:
        out["empty"] = True
        return out

    # --- thresholds -----------------------------------------------------------
    cs = ctx["cs"]
    out["thresholds"] = {
        "hr_max": round(ctx["hr_max"]) if ctx["hr_max"] else None,
        "critical_speed": {"pace": cs.pace, "d_prime": round(cs.d_prime), "points": cs.points,
                           "r2": round(cs.r2, 3)} if cs else None,
        "ftp": round(ctx["ftp"]) if ctx["ftp"] else None,
    }
    if cs and group in ("run", "all"):
        out["zones"] = tr.pace_zones(cs.cs)

    # --- fitness, fatigue, form -------------------------------------------------
    keep = 180
    out["fitness"] = {
        "dates": [d.isoformat() for d in fit["dates"][-keep:]],
        "fitness": [round(v, 1) for v in fit["fitness"][-keep:]],
        "fatigue": [round(v, 1) for v in fit["fatigue"][-keep:]],
        "form": [round(v, 1) for v in fit["form"][-keep:]],
        "acwr": [round(v, 2) if v is not None else None for v in fit["acwr"][-keep:]],
    }
    ctl, atl, tsb = fit["fitness"][-1], fit["fatigue"][-1], fit["form"][-1]
    acwr = fit["acwr"][-1]

    # --- volume -------------------------------------------------------------------
    weeks = tr.weekly_volume(acts, 26, now, **kw)
    out["weekly"] = [{"week": w["week"], "distance_km": round(w["distance"] / 1000, 1),
                      "hours": round(w["time"] / 3600, 2), "count": w["count"], "load": round(w["load"])}
                     for w in weeks]
    complete = weeks[:-1]
    last4 = complete[-4:]
    prev4 = complete[-8:-4]
    vol_last = np.mean([w["time"] for w in last4]) / 3600
    vol_prev = np.mean([w["time"] for w in prev4]) / 3600 if prev4 else 0
    active_weeks = sum(1 for w in complete[-12:] if w["count"] > 0)

    # --- performance ----------------------------------------------------------------
    perf = None
    findings: list[dict] = []
    if group != "all":
        proxy, points = tr.performance_points(acts, group)
        series = tr.monthly_best(points, proxy) if proxy else []
        if series:
            label, unit, direction = tr.PROXY[proxy]
            t = tr.trend(series)
            current = current_performance(points, proxy, now)
            perf = {"proxy": proxy, "label": label, "unit": unit, "direction": direction,
                    "series": [{"month": s["month"], "value": round(s["value"], 2),
                                "display": tr.fmt_proxy(s["value"], proxy)} for s in series],
                    "trend": t, "current": current,
                    "current_display": tr.fmt_proxy(current, proxy) if current else None}
            if t:
                lo, hi = t["ci_pct"]
                better = -1 if tr.lower_is_better(proxy) else 1
                if better * lo > 0 and better * hi > 0:
                    findings.append(finding(
                        "trend", "good", f"{label} is improving",
                        f"Over the last {t['months']} months your monthly best has improved by about "
                        f"{abs(t['pct_per_month']):.1f}% a month (95% interval "
                        f"{min(abs(lo), abs(hi)):.1f}\u2013{max(abs(lo), abs(hi)):.1f}%).",
                        "What you are doing is working. Keep the structure and progress load gradually.",
                        pct_per_month=t["pct_per_month"], ci=t["ci_pct"]))
                elif better * lo < 0 and better * hi < 0:
                    findings.append(finding(
                        "trend", "watch", f"{label} has been slipping",
                        f"Your monthly best has worsened by about {abs(t['pct_per_month']):.1f}% a month "
                        f"over {t['months']} months (95% interval "
                        f"{min(abs(lo), abs(hi)):.1f}\u2013{max(abs(lo), abs(hi)):.1f}%).",
                        "Check consistency and recovery first. A block with one or two quality sessions "
                        "a week on an easy base usually turns this around.",
                        pct_per_month=t["pct_per_month"], ci=t["ci_pct"]))
                else:
                    findings.append(finding(
                        "trend", "info", f"{label} is stable",
                        f"No clear change over the last {t['months']} months: the 95% interval for the "
                        f"monthly change ({lo:+.1f}% to {hi:+.1f}%) includes zero.",
                        "To move it, change something: add a weekly quality session or a little volume.",
                        pct_per_month=t["pct_per_month"], ci=t["ci_pct"]))
    out["performance"] = perf

    # --- efficiency (runs with heart rate) ------------------------------------------------
    if group == "run":
        # Median per month (not the best): this describes typical easy runs.
        eff_points = tr.efficiency_points(acts, ctx["hr_max"])
        by_month: dict[str, list[float]] = {}
        for dt, v in eff_points:
            by_month.setdefault(dt.strftime("%Y-%m"), []).append(v)
        eff = [{"month": m, "value": float(np.median(v)), "count": len(v)}
               for m, v in sorted(by_month.items()) if len(v) >= 2]
        out["efficiency"] = [{"month": e["month"], "value": round(e["value"], 3)} for e in eff]
        et = tr.trend(eff)
        if et:
            lo, hi = et["ci_pct"]
            if lo > 0:
                findings.append(finding(
                    "efficiency", "good", "Aerobic efficiency is rising",
                    f"On easy runs you are covering more ground per heartbeat: about "
                    f"{et['pct_per_month']:.1f}% a month (95% interval {lo:.1f}\u2013{hi:.1f}%).",
                    "A classic sign of a growing aerobic base. Keep easy runs genuinely easy.",
                    **et))
            elif hi < 0:
                findings.append(finding(
                    "efficiency", "watch", "Aerobic efficiency is falling",
                    f"On easy runs you are covering less ground per heartbeat: about "
                    f"{abs(et['pct_per_month']):.1f}% a month.",
                    "Often fatigue, heat, illness or less easy volume. Look at sleep and recovery, and "
                    "rebuild easy mileage.", **et))

    # --- race predictions and PR chances ----------------------------------------------------
    if group == "run":
        efforts = run_efforts(details)
        preds = tr.race_predictions(acts, efforts, now)
        bests = {b["name"]: b for b in personal_bests(details.values())}
        name_map = {"5k": "5k", "10k": "10k", "Half marathon": "Half-Marathon", "Marathon": "Marathon"}
        for p in preds:
            pb = bests.get(name_map[p["name"]])
            p["pb"] = pb["time"] if pb else None
            p["pb_within_reach"] = bool(pb and p["seconds"] < pb["seconds"] and not p["note"])
        out["predictions"] = preds
        reach = [p for p in preds if p["pb_within_reach"]]
        if reach:
            p = reach[0]
            findings.append(finding(
                "pr", "good", f"A {p['name']} PR looks within reach",
                f"Your recent running predicts {p['time']} (range {p['range'][0]}\u2013{p['range'][1]}), "
                f"faster than your best of {p['pb']}.",
                f"Pick a flat route or a race in the next few weeks and go for it. Choose 'Get faster' "
                f"with {p['name']} in the plan builder for a focused block.", prediction=p))

    # --- intensity balance ---------------------------------------------------------------------
    mix = tr.intensity_mix(acts, months=6, now=now, **kw)
    out["intensity"] = mix
    last3 = mix[-3:]
    tot = sum(m["low"] + m["moderate"] + m["high"] for m in last3)
    if tot > 5:
        low = sum(m["low"] for m in last3) / tot
        hard = sum(m["high"] for m in last3) / tot
        if low < 0.65:
            findings.append(finding(
                "intensity", "watch", "Most of your training is at moderate effort",
                f"Only {low:.0%} of your time in the last 3 months was easy. Endurance athletes "
                "typically do about 75\u201380% easy, and too much 'medium' work builds fatigue "
                "faster than fitness.",
                "Slow your easy sessions down (see the easy targets in the plan) and make the hard "
                "ones properly hard.", low_share=low, high_share=hard))
        else:
            findings.append(finding(
                "intensity", "good", "Good balance of easy and hard work",
                f"{low:.0%} of your time in the last 3 months was easy, in line with what works for "
                "most endurance athletes.", None, low_share=low, high_share=hard))

    # --- load, fatigue, consistency ----------------------------------------------------------
    status, meaning = tr.acwr_band(acwr)
    findings.append(finding(
        "load_ratio", status, "Recent load vs. your usual",
        ("Hardly any training in the last week compared with your usual." if acwr is not None and acwr < 0.1
         else f"Your last week's load is {acwr:.2f}\u00d7 your 4-week average: {meaning}." if acwr
         else f"Workload ratio: {meaning}."),
        {"warning": "Ease off for a few days. Big spikes in load are a common precursor to injury.",
         "watch": "Fine for a short block, but don't stack another big week on top.",
         "info": "Build back gradually: around 10% more per week is a sensible ceiling.",
         "good": None}[status],
        ratio=acwr))
    if tsb < -25:
        findings.append(finding(
            "form", "warning", "You are carrying a lot of fatigue",
            f"Form is {tsb:.0f} (fitness {ctl:.0f}, fatigue {atl:.0f}).",
            "Plan 3\u20135 easier days before your next hard session or race.", form=tsb))
    elif tsb > 15 and ctl > 10:
        findings.append(finding(
            "form", "info", "You are fresh",
            f"Form is +{tsb:.0f}: well rested. Ideal before a race; for longer periods it means "
            "fitness is slowly fading.", "A good moment for a race or a time trial.", form=tsb))
    if vol_prev and vol_last > 1.3 * vol_prev:
        findings.append(finding(
            "volume", "watch", "Volume has jumped",
            f"Your last 4 weeks averaged {vol_last:.1f} h, up {_pct(vol_last, vol_prev):.0f}% on the "
            "4 weeks before.", "Hold this level for a couple of weeks before adding more.",
            last=vol_last, previous=vol_prev))
    elif vol_prev and vol_last < 0.7 * vol_prev:
        findings.append(finding(
            "volume", "info", "Volume is down",
            f"Your last 4 weeks averaged {vol_last:.1f} h, {abs(_pct(vol_last, vol_prev)):.0f}% below "
            "the 4 weeks before.", "If that wasn't planned, rebuild gradually; the plan builder starts "
            "from where you are now.", last=vol_last, previous=vol_prev))
    findings.append(finding(
        "consistency", "good" if active_weeks >= 10 else "watch" if active_weeks <= 6 else "info",
        "Consistency",
        f"You trained in {active_weeks} of the last 12 weeks.",
        None if active_weeks >= 10 else "Consistency beats big weeks: two or three sessions every "
        "week will do more than occasional large ones.", active_weeks=active_weeks))

    # Is the current load enough to hold today's fitness? Fitness is a 42-day
    # average of daily load, so it holds steady at about 7 x fitness per week.
    load_last4 = np.mean([w["load"] for w in last4]) if last4 else 0
    hours_last4 = np.mean([w["time"] for w in last4]) / 3600 if last4 else 0
    sustain = 7 * ctl
    if ctl > 10 and load_last4 < 0.85 * sustain and hours_last4 > 0:
        need_h = sustain / (load_last4 / hours_last4) if load_last4 else 0
        findings.append(finding(
            "fitness_direction", "info", "Your fitness is drifting down",
            f"Your recent weeks score about {load_last4:.0f} load points, below the ~{sustain:.0f} a week "
            f"that holds your current fitness ({ctl:.0f}).",
            f"That's fine in a recovery phase. To hold fitness, aim for roughly {need_h:.1f} h a week at "
            "your usual mix of effort.", weekly_load=load_last4, sustain=sustain, hours_needed=need_h))
    elif ctl > 10 and load_last4 > 1.25 * sustain:
        findings.append(finding(
            "fitness_direction", "good", "Your fitness is building",
            f"Your recent weeks score about {load_last4:.0f} load points, above the ~{sustain:.0f} that "
            f"holds your current fitness ({ctl:.0f}).",
            "Keep recovery weeks in the mix so the extra load turns into fitness rather than fatigue.",
            weekly_load=load_last4, sustain=sustain))

    if group == "run" and last4:
        avg_week_km = np.mean([w["distance"] for w in last4]) / 1000
        longest = max(w["longest"] for w in last4) / 1000
        if avg_week_km > 10 and longest / avg_week_km > 0.45:
            findings.append(finding(
                "long_run", "watch", "Your long run carries too much of the week",
                f"Your longest recent run ({longest:.1f} km) is {longest / avg_week_km:.0%} of an "
                f"average week ({avg_week_km:.1f} km).",
                "Spread the volume: add easy kilometres on other days before lengthening the long run.",
                longest_km=longest, weekly_km=avg_week_km))
        cad = [a["average_cadence"] * 2 for a in _recent(acts, now, 90) if a.get("average_cadence")]
        if len(cad) >= 5:
            out["cadence"] = round(float(np.median(cad)))

    out["findings"] = sorted(findings, key=lambda f: STATUS_ORDER[f["status"]])
    out["summary"] = {
        "weekly_hours": round(vol_last, 1),
        "weekly_km": round(np.mean([w["distance"] for w in last4]) / 1000, 1) if last4 else 0,
        "fitness": round(ctl), "fatigue": round(atl), "form": round(tsb),
        "acwr": round(acwr, 2) if acwr else None, "active_weeks": active_weeks,
    }
    out["coverage"] = {
        "with_heart_rate": sum(1 for a in acts if a.get("average_heartrate")) / len(acts),
        "runs_scanned": len([1 for a in acts if a.get("id") in details]) if group == "run" else None,
    }
    return out


# ---------------------------------------------------------------------------
# Plan + projection
# ---------------------------------------------------------------------------
def plan_with_projection(activities: list[dict], details: dict[int, dict], req: PlanRequest,
                         now: datetime | None = None) -> dict:
    now = now or datetime.now()
    ctx = context(activities, details, req.group, now)
    acts = ctx["acts"]
    proxy, points = tr.performance_points(acts, req.group) if req.group != "all" else ("", [])
    swim_best = current_performance(points, proxy, now) if proxy == "swim" else None
    speeds = [a["distance"] / a["moving_time"] for a in acts
              if a.get("moving_time") and a.get("distance")]
    pctx = Context(cs=ctx["cs"], ftp=ctx["ftp"], hr_max=ctx["hr_max"], best_swim_pace=swim_best,
                   typical_speed=float(np.median(speeds)) if speeds else None,
                   acwr=ctx["fitness"]["acwr"][-1])
    plan = build_plan(req, activities, pctx, now)
    plan["zones"] = tr.pace_zones(ctx["cs"].cs) if ctx["cs"] and req.group == "run" else None

    projection: dict = {"available": False, "reason": "no comparable performance data for this sport"}
    if proxy:
        series = tr.monthly_best(points, proxy)
        current = current_performance(points, proxy, now)
        if current and series:
            end = date.fromisoformat(plan["weeks"][-1]["sessions"][-1]["date"]) if plan["weeks"] else now.date()
            weeks = tr.weekly_volume(acts, 5, now, **ctx["kw"])[:-1]
            recent_load = float(np.median([w["load"] for w in weeks])) if weeks else 0.0
            projection = plan_projection(series, proxy, ctx["fitness"], plan, current, now, recent_load)
            projection["trend"] = trend_projection(series, current, end)
            projection["current"] = current
            projection["proxy"] = proxy
            projection["label"] = tr.PROXY[proxy][0]
            projection["end"] = end.isoformat()
            projection["history"] = [{"month": s["month"], "value": s["value"]} for s in series[-18:]]
            projection["recent_weekly_load"] = recent_load
            projection["note"] = (
                "Projections start from your best performance of the last 90 days. The plan projection "
                "only knows how your performance has followed your overall training load in the past; it "
                "can't credit the plan's structure (intervals, long runs), so treat it as a cautious floor. "
                "Ranges are 80% intervals from bootstrapping your own history.")
            for key in ("plan", "keep", "trend"):
                if projection.get(key):
                    sc = projection[key]
                    sc["display"] = tr.fmt_proxy(sc["value"], proxy)
                    sc["range"] = [tr.fmt_proxy(sc["low"], proxy), tr.fmt_proxy(sc["high"], proxy)]
            projection["current_display"] = tr.fmt_proxy(current, proxy)
            if proxy == "run":
                dist = dict(tr.RACE_DISTANCES).get(req.target, 10000.0)

                def race(v: float) -> str:
                    return fmt_clock(tr.riegel(v * 10, 10000, dist))
                for key in ("plan", "keep", "trend"):
                    if projection.get(key):
                        sc = projection[key]
                        sc["race_time"] = race(sc["value"])
                        sc["race_range"] = [race(sc["low"]), race(sc["high"])]
                projection["race"] = req.target
                projection["current_race_time"] = race(current)
    plan["projection"] = projection
    return plan
