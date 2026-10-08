"""Calendar contract tests; Google responses are mocked, never live accounts."""
import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import google_calendar as cal


class CalendarRegressionTests(unittest.TestCase):
    def test_oauth_requests_events_scope_and_offline_access(self):
        with patch.dict(os.environ, {'GOOGLE_CLIENT_ID':'test-id','GOOGLE_CLIENT_SECRET':'test-secret',
                                   'PUBLIC_APP_URL':'https://example.test'}), \
             patch.object(cal.database, 'create_google_oauth_state') as save:
            url = cal.authorization_url(11, 22)
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query['scope'], [cal.CALENDAR_SCOPE])
        self.assertEqual(query['access_type'], ['offline'])
        self.assertEqual(query['redirect_uri'], ['https://example.test/oauth/google/callback'])
        self.assertEqual(save.call_args.args[1:3], (11, 22))
        self.assertEqual(query['state'], [save.call_args.args[0]])

    def test_invalid_oauth_state_never_exchanges_code_or_saves_connection(self):
        with patch.object(cal.database, 'consume_google_oauth_state', return_value=None), \
             patch.object(cal, 'exchange_code') as exchange, \
             patch.object(cal.database, 'save_google_calendar_connection') as save:
            with self.assertRaises(RuntimeError):
                cal.connect_from_callback('expired-or-replayed', 'code')
            exchange.assert_not_called(); save.assert_not_called()

    def test_missing_refresh_token_reuses_only_same_business_connection(self):
        with patch.object(cal.database, 'consume_google_oauth_state', return_value={'business_id':11,'user_id':22}), \
             patch.object(cal, 'exchange_code', return_value={'access_token':'new-access','scope':cal.CALENDAR_SCOPE}), \
             patch.object(cal.database, 'get_google_calendar_connection', return_value={'refresh_token':'existing-refresh'}) as get, \
             patch.object(cal.database, 'save_google_calendar_connection') as save, \
             patch.object(cal, '_api') as api:
            self.assertEqual(cal.connect_from_callback('state','code'), 11)
            get.assert_called_once_with(11)
            self.assertEqual(save.call_args.args[:3], (11,22,'primary'))
            self.assertEqual(save.call_args.args[5], 'existing-refresh')
            api.assert_not_called()

    def test_expired_access_token_is_refreshed_and_persisted(self):
        expired = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        with patch.object(cal.database, 'get_google_calendar_connection', return_value={
                'access_token':'old','refresh_token':'refresh','access_token_expires_at':expired}), \
             patch.object(cal, 'refresh_access_token', return_value={'access_token':'new','expires_in':3600}) as refresh, \
             patch.object(cal.database, 'update_google_calendar_tokens') as save:
            self.assertEqual(cal.access_token_for_business(11), 'new')
            refresh.assert_called_once_with('refresh')
            self.assertEqual(save.call_args.args[:2], (11,'new'))

    def test_event_lifecycle_posts_then_patches_without_duplicate_and_deletes(self):
        appointment = {'id':'a1','date':'2099-10-05','time':'14:00','service':'Saç Kesimi',
                       'customer_name':'Ali','status':'pending'}
        connection = {'calendar_id':'primary'}
        with patch.object(cal.database, 'get_google_calendar_connection', return_value=connection), \
             patch.object(cal, 'access_token_for_business', return_value='events-token'), \
             patch.object(cal.database, 'get_google_event_id', side_effect=[None,'event-1','event-1']) as get, \
             patch.object(cal.database, 'get_business_config', return_value={'timezone':'Europe/Istanbul','appointment_duration_minutes':60}), \
             patch.object(cal.database, 'save_google_event') as save, \
             patch.object(cal.database, 'delete_google_event') as delete, \
             patch.object(cal, '_api', return_value={'id':'event-1'}) as api:
            cal.sync_appointment(11, appointment)
            cal.sync_appointment(11, {**appointment,'status':'confirmed'})
            cal.sync_appointment(11, {**appointment,'status':'cancelled'})
        self.assertEqual([c.args[0] for c in api.call_args_list], ['POST','PATCH','DELETE'])
        self.assertEqual([c.args[1] for c in api.call_args_list], [
            '/calendars/primary/events','/calendars/primary/events/event-1','/calendars/primary/events/event-1'])
        payload = api.call_args_list[0].args[3]
        self.assertEqual(payload['start'], {'dateTime':'2099-10-05T14:00:00','timeZone':'Europe/Istanbul'})
        self.assertEqual(payload['end']['dateTime'], '2099-10-05T15:00:00')
        self.assertEqual(payload['extendedProperties']['private']['nexora_business_id'], '11')
        save.assert_called_once_with('a1',11,'event-1')
        delete.assert_called_once_with('a1',11)
        self.assertTrue(all(c.args == ('a1',11) for c in get.call_args_list))

    def test_api_accepts_empty_success_response_on_event_delete(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return b''
        with patch.object(cal.urllib.request, 'urlopen', return_value=Response()) as request:
            self.assertEqual(cal._api('DELETE','/calendars/primary/events/event-1','token'), {})
        self.assertEqual(request.call_args.args[0].method, 'DELETE')

    def test_delete_already_missing_event_removes_mapping(self):
        with patch.object(cal.database, 'get_google_calendar_connection', return_value={'calendar_id':'primary'}), \
             patch.object(cal, 'access_token_for_business', return_value='token'), \
             patch.object(cal.database, 'get_google_event_id', return_value='gone'), \
             patch.object(cal.database, 'delete_google_event') as delete, \
             patch.object(cal, '_api', side_effect=HTTPError('https://example.test',404,'gone',{},io.BytesIO())):
            cal.sync_appointment(11, {'id':'a1','status':'cancelled'})
            delete.assert_called_once_with('a1',11)

    def test_failed_events_report_failure_without_saving_mapping(self):
        appt={'id':'a1','date':'2099-10-05','time':'14:00','service':'Saç','customer_name':'Ali'}
        for code in [403,429,500]:
            with self.subTest(status=code), \
                 patch.object(cal.database, 'get_google_calendar_connection', return_value={'calendar_id':'primary'}), \
                 patch.object(cal, 'access_token_for_business', return_value='token'), \
                 patch.object(cal.database, 'get_google_event_id', return_value=None), \
                 patch.object(cal.database, 'get_business_config', return_value={}), \
                 patch.object(cal.database, 'save_google_event') as save, \
                 patch.object(cal, '_api', side_effect=HTTPError('https://example.test',code,'error',{},io.BytesIO())):
                results=cal.sync_all(11,[appt])
                self.assertFalse(results[0]['ok'])
                save.assert_not_called()
