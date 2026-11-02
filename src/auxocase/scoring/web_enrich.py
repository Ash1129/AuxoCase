"""Fill a missing job title or company size from the public web, for the leads the licensed tool misses.

Half of referral and webinar leads arrive without a title or a size (BrightPath answer 1), and the
licensed tool finds a share of them (`enrich.py`). This is the second line: a reader with web search
switched on looks the lead up and answers with a value, a source URL and a quote copied word for word
from that page. The reader proposes; code decides. `verify` fetches the page itself and keeps a value
only when the quote is on the page and carries the fact: the title quote names the person and the
title, the headcount quote contains the number. Anything else is dropped as if nothing had been found,
and the lead stays blank, which the model treats as its own value (never as a junior title).

What leaves the service is the person's name, the company's name and its domain, nothing else; what
comes back is a business role and a company fact, each with the page it came from (`*_source` in
`enrichment_log.filled`). The reader never writes to HubSpot and never touches a score: the value it
finds is scored like any other value on the form.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
from dataclasses import dataclass
from html import unescape
from typing import Callable, Optional
from urllib.parse import urlsplit

from ..hubspot.models import Company, Contact
from ..llm.client import LLM

log = logging.getLogger(__name__)

SYSTEM = """You look up two public facts about a business lead for a sales team: the person's job title at the named company, and how many people the company employs. Use web search. Prefer the company's own website, its press releases and business directories (the company's LinkedIn or Crunchbase page). Report nothing about the person beyond their business role: no personal details, no opinions, no other people.

Answer with JSON only, no prose, exactly these keys:
{"job_title": string or null, "job_title_url": string or null, "job_title_quote": string or null,
 "num_employees": integer or null, "num_employees_url": string or null, "num_employees_quote": string or null}

Each quote must be copied word for word from the page at its URL, 10 to 300 characters, and must contain the fact: the title quote names the person and the title; the employee quote contains the number. If you cannot find a fact with such a quote, return null for it. Never guess and never round."""

NEED_LINES = {"job_title": "job_title (this person's current role at the company)",
              "num_employees": "num_employees (how many people the company employs)"}
MAX_EMPLOYEES = 1_000_000
QUOTE_MIN, QUOTE_MAX = 10, 300
TITLE_MAX = 80
PAGE_MAX_BYTES = 2_000_000
FETCH_TIMEOUT_S = 10

SENIORITY_RULES = (     # the vocabulary HubSpot's points and the features use; "vice president" is read before "president"
    ("vp", ("vp", "vice president", "svp", "evp")),
    ("c_level", ("chief ", "ceo", "cfo", "coo", "cto", "cio", "chro", "cmo", "founder", "co-founder", "owner", "president", "managing director", "partner")),
    ("director", ("director", "head of")),
    ("manager", ("manager", "lead", "supervisor", "controller")),
)


def seniority_of(title: str) -> str:
    t = f" {re.sub(r'[^a-z0-9& -]', ' ', title.lower())} "
    for level, words in SENIORITY_RULES:
        if any(f" {w}" in t if w.endswith(" ") else re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", t) for w in words):
            return level
    return "individual"


def prompt(contact: Contact, company: Optional[Company], need: list[str]) -> str:
    lines = ["TASK: enrich", "Need: " + "; ".join(NEED_LINES[n] for n in need)]
    if "job_title" in need:
        lines.append(f"Person: {contact.first_name} {contact.last_name}".rstrip())
    if company is not None and company.name:
        lines.append(f"Company: {company.name}" + (f" ({company.domain})" if company.domain else ""))
    elif "@" in (contact.email or ""):
        lines.append(f"Company domain: {contact.email.rsplit('@', 1)[1]}")      # the domain only, never the address
    return "\n".join(lines)


# ---------------------------------------------------------------- the check
def _norm(s: str) -> str:
    s = (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def _good_quote(q) -> bool:
    return isinstance(q, str) and QUOTE_MIN <= len(q.strip()) <= QUOTE_MAX


def parse(raw: str) -> Optional[dict]:
    """The reader's JSON, with or without a code fence; None if it is not an object."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw or "")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def safe_url(url: str, resolve: Callable = socket.getaddrinfo) -> bool:
    """http(s) to a public host: never the service's own network, a file or a loopback address."""
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower().rstrip(".")
    if u.scheme not in ("http", "https") or not host or host == "localhost" or host.endswith((".local", ".localhost", ".internal")):
        return False
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addrs = [ipaddress.ip_address(info[4][0]) for info in resolve(host, u.port or (443 if u.scheme == "https" else 80))]
        except (socket.gaierror, OSError, ValueError):
            return False
    return bool(addrs) and all(a.is_global for a in addrs)


def to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1\s*>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    return _norm(unescape(html))


def fetch_text(url: str) -> str:
    """The page's visible text, or "" when it cannot be fetched safely (then nothing is kept from it)."""
    import httpx
    if not safe_url(url):
        return ""
    try:
        with httpx.Client(timeout=FETCH_TIMEOUT_S, follow_redirects=True,
                          headers={"User-Agent": "AuxoCase enrichment check (quote verification)"}) as client:
            with client.stream("GET", url) as r:
                if r.status_code != 200 or not safe_url(str(r.url)):
                    return ""
                raw = bytearray()
                for chunk in r.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > PAGE_MAX_BYTES:
                        break
                return to_text(bytes(raw).decode(r.encoding or "utf-8", errors="replace"))
    except Exception as e:       # a slow or odd page is a miss, not a failure of the lead
        log.info("enrichment page not fetched: %s (%s)", url, type(e).__name__)
        return ""


def verify(answer: dict, contact: Contact, need: list[str], fetch: Callable[[str], str] = fetch_text) -> dict:
    """What the reader proposed and the page confirms. Each kept value names its page (`<field>_source`)."""
    found: dict = {}
    pages: dict[str, str] = {}

    def page(url: str) -> str:
        if url not in pages:
            pages[url] = _norm(fetch(url))
        return pages[url]

    if "job_title" in need:
        title, url, quote = answer.get("job_title"), answer.get("job_title_url"), answer.get("job_title_quote")
        surname = _norm(contact.last_name)
        if (isinstance(title, str) and 2 <= len(title.strip()) <= TITLE_MAX and "@" not in title and not re.search(r"https?:|\n", title)
                and _good_quote(quote) and isinstance(url, str) and surname):
            q = _norm(quote)
            if _norm(title) in q and surname in q and q in page(url):
                found.update(job_title=title.strip(), seniority=seniority_of(title), job_title_source=url)
    if "num_employees" in need:
        n, url, quote = answer.get("num_employees"), answer.get("num_employees_url"), answer.get("num_employees_quote")
        if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= MAX_EMPLOYEES and _good_quote(quote) and isinstance(url, str):
            q = _norm(quote)
            digits = re.sub(r"(?<=\d)[,.](?=\d{3}\b)", "", q)        # 1,200 and 1.200 are 1200
            if re.search(rf"(?<!\d){n}(?!\d)", digits) and q in page(url):
                found.update(num_employees=n, num_employees_source=url)
    return found


# ---------------------------------------------------------------- the enrichers
@dataclass
class WebEnricher:
    """Asks the reader (web search on) and keeps only what `verify` confirms on the page."""
    llm: LLM
    fetch: Callable[[str], str] = fetch_text
    name: str = "web"

    def lookup(self, contact: Contact, company: Optional[Company], need: list[str]) -> dict:
        try:
            raw = self.llm.complete(SYSTEM, prompt(contact, company, need), max_tokens=400)
        except Exception as e:       # the reader being down is a miss for this lead; the pipeline carries on
            log.warning("web enrichment failed for %s: %s", contact.id, e)
            return {}
        answer = parse(raw)
        return verify(answer, contact, need, self.fetch) if answer else {}


class ChainEnricher:
    """The licensed tool first, then the web for what it did not find. Named after its parts (`simulated+web`)."""

    def __init__(self, enrichers: list):
        self.enrichers = list(enrichers)
        self.name = "+".join(e.name for e in self.enrichers)

    def lookup(self, contact: Contact, company: Optional[Company], need: list[str]) -> dict:
        found: dict = {}
        left = list(need)
        for e in self.enrichers:
            if not left:
                break
            got = e.lookup(contact, company, left) or {}
            found.update(got)
            left = [n for n in left if n not in got]
        return found
