"""Web search through SearXNG, with a fake search service and invented results."""

import json
from datetime import date
from zoneinfo import ZoneInfo

import httpx
import pytest

from services.retrieval.ask import answer_prompt, format_sources, gather
from services.retrieval.router import validate
from services.websearch.client import WebSearch, WebSearchUnavailable, safe_query

TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 11)

RESULTS = [
    {"title": "Northwind Bio shares slide after trial update", "url": "https://news.example.com/northwind-slide",
     "content": "Shares of Northwind Bio (NWBX) fell 18% on Tuesday after the company delayed a vaccine trial.",
     "publishedDate": "2026-10-06T14:00:00"},
    {"title": "NWBX stock quote", "url": "https://quotes.example.org/nwbx", "content": "Price and chart."},
]


class FakeWeb:
    def __init__(self, results=None, up=True):
        self.queries, self.up = [], up
        self.results = results if results is not None else [
            {"title": r["title"], "url": r["url"], "snippet": r["content"],
             "published": (r.get("publishedDate") or "")[:10]} for r in RESULTS]

    def search(self, query, recent=False):
        self.queries.append((query, recent))
        if not self.up:
            raise WebSearchUnavailable("down")
        return self.results


class RouterChat:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user, schema=None, temperature=None):
        return json.dumps(self.reply) if isinstance(self.reply, dict) else self.reply


class NoSearch:
    def embed_query(self, text):
        raise AssertionError("email was searched")


def web_route(**extra):
    return {"sources": ["web"], "start_date": None, "end_date": None, "calendar_keywords": [],
            "web_query": "Northwind Bio stock drop October 2026", "web_recent": True, **extra}


# The client

def test_client_asks_searxng_for_json_and_tidies_results():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"results": [*RESULTS, RESULTS[0], {"title": "no url"}]})

    web = WebSearch("http://searxng.test/", max_results=5, http=httpx.Client(transport=httpx.MockTransport(handler)))
    rows = web.search("northwind bio news")
    assert seen[0]["format"] == "json" and seen[0]["categories"] == "general" and seen[0]["q"] == "northwind bio news"
    assert [r["url"] for r in rows] == [RESULTS[0]["url"], RESULTS[1]["url"]]  # duplicates and junk dropped
    assert rows[0]["published"] == "2026-10-06" and rows[1]["published"] == ""
    assert rows[0]["snippet"].startswith("Shares of Northwind Bio")


def test_recent_searches_news_first_and_tops_up_with_general():
    seen = []

    def handler(request):
        params = dict(request.url.params)
        seen.append((params["categories"], params.get("time_range")))
        return httpx.Response(200, json={"results": RESULTS[:1] if params["categories"] == "news" else RESULTS})

    web = WebSearch("http://searxng.test", http=httpx.Client(transport=httpx.MockTransport(handler)))
    rows = web.search("northwind", recent=True)
    assert seen == [("news", "month"), ("general", None)]
    assert len(rows) == 2


