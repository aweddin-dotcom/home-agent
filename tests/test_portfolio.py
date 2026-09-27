"""Portfolio Analyzer integration, with a fake app and invented numbers."""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from services.portfolio.client import PortfolioClient, PortfolioUnavailable
from services.portfolio.digest import investment_lines, mover_lines, news_lines, statement_lines, watchlist_lines
from services.retrieval.ask import answer_prompt, format_sources, gather
from services.retrieval.router import validate

TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 27)

DIGEST = {
    "total_value": 120000,
    "accounts": [{"broker": "schwab", "account_type": "Brokerage", "statement_date": "2026-08-31", "total_value": 100000},
                 {"broker": "vanguard", "account_type": "Roth IRA", "statement_date": "2026-07-31", "total_value": 20000}],
    "watchlist_alerts": [{"ticker": "AAPL", "label": "Apple", "zone": "buy", "price": 204.0, "target": 200.0,
                          "pct_from_target": 2.0},
                         {"ticker": "NVDA", "label": None, "zone": "sell", "price": 140.0, "target": 150.0,
                          "pct_from_target": -6.7}],
    "movers": [{"ticker": "SPY", "statement_date": "2026-08-31", "price": 605.0, "pct": 10.0},
               {"ticker": "TLT", "statement_date": "2026-08-31", "price": 80.0, "pct": -6.2}],
}
NEWS = {"articles": [
    {"title": "SPY hits a record", "summary": "Stocks rallied.", "publisher": "Wire", "published": "2026-09-27T12:00:00Z",
     "tickers": ["SPY"], "about_holdings": ["SPY"]},
    {"title": "Apple unveils a gadget", "summary": "", "publisher": "Wire", "published": "2026-09-26T12:00:00Z",
     "tickers": ["AAPL"], "about_holdings": []},
]}
SUMMARY = {"text": "== ACCOUNTS  (total portfolio: $120,000) ==\n  Brokerage | schwab | $100,000\n== HOLDINGS ==\n  SPY ..."}


class FakePortfolio:
    def __init__(self, up=True):
        self.up = up
        self.calls = []

    def _answer(self, name, value):
        self.calls.append(name)
        if not self.up:
            raise PortfolioUnavailable("down")
        return value

    def summary(self):
        return self._answer("summary", SUMMARY)

    def digest(self):
        return self._answer("digest", DIGEST)

    def news(self, days=2):
        return self._answer("news", NEWS)


# --- client -----------------------------------------------------------------------


def test_client_reads_the_endpoints_and_reports_outages():
    def handler(request):
        paths = {"/api/home-agent/summary": SUMMARY, "/api/home-agent/digest": DIGEST,
                 "/api/home-agent/news": NEWS}
        if request.url.path == "/api/home-agent/news":
            assert request.url.params["days"] == "3"
        return httpx.Response(200, json=paths[request.url.path])

    client = PortfolioClient("http://app:5000/", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.summary() == SUMMARY and client.digest() == DIGEST and client.news(days=3) == NEWS

    def down(request):
        raise httpx.ConnectError("refused")

    broken = PortfolioClient("http://app:5000", http=httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(PortfolioUnavailable):
        broken.summary()
    error = PortfolioClient("http://app:5000", http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(500, text="boom"))))
    with pytest.raises(PortfolioUnavailable):
        error.digest()


# --- router and chat ------------------------------------------------------------------


def reply(**fields):
    base = {"sources": ["portfolio"], "start_date": None, "end_date": None, "calendar_keywords": [],
            "mail_folder": None, "about_purchases": False, "portfolio_news": False}
    return json.dumps({**base, **fields})


def test_router_portfolio_source_and_news_flag():
    assert validate(reply()).sources == ["portfolio"]
    news = validate(reply(sources=["general"], portfolio_news=True))
    assert news.news and news.sources == ["portfolio"]  # market news about their holdings isn't "general"


class RouteChat:
    def __init__(self, route):
        self.route = route

    def complete(self, system, user, schema=None, temperature=None):
        return self.route


def test_chat_question_gets_the_portfolio_summary():
    portfolio = FakePortfolio()
    context = gather("how is my portfolio doing?", None, None, RouteChat(reply()), 3, [], TODAY, TZ, None, portfolio)
    assert context.portfolio == "used" and portfolio.calls == ["summary"]
    assert "from the user's Portfolio Analyzer app" in context.sections[0] and "== HOLDINGS ==" in context.sections[0]
    assert context.hits == []  # email wasn't searched
    assert "Portfolio Analyzer: summary of accounts and holdings" in format_sources(context, TZ)
    assert "not advice" in answer_prompt("q", context, TODAY)[0]


def test_news_question_adds_headlines_holdings_first():
    portfolio = FakePortfolio()
    context = gather("any news on my stocks?", None, None, RouteChat(reply(portfolio_news=True)), 3, [], TODAY, TZ,
                     None, portfolio)
    assert portfolio.calls == ["summary", "news"]
    news = context.sections[1]
    assert news.startswith("Recent news about the user's holdings and watchlist")
    assert news.index("SPY hits a record") < news.index("Apple unveils")
    assert "[N1]" in format_sources(context, TZ)


def test_app_not_running_is_said_plainly():
    context = gather("how is my IRA?", None, None, RouteChat(reply()), 3, [], TODAY, TZ, None, FakePortfolio(up=False))
    assert context.portfolio == "unavailable"
    assert "isn't running" in context.sections[0]
    assert "Portfolio Analyzer: not available" in format_sources(context, TZ)


