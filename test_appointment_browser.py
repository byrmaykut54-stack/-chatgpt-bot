"""Opt-in real Chromium tests against the full frontend with isolated API fixtures.
Run with RUN_BROWSER_TESTS=1 after installing requirements-test.txt and Chromium.
"""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


@unittest.skipUnless(os.getenv('RUN_BROWSER_TESTS') == '1', 'RUN_BROWSER_TESTS=1 required')
class AppointmentBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)
        cls.html = Path('index.html').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context(timezone_id='Europe/Istanbul', viewport={'width':1280,'height':900})
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.errors = []
        self.page.on('pageerror', lambda err: self.errors.append(str(err)))
        self.items = []
        self.requests = []
        self.status_error = None
        self.date = datetime.now(timezone(timedelta(hours=3))).strftime('%Y-%m-%d')
        self.items.append({'id':'a1','customer_name':'Ali','phone':'05550000000','date':self.date,
                           'time':'14:00','service':'Saç Kesimi','status':'pending'})
        self.page.on('dialog', lambda dialog: dialog.dismiss())
        self.context.route('**/*', self.route)
        self.page.goto('https://mexay.test/#appointments')
        self.page.wait_for_selector('#app', state='visible')
        self.page.wait_for_selector('#nxAppointmentList .nx-table-link')

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], 'Unhandled browser JavaScript errors')

    def route(self, route):
        req=route.request
        parsed=urlparse(req.url)
        path=parsed.path
        if parsed.hostname != 'mexay.test':
            route.abort(); return
        if path == '/':
            route.fulfill(status=200, content_type='text/html', body=self.html); return
        body = {}
        code = 200
        if path == '/api/auth/status': body={'authenticated':True,'email':'test@example.com'}
        elif path == '/api/master/status': code=403
        elif path == '/api/business': body={'business_name':'Test','sector':'Berber','working_hours':'09:00-18:00','services':[],'closed_days':['Pazar']}
        elif path == '/api/appointments' and req.method == 'GET': body=self.items
        elif path == '/api/appointments/status':
            data=req.post_data_json
            self.requests.append(data)
            if self.status_error == 'network':
                route.abort(); return
            if self.status_error:
                code=self.status_error; body={'error':'Durum güncellenemedi.'}
            else:
                next(x for x in self.items if x['id']==data['id'])['status']=data['status']
                body={'success':True}
        elif path == '/api/appointments' and req.method == 'POST':
            data=req.post_data_json
            self.requests.append(data)
            body={**data,'id':'a2','status':'pending'}
            self.items.append(body); code=201
        elif path == '/api/subscription': body={'plan':'trial','status':'active','trial_active':True}
        elif path == '/api/usage': body={'plan':'trial','limits':{},'used':{},'remaining':{}}
        elif path == '/api/calendar/status': body={'connected':False}
        elif path == '/api/availability': body={'closed':False,'slots':['14:00','15:00']}
        elif path in ['/api/messages','/api/customers','/api/users','/api/audit']: body=[]
        elif path == '/api/reports': body={}
        else: code=404
        route.fulfill(status=code, content_type='application/json', body=json.dumps(body))

    def open_detail(self):
        self.page.locator('#nxAppointmentList .nx-table-link').first.click()
        self.page.wait_for_selector('#nxAppointmentModal.is-open')
        self.assertEqual(self.page.locator('#nxModalCustomer').inner_text(), 'Ali')
        self.assertEqual(self.page.locator('#nxModalPhone').inner_text(), '05550000000')
        self.assertEqual(self.page.locator('#nxModalTime').inner_text(), '14:00')

    def test_detail_actions_persist_on_reload_desktop_and_mobile(self):
        for viewport in [{'width':1280,'height':900},{'width':390,'height':844}]:
            with self.subTest(viewport=viewport):
                self.page.set_viewport_size(viewport)
                self.items[0]['status']='pending'
                self.page.reload()
                self.page.wait_for_selector('#nxAppointmentList .nx-table-link')
                self.open_detail()
                self.page.locator('#nxModalActions .approve').click()
                self.page.wait_for_selector('#nxAppointmentModal.is-open', state='hidden')
                self.assertEqual(self.requests[-1], {'id':'a1','status':'confirmed'})
                self.page.reload(); self.page.wait_for_selector('#nxAppointmentList .nx-table-link')
                self.open_detail()
                self.page.locator('#nxModalActions .approve').click()
                self.page.wait_for_selector('#nxAppointmentModal.is-open', state='hidden')
                self.assertEqual(self.requests[-1], {'id':'a1','status':'completed'})
                self.page.reload(); self.page.wait_for_selector('#nxAppointmentList .nx-table-link')
                self.open_detail()
                self.assertEqual(self.page.locator('#nxModalActions .approve').count(), 0)
                self.page.locator('#nxModalClose').click()
                self.items[0]['status']='pending'
                self.page.reload(); self.page.wait_for_selector('#nxAppointmentList .nx-table-link')
                self.open_detail()
                self.page.locator('#nxModalActions .cancel').click()
                self.page.wait_for_selector('#nxAppointmentModal.is-open', state='hidden')
                self.assertEqual(self.requests[-1], {'id':'a1','status':'cancelled'})

    def test_failed_status_request_keeps_detail_and_original_status(self):
        for status in [401,409,500,'network']:
            with self.subTest(status=status):
                self.status_error=status
                self.open_detail()
                self.page.locator('#nxModalActions .approve').click()
                self.page.wait_for_function("!document.getElementById('nxModalActions').dataset.busy")
                self.assertEqual(self.page.locator('#nxAppointmentModal').get_attribute('aria-hidden'), 'false')
                self.assertEqual(self.items[0]['status'], 'pending')
                self.page.locator('#nxModalClose').click()

    def test_create_appointment_and_reload(self):
        date=datetime.now(timezone(timedelta(hours=3)))+timedelta(days=1)
        while date.weekday()==6: date+=timedelta(days=1)
        self.page.locator('#customer_name').fill('Veli')
        self.page.locator('#phone').fill('05551112233')
        self.page.locator('#date').fill(date.strftime('%Y-%m-%d'))
        self.page.locator('#time').fill('15:00')
        self.page.locator('#service').fill('Saç Kesimi')
        self.page.locator('#appointmentForm button[type=submit]').click()
        self.page.wait_for_function("document.getElementById('nxCreateStatus').classList.contains('ok')")
        self.assertEqual(self.items[-1]['customer_name'], 'Veli')
        self.page.reload()
        self.page.wait_for_selector('#nxApptDate')
        self.page.locator('#nxApptDate').fill(date.strftime('%Y-%m-%d'))
        self.page.locator('#nxApptDate').dispatch_event('change')
        self.page.wait_for_selector('#nxAppointmentList .nx-table-link')
        self.assertIn('Veli', self.page.locator('#nxAppointmentList').inner_text())
