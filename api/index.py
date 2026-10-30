import os, re, uuid, hashlib, hmac, logging
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from supabase import create_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gmail-checker-business")

app = FastAPI(title="Gmail Checker Business Bot")

TELEGRAM_TOKEN = os.environ["8720270022:AAH3kGgBqCBqvxGn182hZaTW7OccHibEuqM"]
TELEGRAM_SECRET = os.getenv("https://bothdueued.vercel.app", "")
ADMIN_WEBHOOK_SECRET = os.environ["https://t.me/kramtzy1"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
MIDTRANS_SERVER_KEY = os.environ["MIDTRANS_SERVER_KEY"]
MIDTRANS_IS_PRODUCTION = os.getenv("MIDTRANS_IS_PRODUCTION", "false").lower() == "true"
OWNER_IDS = {x.strip() for x in os.getenv("BOT_OWNER_IDS", "").split(",") if x.strip()}
SUPPORT_USERNAME = os.getenv("BOT_SUPPORT_USERNAME", "").lstrip("@")
PRICE_PER_EMAIL = int(os.getenv("PRICE_PER_EMAIL", "100"))
VIP_30_PRICE = int(os.getenv("VIP_30_PRICE", "25000"))
VIP_30_DAILY_LIMIT = int(os.getenv("VIP_30_DAILY_LIMIT", "500"))
MAX_EMAILS = 50

API_KEYS = [os.getenv(f"EV_API_KEY_{i}", "").strip() for i in range(1, 6)]
ACTIVE_KEY_SLOT = max(1, min(5, int(os.getenv("EV_ACTIVE_KEY", "1"))))
EV_BASE = "https://api.emailvalidation.io/v1"
MIDTRANS_BASE = "https://app.midtrans.com" if MIDTRANS_IS_PRODUCTION else "https://app.sandbox.midtrans.com"

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def tg(method: str, payload: dict):
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}", json=payload, timeout=25)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data.get("result")


