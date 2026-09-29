"""Browser Pong league standings, from the game's own match history.

Browser Pong (a separate app on this machine) serves its league matches,
read-only, at /api/matches. The standings are worked out here, in code, so
the chat model only has to phrase them. Player names are treated the way
the game treats them: capitalization doesn't matter, and each player is
shown with their most-used spelling.
"""

import os
from collections import Counter, defaultdict

import httpx

RECENT = 5


class PongUnavailable(Exception):
    """Browser Pong isn't running or didn't answer."""


class PongClient:
    def __init__(self, url, timeout=10, http=None):
        self.url = url.rstrip("/")
        self.http = http or httpx.Client(timeout=timeout)

    def matches(self):
        try:
            response = self.http.get(f"{self.url}/api/matches")
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise PongUnavailable(f"Browser Pong didn't answer ({type(error).__name__})") from error
        return data if isinstance(data, list) else []


def client_from_settings():
    """A client per config/pong.yaml, or None when the connection is off."""
    from services.common import settings

    config = settings.load_config("pong.yaml")
    if not config.get("enabled"):
        return None
    return PongClient(os.environ.get("PONG_URL", config["url"]), config.get("timeout_seconds", 10))


def _key(name):
    return str(name or "").strip().lower()


def standings(matches):
    """Leaderboard rows (best first), head-to-head records, and display names."""
    spellings = defaultdict(Counter)
    for m in matches:
        for n in (m.get("leftPlayer"), m.get("rightPlayer")):
            spellings[_key(n)][str(n or "").strip()] += 1
    names = {k: c.most_common(1)[0][0] for k, c in spellings.items()}

    record = defaultdict(lambda: {"wins": 0, "losses": 0})
    h2h = defaultdict(lambda: Counter())
    for m in matches:
        left, right, winner = _key(m.get("leftPlayer")), _key(m.get("rightPlayer")), _key(m.get("winner"))
        if winner not in (left, right):
            continue
        loser = right if winner == left else left
        record[winner]["wins"] += 1
        record[loser]["losses"] += 1
        h2h[tuple(sorted((left, right)))][winner] += 1

    rows = []
    for k, r in record.items():
        played = r["wins"] + r["losses"]
        rows.append({"name": names[k], "wins": r["wins"], "losses": r["losses"], "played": played,
                     "pct": round(100 * r["wins"] / played) if played else 0})
    # The game's own leaderboard order: most wins, then fewest losses.
    rows.sort(key=lambda r: (-r["wins"], r["losses"], r["name"].lower()))
    pairs = [{"a": names[a], "b": names[b], "a_wins": c[a], "b_wins": c[b]} for (a, b), c in h2h.items()]
    pairs.sort(key=lambda p: -(p["a_wins"] + p["b_wins"]))
    return rows, pairs, names


def _score(m):
    score = f"{m.get('leftScore')}–{m.get('rightScore')}"
    if (m.get("bestOf") or 1) > 1:
        games = ", ".join(f"{a}–{b}" for a, b in m.get("games") or [])
        score = f"{score} in games (best of {m['bestOf']}" + (f": {games})" if games else ")")
    return score


def standings_section(matches):
    """What the model is given for a Browser Pong question."""
    if not matches:
        return "Browser Pong league: no league matches recorded yet."
    rows, pairs, names = standings(matches)
    days = sorted(str(m.get("playedAt") or "")[:10] for m in matches if m.get("playedAt"))
    span = f", {days[0]} to {days[-1]}" if days else ""
    lines = [
        f"Browser Pong league, from the family's game on this computer ({len(matches)} league matches{span}). "
        "Ranked by wins, then fewest losses; all numbers are already worked out, so use them as given.",
        "Leaderboard:",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(f"  {i}. {r['name']}: {r['wins']} wins, {r['losses']} losses ({r['pct']}% of {r['played']} matches)")
    lines.append("Head-to-head:")
    for p in pairs:
        lines.append(f"  {p['a']} vs {p['b']}: {p['a']} {p['a_wins']}, {p['b']} {p['b_wins']}")
    recent = sorted(matches, key=lambda m: str(m.get("playedAt") or ""), reverse=True)[:RECENT]
    lines.append("Most recent matches:")
    for m in recent:
        left, right = names[_key(m.get("leftPlayer"))], names[_key(m.get("rightPlayer"))]
        winner = names.get(_key(m.get("winner")), m.get("winner"))
        lines.append(f"  {str(m.get('playedAt') or '')[:10]}  {left} vs {right}  {_score(m)}  (winner: {winner})")
    return "\n".join(lines)
