"""A small read-only Microsoft Graph client: GET with paging, and waiting
when Microsoft asks to slow down (429/503/504, honoring Retry-After)."""

import time

import httpx

BASE = "https://graph.microsoft.com/v1.0"
WAITS = (15, 30, 60, 60, 60, 60)
RETRY_STATUSES = (429, 503, 504)
MAX_RETRY_AFTER = 120


class GraphError(Exception):
    def __init__(self, status, detail):
        super().__init__(f"Microsoft Graph returned {status}: {detail}")
        self.status = status


class GraphClient:
    def __init__(self, get_token, log=print, sleep=time.sleep, http=None, waits=WAITS):
        self.get_token = get_token
        self.log = log
        self.sleep = sleep
        self.http = http or httpx.Client(timeout=60)
        self.waits = waits

    def get(self, path, params=None, headers=None):
        url = path if path.startswith("http") else BASE + path
        for wait in (*self.waits, None):
            response = self.http.get(
                url, params=params, headers={"Authorization": f"Bearer {self.get_token()}", **(headers or {})}
            )
            if response.status_code in RETRY_STATUSES and wait is not None:
                try:
                    wait = min(int(response.headers.get("Retry-After", wait)), MAX_RETRY_AFTER)
                except ValueError:
                    pass
                self.log(f"  Microsoft asked to slow down; waiting {wait}s, then continuing...")
                self.sleep(wait)
                continue
            if response.status_code >= 400:
                raise GraphError(response.status_code, response.text[:300])
            return response.json()

    def paged(self, path, params=None, headers=None):
        """Every item across all pages of a collection."""
        data = self.get(path, params, headers)
        while True:
            yield from data.get("value", [])
            next_link = data.get("@odata.nextLink")
            if not next_link:
                return
            data = self.get(next_link, headers=headers)  # the link already carries the query
