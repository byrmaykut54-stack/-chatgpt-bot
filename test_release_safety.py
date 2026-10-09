"""HTTP regressions that do not require production data or provider calls."""
import http.client
import json
import os
import threading
import unittest
from http.server import HTTPServer
from urllib.parse import quote
from unittest.mock import patch
import business_assistant


class ReleaseSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(('127.0.0.1', 0), business_assistant.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def get(self, path):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        conn.request('GET', path)
        response = conn.getresponse()
        body = response.read().decode()
        conn.close()
        self.assertEqual(response.status, 200)
        return body

    def test_checkout_token_cannot_close_script_element(self):
        token = '</script><script>alert(1)</script>'
        body = self.get('/billing/iyzico/callback?token=' + quote(token, safe=''))
        self.assertNotIn(token, body)
        self.assertEqual(body.count('</script>'), 1)
        self.assertIn('\\u003c/script\\u003e', body)

    def test_payment_readiness_requires_complete_checkout_configuration(self):
        keys = ('IYZICO_API_KEY', 'IYZICO_SECRET_KEY', 'IYZICO_PRICING_PLAN_REFERENCE_CODE', 'IYZICO_SUBSCRIPTION_CALLBACK_URL')
        with patch.dict(os.environ, {}, clear=True):
            for count in range(5):
                os.environ.update({key: 'test-only' for key in keys[:count]})
                self.assertEqual(json.loads(self.get('/api/capabilities'))['payments_configured'], count == 4)
            os.environ[keys[2]] = ' '
            self.assertFalse(json.loads(self.get('/api/capabilities'))['payments_configured'])
