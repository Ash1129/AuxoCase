"""The web lookup: the reader proposes a value with a page and a quote; code keeps it only if the page says so."""
import json

import pytest

from auxocase.data.generate import generate
from auxocase.hubspot.mock import MockHubSpot
from auxocase.hubspot.models import Company, Contact
from auxocase.llm.client import ClaudeCLI, Offline
from auxocase.pool import pool_contacts
from auxocase.scoring.enrich import NoEnricher, SimulatedEnricher, enrich_lead, make_enricher
from auxocase.scoring.web_enrich import ChainEnricher, WebEnricher, parse, prompt, safe_url, seniority_of, to_text, verify
from auxocase.storage.db import connect

PAGE = ("https://www.acme-payroll.example/about", "About Acme. Priya Natarajan, VP of People Operations, joined in 2021. "
        "Acme employs 1,250 people across three offices.")


class FakeLLM:
    name = "fake"

    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def complete(self, system, user, max_tokens=600):
        self.prompts.append(user)
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


def fetch(url):
    return PAGE[1] if url == PAGE[0] else ""


def lead(last="Natarajan"):
    c = Contact(id="c1", email="priya@acme-payroll.example", first_name="Priya", last_name=last, company_id="co1")
    return c, Company(id="co1", name="Acme Payroll", domain="acme-payroll.example")


def answer(**over):
    a = {"job_title": "VP of People Operations", "job_title_url": PAGE[0],
         "job_title_quote": "Priya Natarajan, VP of People Operations, joined in 2021",
         "num_employees": 1250, "num_employees_url": PAGE[0], "num_employees_quote": "Acme employs 1,250 people across three offices"}
    a.update(over)
    return a


def test_keeps_a_value_only_when_the_quote_is_on_the_page_and_carries_the_fact():
    c, co = lead()
    found = WebEnricher(FakeLLM(answer()), fetch=fetch).lookup(c, co, ["job_title", "num_employees"])
    assert found["job_title"] == "VP of People Operations" and found["seniority"] == "vp" and found["num_employees"] == 1250
    assert found["job_title_source"] == PAGE[0] and found["num_employees_source"] == PAGE[0]
    # the quote is not on the page
    assert verify(answer(job_title_quote="Priya Natarajan, VP of People Operations, joined in 2019"), c, ["job_title"], fetch) == {}
    # the quote is on the page but does not name the person, or not the title
    assert verify(answer(job_title_quote="Acme employs 1,250 people across three offices"), c, ["job_title"], fetch) == {}
    assert verify(answer(job_title="Chief People Officer"), c, ["job_title"], fetch) == {}
    assert verify(answer(), lead("Rao")[0], ["job_title"], fetch) == {}
    # the number must be in the quote, as a number
    assert verify(answer(num_employees=1200), c, ["num_employees"], fetch) == {}
    assert verify(answer(num_employees=True), c, ["num_employees"], fetch) == {}
    assert verify(answer(num_employees=0), c, ["num_employees"], fetch) == {}
    assert verify(answer(num_employees="1250"), c, ["num_employees"], fetch) == {}
    # a page that cannot be fetched keeps nothing
    assert verify(answer(num_employees_url="https://elsewhere.example/x"), c, ["num_employees"], fetch) == {}
    # only what was asked for
    assert set(verify(answer(), c, ["num_employees"], fetch)) == {"num_employees", "num_employees_source"}


def test_the_reader_s_mistakes_are_a_miss_not_a_failure():
    c, co = lead()
    for raw in ("not json", "[]", "null", "", "```json\n{\"job_title\": null}\n```"):
        assert WebEnricher(FakeLLM(raw), fetch=fetch).lookup(c, co, ["job_title", "num_employees"]) == {}
    assert parse("```json\n" + json.dumps(answer()) + "\n```") == answer()

    class Down:
        name = "down"

        def complete(self, *a, **k):
            raise RuntimeError("no reader")
    assert WebEnricher(Down(), fetch=fetch).lookup(c, co, ["job_title"]) == {}
    assert WebEnricher(Offline(), fetch=fetch).lookup(c, co, ["job_title", "num_employees"]) == {}      # no web offline


def test_the_prompt_carries_the_name_company_and_domain_only():
    c, co = lead()
    p = prompt(c, co, ["job_title", "num_employees"])
    assert p.startswith("TASK: enrich") and "Priya Natarajan" in p and "Acme Payroll (acme-payroll.example)" in p
    assert "priya@" not in p and c.id not in p
    assert "Person:" not in prompt(c, co, ["num_employees"])                     # a company fact needs no name
    assert "Company domain: acme-payroll.example" in prompt(c, None, ["job_title"])


