"""Run only against an explicitly isolated PostgreSQL TEST_DATABASE_URL."""
import os
import json
import unittest
import threading
import http.client
from http.server import HTTPServer
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Dedicated TEST_DATABASE_URL required')
class AuthIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import database, business_assistant
        cls.db = database
        cls.module = business_assistant
        database.DATABASE_URL = os.environ['TEST_DATABASE_URL']
        database.ensure_schema()
        cls.server = HTTPServer(('127.0.0.1', 0), business_assistant.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        with self.db.connection() as c:
            c.execute('TRUNCATE businesses RESTART IDENTITY CASCADE')
        self.module.RATE_LIMITS.clear()

    def request(self, path, payload=None, cookie='', raw=None, method='POST'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        headers = {'Content-Type':'application/json','Origin':f'https://127.0.0.1:{self.server.server_port}'}
        if cookie: headers['Cookie'] = cookie
        conn.request(method,path,body=raw if raw is not None else json.dumps(payload or {}),headers=headers)
        response = conn.getresponse()
        result = response.status, json.loads(response.read()), response.getheader('Set-Cookie', '').split(';')[0]
        conn.close()
        return result

    def test_register_login_appointment_and_tenant_isolation(self):
        with patch.object(self.module, 'send_email', return_value=False):
            status, data, cookie = self.request('/api/auth/register', {'email':'one@example.com','password':'longpassword123','business_name':'Test One'})
            self.assertEqual(status,201)
            self.assertEqual(self.request('/api/auth/register', {'email':'one@example.com','password':'longpassword123','business_name':'Duplicate'})[0],409)
            with self.db.connection() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM businesses').fetchone()[0],1)
            status, _, other_cookie = self.request('/api/auth/register', {'email':'two@example.com','password':'longpassword123','business_name':'Test Two'})
            self.assertEqual(status,201)
        self.assertEqual(self.request('/api/auth/login',{'email':'one@example.com','password':'wrongpassword'})[0],401)
        self.assertEqual(self.request('/api/auth/login',{'email':'one@example.com','password':'longpassword123'})[0],200)
        appt={'customer_name':'Ali','phone':'05550000000','date':'2099-10-05','time':'14:00','service':'Saç Kesimi'}
        self.assertEqual(self.request('/api/appointments',appt,cookie)[0],201)
        self.assertEqual(self.request('/api/appointments',appt,cookie)[0],409)
        status, appointments, _ = self.request('/api/appointments',cookie=other_cookie,method='GET')
        self.assertEqual(status,200)
        self.assertEqual(appointments,[])

    def test_atomic_recovery_single_use_revokes_sessions(self):
        bid,uid=self.db.register_business_owner('Test', self.module.DEFAULT_CONFIG,'reset@example.com',self.module.hash_password('longpassword123'))
        self.db.create_session('session-hash',uid,datetime.now(timezone.utc)+timedelta(days=1))
        self.db.create_password_reset_token('reset-token-hash',uid,datetime.now(timezone.utc)+timedelta(minutes=30))
        self.assertEqual(self.db.reset_password_with_token('reset-token-hash',self.module.hash_password('newlongpassword123')),uid)
        self.assertIsNone(self.db.reset_password_with_token('reset-token-hash','unused'))
        with self.db.connection() as c:
            self.assertIsNotNone(c.execute("SELECT revoked_at FROM sessions WHERE token_hash='session-hash'").fetchone()[0])

    def test_bad_json_and_health(self):
        self.assertEqual(self.request('/api/auth/register',raw='[]')[0],400)
        self.assertEqual(self.request('/api/auth/register',raw='{bad')[0],400)
        status,data,_=self.request('/health',method='GET')
        self.assertEqual(status,200)
        self.assertEqual(data['database'],'ok')

    def test_duplicate_registration_rolls_back_business(self):
        self.db.register_business_owner('First',self.module.DEFAULT_CONFIG,'duplicate@example.com','hash')
        with self.assertRaises(self.db.psycopg.errors.UniqueViolation):
            self.db.register_business_owner('Second',self.module.DEFAULT_CONFIG,'duplicate@example.com','hash')
        with self.db.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM businesses').fetchone()[0],1)
