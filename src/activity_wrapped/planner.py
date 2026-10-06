"""Rule-based training plans built from the athlete's own recent training.

The rules are deliberately conservative and simple enough to state in full:

- Start from what the athlete actually did: the median weekly volume of the
  last four complete weeks. If that is well below their usual load (a break,
  an injury, a busy month), rebuild from there rather than from the old level.
- Grow build weeks gradually (5-8% a week depending on the goal) and cap the
  peak relative to the starting point. If the current load is already
  spiking (workload ratio above 1.3), the first week holds steady.
- Every fourth week is a recovery week at 80% volume.
- Before a race, taper: 80% volume two weeks out, 60% in race week.
- About one session in four or five is hard; the rest are easy. Hard
  sessions are never on consecutive days or the day before the long one.
- Targets come from the athlete's own critical speed (running), estimated
  threshold power (cycling), heart rate or, failing those, perceived effort.

This is a training aid, not medical advice. Pain, illness or unusual fatigue
should override any plan.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import median

from .stats import fmt_clock, local_start
from .training import (
    RACE_DISTANCES,
    CriticalSpeed,
    in_group,
    week_start,
    weekly_volume,
)

GOALS = ("maintain", "endurance", "faster")
GOAL_LABELS = {"maintain": "Maintain fitness", "endurance": "Build endurance", "faster": "Get faster"}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Volume unit per sport group: (unit label, function from weekly totals to that unit)
UNITS = {
    "run": ("km", lambda w: w["distance"] / 1000),
    "swim": ("km", lambda w: w["distance"] / 1000),
    "ride": ("h", lambda w: w["time"] / 3600),
    "hike": ("h", lambda w: w["time"] / 3600),
    "all": ("h", lambda w: w["time"] / 3600),
}
STARTING_VOLUME = {"run": 15.0, "swim": 3.0, "ride": 3.0, "hike": 3.0, "all": 3.0}
MIN_SESSION = {"run": 3.0, "swim": 0.8, "ride": 0.75, "hike": 0.75, "all": 0.5}
# Hard sessions need room for a warm-up, the work and a cool-down.
MIN_QUALITY = {"run": 7.0, "swim": 1.5, "ride": 1.0, "hike": 1.0, "all": 0.75}
GROWTH = {"maintain": 1.00, "endurance": 1.08, "faster": 1.05}
PEAK_CAP = {"maintain": 1.05, "endurance": 1.5, "faster": 1.3}
LONG_TARGET_KM = {"5k": 12, "10k": 15, "Half marathon": 19, "Marathon": 32}
# Average intensity factor of each session type, for planned training load.
SESSION_IF = {"easy": 0.72, "long": 0.75, "steady": 0.85, "quality": 0.88, "recovery": 0.65}


@dataclass
class PlanRequest:
    group: str = "run"
    goal: str = "faster"
    target: str = "10k"
    weeks: int = 8
    days: int = 4
    race_date: date | None = None


@dataclass
class Context:
    """What the planner knows about the athlete."""

    cs: CriticalSpeed | None = None
    ftp: float | None = None
    hr_max: float | None = None
    best_swim_pace: float | None = None  # seconds per 100 m
    typical_speed: float | None = None  # m/s, for converting distance to time
    acwr: float | None = None


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------
def _run_target(ctx: Context, lo: float, hi: float, hr: tuple[int, int], rpe: str) -> str:
    if ctx.cs:
        return f"{fmt_clock(1000 / (ctx.cs.cs * hi))}\u2013{fmt_clock(1000 / (ctx.cs.cs * lo))} /km"
    return _hr_or_rpe(ctx, hr, rpe)


def _hr_or_rpe(ctx: Context, hr: tuple[int, int], rpe: str) -> str:
    if ctx.hr_max:
        return f"{hr[0] * ctx.hr_max / 100:.0f}\u2013{hr[1] * ctx.hr_max / 100:.0f} bpm"
    return f"effort {rpe}"


def _ride_target(ctx: Context, lo: float, hi: float, hr: tuple[int, int], rpe: str) -> str:
    if ctx.ftp:
        return f"{lo * ctx.ftp:.0f}\u2013{hi * ctx.ftp:.0f} W"
    return _hr_or_rpe(ctx, hr, rpe)


def _swim_target(ctx: Context, lo: float, hi: float, rpe: str) -> str:
    if ctx.best_swim_pace:
        return f"{fmt_clock(ctx.best_swim_pace + lo)}\u2013{fmt_clock(ctx.best_swim_pace + hi)} /100m"
    return f"effort {rpe}"


def _speed_for(group: str, ctx: Context, frac: float) -> float | None:
    """Expected speed (m/s) of a session, for turning distance into time."""
    if group == "run" and ctx.cs:
        return ctx.cs.cs * frac
    if group == "swim" and ctx.best_swim_pace:
        return 100 / (ctx.best_swim_pace + (12 if frac < 0.9 else 3))
    return ctx.typical_speed


# ---------------------------------------------------------------------------
# Session library: (kind, title, description, target)
# ---------------------------------------------------------------------------
def quality_sessions(group: str, goal: str, target: str, ctx: Context) -> list[tuple[str, str, str, str]]:
    if group == "run":
        thr = _run_target(ctx, 0.90, 0.98, (86, 91), "7/10")
        cs = _run_target(ctx, 0.98, 1.04, (90, 94), "8/10")
        vo2 = _run_target(ctx, 1.04, 1.12, (92, 97), "9/10")
        steady = _run_target(ctx, 0.82, 0.90, (80, 86), "6/10")
        if goal == "faster" and target in ("5k", "10k"):
            return [("quality", "Intervals", "Warm up 15 min. 6 \u00d7 800 m hard with 2 min easy jog between. "
                     "Cool down.", vo2),
                    ("quality", "Threshold", "Warm up. 3 \u00d7 8 min at threshold, 2 min jog between.", thr),
                    ("quality", "Critical-speed kilometres", "Warm up. 5 \u00d7 1 km with 90 s jog between.", cs),
                    ("quality", "Hill repeats", "Warm up. 8 \u00d7 60 s uphill, strong but controlled; "
                     "jog back down.", "effort 8/10")]
        if goal == "faster":
            return [("quality", "Threshold", "Warm up. 2 \u00d7 15 min at threshold, 3 min jog between.", thr),
                    ("quality", "Cruise intervals", "Warm up. 5 \u00d7 1.6 km at threshold, 60 s jog between.",
                     thr),
                    ("steady", "Steady run", "40\u201360 min continuous at steady pace.", steady)]
        if goal == "endurance":
            return [("steady", "Steady run", "Middle 20\u201340 min at steady pace, easy either side.", steady),
                    ("quality", "Tempo", "Warm up. 20 min at threshold. Cool down.", thr)]
        return [("quality", "Fartlek", "8 \u00d7 1 min brisk / 2 min easy, inside an easy run.", thr),
                ("quality", "Tempo", "Warm up. 20 min at threshold. Cool down.", thr)]
    if group == "ride":
        ss = _ride_target(ctx, 0.88, 0.94, (82, 88), "7/10")
        thr = _ride_target(ctx, 0.95, 1.05, (87, 92), "8/10")
        vo2 = _ride_target(ctx, 1.06, 1.20, (92, 97), "9/10")
        if goal == "faster":
            return [("quality", "VO2max", "Warm up 15 min. 5 \u00d7 4 min hard, 4 min easy spinning.", vo2),
                    ("quality", "Threshold", "Warm up. 2 \u00d7 20 min at threshold, 5 min easy.", thr),
                    ("quality", "Sweet spot", "Warm up. 3 \u00d7 15 min sweet spot, 5 min easy.", ss)]
        return [("quality", "Sweet spot", "Warm up. 3 \u00d7 12 min sweet spot, 5 min easy.", ss),
                ("steady", "Tempo ride", "60\u201390 min with the middle hour at a steady tempo.", ss)]
    if group == "swim":
        thr = _swim_target(ctx, 2, 5, "7/10")
        fast = _swim_target(ctx, -3, 0, "9/10")
        return [("quality", "Threshold set", "Warm up 300 m. 8 \u00d7 100 m with 15 s rest. Cool down.", thr),
                ("quality", "Speed set", "Warm up. 12 \u00d7 50 m fast with 30 s rest. Cool down.", fast),
                ("steady", "Technique + pull", "Drills 6 \u00d7 50 m, then 4 \u00d7 200 m pull buoy, steady.",
                 _swim_target(ctx, 8, 12, "5/10"))]
    if group == "hike":
        return [("steady", "Hill session", "Pick the steepest route nearby; sustained climbing "
                 "at a pace you can still talk at.", _hr_or_rpe(ctx, (75, 85), "6/10"))]
    return [("steady", "Moderate session", "A continuous session at a comfortably hard effort.",
             _hr_or_rpe(ctx, (75, 85), "6/10"))]


def easy_target(group: str, ctx: Context) -> str:
    if group == "run":
        return _run_target(ctx, 0.70, 0.82, (65, 78), "3\u20134/10")
    if group == "ride":
        return _ride_target(ctx, 0.55, 0.75, (60, 75), "3\u20134/10")
    if group == "swim":
        return _swim_target(ctx, 12, 18, "3\u20134/10")
    return _hr_or_rpe(ctx, (60, 75), "3\u20134/10")


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
def _circular_gap(a: int, b: int) -> int:
    d = abs(a - b) % 7
    return min(d, 7 - d)


def choose_days(days: int, long_day: int) -> tuple[list[int], list[int]]:
    """Training days (Mon=0) and, in order of preference, the ones best
    suited to hard sessions: as far as possible from the long session and
    from each other, never the day before the long one."""
    chosen = [long_day]
    while len(chosen) < days:
        best = max((d for d in range(7) if d not in chosen),
                   key=lambda d: (min(_circular_gap(d, c) for c in chosen), -d))
        chosen.append(best)
    candidates = [d for d in chosen if d != long_day and (d + 1) % 7 != long_day]
    quality_order = []
    for _ in range(len(candidates)):
        pick = max((d for d in candidates if d not in quality_order),
                   key=lambda d: (min(_circular_gap(d, c) for c in [long_day, *quality_order]), -d))
        quality_order.append(pick)
    return sorted(chosen), quality_order


def preferred_long_day(activities: list[dict], group: str) -> int:
    """The weekday the athlete usually does their longest session on."""
    best_by_week: dict = {}
    for a in activities:
        if not in_group(a, group) or not local_start(a):
            continue
        wk = week_start(local_start(a).date())
        if wk not in best_by_week or a.get("moving_time", 0) > best_by_week[wk].get("moving_time", 0):
            best_by_week[wk] = a
    days = Counter(local_start(a).weekday() for a in best_by_week.values())
    return days.most_common(1)[0][0] if days else 6


# ---------------------------------------------------------------------------
# Volume progression
# ---------------------------------------------------------------------------
def baseline(activities: list[dict], group: str, now: datetime) -> dict:
    unit, measure = UNITS[group]
    acts = [a for a in activities if in_group(a, group)]
    weeks = weekly_volume(acts, 13, now)[:-1]  # complete weeks only
    vols = [measure(w) for w in weeks]
    recent, prior = vols[-4:], vols[-12:-4]
    longest = max((w["longest"] for w in weeks[-4:]), default=0) / 1000
    longest_time = 0.0
    for a in acts:
        dt = local_start(a)
        if dt and dt >= now - timedelta(days=28):
            longest_time = max(longest_time, a.get("moving_time", 0) / 3600)
    return {"unit": unit, "recent": median(recent) if recent else 0.0,
            "prior": median(prior) if prior else 0.0, "longest_km": longest,
            "longest_h": longest_time, "weeks": vols}


def volumes(req: PlanRequest, base: dict, acwr: float | None) -> tuple[list[float], list[str], list[str]]:
    """Weekly volumes and the kind of each week, plus notes explaining any
    adjustment made to the starting point."""
    notes = []
    start = base["recent"]
    if start <= 0:
        start = STARTING_VOLUME[req.group]
        notes.append(f"No recent training found, so the plan starts at a gentle "
                     f"{start:g} {base['unit']} a week.")
    elif base["prior"] and start < 0.5 * base["prior"]:
        notes.append("Your last four weeks are well below your usual volume, so the plan rebuilds "
                     "from where you are now rather than where you were.")
    growth, cap = GROWTH[req.goal], PEAK_CAP[req.goal] * start
    if acwr and acwr > 1.3:
        notes.append(f"Your recent load is already {acwr:.2f}\u00d7 your usual, so week 1 holds "
                     "steady before building.")
    vols, kinds = [], []
    level = start * (0.95 if acwr and acwr > 1.3 else 1.0)
    for i in range(req.weeks):
        weeks_left = req.weeks - i
        if req.race_date and weeks_left == 1:
            vols.append(round(max(vols or [start]) * 0.6, 1))
            kinds.append("race week")
            continue
        if req.race_date and weeks_left == 2:
            vols.append(round(max(vols or [start]) * 0.8, 1))
            kinds.append("taper")
            continue
        if (i + 1) % 4 == 0:
            vols.append(round(level * 0.8, 1))
            kinds.append("recovery")
            continue
        if i > 0 and not (acwr and acwr > 1.3 and i == 1):
            level = min(level * growth, cap)
        vols.append(round(level, 1))
        kinds.append("build" if req.goal != "maintain" else "steady")
    return vols, kinds, notes


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
def _duration_h(group: str, amount: float, speed: float | None) -> float:
    """Hours for a session given in the group's unit."""
    if UNITS[group][0] == "h":
        return amount
    if speed:
        return amount * 1000 / speed / 3600
    return amount / (10 if group == "run" else 2.5)  # rough fallbacks: 10 km/h running, 2.5 km/h swimming


