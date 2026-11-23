import copy
import json
import os
import secrets
from pathlib import Path


DEFAULTS = {
    "focus": "",
    "exclude": "Opinion, routine product announcements, repeated coverage without a material update.",
    "threshold": 75,
    "paused": False,
    "mode": "preview",
    "preferred_model": "auto",
    "fallback": True,
    "group_id": "",
    "signal_account": "",
    "signal_url": "http://127.0.0.1:7583",
    "include_sources": False,
    "footer_model": True,
    "footer_age": False,
    "footer_usage": True,
    "footer_hourly": False,
    "footer_score": False,
    "include_images": False,
    "image_min_score": 75,
    "include_videos": False,
    "video_min_score": 75,
    "web_expand": False,
    "tavily_key": "",
    "topic_feedback": False,
    "feedback_weight": 3,
    "story_mute": False,
    "emergency": False,
    "reply_cleanup": False,
    "summary_length": "normal",
    "retention_articles_days": 7,
    "retention_history_days": 30,
    "backup_retention_days": 30,
    "backup_keep": 3,
    "backup_prune_enabled": False,
    "batch_size": 6,
    "poll_seconds": 180,
    "max_age_hours": 6,
    "max_posts_per_hour": 30,
    "models": [
        {"name": "groq-20b", "provider": "groq", "model": "openai/gpt-oss-20b", "key": "", "enabled": True, "daily_requests": 900, "json_mode": True},
        {"name": "groq-120b", "provider": "groq", "model": "openai/gpt-oss-120b", "key": "", "enabled": True, "daily_requests": 900, "json_mode": True},
        {"name": "cloudflare-gemma", "provider": "cloudflare", "model": "@cf/google/gemma-4-26b-a4b-it", "key": "", "account_id": "", "enabled": True, "daily_requests": 200, "json_mode": False},
        {"name": "openrouter-free", "provider": "openrouter", "model": "openrouter/free", "key": "", "enabled": True, "daily_requests": 50, "json_mode": True},
        {"name": "nvidia-test", "provider": "nvidia", "model": "openai/gpt-oss-20b", "key": "", "enabled": False, "daily_requests": 100, "json_mode": False},
        {"name": "requesty-gemma", "provider": "requesty", "model": "google/gemma-4-31b-it", "key": "", "enabled": False, "daily_requests": 180, "json_mode": False},
        {"name": "zai-flash", "provider": "zai", "model": "glm-4.7-flash", "key": "", "enabled": False, "daily_requests": 100, "json_mode": False},
    ],
}

CLOUDFLARE_FREE_MODELS = {
    "@cf/qwen/qwen3-30b-a3b-fp8", "@cf/meta/llama-3.1-8b-instruct-fp8-fast",
    "@cf/meta/llama-3.2-3b-instruct", "@cf/meta/llama-3.2-1b-instruct",
    "@cf/openai/gpt-oss-20b", "@cf/openai/gpt-oss-120b",
    "@cf/ibm-granite/granite-4.0-h-micro", "@cf/google/gemma-4-26b-a4b-it",
}
# Allow these to load unchanged on upgrade, but never spend attempts on them.
CLOUDFLARE_RETIRED = {
    "@cf/qwen/qwen3-30b-a3b-fp8", "@cf/meta/llama-3.2-1b-instruct", "@cf/meta/llama-3.2-3b-instruct",
}
ZAI_FREE_MODELS = {"glm-4.7-flash", "glm-4.5-flash"}
REQUESTY_MODELS = {
    "google/gemma-4-31b-it", "novita/ling-3.1-flash",
    "nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-3-ultra-550b-a55b",
}


