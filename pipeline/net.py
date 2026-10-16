"""Small HTTP helper with retries, throttling and clear logging."""
import time
import requests

from config import USER_AGENT

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT
_last = {}


def get(url, *, headers=None, params=None, timeout=60, retries=4, min_interval=0.0, host_key=None,
        ok_404=False):
    """GET with retry/backoff. min_interval throttles calls per host_key."""
    key = host_key or url.split("/")[2]
    for attempt in range(retries + 1):
        wait = min_interval - (time.time() - _last.get(key, 0))
        if wait > 0:
            time.sleep(wait)
        _last[key] = time.time()
        try:
            r = _session.get(url, headers=headers, params=params, timeout=timeout)
        except requests.RequestException as e:
            err = str(e)
            r = None
        if r is not None:
            if 200 <= r.status_code < 300:
                return r
            if r.status_code == 404 and ok_404:
                return None
            err = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code in (400, 401, 403, 404) and attempt >= 1:
                break
        if r is not None and r.status_code == 302 and 'Rate Limit' in r.text:
            time.sleep(30)
        if r is not None and r.status_code == 429:
            time.sleep(15)
        time.sleep(min(60, 2 ** attempt * 2))
    raise RuntimeError(f"GET failed for {url.split('?')[0]}: {err}")
