import hashlib
import hmac
import json
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from collections import defaultdict

CONFIG_PATH = "business_config.json"
HTML_PATH = "index.html"
APPOINTMENTS_PATH = "appointments.json"
MESSAGES_PATH = "messages.json"

try:
    import database
except ImportError:
    database = None

DEFAULT_CONFIG = {
    "business_name": "Demo İşletme",
    "sector": "Berber",
    "services": [
        {"name": "Saç Kesimi", "price": "500 TL"},
        {"name": "Saç + Sakal", "price": "750 TL"}
    ],
    "working_hours": "09:00-18:00",
    "closed_days": ["Pazar"],
    "address": "İşletme adresini config dosyasına yazın",
    "tone": "samimi, kısa ve profesyonel"
}

RATE_LIMITS = defaultdict(list)
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 20

SYSTEM_PROMPT = """Sen bir küçük işletme müşteri iletişim asistanısın.
İşletmenin verdiği bilgilere sadık kal. Bilgi yoksa uydurma.
Fiyat, çalışma saati veya randevu uygunluğu kesin değilse kesinmiş gibi söyleme.
Yanıtları Türkçe, kısa, sıcak ve profesyonel yaz.
Müşteri randevu istiyorsa tarih ve saat bilgisini netleştirmeye çalış.
Ödeme, sağlık, hukuki veya güvenlik konusunda işletmenin bilmediği bilgileri uydurma.
"""

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_config():
    return load_json(CONFIG_PATH, DEFAULT_CONFIG)

def load_config_for_user(user):
    if user and database_enabled():
        value = user[5]
        return json.loads(value) if isinstance(value, str) else value
    return load_config()

def database_enabled():
    return bool(database and database.enabled())

def hash_password(password):
    salt = os.urandom(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
    return "scrypt$16384$8$1$" + salt.hex() + "$" + derived.hex()

def verify_password(password, stored):
    try:
        _, n, r, pp, salt_hex, digest_hex = stored.split("$")
        derived = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(pp))
        return hmac.compare_digest(derived.hex(), digest_hex)
    except Exception:
        return False

def cookie_token(handler):
    for part in handler.headers.get("Cookie", "").split(";"):
        if part.strip().startswith("session="): return part.strip().split("=", 1)[1]
    return ""

def current_user(handler):
    if not database_enabled(): return None
    token = cookie_token(handler)
    return database.get_session_user(hashlib.sha256(token.encode("utf-8")).hexdigest()) if token else None

def rate_limited(key, limit=RATE_LIMIT_MAX, window=RATE_LIMIT_WINDOW):
    now = datetime.now(timezone.utc).timestamp()
    values = [x for x in RATE_LIMITS[key] if now - x < window]
    if len(values) >= limit:
        RATE_LIMITS[key] = values
        return True
    values.append(now)
    RATE_LIMITS[key] = values
    return False

def auth_required(handler):
    user = current_user(handler)
    if not user: handler.send_json(401, {"error":"Giriş yapmanız gerekiyor."}); return None
    return user

def user_can(user, module):
    if not user or not database_enabled(): return False
    if user[3] == 'owner': return True
    permissions = database.get_user_permissions(user[0]) or {}
    return bool(permissions.get(module, False))


def business_config_for_user(user):
    value = user[5]
    return json.loads(value) if isinstance(value, str) else value


def initialize_database():
    if database_enabled():
        database.ensure_schema()
        config = load_config()
        business_id = database.get_business_id(config)
        legacy_appointments = load_json(APPOINTMENTS_PATH, [])
        legacy_messages = load_json(MESSAGES_PATH, [])
        current_appointments = database.load_appointments(config)
        current_messages = database.load_messages(config)
        if not current_appointments and legacy_appointments:
            database.save_appointments(config, legacy_appointments)
        if not current_messages and legacy_messages:
            database.save_messages(config, legacy_messages)

def require_permission(handler, module):
    user = auth_required(handler)
    if not user:
        return None
    if not user_can(user, module):
        handler.send_json(403, {"error": "Bu işlem için yetkiniz yok."})
        return None
    return user

