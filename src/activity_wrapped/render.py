"""Render computed stats as a shareable PNG card with Pillow.

The card is a vertical stack of independent sections. Each section knows
its own height, so the card grows or shrinks to fit whatever the user
selects. SECTIONS is the single vocabulary shared with the frontend: the
checkbox list is built from it, and the preview the user sees is this
renderer's own output, so preview and download can never disagree.
"""

from __future__ import annotations

import io
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).parent / "fonts"

SECTIONS = [
    {"key": "headline", "label": "Total distance", "default": True},
    {"key": "totals", "label": "Moving time, elevation, active days", "default": True},
    {"key": "by_sport", "label": "Breakdown by sport", "default": True},
    {"key": "series", "label": "Distance by month / year (chart)", "default": True},
    {"key": "best_efforts", "label": "Best efforts (named PRs)", "default": True},
    {"key": "pace", "label": "Average pace / speed (main sport)", "default": False},
    {"key": "eddington", "label": "Eddington number", "default": True},
    {"key": "streak", "label": "Longest streak", "default": False},
    {"key": "habits", "label": "Favourite day & time", "default": False},
    {"key": "longest", "label": "Longest activity", "default": False},
    {"key": "climb", "label": "Biggest climb", "default": False},
    {"key": "achievements", "label": "Achievements & PR count", "default": False},
    {"key": "busiest_month", "label": "Busiest month", "default": False},
    {"key": "repeated_name", "label": "Go-to activity name", "default": False},
]
SECTION_KEYS = [s["key"] for s in SECTIONS]
DEFAULT_SECTIONS = [s["key"] for s in SECTIONS if s["default"]]

THEMES = {
    "midnight": {"bg": "#0b1016", "card": "#141c26", "box": "#0f161e", "border": "#263241",
                 "text": "#eef2f6", "muted": "#8a97a8", "accent": "#4fd1c5", "bar": "#4fd1c5"},
    "paper": {"bg": "#e9e3d8", "card": "#fbf8f3", "box": "#f1ece3", "border": "#ddd3c4",
              "text": "#1f1b16", "muted": "#7a6f61", "accent": "#c2410c", "bar": "#c2410c"},
    "forest": {"bg": "#0a1510", "card": "#11221a", "box": "#0d1b14", "border": "#24402f",
               "text": "#e8f3ec", "muted": "#8fb19c", "accent": "#a3e635", "bar": "#a3e635"},
    "dusk": {"bg": "#140f1f", "card": "#1d1630", "box": "#171127", "border": "#352a52",
             "text": "#f3effa", "muted": "#a197b8", "accent": "#f472b6", "bar": "#c084fc"},
}

W = 1080
MARGIN = 40          # space between image edge and card
PAD = 96             # space between image edge and content
CW = W - 2 * PAD     # content width
GAP = 24             # gap between boxes in a row
SECTION_GAP = 36     # space above and below each divider

PREFERRED_EFFORTS = ["1k", "1 mile", "5k", "10k", "Half-Marathon", "Marathon"]


@cache
def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / f"Poppins-{weight}.ttf"
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:  # pragma: no cover - bundled fonts missing
        return ImageFont.load_default(size=size)


def fit(draw: ImageDraw.ImageDraw, text: str, f, max_w: float) -> str:
    """Shorten text with an ellipsis until it fits max_w pixels."""
    if draw.textlength(text, font=f) <= max_w:
        return text
    while text and draw.textlength(text + "\u2026", font=f) > max_w:
        text = text[:-1]
    return text.rstrip() + "\u2026"


class Builder:
    def __init__(self, theme: dict) -> None:
        self.t = theme
        self.blocks: list[tuple[int, callable]] = []

    def add(self, height: int, fn) -> None:
        if self.blocks:
            self.blocks.append((2 * SECTION_GAP, self._divider))
        self.blocks.append((height, fn))

    def _divider(self, d, y):
        d.line([(PAD, y + SECTION_GAP), (W - PAD, y + SECTION_GAP)], fill=self.t["border"], width=2)

    @property
    def height(self) -> int:
        return sum(h for h, _ in self.blocks)

    # -- reusable pieces --------------------------------------------------------
    def label(self, d, y, text):
        d.text((PAD, y), text.upper(), font=font("Medium", 24), fill=self.t["muted"])

    def boxes(self, d, y, items, h=136):
        items = [i for i in items if i]
        n = len(items)
        bw = (CW - GAP * (n - 1)) / n
        for i, (label, value) in enumerate(items):
            x0 = PAD + i * (bw + GAP)
            d.rounded_rectangle([x0, y, x0 + bw, y + h], radius=18, fill=self.t["box"])
            d.text((x0 + 28, y + 24), fit(d, label.upper(), font("Medium", 22), bw - 56),
                   font=font("Medium", 22), fill=self.t["muted"])
            vf = font("Bold", 40 if n < 3 else 36)
            d.text((x0 + 28, y + 62), fit(d, value, vf, bw - 56), font=vf, fill=self.t["text"])

    def rows(self, d, y, rows, right_accent=False):
        f, fb = font("Regular", 32), font("Bold", 32)
        for left, middle, right in rows:
            rw = d.textlength(right, font=fb if right_accent else f)
            mw = d.textlength(middle, font=f) if middle else 0
            d.text((PAD, y), fit(d, left, f, CW - rw - mw - 60), font=f, fill=self.t["text"])
            if middle:
                d.text((W - PAD - rw - 32 - mw, y), middle, font=f, fill=self.t["muted"])
            d.text((W - PAD - rw, y), right, font=fb if right_accent else f,
                   fill=self.t["accent"] if right_accent else self.t["muted"])
            y += 56


