"""The branded email: a draft's plain text set in BrightPath's colours, as the lead will see it.

The drafts are written and checked as plain text (llm/emails.py, replies.py, callnotes.py); this wraps one for
sending. A mailto link cannot carry HTML, so the lead page shows this rendering and a button that puts it on the
clipboard as rich text: pasted into the message the email app opens, the header, colours and button come with it.

Email clients ignore stylesheets, so everything is tables and inline styles. The palette is the one the service's
pages use (base.html's light tokens, from ashwinprajeeth.com): a near-black header band with the brand in gold, a
white card for the words on the site's grey, the booking link as a gold button, a muted footer.
"""
from __future__ import annotations

import re
from html import escape

BRAND = "BrightPath Payroll"
TAGLINE = "Payroll software for small and mid-sized businesses"

BG = "#f3f2f2"          # the page behind the card
CARD = "#ffffff"
LINE = "#d7d3d3"
INK = "#201f1d"
MUTED = "#605d5d"
BAND = "#1a1919"        # the header band
BAND_MUTED = "#aeaba3"
GOLD = "#b68235"        # the accent, buttons and rules
GOLD_LIGHT = "#e1ad66"  # the brand name on the band
GOLD_DEEP = "#7d5411"   # links on white

HEADING = "'Cormorant Garamond', Georgia, 'Times New Roman', serif"
BODY = "Lora, Georgia, 'Times New Roman', serif"

URL = re.compile(r"https?://[^\s<>\"]+")
BOOKING = re.compile(r"^(?P<lead>.*?(?:here|time|calendar|link)[^:]*):\s*(?P<url>https?://\S+)\s*$", re.I)
BUTTON_WORDS = "Pick a time"


def _line(text: str) -> str:
    """One line of the draft as HTML: escaped, with any link made clickable."""
    out, at = [], 0
    for m in URL.finditer(text):
        out.append(escape(text[at:m.start()]))
        url = m.group(0).rstrip(".,;:)")
        tail = m.group(0)[len(url):]
        out.append(f'<a href="{escape(url, quote=True)}" style="color:{GOLD_DEEP};text-decoration:underline">{escape(url)}</a>{escape(tail)}')
        at = m.end()
    out.append(escape(text[at:]))
    return "".join(out)


def _paragraph(block: str) -> str:
    p_style = f"margin:0 0 16px 0;font-family:{BODY};font-size:15px;line-height:1.6;color:{INK}"
    m = BOOKING.match(block.strip())
    if m:
        # The booking line becomes the sentence and a button, so the lead can pick a time with one click.
        return (f'<p style="{p_style}">{escape(m.group("lead"))}:</p>'
                f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:auto;margin:0 0 20px 0"><tr>'
                f'<td style="background:{GOLD};border-radius:4px">'
                f'<a href="{escape(m.group("url"), quote=True)}" style="display:inline-block;padding:11px 20px;font-family:{BODY};font-size:15px;'
                f'font-weight:600;line-height:20px;color:#ffffff;text-decoration:none">{BUTTON_WORDS}</a></td></tr></table>')
    lines = [_line(ln) for ln in block.split("\n")]
    return f'<p style="{p_style}">{"<br>".join(lines)}</p>'


def branded(body: str, sender: str = "") -> str:
    """The draft's body as a branded HTML email. `sender` is the SDR's name for the footer, when known."""
    blocks = [b for b in re.split(r"\n\s*\n", body.strip()) if b.strip()]
    words = "".join(_paragraph(b) for b in blocks)
    signed = f" · Sent by {escape(sender)}" if sender else ""
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background:{BG};margin:0;padding:0">'
        f'<tr><td align="center" style="padding:24px 12px">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%">'
        # the header band
        f'<tr><td style="background:{BAND};padding:18px 28px 16px 28px;border-radius:6px 6px 0 0">'
        f'<span style="font-family:{HEADING};font-size:24px;line-height:28px;font-weight:600;color:{GOLD_LIGHT}">{BRAND}</span><br>'
        f'<span style="font-family:{BODY};font-size:12px;line-height:18px;color:{BAND_MUTED}">{TAGLINE}</span>'
        f'</td></tr>'
        f'<tr><td style="background:{GOLD};height:3px;line-height:3px;font-size:3px">&nbsp;</td></tr>'
        # the words
        f'<tr><td style="background:{CARD};border:1px solid {LINE};border-top:none;padding:28px 28px 12px 28px;border-radius:0 0 6px 6px">'
        f'{words}'
        f'</td></tr>'
        # the footer
        f'<tr><td style="padding:16px 8px 0 8px;font-family:{BODY};font-size:11px;line-height:16px;color:{MUTED};text-align:center">'
        f'{BRAND} · {TAGLINE.lower()}{signed}'
        f'</td></tr>'
        f'</table></td></tr></table>'
    )
