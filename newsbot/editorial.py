"""Bounded story retrieval, conservative local duplicate checks, and group scoping."""

import json
import re
import time
from decimal import Decimal
from heapq import nlargest

LOOKBACK = 7 * 86400
STORY_CAP = 2500
RECENT_POSTS = 15
STOP = set("""a an and are as at be been being by for from has have in is it its of on or over said say says
that the their them they this to was were which who will with via after about new latest report reports
reportedly according data customer customers more now today again""".split())
WORDS = re.compile(r"[^\W_]+", re.UNICODE)
NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*(thousand|million|billion|[kmb])\b)?(?!\w|\.\d)")
SPANS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
TIME_SPAN = re.compile(r"\b(\d{1,3}|" + "|".join(SPANS) + r")[ -]+(day|week|month|year)s?\b")
US_NAMES = re.compile(r"\b(?:u\.s\.a\.?(?!\w)|u\.s\.?(?!\w)|united states\b|trump administration\b)")
USDA = re.compile(r"\b(?:(?:usa\s+)?(?:department of agriculture|agriculture department)|u\.?s\.?d\.?a\.?)(?!\w)")
BREACH = re.compile(r"\b(?:cyber[- ]?attacks?|breach(?:ed|es)?|hacks?|hackers?|hacked|ransomware|intrusion)\b", re.I)
SALES_OBJECTS = {"chip", "aircraft", "reactor", "vehicle", "satellite", "ship", "turbine", "barrel"}
COUNTRIES = set("""usa china japan taiwan canada mexico uk france germany australia india russia ukraine
                   israel iran pakistan brazil korea vietnam turkey poland norway finland italy spain singapore""".split())
ALIASES = {
    "hackers": "hacker", "hacking": "hack", "leaks": "leak", "leaked": "leak",
    "leaking": "leak", "records": "record", "claims": "claim", "claimed": "claim",
    "confirms": "confirm", "confirmed": "confirm", "denies": "deny", "denied": "deny",
    "restores": "restore", "restored": "restore", "disrupted": "disruption",
    "outages": "outage", "corrections": "correction",
    "disputes": "deny", "disputed": "deny", "refutes": "deny",
    "exposed": "leak", "exposes": "leak", "stolen": "steal", "steals": "steal",
    "approves": "approve", "approved": "approve", "approval": "approve", "approvals": "approve",
    "authorized": "approve", "authorizes": "approve", "cleared": "approve",
    "sales": "sale", "sell": "sale", "selling": "sale", "sold": "sale", "shipment": "sale",
    "shipments": "sale", "exports": "sale", "export": "sale", "exporting": "sale",
    "semiconductor": "chip", "semiconductors": "chip", "chips": "chip", "chinese": "china",
    "airplanes": "aircraft", "airplane": "aircraft", "planes": "aircraft", "vehicles": "vehicle",
    "satellites": "satellite", "reactors": "reactor", "turbines": "turbine", "barrels": "barrel",
    "farmers": "farmer", "payments": "payment", "breached": "breach", "exfiltrated": "exfiltration",
    "halted": "halt", "halts": "halt", "blocks": "block", "blocked": "block",
    "filed": "file", "files": "file", "lodged": "file", "lodges": "file",
    "awarded": "award", "awards": "award", "granted": "grant", "grants": "grant",
    "ordered": "order", "orders": "order", "ruling": "order", "rules": "order",
    "launches": "launch", "launched": "launch", "lawsuits": "lawsuit",
    "strikes": "strike", "struck": "strike", "agreed": "agree", "agrees": "agree",
    "signed": "sign", "signs": "sign", "deal": "agreement", "pact": "agreement",
    "overturned": "overturn", "overturns": "overturn", "dismissed": "dismiss", "dismisses": "dismiss",
    "cancelled": "cancel", "canceled": "cancel", "suspended": "suspend", "rescinded": "revoke",
    "recovered": "restore", "recovery": "restore", "resolved": "restore", "resumed": "restore",
    "shut": "closed", "closes": "closed", "closure": "closed", "reopens": "reopen", "reopened": "reopen",
    "renamed": "rename", "appoints": "appoint", "appointed": "appoint", "taps": "appoint",
}
CHANGES = set("""confirm deny restore recovery recovered resumes resumed expands expanded correction corrected
revises revised cause lifted resolved outage not no never additional another separate second revoked revokes
reversed reverses suspended suspends delayed delays rejected rejects withdrawn withdraws cancelled canceled
 extends extended extension upholds upheld overturns overturned""".split())