def load_appointments(user=None):
    if database_enabled():
        return database.load_appointments_by_business(user[1]) if user else database.load_appointments(load_config())
    return load_json(APPOINTMENTS_PATH, [])

def save_appointments(items, user=None):
    if database_enabled():
        database.save_appointments_by_business(user[1], items) if user else database.save_appointments(load_config(), items)
    else:
        save_json(APPOINTMENTS_PATH, items)

def load_messages(user=None):
    if database_enabled():
        return database.load_messages_by_business(user[1]) if user else database.load_messages(load_config())
    return load_json(MESSAGES_PATH, [])

def save_messages(items, user=None):
    if database_enabled():
        database.save_messages_by_business(user[1], items) if user else database.save_messages(load_config(), items)
    else:
        save_json(MESSAGES_PATH, items)

def gemini_request(prompt, max_tokens=1000):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY tanımlı değil.")
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": max_tokens}}
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        data = json.load(response)
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

def ask_gemini(message, config):
    prompt = f"""{SYSTEM_PROMPT}

İŞLETME BİLGİLERİ:
{json.dumps(config, ensure_ascii=False, indent=2)}

MÜŞTERİ MESAJI:
{message}

Sadece müşteriye gönderilebilecek cevabı üret."""
    return gemini_request(prompt, 1200)

def detect_appointment_intent(message, config):
    prompt = f"""Bir işletme mesajını randevu açısından sınıflandır.
Sadece geçerli JSON döndür, Markdown kullanma.
Alanlar:
intent: "appointment" veya "other"
customer_name: mesajda varsa isim, yoksa ""
phone: mesajda varsa telefon, yoksa ""
date: açıkça belirtilen tarih varsa YYYY-MM-DD, yoksa ""
time: açıkça belirtilen saat varsa HH:MM, yoksa ""
service: işletmenin hizmetlerinden biri veya mesajdaki hizmet, yoksa ""
note: kısa not

İŞLETME:
{json.dumps(config, ensure_ascii=False)}

MESAJ:
{message}
"""
    raw = gemini_request(prompt, 600)
    try:
        start, end = raw.find("{"), raw.rfind("}")
        return json.loads(raw[start:end + 1])
    except Exception:
        return {"intent": "other", "customer_name": "", "phone": "", "date": "", "time": "", "service": "", "note": ""}

def create_appointment(data, user=None):
    appointment = {
        "id": datetime.utcnow().strftime("%Y%m%d%H%M%S%f"),
        "customer_name": str(data.get("customer_name", "")).strip(),
        "phone": str(data.get("phone", "")).strip(),
        "date": str(data.get("date", "")).strip(),
        "time": str(data.get("time", "")).strip(),
        "service": str(data.get("service", "")).strip(),
        "note": str(data.get("note", "")).strip(),
        "status": "pending",
        "created_at": datetime.utcnow().isoformat() + "Z"
    }
    required = ["customer_name", "phone", "date", "time", "service"]
    if any(not appointment[x] for x in required):
        return None, "customer_name, phone, date, time ve service zorunlu"

    items = load_appointments(user)
    active = {"pending", "confirmed"}
    conflict = next((item for item in items if item.get("date") == appointment["date"] and item.get("time") == appointment["time"] and item.get("status") in active), None)
    if conflict:
        return None, "Bu tarih ve saatte başka bir randevu bulunuyor."

    items.append(appointment)
    save_appointments(items, user)
    return appointment, None

def appointment_time_from_text(text):
    match = re.search(r"\b([01]?\d|2[0-3])(?:[:.]([0-5]\d))?\b", text)
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2) or "00")
    return f"{hour:02d}:{minute:02d}"

