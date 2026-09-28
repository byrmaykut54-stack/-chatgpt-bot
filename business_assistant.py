import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

CONFIG_PATH = "business_config.json"

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

def ask_gemini(message, config):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY tanımlı değil.")

    business_context = json.dumps(config, ensure_ascii=False, indent=2)
    prompt = f"""{SYSTEM_PROMPT}

İŞLETME BİLGİLERİ:
{business_context}

MÜŞTERİ MESAJI:
{message}

Sadece müşteriye gönderilebilecek cevabı üret."""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
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

class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok", "service": "AI İşletme Asistanı"})
            return
        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/chat":
            self.send_json(404, {"error": "Not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            data = json.loads(raw or "{}")
            message = str(data.get("message", "")).strip()

            if not message:
                self.send_json(400, {"error": "message alanı gerekli"})
                return

            answer = ask_gemini(message, load_config())
            self.send_json(200, {"reply": answer})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

if __name__ == "__main__":
    print("AI İşletme Asistanı çalışıyor: http://localhost:8080")
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