# --- digest ------------------------------------------------------------------------------


def test_long_lists_are_capped_closest_first():
    alerts = [{**DIGEST["watchlist_alerts"][0], "ticker": f"T{i}", "label": None, "pct_from_target": p}
              for i, p in enumerate([-30.0, 1.0, -0.5, 4.0, -12.0, 2.5, -8.0])]
    lines = watchlist_lines(alerts, limit=3)
    assert [line.split()[2] for line in lines[:3]] == ["T2", "T1", "T5"]  # closest to target first
    assert lines[3] == "- ...and 4 more watchlist tickers near or past their targets"
    movers = [{"ticker": f"M{i}", "statement_date": "2026-08-31", "price": 10.0, "pct": 6.0} for i in range(5)]
    assert mover_lines(movers, limit=3)[-1] == "- ...and 2 more holdings moved 5% or more since their statements"


def test_watchlist_and_mover_lines():
    assert watchlist_lines(DIGEST["watchlist_alerts"]) == [
        "- Watchlist: AAPL (Apple) at $204.00, 2.0% above your buy target ($200.00)",
        "- Watchlist: NVDA at $140.00, 6.7% below your sell target ($150.00)",
    ]
    assert mover_lines(DIGEST["movers"]) == [
        "- SPY up 10.0% since your Aug 31 statement (now $605.00)",
        "- TLT down 6.2% since your Aug 31 statement (now $80.00)",
    ]


CONFIG = {"stale_statement_days": 45, "brokers": {"vanguard": ["vanguard"], "schwab": ["schwab"], "tsp": ["tsp"]}}


def email(sender, subject, day):
    return {"sender": sender, "subject": subject, "date": f"{day}T09:00:00-04:00"}


def test_statement_reminders_from_email_and_age():
    emails = [
        email("Schwab <alerts@schwab.example>", "Your statement is ready to view", "2026-09-20"),  # newer than Aug 31
        email("Vanguard <no-reply@vanguard.example>", "Market commentary", "2026-09-21"),  # not a statement
        email("Schwab <alerts@schwab.example>", "Trade confirmation", "2026-09-22"),  # not a statement
    ]
    lines = statement_lines(DIGEST["accounts"], emails, TODAY, CONFIG)
    assert lines == [
        "- New Schwab statement available (email Sep 20); your latest upload is from Aug 31. "
        "Upload it to Portfolio Analyzer.",
        "- Latest Vanguard statement uploaded is from Jul 31 (58 days ago).",
    ]
    # Once uploaded (statement date on or after the email), no reminder.
    uploaded = [{**DIGEST["accounts"][0], "statement_date": "2026-09-20"}]
    assert statement_lines(uploaded, emails, TODAY, CONFIG) == []


def test_news_is_condensed_by_the_model_or_listed():
    class Condense:
        def complete(self, system, user, schema=None, temperature=None):
            assert "(SPY) SPY hits a record" in user and "Apple" not in user  # holdings news only
            return "Here you go:\n- (SPY) S&P 500 ETF hit a record high.\n"

    assert news_lines(Condense(), NEWS["articles"], 10) == ["- News: (SPY) S&P 500 ETF hit a record high."]

    class Broken:
        def complete(self, *args, **kwargs):
            raise RuntimeError("down")

    assert news_lines(Broken(), NEWS["articles"], 10) == ["- News: (SPY) SPY hits a record"]
    assert news_lines(Broken(), [], 10) == []


def test_investment_lines_and_outage_note():
    class Condense:
        def complete(self, *args, **kwargs):
            return "- (SPY) Record high."

    lines, note = investment_lines(FakePortfolio(), Condense(), [], TODAY, CONFIG, log=lambda _: None)
    assert note is None
    assert lines[0].startswith("- Watchlist: AAPL") and "- News: (SPY) Record high." in lines
    lines, note = investment_lines(FakePortfolio(up=False), Condense(), [], TODAY, CONFIG, log=lambda _: None)
    assert lines == [] and "wasn't running" in note
    assert investment_lines(None, Condense(), [], TODAY, CONFIG) == ([], None)


def test_digest_has_an_investments_section(tmp_path):
    import yaml

    from services.digest.build import build
    from services.ingestion.store import Store

    class Chat:
        def complete(self, system, user, schema=None, temperature=None):
            return "- (SPY) Record high." if schema is None else json.dumps(
                {"category": "skip", "summary": "x", "due": None})

    config = yaml.safe_load(open("config/digest.yaml", encoding="utf-8"))
    now = datetime(2026, 9, 28, 7, 0, tzinfo=TZ)
    text = build(Store(tmp_path / "s.db"), Chat(), config, now, TZ, portfolio=FakePortfolio(),
                 portfolio_config=CONFIG, log=lambda _: None)
    section = text.split("**Investments**\n")[1].split("\n\n")[0]
    assert "AAPL (Apple) at $204.00" in section and "SPY up 10.0%" in section and "Record high" in section
    down = build(Store(tmp_path / "t.db"), Chat(), config, now, TZ, portfolio=FakePortfolio(up=False),
                 portfolio_config=CONFIG, log=lambda _: None)
    assert "**Investments**" not in down and "Portfolio Analyzer wasn't running" in down
