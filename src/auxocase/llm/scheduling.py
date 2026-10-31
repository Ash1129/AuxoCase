"""The back and forth of booking a demo, done for the SDR: times offered, the lead's pick read, the meeting confirmed.

The first email carries the SDR's booking link; most leads who want a demo still answer in words ("next week?",
"Tuesday at 2 works"). This module is the words side: three free times from the SDR's calendar to offer, the time
a lead picked read from their reply, and the short emails that go back (the offer, the confirmation, or new times
when the pick clashes). routing/scheduling.py does the booking itself and sends what is written here.

Times are offered in the display zone (service/tz.py, Eastern by default) on working days, and stored as the
service stores every time, naive UTC. Offline the pick is read with patterns (a weekday, a date, a time, "the second
one"); with Claude on, triage returns the pick as `when` and the patterns stand in when it does not. The reply is
the lead's text: data, never instructions.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from ..service import tz

OFFER = 3                                  # times offered at a time
SLOT_HOURS = (10, 14)                      # local hours offered, each working day
SLOT_MINUTES = 30
LEAD_HOURS = 20                            # nothing offered sooner than this from now
DAYS_AHEAD = 10                            # how far the offer looks
WEEKDAYS = "monday tuesday wednesday thursday friday saturday sunday".split()
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()

DAY_WORD = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)(?:day|nesday|rsday|urday|sday)?\b", re.I)
DATE_WORDS = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|"
                        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b|"
                        r"\bthe (\d{1,2})(?:st|nd|rd|th)\b", re.I)
TIME_WORDS = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b|\b(?:at\s+)(\d{1,2})(?::(\d{2}))?\b(?!\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|st|nd|rd|th|people|ppl))|\b(\d{1,2}):(\d{2})\b", re.I)
ORDINAL = re.compile(r"\b(?:the\s+)?(first|second|third|1st|2nd|3rd)\s+(?:one|option|slot|time)\b|\b(?:option|slot|time)\s+([123])\b", re.I)
NOON = re.compile(r"\b(noon|midday)\b", re.I)


def local(dt: datetime) -> datetime:
    return tz.display(dt)


def utc_naive(dt: datetime) -> datetime:
    """A zone-aware local time as the service stores it."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def slot_text(dt: datetime) -> str:
    """'Tue 4 Nov, 14:00 ET' for a stored (naive UTC) time."""
    d = local(dt)
    return f"{d:%a} {d.day} {d:%b}, {d:%H:%M} {tz.LABEL}"


def slot_day_text(dt: datetime) -> str:
    d = local(dt)
    return f"{d:%A} {d.day} {d:%B} at {d:%H:%M} {tz.LABEL}"


def free(start: datetime, busy: Sequence[datetime]) -> bool:
    return all(abs((start - b).total_seconds()) >= SLOT_MINUTES * 60 for b in busy)


def slots(busy: Sequence[datetime], now: datetime, n: int = OFFER) -> list[datetime]:
    """The next `n` free times to offer: working days from tomorrow, at SLOT_HOURS local, not sooner than LEAD_HOURS
    away and not within a slot of a meeting the SDR has. Stored times (naive UTC)."""
    out: list[datetime] = []
    today = local(now).date()
    earliest = now + timedelta(hours=LEAD_HOURS)
    for d in range(1, DAYS_AHEAD + 1):
        day = today + timedelta(days=d)
        if day.weekday() >= 5:
            continue
        for h in SLOT_HOURS:
            start = utc_naive(datetime(day.year, day.month, day.day, h, 0, tzinfo=tz.TZ))
            if start >= earliest and free(start, busy):
                out.append(start)
                if len(out) == n:
                    return out
    return out


def _hour(h: int, minute: int, ampm: str) -> Optional[tuple[int, int]]:
    ampm = ampm.replace(".", "").lower()
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    elif not ampm and h < 8:
        h += 12                                     # "at 2": the afternoon, on a working day
    return (h, minute) if 0 <= h < 24 and 0 <= minute < 60 else None


def picked_time(text: str, now: datetime, offered: Sequence[datetime] = ()) -> Optional[datetime]:
    """The time the lead picked, as a stored (naive UTC) time, or None when the reply names none. Reads "the second
    one" against the times offered, a weekday or a date with a time, or a time alone when it matches an offer."""
    text = text or ""
    m = ORDINAL.search(text)
    if m and offered:
        i = {"first": 0, "second": 1, "third": 2, "1st": 0, "2nd": 1, "3rd": 2}.get((m.group(1) or "").lower(), int(m.group(2) or 1) - 1)
        return offered[i] if 0 <= i < len(offered) else None
    here = local(now)
    day = None
    m = DAY_WORD.search(text)
    if m:
        wd = WEEKDAYS.index(next(w for w in WEEKDAYS if w.startswith(m.group(1).lower())))
        ahead = (wd - here.weekday()) % 7 or 7
        day = (here + timedelta(days=ahead)).date()
    elif re.search(r"\btomorrow\b", text, re.I):
        day = (here + timedelta(days=1)).date()
    elif re.search(r"\btoday\b", text, re.I):
        day = here.date()
    else:
        m = DATE_WORDS.search(text)
        if m:
            if m.group(5):
                dnum, mon = int(m.group(5)), here.month
                if dnum < here.day:
                    mon = mon % 12 + 1
            else:
                dnum = int(m.group(1) or m.group(4))
                mon = MONTHS.index((m.group(2) or m.group(3)).lower()[:3]) + 1
            year = here.year + (1 if mon < here.month else 0)
            try:
                day = datetime(year, mon, dnum).date()
            except ValueError:
                day = None
    hm = None
    if NOON.search(text):
        hm = (12, 0)
    else:
        m = TIME_WORDS.search(text)
        if m:
            if m.group(1):
                hm = _hour(int(m.group(1)), int(m.group(2) or 0), m.group(3))
            elif m.group(4):
                hm = _hour(int(m.group(4)), int(m.group(5) or 0), "")
            else:
                hm = _hour(int(m.group(6)), int(m.group(7)), "")
    if day and hm:
        start = utc_naive(datetime(day.year, day.month, day.day, hm[0], hm[1], tzinfo=tz.TZ))
        return start if start > now else None
    if hm and not day:
        for o in offered:
            if (local(o).hour, local(o).minute) == hm:
                return o
    if day and not hm:
        for o in offered:
            if local(o).date() == day:
                return o
    return None


def _wrap(first_name: str, sender: str, middle: str) -> str:
    return f"Hi {first_name or 'there'},\n\n{middle}\n\nThanks,\n{sender}"


def offer_body(first_name: str, sender: str, times: Sequence[datetime], link: str, after_clash: bool = False) -> str:
    listed = "; ".join(slot_text(t) for t in times)
    lead = "That time is taken on my side, sorry. " if after_clash else "Great, thanks for coming back to me. "
    pick = f" Or pick any time here: {link}" if link else ""
    return _wrap(first_name, sender, f"{lead}Would one of these work for a 30-minute walk-through of BrightPath? {listed}. Reply with the one that suits.{pick}")


def confirm_body(first_name: str, sender: str, start: datetime) -> str:
    return _wrap(first_name, sender, f"Booked: {slot_day_text(start)}, 30 minutes. The calendar invite follows from HubSpot. If it needs moving, reply here and I will find another time.")


def re_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else (f"Re: {subject}" if subject else "Re: BrightPath Payroll")
