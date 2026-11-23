"""News footers and recorded message metadata. Never edits or deletes by itself."""

import json
import re
import time

from .editorial import metadata
from .feeds import signalled_feed_url
from .formatting import SIGNAL_INLINE_BYTES, Message, SignalText, article_age, insert_styled

FOOTER_FIELDS = {
    "model": ("footer_model", "Generating model"),
    "usage": ("footer_usage", "AI calls left"),
    "age": ("footer_age", "Article age"),
    "source": ("include_sources", "Source"),
    "hourly": ("footer_hourly", "Posts sent this hour"),
    "score": ("footer_score", "Relevance score"),
}

PROVIDERS = {"groq": "Groq", "openrouter": "OpenRouter", "cloudflare": "Cloudflare",
             "gemini": "Gemini", "nvidia": "NVIDIA", "sambanova": "SambaNova",
             "requesty": "Requesty", "zai": "Z.ai"}
FRIENDLY = {
    "openai/gpt-oss-20b": "GPT-OSS 20B",
    "openai/gpt-oss-120b": "GPT-OSS 120B",
    "@cf/openai/gpt-oss-20b": "GPT-OSS 20B",
    "@cf/openai/gpt-oss-120b": "GPT-OSS 120B",
}


def model_label(item, exact=False):
    if item.get("route"):
        return "Emergency headline · no AI" if item["route"] == "emergency" else "Instant feed headline · no AI"
    decision = metadata(item, "decision")
    model = item.get("actual_model") or decision.get("configured_model") or ""
    provider = PROVIDERS.get(item.get("provider"), item.get("provider") or "")
    if not model:
        return " · ".join(filter(None, (provider, item.get("model"), "generating ID unavailable")))
    reported = decision.get("reported")
    note = ("configured" if reported is False else "reported status unknown" if reported is None and exact else "")
    short_id = model.rsplit("/", 1)[-1] if item.get("provider") == "cloudflare" and model.startswith("@cf/") else model
    label = model if exact else FRIENDLY.get(model, short_id)
    return " · ".join(filter(None, (provider, label, note)))


def render_news(item, config, pool, definition, sent, now=None):
    now = time.time() if now is None else now
    direct = bool(item.get("route"))
    headline = item["summary"]
    # Use the canonical entry link, not any URLs in an unreviewed headline.
    if direct:
        headline = re.sub(r"\bhttps?://\S+", "", headline, flags=re.I).strip() or "Feed headline available in the dashboard."
    message = Message().line(headline)
    if (item.get("route") == "instant" or config["include_sources"]) and signalled_feed_url(item["url"]):
        message.line(f"🔗 {item['url']}")
    ai = []
    if direct or config["footer_model"]:
        ai.append(("🚨 " if item.get("route") == "emergency" else "⚡ " if direct else "") + model_label(item))
    usage = (f"{pool.local_remaining(definition):,}/{definition.get('daily_requests', 200):,} calls"
             if config["footer_usage"] and not direct else "")
    score = config["footer_score"] and not direct
    inline_usage = bool(usage and not ai and (config["footer_hourly"] or score))
    if usage and not inline_usage:
        ai.append(usage)
    if ai or config["footer_age"] or config["footer_hourly"] or score:
        message.line()
    if ai:
        message.line(" · ".join(ai), "ITALIC")
    if config["footer_hourly"] or score:
        if inline_usage:
            message.add(usage, "ITALIC").add(" · ")
        if config["footer_hourly"]:
            message.add(f"📰 {sent + 1}/{config['max_posts_per_hour']}")
            if score:
                message.add(" · ")
        if score:
            message.add("🎯 ").add(item["score"], "BOLD")
        message.line()
    if config["footer_age"]:
        published = item["published"] if item["published"] != item["discovered"] else None
        age = article_age(published, now).removeprefix("Published ")
        message.add("🕒 ").add(age, "BOLD")
    return message.build()


def recorded_message(message, score, image, age=False):
    if len(message.encode()) + 84 > SIGNAL_INLINE_BYTES:
        return ""
    # Score is the last field before age. Save the base text and position, not
    # the current preferences/age, so later caption edits don't rewrite history.
    offset = message.rfind("\n") if age else len(message)
    return json.dumps({"text": str(message), "styles": message.styles, "score": score,
                       "image": image, "footer_end": offset}, ensure_ascii=False)


def edited_news(saved, suffix, muted):
    message = SignalText(saved["text"], saved["styles"])
    offset = saved.get("footer_end", len(message))  # v1.0.4 frames end in the score
    if suffix and saved.get("score"):
        message = insert_styled(message, offset, suffix)
        offset += len(suffix)
    if muted:
        message = insert_styled(message, offset, "\n🔕 Story muted", "ITALIC")
    return message


def queue_post_edit(store, item_id, now):
    store.db.execute("""INSERT INTO footer_edits(item_id,due) VALUES(?,?) ON CONFLICT(item_id)
                        DO UPDATE SET due=excluded.due WHERE due<=0 AND edits<10""", (item_id, now + 5))
