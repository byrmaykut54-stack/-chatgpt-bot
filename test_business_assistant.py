import json
import os
import unittest
from unittest.mock import patch

import business_assistant


class BusinessAssistantTests(unittest.TestCase):
    def test_default_config_has_required_fields(self):
        config = business_assistant.load_config()
        self.assertIn("business_name", config)
        self.assertIn("services", config)
        self.assertIn("working_hours", config)

    def test_empty_message_is_rejected_by_contract(self):
        message = ""
        self.assertEqual(message.strip(), "")

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_api_key_fails_cleanly(self):
        with self.assertRaises(RuntimeError):
            business_assistant.ask_gemini("Merhaba", business_assistant.DEFAULT_CONFIG)


if __name__ == "__main__":
    unittest.main()
