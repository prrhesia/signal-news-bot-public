"""Opt-in public search for a recent headline, without reading Signal chat."""

import re

import aiohttp

from .ai import ModelOutputError, PoolUnavailable, json_output
from .editorial import CONTEXT_WORDS, EVENT_STAGES, numeric_terms, terms
from .feeds import canonical_url, signalled_feed_url, text
from .formatting import Message
from .http import read_json

SEARCH_URL = "https://api.tavily.com/search"
URL_TEXT = re.compile(
    r"(?:\b(?:https?|ftp)://|\bmailto:|(?<![\w.-])(?:[\w.+-]+@)?(?:[\w-]+\.)+[a-z]{2,}(?![\w-]))"
    r"[^\s<>\])]*", re.I)
SENTENCES = re.compile(r"(?<!\b[A-Z]\.[A-Z]\.)(?<=[.!?])\s+(?=[A-Z0-9])")
TEASER = re.compile(r"^(?:read (?:more|the full (?:story|article))\b|continue reading\b|"
                    r"(?:this|the) (?:post|article) (?:first )?appeared\b|click here\b)", re.I)
DETAIL_FILLER = CONTEXT_WORDS | {"officially", "recently", "major", "significant", "important",
                                "development", "update", "full", "story", "details", "article"}
DETAIL_ALIASES = {"approving": "approve", "greenlit": "approve", "greenlight": "approve",
                  "announces": "announce", "announced": "announce",
                  "suspends": "suspend", "pauses": "pause", "paused": "pause",
                  "includes": "include", "included": "include"}


class SearchUnavailable(Exception):
    """A fixed diagnostic that never includes a response body or request details."""


def plain_details(value, limit):
    return " ".join(URL_TEXT.sub(" ", text(value, 3600)).split())[:limit].rstrip()


def detail_terms(value):
    return {DETAIL_ALIASES.get(word, word) for word in terms(value)} - DETAIL_FILLER


def feed_details(row):
    known = detail_terms(plain_details(row["summary"], 1200)) | detail_terms(plain_details(row["title"], 500))
    added = []
    for sentence in SENTENCES.split(plain_details(row["description"], 1800)):
        if not 12 <= len(sentence) <= 320 or TEASER.match(sentence):
            continue
        words = detail_terms(sentence)
        new = words - known
        # One new synonym or attribution is weak evidence of a new fact.
        # A new quantity, changed outcome, or short standalone detail can still qualify.
        if len(new) >= 2 or new and (numeric_terms(new) or new & EVENT_STAGES or len(words) <= 4):
            added.append(sentence)
            known |= words
            if len(added) == 3:
                break
    return added


class TavilySearch:
    def __init__(self, session):
        self.session = session

    async def search(self, headline, key):
        payload = {"query": headline, "topic": "news", "search_depth": "basic", "max_results": 4,
                   "include_answer": False, "include_raw_content": False, "include_images": False,
                   "auto_parameters": False}
        async with self.session.post(SEARCH_URL, headers={"Authorization": "Bearer " + key},
                     json=payload, timeout=aiohttp.ClientTimeout(total=7), allow_redirects=False) as response:
            if response.status != 200:
                if response.status in {401, 403}:
                    raise SearchUnavailable("Tavily refused authorization; check the saved key and plan.")
                if response.status == 429:
                    raise SearchUnavailable("Tavily rate limit or account quota reached (HTTP 429).")
                raise SearchUnavailable(f"Tavily search failed (HTTP {response.status}).")
            try:
                data = await read_json(response, 100_000)
            except ValueError:
                raise SearchUnavailable("Tavily returned invalid or oversized JSON.") from None
            return source_rows(data, headline)


def source_rows(data, headline):
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise SearchUnavailable("Tavily response is missing a valid results list.")
    rows, urls = [], set()
    headline_terms = terms(headline)
    for result in results[:4]:
        if not isinstance(result, dict):
            continue
        url, title, snippet = (result.get(name) for name in ("url", "title", "content"))
        if not (isinstance(url, str) and signalled_feed_url(url)
                and isinstance(title, str) and isinstance(snippet, str)):
            continue
        url = canonical_url(url)
        title = plain_details(title[:1000], 150)
        snippet = plain_details(snippet[:3000], 420)
        if len(url) > 250 or url in urls or len(snippet) < 40 or len(terms(title + " " + snippet) & headline_terms) < 2:
            continue
        urls.add(url)
        rows.append({"id": len(rows) + 1, "title": title, "snippet": snippet, "url": url})
    if results and not rows:
        raise SearchUnavailable("Tavily results did not contain relevant snippets with eligible public links.")
    return rows


