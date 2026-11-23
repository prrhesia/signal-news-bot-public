import asyncio
import base64
import calendar
import ipaddress
import json
import re
import socket
import time
from collections import Counter
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import aiohttp
import feedparser
from aiohttp.abc import AbstractResolver
from bs4 import BeautifulSoup
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError


def public_ip(address):
    return address.is_global and not address.is_multicast


def public_url(url):
    try:
        p = urlsplit(url)
        if p.scheme not in {"https", "http"} or not p.hostname or p.username is not None or p.password is not None:
            return False
        if p.port not in {None, 80, 443}:
            return False
        try:
            return public_ip(ipaddress.ip_address(p.hostname))
        except ValueError:
            return p.hostname.lower() != "localhost" and not p.hostname.lower().endswith((".local", ".localhost"))
    except ValueError:
        return False


class PublicResolver(AbstractResolver):
    """Validate the exact addresses used by the connector, including after redirects."""
    def __init__(self):
        self.resolver = aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await self.resolver.resolve(host, port, family)
        if not addresses or any(not public_ip(ipaddress.ip_address(a["host"])) for a in addresses):
            raise OSError("Non-public destination blocked")
        return addresses

    async def close(self):
        await self.resolver.close()


def text(html, limit=1800):
    return " ".join(BeautifulSoup(str(html or "")[:100_000], "html.parser").get_text(" ", strip=True).split())[:limit]


def canonical_url(url):
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, urlencode(query), ""))


def parse_opml(data):
    if len(data) > 3_000_000:
        raise ValueError("OPML file exceeds 3 MB")
    try:
        root = ElementTree.fromstring(data)
    except (ParseError, DefusedXmlException):
        raise ValueError("Malformed or unsafe OPML XML") from None
    if root.tag.lower() != "opml":
        raise ValueError("Expected an OPML subscription export.")
    result = {}
    for node in root.iter("outline"):
        url = node.get("xmlUrl", "").strip()
        if url:
            if not public_url(url):
                raise ValueError("Only public HTTP(S) feed URLs on ports 80/443 are allowed.")
            result[url] = (node.get("title") or node.get("text") or url)[:250]
    if not result or len(result) > 3000:
        raise ValueError("OPML must contain between 1 and 3000 feeds.")
    return list(result.items())


@asynccontextmanager
async def public_response(session, url, headers=None, allowed=None):
    for _ in range(6):
        if not public_url(url) or allowed is not None and not allowed(url):
            raise ValueError("Feed or media destination blocked")
        async with session.get(url, headers=headers, allow_redirects=False) as r:
            if r.status in {301, 302, 303, 307, 308}:
                location = r.headers.get("Location")
                if not location:
                    raise ValueError("Redirect has no destination")
                url = urljoin(url, location)
                headers = None
                continue
            yield r, url
            return
    raise ValueError("Too many redirects")


async def fetch_public(session, url, headers=None, max_bytes=3_000_000, final_url=False, *, allowed=None):
    async with public_response(session, url, headers, allowed) as (r, url):
        if r.status != 304:
            r.raise_for_status()
        data = bytearray()
        if r.status != 304:
            async for chunk in r.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError("Feed exceeds size limit")
        result = r.status, bytes(data), {k.lower(): v for k, v in r.headers.items()}
        return (*result, url) if final_url else result