def local_intent_hint(message, config):
    text = message.lower()
    time_text = appointment_time_from_text(text)
    appointment_words = ("randevu", "rezervasyon", "uygun saat", "saat", "boş mu", "boş", "müsait", "uygun")
    if any(word in text for word in appointment_words):
        service = ""
        for item in config.get("services", []):
            name = str(item.get("name", "")).strip()
            if name and name.lower() in text:
                service = name
                break

        today = datetime.now().strftime("%Y-%m-%d") if time_text and any(word in text for word in ("boş", "müsait", "uygun")) else ""

        return {
            "intent": "availability" if any(word in text for word in ("boş", "müsait", "uygun")) else "appointment",            "customer_name": "",
            "phone": "",
            "date": today,
            "time": time_text,
            "service": service,
            "note": ""
        }

    for item in config.get("services", []):
        name = str(item.get("name", "")).strip()
        price = str(item.get("price", "")).strip()
        normalized = name.lower().replace("+", " ")
        if name and price and (
            name.lower() in text or (normalized.replace(" ", "") == "saçsakal" and "saç sakal" in text)
        ) and any(word in text for word in ("fiyat", "ne kadar", "ücret", "kaç tl")):
            return {"intent": "price", "service": name, "price": price}
    return None

def is_closed_day(date, config):
    try:
        day_name = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"][datetime.strptime(date, "%Y-%m-%d").weekday()]
    except ValueError:
        return False
    return day_name in {str(day).strip() for day in config.get("closed_days", [])}

def is_time_available(date, time, config=None, user=None):
    if not date or not time:
        return None
    config = config or load_config()
    if is_closed_day(date, config):
        return False
    hours = str(config.get("working_hours", "09:00-18:00"))
    match = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", hours)
    if not match:
        return False
    start = int(match.group(1)) * 60 + int(match.group(2))
    end = int(match.group(3)) * 60 + int(match.group(4))
    try:
        requested = int(time[:2]) * 60 + int(time[3:5])
    except (ValueError, TypeError):
        return False
    if requested < start or requested >= end:
        return False
    items = load_appointments(user)
    active = {"pending", "confirmed"}
    return not any(item.get("date") == date and item.get("time") == time and item.get("status") in active for item in items)

def get_free_slots(date, config, user=None):
    if is_closed_day(date, config):
        return []
    hours = str(config.get("working_hours", "09:00-18:00"))
    match = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", hours)
    if not match:
        return []
    start = int(match.group(1))
    end = int(match.group(3))
    first_hour = start
    if date == datetime.now().strftime("%Y-%m-%d"):
        first_hour = max(start, datetime.now().hour + (1 if datetime.now().minute else 0))
    return [f"{hour:02d}:00" for hour in range(first_hour, end) if is_time_available(date, f"{hour:02d}:00", config, user)]

def handle_customer_message(message, channel="web", customer_name="", phone="", user=None):
    config = business_config_for_user(user) if user else load_config()
    hint = local_intent_hint(message, config)
    intent = hint if hint else detect_appointment_intent(message, config)
    record = {
        "id": datetime.utcnow().strftime("%Y%m%d%H%M%S%f"),
        "channel": channel,
        "direction": "inbound",
        "customer_name": customer_name or intent.get("customer_name", ""),
        "phone": phone or intent.get("phone", ""),
        "message": message,
        "intent": intent.get("intent", "other"),
        "created_at": datetime.utcnow().isoformat() + "Z"
    }
    messages = load_messages(user)
    messages.append(record)
    save_messages(messages, user)

    if intent.get("intent") == "price":
        return {"reply": f"{intent.get('service')} fiyatı {intent.get('price')}."}

    if intent.get("intent") == "availability":
        date = intent.get("date", "")
        time = intent.get("time", "")
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        if is_closed_day(date, config):
            return {"reply": f"{date} günü işletme kapalıdır. Pazar günleri tatildir."}
        if not time:
            slots = get_free_slots(date, config, user)
            if slots:
                return {"reply": f"Bugün ({date}) boş saatler: " + ", ".join(slots) + "."}
            return {"reply": f"Bugün ({date}) için uygun boş saat görünmüyor."}
        available = is_time_available(date, time, config, user)
        if available:
            return {"reply": f"Evet, {date} günü saat {time} şu an boş görünüyor."}
        return {"reply": f"Maalesef {date} günü saat {time} uygun değil. Çalışma saatleri 09:00-18:00, Pazar günleri tatil."}

    if intent.get("intent") == "appointment":
        intent["customer_name"] = record["customer_name"]
        intent["phone"] = record["phone"]
        required = ["customer_name", "phone", "date", "time", "service"]
        missing = [x for x in required if not str(intent.get(x, "")).strip()]
        if not missing:
            if is_closed_day(intent["date"], config):
                return {"reply": "Pazar günü işletme kapalı. Lütfen başka bir gün seçer misiniz?"}
            if not is_time_available(intent["date"], intent["time"], config, user):
                return {"reply": "Seçtiğiniz tarih veya saat uygun değil. Çalışma saatleri 09:00-18:00, Pazar günleri tatil."}
            appointment, error = create_appointment(intent, user)
            if appointment:
                return {"reply": f"Randevunuzu aldım. {appointment['date']} {appointment['time']} için {appointment['service']} kaydınız oluşturuldu.", "appointment": appointment}
            if error == "Bu tarih ve saatte başka bir randevu bulunuyor.":
                return {"reply": "Bu tarih ve saatte başka bir randevu bulunuyor. Lütfen farklı bir saat seçer misiniz?"}
        labels = {"customer_name":"adınızı","phone":"telefon numaranızı","date":"tarihi","time":"saati","service":"hizmeti"}
        return {"reply": "Randevu oluşturabilmem için lütfen " + ", ".join(labels[x] for x in missing) + " bilgisini de paylaşır mısınız?"}

    return {"reply": ask_gemini(message, config)}