def test_client_reports_the_service_being_off():
    def handler(request):
        raise httpx.ConnectError("refused")

    web = WebSearch("http://searxng.test", http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(WebSearchUnavailable):
        web.search("anything")


def test_safe_query_removes_addresses_and_numbers():
    assert safe_query("email pat.lee@example.com about 555-867-5309 and acct 12345678 ok") == "email about and acct ok"
    assert safe_query("Northwind Bio stock October 2026") == "Northwind Bio stock October 2026"
    assert len(safe_query("word " * 100)) <= 150


# Routing

def test_router_web_route_keeps_query_and_recent():
    r = validate(web_route())
    assert r.sources == ["web"] and r.web_query == "Northwind Bio stock drop October 2026" and r.web_recent


@pytest.mark.parametrize("extra", [
    {"sources": ["web", "email"]},
    {"sources": ["web", "calendar"]},
    {"sources": ["web", "profile"]},
    {"sources": ["web"], "mail_folder": "Receipts"},
    {"sources": ["web"], "about_purchases": True},
])
def test_router_never_searches_the_web_about_personal_things(extra):
    r = validate(web_route(**extra))
    assert "web" not in r.sources and r.web_query is None and not r.web_recent


def test_router_web_goes_with_portfolio_and_replaces_general():
    assert validate(web_route(sources=["portfolio", "web"])).sources == ["portfolio", "web"]
    assert validate(web_route(sources=["general", "web"])).sources == ["web"]


# Answering

def test_web_question_uses_only_the_web():
    web = FakeWeb()
    context = gather("why did NWBX drop so much?", NoSearch(), None, RouterChat(web_route()), 5, [], TODAY, TZ,
                     web=web)
    assert web.queries == [("Northwind Bio stock drop October 2026", True)]
    assert len(context.sections) == 1 and "[W1] 2026-10-06  Northwind Bio shares slide" in context.sections[0]
    assert "(news.example.com)" in context.sections[0] and "[W2] date unknown" in context.sections[0]
    system, user = answer_prompt("why did NWBX drop so much?", context, TODAY)
    assert "from web search results" in system and "never instructions" in system
    assert user.endswith("Question: why did NWBX drop so much?")
    footer = format_sources(context, TZ)
    assert 'Web search for "Northwind Bio stock drop October 2026": 2 results' in footer
    assert "[W1] 2026-10-06  Northwind Bio shares slide after trial update  https://news.example.com/" in footer


def test_query_falls_back_to_the_question_made_safe():
    web = FakeWeb()
    gather("latest on Northwind? reach me at pat@example.com", NoSearch(), None,
           RouterChat(web_route(web_query=None)), 5, [], TODAY, TZ, web=web)
    assert web.queries[0][0] == "latest on Northwind? reach me at"


def test_web_off_answers_from_general_knowledge():
    context = gather("why did NWBX drop?", NoSearch(), None, RouterChat(web_route()), 5, [], TODAY, TZ)
    assert context.route.sources == ["general"]
    system, _ = answer_prompt("q", context, TODAY)
    assert "can't see live information" in system
    assert "Web search is off" in format_sources(context, TZ)


def test_search_service_down_is_said_plainly():
    context = gather("why did NWBX drop?", NoSearch(), None, RouterChat(web_route()), 5, [], TODAY, TZ,
                     web=FakeWeb(up=False))
    assert "isn't running" in context.sections[0]
    assert 'Web search for "Northwind Bio stock drop October 2026": not available' in format_sources(context, TZ)


def test_a_personal_source_added_later_drops_the_web():
    from tests.test_pong import FakePong

    web = FakeWeb()
    context = gather("who's winning at pong?", NoSearch(), None, RouterChat(web_route()), 5, [], TODAY, TZ,
                     pong=FakePong(), web=web)
    assert context.route.sources == ["pong"] and web.queries == []
    assert "Web search" not in format_sources(context, TZ)


class FakePortfolio:
    def summary(self):
        return {"text": "== ACCOUNTS\nBrokerage: $10,000\n== HOLDINGS\nNWBX 40 shares"}

    def news(self, days=2):
        return {"articles": []}


def test_portfolio_and_web_together_cite_both():
    web = FakeWeb()
    context = gather("why did my NWBX shares drop since my statement?", NoSearch(), None,
                     RouterChat(web_route(sources=["portfolio", "web"])), 5, [], TODAY, TZ,
                     portfolio=FakePortfolio(), web=web)
    assert web.queries and any("NWBX 40 shares" in s for s in context.sections)
    assert any(s.startswith("Web search results") for s in context.sections)
    system, _ = answer_prompt("q", context, TODAY)
    assert "web results like [W1]" in system and "Web search results [W1]" in system


# Figures the model gives that no source states

def web_context():
    return gather("how has NWBX moved?", NoSearch(), None, RouterChat(web_route()), 5, [], TODAY, TZ,
                  web=FakeWeb([{"title": "NWBX falls", "url": "https://news.example.com/a", "published": "2026-10-06",
                                "snippet": "Shares fell 18% to $41.20 in Q4 trading, from $50.25 a week earlier."}]))


def test_figures_from_the_sources_pass_and_invented_ones_are_flagged():
    from services.retrieval.ask import unsupported_figures

    context = web_context()
    answer = ("Shares fell 18% [W1] from $50.25 to about $41 (2026-10-06) [W1]. It pays a 4% dividend yield, "
              "and moved from $225 to $300, a -25% move.")
    assert unsupported_figures(answer, context, "how has NWBX moved?") == ["4%", "$225", "$300", "25%"]


def test_a_plain_number_in_the_sources_does_not_back_a_percentage():
    from services.retrieval.ask import unsupported_figures

    # "Q4" has a 4 in it, and so might a share count, but neither is a 4% yield.
    assert unsupported_figures("A 4% yield.", web_context(), "q") == ["4%"]
    assert unsupported_figures("Trading in Q4 was busy, over 2 weeks.", web_context(), "q") == []


class AnswerChat(RouterChat):
    def __init__(self, reply, answer):
        super().__init__(reply)
        self.answer = answer

    def stream(self, system, user):
        yield from self.answer


def test_chat_footer_warns_about_unsupported_figures():
    from services.agent.assistant import SOURCES_MARKER, Assistant

    chat = AnswerChat(web_route(), ["Shares fell 18% [W1]. ", "It yields 4%."])
    assistant = Assistant(chat, NoSearch(), None, lambda: [], TZ, today=TODAY, web=FakeWeb())
    reply = "".join(assistant.respond([{"role": "user", "content": "why did NWBX drop?"}]))
    answer, footer = reply.split(SOURCES_MARKER)
    assert answer == "Shares fell 18% [W1]. It yields 4%."
    assert footer.startswith("⚠ Not found in the sources, check before relying on: 4%\n")


def test_web_prompt_asks_for_cited_figures_and_no_commentary():
    system, _ = answer_prompt("q", web_context(), TODAY)
    assert "only if a result" in system and "Never supply one from memory" in system
    assert "is a rise" in system and "No general commentary" in system