def send(chat_id: int, text: str, **kwargs):
    return tg("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True, **kwargs})


def upsert_user(user: dict):
    uid = str(user["id"])
    supabase.table("users").upsert({
        "telegram_id": uid,
        "username": user.get("username"),
        "full_name": " ".join(filter(None, [user.get("first_name"), user.get("last_name")])),
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="telegram_id").execute()
    supabase.rpc("ensure_wallet", {"p_user_id": uid}).execute()


def get_wallet(uid: str) -> int:
    row = supabase.table("wallets").select("balance").eq("telegram_id", uid).maybe_single().execute().data
    return int(row["balance"]) if row else 0


def is_owner(uid: str) -> bool:
    return uid in OWNER_IDS


def active_key() -> str:
    return API_KEYS[ACTIVE_KEY_SLOT - 1]


def check_email(email: str) -> tuple[str, str]:
    key = active_key()
    if not key:
        return "api_error", "API key belum dikonfigurasi"
    try:
        r = requests.get(EV_BASE, params={"email": email, "apikey": key}, timeout=25)
        if r.status_code != 200:
            return "api_error", f"Provider HTTP {r.status_code}"
        data = r.json()
        # Deliverability classification only; not account-existence verification.
        state = str(data.get("state", "")).lower()
        if state in ("deliverable", "undeliverable", "risky"):
            return state, str(data.get("reason") or data.get("state") or "")
        if data.get("smtp_check") is True and data.get("format_valid") is True:
            return "deliverable", "Provider reports deliverable"
        if data.get("smtp_check") is False:
            return "undeliverable", "Provider reports not deliverable"
        return "risky", "Provider result is inconclusive"
    except Exception as exc:
        log.warning("Email provider error: %s", exc)
        return "api_error", "Koneksi/provider error"


EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@gmail\.com(?![\w.-])", re.I)


def extract_emails(text: str) -> list[str]:
    return list(dict.fromkeys(x.lower() for x in EMAIL_RE.findall(text)))[:MAX_EMAILS]


def charge(uid: str, amount: int, reason: str) -> bool:
    result = supabase.rpc("debit_wallet", {
        "p_user_id": uid, "p_amount": amount, "p_description": reason
    }).execute()
    return bool(result.data)


def handle_start(chat_id: int, user: dict):
    uid = str(user["id"])
    send(chat_id,
        "👋 Selamat datang di Gmail Checker Business.\n\n"
        "Perintah:\n"
        "/saldo — cek saldo\n"
        "/topup 10000 — buat pembayaran top-up\n"
        "/vip — beli VIP 30 hari\n"
        "/cek — petunjuk pengecekan\n"
        "/history — riwayat pengecekan\n"
        "/id — Telegram ID\n\n"
        "Catatan: hasil provider adalah sinyal deliverability, bukan bukti akun Gmail terdaftar, aktif, atau pernah login."
    )


def create_midtrans_payment(uid: str, amount: int):
    order_id = "GCB-" + uuid.uuid4().hex[:20].upper()
    payload = {
        "transaction_details": {"order_id": order_id, "gross_amount": amount},
        "customer_details": {"first_name": f"Telegram {uid}"},
        "item_details": [{"id": "wallet-topup", "price": amount, "quantity": 1, "name": "Top up saldo bot"}],
        "custom_field1": uid,
    }
    import base64
    auth = base64.b64encode((MIDTRANS_SERVER_KEY + ":").encode()).decode()
    r = requests.post(
        f"{MIDTRANS_BASE}/snap/v1/transactions",
        json=payload,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    supabase.table("transactions").insert({
        "telegram_id": uid, "order_id": order_id, "amount": amount,
        "status": "pending", "transaction_type": "topup",
    }).execute()
    return data["redirect_url"], order_id


def process_message(message: dict):
    chat = message.get("chat", {})
    user = message.get("from", {})
    if not chat or not user:
        return
    chat_id = chat["id"]
    uid = str(user["id"])
    upsert_user(user)

    banned = supabase.table("banned_users").select("telegram_id").eq("telegram_id", uid).maybe_single().execute().data
    if banned and not is_owner(uid):
        send(chat_id, "Akses bot Anda sedang dibatasi.")
        return

    text = (message.get("text") or "").strip()
    if text.startswith("/"):
        command, *parts = text.split(maxsplit=1)
        command = command.split("@")[0].lower()
        arg = parts[0] if parts else ""

        if command == "/start":
            handle_start(chat_id, user)
        elif command == "/id":
            send(chat_id, f"Telegram ID: `{uid}`", parse_mode="Markdown")
        elif command == "/saldo":
            send(chat_id, f"💰 Saldo Anda: Rp{get_wallet(uid):,}".replace(",", "."))
        elif command == "/topup":
            try:
                amount = int(arg)
                if amount < 10000 or amount > 10000000:
                    raise ValueError()
                url, order_id = create_midtrans_payment(uid, amount)
                send(chat_id, f"💳 Tagihan top-up Rp{amount:,}\nOrder: {order_id}\n\nSelesaikan pembayaran:\n{url}\n\nSaldo masuk setelah pembayaran terverifikasi.".replace(",", "."))
            except ValueError:
                send(chat_id, "Format: /topup 10000\nMinimal Rp10.000, maksimal Rp10.000.000.")
            except Exception:
                log.exception("Failed to create Midtrans invoice")
                send(chat_id, "Gagal membuat tagihan. Coba beberapa saat lagi.")
        elif command == "/vip":
            if not charge(uid, VIP_30_PRICE, "Pembelian VIP 30 hari"):
                send(chat_id, f"Saldo tidak cukup. Harga VIP 30 hari Rp{VIP_30_PRICE:,}. Gunakan /topup.".replace(",", "."))
            else:
                supabase.rpc("activate_vip", {"p_user_id": uid, "p_days": 30, "p_daily_limit": VIP_30_DAILY_LIMIT}).execute()
                send(chat_id, "⭐ VIP aktif selama 30 hari. Terima kasih!")
        elif command == "/cek":
            send(chat_id, f"Kirim daftar alamat Gmail dalam satu pesan (maksimal {MAX_EMAILS}). Biaya Rp{PRICE_PER_EMAIL:,} per alamat. Hasil hanya menunjukkan sinyal deliverability provider, bukan status akun.".replace(",", "."))
        elif command == "/history":
            rows = supabase.table("checks").select("created_at,total,deliverable,undeliverable,risky,api_error").eq("telegram_id", uid).order("created_at", desc=True).limit(5).execute().data or []
            if not rows:
                send(chat_id, "Belum ada riwayat.")
            else:
                lines = ["📚 Riwayat terakhir:"]
                for row in rows:
                    lines.append(f"{row['created_at'][:16]} | total {row['total']} | deliverable {row['deliverable']} | undeliverable {row['undeliverable']} | risky {row['risky']} | error {row['api_error']}")
                send(chat_id, "\n".join(lines))
        elif command == "/vipinfo":
            row = supabase.table("vip_subscriptions").select("*").eq("telegram_id", uid).maybe_single().execute().data
            send(chat_id, f"VIP: {row['expires_at'] if row else 'Tidak aktif'}")
        elif command == "/help":
            send(chat_id, "/saldo\n/topup 10000\n/vip\n/vipinfo\n/cek\n/history\n/id")
        elif command == "/users" and is_owner(uid):
            count = len(supabase.table("users").select("telegram_id", count="exact").execute().data or [])
            send(chat_id, f"Pengguna terdaftar: {count}")
        else:
            send(chat_id, "Perintah tidak dikenal. Ketik /help.")
        return

    emails = extract_emails(text)
    if not emails:
        send(chat_id, "Kirim alamat @gmail.com atau ketik /help.")
        return

    vip = supabase.table("vip_subscriptions").select("expires_at,daily_limit").eq("telegram_id", uid).maybe_single().execute().data
    vip_active = bool(vip and vip.get("expires_at") and datetime.fromisoformat(vip["expires_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc))
    price = 0 if vip_active else PRICE_PER_EMAIL * len(emails)
    if price and not charge(uid, price, f"Pengecekan {len(emails)} email"):
        send(chat_id, f"Saldo tidak cukup. Biaya Rp{price:,}. Saldo Anda Rp{get_wallet(uid):,}. Gunakan /topup.".replace(",", "."))
        return

    send(chat_id, f"⏳ Memproses {len(emails)} alamat...")
    counts = {"deliverable": 0, "undeliverable": 0, "risky": 0, "api_error": 0}
    result_lines = []
    for email in emails:
        status, detail = check_email(email)
        counts[status] = counts.get(status, 0) + 1
        result_lines.append(f"{status.upper()} | {email} | {detail}")
    supabase.table("checks").insert({
        "telegram_id": uid, "total": len(emails),
        "deliverable": counts["deliverable"], "undeliverable": counts["undeliverable"],
        "risky": counts["risky"], "api_error": counts["api_error"],
        "results": result_lines,
    }).execute()
    summary = "\n".join(result_lines)
    send(chat_id, f"📊 Hasil {len(emails)} alamat\n\n{summary}\n\nCatatan: hasil bukan verifikasi keberadaan atau aktivitas akun Gmail."[:3900])


@app.get("/")
def health():
    return {"ok": True, "service": "gmail-checker-business", "mode": "webhook"}


@app.post("/api/telegram")
async def telegram_webhook(request: Request):
    if TELEGRAM_SECRET:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(secret, TELEGRAM_SECRET):
            raise HTTPException(status_code=403, detail="Invalid webhook secret")
    update = await request.json()
    message = update.get("message") or update.get("edited_message")
    if message:
        try:
            process_message(message)
        except Exception:
            log.exception("Telegram update processing failed")
    return {"ok": True}


@app.post("/api/midtrans")
async def midtrans_webhook(request: Request):
    data = await request.json()
    required = ["order_id", "status_code", "gross_amount", "signature_key"]
    if any(k not in data for k in required):
        raise HTTPException(status_code=400, detail="Missing fields")
    raw = data["order_id"] + data["status_code"] + data["gross_amount"] + MIDTRANS_SERVER_KEY
    expected = hashlib.sha512(raw.encode()).hexdigest()
    if not hmac.compare_digest(expected, str(data["signature_key"])):
        raise HTTPException(status_code=403, detail="Invalid signature")

    status = data.get("transaction_status")
    fraud = data.get("fraud_status")
    if status in ("settlement", "capture") and (status != "capture" or fraud in (None, "accept")):
        # SQL RPC enforces idempotency and credits only the matching pending order.
        supabase.rpc("complete_topup", {
            "p_order_id": data["order_id"],
            "p_amount": int(float(data["gross_amount"])),
            "p_provider_status": status,
        }).execute()
    elif status in ("expire", "cancel", "deny"):
        supabase.table("transactions").update({"status": status}).eq("order_id", data["order_id"]).eq("status", "pending").execute()
    return {"ok": True}


@app.post("/api/admin/telegram-webhook")
async def set_telegram_webhook(request: Request):
    supplied = request.headers.get("X-Admin-Secret", "")
    if not hmac.compare_digest(supplied, ADMIN_WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")
    body = await request.json()
    url = body.get("url", "").rstrip("/")
    if not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="HTTPS URL required")
    payload = {"url": url + "/api/telegram", "allowed_updates": ["message"]}
    if TELEGRAM_SECRET:
        payload["secret_token"] = TELEGRAM_SECRET
    result = tg("setWebhook", payload)
    return {"ok": True, "result": result}
