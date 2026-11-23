"""Opt-in public search for a recent headline, without reading Signal chat."""

import re

import aiohttp

from .ai import ModelOutputError, PoolUnavailable, json_output
from .editorial import numeric_terms, terms
from .feeds import canonical_url, signalled_feed_url, text
from .formatting import Message
from .http import read_json

SEARCH_URL = "https://api.tavily.com/search"
URL_TEXT = re.compile(r"\b(?:https?://|www\.)\S+", re.I)


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
                return []
            return source_rows(await read_json(response, 100_000), headline)


def source_rows(data, headline):
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []
    rows, urls = [], set()
    headline_terms = terms(headline)
    for result in results[:4]:
        if not isinstance(result, dict):
            continue
        url, title, snippet = (result.get(name) for name in ("url", "title", "content"))
        if not (isinstance(url, str) and len(url) <= 250 and signalled_feed_url(url)
                and isinstance(title, str) and isinstance(snippet, str)):
            continue
        url = canonical_url(url)
        title = text(URL_TEXT.sub("", title[:1000]), 150)
        snippet = text(URL_TEXT.sub("", snippet[:3000]), 420)
        if url in urls or len(snippet) < 40 or len(terms(title + " " + snippet) & headline_terms) < 2:
            continue
        urls.add(url)
        rows.append({"id": len(rows) + 1, "title": title, "snippet": snippet, "url": url})
    return rows


def detail_result(content, headline, sources):
    data = json_output(content)
    reply, ids = data.get("text"), data.get("sources")
    if reply == "" and ids == []:
        return "", []
    if (not isinstance(reply, str) or not 12 <= len(reply) <= 260 or len(reply.split()) > 45
            or URL_TEXT.search(reply) or any(c in reply for c in "\r\n")
            or not isinstance(ids, list) or not 1 <= len(ids) <= 2
            or any(type(i) is not int or not 1 <= i <= len(sources) for i in ids) or len(ids) != len(set(ids))):
        raise ModelOutputError("Invalid web details or citations")
    evidence = set().union(*(terms(sources[i - 1]["title"] + " " + sources[i - 1]["snippet"]) for i in ids))
    new = terms(reply) - terms(headline)
    if len(new & evidence) < 2 or numeric_terms(terms(reply)) - evidence - terms(headline):
        raise ModelOutputError("Web details not supported by cited snippets")
    return " ".join(reply.split()), ids


async def web_details(search, pool, title, key, still_allowed, reserve):
    message = Message().line("🔎 More from the web", "BOLD")
    headline = " ".join(URL_TEXT.sub("", title).split())[:220]
    if len(terms(headline)) >= 3 and still_allowed() and pool.expansion_ready() and reserve():
        try:
            sources = await search.search(headline, key)
            if sources and still_allowed():
                evidence = [{k: v for k, v in row.items() if k != "url"} for row in sources]
                reply, ids = await pool.expand(headline, evidence, lambda value: detail_result(value, headline, sources))
                if reply:
                    message.line(reply)
                    for i in ids:
                        message.line("🔗 " + sources[i - 1]["url"])
                    return message.build()
        except (aiohttp.ClientError, TimeoutError, ValueError, PoolUnavailable):
            pass
    return message.line("No additional details could be supported by the available sources.").build()