def test_only_public_hosts_are_fetched():
    public = lambda host, port: [(None, None, None, None, ("93.184.216.34", 0))]
    private = lambda host, port: [(None, None, None, None, ("10.0.0.5", 0))]
    assert safe_url("https://www.example.com/about", resolve=public)
    assert not safe_url("https://intranet.example.com/", resolve=private)
    for bad in ("http://localhost:8002/", "http://127.0.0.1/", "http://10.1.2.3/x", "http://169.254.169.254/latest", "file:///etc/passwd",
                "ftp://example.com/", "http://host.internal/", "not a url"):
        assert not safe_url(bad, resolve=public), bad
    assert to_text("<html><script>x=1</script><p>Acme&nbsp;employs <b>1,250</b> people.</p></html>") == "acme employs 1,250 people."


@pytest.mark.parametrize("title,level", [("Chief Financial Officer", "c_level"), ("CEO & Co-founder", "c_level"), ("VP of People Operations", "vp"),
                                         ("Senior Vice President, Finance", "vp"), ("Director of HR", "director"), ("Head of Payroll", "director"),
                                         ("Payroll Manager", "manager"), ("Team Lead", "manager"), ("Payroll Specialist", "individual"),
                                         ("Coordinator", "individual")])
def test_seniority_from_a_title(title, level):
    assert seniority_of(title) == level


def test_the_chain_asks_the_web_only_for_what_the_tool_missed():
    class Tool:
        name = "tool"

        def lookup(self, contact, company, need):
            return {"num_employees": 300} if "num_employees" in need else {}
    web = FakeLLM(answer())
    chain = ChainEnricher([Tool(), WebEnricher(web, fetch=fetch)])
    c, co = lead()
    found = chain.lookup(c, co, ["job_title", "num_employees"])
    assert chain.name == "tool+web" and found["num_employees"] == 300 and found["job_title"] == "VP of People Operations"
    assert len(web.prompts) == 1 and "num_employees" not in web.prompts[0]
    found_all = ChainEnricher([WebEnricher(FakeLLM(answer()), fetch=fetch), Tool()]).lookup(c, co, ["num_employees"])
    assert found_all["num_employees"] == 1250                                     # the first to answer wins


def test_make_enricher_reads_the_setting(tmp_path):
    conn = connect(tmp_path / "a.db")
    assert isinstance(make_enricher(conn, "none"), NoEnricher)
    assert isinstance(make_enricher(conn), NoEnricher)                           # no generated data, nothing set
    assert isinstance(make_enricher(conn, "web", llm=Offline()), WebEnricher)
    chain = make_enricher(conn, "simulated, web", llm=Offline())
    assert isinstance(chain, ChainEnricher) and chain.name == "simulated+web"
    assert isinstance(chain.enrichers[0], SimulatedEnricher)
    with pytest.raises(ValueError):
        make_enricher(conn, "clearbit")


def test_the_cli_switches_search_on_for_the_lookup_only(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cli = ClaudeCLI(model="sonnet", draft_model="opus")
    web = cli.command("claude", "s", "TASK: enrich\nNeed: job_title")
    other = cli.command("claude", "s", "TASK: signals\nnotes")
    assert web[web.index("--tools") + 1] == "WebSearch,WebFetch" and "--restricted" in web
    assert other[other.index("--tools") + 1] == ""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    bare = cli.command("claude", "s", "TASK: enrich\nNeed: job_title")
    assert "--bare" in bare and bare[bare.index("--tools") + 1] == "WebSearch,WebFetch"


def test_enrich_lead_writes_the_web_s_answer_back_and_logs_the_provider(tmp_path):
    conn = connect(tmp_path / "w.db")
    hs = MockHubSpot(conn, faithful_delivery=True)
    hs.ensure_properties()
    generate(conn, months=2, seed=5)
    c = next(x for x in pool_contacts(hs, conn) if not x.job_title and x.company_id)
    co = hs.get_company(c.company_id)
    url = "https://example-co.example/team"
    quote = f"{c.first_name} {c.last_name} is Director of Finance"
    llm = FakeLLM({"job_title": "Director of Finance", "job_title_url": url, "job_title_quote": quote,
                   "num_employees": None, "num_employees_url": None, "num_employees_quote": None})
    enricher = ChainEnricher([NoEnricher(), WebEnricher(llm, fetch=lambda u: f"Our team. {quote}. Since 2010." if u == url else "")])
    c1, _, filled = enrich_lead(hs, conn, enricher, c, co)
    assert c1.job_title == "Director of Finance" and c1.seniority == "director"
    assert hs.get_contact(c.id).job_title == "Director of Finance" and hs.get_contact(c.id).seniority == "director"
    row = conn.execute("SELECT provider, filled FROM enrichment_log WHERE contact_id=?", (c.id,)).fetchone()
    assert row["provider"] == "none+web" and json.loads(row["filled"])["job_title_source"] == url
    assert enrich_lead(hs, conn, enricher, c, co)[2] == filled and len(llm.prompts) == 1           # asked once, ever