def build_plan(req: PlanRequest, activities: list[dict], ctx: Context, now: datetime) -> dict:
    if req.goal not in GOALS:
        raise ValueError(f"goal must be one of {GOALS}")
    if req.race_date:
        weeks = (week_start(req.race_date) - week_start(now.date() + timedelta(days=7))).days // 7 + 1
        req.weeks = max(2, min(20, weeks))
    req.weeks = max(2, min(20, req.weeks))
    req.days = max(2, min(7, req.days))
    base = baseline(activities, req.group, now)
    vols, kinds, notes = volumes(req, base, ctx.acwr)
    unit = base["unit"]
    long_day = preferred_long_day(activities, req.group)
    days, quality_days = choose_days(req.days, long_day)
    library = quality_sessions(req.group, req.goal, req.target, ctx)
    start = week_start(now.date()) + timedelta(days=7)
    min_session = MIN_SESSION[req.group]

    base_long = base["longest_km"] if unit == "km" else base["longest_h"]
    long_goal = LONG_TARGET_KM.get(req.target, 16) if (req.group == "run" and req.goal != "maintain") else None

    weeks, q_index = [], 0
    long_level = base_long or 0.3 * vols[0]
    for i, (vol, kind) in enumerate(zip(vols, kinds, strict=True)):
        n_quality = {"faster": 2, "endurance": 1, "maintain": 1}[req.goal]
        if req.days <= 3:
            n_quality = min(n_quality, 1)
        if kind in ("recovery", "race week"):
            n_quality = min(n_quality, 1)
        if req.group == "hike":
            n_quality = min(n_quality, 1)
        n_quality = min(n_quality, len(quality_days))

        # Long session: grows gently towards the goal, never more than about
        # a third of the week (35% for marathon training).
        share_cap = 0.35 if req.target == "Marathon" else 0.32
        if kind == "build" and long_goal:
            long_level = min(long_level + (1.5 if unit == "km" else 0.25), long_goal)
        long_amt = min(max(long_level, 0.25 * vol), share_cap * vol) if req.days > 1 else vol
        if kind in ("recovery", "taper", "race week"):
            long_amt = min(long_amt, 0.25 * vol)
        q_amt = max(0.16 * vol, MIN_QUALITY[req.group])
        n_easy = req.days - 1 - n_quality
        rest = vol - long_amt - n_quality * q_amt
        easy_amt = rest / n_easy if n_easy else 0
        long_amt = max(long_amt, q_amt)
        if n_easy and easy_amt > long_amt:  # an easy day should never outgrow the long one
            long_amt = easy_amt = (vol - n_quality * q_amt) / (n_easy + 1)
        if n_easy and easy_amt < min_session:  # little volume for this many days: keep sessions sensible
            easy_amt = min_session

        sessions = []
        q_days = quality_days[:n_quality]
        for d in days:
            if d == long_day:
                title, desc, kind_s = "Long session", "Relaxed and conversational throughout.", "long"
                if req.group == "run" and req.target == "Marathon" and req.goal == "faster" and kind == "build":
                    desc = "Easy, with the last 25\u201340 min at your goal marathon effort."
                sess = {"kind": kind_s, "title": title, "description": desc, "amount": long_amt,
                        "target": easy_target(req.group, ctx)}
            elif d in q_days:
                k, title, desc, target = library[q_index % len(library)]
                q_index += 1
                sess = {"kind": k, "title": title, "description": desc, "amount": q_amt, "target": target}
            else:
                desc = "Easy and relaxed."
                if req.group == "run" and req.goal == "faster" and d == min(days) and kind != "race week":
                    desc = "Easy, finishing with 6 \u00d7 20 s strides (fast but relaxed, full recovery)."
                sess = {"kind": "easy", "title": "Easy session", "description": desc, "amount": easy_amt,
                        "target": easy_target(req.group, ctx)}
            if req.race_date and kind == "race week" and d == long_day:
                sess = {"kind": "quality", "title": "Race day",
                        "description": f"{req.target} race. Easy warm-up, start controlled.",
                        "amount": next((m / 1000 for n, m in RACE_DISTANCES if n == req.target), long_amt),
                        "target": "race effort"}
            speed = _speed_for(req.group, ctx, 0.78 if sess["kind"] in ("easy", "long") else 0.95)
            hours = _duration_h(req.group, sess["amount"], speed)
            sess.update({
                "day": WEEKDAYS[d],
                "date": (start + timedelta(weeks=i, days=d)).isoformat(),
                "amount": round(sess["amount"], 1),
                "unit": unit,
                "minutes": round(hours * 60 / 5) * 5,
                "load": round(hours * SESSION_IF.get(sess["kind"], 0.8) ** 2 * 100),
            })
            sessions.append(sess)
        weeks.append({
            "index": i + 1, "start": (start + timedelta(weeks=i)).isoformat(), "kind": kind,
            "volume": round(sum(s["amount"] for s in sessions), 1), "unit": unit,
            "load": sum(s["load"] for s in sessions), "sessions": sessions,
        })

    peak_long = max((s["amount"] for w in weeks for s in w["sessions"] if s["kind"] == "long"), default=0)
    if long_goal and req.target in ("Half marathon", "Marathon") and peak_long < 0.8 * long_goal:
        notes.append(f"At your current volume the long run peaks at {peak_long:.0f} km. For a "
                     f"{req.target.lower()} most runners build to {long_goal} km or so: allow more weeks, "
                     "or build weekly volume first.")
    if req.group == "run" and not ctx.cs:
        notes.append("Not enough recent hard efforts to estimate your critical speed, so targets use "
                     + ("heart rate." if ctx.hr_max else "perceived effort (0\u201310)."))
    if req.group == "ride" and not ctx.ftp:
        notes.append("No power data, so targets use " + ("heart rate." if ctx.hr_max else "perceived effort."))
    notes.append("Move sessions around life as needed, but keep hard days apart. Pain, illness or "
                 "unusual fatigue overrides the plan.")
    return {
        "group": req.group, "goal": req.goal, "goal_label": GOAL_LABELS[req.goal], "target": req.target,
        "race_date": req.race_date.isoformat() if req.race_date else None,
        "weeks": weeks, "baseline": {"weekly": round(base["recent"], 1), "unit": unit,
                                     "longest": round(base_long, 1)},
        "notes": notes, "long_day": WEEKDAYS[long_day],
    }


def to_ics(plan: dict) -> str:
    """The plan as an iCalendar file: one all-day event per session."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Activity Wrapped//Training plan//EN",
             "CALSCALE:GREGORIAN"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for w in plan["weeks"]:
        for s in w["sessions"]:
            d = date.fromisoformat(s["date"])
            summary = f"{s['title']} \u2013 {s['amount']:g} {s['unit']}"
            desc = (f"{s['description']}\nTarget: {s['target']}\n"
                    f"About {s['minutes']} min. Week {w['index']} ({w['kind']}).")
            lines += ["BEGIN:VEVENT", f"UID:aw-{plan['group']}-{s['date']}-{s['kind']}@activity-wrapped",
                      f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
                      f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
                      f"SUMMARY:{esc(summary)}", f"DESCRIPTION:{esc(desc)}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

