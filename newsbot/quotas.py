"""Keep the bot's daily counters separate from provider quota windows."""

import math
import re
import time

from .formatting import duration_label

# Official provider docs are listed in docs/PROVIDERS.md. Groq and SambaNova
# supply time-until-reset on responses rather than a documented fixed clock time.
FIXED_RESET = {
    "cloudflare": "Cloudflare neurons: daily at 00:00 UTC",
    "openrouter": "OpenRouter free requests: daily at 00:00 UTC",
    "gemini": "Gemini daily requests: midnight US Pacific",
    "nvidia": "NVIDIA evaluation: per-minute limits; no fixed daily reset listed",
}


def duration_reset(value, now):
    if not isinstance(value, str) or len(value) > 40:
        return None
    value = value.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        seconds = float(value)
    elif re.fullmatch(r"(?:\d+(?:\.\d+)?[dhms])+", value):
        scale = {"d": 86400, "h": 3600, "m": 60, "s": 1}
        seconds = sum(float(n) * scale[unit] for n, unit in re.findall(r"(\d+(?:\.\d+)?)([dhms])", value))
    else:
        return None
    return now + seconds if 0 <= seconds <= 172800 else None


def snapshot_stale(quota, bucket, checked, now):
    reset = quota.get(bucket + "_reset")
    return (checked and now - checked > (60 if bucket == "tokens" else 120)
            or type(reset) in {int, float} and reset <= now)


def provider_reset_line(provider, quota, now=None):
    if provider not in {"groq", "sambanova"}:
        return FIXED_RESET.get(provider, "")
    now = time.time() if now is None else now
    display = []
    requests = quota.get("requests_reset")
    if type(requests) in {int, float} and now < requests <= now + 172800:
        display.append("daily requests ~" + time.strftime("%m-%d %H:%M UTC", time.gmtime(requests)))
    else:
        display.append("daily request time not reported" if requests is None else "daily reset snapshot expired")
    tokens = quota.get("tokens_reset")
    if type(tokens) in {int, float} and now < tokens <= now + 172800:
        display.append(f"tokens/min ~{duration_label(math.ceil(tokens - now))}")
    return "Reset: " + " · ".join(display)
