"""Read-only client for Portfolio Analyzer's /api/home-agent endpoints.

Portfolio Analyzer owns the investment data; Home Agent only asks it for a
summary (chat), digest data, and news. Nothing is written back.
"""

import os

import httpx


class PortfolioUnavailable(Exception):
    """Portfolio Analyzer isn't running or didn't answer."""


class PortfolioClient:
    def __init__(self, url, timeout=30, http=None):
        self.url = url.rstrip("/")
        self.http = http or httpx.Client(timeout=timeout)

    def _get(self, path, **params):
        try:
            response = self.http.get(f"{self.url}/api/home-agent/{path}", params=params or None)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise PortfolioUnavailable(f"Portfolio Analyzer didn't answer ({type(error).__name__})") from error

    def summary(self):
        """The advisor's full text context: accounts, holdings, allocation, prices, watchlist, plans."""
        return self._get("summary")

    def digest(self):
        """Balances by account, watchlist alerts, and big moves since the last statement."""
        return self._get("digest")

    def news(self, days=2):
        """Recent headlines for held and watchlist tickers."""
        return self._get("news", days=days)


def client_from_settings():
    """A client per config/portfolio.yaml, or None when the integration is off."""
    from services.common import settings

    config = settings.load_config("portfolio.yaml")
    if not config.get("enabled"):
        return None
    return PortfolioClient(os.environ.get("PORTFOLIO_URL", config["url"]), config.get("timeout_seconds", 30))
