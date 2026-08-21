"""Shared utilities for Eurostat ingestion modules — HTTP retry with operational logging."""
import logging
import time

import requests

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


def request_with_retry(method: str, url: str, **kwargs):
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=60, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            wait = 2 ** attempt
            logger.warning(
                "Network error, retrying | url=%s attempt=%s/%s wait=%ss error=%s",
                url, attempt, MAX_RETRIES, wait, exc,
            )
            time.sleep(wait)
            continue

        if resp.status_code in (429, 503) or 500 <= resp.status_code < 600:
            retry_after = int(resp.headers.get("retry-after", 2 ** attempt))
            logger.warning(
                "Request failed, retrying | url=%s status=%s attempt=%s/%s wait=%ss",
                url, resp.status_code, attempt, MAX_RETRIES, retry_after,
            )
            time.sleep(retry_after)
            continue

        if not resp.ok:
            logger.error("Request failed | url=%s status=%s", url, resp.status_code)
            resp.raise_for_status()

        return resp

    raise RuntimeError(f"Request failed after {MAX_RETRIES} retries: {last_exc}")
