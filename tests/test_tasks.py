import tempfile
import unittest
from aria_healthcare.service import ResearchService
from aria_healthcare.tasks import TaskService
from aria_healthcare.server import Server
from test_research import fixture_client


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.svc = ResearchService(self.temp.name, fixture_client())
        self.tasks = TaskService(self.svc)

    def tearDown(self):
        self.svc.close()
        self.temp.cleanup()

    def ready(self):
        self.svc.search('synthetic')
        a = self.svc.article('12345')
        b = self.svc.create_bundle('Question', [{'text': 'Synthetic finding', 'pmid': '12345',
            'source_sha256': a['content_sha256'], 'section': 0,
            'quote': '40 participants completed follow-up.', 'stance': 'uncertain'}], ['Synthetic only'])
        t = self.tasks.create('Question', ['Public studies'], ['Private patient records'])
        t = self.tasks.screen(t['task_id'], t['digest'], '12345', 'include', 'Fixture matches draft criteria')
        t = self.tasks.attach(t['task_id'], t['digest'], b['bundle_id'])
        return self.tasks.request_review(t['task_id'], t['digest'])

    def test_review_bound_to_revision_and_invalidated_after_edit(self):
        t = self.ready()
        reviewed = self.tasks.record_operator_review(t['task_id'], t['digest'], 'Test operator', 'approve', 'Fixture review')
        self.assertFalse(reviewed['snapshot']['review']['identity_verified'])
        self.assertIsNone(reviewed['scientifically_verified'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.tasks.update(t['task_id'], t['digest'], 'Changed', ['One'], ['Two'])
        edited = self.tasks.update(t['task_id'], reviewed['digest'], 'Changed', ['One'], ['Two'])
        self.assertIsNone(edited['snapshot']['review'])
        self.assertEqual(edited['snapshot']['bundle_ids'], [])
        self.assertEqual(edited['snapshot']['screening'], [])

    def test_resume_and_preserve_history(self):
        t = self.ready()
        self.svc.close()
        self.svc = ResearchService(self.temp.name, fixture_client())
        self.tasks = TaskService(self.svc)
        self.assertEqual(self.tasks.get(t['task_id'])['digest'], t['digest'])
        count = self.svc.db.execute("SELECT count(*) FROM records WHERE kind='task_revision'").fetchone()[0]
        self.assertEqual(count, 4)

    def test_review_without_evidence_and_unknown_decision_rejected(self):
        t = self.tasks.create('Question', ['One'], ['Two'])
        with self.assertRaises(ValueError):
            self.tasks.request_review(t['task_id'], t['digest'])
        with self.assertRaises(ValueError):
            self.tasks.record_operator_review(t['task_id'], t['digest'], 'Test', 'approve', 'No evidence')

    def test_no_review_mutator_exposed_over_mcp(self):
        s = Server(self.svc)
        s.initialized = s.ready = True
        reply = s.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'health_record_operator_review'}})
        self.assertIn('error', reply)

    def test_atomic_revision_rejects_concurrent_writer(self):
        t = self.tasks.create('Question', ['One'], ['Two'])
        fresh = self.tasks.update(t['task_id'], t['digest'], 'New', ['One'], ['Two'])
        with self.assertRaisesRegex(ValueError, 'Concurrent'):
            self.tasks._save(t['task_id'], {**t['snapshot'], 'revision': 3}, t['digest'])
        self.assertEqual(self.tasks.get(t['task_id'])['digest'], fresh['digest'])
