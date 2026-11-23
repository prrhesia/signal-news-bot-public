"""Credential-safe installation/readiness diagnostics."""

import base64
import shutil

import aiohttp

from .http import read_json
from .store import Store


async def doctor(config, directory):
    ok = True
    store = Store(directory / "news.db")
    try:
        result = store.db.execute("PRAGMA quick_check").fetchone()[0]
        print("Database:", result)
        ok = result == "ok"
    finally:
        store.close()
    print("Data disk free:", round(shutil.disk_usage(directory).free / 2**30, 2), "GiB")
    auth = base64.b64encode(("admin:" + config.data["dashboard_password"]).encode()).decode()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5), trust_env=False) as session:
        try:
            async with session.get("http://127.0.0.1:8787/health", headers={"Authorization": "Basic " + auth}, allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError("Dashboard not ready")
                data = await read_json(response)
                print("Dashboard: ready; mode:", data["mode"], "; Signal event stream:", data["signal_connected"])
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
            print("Dashboard: NOT ready (check newsbotctl logs)")
            ok = False
        try:
            async with session.get(config.data["signal_url"] + "/api/v1/check", allow_redirects=False) as response:
                print("Signal daemon:", "ready" if response.status == 200 else "not ready")
                ok = ok and response.status == 200
        except (aiohttp.ClientError, TimeoutError):
            print("Signal daemon: not ready (registration may still be needed)")
            ok = False
    print("Provider credentials and live group delivery are NOT verified by this check.")
    return ok