def send_whatsapp_text(to, message):
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    if not token or not phone_number_id:
        return False, "WhatsApp erişim bilgileri tanımlı değil."

    url = f"https://graph.facebook.com/v23.0/{phone_number_id}/messages"
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"preview_url": False, "body": message}}
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return True, json.load(response)
    except Exception as exc:
        return False, str(exc)

def whatsapp_verify(query):
    params = parse_qs(query)
    mode = params.get("hub.mode", [""])[0]
    token = params.get("hub.verify_token", [""])[0]
    challenge = params.get("hub.challenge", [""])[0]
    expected = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    if mode == "subscribe" and expected and token == expected:
        return 200, challenge
    return 403, "Webhook doğrulaması başarısız"

def whatsapp_business_id(data):
    try:
        phone_number_id = str(data["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"]).strip()
    except (KeyError, IndexError, TypeError):
        return None
    mapping = os.environ.get("WHATSAPP_BUSINESS_MAP", "").strip()
    if not mapping or not database_enabled():
        return None
    try:
        parsed = json.loads(mapping)
        value = parsed.get(phone_number_id)
        return int(value) if value is not None else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None

def verify_whatsapp_signature(raw_body, signature):
    secret = os.environ.get("META_APP_SECRET", "").strip()
    if not secret:
        return False
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

def whatsapp_message_already_processed(message_id, business_id=None):
    if not message_id or business_id is None:
        return False
    return any(item.get("message_id") == message_id for item in database.load_messages_by_business(business_id))

def parse_whatsapp_message(data):
    try:
        value = data["entry"][0]["changes"][0]["value"]
        messages = value.get("messages", [])
        if not messages:
            return None
        msg = messages[0]
        if msg.get("type") != "text":
            return None
        return {"message": msg["text"]["body"], "phone": msg.get("from", ""), "customer_name": value.get("contacts", [{}])[0].get("profile", {}).get("name", ""), "message_id": msg.get("id", "")}
    except (KeyError, IndexError, TypeError):
        return None

