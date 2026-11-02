import http.client
import tempfile
import threading
import unittest
from urllib.parse import urlencode
from aria_healthcare.workbench import Workbench
from aria_healthcare.service import ResearchService
from aria_healthcare.tasks import TaskService
from test_research import fixture_client


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = Workbench(self.temp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = 'aria_health_session=' + self.server.token

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, method, path, data=None, auth=True, origin=None):
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {}
        if auth:
            headers['Cookie'] = self.cookie
        if method == 'POST':
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            headers['Origin'] = origin or self.server.origin
        body = urlencode(data) if data is not None else None
        c.request(method, path, body, headers)
        r = c.getresponse()
        result = r.status, dict(r.getheaders()), r.read().decode()
        c.close()
        return result

    def test_session_bootstrap_and_cross_origin_rejected(self):
        self.assertEqual(self.request('GET', '/', auth=False)[0], 401)
        status, headers, _ = self.request('GET', '/?token=' + self.server.token, auth=False)
        self.assertEqual(status, 303)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertEqual(self.request('POST', '/create', {'csrf': self.server.token}, origin='https://example.invalid')[0], 403)
        self.assertEqual(self.request('POST', '/create', {'csrf': 'wrong'})[0], 403)

    def test_create_and_render_escapes_source_text(self):
        status, headers, _ = self.request('POST', '/create', {'csrf': self.server.token,
            'question': '<script>alert(1)</script>', 'inclusion': 'Public studies', 'exclusion': 'Private records'})
        self.assertEqual(status, 303)
        status, headers, body = self.request('GET', headers['Location'])
        self.assertEqual(status, 200)
        self.assertIn('&lt;script&gt;', body)
        self.assertNotIn('<script>', body)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def prepare(self):
        svc = ResearchService(self.temp.name, fixture_client())
        search = svc.search('synthetic')
        a = svc.article('12345')
        b = svc.create_bundle('Synthetic question', [{'text': 'Synthetic research finding',
            'pmid': '12345', 'source_sha256': a['content_sha256'], 'section': 0,
            'quote': '40 participants completed follow-up.', 'stance': 'uncertain'}], ['Not clinical evidence'], search['search_id'])
        tasks = TaskService(svc)
        t = tasks.create('Synthetic question', ['Public studies'], ['Private records'])
        t = tasks.screen(t['task_id'], t['digest'], '12345', 'include', 'Synthetic fixture')
        t = tasks.attach(t['task_id'], t['digest'], b['bundle_id'])
        t = tasks.request_review(t['task_id'], t['digest'])
        svc.close()
        return t

    def test_human_review_form_bound_to_displayed_revision(self):
        task = self.prepare()
        status, _, body = self.request('GET', '/task?id=' + task['task_id'])
        self.assertEqual(status, 200)
        self.assertIn('40 participants', body)
        self.assertIn('Record local operator review', body)
        data = {'csrf': self.server.token, 'task_id': task['task_id'], 'expected_digest': task['digest'],
                'reviewer': 'Test operator', 'decision': 'approve', 'note': 'Fixture only', 'confirmation': 'wrong'}
        self.assertEqual(self.request('POST', '/review', data)[0], 400)
        data['confirmation'] = task['digest']
        self.assertEqual(self.request('POST', '/review', data)[0], 303)
        self.assertEqual(self.request('POST', '/review', data)[0], 400)
        svc = ResearchService(self.temp.name, fixture_client())
        reviewed = TaskService(svc).get(task['task_id'])
        self.assertEqual(reviewed['snapshot']['stage'], 'review_recorded')
        self.assertIsNone(reviewed['scientifically_verified'])
        self.assertFalse(reviewed['snapshot']['review']['identity_verified'])
        svc.close()