# ---------------------------------------------------------------------------
# Sections: each takes (builder, stats) and adds itself if it has data
# ---------------------------------------------------------------------------
def s_headline(b, s):
    def fn(d, y):
        b.label(d, y, "total distance")
        d.text((PAD - 6, y + 30), s["totals"]["distance"], font=font("Bold", 124), fill=b.t["accent"])
    b.add(196, fn)


def s_totals(b, s):
    t = s["totals"]
    b.add(136, lambda d, y: b.boxes(d, y, [("moving time", t["moving_time"]),
                                            ("elevation", t["elevation"]),
                                            ("active days", str(t["active_days"]))]))


def s_pace(b, s):
    p = s.get("pace")
    if p:
        b.add(136, lambda d, y: b.boxes(d, y, [(f"{p['label']} \u00b7 {p['sport']}", p["value"])]))


def s_by_sport(b, s):
    sports = s["by_sport"][:5]

    def fn(d, y):
        b.label(d, y, "by sport")
        rows = []
        for sp in sports:
            parts = [sp["distance"], f"{sp['count']}\u00d7"] + ([sp["pace"]] if sp["pace"] else [])
            rows.append((sp["label"], "", "  \u00b7  ".join(parts)))
        b.rows(d, y + 52, rows)
    b.add(52 + 56 * len(sports) - 14, fn)


def s_series(b, s):
    ser = s["series"]
    if not ser["values"] or max(ser["values"]) <= 0:
        return
    chart_h = 230

    def fn(d, y):
        b.label(d, y, ser["title"])
        top = y + 84
        n = len(ser["values"])
        slot = CW / n
        bw = slot * 0.62
        vmax = max(ser["values"])
        peak = ser["values"].index(vmax)
        for i, (lab, v) in enumerate(zip(ser["labels"], ser["values"], strict=True)):
            x0 = PAD + i * slot + (slot - bw) / 2
            h = max(4, chart_h * v / vmax)
            d.rounded_rectangle([x0, top + chart_h - h, x0 + bw, top + chart_h],
                                radius=min(10, bw / 2), fill=b.t["bar"] if i == peak else b.t["border"])
            lw = d.textlength(lab, font=font("Medium", 24))
            d.text((x0 + bw / 2 - lw / 2, top + chart_h + 14), lab, font=font("Medium", 24),
                   fill=b.t["muted"])
        peak_txt = f"{vmax:,.0f} {ser['unit']}"
        f = font("Bold", 26)
        tw = d.textlength(peak_txt, font=f)
        px = PAD + peak * slot + slot / 2 - tw / 2
        px = min(max(px, PAD), W - PAD - tw)
        d.text((px, top - 40), peak_txt, font=f, fill=b.t["text"])
    b.add(84 + chart_h + 52, fn)


def s_best_efforts(b, s):
    be = s["best_efforts"]
    efforts = be["efforts"]
    if not efforts:
        return
    by_name = {e["name"]: e for e in efforts}
    chosen = [by_name[n] for n in PREFERRED_EFFORTS if n in by_name] or efforts[:6]
    partial = be["scanned"] < be["total"]

    def fn(d, y):
        b.label(d, y, "best efforts")
        b.rows(d, y + 52, [(e["name"], e["date"], e["time"]) for e in chosen], right_accent=True)
        if partial:
            note = f"from {be['scanned']} of {be['total']} runs scanned"
            d.text((PAD, y + 52 + 56 * len(chosen) + 4), note, font=font("Regular", 24),
                   fill=b.t["muted"])
    b.add(52 + 56 * len(chosen) - 14 + (44 if partial else 0), fn)


def s_eddington(b, s):
    e = s["eddington"]

    def fn(d, y):
        b.label(d, y, "eddington number")
        d.text((PAD - 4, y + 28), str(e["value"]), font=font("Bold", 96), fill=b.t["accent"])
        sub = f"{e['value']} days of at least {e['value']} {e['unit']}"
        vw = d.textlength(str(e["value"]), font=font("Bold", 96))
        d.text((PAD + vw + 28, y + 82), sub, font=font("Regular", 30), fill=b.t["muted"])
    b.add(150, fn)


