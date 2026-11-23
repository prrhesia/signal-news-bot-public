import asyncio
import json
import re
import time
import uuid

import aiohttp

from .http import read_json
from .formatting import EXPAND_EMOJI, SignalText, insert_styled, reaction_emoji
from .moderation import KEEP_REPLIES, REPLY_TTL, arm_reply, valid_target


class SignalError(Exception):
    def __init__(self, message, signal_timestamp=None):
        super().__init__(message)
        self.signal_timestamp = signal_timestamp


def confirmed_result(result, deletion=False):
    if not isinstance(result, dict) or type(result.get("timestamp")) is not int or not 0 < result["timestamp"] < 2**63:
        raise SignalError("Signal did not confirm a message timestamp; inspect group before retrying")
    recipients = result.get("results", [])
    if (result.get("errors") or not isinstance(recipients, list) or (deletion and not recipients)
            or any(not isinstance(r, dict) or r.get("type") not in ({"SUCCESS"} if deletion else {None, "SUCCESS"})
                   for r in recipients)):
        raise SignalError("Possible partial delivery; inspect group before retrying", result["timestamp"])
    return result


def direct_recipient(source):
    if not isinstance(source, str):
        return ""
    if re.fullmatch(r"\+[1-9]\d{6,14}", source, flags=re.ASCII):
        return source
    try:
        parsed = uuid.UUID(source)
    except (ValueError, AttributeError):
        return ""
    return str(parsed) if parsed.int and source.casefold() == str(parsed) else ""