class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            try:
                size = os.path.getsize(HTML_PATH)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(size))
                self.end_headers()
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
            return
        if parsed.path == "/health":
            body = b'{"status":"ok","service":"AI \\u0130\\u015fletme Asistan\\u0131"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()


    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/webhook/whatsapp":
            status, body = whatsapp_verify(parsed.query)
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            return
        if parsed.path in ("/", "/index.html"):
            try:
                with open(HTML_PATH, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self.send_json(404, {"error": "index.html bulunamadı"})
            return
        if parsed.path == "/api/auth/status":
            user=current_user(self); self.send_json(200,{"authenticated":bool(user),"email":user[2] if user else ""}); return
        if parsed.path == "/api/auth/setup-available":
            self.send_json(200,{"available":database_enabled() and database.count_users()==0}); return
        if parsed.path == "/api/business":
            user=auth_required(self)
            if not user: return
            if not user_can(user,"business_settings"):
                self.send_json(403,{"error":"İşletme ayarlarına erişim yetkiniz yok."}); return
            self.send_json(200, business_config_for_user(user)); return
        if parsed.path == "/api/appointments":
            user=auth_required(self)
            if not user: return
            if not user_can(user,"appointments"):
                self.send_json(403,{"error":"Randevulara erişim yetkiniz yok."}); return
            self.send_json(200, load_appointments(user)); return
        if parsed.path == "/api/messages":            user=auth_required(self)
            if not user: return
            if not user_can(user,"messages"):
                self.send_json(403,{"error":"Mesajlara erişim yetkiniz yok."}); return
            self.send_json(200, load_messages(user)); return
        if parsed.path == "/api/customers":
            user=auth_required(self)
            if not user: return
            if not user_can(user,"customers"):
                self.send_json(403,{"error":"Müşterilere erişim yetkiniz yok."}); return
            rows=database.list_customers_by_business(user[1])
            self.send_json(200,[{"id":r[0],"name":r[1],"phone":r[2],"created_at":r[3].isoformat() if hasattr(r[3],"isoformat") else str(r[3]),"updated_at":r[4].isoformat() if hasattr(r[4],"isoformat") else str(r[4])} for r in rows]); return
        if parsed.path == "/api/reports":
            user=auth_required(self)
            if not user: return
            if not user_can(user,"reports"):
                self.send_json(403,{"error":"Raporlara erişim yetkiniz yok."}); return
            self.send_json(200,database.get_business_report(user[1])); return
        if parsed.path == "/api/users":
            user=auth_required(self)
            if not user: return
            if not user_can(user,"team"):
                self.send_json(403,{"error":"Ekip bilgilerine erişim yetkiniz yok."}); return
            rows=database.list_users(user[1])
            self.send_json(200,[{"id":r[0],"email":r[1],"role":r[2],"created_at":r[3].isoformat() if hasattr(r[3],"isoformat") else str(r[3])} for r in rows]); return
        if parsed.path == "/health":
            self.send_json(200, {"status": "ok", "service": "AI İşletme Asistanı"}); return
        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) or b"{}"

            if self.path == "/webhook/whatsapp" and not verify_whatsapp_signature(raw_body, self.headers.get("X-Hub-Signature-256", "")):
                self.send_json(403, {"error": "WhatsApp webhook imza doğrulaması başarısız"})
                return

            data = json.loads(raw_body)

            if self.path == "/api/auth/register":
                email=str(data.get("email","")).strip().lower()
                password=str(data.get("password",""))
                business_name=str(data.get("business_name","")).strip()
                if not email or "@" not in email or len(password) < 8 or not business_name:
                    self.send_json(400,{"error":"İşletme adı, geçerli e-posta ve en az 8 karakterli şifre gerekli."}); return
                if not database_enabled():
                    self.send_json(503,{"error":"Veritabanı hazır değil."}); return
                if database.get_user_by_email(email):
                    self.send_json(409,{"error":"Bu e-posta zaten kayıtlı. Giriş yapmayı deneyin."}); return
                config=dict(DEFAULT_CONFIG)
                config["business_name"]=business_name
                business_id=database.create_business(business_name,config)
                user_id=database.create_user(email,hash_password(password),business_id,"owner")
                token=os.urandom(32).hex()
                expires=None
                database.create_session(hashlib.sha256(token.encode("utf-8")).hexdigest(),user_id,expires)
                self.send_response(201)
                self.send_header("Set-Cookie","session="+token+"; HttpOnly; SameSite=Lax; Path=/; Secure; Expires=Fri, 31 Dec 2099 23:59:59 GMT")
                self.send_header("Content-Type","application/json; charset=utf-8")
                body=json.dumps({"success":True,"email":email},ensure_ascii=False).encode("utf-8")
                self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body); return

            if self.path == "/api/auth/login":
                email=str(data.get("email","")).strip().lower()
                password=str(data.get("password",""))
                if not email or not password:
                    self.send_json(400,{"error":"E-posta ve şifre gerekli."}); return
                if not database_enabled():
                    self.send_json(503,{"error":"Veritabanı hazır değil."}); return
                user=database.get_user_by_email(email)
                if not user or not verify_password(password,user[3]):
                    self.send_json(401,{"error":"E-posta veya şifre hatalı."}); return
                token=os.urandom(32).hex()
                expires=None
                database.create_session(hashlib.sha256(token.encode("utf-8")).hexdigest(),user[0],expires)
                self.send_response(200)
                self.send_header("Set-Cookie","session="+token+"; HttpOnly; SameSite=Lax; Path=/; Secure; Expires=Fri, 31 Dec 2099 23:59:59 GMT")
                self.send_header("Content-Type","application/json; charset=utf-8")
                body=json.dumps({"success":True,"email":user[2]},ensure_ascii=False).encode("utf-8")
                self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body); return

            if self.path == "/api/auth/logout":
                token=cookie_token(self)
                if token and database_enabled():
                    database.revoke_session(hashlib.sha256(token.encode("utf-8")).hexdigest())
                self.send_response(200)
                self.send_header("Set-Cookie","session=; HttpOnly; SameSite=Lax; Path=/; Secure; Max-Age=0")
                self.send_header("Content-Type","application/json; charset=utf-8")
                body=b'{"success":true}'
                self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body); return

            if self.path == "/webhook/whatsapp":
                incoming = parse_whatsapp_message(data)
                if not incoming:
                    self.send_json(200, {"received": True, "processed": False}); return
                if whatsapp_message_already_processed(incoming.get("message_id", "")):
                    self.send_json(200, {"received": True, "processed": False, "duplicate": True}); return
                result = handle_customer_message(incoming["message"], "whatsapp", incoming["customer_name"], incoming["phone"])
                ok, send_result = send_whatsapp_text(incoming["phone"], result["reply"])
                out = {"received": True, "processed": True, "result": result, "reply_sent": ok}
                if not ok:
                    out["reply_error"] = send_result
                else:
                    messages = load_messages()
                    messages.append({"id": datetime.utcnow().strftime("%Y%m%d%H%M%S%f"), "message_id": incoming.get("message_id", ""), "channel": "whatsapp", "direction": "outbound", "customer_name": incoming["customer_name"], "phone": incoming["phone"], "message": result["reply"], "intent": "ai_reply", "created_at": datetime.utcnow().isoformat() + "Z"})
                    save_messages(messages)
                self.send_json(200, out); return

            if self.path in ("/chat", "/webhook/message"):
                user=auth_required(self)
                if not user: return
                if not user_can(user,"messages"):
                    self.send_json(403,{"error":"AI müşteri asistanına erişim yetkiniz yok."}); return
                message = str(data.get("message", "")).strip()
                if not message:
                    self.send_json(400, {"error": "message alanı gerekli"}); return
                result = handle_customer_message(message, str(data.get("channel", "web")).strip() or "web", str(data.get("customer_name", "")).strip(), str(data.get("phone", "")).strip(), user)
                self.send_json(200, result); return

            if self.path == "/api/users/create":
                user=auth_required(self)
                if not user: return
                if user[3] != "owner":
                    self.send_json(403,{"error":"Sadece işletme sahibi çalışan ekleyebilir."}); return
                email=str(data.get("email","")).strip().lower()
                password=str(data.get("password",""))
                role=str(data.get("role","staff")).strip()
                if not email or "@" not in email or len(password) < 8:
                    self.send_json(400,{"error":"Geçerli e-posta ve en az 8 karakterli şifre gerekli."}); return
                if role != "staff":
                    self.send_json(400,{"error":"Yeni çalışan hesapları staff rolüyle oluşturulabilir."}); return
                if database.get_user_by_email(email):
                    self.send_json(409,{"error":"Bu e-posta zaten kayıtlı."}); return
                uid=database.create_user(email,hash_password(password),user[1],role)
                self.send_json(201,{"id":uid,"email":email,"role":role}); return

            if self.path == "/api/users/permissions":
                user=auth_required(self)
                if not user: return
                if user[3] != "owner":
                    self.send_json(403,{"error":"Sadece işletme sahibi yetki değiştirebilir."}); return
                target=int(data.get("user_id",0))
                allowed={"appointments","customers","messages","business_settings","team","reports"}
                incoming=data.get("permissions")
                if not isinstance(incoming,dict):
                    self.send_json(400,{"error":"permissions gerekli"}); return
                permissions={k:bool(incoming.get(k,False)) for k in allowed}
                if target == user[0]:
                    self.send_json(400,{"error":"Kendi owner yetkilerinizi bu ekrandan değiştirmeyin."}); return
                target_user=database.get_user_in_business(user[1],target)
                if not target_user:
                    self.send_json(404,{"error":"Çalışan bulunamadı"}); return
                if target_user[2] == "owner":
                    self.send_json(400,{"error":"Owner hesabının yetkileri değiştirilemez."}); return
                if not database.update_user_permissions(user[1],target,permissions):
                    self.send_json(404,{"error":"Çalışan bulunamadı"}); return
                self.send_json(200,{"success":True,"permissions":permissions}); return

            if self.path == "/api/users/role":
                user=auth_required(self)
                if not user: return
                if user[3] != "owner":
                    self.send_json(403,{"error":"Sadece işletme sahibi yetki değiştirebilir."}); return
                target=int(data.get("user_id",0)); role=str(data.get("role","staff")).strip()
                if role not in {"owner","staff"}:
                    self.send_json(400,{"error":"Geçersiz rol."}); return
                if target == user[0]:
                    self.send_json(400,{"error":"Kendi rolünüzü bu ekrandan değiştiremezsiniz."}); return
                target_user=database.get_user_in_business(user[1],target)
                if not target_user:
                    self.send_json(404,{"error":"Kullanıcı bulunamadı."}); return
                if target_user[2] == "owner" and role == "staff" and database.count_owners(user[1]) <= 1:
                    self.send_json(400,{"error":"İşletmede en az bir owner kalmalıdır."}); return
                database.update_user_role(user[1],target,role)
                self.send_json(200,{"success":True,"role":role}); return

            if self.path == "/api/business":
                user=auth_required(self)
                if not user: return
                if not user_can(user,"business_settings"):
                    self.send_json(403,{"error":"İşletme ayarlarını değiştirme yetkiniz yok."}); return
                name=str(data.get("business_name","")).strip()
                config=data.get("config") if isinstance(data.get("config"),dict) else data
                if name: config["business_name"]=name
                if not str(config.get("business_name","")).strip():
                    self.send_json(400,{"error":"İşletme adı gerekli"}); return
                database.update_business(user[1], str(config["business_name"]), config)
                self.send_json(200, config); return

            if self.path == "/api/appointments":
                user=auth_required(self)
                if not user: return
                if not user_can(user,"appointments"):
                    self.send_json(403,{"error":"Randevu işlemi yapma yetkiniz yok."}); return
                appointment, error = create_appointment(data, user)
                if not appointment:
                    self.send_json(409 if error and "başka bir randevu" in error else 400, {"error": error or "Randevu oluşturulamadı"}); return
                self.send_json(201, appointment); return

            if self.path == "/api/appointments/status":
                user=auth_required(self)
                if not user: return
                if not user_can(user,"appointments"):
                    self.send_json(403,{"error":"Randevu durumunu değiştirme yetkiniz yok."}); return
                appointment_id = str(data.get("id", "")).strip()
                status = str(data.get("status", "")).strip()
                if status not in {"pending", "confirmed", "cancelled", "completed"}:
                    self.send_json(400, {"error": "Geçersiz durum"}); return
                items = load_appointments(user)
                for item in items:
                    if item["id"] == appointment_id:
                        item["status"] = status
                        save_appointments(items, user)
                        self.send_json(200, {"success": True}); return
                self.send_json(404, {"error": "Randevu bulunamadı"}); return

            self.send_json(404, {"error": "Not found"})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

if __name__ == "__main__":
    initialize_database()
    port = int(os.environ.get("PORT", "8080"))
    print(f"AI İşletme Asistanı: http://0.0.0.0:{port}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()