def detail_result(content, headline, sources, *, summary=""):
    data = json_output(content)
    reply, ids = data.get("text"), data.get("sources")
    if reply == "" and ids == []:
        return "", []
    if (not isinstance(reply, str) or not 12 <= len(reply) <= 260 or len(reply.split()) > 45
            or URL_TEXT.search(reply) or any(c in reply for c in "\r\n")
            or not isinstance(ids, list) or not 1 <= len(ids) <= 2
            or any(type(i) is not int or not 1 <= i <= len(sources) for i in ids) or len(ids) != len(set(ids))):
        raise ModelOutputError("Invalid web details or citations")
    cited = [detail_terms(sources[i - 1]["title"] + " " + sources[i - 1]["snippet"]) for i in ids]
    evidence = set().union(*cited)
    headline_words = detail_terms(headline)
    known = headline_words | detail_terms(summary)
    reply_words = detail_terms(reply)
    new = reply_words - known
    sentences = [detail_terms(sentence) - known for sentence in SENTENCES.split(reply)]
    # Overlap catches unsupported new numbers, uncited links, and extra assertions
    # that have no matching evidence. The model is still responsible for meaning.
    if (any(len(new & supporting) < 2 and not numeric_terms(new & supporting) for supporting in cited)
            or len(new & evidence) < 2 or numeric_terms(reply_words) - evidence - headline_words
            or any(not additions & evidence or len(additions - evidence) > 1 for additions in sentences)):
        raise ModelOutputError("Web details not supported by cited snippets")
    return " ".join(reply.split()), ids


def search_links(message, sources):
    message.line("Related search results (links only; no verified summary):")
    for source in sources[:2]:
        message.line("🔗 " + source["url"])
    return message.build()


async def web_details(search, pool, title, key, still_allowed, reserve, record, *, summary=""):
    message = Message().line("🔎 More from the web", "BOLD")
    unavailable = "Web details are unavailable for this post."
    no_details = "No additional details could be supported by the available sources."
    headline = plain_details(title, 220)
    if len(terms(headline)) < 3:
        record("No search: saved headline has too little searchable text.")
        return message.line(unavailable).build()
    if not still_allowed():
        record("Web details stopped before Tavily because the post or settings changed.")
        return None
    if not reserve():
        record("No search: local Tavily daily or monthly attempt limit reached.")
        return message.line(unavailable).build()
    record("Reserved one Tavily search attempt.")
    try:
        sources = await search.search(headline, key)
    except SearchUnavailable as error:
        record(str(error))
        return message.line(unavailable).build()
    except TimeoutError:
        record("Tavily search timed out.")
        return message.line(unavailable).build()
    except aiohttp.ClientError:
        record("Tavily search failed to connect or read the response.")
        return message.line(unavailable).build()
    if not still_allowed():
        record("Web details stopped after Tavily because the post or settings changed.")
        return None
    if not sources:
        record("Tavily search returned no results.")
        return message.line(no_details).build()
    if not pool.expansion_ready():
        record("Search found sources; no eligible free model is ready, returning links only.")
        return search_links(message, sources)

    evidence = [{k: v for k, v in row.items() if k != "url"} for row in sources]
    record("Search found usable sources; checking them with at most one free model call.")
    try:
        reply, ids = await pool.expand(
            headline, evidence, lambda value: detail_result(value, headline, sources, summary=summary))
    except (ModelOutputError, PoolUnavailable):
        record("Web details model failed or returned unusable citations; returning links only. See model events.")
        return search_links(message, sources)
    if not reply:
        record("Web details model found no additional supported details.")
        return message.line(no_details).build()
    record("Web details passed citation checks.")
    message.line(reply)
    for i in ids:
        message.line("🔗 " + sources[i - 1]["url"])
    return message.build()