class Signal:
    def __init__(self, config, store, session):
        self.config, self.store, self.session = config, store, session
        self.connected = False
        self.last_event = None
        self.discovered_account = None
        self.account_ids = {}

    @property
    def current_account(self):
        return self.config.data["signal_account"] or self.discovered_account or ""

    async def resolve_account(self):
        if self.current_account:
            return self.current_account
        accounts = await self.accounts()
        if not isinstance(accounts, list) or len(accounts) != 1 or not isinstance(accounts[0], dict):
            raise SignalError("Link exactly one account or select an account in the dashboard")
        account = accounts[0].get("number") or accounts[0].get("aci") or accounts[0].get("uuid")
        if not isinstance(account, str) or not account:
            raise SignalError("Cannot identify linked Signal account")
        self.discovered_account = account
        return self.current_account

    async def rpc(self, method, params=None):
        params = dict(params or {})
        if method != "listAccounts":
            params["account"] = await self.resolve_account()
        async with self.session.post(self.config.data["signal_url"] + "/api/v1/rpc",
                    json={"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params},
                    timeout=aiohttp.ClientTimeout(total=60), allow_redirects=False) as r:
            if r.status != 200:
                raise SignalError("Signal HTTP error")
            data = await read_json(r, 2_000_000)
            if not isinstance(data, dict) or "result" not in data and "error" not in data:
                raise SignalError("Malformed Signal RPC response")
            if "error" in data:
                raise SignalError("Signal RPC error; inspect signal-cli service logs")
        return data.get("result", {})

    async def accounts(self):
        accounts = await self.rpc("listAccounts")
        if isinstance(accounts, list):
            for account in accounts:
                if not isinstance(account, dict):
                    continue
                ids = {str(account[k]).casefold() for k in ("number", "aci", "uuid") if account.get(k)}
                for identity in ids:
                    self.account_ids[identity] = ids
        return accounts

    async def owns(self, authors):
        selected = self.config.data["signal_account"] or self.discovered_account
        if not selected or selected.casefold() not in self.account_ids:
            if not selected or not any(str(a).casefold() == selected.casefold() for a in authors):
                if not self.store.maintenance_due("signal-accounts", 60) and selected:
                    return False
                self.store.mark_maintenance("signal-accounts")
                try:
                    accounts = await self.accounts()
                    if not selected and isinstance(accounts, list) and len(accounts) == 1:
                        selected = accounts[0].get("number") or accounts[0].get("aci") or accounts[0].get("uuid")
                        self.discovered_account = selected
                except (SignalError, aiohttp.ClientError, ValueError, TimeoutError):
                    return False
        if not selected:
            return False
        identity = selected.casefold()
        return bool({str(a).casefold() for a in authors} & self.account_ids.get(identity, {identity}))

    async def send(self, message, attachment=None, edit_timestamp=None, *, quote=None):
        if not self.config.data["group_id"]:
            raise SignalError("No group selected")
        params = {"groupId": self.config.data["group_id"], "message": str(message)}
        if isinstance(message, SignalText) and message.styles:
            params["textStyle"] = list(message.styles)
        if attachment:
            params["attachment"] = [attachment] if isinstance(attachment, str) else list(attachment)
        if edit_timestamp:
            params.update(editTimestamp=int(edit_timestamp), noUrgent=True)
        if quote:
            timestamp, author, body = quote
            params.update(quoteTimestamp=int(timestamp), quoteAuthor=author, quoteMessage=body)
        return confirmed_result(await self.rpc("send", params))

    async def send_dm_test(self, recipient, scope, author):
        selected = await self.resolve_account()
        recipient = direct_recipient(recipient)
        if (not recipient or not self.connected or not scope[0] or not author or selected != author
                or scope != (self.config.data["group_id"], self.config.data["signal_account"])
                or recipient.casefold() in self.account_ids.get(selected.casefold(), {selected.casefold()})):
            raise SignalError("Direct test is no longer eligible")
        return confirmed_result(await self.rpc("send", {
            "recipient": [recipient], "message": "✅ Signal News Bot direct message test."}))

    async def remote_delete(self, target, scope, author):
        selected = await self.resolve_account()
        stamp = valid_target(target, time.time())
        if (not stamp or not scope[0] or scope != (self.config.data["group_id"], self.config.data["signal_account"])
                or selected != author):
            raise SignalError("Deletion target expired or Signal account/group changed")
        result = await self.rpc("remoteDelete", {"groupId": scope[0], "targetTimestamp": stamp})
        return confirmed_result(result, deletion=True)

    def incoming(self, data):
        if not isinstance(data, dict):
            return None
        params = data.get("params", data)
        if not isinstance(params, dict):
            return None
        params = params.get("result", params)
        if not isinstance(params, dict):
            return None
        account = self.config.data["signal_account"] or self.discovered_account
        incoming_account = params.get("account")
        if account and isinstance(incoming_account, str):
            identity = account.casefold()
            if incoming_account.casefold() not in self.account_ids.get(identity, {identity}):
                return None
        envelope = params.get("envelope") or {}
        if not isinstance(envelope, dict):
            return None
        message = envelope.get("dataMessage") or {}
        if not isinstance(message, dict):
            return None
        group_info = message.get("groupInfo") or {}
        group = group_info.get("groupId") if isinstance(group_info, dict) else None
        body = message.get("message", "")
        reaction = message.get("reaction")
        is_reaction = isinstance(reaction, dict) and (
            self.config.data["topic_feedback"] or self.config.data["story_mute"]
            or isinstance(reaction.get("emoji"), str) and reaction_emoji(reaction["emoji"]) == EXPAND_EMOJI)
        if not group or group != self.config.data["group_id"] or not (is_reaction or isinstance(body, str) and body.startswith("/")):
            return None
        if account and account in {envelope.get("sourceNumber"), envelope.get("sourceUuid"), envelope.get("source")}:
            return None
        timestamp = envelope.get("timestamp", message.get("timestamp", 0))
        try:
            timestamp = int(timestamp)
            if abs(time.time() - timestamp / 1000) > 600:
                return None
        except (TypeError, ValueError):
            return None
        source = envelope.get("sourceUuid") or envelope.get("sourceNumber") or envelope.get("source")
        if not isinstance(source, str) or not source or len(source) > 128:
            return None
        identity = f"{group}:{source}:{timestamp}" + (":reaction" if is_reaction else "")
        if self.store.execute("INSERT OR IGNORE INTO commands(id,at) VALUES(?,?)", (identity, time.time())).rowcount == 0:
            return None
        if is_reaction:
            return "/_reaction", source, {"group": group, "event_at": timestamp, "reaction": reaction}
        cmd = body.partition(" ")[0].casefold()
        if cmd == "/dmtest":
            recipient = (direct_recipient(envelope.get("sourceUuid")) or direct_recipient(envelope.get("sourceNumber"))
                         or direct_recipient(envelope.get("source")))
            return body[:2500], source, {"group": group, "event_at": timestamp, "dm_recipient": recipient,
                                        "signal_account": incoming_account if isinstance(incoming_account, str) else account}
        if cmd in {"/why", "/expand"}:
            quote = message.get("quote")
            return body[:2500], source, {"group": group, "quote": quote if isinstance(quote, dict) else {}}
        if cmd in {"/feed", "/summary", "/purge", "/emergency", "/rapemode", "/storymute"}:
            return body[:2500], source, {"group": group}
        return body[:2500], source

    async def listen(self, command):
        while True:
            try:
                headers = {"Last-Event-ID": self.last_event} if self.last_event else {}
                async with self.session.get(self.config.data["signal_url"] + "/api/v1/events", headers=headers,
                       timeout=aiohttp.ClientTimeout(total=None, sock_read=90)) as r:
                    r.raise_for_status()
                    self.connected = True
                    lines = []
                    event_id = None
                    async for raw in r.content:
                        line = raw.decode("utf-8").rstrip("\r\n")
                        if line.startswith("id:"):
                            event_id = line[3:].strip()
                        elif line.startswith("data:"):
                            lines.append(line[5:].lstrip())
                        elif line == "" and lines:
                            try:
                                incoming = self.incoming(json.loads("\n".join(lines)))
                                if incoming:
                                    scope = (self.config.data["group_id"], self.config.data["signal_account"])
                                    reply = await command(*incoming)
                                    if reply and scope == (self.config.data["group_id"], self.config.data["signal_account"]):
                                        cmd = incoming[0].partition(" ")[0].casefold()
                                        cleanup = self.config.data["reply_cleanup"] and cmd not in KEEP_REPLIES
                                        if cleanup:
                                            if not isinstance(reply, SignalText):
                                                reply = SignalText(reply)
                                            reply = insert_styled(reply, len(reply), f"\n⏳ {REPLY_TTL // 60}m", "ITALIC")
                                        author = self.current_account
                                        result = await self.send(reply)
                                        current = (self.config.data["group_id"], self.config.data["signal_account"])
                                        if cleanup and self.config.data["reply_cleanup"] and current == scope:
                                            arm_reply(self.store, cmd, result, scope, author or self.current_account)
                            except Exception:
                                self.store.event("signal", "Command handling failed; see status and service connectivity")
                            lines = []
                            if event_id:
                                self.last_event = event_id
            except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError):
                pass
            finally:
                self.connected = False
            await asyncio.sleep(5)
