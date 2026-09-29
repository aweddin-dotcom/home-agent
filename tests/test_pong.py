"""Browser Pong league standings, with a fake game and invented matches."""

from datetime import date
from zoneinfo import ZoneInfo

import httpx

from services.pong.standings import PongClient, PongUnavailable, standings, standings_section
from services.retrieval.ask import answer_prompt, format_sources, gather

TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 27)

MATCHES = [
    {"id": 1, "playedAt": "2026-09-01T10:00:00Z", "leftPlayer": "Sam", "rightPlayer": "Alex",
     "leftScore": 11, "rightScore": 5, "winner": "Sam"},
    {"id": 2, "playedAt": "2026-09-02T10:00:00Z", "leftPlayer": "sam", "rightPlayer": "Alex",
     "leftScore": 7, "rightScore": 11, "winner": "Alex"},
    {"id": 3, "playedAt": "2026-09-03T10:00:00Z", "leftPlayer": "Alex", "rightPlayer": "SAM",
     "leftScore": 9, "rightScore": 11, "winner": "SAM"},
    {"id": 4, "playedAt": "2026-09-04T10:00:00Z", "leftPlayer": "Robin", "rightPlayer": "Sam",
     "leftScore": 1, "rightScore": 2, "bestOf": 3, "games": [[11, 7], [9, 11], [10, 12]], "winner": "Sam"},
]


class FakePong:
    def __init__(self, matches=MATCHES, up=True):
        self._matches, self.up = matches, up

    def matches(self):
        if not self.up:
            raise PongUnavailable("down")
        return self._matches


def test_standings_merge_capitalization_and_rank_by_wins():
    rows, pairs, _ = standings(MATCHES)
    assert [(r["name"], r["wins"], r["losses"], r["pct"]) for r in rows] == [
        ("Sam", 3, 1, 75), ("Alex", 1, 2, 33), ("Robin", 0, 1, 0)]
    assert {"a": "Alex", "b": "Sam", "a_wins": 1, "b_wins": 2} in pairs


def test_section_lists_leader_first_with_series_detail():
    text = standings_section(MATCHES)
    assert "4 league matches, 2026-09-01 to 2026-09-04" in text
    assert "  1. Sam: 3 wins, 1 losses (75% of 4 matches)" in text
    assert "Alex vs Sam: Alex 1, Sam 2" in text
    assert "Robin vs Sam  1–2 in games (best of 3: 11–7, 9–11, 10–12)  (winner: Sam)" in text


def test_no_matches_yet():
    assert "no league matches recorded yet" in standings_section([])


def test_client_reads_the_games_api():
    def handler(request):
        assert request.url.path == "/api/matches"
        return httpx.Response(200, json=MATCHES)

    client = PongClient("http://pong.test", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(client.matches()) == 4


def test_client_reports_the_game_being_off():
    def handler(request):
        raise httpx.ConnectError("refused")

    client = PongClient("http://pong.test", http=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        client.matches()
        raise AssertionError("expected PongUnavailable")
    except PongUnavailable:
        pass


class RouterChat:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user, schema=None, temperature=None):
        return self.reply


class NoSearch:
    def embed_query(self, text):
        raise AssertionError("email was searched")


ROUTE_PONG = '{"sources": ["pong"], "start_date": null, "end_date": null, "calendar_keywords": []}'
ROUTE_GENERAL = '{"sources": ["general"], "start_date": null, "end_date": null, "calendar_keywords": []}'


def test_pong_question_uses_only_the_standings():
    context = gather("who has the best browser pong record?", NoSearch(), None, RouterChat(ROUTE_PONG), 5,
                     [], TODAY, TZ, pong=FakePong())
    assert len(context.sections) == 1 and "1. Sam: 3 wins" in context.sections[0]
    assert "Browser Pong: 4 league matches" in format_sources(context, TZ)
    system, _ = answer_prompt("q", context, TODAY)
    assert "Browser Pong standings" in system
    # Nothing to cite: the model isn't told how, so it doesn't invent [E1].
    assert "Cite what you used" not in system and "don't add any like [1]" in system


def test_the_word_pong_catches_a_missed_route():
    context = gather("who's winning at pong?", NoSearch(), None, RouterChat(ROUTE_GENERAL), 5,
                     [], TODAY, TZ, pong=FakePong())
    assert context.route.sources == ["pong"]


def test_game_not_running():
    context = gather("pong standings?", NoSearch(), None, RouterChat(ROUTE_PONG), 5, [], TODAY, TZ,
                     pong=FakePong(up=False))
    assert "isn't running" in context.sections[0]
    assert "Browser Pong: not available" in format_sources(context, TZ)
