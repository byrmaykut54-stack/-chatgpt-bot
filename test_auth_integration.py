"""Run only against an explicitly isolated PostgreSQL TEST_DATABASE_URL."""
import os
import json
import unittest
import threading
import http.client
import hashlib
import re
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
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
        cls.original_database_url = database.DATABASE_URL
        database.DATABASE_URL = os.environ['TEST_DATABASE_URL']
        database.ensure_schema()
        cls.server = HTTPServer(('127.0.0.1', 0), business_assistant.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.db.DATABASE_URL = cls.original_database_url

    def setUp(self):
        with self.db.connection() as c:
            c.execute('TRUNCATE businesses, rate_limits RESTART IDENTITY CASCADE')
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



    def owner(self, email='recovery@example.com'):
        bid, uid = self.db.register_business_owner('Regression', self.module.DEFAULT_CONFIG,
            email, self.module.hash_password('original-password123'))
        return bid, uid

    def session(self, uid, raw_token):
        self.db.create_session(hashlib.sha256(raw_token.encode()).hexdigest(), uid,
            datetime.now(timezone.utc) + timedelta(days=1))
        return 'session=' + raw_token

    def test_http_recovery_changes_password_and_invalidates_all_sessions(self):
        _, uid = self.owner()
        cookies = [self.session(uid, 'old-session-one'), self.session(uid, 'old-session-two')]
        with patch.object(self.module, 'send_email', return_value=True) as mail:
            status, _, _ = self.request('/api/auth/forgot-password', {'email':'recovery@example.com'})
        self.assertEqual(status, 200)
        self.assertEqual(mail.call_args.args[0], 'recovery@example.com')
        token = re.search(r'reset_token=([a-f0-9]{64})', mail.call_args.args[2]).group(1)
        with self.db.connection() as c:
            stored = c.execute('SELECT token_hash FROM password_reset_tokens').fetchone()[0]
        self.assertEqual(stored, hashlib.sha256(token.encode()).hexdigest())
        payload = {'token': token, 'password': 'replacement-password123'}
        self.assertEqual(self.request('/api/auth/reset-password', payload)[0], 200)
        self.assertEqual(self.request('/api/auth/reset-password', payload)[0], 400)
        self.assertEqual(self.request('/api/auth/login', {'email':'recovery@example.com','password':'original-password123'})[0], 401)
        self.assertEqual(self.request('/api/auth/login', {'email':'recovery@example.com','password':'replacement-password123'})[0], 200)
        for cookie in cookies:
            self.assertFalse(self.request('/api/auth/status', cookie=cookie, method='GET')[1]['authenticated'])
            self.assertEqual(self.request('/api/appointments', cookie=cookie, method='GET')[0], 401)

    def test_expired_invalid_and_weak_password_do_not_consume_valid_token(self):
        _, uid = self.owner()
        valid, expired = 'a' * 64, 'b' * 64
        valid_hash = hashlib.sha256(valid.encode()).hexdigest()
        self.db.create_password_reset_token(valid_hash, uid, datetime.now(timezone.utc)+timedelta(minutes=30))
        # Insert separately: the public creator deliberately invalidates prior tokens.
        with self.db.connection() as c:
            c.execute('INSERT INTO password_reset_tokens(token_hash,user_id,expires_at) VALUES(%s,%s,%s)',
                (hashlib.sha256(expired.encode()).hexdigest(), uid, datetime.now(timezone.utc)-timedelta(seconds=1)))
        for token, password in [(expired,'replacement-password123'), ('c'*64,'replacement-password123'),
                                (valid,'short'), (valid,'x'*129)]:
            with self.subTest(token=token[:1], password_length=len(password)):
                self.assertEqual(self.request('/api/auth/reset-password', {'token':token,'password':password})[0], 400)
        self.assertTrue(self.module.verify_password('original-password123', self.db.get_user_by_email('recovery@example.com')[3]))
        self.assertEqual(self.request('/api/auth/reset-password', {'token':valid,'password':'replacement-password123'})[0], 200)

    def test_recovery_rollback_preserves_password_token_and_sessions(self):
        _, uid = self.owner()
        cookie = self.session(uid, 'rollback-session')
        self.db.create_password_reset_token('rollback-token', uid, datetime.now(timezone.utc)+timedelta(minutes=30))
        original_connection = self.db.connection
        @contextmanager
        def failing_connection():
            with original_connection() as conn:
                class Fault:
                    def execute(self, sql, params=None):
                        if sql.startswith('UPDATE sessions SET revoked_at'):
                            raise RuntimeError('Injected failure after password and token updates')
                        return conn.execute(sql, params)
                yield Fault()
        with patch.object(self.db, 'connection', failing_connection):
            with self.assertRaises(RuntimeError):
                self.db.reset_password_with_token('rollback-token', self.module.hash_password('replacement-password123'))
        self.assertTrue(self.module.verify_password('original-password123', self.db.get_user_by_email('recovery@example.com')[3]))
        self.assertTrue(self.request('/api/auth/status', cookie=cookie, method='GET')[1]['authenticated'])
        with self.db.connection() as c:
            self.assertIsNone(c.execute("SELECT used_at FROM password_reset_tokens WHERE token_hash='rollback-token'").fetchone()[0])
        self.assertEqual(self.db.reset_password_with_token('rollback-token', self.module.hash_password('replacement-password123')), uid)

    def test_concurrent_recovery_has_one_winner(self):
        _, uid = self.owner()
        self.db.create_password_reset_token('race-token', uid, datetime.now(timezone.utc)+timedelta(minutes=30))
        barrier = threading.Barrier(2)
        passwords = ['winner-one-password123', 'winner-two-password123']
        hashes = [self.module.hash_password(p) for p in passwords]
        def reset(i):
            barrier.wait(timeout=5)
            return self.db.reset_password_with_token('race-token', hashes[i])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reset, range(2)))
        self.assertEqual(results.count(uid), 1)
        self.assertEqual(results.count(None), 1)
        stored = self.db.get_user_by_email('recovery@example.com')[3]
        for i, password in enumerate(passwords):
            self.assertEqual(self.module.verify_password(password, stored), results[i] == uid)

    def test_failed_recovery_email_discards_token_and_unknown_account_sends_nothing(self):
        self.owner()
        with patch.object(self.module, 'send_email', return_value=False) as mail:
            self.assertEqual(self.request('/api/auth/forgot-password', {'email':'recovery@example.com'})[0], 503)
            self.assertEqual(mail.call_count, 1)
        with self.db.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM password_reset_tokens').fetchone()[0], 0)
        with patch.object(self.module, 'send_email') as mail:
            self.assertEqual(self.request('/api/auth/forgot-password', {'email':'unknown@example.com'})[0], 200)
            mail.assert_not_called()

    def test_recovery_rate_limit_blocks_fourth_request(self):
        self.owner()
        with patch.object(self.module, 'send_email', return_value=True) as mail:
            for _ in range(3):
                self.assertEqual(self.request('/api/auth/forgot-password', {'email':'recovery@example.com'})[0], 200)
            self.assertEqual(self.request('/api/auth/forgot-password', {'email':'recovery@example.com'})[0], 429)
            self.assertEqual(mail.call_count, 3)

    def test_appointment_status_is_tenant_scoped_and_requires_permission(self):
        bid, uid = self.owner()
        _, other_uid = self.owner('other@example.com')
        staff_uid = self.db.create_user('staff@example.com', 'hash', bid, 'staff')
        self.db.update_user_permissions(bid, staff_uid, {'appointments': False})
        owner_cookie = self.session(uid, 'owner-session')
        other_cookie = self.session(other_uid, 'other-session')
        staff_cookie = self.session(staff_uid, 'staff-session')
        appt = {'customer_name':'Ali','phone':'05550000000','date':'2099-10-05','time':'14:00','service':'Saç Kesimi'}
        status, created, _ = self.request('/api/appointments', appt, owner_cookie)
        self.assertEqual(status, 201)
        payload = {'id':created['id'], 'status':'confirmed'}
        self.assertEqual(self.request('/api/appointments/status', payload)[0], 401)
        self.assertEqual(self.request('/api/appointments/status', payload, other_cookie)[0], 404)
        self.assertEqual(self.request('/api/appointments/status', payload, staff_cookie)[0], 403)
        self.assertEqual(self.db.load_appointments_by_business(bid)[0]['status'], 'pending')
        for state in ['confirmed','completed','cancelled']:
            self.assertEqual(self.request('/api/appointments/status', {**payload,'status':state}, owner_cookie)[0], 200)
            self.assertEqual(self.db.load_appointments_by_business(bid)[0]['status'], state)

    def test_calendar_operations_require_owner_and_use_session_business(self):
        bid, uid = self.owner()
        other_bid, other_uid = self.owner('other@example.com')
        staff_uid = self.db.create_user('staff@example.com', 'hash', bid, 'staff')
        owner_cookie = self.session(uid, 'calendar-owner')
        other_cookie = self.session(other_uid, 'calendar-other')
        staff_cookie = self.session(staff_uid, 'calendar-staff')
        cal = self.module.google_calendar
        with patch.object(cal, 'configured', return_value=True), \
             patch.object(cal, 'authorization_url', return_value='https://example.test/oauth') as connect, \
             patch.object(cal, 'status', return_value={'connected':True}) as status, \
             patch.object(cal, 'sync_all', return_value=[]) as sync, \
             patch.object(cal, 'disconnect') as disconnect:
            for path, method in [('/api/calendar/connect','GET'),('/api/calendar/sync','POST'),('/api/calendar/disconnect','POST')]:
                self.assertEqual(self.request(path, method=method)[0], 401)
                self.assertEqual(self.request(path, cookie=staff_cookie, method=method)[0], 403)
            connect.assert_not_called(); sync.assert_not_called(); disconnect.assert_not_called()
            self.assertEqual(self.request('/api/calendar/connect', cookie=owner_cookie, method='GET')[0], 200)
            connect.assert_called_once_with(bid, uid)
            self.assertEqual(self.request('/api/calendar/sync', {'business_id':other_bid}, owner_cookie)[0], 200)
            sync.assert_called_once_with(bid, [])
            self.assertEqual(self.request('/api/calendar/disconnect', {'business_id':bid}, other_cookie)[0], 200)
            disconnect.assert_called_once_with(other_bid)
            self.assertEqual(self.request('/api/calendar/status', cookie=staff_cookie, method='GET')[0], 403)