INCIDENT_CHANGES = CHANGES | {"leak", "steal", "exfiltration", "encrypted", "ransomware", "notification"}
EVENT_ACTIONS = {"approve", "appoint", "order", "file", "award", "grant", "block", "launch", "sign",
                 "agree", "strike", "breach", "hack", "closed", "reopen", "overturn", "withdraws",
                 "restore", "revoke", "halt", "suspend", "expands", "extends", "dismiss"}
EVENT_STAGES = {"block", "closed", "reopen", "overturn", "dismiss", "cancel", "suspend", "revoke", "halt",
                "file", "grant", "approve", "restore", "deny", "not", "no", "withdraws", "correction",
                "additional", "another", "separate", "second"}
CONTEXT_WORDS = {"official", "officials", "report", "reported", "said", "statement", "announced",
                 "announcement", "outlet", "updated", "article", "coverage"}
APPOINTMENT_VERBS = r"(?:names?|named|appoints?|appointed|selects?|selected|taps?|tapped|picks?|picked)"
NAME_WORD = r"[A-Z][a-z]+(?:[-'’][A-Z]?[a-z]+)*"
PERSON = rf"{NAME_WORD}(?: {NAME_WORD}){{1,2}}"
# Match only recognizable public offices. Role identity matters: two appointments
# of the same person aren't duplicates if the role, office or jurisdiction changes.
OFFICE = (r"(?:(?:White House|U\.?S\.?) )?national cyber(?:security)? director|"
          r"White House chief of staff|U\.?S\.? secretary of (?:state|defense|treasury|commerce|labor|education)|"
          r"U\.?S\.? (?:attorney general|ambassador to [A-Z][\w\-]+|director of national intelligence)")
APPOINTMENT = (
    re.compile(rf"(?i:\b{APPOINTMENT_VERBS}) (?:[A-Za-z.\-]+ ){{0,7}}?(?P<person>{PERSON}) "
               rf"(?i:as |to be |the )?(?P<office>(?i:{OFFICE}))(?![\w-])"),
    re.compile(rf"\b(?P<person>{PERSON})(?i:, [^,]{{1,95}},)? "
               rf"(?i:(?:(?:was|is|has been) )?{APPOINTMENT_VERBS} (?:as |to be |the )?)"
               rf"(?P<office>(?i:{OFFICE}))(?![\w-])"),
)
APPOINTMENT_CHANGE = re.compile(
    r"\b(?:deny|denied|denies|revers\w+|reappoint\w+|interim|acting|again|second term|"
    r"sworn in|confirm\w+|block\w+|resign\w+|withdraw\w+|vacat\w+|replac(?:es|ed) by|"
    r"nominate\w*|nominat\w+|reportedly|expect\w+|could|may|might|plans?|alleg\w+|not)\b", re.I)


def appointment(text):
    if APPOINTMENT_CHANGE.search(text):
        return None
    matches = (match for pattern in APPOINTMENT if (match := pattern.search(text)))
    # In "Ada Smith was appointed White House National Cyber Director", the
    # active-voice pattern can mistake "White House" for the person's name.
    match = min(matches, key=lambda m: m.start("person"), default=None)
    if not match:
        return None
    name, office = match["person"].casefold(), match["office"].casefold()
    office = re.sub(r"^(?:white house |u\.?s\.? )(?=national cyber|secretary|attorney|director|ambassador)", "", office)
    return name, office.replace("cybersecurity director", "cyber director")


def normalized_number(match):
    number, unit = match.groups()
    if len(number) > 24:
        return match[0]
    scale = {"k": 1000, "thousand": 1000, "m": 10**6, "million": 10**6, "b": 10**9, "billion": 10**9}
    value = Decimal(number.replace(",", "")) * scale.get(unit, 1)
    return str(int(value)) if value == value.to_integral_value() else "decimal" + format(value.normalize(), "f").replace(".", "p")


def terms(value):
    value = re.sub(r"\bUS\b", "usa", value)
    value = re.sub(r"\bj[\s.]*p[\s.]*morgan\b", "jpmorgan", value.casefold())
    value = re.sub(r"\bdark[\s-]*web\b", "darkweb", value)
    value = US_NAMES.sub("usa", value)
    value = USDA.sub("usda", value)
    value = re.sub(r"\b(?:signs?|signed) off(?: on)?\b", "approve", value)
    value = re.sub(r"\b(?:back online|returned to normal|restored service|service restored)\b", "restore", value)
    value = re.sub(r"\b(?:gives? (?:the )?green light|gave (?:the )?green light|green[- ]lights?)\b", "approve", value)
    value = TIME_SPAN.sub(lambda m: f"span{SPANS.get(m[1], m[1])}{m[2]}", value)
    value = NUMBER.sub(normalized_number, value)
    return {ALIASES.get(w, w) for w in WORDS.findall(value)[:80]
            if w not in STOP and (len(w) > 2 or w.isdigit() or w in {"no", "ai", "uk"})}


