"""Running Google API requests politely: wait and retry when Google says
the per-minute quota is used up, instead of failing the sync."""

import time

# Seconds to wait before each retry. Gmail's quota is per minute, so the
# longer waits let a full minute pass.
WAITS = (15, 30, 60, 60, 60, 60)
RATE_LIMIT_REASONS = (b"rateLimitExceeded", b"userRateLimitExceeded")


def is_rate_limited(error):
    status = getattr(error.resp, "status", None)
    if status == 429:
        return True
    content = error.content or b""
    return status == 403 and any(reason in content for reason in RATE_LIMIT_REASONS)


def execute(request, log=print, sleep=time.sleep, waits=WAITS):
    """request.execute(), retrying after rate-limit errors. Other errors, and
    a rate limit that outlasts every wait, are raised."""
    from googleapiclient.errors import HttpError

    for wait in (*waits, None):
        try:
            return request.execute()
        except HttpError as error:
            if wait is None or not is_rate_limited(error):
                raise
            log(f"  Google's per-minute limit reached; waiting {wait}s, then continuing...")
            sleep(wait)
