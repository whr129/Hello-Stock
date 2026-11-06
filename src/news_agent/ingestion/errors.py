from urllib.error import HTTPError

import httpx


def http_error_status(error: BaseException) -> int | None:
    """Read structured status codes without interpreting remote error text."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, HTTPError):
            return error.code
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code
        error = error.__cause__
    return None
