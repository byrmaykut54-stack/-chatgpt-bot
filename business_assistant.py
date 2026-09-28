import json
import os
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

CONFIG_PATH = "business_config.json"
HTML_PATH = "index.html"
APPOINTMENTS_PATH = "appointments.json"

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

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return DEFAULT_CONFIG
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_appointments():
    if not os.path.exists(APPOINTMENTS_PATH):
        return []
    with open(APPOINTMENTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_appointments(items):
    with open(APPOINTMENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def gemini_request(prompt, max_tokens=1000):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY tanımlı değil.")

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens}
    }
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        data = json.load(response)
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

def ask_gemini(message, config):
    business_context = json.dumps(config, ensure_ascii=False, indent=2)
    prompt = f"""{SYSTEM_PROMPT}

İŞLETME BİLGİLERİ:
{business_context}

MÜŞTERİ MESAJI:
{message}

Sadece müşteriye gönderilebilecek cevabı üret."""
    return gemini_request(prompt, 1200)

def detect_appointment_intent(message, config):
    business_context = json.dumps(config, ensure_ascii=False)
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
{business_context}

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
        return None
    items = load_appointments()
    items.append(appointment)
    save_appointments(items)
    return appointment

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
        if self.path in ("/", "/index.html"):
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

        if self.path == "/api/business":
            self.send_json(200, load_config())
            return

        if self.path == "/api/appointments":
            self.send_json(200, load_appointments())
            return

        if self.path == "/health":
            self.send_json(200, {"status": "ok", "service": "AI İşletme Asistanı"})
            return

        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            data = json.loads(raw or "{}")

            if self.path == "/chat":
                message = str(data.get("message", "")).strip()
                if not message:
                    self.send_json(400, {"error": "message alanı gerekli"})
                    return

                intent = detect_appointment_intent(message, load_config())
                if intent.get("intent") == "appointment":
                    required = ["customer_name", "phone", "date", "time", "service"]
                    missing = [x for x in required if not str(intent.get(x, "")).strip()]
                    if not missing:
                        appointment = create_appointment(intent)
                        if appointment:
                            self.send_json(200, {
                                "reply": f"Randevunuzu aldım. {appointment['date']} {appointment['time']} için {appointment['service']} kaydınız oluşturuldu.",
                                "appointment": appointment
                            })
                            return

                    labels = {
                        "customer_name": "adınızı",
                        "phone": "telefon numaranızı",
                        "date": "tarihi",
                        "time": "saati",
                        "service": "hizmeti"
                    }
                    missing_text = ", ".join(labels[x] for x in missing)
                    self.send_json(200, {
                        "reply": f"Randevu oluşturabilmem için lütfen {missing_text} bilgisini de paylaşır mısınız?"
                    })
                    return

                answer = ask_gemini(message, load_config())
                self.send_json(200, {"reply": answer})
                return

            if self.path == "/api/appointments":
                appointment = create_appointment(data)
                if not appointment:
                    self.send_json(400, {"error": "customer_name, phone, date, time ve service zorunlu"})
                    return
                self.send_json(201, appointment)
                return

            if self.path == "/api/appointments/status":
                appointment_id = str(data.get("id", "")).strip()
                status = str(data.get("status", "")).strip()
                allowed = {"pending", "confirmed", "cancelled", "completed"}
                if not appointment_id or status not in allowed:
                    self.send_json(400, {"error": "Geçersiz id veya durum"})
                    return
                items = load_appointments()
                for item in items:
                    if item["id"] == appointment_id:
                        item["status"] = status
                        save_appointments(items)
                        self.send_json(200, {"success": True})
                        return
                self.send_json(404, {"error": "Randevu bulunamadı"})
                return

            self.send_json(404, {"error": "Not found"})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

if __name__ == "__main__":
    print("AI İşletme Asistanı: http://localhost:8080")
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
