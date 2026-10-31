"""The branded email: a draft's text in BrightPath's colours, safe to paste into an email."""
from auxocase.service.email_html import BRAND, GOLD, branded


def test_branded_email_wraps_the_draft_in_the_header_card_and_footer():
    html = branded("Hi Laura,\n\nThanks for getting in touch.\nTwo lines here.\n\nBest,\nSam", sender="Sam Ortiz")
    assert html.startswith('<table role="presentation"') and html.count("<table") == html.count("</table>")
    assert f">{BRAND}</span>" in html and GOLD in html
    assert "<p" in html and "Thanks for getting in touch.<br>Two lines here." in html   # a paragraph per blank line, a break per line
    assert "Sent by Sam Ortiz" in html
    assert "<style" not in html and "class=" not in html                                   # inline styles only: email clients drop the rest


def test_branded_email_escapes_html_and_links_urls():
    html = branded("Hi <b>Laura</b>,\n\nSee https://example.com/x. Then more.")
    assert "<b>Laura</b>" not in html and "&lt;b&gt;Laura&lt;/b&gt;" in html
    assert '<a href="https://example.com/x"' in html and ">https://example.com/x</a>." in html


def test_booking_line_becomes_a_button():
    html = branded("Hi Laura,\n\nIf it's easier, pick a time that suits you here: https://meet.example/sam\n\nBest,\nSam")
    assert '<a href="https://meet.example/sam"' in html and ">Pick a time</a>" in html
    assert f"background:{GOLD}" in html and "pick a time that suits you here:</p>" in html