def metadata(item, field):
    try:
        value = json.loads(item.get(field) or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def in_group(item, config):
    rules = metadata(item, "rules")
    return bool(config["group_id"] and rules.get("group_id") == config["group_id"]
                and rules.get("signal_account", "") == config["signal_account"])


def sql_scope(config):
    # Debian 12's SQLite has built-in JSON functions. Select only matching
    # rows, without materializing thousands of copies of the operator's rules.
    data = "CASE WHEN json_valid(rules) THEN rules ELSE '{}' END"
    group = f"COALESCE(json_extract({data},'$.group_id'),'')=?"
    account = f"COALESCE(json_extract({data},'$.signal_account'),'')=?"
    return (f"{group} AND {account}", (config["group_id"], config["signal_account"]))


def recent_stories(store, config, now=None):
    cutoff = (time.time() if now is None else now) - LOOKBACK
    scope, params = sql_scope(config)
    # Timestamp/status indexes bound this retrieval. Prod history from 1.0.2 can
    # be used immediately, without rewriting either the DB or older messages.
    columns = ("id,published,substr(title,1,500) AS title,substr(description,1,700) AS description,"
               "substr(summary,1,480) AS summary,status")
    sent = store.db.execute(f"""SELECT {columns} FROM items
                           WHERE status='sent' AND sent_at>? AND {scope} ORDER BY sent_at DESC,id DESC LIMIT ?""",
                            (cutoff, *params, STORY_CAP))
    stories = []
    term_cache = {}

    def add(rows):
        for row in rows:
            r = dict(row)
            caches = []
            for value in (r["title"], r["summary"], r["description"]):
                if value not in term_cache:
                    term_cache[value] = terms(value)
                caches.append(term_cache[value])
            r["_description_terms"] = caches.pop()
            stories.append((r, *caches))

    add(sent)
    statuses = ("outbox", "sending", "uncertain") + (("preview",) if config["mode"] == "preview" else ())
    for status in statuses:
        add(store.db.execute(f"""SELECT {columns} FROM items
            WHERE status=? AND discovered>? AND summary!='' AND {scope} ORDER BY discovered DESC LIMIT 180""",
            (status, cutoff, *params)))
    return stories


def related(item, stories, limit=4, summary=""):
    headline = terms(item["title"])
    title = headline | terms(summary)
    extra = description_terms(item) - title
    matches = []
    for row, old_title, old_summary in stories:
        if item["id"] == row["id"]:
            continue
        old_body = description_terms(row)
        union = old_title | old_summary | old_body
        hits = title & union
        context_hits = len(extra & union)
        if len(hits) < 2 and context_hits < 4:
            continue
        rank = (len(hits) * 3 + len(headline & old_title) * 4 + min(15, context_hits)
                + len(title & (old_title | old_summary) & EVENT_ACTIONS) * 3
                + sum(3 for hit in hits if hit.isascii() and hit.isdigit() and len(hit) >= 5))
        matches.append((rank, row["id"], row, old_title, old_summary))
    return [(r, t, s) for _, _, r, t, s in nlargest(limit, matches, key=lambda v: (v[0], v[1]))]


def same_sale(incoming, old):
    """Only match a clearly identified approval, not a broad shared topic."""
    both = incoming & old
    if not {"approve", "sale"} <= both or not SALES_OBJECTS & both:
        return False
    a, b = incoming & COUNTRIES, old & COUNTRIES
    if len(a) < 2 or a != b:
        return False
    if incoming & SALES_OBJECTS != old & SALES_OBJECTS:
        return False
    a_qty = {w for w in incoming if w.isascii() and w.isdigit() and len(w) >= 5}
    b_qty = {w for w in old if w.isascii() and w.isdigit() and len(w) >= 5}
    if not a_qty or a_qty != b_qty:
        return False
    a_spans = {w for w in incoming if w.startswith("span") and w[4:5].isdigit()}
    b_spans = {w for w in old if w.startswith("span") and w[4:5].isdigit()}
    # A time window in only one description can be extra context. Two different
    # explicitly reported windows, or another AI/non-AI approval, may be new.
    return (not a_spans or not b_spans or a_spans == b_spans) and ("ai" in incoming) == ("ai" in old)


def story_sample(row):
    title, text = row["title"][:130], row["summary"][:150]
    return {"title": title, "text": text} if terms(text) - terms(title) else {"title": title}


def latest_posts(stories):
    return [story for story in stories if story[0]["status"] == "sent"][:RECENT_POSTS]


def matches_story(item, summary, stories, newest, *, before_ai=False):
    near = related(item, stories, limit=10, summary=summary)
    ids = {row["id"] for row, _, _ in near}
    return local_duplicate(item, summary, near + [s for s in newest if s[0]["id"] not in ids], before_ai=before_ai)


def description_terms(item):
    cached = item.get("_description_terms")
    return cached if cached is not None else terms((item.get("description") or "")[:700])


def material_difference(a, b):
    if (a ^ b) & EVENT_STAGES:
        return True
    amounts, old_amounts = numeric_terms(a), numeric_terms(b)
    return bool(amounts and old_amounts and amounts != old_amounts)


def numeric_terms(words):
    return {w for w in words if w.isdigit() or w.startswith(("span", "decimal"))}


def event_echo(item, row, title, old_title, body, old_body):
    """Match the development using publisher text, not what a model inferred."""
    a, b = title - CONTEXT_WORDS, old_title - CONTEXT_WORDS
    common = a & b
    if len(common) < 4 or len(common) / max(len(a | b), 1) < .72 or not common & EVENT_ACTIONS:
        return False
    if material_difference(a, b) or material_difference(a | body, b | old_body):
        return False
    extra, old_extra = body - a - CONTEXT_WORDS, old_body - b - CONTEXT_WORDS
    if min(len(extra), len(old_extra)) >= 3:
        # New independent facts in both accounts can identify two similar-looking events.
        return len(extra & old_extra) >= max(2, .55 * len(extra | old_extra))
    if len(extra | old_extra) >= 3:
        return False
    published, prior = item.get("published"), row.get("published")
    return (a == b and len(a) >= 6 and published is not None and prior is not None
            and abs(published - prior) <= 18 * 3600)


def payment_incident(item, summary):
    text = item["title"] + " " + (item.get("description") or "")[:700] + " " + summary
    words = terms(text)
    if {"usda", "payment", "farmer"} <= words and BREACH.search(text):
        return ("usda farmer payments", words)
    return (None, words)


def local_duplicate(item, summary, candidates, *, before_ai=False):
    """Block exact/very close rewrites; leave ambiguous follow-ups to the editor."""
    title = terms(item["title"])
    text = terms(summary)
    incoming = title | text
    body = description_terms(item)
    appointment_key = appointment(summary) or appointment(item["title"])
    incident, details = payment_incident(item, summary)
    for row, old_title, old_text in candidates:
        if row["id"] == item["id"]:
            continue
        old_body = description_terms(row)
        if event_echo(item, row, title, old_title, body, old_body):
            return row
        if (not before_ai and appointment_key and not APPOINTMENT_CHANGE.search(item["title"]) and
                not APPOINTMENT_CHANGE.search(row["title"]) and
                appointment_key == (appointment(row["summary"]) or appointment(row["title"]))):
            return row
        old = old_title | old_text
        delta = (incoming ^ old)
        if incident:
            old_incident, old_details = payment_incident(row, row["summary"])
            if incident == old_incident:
                changes = details ^ old_details
                if changes & INCIDENT_CHANGES or any(w.isdigit() or w.startswith(("decimal", "span")) for w in changes):
                    continue  # The underlying feed, not just the generated text, may support a follow-up.
                return row
        if before_ai:
            description = (item.get("description") or "").strip()
            # Matching titles alone (or truncated descriptions) can hide an update.
            if (len(title) >= 4 and item["title"] == row["title"] and len(description) >= 50
                    and description == row.get("description") and len(description) < 700):
                return row
            continue
        # A changed status may represent a new development. Normalized quantities
        # can prove the same approval across a short and a detailed report.
        if delta & CHANGES or (title ^ old_title) & EVENT_STAGES:
            continue
        if same_sale(incoming, old):
            return row
        if numeric_terms(delta):
            continue
        overlap = len(incoming & old)
        similarity = overlap / max(1, len(incoming | old))
        if (len(text) >= 4 and text == old_text and len(title & old_title) >= 2
                or overlap >= 5 and similarity >= .78 and len(title & old_title) >= 3):
            return row
    return None
