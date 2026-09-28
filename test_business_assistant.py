import os
import tempfile
import unittest
from unittest.mock import patch

import business_assistant


class BusinessAssistantTests(unittest.TestCase):
    def test_default_config_has_required_fields(self):
        config = business_assistant.load_config()
        self.assertIn("business_name", config)
        self.assertIn("services", config)
        self.assertIn("working_hours", config)

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_api_key_fails_cleanly(self):
        with self.assertRaises(RuntimeError):
            business_assistant.ask_gemini("Merhaba", business_assistant.DEFAULT_CONFIG)

    def test_create_appointment_requires_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "appointments.json")
            with patch.object(business_assistant, "APPOINTMENTS_PATH", path):
                appointment, error = business_assistant.create_appointment({})
                self.assertIsNone(appointment)
                self.assertIn("zorunlu", error)

    def test_create_appointment_blocks_same_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "appointments.json")
            data = {
                "customer_name": "Ali", "phone": "05550000000",
                "date": "2026-10-01", "time": "14:00", "service": "Saç Kesimi"
            }
            with patch.object(business_assistant, "APPOINTMENTS_PATH", path):
                first, error1 = business_assistant.create_appointment(data)
                second, error2 = business_assistant.create_appointment({**data, "customer_name": "Veli"})
                self.assertIsNotNone(first)
                self.assertIsNone(error1)
                self.assertIsNone(second)
                self.assertIn("başka bir randevu", error2)

    def test_parse_whatsapp_text(self):
        payload = {
            "entry": [{"changes": [{"value": {
                "contacts": [{"profile": {"name": "Ali"}}],
                "messages": [{"id": "wamid.test", "from": "905551112233", "type": "text", "text": {"body": "Merhaba"}}]
            }}]}]
        }
        parsed = business_assistant.parse_whatsapp_message(payload)
        self.assertEqual(parsed["message"], "Merhaba")
        self.assertEqual(parsed["phone"], "905551112233")
        self.assertEqual(parsed["message_id"], "wamid.test")


if __name__ == "__main__":
    unittest.main()
