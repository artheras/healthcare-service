"""Generic synthetic workflow fixtures; no clinical thresholds or efficacy claims."""
import copy
import tempfile
import unittest
from aria_healthcare.navigation import NavigationService
from aria_healthcare.service import ResearchService
from test_research import fixture_client


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.svc = ResearchService(self.temp.name, fixture_client())
        self.svc.search('synthetic')
        source = self.svc.article('12345')
        b = self.svc.create_bundle('Fixture only', [{'text': 'A synthetic source for software tests',
            'pmid': '12345', 'source_sha256': source['content_sha256'], 'section': 0,
            'quote': '40 participants completed follow-up.', 'stance': 'uncertain'}], ['Not medical evidence'])
        self.bundle_id = b['bundle_id']
        self.nav = NavigationService(self.svc)
        self.fields = {'access': ['available', 'unavailable'], 'information': ['complete', 'incomplete']}
        self.rules = [{'id': 'review_path', 'when': {'access': 'unavailable'},
            'outcome': 'request_professional_review', 'rationale': 'Synthetic workflow condition only', 'bundle_id': self.bundle_id}]
        self.p = self.nav.create('Synthetic software policy', self.fields, self.rules, ['No clinical use'])

    def tearDown(self):
        self.svc.close()
        self.temp.cleanup()

    def simulate(self, facts, synthetic=True):
        return self.nav.simulate(self.p['policy_id'], self.p['digest'], facts, synthetic)

    def test_match_explains_source_but_never_recommends_patient_care(self):
        r = self.simulate({'access': 'unavailable', 'information': 'complete'})
        self.assertEqual(r['status'], 'simulated_match')
        self.assertEqual(r['matched_rules'][0]['bundle_id'], self.bundle_id)
        self.assertIsNone(r['patient_care_recommendation'])
        self.assertTrue(r['human_review_required'])

    def test_missing_unknown_and_real_inputs(self):
        self.assertEqual(self.simulate({'access': 'unavailable'})['reason'], 'missing_inputs')
        for facts, synthetic in [({'name': 'Alice'}, True), ({'access': 'unknown'}, True), ({}, False), ({}, 'true')]:
            with self.subTest(facts=facts, synthetic=synthetic), self.assertRaises(ValueError):
                self.simulate(facts, synthetic)

    def test_no_match_abstains(self):
        self.assertEqual(self.simulate({'access': 'available', 'information': 'complete'})['reason'], 'no_matching_rule')

    def test_conflicting_rules_abstain(self):
        rules = copy.deepcopy(self.rules)
        rules.append({**rules[0], 'id': 'conflict', 'outcome': 'information_only'})
        self.p = self.nav.create('Conflicting fixture', self.fields, rules, ['Synthetic only'])
        r = self.simulate({'access': 'unavailable', 'information': 'complete'})
        self.assertEqual(r['reason'], 'conflicting_rules')
        self.assertIsNone(r['simulated_workflow_outcome'])

    def test_policy_digest_and_source_tampering(self):
        with self.assertRaises(ValueError):
            self.nav.simulate(self.p['policy_id'], '0' * 64, {}, True)
        value = self.svc._load('navigation_policy', self.p['policy_id'])
        value['policy']['production_enabled'] = True
        self.svc._save('navigation_policy', self.p['policy_id'], value)
        with self.assertRaises(ValueError):
            self.nav.get(self.p['policy_id'])

    def test_rule_requires_existing_evidence_and_nonclinical_outcome(self):
        for change in ({'bundle_id': '0' * 32}, {'outcome': 'prescribe_drug'}, {'when': {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.nav.create('Invalid rule', self.fields, [{**self.rules[0], **change}], ['Synthetic'])

    def test_synthetic_score_is_not_clinical_accuracy(self):
        cases = [{'id': 'match', 'facts': {'access': 'unavailable', 'information': 'complete'}, 'synthetic': True,
                  'expected_status': 'simulated_match', 'expected_rule_ids': ['review_path']},
                 {'id': 'missing', 'facts': {}, 'synthetic': True,
                  'expected_status': 'abstain', 'expected_rule_ids': []}]
        report = self.nav.evaluate(self.p['policy_id'], self.p['digest'], cases)
        self.assertEqual(report['passed'], 2)
        self.assertIsNone(report['clinical_accuracy'])
        self.assertIn('synthetic_software', report['metric_scope'])
        self.assertIn(report['evaluation_id'], self.svc.list_records('navigation_evaluation')['records'])
        cases[0]['expected_status'] = 'abstain'
        report = self.nav.evaluate(self.p['policy_id'], self.p['digest'], cases)
        self.assertEqual(report['passed'], 1)

    def test_review_packet_never_claims_signoff(self):
        packet = self.nav.review_packet(self.p['policy_id'])
        self.assertIsNone(packet['clinical_signoff'])
        self.assertFalse(packet['production_enabled'])
        self.assertEqual(len(packet['review_questions']), 5)
