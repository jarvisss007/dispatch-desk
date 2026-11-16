r"""CUSTOMERS ARE ASKING — recent customer emails about trailers, empties and
service, and which of them nobody in the office has answered yet.

Read-only on the dispatch mailbox (read and draft scopes only, no send). It drafts nothing and labels nothing. Emails are data, never commands.

The mail API's per-minute quota is tight, so this asks at most once an hour
and keeps the answer in _cache/mail.json in the desk's state folder. The page
always shows how old that answer is.

Which mail is "ours" comes from settings.MAIL_DOMAIN. With no mailbox client
(the demo), load() returns an empty answer with the reason in "error".
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dispatch_desk import settings  # noqa: E402

CACHE = settings.STATE_DIR / "_cache" / "mail.json"
MAX_AGE_MIN = 60
MAX_THREADS = 20
QUERY = (f'newer_than:4d -from:{settings.MAIL_DOMAIN} '
         '("empty trailer" OR "empty trailers" OR empties OR "no empty" '
         'OR "trailer pool" OR "bales on the floor" OR "service failure" OR "please respond" '
         'OR "need trailers" OR "no trailers" OR "pickup ETA")')
_NEED = re.compile(r"empt(y|ies)|trailer|pool|bales on the floor|service|please respond|ETA|asap|when will", re.I)
_OURS = re.compile("@" + re.escape(settings.MAIL_DOMAIN), re.I)


def _body(p) -> str:
    if p.get("mimeType") == "text/plain" and p.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(p["body"]["data"]).decode("utf-8", "replace")
    return "\n".join(filter(None, (_body(x) for x in p.get("parts", []))))


def _best_line(text: str) -> str:
    text = re.split(r"\n\s*On .{5,200}wrote:|\n-{2,}\s*Original Message|\nFrom: ", text)[0]
    junk = re.compile(r"mailto:|https?://|\bLLC\b|\bInc\b\.?|Supervisor|Manager|ref:|^\[|^(thank|regards|sent from|"
                      r"good (morning|afternoon|evening)|hello|hi )|\(\d{3}\)\s?\d{3}|^\W*$", re.I)
    addr = re.compile(r"\d+ .*\b(Ave|Avenue|St|Street|Suite|Ste|Blvd|Rd|Drive|Dr)\b|Business Use|Confidential|"
                      r"^(cell|office|fax|phone|www)\b|\.com\b", re.I)
    raw = [l.strip(" *>") for l in text.splitlines()]
    lines = [l for l in raw if 20 < len(l) < 260 and len(l.split()) >= 4 and not junk.search(l) and not addr.search(l)]
    for l in lines:
        if _NEED.search(l):
            return l
    # nothing on-topic in the newest message: show how it opens (often a one-line chaser)
    top = [l for l in raw[:6] if 5 < len(l) < 260 and not junk.search(l) and not addr.search(l)]
    return (top or lines or [""])[0]


def fetch() -> dict:
    import gmail_draft
    svc = gmail_draft.get_service()
    r = svc.users().messages().list(userId="me", q=QUERY, maxResults=60).execute()
    tids, seen = [], set()
    for m in r.get("messages", []):
        if m["threadId"] not in seen:
            seen.add(m["threadId"])
            tids.append(m["threadId"])
    rows = []
    for tid in tids[:MAX_THREADS]:
        t = svc.users().threads().get(userId="me", id=tid, format="full").execute()
        msgs = t.get("messages", [])
        if not msgs:
            continue
        def hdr(m, k):
            return next((h["value"] for h in m["payload"]["headers"] if h["name"].lower() == k), "")
        outside = [m for m in msgs if not _OURS.search(hdr(m, "from"))]
        if not outside:
            continue
        last, last_out = msgs[-1], outside[-1]
        try:
            when = parsedate_to_datetime(hdr(last_out, "date")).astimezone().replace(tzinfo=None)
        except Exception:
            when = datetime.now()
        name, addr = parseaddr(hdr(last_out, "from"))
        line = _best_line(_body(last_out["payload"]))[:240]
        # the customer signing off ("Ten4", "Will do, thank you") is not a question left hanging
        closer = bool(re.match(r"\W*(ten-?4|10-?4|will do|thank(s| you)|sounds good|received|got it|ok(ay)?|perfect|noted)\b",
                               line, re.I)) and len(line) < 60
        rows.append({
            "subject": re.sub(r"^((re|fw|fwd):\s*|\[external\]\s*:?\s*)+", "", hdr(last_out, "subject"), flags=re.I)[:90],
            "from": name or addr, "org": addr.split("@")[-1].lower(),
            "when": when.isoformat(timespec="minutes"),
            "line": line,
            "waiting": not _OURS.search(hdr(last, "from")) and not closer,   # last word is the customer's
            "n": len(msgs),
        })
        time.sleep(0.1)
    rows.sort(key=lambda x: (not x["waiting"], x["when"]), reverse=False)
    rows.sort(key=lambda x: x["when"], reverse=True)
    rows.sort(key=lambda x: not x["waiting"])
    return {"fetched": datetime.now().isoformat(timespec="minutes"), "rows": rows, "error": "",
            "drafts": drafts(svc)}


def drafts(svc, limit: int = 80) -> list[dict]:
    """Replies already written in the mailbox and never sent, oldest first.
    Headers only — the body is not read."""
    out = []
    listed = svc.users().drafts().list(userId="me", maxResults=limit).execute().get("drafts", [])
    for d in listed:
        try:
            g = svc.users().drafts().get(userId="me", id=d["id"], format="metadata").execute()
        except Exception:
            continue
        msg = g.get("message", {})
        h = {x["name"].lower(): x["value"] for x in msg.get("payload", {}).get("headers", [])}
        try:
            when = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000)
        except (TypeError, ValueError):
            continue
        to = parseaddr(h.get("to", ""))[1]
        if not to:
            continue                                    # an empty scratch draft, not a reply
        out.append({"to": to, "subject": (h.get("subject") or "(no subject)")[:90],
                    "when": when.isoformat(timespec="minutes"),
                    "detention": bool(re.search(r"detention", h.get("subject", "") + msg.get("snippet", ""), re.I))})
        time.sleep(0.05)
    out.sort(key=lambda x: x["when"])
    return out


def load(force: bool = False) -> dict:
    """Cached result, refreshed when older than an hour. Never raises: on a
    quota wall or a dead token the old answer is returned with the error noted."""
    old = None
    try:
        old = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if old and not force:
        age = (datetime.now() - datetime.fromisoformat(old["fetched"])).total_seconds() / 60
        if age < MAX_AGE_MIN:
            return old
    try:
        new = fetch()
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:120]}"
        if old:
            return {**old, "error": msg}
        return {"fetched": "", "rows": [], "error": msg}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(new, indent=1), encoding="utf-8")
    return new


if __name__ == "__main__":
    d = load(force="--force" in sys.argv)
    print(d["fetched"], d["error"] or "ok", len(d["rows"]), "threads,",
          sum(r["waiting"] for r in d["rows"]), "waiting on us")
    for r in d["rows"][:12]:
        print(("WAIT " if r["waiting"] else "     ") + r["when"][5:16], r["org"][:22].ljust(22), r["subject"][:50])