def _line(b, label, value, sub=None):
    def fn(d, y):
        b.label(d, y, label)
        d.text((PAD, y + 36), fit(d, value, font("Bold", 44), CW), font=font("Bold", 44),
               fill=b.t["text"])
        if sub:
            d.text((PAD, y + 100), fit(d, sub, font("Regular", 28), CW), font=font("Regular", 28),
                   fill=b.t["muted"])
    b.add(140 if sub else 96, fn)


def s_streak(b, s):
    st = s["streak"]
    sub = f"{st['from']} \u2013 {st['to']}" if st["days"] > 1 else st["from"]
    _line(b, "longest streak", f"{st['days']} day{'s' if st['days'] != 1 else ''} in a row", sub)


def s_habits(b, s):
    h = s["habits"]
    b.add(136, lambda d, y: b.boxes(d, y, [("favourite day", h["weekday"]),
                                            ("favourite time", h["time_of_day"].capitalize())]))


def s_longest(b, s):
    lo = s["longest"]
    _line(b, "longest activity", f"{lo['value']} \u00b7 {lo['sport']}",
          f"\u201c{lo['name']}\u201d \u00b7 {lo['date']}")


def s_climb(b, s):
    c = s.get("climb")
    if c:
        _line(b, "biggest climb", f"{c['value']} \u00b7 {c['sport']}",
              f"\u201c{c['name']}\u201d \u00b7 {c['date']}")


def s_achievements(b, s):
    a = s["achievements"]
    b.add(136, lambda d, y: b.boxes(d, y, [("achievements", f"{a['achievements']:,}"),
                                            ("PRs set", f"{a['prs']:,}")]))


def s_busiest_month(b, s):
    m = s["busiest_month"]
    _line(b, "busiest month", f"{m['value']} \u00b7 {m['count']} activities")


def s_repeated_name(b, s):
    r = s.get("repeated_name")
    if r:
        _line(b, "go-to activity name", f"\u201c{r['name']}\u201d \u00d7 {r['count']}")


RENDERERS = {
    "headline": s_headline, "totals": s_totals, "pace": s_pace, "by_sport": s_by_sport,
    "series": s_series, "best_efforts": s_best_efforts, "eddington": s_eddington,
    "streak": s_streak, "habits": s_habits, "longest": s_longest, "climb": s_climb,
    "achievements": s_achievements, "busiest_month": s_busiest_month,
    "repeated_name": s_repeated_name,
}


def render_card(stats: dict, sections: list[str] | None = None, theme: str = "midnight",
                athlete: str = "") -> bytes:
    t = THEMES.get(theme, THEMES["midnight"])
    chosen = [k for k in SECTION_KEYS if k in (sections or DEFAULT_SECTIONS)]

    b = Builder(t)
    if stats.get("empty"):
        b.add(120, lambda d, y: d.text((PAD, y + 40), "No activities match these filters.",
                                       font=font("Regular", 32), fill=t["muted"]))
    else:
        for key in chosen:
            RENDERERS[key](b, stats)
        if not b.blocks:
            b.add(80, lambda d, y: d.text((PAD, y + 20), "Pick at least one section.",
                                          font=font("Regular", 32), fill=t["muted"]))

    header_h, footer_h = 210, 120
    H = MARGIN + header_h + b.height + footer_h + MARGIN
    img = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([MARGIN, MARGIN, W - MARGIN, H - MARGIN], radius=40, fill=t["card"],
                        outline=t["border"], width=2)

    # header
    y = MARGIN + 64
    d.text((PAD, y), "ACTIVITY WRAPPED", font=font("Bold", 26), fill=t["accent"])
    d.text((PAD, y + 40), stats["period_label"].capitalize(), font=font("Bold", 52), fill=t["text"])
    if stats.get("sports"):
        sl = fit(d, stats["sports_label"], font("Medium", 26), CW / 2)
        sw = d.textlength(sl, font=font("Medium", 26))
        d.text((W - PAD - sw, y + 4), sl, font=font("Medium", 26), fill=t["muted"])

    # body
    y = MARGIN + header_h
    for h, fn in b.blocks:
        fn(d, y)
        y += h

    # footer
    fy = H - MARGIN - footer_h + 36
    d.line([(PAD, fy), (W - PAD, fy)], fill=t["border"], width=2)
    left = fit(d, athlete or "", font("Medium", 26), CW / 2)
    d.text((PAD, fy + 28), left, font=font("Medium", 26), fill=t["muted"])
    right = f"{stats.get('count', 0):,} activities \u00b7 data from Strava"
    rw = d.textlength(right, font=font("Medium", 26))
    d.text((W - PAD - rw, fy + 28), right, font=font("Medium", 26), fill=t["muted"])

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