def validate_models(models):
    if not isinstance(models, list) or len(models) > 100:
        raise ValueError("Models must be a list with at most 100 entries.")
    names = set()
    for m in models:
        if not isinstance(m, dict):
            raise ValueError("Each model must be an object.")
        name = m.get("name", "")
        if not isinstance(name, str) or not name or len(name) > 60 or not all(c.isalnum() or c in "-_" for c in name):
            raise ValueError("Model aliases must use letters, digits, hyphens or underscores.")
        if name in names or name == "auto":
            raise ValueError("Model aliases must be unique and cannot be 'auto'.")
        names.add(name)
        provider, model = m.get("provider"), m.get("model")
        if provider not in {"groq", "openrouter", "cloudflare", "gemini", "nvidia", "sambanova", "requesty", "zai"}:
            raise ValueError("Supported providers: groq, openrouter, cloudflare, gemini, nvidia, sambanova, requesty, zai.")
        if not isinstance(model, str) or not model or len(model) > 150:
            raise ValueError("A model ID is required.")
        if provider == "openrouter" and not (model.endswith(":free") or model == "openrouter/free"):
            raise ValueError("Only OpenRouter :free models or openrouter/free are permitted.")
        if provider == "requesty" and model not in REQUESTY_MODELS:
            raise ValueError("Use a listed Requesty free model ID, not a routing policy. See docs/PROVIDERS.md.")
        if provider == "zai" and model not in ZAI_FREE_MODELS:
            raise ValueError("Only the Z.ai GLM-4.7-Flash and GLM-4.5-Flash free models are permitted.")
        if provider == "cloudflare":
            if model not in CLOUDFLARE_FREE_MODELS:
                raise ValueError("Cloudflare model is not on this release's free-allocation allowlist.")
            account = m.get("account_id", "")
            if account and (len(account) != 32 or any(c not in "0123456789abcdef" for c in account.lower())):
                raise ValueError("Cloudflare account ID must be 32 hexadecimal characters.")
        if not isinstance(m.get("key", ""), str):
            raise ValueError("API key must be text.")
        cap = m.get("daily_requests", 200)
        if type(cap) is not int or not 1 <= cap <= 100000:
            raise ValueError("daily_requests must be between 1 and 100000.")
        for flag in ("enabled", "json_mode"):
            if flag in m and type(m[flag]) is not bool:
                raise ValueError(f"{flag} must be a boolean.")
    return models


class Config:
    def __init__(self, path):
        self.path = Path(path)
        self.data = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))
            self.data.pop("footer_small", None)
        else:
            self.data.update(dashboard_password=secrets.token_urlsafe(24), csrf=secrets.token_urlsafe(32))
            self.save()
        validate_models(self.data["models"])

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        self.path.chmod(0o600)

    def update(self, **values):
        for key in ("footer_model", "footer_usage", "footer_age", "footer_hourly",
                    "footer_score", "include_sources", "include_images", "include_videos", "web_expand",
                    "topic_feedback", "story_mute", "emergency", "reply_cleanup", "backup_prune_enabled"):
            if key in values and type(values[key]) is not bool:
                raise ValueError(f"{key} must be a boolean.")
        for key, low, high in (("retention_articles_days", 1, 365), ("retention_history_days", 1, 3650),
                               ("backup_retention_days", 1, 3650), ("backup_keep", 1, 100),
                                ("poll_seconds", 60, 86400), ("feedback_weight", 1, 10),
                                 ("image_min_score", 0, 100), ("video_min_score", 0, 100)):
            if key in values and (type(values[key]) is not int or not low <= values[key] <= high):
                raise ValueError(f"{key} must be {low}..{high}.")
        if "summary_length" in values and values["summary_length"] not in {"short", "normal", "detailed", "auto"}:
            raise ValueError("Length must be short, normal, detailed or auto.")
        if "tavily_key" in values and (not isinstance(values["tavily_key"], str)
                or len(values["tavily_key"]) > 256 or any(not ("!" <= c <= "~") for c in values["tavily_key"])):
            raise ValueError("Enter a valid search API key or clear it.")
        if "models" in values:
            validate_models(values["models"])
        previous = copy.deepcopy(self.data)
        self.data.update(values)
        try:
            self.save()
        except OSError:
            self.data.clear()
            self.data.update(previous)
            raise

    def rules_key(self):
        return json.dumps({**{k: self.data[k] for k in ("focus", "exclude", "threshold", "group_id",
                                                        "signal_account", "summary_length", "include_sources")},
                            "editorial": 5}, sort_keys=True)
