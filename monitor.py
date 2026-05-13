"""Zamalek ticket-drop monitor for tazkarti.com.

Polls the public match listing API. When a match featuring Zamalek
appears for the first time, broadcasts an alert to every Telegram
chat that has subscribed via /start on the bot.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))

TAZKARTI_URL = (
    "https://www.tazkarti.com/booksprt/matches/getMatches"
    "?NewRequest=true&allowPaging=false"
)
SEAT_URL_TEMPLATE = (
    "https://www.tazkarti.com/data/TicketPrice-AvailableSeats-{match_id}.json"
)
# Filter seat categories to only Zamalek's allocation (the opposing team gets
# a different teamId inside the same match response).
ZAMALEK_TEAM_ID = int(os.environ.get("ZAMALEK_TEAM_ID", "79"))
# DATA_DIR lets us point state files at a Railway volume in production.
# Locally, defaults to the project folder.
DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "seen_matches.json"
SUBS_FILE = DATA_DIR / "subscribers.json"
SEAT_STATE_FILE = DATA_DIR / "seat_state.json"
LOG_FILE = DATA_DIR / "monitor.log"

ZAMALEK_KEYWORDS = ("zamalek", "zamlek", "زمالك", "الزمالك")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ar,en;q=0.9",
    "Referer": "https://www.tazkarti.com/",
    "Origin": "https://www.tazkarti.com",
}

TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

WELCOME_TEXT = (
    "✅ تم تفعيل اشتراكك في تنبيهات تذاكر الزمالك!\n\n"
    "هتوصلك رسالة هنا أول ما تذاكر أي ماتش للزمالك تنزل على تذكرتي.\n\n"
    "🛑 لإلغاء الاشتراك في أي وقت ابعت /stop"
)

GOODBYE_TEXT = (
    "تم إلغاء اشتراكك. مش هتوصلك تنبيهات تانية.\n"
    "لو غيّرت رأيك ابعت /start تاني."
)


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------- state ----------

def load_seen() -> set[int]:
    if not STATE_FILE.exists():
        return set()
    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen(seen: set[int]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(seen)), encoding="utf-8")


def load_subs() -> dict:
    if not SUBS_FILE.exists():
        return {"last_update_id": 0, "subscribers": {}}
    try:
        data = json.loads(SUBS_FILE.read_text(encoding="utf-8"))
        data.setdefault("last_update_id", 0)
        data.setdefault("subscribers", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"last_update_id": 0, "subscribers": {}}


def save_subs(data: dict) -> None:
    SUBS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_seat_state() -> dict:
    """Map of {match_id_str: {ticketPriceID_str: {"soldOut": bool, "availableSeats": int}}}."""
    if not SEAT_STATE_FILE.exists():
        return {}
    try:
        return json.loads(SEAT_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_seat_state(state: dict) -> None:
    SEAT_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------- tazkarti ----------

def is_zamalek(match: dict) -> bool:
    fields = (
        match.get("teamName1") or "",
        match.get("teamName2") or "",
        match.get("teamNameAr1") or "",
        match.get("teamNameAr2") or "",
        match.get("teamNameFr1") or "",
        match.get("teamNameFr2") or "",
    )
    haystack = " ".join(fields).lower()
    return any(k.lower() in haystack for k in ZAMALEK_KEYWORDS)


def fetch_matches() -> list[dict]:
    r = requests.get(TAZKARTI_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response shape: {type(data).__name__}")
    return data


def fetch_seat_categories(match_id: int) -> list[dict]:
    url = SEAT_URL_TEMPLATE.format(match_id=match_id)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("isSuccessful", True):
        raise ValueError(f"Seat API not successful for {match_id}: {payload.get('error')}")
    return payload.get("data") or []


def format_alert(match: dict) -> str:
    t1 = match.get("teamNameAr1") or match.get("teamName1") or "?"
    t2 = match.get("teamNameAr2") or match.get("teamName2") or "?"
    stadium = match.get("stadiumNameAr") or match.get("stadiumName") or "?"
    kickoff = match.get("kickOffTime") or match.get("date") or "?"
    tournament = (match.get("tournament") or {}).get("nameAr") or (
        match.get("tournament") or {}
    ).get("nameEn") or "?"
    round_name = match.get("roundNameAr") or match.get("roundName") or ""

    return (
        "🚨 تذاكر الزمالك نزلت على تذكرتي!\n\n"
        f"⚽ {t1}  vs  {t2}\n"
        f"🏟️ {stadium}\n"
        f"🏆 {tournament}\n"
        f"📅 {kickoff}\n"
        f"🎯 {round_name}\n\n"
        "👉 https://www.tazkarti.com/#/matches"
    )


def format_seat_alert(match: dict, category: dict) -> str:
    t1 = match.get("teamNameAr1") or match.get("teamName1") or "?"
    t2 = match.get("teamNameAr2") or match.get("teamName2") or "?"
    cat_name = (
        category.get("categoryNameAr")
        or category.get("categoryName")
        or "?"
    )
    price = category.get("price", "?")
    seats = category.get("availableSeats", "?")
    ticket_id = category.get("ticketPriceID", "?")
    return (
        "🟢 تذكرة بقت متاحة للزمالك!\n\n"
        f"⚽ {t1}  vs  {t2}\n"
        f"🎟️ الفئة: {cat_name}\n"
        f"💰 السعر: {price} جنيه\n"
        f"🪑 المقاعد: {seats}\n"
        f"🆔 ticketPriceID: {ticket_id}\n\n"
        "👉 https://www.tazkarti.com/#/matches"
    )


# ---------- telegram ----------

def tg_send(chat_id: str | int, text: str) -> tuple[bool, int]:
    """Return (ok, http_status). On 403/400 the chat is unreachable."""
    try:
        r = requests.post(
            f"{TG_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
            timeout=15,
        )
        return (r.status_code == 200, r.status_code)
    except requests.RequestException as e:
        log(f"Telegram send to {chat_id} failed: {e}")
        return (False, 0)


def poll_updates(subs_data: dict) -> dict:
    """Read new /start and /stop commands from Telegram, update subscribers."""
    offset = subs_data["last_update_id"] + 1 if subs_data["last_update_id"] else None
    params = {"timeout": 0}
    if offset:
        params["offset"] = offset
    try:
        r = requests.get(f"{TG_API}/getUpdates", params=params, timeout=20)
        if r.status_code != 200:
            log(f"getUpdates HTTP {r.status_code}: {r.text[:200]}")
            return subs_data
        payload = r.json()
    except requests.RequestException as e:
        log(f"getUpdates failed: {e}")
        return subs_data

    if not payload.get("ok"):
        log(f"getUpdates not ok: {payload}")
        return subs_data

    updates = payload.get("result", [])
    if not updates:
        return subs_data

    subs = subs_data["subscribers"]
    for upd in updates:
        subs_data["last_update_id"] = max(
            subs_data["last_update_id"], upd.get("update_id", 0)
        )
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            continue
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        chat_id_s = str(chat_id)
        raw_text = (msg.get("text") or "").strip()
        text = raw_text.lower()

        if text.startswith("/broadcast"):
            if chat_id_s != OWNER_CHAT_ID:
                tg_send(chat_id, "🚫 الأمر ده للأدمن بس.")
                continue
            body = raw_text[len("/broadcast"):].strip()
            if not body:
                tg_send(
                    chat_id,
                    "اكتب الرسالة بعد الأمر:\n/broadcast نص الرسالة هنا",
                )
                continue
            sent = broadcast(subs_data, body)
            tg_send(
                chat_id,
                f"✅ اتبعتت لـ {sent}/{len(subs)} مشترك.",
            )
            log(f"Owner broadcast sent to {sent}/{len(subs)}: {body[:80]!r}")
        elif text.startswith("/start"):
            if chat_id_s not in subs:
                subs[chat_id_s] = {
                    "name": chat.get("first_name") or chat.get("title") or "?",
                    "username": chat.get("username"),
                    "subscribed_at": datetime.now().isoformat(timespec="seconds"),
                }
                log(
                    f"+ Subscribed: {chat_id_s} "
                    f"({subs[chat_id_s]['name']} @{subs[chat_id_s]['username']})"
                )
            tg_send(chat_id, WELCOME_TEXT)
        elif text.startswith("/stop"):
            if chat_id_s in subs:
                removed = subs.pop(chat_id_s)
                log(f"- Unsubscribed: {chat_id_s} ({removed.get('name')})")
            tg_send(chat_id, GOODBYE_TEXT)
        elif text.startswith("/status") or text.startswith("/count"):
            tg_send(
                chat_id,
                f"📊 المشتركين الحاليين: {len(subs)}\n"
                f"البوت شغال وبيفحص تذكرتي كل {POLL_INTERVAL_SECONDS} ثانية.",
            )

    return subs_data


def broadcast(subs_data: dict, text: str) -> int:
    """Send text to every subscriber. Returns number of successful sends.
    Removes chats that block the bot (HTTP 403)."""
    subs = subs_data["subscribers"]
    if not subs:
        log("No subscribers to notify")
        return 0

    sent = 0
    to_drop = []
    for chat_id_s in list(subs.keys()):
        ok, status = tg_send(chat_id_s, text)
        if ok:
            sent += 1
        elif status in (400, 403):
            # 403 = user blocked the bot; 400 = chat not found / deactivated.
            to_drop.append(chat_id_s)
            log(f"Dropping {chat_id_s} (status {status})")
        # Be polite to Telegram — 30 msgs/sec max for broadcasts.
        time.sleep(0.05)

    for cid in to_drop:
        subs.pop(cid, None)

    return sent


# ---------- main loop ----------

def ensure_owner_subscribed(subs_data: dict) -> None:
    if OWNER_CHAT_ID and OWNER_CHAT_ID not in subs_data["subscribers"]:
        subs_data["subscribers"][OWNER_CHAT_ID] = {
            "name": "owner",
            "username": None,
            "subscribed_at": datetime.now().isoformat(timespec="seconds"),
        }
        log(f"Auto-added owner chat {OWNER_CHAT_ID} as subscriber")


def check_seats(match: dict, seat_state: dict, subs_data: dict) -> None:
    """Fetch per-category seat availability and broadcast on sold-out -> available."""
    mid = match.get("matchId")
    if mid is None:
        return
    mid_s = str(mid)
    try:
        categories = fetch_seat_categories(mid)
    except (requests.RequestException, ValueError) as e:
        log(f"Seat fetch failed for match {mid}: {e}")
        return

    categories = [c for c in categories if c.get("teamId") == ZAMALEK_TEAM_ID]
    if not categories:
        log(f"No Zamalek-side categories (teamId={ZAMALEK_TEAM_ID}) for match {mid}")
        return

    prev = seat_state.get(mid_s)
    snapshot = {}
    for c in categories:
        tid = c.get("ticketPriceID")
        if tid is None:
            continue
        snapshot[str(tid)] = {
            "soldOut": bool(c.get("soldOut", True)),
            "availableSeats": int(c.get("availableSeats") or 0),
        }

    if prev is None:
        seat_state[mid_s] = snapshot
        available_now = sum(1 for v in snapshot.values() if not v["soldOut"])
        log(
            f"Init seat state for match {mid} — "
            f"{len(snapshot)} categories, {available_now} available (silent)"
        )
        return

    transitions = []
    for c in categories:
        tid = c.get("ticketPriceID")
        if tid is None:
            continue
        tid_s = str(tid)
        cur_sold = bool(c.get("soldOut", True))
        prev_entry = prev.get(tid_s)
        if prev_entry is None:
            if not cur_sold:
                transitions.append(c)
        else:
            if bool(prev_entry.get("soldOut", True)) and not cur_sold:
                transitions.append(c)

    seat_state[mid_s] = snapshot

    for c in transitions:
        log(
            f"SEAT AVAILABLE for match {mid}: "
            f"{c.get('categoryNameAr')} (ticketPriceID={c.get('ticketPriceID')})"
        )
        sent = broadcast(subs_data, format_seat_alert(match, c))
        log(f"  -> alerted {sent}/{len(subs_data['subscribers'])} subscribers")


def check_once(seen: set[int], subs_data: dict, seat_state: dict) -> set[int]:
    matches = fetch_matches()
    zamalek_matches = [m for m in matches if is_zamalek(m)]
    log(
        f"Fetched {len(matches)} matches — Zamalek: {len(zamalek_matches)} "
        f"— subscribers: {len(subs_data['subscribers'])}"
    )

    new_alerts = []
    for m in zamalek_matches:
        mid = m.get("matchId")
        if mid is None:
            continue
        if mid not in seen:
            new_alerts.append(m)
            seen.add(mid)

    for m in new_alerts:
        log(f"NEW Zamalek match: id={m.get('matchId')} — broadcasting")
        sent = broadcast(subs_data, format_alert(m))
        log(f"  -> alerted {sent}/{len(subs_data['subscribers'])} subscribers")

    for m in zamalek_matches:
        check_seats(m, seat_state, subs_data)

    return seen


def main() -> int:
    if not TELEGRAM_TOKEN:
        print("Missing TELEGRAM_BOT_TOKEN in .env", file=sys.stderr)
        return 1

    log(f"Starting monitor — polling every {POLL_INTERVAL_SECONDS}s")
    seen = load_seen()
    subs_data = load_subs()
    seat_state = load_seat_state()
    ensure_owner_subscribed(subs_data)
    save_subs(subs_data)
    log(
        f"Loaded {len(seen)} seen matchId(s), "
        f"{len(subs_data['subscribers'])} subscriber(s), "
        f"{len(seat_state)} match(es) with seat state"
    )

    while True:
        try:
            subs_data = poll_updates(subs_data)
            save_subs(subs_data)
            seen = check_once(seen, subs_data, seat_state)
            save_seen(seen)
            save_seat_state(seat_state)
            save_subs(subs_data)
        except requests.RequestException as e:
            log(f"Network error: {e}")
        except Exception as e:
            log(f"Unexpected error: {e.__class__.__name__}: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Stopped by user")
        sys.exit(0)
