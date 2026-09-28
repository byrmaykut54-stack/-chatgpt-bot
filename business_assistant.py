import hashlib
import hmac
import json
import os
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

CONFIG_PATH = "business_config.json"
HTML_PATH = "index.html"
APPOINTMENTS_PATH = "appointments.json"
MESSAGES_PATH = "messages.json"

DEFAULT_CONFIG = {
    "business_name": "Demo İşletme",
    "sector": "Berber",
    "services": [
        {"name": "Saç Kesimi", "price": "500 TL"},
        {"name": "Saç + Sakal", "price": "750 TL"}
    ],
    "working_hours": "09:00-19:00",
    "address": "İşletme adresini config dosyasına yazın",
    "tone": "samimi, kısa ve profesyonel"
}

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

def load_appointments():
    return load_json(APPOINTMENTS_PATH, [])

def save_appointments(items):
    save_json(APPOINTMENTS_PATH, items)

def load_messages():
    return load_json(MESSAGES_PATH, [])

def save_messages(items):
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

def create_appointment(data):
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

    items = load_appointments()
    active = {"pending", "confirmed"}
    conflict = next((item for item in items if item.get("date") == appointment["date"] and item.get("time") == appointment["time"] and item.get("status") in active), None)
    if conflict:
        return None, "Bu tarih ve saatte başka bir randevu bulunuyor."

    items.append(appointment)
    save_appointments(items)
    return appointment, None

def local_intent_hint(message, config):
    text = message.lower()
    appointment_words = ("randevu", "rezervasyon", "uygun saat", "saat")
    if any(word in text for word in appointment_words):
        import re
        time_match = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", text)
        service = ""
        for item in config.get("services", []):
            name = str(item.get("name", "")).strip()
            if name and name.lower() in text:
                service = name
                break
        return {
            "intent": "appointment",
            "customer_name": "",
            "phone": "",
            "date": "",
            "time": f"{int(time_match.group(1)):02d}:{time_match.group(2)}" if time_match else "",
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

def handle_customer_message(message, channel="web", customer_name="", phone=""):
    config = load_config()
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
    messages = load_messages()
    messages.append(record)
    save_messages(messages)

    if intent.get("intent") == "price":
        return {"reply": f"{intent.get('service')} fiyatı {intent.get('price')}."}

    if intent.get("intent") == "appointment":
        intent["customer_name"] = record["customer_name"]
        intent["phone"] = record["phone"]
        required = ["customer_name", "phone", "date", "time", "service"]
        missing = [x for x in required if not str(intent.get(x, "")).strip()]
        if not missing:
            appointment, error = create_appointment(intent)
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

def verify_whatsapp_signature(raw_body, signature):
    secret = os.environ.get("META_APP_SECRET", "").strip()
    if not secret:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

def whatsapp_message_already_processed(message_id):
    if not message_id:
        return False
    return any(item.get("message_id") == message_id for item in load_messages())

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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

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
        if parsed.path == "/api/business":
            self.send_json(200, load_config()); return
        if parsed.path == "/api/appointments":
            self.send_json(200, load_appointments()); return
        if parsed.path == "/api/messages":
            self.send_json(200, load_messages()); return
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
                message = str(data.get("message", "")).strip()
                if not message:
                    self.send_json(400, {"error": "message alanı gerekli"}); return
                result = handle_customer_message(message, str(data.get("channel", "web")).strip() or "web", str(data.get("customer_name", "")).strip(), str(data.get("phone", "")).strip())
                self.send_json(200, result); return

            if self.path == "/api/appointments":
                appointment, error = create_appointment(data)
                if not appointment:
                    self.send_json(409 if error and "başka bir randevu" in error else 400, {"error": error or "Randevu oluşturulamadı"}); return
                self.send_json(201, appointment); return

            if self.path == "/api/appointments/status":
                appointment_id = str(data.get("id", "")).strip()
                status = str(data.get("status", "")).strip()
                if status not in {"pending", "confirmed", "cancelled", "completed"}:
                    self.send_json(400, {"error": "Geçersiz durum"}); return
                items = load_appointments()
                for item in items:
                    if item["id"] == appointment_id:
                        item["status"] = status
                        save_appointments(items)
                        self.send_json(200, {"success": True}); return
                self.send_json(404, {"error": "Randevu bulunamadı"}); return

            self.send_json(404, {"error": "Not found"})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"AI İşletme Asistanı: http://0.0.0.0:{port}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