FEED_HEADERS = {"User-Agent": "SignalNewsBot/1.0 (RSS reader)", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
VIDEO_TYPES = {"video/mp4", "application/mp4"}
MAX_IMAGES = 4
MAX_VIDEO_URLS = 3
GENERIC_IMAGE = re.compile(r"(?:^|[/_.-])(?:logo|avatar|icon|favicon|pixel|spacer|placeholder|tracking)(?:[/_.-]|$)", re.I)
STATUS_PATH = re.compile(r"/[A-Za-z0-9_]{1,60}/status/([0-9]{1,24})/?")


def signalled_feed_url(url):
    if len(url) > 1800 or not public_url(url) or any(c.isspace() for c in url):
        return False
    parts = urlsplit(url)
    secret_keys = {"key", "apikey", "accesskey", "token", "accesstoken", "authtoken",
                   "password", "pass", "secret", "auth", "signature", "credential", "session", "sessionid", "sessionkey"}
    return not parts.fragment and not any(
        "".join(c for c in key.casefold() if c.isalnum()) in secret_keys or key.casefold().startswith(("x-amz-", "x-goog-"))
        for key, _ in parse_qsl(parts.query, keep_blank_values=True))


def image_candidate(candidate, base_url):
    try:
        url = urljoin(base_url, candidate.get("url") or candidate.get("href") or candidate.get("src") or "")
    except (ValueError, TypeError):
        return ""
    if url == base_url or len(url) > 1800 or not public_url(url) or GENERIC_IMAGE.search(urlsplit(url).path):
        return ""
    for dimension in ("height", "width"):
        value = str(candidate.get(dimension, ""))
        if value.isascii() and value.isdigit() and len(value) <= 5 and int(value) < 100:
            return ""
    return url


def feed_images(entry, base_url):
    urls = {}
    for key in ("media_content", "enclosures"):
        for candidate in entry.get(key, [])[:12]:
            mime = str(candidate.get("type", "")).lower()
            medium = candidate.get("medium")
            if mime and mime not in IMAGE_TYPES or medium and medium != "image" or candidate.get("expression") == "sample":
                continue
            if key == "enclosures" and mime not in IMAGE_TYPES:
                continue
            if url := image_candidate(candidate, base_url):
                urls.setdefault(canonical_url(url), url)
    # Thumbnails can represent a video or a feed-wide logo. Use an item
    # thumbnail only as a fallback, never alongside its full-sized image.
    if not urls and not (entry.get("media_content") or entry.get("enclosures")):
        for candidate in entry.get("media_thumbnail", [])[:4]:
            if url := image_candidate(candidate, base_url):
                urls[canonical_url(url)] = url
                break
    return list(urls.values())[:MAX_IMAGES]


def item_images(item):
    try:
        urls = json.loads(item["image_urls"]) if item["image_urls"] else [item["image_url"]]
    except (TypeError, ValueError):
        return []
    return [url for url in urls[:MAX_IMAGES] if isinstance(url, str) and url] if isinstance(urls, list) else []


def safe_video_url(value, base_url, declared=False):
    if not isinstance(value, str) or not value:
        return ""
    try:
        url = urljoin(base_url, value.strip())
        if signalled_feed_url(url) and url != base_url and (declared or urlsplit(url).path.lower().endswith(".mp4")):
            return url
    except (ValueError, TypeError):
        pass
    return ""


def video_candidates(entry, base_url):
    urls = {}
    for key in ("media_content", "enclosures"):
        for candidate in entry.get(key, [])[:12]:
            mime = str(candidate.get("type", "")).partition(";")[0].strip().lower()
            if (candidate.get("medium") not in {None, "", "video"} or candidate.get("expression") == "sample"
                    or mime and mime not in VIDEO_TYPES | {"application/octet-stream", "video/*"}):
                continue
            if url := safe_video_url(candidate.get("url") or candidate.get("href"), base_url, mime in VIDEO_TYPES):
                urls.setdefault(canonical_url(url), url)
    return list(urls.values())[:MAX_VIDEO_URLS]


def own_status_url(url, base):
    if not signalled_feed_url(url) or not signalled_feed_url(base):
        return False
    path, origin = urlsplit(url), urlsplit(base)
    own, candidate = STATUS_PATH.fullmatch(origin.path), STATUS_PATH.fullmatch(path.path)
    return bool(own and candidate and own[1] == candidate[1] and path.hostname == origin.hostname
                and (origin.scheme != "https" or path.scheme == "https"))


def inline_media(html, url, base_url):
    photos, clips, posters = {}, {}, set()
    page = ""
    same_feed = urlsplit(url).hostname == urlsplit(base_url).hostname
    looks_nitter = same_feed and bool(STATUS_PATH.fullmatch(urlsplit(url).path))
    for tag in html.find_all("video", limit=4):
        if tag.find_parent(["blockquote", "aside"]):
            continue
        if poster := image_candidate({"src": tag.get("poster")}, url):
            posters.add(canonical_url(poster))
        for source in [tag, *tag.find_all("source", src=True, limit=4)]:
            if clip := safe_video_url(source.get("src"), url, source.get("type") in VIDEO_TYPES):
                clips.setdefault(canonical_url(clip), clip)

    for tag in html.find_all("img", src=True, limit=12):
        if tag.find_parent(["blockquote", "aside", "video"]) or not (candidate := image_candidate(tag, url)):
            continue
        key = canonical_url(candidate)
        if key in posters:
            continue
        anchor = tag.find_parent("a", href=True)
        nitter_photo = (looks_nitter and urlsplit(candidate).hostname == urlsplit(url).hostname
                        and urlsplit(candidate).path.startswith("/pic/"))
        if anchor:
            try:
                link = canonical_url(urljoin(url, anchor["href"]))
            except (ValueError, TypeError):
                continue
            # Upstream Nitter puts only a same-status thumbnail in ordinary video RSS entries.
            if nitter_photo and own_status_url(link, url) and anchor.get_text(" ", strip=True).casefold() == "video":
                page = url
                posters.add(key)
                continue
            if link not in {url, key}:
                continue
        gallery = tag.find_parent(class_="gallery")
        photos.setdefault(key, (candidate, nitter_photo, gallery))
    identified = [v for v in photos.values() if v[1]]
    if len(identified) < 2:
        identified = [v for v in photos.values() if v[2] is not None]
        if not identified or any(v[2] is not identified[0][2] for v in identified):
            identified = []
    result = identified if len(identified) >= 2 else list(photos.values()) if len(photos) == 1 else []
    return [v[0] for v in result], list(clips.values()), page, posters


def item_videos(item):
    try:
        clips = json.loads(item.get("video_urls") or "[]")
    except (TypeError, ValueError):
        clips = []
    return ([url for url in clips[:MAX_VIDEO_URLS] if isinstance(url, str) and signalled_feed_url(url)]
            if isinstance(clips, list) else []), item.get("video_page") or ""


async def fetch_image(session, url):
    try:
        async with asyncio.timeout(12):
            status, data, headers = await fetch_public(session, url, {"Accept": ", ".join(sorted(IMAGE_TYPES))}, 1_000_000)
        mime = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        magic = {"image/jpeg": data.startswith(b"\xff\xd8\xff"),
                 "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
                 "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
                 "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP"}
        if status == 200 and magic.get(mime):
            return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    except (aiohttp.ClientError, TimeoutError, ValueError):
        pass
    return None


class FeedWorker:
    def __init__(self, store, config, session):
        self.store, self.config, self.session = store, config, session

    def current(self, feed):
        return self.store.one("SELECT id FROM feeds WHERE id=? AND enabled=1 AND removed=0 AND revision=?",
                              (feed["id"], feed.get("revision", 0)))

    async def subscribe(self, url, still_allowed):
        if not signalled_feed_url(url):
            return "⚠️ Use a public RSS or Atom URL without credentials."
        row = self.store.one("SELECT * FROM feeds WHERE url=?", (url,))
        if row and not row["removed"]:
            return f"ℹ️ Already subscribed as #{row['id']}."
        try:
            async with asyncio.timeout(25):
                status, data, headers, base_url = await fetch_public(self.session, url, FEED_HEADERS, final_url=True)
                parsed = await asyncio.to_thread(feedparser.parse, data)
            if status != 200 or not parsed.version or (parsed.bozo and not parsed.entries):
                return "⚠️ The server did not return readable RSS or Atom."
            if not still_allowed():
                return "⚠️ Group or maintenance settings changed; feed was not added."
            title = text(parsed.feed.get("title", ""), 150) or "RSS feed"
            now = time.time()
            with self.store.db:
                inserted = self.store.db.execute("""INSERT INTO feeds(url,title,interval) VALUES(?,?,?)
                    ON CONFLICT(url) DO UPDATE SET title=excluded.title,removed=0,enabled=1,
                    initialized=0,revision=revision+1 WHERE removed=1""", (url, title, self.config.data["poll_seconds"]))
                feed = self.store.one("SELECT * FROM feeds WHERE url=?", (url,))
                if not inserted.rowcount:
                    return f"ℹ️ Already subscribed as #{feed['id']}."
                self.ingest(feed, parsed, headers, now, base_url)
                self.store.db.execute("UPDATE feeds SET checked=?,next_poll=?+interval,failures=0,error='' WHERE id=?",
                                      (now, now, feed["id"]))
            return f"✅ Added feed #{feed['id']}. Existing entries won’t be posted."
        except aiohttp.ClientResponseError as exc:
            return f"⚠️ Feed server returned HTTP {exc.status}; nothing was added."
        except TimeoutError:
            return "⚠️ Feed check timed out; nothing was added."
        except (ValueError, aiohttp.ClientError):
            return "⚠️ Feed was unreachable, unsafe, or too large; nothing was added."

    async def poll(self, feed):
        headers = dict(FEED_HEADERS)
        if feed["etag"]:
            headers["If-None-Match"] = feed["etag"]
        if feed["modified"]:
            headers["If-Modified-Since"] = feed["modified"]
        now = time.time()
        try:
            async with asyncio.timeout(25):
                status, data, response_headers, base_url = await fetch_public(self.session, feed["url"], headers, final_url=True)
            if status != 304:
                parsed = await asyncio.to_thread(feedparser.parse, data)
                if not parsed.version:
                    raise ValueError("Response is not a recognized RSS/Atom feed")
                if not self.current(feed):
                    return
                with self.store.db:
                    self.ingest(feed, parsed, response_headers, now, base_url)
            if not self.current(feed):
                return
            self.store.execute("UPDATE feeds SET checked=?,next_poll=?+interval,failures=0,error='' WHERE id=?",
                               (now, now, feed["id"]))
        except Exception as exc:
            if not self.current(feed):
                return
            # Do not persist arbitrary exception strings containing URLs/credentials.
            error = f"HTTP {exc.status}" if isinstance(exc, aiohttp.ClientResponseError) else type(exc).__name__
            failures = feed["failures"] + 1
            self.store.execute("UPDATE feeds SET checked=?,next_poll=?+MIN(3600,interval*?),failures=?,error=? WHERE id=?",
                                (now, now, 2 ** min(failures, 4), failures, error, feed["id"]))

    def ingest(self, feed, parsed, response_headers, now, base_url):
        inserts = []
        pending_urls = set()
        images, videos = {}, {}
        claims, video_claims = Counter(), Counter()
        for entry in parsed.entries[:300]:
            title = text(entry.get("title", ""), 500)
            try:
                url = canonical_url(urljoin(base_url, entry.get("link", ""))) if entry.get("link") else ""
            except (ValueError, TypeError):
                continue
            if not title or not public_url(url) or url in pending_urls:
                continue
            pending_urls.add(url)
            entry_images = feed_images(entry, base_url)
            claims.update(canonical_url(image) for image in entry_images)
            entry_videos = video_candidates(entry, base_url)
            video_claims.update(canonical_url(clip) for clip in entry_videos)
            guid = str(entry.get("id") or url or title)[:2000]
            if self.store.seen_article(feed["id"], guid, url):
                continue
            if self.store.one("SELECT id FROM items WHERE url=? OR (feed_id=? AND guid=?) LIMIT 1", (url, feed["id"], guid)):
                continue
            summary = entry.get("summary", "") or next((c.get("value", "") for c in entry.get("content", [])), "")
            date = entry.get("published_parsed") or entry.get("updated_parsed")
            try:
                published = calendar.timegm(date) if date else now
            except (ValueError, OverflowError, TypeError):
                published = now
            state = "queued" if feed["initialized"] else "baseline"
            if published < now - self.config.data["max_age_hours"] * 3600 or published > now + 900:
                state = "expired"
            html = BeautifulSoup(str(summary)[:100_000], "html.parser")
            description = " ".join(html.get_text(" ", strip=True).split())[:1800]
            inserts.append((feed["id"], guid, url, title, description, published, now, state))
            inline_photos, inline_clips, nitter_page, posters = inline_media(html, url, base_url)
            if not entry_images or len(inline_photos) >= 2:
                existing_images = {canonical_url(image) for image in entry_images}
                extra_photos = {canonical_url(image): image for image in inline_photos
                                if canonical_url(image) not in existing_images}
                claims.update(extra_photos.keys())
                entry_images += list(extra_photos.values())
            existing_clips = {canonical_url(clip) for clip in entry_videos}
            extra_clips = {canonical_url(clip): clip for clip in inline_clips if canonical_url(clip) not in existing_clips}
            video_claims.update(extra_clips.keys())
            images[url] = [image for image in entry_images if canonical_url(image) not in posters][:MAX_IMAGES]
            videos[url] = (entry_videos + list(extra_clips.values()), nitter_page)
        # Ambiguous media claimed by multiple entries in the same fetch is not
        # proof it belongs to either story. Omit it instead of guessing.
        images = {url: [image for image in urls if claims[canonical_url(image)] == 1]
                  for url, urls in images.items()}
        videos = {url: ([clip for clip in clips if video_claims[canonical_url(clip)] == 1][:MAX_VIDEO_URLS], page)
                  for url, (clips, page) in videos.items()}
        c = self.config.data
        setting = self.store.one("SELECT instant FROM feeds WHERE id=?", (feed["id"],))
        route = ("emergency" if c["emergency"] else "instant" if setting and setting["instant"] else "") if c["group_id"] else ""
        self.store.insert_items(inserts, images, transaction=False, route=route, videos=videos,
                                rules=self.config.rules_key() if route else "", preview=c["mode"] == "preview")
        self.store.db.execute("UPDATE feeds SET etag=?,modified=?,initialized=1 WHERE id=?",
                              (response_headers.get("etag", ""), response_headers.get("last-modified", ""), feed["id"]))

    async def run(self):
        active = {}
        try:
            while True:
                for task in list(active):
                    if task.done():
                        task.result()
                        del active[task]
                busy = set(active.values())
                due = self.store.rows("SELECT * FROM feeds WHERE enabled=1 AND next_poll<=? ORDER BY next_poll LIMIT 6", (time.time(),))
                for feed in due:
                    if len(active) >= 3:
                        break
                    if feed["id"] not in busy:
                        active[asyncio.create_task(self.poll(feed))] = feed["id"]
                if active:
                    await asyncio.wait(active, timeout=3, return_when=asyncio.FIRST_COMPLETED)
                else:
                    await asyncio.sleep(3)
        finally:
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
