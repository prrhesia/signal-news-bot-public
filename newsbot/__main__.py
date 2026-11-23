import argparse
import asyncio
import fcntl
import logging
import os
import signal as os_signal
import socket
import shutil
from contextlib import contextmanager, nullcontext
from pathlib import Path

import aiohttp
from aiohttp import web

from .ai import ModelPool
from .config import Config
from .engine import Engine
from .doctor import doctor
from .expansion import TavilySearch
from .feeds import FeedWorker, PublicResolver, parse_opml
from .media import clean_media_spool
from .signal import Signal
from .store import Store
from .ai_check import check_ai
from .web import create_app


@contextmanager
def exclusive_run(directory):
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "bot.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another bot instance is already using this data directory") from None
        yield


async def run(args):
    directory = Path(args.data).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = Config(directory / "config.json")
    if args.command == "init":
        print("Configuration:", config.path)
        print("Dashboard username: admin")
        print("Dashboard password:", config.data["dashboard_password"])
        return
    if args.command == "doctor":
        if not await doctor(config, directory):
            raise SystemExit(1)
        return
    if args.command == "test-ai":
        if not await check_ai(config, directory, args.alias):
            raise SystemExit(1)
        return
    store = Store(directory / "news.db", recover=args.command == "serve")
    if args.command == "compact":
        try:
            size = store.storage()["bytes"]
            if shutil.disk_usage(directory).free < size * 2 + 16 * 1048576:
                raise SystemExit("Insufficient free space for safe SQLite compaction; no compaction attempted.")
            store.compact()
            print("Local database compacted. No Signal messages were changed.")
        finally:
            store.close()
        return
    if args.command == "import":
        feeds = parse_opml(Path(args.opml).read_bytes())
        added = sum(store.add_feed(url, title, config.data["poll_seconds"]) for url, title in feeds)
        print(f"Imported {added} new subscriptions. Existing articles will establish a baseline.")
        store.close()
        return
    clean_media_spool(directory)
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), limit=3, family=socket.AF_INET)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25), connector=connector, trust_env=False) as feeds_session, \
               aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60), trust_env=False) as api_session:
        pool = ModelPool(store, config, api_session)
        signal = Signal(config, store, api_session)
        feed_worker = FeedWorker(store, config, feeds_session)
        engine = Engine(store, config, pool, signal, feed_worker, TavilySearch(api_session))
        app = create_app(config, store, pool, signal, engine)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, args.host, args.port).start()
        print(f"Dashboard listening on {args.host}:{args.port}; mode={config.data['mode']}", flush=True)
        tasks = [asyncio.create_task(feed_worker.run()), asyncio.create_task(engine.run()),
                 asyncio.create_task(signal.listen(engine.command))]
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (os_signal.SIGINT, os_signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        # A background task exiting unexpectedly must restart the service, not leave a half-alive bot.
        waiter = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait([waiter, *tasks], return_when=asyncio.FIRST_COMPLETED)
        for task in [waiter, *tasks]:
            task.cancel()
        await asyncio.gather(waiter, *tasks, return_exceptions=True)
        await runner.cleanup()
        store.close()
        if waiter not in done:
            raise RuntimeError("A background worker stopped unexpectedly")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="RSS → free-provider AI → Signal news editor")
    parser.add_argument("--data", default=os.environ.get("NEWSBOT_DATA", "./data"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("doctor")
    sub.add_parser("compact", help="Offline SQLite compaction; stop the service first")
    check = sub.add_parser("test-ai", help="One synthetic AI request; no Signal posting")
    check.add_argument("alias")
    imp = sub.add_parser("import")
    imp.add_argument("opml")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    with exclusive_run(args.data) if args.command in {"serve", "compact"} else nullcontext():
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
