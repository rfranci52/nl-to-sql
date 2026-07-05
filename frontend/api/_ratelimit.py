"""Per-visitor + global daily rate limit via Upstash Redis REST. Fails OPEN:
if Upstash isn't configured, or the limiter errors, requests are allowed. Shared
by /ask and /order, each with its own bucket + limits.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import date

UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")


def _redis(*parts):
    url = UPSTASH_URL + "/" + "/".join(urllib.parse.quote(str(p), safe="") for p in parts)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read()).get("result")


def check(ip, bucket, per_visitor, global_, visitor_msg, global_msg):
    """(allowed, message). No-op allow unless Upstash is configured."""
    if not (UPSTASH_URL and UPSTASH_TOKEN):
        return True, ""
    try:
        today = date.today().isoformat()
        gkey = f"pz:{bucket}:g:{today}"
        vkey = f"pz:{bucket}:v:{ip}:{today}"
        g = _redis("INCR", gkey); _redis("EXPIRE", gkey, 172800)
        v = _redis("INCR", vkey); _redis("EXPIRE", vkey, 172800)
        if g and int(g) > global_:
            return False, global_msg
        if v and int(v) > per_visitor:
            return False, visitor_msg
        return True, ""
    except Exception:
        return True, ""  # never break the demo because the limiter had a hiccup
