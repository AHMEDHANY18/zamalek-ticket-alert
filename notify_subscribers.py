"""One-off announcement to every Telegram subscriber.

Run this BEFORE turning on the per-minute status broadcasts in monitor.py
so subscribers know what to expect and can mute the chat if they want.

    python notify_subscribers.py
"""

import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent)))
SUBS_FILE = DATA_DIR / "subscribers.json"

ANNOUNCEMENT = (
    "📢 تنبيه من الأدمن\n\n"
    "البوت هيشتغل دلوقتي في وضع جديد:\n"
    "كل دقيقة هيوصلك رسالة بحالة تذاكر الزمالك (المتاح والـ sold out).\n\n"
    "⚠️ ده معناه رسائل كتيرة جدًا في اليوم. لو الموضوع بيزعجك:\n"
    "🔕 اعمل Mute للبوت من إعدادات الشات\n"
    "🛑 أو ابعت /stop عشان تلغي الاشتراك خالص\n\n"
    "أول ما تذكرة تنزل متاحة، هيتبعتلك تنبيه واضح حتى لو معمول mute "
    "(لو الإشعارات مفتوحة).\n\n"
    "شكراً على صبركم 🙏"
)


def main() -> int:
    if not TELEGRAM_TOKEN:
        print("Missing TELEGRAM_BOT_TOKEN in .env", file=sys.stderr)
        return 1
    if not SUBS_FILE.exists():
        print(f"No subscribers file at {SUBS_FILE}", file=sys.stderr)
        return 1

    subs_data = json.loads(SUBS_FILE.read_text(encoding="utf-8"))
    subs = subs_data.get("subscribers", {})
    if not subs:
        print("No subscribers to notify.")
        return 0

    print(f"Sending announcement to {len(subs)} subscriber(s)...")
    api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    sent = 0
    failed = []
    for chat_id, info in subs.items():
        try:
            r = requests.post(
                api,
                json={"chat_id": chat_id, "text": ANNOUNCEMENT},
                timeout=15,
            )
            if r.status_code == 200:
                sent += 1
                print(f"  ✓ {chat_id} ({info.get('name', '?')})")
            else:
                failed.append((chat_id, r.status_code, r.text[:120]))
                print(f"  ✗ {chat_id} — HTTP {r.status_code}")
        except requests.RequestException as e:
            failed.append((chat_id, 0, str(e)))
            print(f"  ✗ {chat_id} — {e}")
        time.sleep(0.05)

    print(f"\nDone. Sent {sent}/{len(subs)}.")
    if failed:
        print("Failures:")
        for cid, status, msg in failed:
            print(f"  {cid}: status={status} body={msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
