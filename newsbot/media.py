"""Bounded preparation of a feed item's own photos and optional MP4."""

import asyncio
import base64
import io

import aiohttp
from bs4 import BeautifulSoup
from PIL import Image

from .feeds import VIDEO_TYPES, canonical_url, fetch_public, own_status_url, public_response, safe_video_url, signalled_feed_url

SIDE = 16
MAX_PIXELS = 4_500_000
FORMATS = ("JPEG", "PNG", "GIF", "WEBP")
MAX_VIDEO_BYTES = 16 * 1024 * 1024


def fingerprint(payload):
    try:
        data = base64.b64decode(payload.partition(",")[2], validate=True)
        with Image.open(io.BytesIO(data), formats=FORMATS) as image:
            width, height = image.size
            if not width or not height or width * height > MAX_PIXELS:
                return None
            # Do not collapse distinct animations based on their first frame.
            if getattr(image, "is_animated", False):
                return width / height, None
            sample = image.resize((SIDE, SIDE), Image.Resampling.BILINEAR).convert("RGB").tobytes()
            return width / height, sample
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None


def same_picture(a, b):
    ratio, sample = a
    other_ratio, other_sample = b
    if sample is None or other_sample is None or min(ratio, other_ratio) < .96 * max(ratio, other_ratio):
        return False
    delta = [abs(x - y) for x, y in zip(sample, other_sample)]
    return sum(delta) <= 5 * len(delta) and sum(d > 20 for d in delta) <= len(delta) // 20


def distinct_images(images):
    kept, samples = [], []
    for payload in dict.fromkeys(image for image in images if image):
        mark = fingerprint(payload)
        if mark is None or any(same_picture(mark, other) for other in samples):
            continue
        kept.append(payload)
        samples.append(mark)
    return kept


def own_nitter_clips(data, page):
    html = BeautifulSoup(data, "html.parser")
    main = html.select_one(".main-tweet")
    body = main.select_one(".tweet-body") if main else None
    if body is None:
        return []
    urls = {}
    for node in body.select(".attachments .video-download[href], .attachments video[src], .attachments video source[src]",
                            limit=12):
        quoted = node.find_parent(class_="quote")
        if quoted is not None or node.find_parent("blockquote") is not None:
            continue
        declared = "video-download" in node.get("class", []) or node.get("type") in VIDEO_TYPES
        if clip := safe_video_url(node.get("href") or node.get("src"), page, declared):
            urls.setdefault(canonical_url(clip), clip)
    return list(urls.values())[:3]


def mp4_boxes(file, start, end):
    for _ in range(512):
        if start == end:
            return
        file.seek(start)
        header = file.read(min(8, end - start))
        if len(header) != 8:
            break
        length = int.from_bytes(header[:4], "big")
        payload = start + 8
        if length == 1:
            if end - payload < 8:
                break
            length = int.from_bytes(file.read(8), "big")
            payload += 8
        elif not length:
            length = end - start
        if length < payload - start or start + length > end:
            break
        yield header[4:], payload, start + length
        start += length
    if start != end:
        raise ValueError("Invalid MP4 box layout")


def is_mp4_video(path, size):
    # Inspect bounded local box metadata, without invoking a codec or a network-enabled downloader.
    with path.open("rb") as file:
        top = list(mp4_boxes(file, 0, size))
        if not top or top[0][0] != b"ftyp" or top[0][2] - top[0][1] < 8:
            return False
        if not any(kind == b"mdat" and end > start for kind, start, end in top):
            return False
        for kind, start, end in top:
            if kind != b"moov":
                continue
            for track, lo, hi in mp4_boxes(file, start, end):
                if track != b"trak":
                    continue
                for part, a, b in mp4_boxes(file, lo, hi):
                    if part != b"mdia":
                        continue
                    for box, offset, stop in mp4_boxes(file, a, b):
                        if box == b"hdlr" and stop - offset >= 12:
                            file.seek(offset + 8)
                            if file.read(4) == b"vide":
                                return True
    return False


async def download_mp4(session, url, path):
    try:
        async with asyncio.timeout(9):
            async with public_response(session, url, {"Accept": "video/mp4, application/octet-stream"},
                                       allowed=signalled_feed_url) as (response, _):
                mime = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
                if (response.status != 200 or mime not in VIDEO_TYPES | {"application/octet-stream"}
                        or (response.content_length or 0) > MAX_VIDEO_BYTES):
                    return False
                size = 0
                with path.open("wb") as file:
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > MAX_VIDEO_BYTES:
                            return False
                        file.write(chunk)
            return is_mp4_video(path, size)
    except (aiohttp.ClientError, TimeoutError, ValueError, OSError):
        return False


async def prepare_video(session, urls, page, item_url, path):
    try:
        async with asyncio.timeout(18):
            for url in urls:
                if await download_mp4(session, url, path):
                    return True
            if not page or not own_status_url(page, item_url):
                return False
            status, data, headers, resolved = await fetch_public(
                session, page, {"Accept": "text/html"}, 1_500_000, final_url=True,
                allowed=lambda url: own_status_url(url, item_url))
            if status == 200 and headers.get("content-type", "").partition(";")[0].strip().lower() == "text/html":
                for url in await asyncio.to_thread(own_nitter_clips, data, resolved):
                    if url not in urls and await download_mp4(session, url, path):
                        return True
    except (aiohttp.ClientError, TimeoutError, ValueError, OSError):
        pass
    return False


def clean_media_spool(directory):
    # exclusive_run is held at service startup. Only remove the bot's known temp files.
    for entry in directory.glob(".relay-media-*"):
        if not entry.is_dir() or entry.is_symlink():
            continue
        try:
            (entry / "clip.mp4").unlink(missing_ok=True)
            entry.rmdir()
        except OSError:
            pass
