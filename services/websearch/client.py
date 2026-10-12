"""Web search through SearXNG, a search engine run locally in Docker.

SearXNG passes the query on to public search engines (Google, Bing,
DuckDuckGo and others) and returns their combined results. Only the query
leaves this machine. It's written from the user's question alone, never
from their mail, calendar, or notes (see the router), and safe_query()
removes email addresses and long numbers before it's sent.
"""

import os
import re

import httpx


class WebSearchUnavailable(Exception):
    """SearXNG isn't running or didn't answer."""


EMAIL = re.compile(r"\S+@\S+\.\S+")
LONG_NUMBER = re.compile(r"\+?\d[\d\s().-]{6,}\d")  # phone and account numbers
MAX_QUERY_CHARS = 150
SNIPPET_CHARS = 300
MIN_NEWS_RESULTS = 3  # fewer news articles than this: add ordinary results too


def safe_query(text):
    """The query with email addresses and phone or account numbers removed."""
    text = LONG_NUMBER.sub(" ", EMAIL.sub(" ", text or ""))
    return " ".join(text.split())[:MAX_QUERY_CHARS].strip()


class WebSearch:
    def __init__(self, url, max_results=6, language="en", timeout=15, http=None):
        self.url = url.rstrip("/")
        self.max_results = max_results
        self.language = language
        self.http = http or httpx.Client(timeout=timeout)

    def _query(self, query, **params):
        try:
            response = self.http.get(f"{self.url}/search", params={
                "q": query, "format": "json", "language": self.language, "safesearch": 1, **params})
            response.raise_for_status()
            results = response.json().get("results") or []
        except (httpx.HTTPError, ValueError, AttributeError) as error:
            raise WebSearchUnavailable(f"SearXNG didn't answer ({type(error).__name__})") from error
        rows = []
        for r in results:
            if not isinstance(r, dict) or not r.get("url") or not r.get("title"):
                continue
            rows.append({
                "title": " ".join(str(r["title"]).split()),
                "url": r["url"],
                "snippet": " ".join(str(r.get("content") or "").split())[:SNIPPET_CHARS],
                "published": str(r.get("publishedDate") or "")[:10],
            })
        return rows

    def search(self, query, recent=False):
        """Results for the query, best first: dicts with title, url, snippet,
        and published (YYYY-MM-DD, or "" when unknown). `recent` looks in
        news from the last month first."""
        rows = self._query(query, categories="news", time_range="month") if recent else []
        if len(rows) < MIN_NEWS_RESULTS:
            rows += self._query(query, categories="general")
        seen, unique = set(), []
        for row in rows:
            if row["url"] not in seen:
                seen.add(row["url"])
                unique.append(row)
        return unique[: self.max_results]


def client_from_settings():
    """A client per config/web_search.yaml, or None when web search is off."""
    from services.common import settings

    config = settings.load_config("web_search.yaml")
    if not config.get("enabled"):
        return None
    return WebSearch(os.environ.get("WEB_SEARCH_URL", config["url"]), config.get("max_results", 6),
                     config.get("language", "en"), config.get("timeout_seconds", 15))
