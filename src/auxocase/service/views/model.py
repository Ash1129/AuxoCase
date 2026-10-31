"""Model & approvals: one shared model, live only when marketing and sales both approve.

Every figure comes from what is stored: each version's test metrics in the registry (measured once, when it was
trained), the approvals, the latest channel-monitor run, the SDR overrides and the outcomes. The one thing worked out
per view is the shadow check, which scores recent leads with the live and shadow models when a shadow exists.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from ...data.generate import LATE_TOUCH_FACTOR
from ...feedback import loop as fb
from ...scoring.model import CAPACITY_PER_MONTH
from ...scoring.pipeline import RESCORE_PENDING
from ...storage.db import clock, get_setting, loads
from ..security import approval_token
from .lead import SMALL_BAND
from .stats import hit_rate

TEAMS = ("marketing", "sales")
CLOSE = 0.02          # precision at K this near the cold-start table's reads as "about as well" (a few demos in 300)
SAME_BASE = 0.05      # versions whose test months booked within this of each other are named as one group
FULL_BAR = 0.10       # a full bar in "what the model adds", unless a gap is bigger
BAR_PX = 136
SHADOW_K = 500        # the shadow check's top K, the brief's monthly capacity
FAMILIES = {"lr": "logistic regression", "hgb": "gradient boosting"}
COUNTS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
METRIC_KEYS = ("rows", "positives", "base_rate", "precision_at_k", "hubspot_precision_at_k", "points_precision_at_k",
               "lift_at_k", "lift_over_points", "auc_pr", "ece", "k", "test_rows", "challenger")
OVERRIDES = (("skipped", "Skipped"), ("promote", "Moved up"), ("demote", "Moved down"), ("worked", "Marked worked"),
             ("junk", "Junk"))
FLAG_WORDS = {"above": "above their limits", "below": "below their limits", "ok": "inside",
              "held": f"under {fb.MIN_GROUP} worked leads"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def _day(ts: Optional[str]) -> Optional[str]:
    """'2028-08-01T00:00:00' -> '1 Aug 2028'."""
    d = _parse(ts)
    return f"{d.day} {MONTHS[d.month - 1][:3]} {d.year}" if d else None


def _month(period: str) -> str:
    """'2028-10' -> 'October 2028'."""
    try:
        y, m = period.split("-")[:2]
        return f"{MONTHS[int(m) - 1]} {y}"
    except (ValueError, IndexError):
        return period


def _months(periods: Optional[list]) -> Optional[str]:
    """['2028-05', '2028-06'] -> 'May–Jun 2028'; across a year end, 'Dec 2027–Jan 2028'."""
    ps = sorted(str(p) for p in periods or [] if re.fullmatch(r"\d{4}-\d{2}", str(p)))
    if not ps:
        return None
    (y1, m1), (y2, m2) = ps[0].split("-"), ps[-1].split("-")
    a, b = MONTHS[int(m1) - 1][:3], MONTHS[int(m2) - 1][:3]
    if ps[0] == ps[-1]:
        return f"{a} {y1}"
    return f"{a}–{b} {y1}" if y1 == y2 else f"{a} {y1}–{b} {y2}"


def _and(items: list) -> str:
    items = [str(i) for i in items]
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _count(k: int) -> str:
    return COUNTS[k] if k < len(COUNTS) else str(k)


def _range(xs: list[int]) -> str:
    """[101, 102, 103] -> 'seeds 101 to 103'; [7] -> 'seed 7'; [3, 9] -> 'seeds 3 and 9'."""
    if len(xs) == 1:
        return f"seed {xs[0]}"
    if xs == list(range(xs[0], xs[-1] + 1)):
        return f"seeds {xs[0]} to {xs[-1]}"
    return f"seeds {_and(xs)}"


def _fixed(x: float, digits: int = 3) -> str:
    """Half-up, as people round: the stored metrics have four decimals, so a gap of 0.0785 reads 0.079, not the
    0.078 binary floats give."""
    return str(Decimal(repr(round(abs(x), 6))).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def _signed(x: float, digits: int = 3) -> str:
    """+0.074 / −0.037, with a real minus sign."""
    s = _fixed(x, digits)
    return ("+" if x > 0 and float(s) else "−" if x < 0 and float(s) else "") + s


def _diff(a, b) -> Optional[float]:
    return None if a is None or b is None else a - b


def _change(d: Optional[float], higher_is_better: bool = True) -> Optional[dict]:
    """A change against the live model: {word better|worse|level, text}."""
    if d is None:
        return None
    if not float(_fixed(d)):
        return {"word": "level", "text": "level"}
    good = (d > 0) == higher_is_better
    return {"word": "better" if good else "worse", "text": f"{'better' if good else 'worse'} {_signed(d)}"}


def _effect(d: Optional[float], scale: float) -> Optional[dict]:
    """One cell pair in "what the model adds": the word, the signed gap and the bar."""
    if d is None:
        return None
    word = "level" if not float(_fixed(d)) else ("better" if d > 0 else "worse")
    return {"word": word, "effect": _signed(d), "bar": min(BAR_PX, round(BAR_PX * abs(d) / scale))}


def _versions(st) -> list[dict]:
    """Registry rows, newest first, with parsed metrics (the keys the page reads default to None), approvals, the
    form token, and the facts the page prints."""
    regs = [dict(r) for r in st.conn.execute("SELECT * FROM model_registry ORDER BY trained_at DESC").fetchall()]
    for r in regs:
        m = loads(r["metrics"]) or {}
        for k in METRIC_KEYS:
            m.setdefault(k, None)
        r["metrics"] = m
        r["approvals"] = [dict(a) for a in st.conn.execute("SELECT * FROM approvals WHERE version=? ORDER BY id", (r["version"],)).fetchall()]
        r["token"] = approval_token(st.conn, r["version"])
        r["signed"] = {t: next((a for a in reversed(r["approvals"]) if a["team"] == t and a["decision"] == "approve"), None) for t in TEAMS}
        r["sent_back"] = next((a for a in reversed(r["approvals"]) if a["decision"] == "send_back"), None)
        r["trained"] = _day(r["trained_at"])
        r["family_name"] = FAMILIES.get(r["family"], r["family"] or "[NOT RECORDED]")
        r["simulated"] = "simulated" in (r["notes"] or "")
        tested = m.get("test_months") or m.get("validation_months") or []
        r["tested"] = _months(tested)
        r["tested_key"] = tuple(sorted(tested))
        bins = m.get("calibration_bins") or []
        r["test_n"] = m["test_rows"] or (sum(b["n"] for b in bins) if bins else None)
        r["test_booked"] = round(m["base_rate"] * r["test_n"]) if r["test_n"] and m["base_rate"] is not None else None
        r["split"] = {"train": _months(m.get("train_months")), "calibration": _months(m.get("calibration_months"))} if m.get("train_months") else None
        r["d_hub"] = _diff(m["precision_at_k"], m["hubspot_precision_at_k"])
        r["d_pts"] = _diff(m["precision_at_k"], m["points_precision_at_k"])
        r["bands"] = _bands(m)
        r["vs"] = None
    return regs


def _families(side: list[dict]) -> Optional[str]:
    """'All four are logistic regression; gradient boosting was trained beside each and not chosen.'"""
    if not side:
        return None
    fams: dict[str, list[str]] = {}
    for r in side:
        fams.setdefault(r["family_name"], []).append(r["version"])
    if len(fams) == 1:
        fam = next(iter(fams))
        head = f"All {_count(len(side))} are {fam}" if len(side) > 1 else f"{side[0]['version']} is {fam}"
    else:
        head = "; ".join(f"{_and(vs)} {'is' if len(vs) == 1 else 'are'} {fam}" for fam, vs in fams.items())
    rivals = {(r["metrics"]["challenger"] or {}).get("family") for r in side}
    if None in rivals or not rivals:
        return head + "."
    if len(rivals) == 1:
        rival = FAMILIES.get(next(iter(rivals)), next(iter(rivals)))
        return head + f"; {rival} was trained beside {'each' if len(side) > 1 else 'it'} and not chosen."
    return head + "; the other family was trained beside each and not chosen."


def _bands(m: dict) -> list[dict]:
    """Calibration bins as the table and the reliability plot draw them: whole percents, `few` under SMALL_BAND,
    and the point's place in the plot's 190px square (x 40..230, y 198..8)."""
    out = []
    for b in m.get("calibration_bins") or []:
        pred, obs = b.get("predicted"), b.get("observed")
        out.append({"label": f"{round(100 * b['lo'])}–{round(100 * b['hi'])}%", "n": b["n"], "few": b["n"] < SMALL_BAND,
                    "pred": pred, "obs": obs,
                    "predicted": None if pred is None else round(100 * pred), "observed": None if obs is None else round(100 * obs),
                    "cx": None if pred is None else round(40 + 190 * pred, 1), "cy": None if obs is None else round(198 - 190 * obs, 1)})
    return out


def _calibration(r: Optional[dict]) -> Optional[dict]:
    """For one version: typical (the band with the most leads), ece_points, the plot's aria-label, and `judged`
    (bands with SMALL_BAND or more leads)."""
    if not r or not r["bands"]:
        return None
    bands = [b for b in r["bands"] if b["pred"] is not None and b["obs"] is not None]
    judged = [b for b in bands if not b["few"]]
    typical = max(bands, key=lambda b: b["n"]) if bands else None
    gap = math.ceil(100 * max(abs(b["obs"] - b["pred"]) for b in judged)) if judged else None
    label = f"Reliability plot for {r['version']}."
    if judged:
        pairs = [f"{judged[0]['predicted']}% scored and {judged[0]['observed']}% booked"] + [f"{b['predicted']}% and {b['observed']}%" for b in judged[1:]]
        label += (f" In every band with {SMALL_BAND} or more leads, the share that booked is within {gap} points of the score: "
                  + ", ".join(pairs) + ".")
    ece = r["metrics"]["ece"]
    return {"typical": typical, "ece_points": None if ece is None else round(100 * ece, 1), "label": label, "judged": len(judged)}


def _plainly(current: Optional[dict], waiting: list[dict]) -> Optional[str]:
    """The plain answer to "what does the model add over the points tables", from the stored test metrics."""
    out = []
    behind = [r for r in waiting if r["d_pts"] is not None and r["d_pts"] < 0]
    ahead = [r for r in waiting if r["d_pts"] is not None and r["d_pts"] > 0]
    known = len(behind) + len(ahead)
    cands = ("" if not known else "every candidate falls behind it" if len(behind) == known else
             "every candidate is ahead of it" if len(ahead) == known else
             f"{len(behind)} of the {known} candidates fall behind it")
    if current and current["d_pts"] is not None:
        d, v = current["d_pts"], current["version"]
        if abs(d) <= CLOSE:
            first = (f"Plainly: on worked leads, our cold-start points table ranks about as well as the model. Live {v} is "
                     + (f"{'ahead of' if d > 0 else 'behind'} it by {_fixed(d)}" if float(_fixed(d)) else "level with it"))
        elif d > 0:
            first = f"On worked leads, live {v} ranks ahead of our cold-start points table by {_fixed(d)}"
        else:
            first = f"Plainly: on worked leads, our cold-start points table ranks better than live {v}, by {_fixed(d)}"
        out.append(first + (f", and {cands}." if cands else "."))
    elif cands:
        out.append(f"On worked leads, measured against our cold-start points table, {cands.replace(' it', ' the table')}.")
    lifts = [r["metrics"]["lift_at_k"] for r in ([current] if current else []) + waiting if r["metrics"]["lift_at_k"] is not None]
    if lifts:
        lo, hi = round(100 * (min(lifts) - 1)), round(100 * (max(lifts) - 1))
        every = "every version finds" if len(lifts) > 1 else "it finds"
        lead = "Against HubSpot's points, which the model replaces,"

        def span(a: int, b: int) -> str:
            return f"{a}%" if a == b else f"{a} to {b}%"
        if lo > 0:
            out.append(f"{lead} {every} {span(lo, hi)} more demos in its top K.")
        elif hi < 0:
            out.append(f"{lead} {every} {span(-hi, -lo)} fewer demos in its top K.")
        elif lo == hi == 0:
            out.append(f"{lead} {every} about as many demos in its top K.")
        elif lo == 0:
            out.append(f"{lead} {every} as many demos in its top K or up to {hi}% more.")
        elif hi == 0:
            out.append(f"{lead} {every} as many demos in its top K or up to {-lo}% fewer.")
        else:
            out.append(f"{lead} versions range from {-lo}% fewer to {hi}% more demos in their top K.")
    return " ".join(out) or None


def _tested_where(side: list[dict]) -> Optional[str]:
    """Which versions were tested where how many worked leads booked, grouped when their rates are close."""
    rated = sorted((r for r in side if r["metrics"]["base_rate"] is not None), key=lambda r: -r["metrics"]["base_rate"])
    if not rated:
        return None
    tested = rated[0]["tested"]
    if len(rated) == 1:
        on = f" on {tested}," if tested else ","
        return f"{rated[0]['version']}: tested{on.rstrip(',')} where {round(100 * rated[0]['metrics']['base_rate'])}% of worked leads booked."
    if len({r["tested_key"] for r in rated}) == 1:
        rates = {round(100 * r["metrics"]["base_rate"]) for r in rated}
        months = f" ({tested})" if tested else ""
        return f"All tested on the same months{months}, where {min(rates)}% of worked leads booked."
    groups: list[list[dict]] = [[rated[0]]]
    for r in rated[1:]:
        if groups[-1][-1]["metrics"]["base_rate"] - r["metrics"]["base_rate"] <= SAME_BASE:
            groups[-1].append(r)
        else:
            groups.append([r])
    parts = []
    for i, g in enumerate(groups):
        rates = sorted(round(100 * r["metrics"]["base_rate"]) for r in g)
        span = f"{rates[0]}%" if rates[0] == rates[-1] else f"{rates[0]} to {rates[-1]}%"
        names = _and([r["version"] for r in sorted(g, key=lambda r: r["trained_at"] or "")])
        parts.append(f"{names} where {span} of worked leads booked" if i == 0 else f"{names} where {span}")
    return "Tested on different months: " + ", ".join(parts) + "."


def _month_summary(period: str, rows: list[dict]) -> str:
    """'September 2028: referral and website above their limits; events and webinar inside; email and paid ads under 30 worked leads.'"""
    if all(r["flag"] == "held" for r in rows):
        return f"{_month(period)}: under {fb.MIN_GROUP} worked leads in every channel."
    parts = []
    for flag in ("above", "below", "ok", "held"):
        chs = [r["label"].lower() for r in rows if r["flag"] == flag]
        if chs:
            parts.append(f"{_and(chs)} {FLAG_WORDS[flag]}")
    return f"{_month(period)}: " + "; ".join(parts) + "."


def _monitor(conn, speed: dict, effect: dict) -> Optional[dict]:
    """The latest channel-monitor run: run (its log time), period (the newest month, 'October 2028'), rows (that
    month's channels, each with label, booked/predicted/lcl/ucl in whole percents), earlier (one sentence per older
    month, newest first), every (every row of the run), headline (the plain reading of the newest month)."""
    every = [dict(r) for r in conn.execute("SELECT * FROM channel_monitor WHERE created_at = (SELECT MAX(created_at) FROM channel_monitor) ORDER BY period, channel").fetchall()]
    if not every:
        return None
    for r in every:
        r["label"] = (r["channel"] or "[NOT RECORDED]").replace("_", " ").capitalize()
        r["month"] = _month(r["period"])
        r["booked"], r["predicted"], r["low"], r["high"] = (None if r[k] is None else round(100 * r[k])
                                                            for k in ("demo_rate", "predicted_rate", "lcl", "ucl"))
    periods = sorted({r["period"] for r in every})
    rows = [r for r in every if r["period"] == periods[-1]]
    judged = [r for r in rows if r["flag"] != "held"]
    above, below = [r["label"].lower() for r in judged if r["flag"] == "above"], [r["label"].lower() for r in judged if r["flag"] == "below"]
    faster = (speed.get("n") and (speed.get("baseline") or {}).get("n") and speed["median_hours"] < speed["baseline"]["median_hours"]
              and (effect.get("factor") or 1) < 1)
    if not judged:
        headline = f"No channel had {fb.MIN_GROUP} worked leads this month, so none is judged yet."
    elif above and not below:
        where = "" if len(above) == len(judged) else f" in {len(above)} of the {len(judged)} channels with enough worked leads"
        headline = f"Leads now book above their scores{where}" + (", most likely because they are touched sooner." if faster else ".")
    elif below and not above:
        headline = f"Leads in {_and(below)} now book below their scores: the model is too hopeful there."
    elif above and below:
        headline = f"The scores have drifted both ways: {_and(above)} book above them, {_and(below)} below."
    else:
        headline = "Every channel with enough worked leads books within its limits: the scores still hold."
    return {"run": every[0]["created_at"], "period": _month(periods[-1]), "rows": rows, "headline": headline,
            "earlier": [_month_summary(p, [r for r in every if r["period"] == p]) for p in reversed(periods[:-1])], "every": every}


def _overrides(conn) -> dict:
    """rows: one per kind of SDR action (all five, zero when unused) with times, leads and booked (leads whose
    outcome is a booked demo); skips, reason (the most given skip reason and how often), first_day/last_day (log
    dates, '28 Oct 2026') and total."""
    by = {r["action"]: dict(r) for r in conn.execute(
        "SELECT v.action, COUNT(*) times, COUNT(DISTINCT v.contact_id) leads, "
        "COUNT(DISTINCT CASE WHEN o.demo_booked = 1 THEN v.contact_id END) booked "
        "FROM overrides v LEFT JOIN outcomes o ON o.contact_id = v.contact_id GROUP BY v.action").fetchall()}
    rows = [{"action": a, "label": label, **{k: (by.get(a) or {}).get(k, 0) for k in ("times", "leads", "booked")}} for a, label in OVERRIDES]
    reasons = Counter(r["note"] for r in conn.execute("SELECT note FROM overrides WHERE action='skipped'").fetchall() if r["note"])
    span = conn.execute("SELECT MIN(created_at) first, MAX(created_at) last, COUNT(*) n FROM overrides").fetchone()
    return {"rows": rows, "skips": (by.get("skipped") or {}).get("times", 0), "reason": reasons.most_common(1)[0] if reasons else None,
            "first_day": _day(span["first"]), "last_day": _day(span["last"]), "total": span["n"]}


def _wait(hours: float) -> str:
    """70.8 -> 'a median of about 3 days'; 17.0 -> 'a median of 17h'."""
    return f"a median of about {round(hours / 24)} days" if hours >= 48 else f"a median of {round(hours)}h"


def model_page(st) -> dict:
    """regs: every version, newest first, each with metrics (parsed; the keys the page reads default to None),
    approvals, token (the form's `seen`), signed {marketing, sales: the latest approval or None}, sent_back (the
    send-back row or None), trained ('1 Aug 2028'), family_name, simulated (its notes say so), tested ('May–Jun 2028'),
    test_n/test_booked, split ({train, calibration} months, or None for a row from before the three-way split),
    d_hub/d_pts (precision at K minus HubSpot's points / the cold-start table), bands (calibration bins in whole
    percents, with plot points), vs (a candidate's change against the live model: lift_hub, lift_pts, ece, each
    {word, text} or None; None on other versions).

    current: the live version or None; waiting: the candidates, newest first; side: the side-by-side's columns (live,
    then candidates oldest first); sent_back, shadow_reg, retired (version names): the versions not in it.
    adds: [{version, live, k, hub, pts}], hub/pts {word, effect, bar} or None; bar_scale (a full bar); plainly (the
    plain answer to what the model adds); tested_where; families; capacity (touches a month). calibration: the live
    version's typical band, ece_points, plot label (see _calibration). monitor: see _monitor, or None. overrides: see
    _overrides. shadow: the shadow check (n, k, live/shadow {version, precision_at_k}) or None. speed (speed_to_lead),
    effect (speed_effect: n_fast, n_slow, rate_fast, rate_slow, factor, each None when unknown), late_factor (routing's
    stale_lead_factor), built_in (the generator's own factor, for the mock), learned_wait (how long the live model's
    training leads waited, when it learned only from leads before routing). began: when routing began ('1 Aug 2028',
    service clock); hit: hit_rate since then. rescoring: {version, left} while a rescore is pending, else None.
    scored: {model version: leads whose latest score it gave}. seeds_text: the simulated months' seeds, 'seeds 101 to
    103'. small_band, min_group: the thresholds the page names."""
    regs = _versions(st)
    current = next((r for r in regs if r["status"] == "live"), None)
    waiting = [r for r in regs if r["status"] == "candidate"]
    side = ([current] if current else []) + list(reversed(waiting))
    ds = [abs(d) for r in side for d in (r["d_hub"], r["d_pts"]) if d is not None]
    scale = max(FULL_BAR, math.ceil(round(max(ds, default=0) / 0.05, 6)) * 0.05)
    adds = [{"version": r["version"], "live": r is current, "k": r["metrics"]["k"],
             "hub": _effect(r["d_hub"], scale), "pts": _effect(r["d_pts"], scale)} for r in side]
    lm = current["metrics"] if current else {}
    for r in waiting:
        r["vs"] = {"lift_hub": _change(_diff(r["metrics"]["lift_at_k"], lm.get("lift_at_k"))),
                   "lift_pts": _change(_diff(r["metrics"]["lift_over_points"], lm.get("lift_over_points"))),
                   "ece": _change(_diff(r["metrics"]["ece"], lm.get("ece")), higher_is_better=False)}

    speed = fb.speed_to_lead(st.conn)
    effect = {k: None for k in ("n_fast", "n_slow", "rate_fast", "rate_slow", "factor")} | fb.speed_effect(st.conn)
    began_iso = st.conn.execute("SELECT MIN(created_at) b FROM routing_log WHERE decision IN ('assigned','holdout_assigned')").fetchone()["b"]
    began = _parse(began_iso)
    hit = hit_rate(st.conn, days=max(1, math.ceil((clock(st.conn) - began).total_seconds() / 86400))) if began else None
    learned_wait = None
    if current and began_iso and (current["trained_at"] or "") <= began_iso and speed["baseline"].get("n"):
        learned_wait = _wait(speed["baseline"]["median_hours"])

    shadow_reg = next((r for r in regs if r["status"] == "shadow"), None)
    shadow = fb.shadow_comparison(st.hs, st.conn, k=SHADOW_K) if shadow_reg else None
    if shadow:
        shadow["k"] = min(SHADOW_K, shadow["n"])
    rescoring = None
    if get_setting(st.conn, RESCORE_PENDING):
        rescoring = {"version": get_setting(st.conn, RESCORE_PENDING), "left": len(st.scorer().stale_pool())}
    scored = {r["model_version"]: r["n"] for r in st.conn.execute(
        "SELECT s.model_version, COUNT(*) n FROM scores s JOIN (SELECT MAX(id) id FROM scores GROUP BY contact_id) m ON m.id = s.id "
        "GROUP BY s.model_version ORDER BY n DESC").fetchall()}
    seeds = sorted({int(s) for r in regs if r["simulated"] for s in re.findall(r"seed (\d+)", r["notes"] or "")})
    return {"regs": regs, "current": current, "waiting": waiting, "side": side,
            "sent_back": [r for r in regs if r["status"] == "sent_back"], "shadow_reg": shadow_reg,
            "retired": [r["version"] for r in regs if r["status"] == "retired"],
            "adds": adds, "bar_scale": scale, "plainly": _plainly(current, waiting), "tested_where": _tested_where(side),
            "families": _families(side), "capacity": CAPACITY_PER_MONTH, "calibration": _calibration(current),
            "monitor": _monitor(st.conn, speed, effect), "overrides": _overrides(st.conn),
            "shadow": shadow, "speed": speed, "effect": effect, "late_factor": get_setting(st.conn, "stale_lead_factor"),
            "built_in": LATE_TOUCH_FACTOR, "learned_wait": learned_wait, "began": _day(began_iso), "hit": hit,
            "rescoring": rescoring, "scored": scored, "seeds_text": _range(seeds) if seeds else None,
            "small_band": SMALL_BAND, "min_group": fb.MIN_GROUP}
