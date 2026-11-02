"""Offline acceptance tests. XML and findings below are deliberately synthetic."""
import copy
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error

from aria_healthcare.pubmed import PubMedClient, MAX_RESPONSE, digest, xml_root
from aria_healthcare.service import ResearchService
from aria_healthcare.server import Server

SEARCH = b'<eSearchResult><Count>1</Count><IdList><Id>12345</Id></IdList><QueryTranslation>synthetic trial</QueryTranslation></eSearchResult>'
ARTICLE = b'''<?xml version="1.0"?>
<!DOCTYPE PubmedArticleSet SYSTEM "https://example.invalid/never-fetch.dtd">
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345</PMID><Article>
<ArticleTitle>Synthetic <i>fixture</i> only</ArticleTitle><Journal><Title>Fixture Journal</Title>
<JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>
<Abstract><AbstractText Label="RESULTS">In this synthetic fixture, 40 participants completed follow-up.</AbstractText>
<AbstractText Label="CONCLUSIONS">This fixture does not establish clinical efficacy.</AbstractText></Abstract>
<PublicationTypeList><PublicationType>Retracted Publication</PublicationType></PublicationTypeList>
</Article><CommentsCorrectionsList><CommentsCorrections RefType="RetractionIn"><PMID>54321</PMID></CommentsCorrections></CommentsCorrectionsList>
</MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.0000/fixture</ArticleId>
<ArticleId IdType="pmc">PMC12345</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>'''


class Response(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        value = self.items.pop(0)
        if isinstance(value, Exception):
            raise value
        return Response(value, request.full_url)


def fixture_client(items=None):
    return PubMedClient(enabled=True, opener=Opener(items or [SEARCH, ARTICLE]), sleep=lambda _: None)


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = ResearchService(self.temp.name, fixture_client())

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def prepare(self):
        search = self.service.search("synthetic trial")
        source = self.service.article("12345")
        claim = {"text": "A synthetic follow-up finding, not medical evidence.", "pmid": "12345",
                 "source_sha256": source["content_sha256"], "section": 0,
                 "quote": "40 participants completed follow-up.", "stance": "uncertain"}
        return search, source, claim

    def test_end_to_end_research_survives_restart(self):
        search, source, claim = self.prepare()
        self.assertEqual(source["content"]["title"], "Synthetic fixture only")
        self.assertEqual(source["content"]["corrections"][0]["type"], "RetractionIn")
        bundle = self.service.create_bundle("Synthetic question", [claim], ["Fixture only"], search["search_id"])
        self.service.close()
        self.service = ResearchService(self.temp.name, PubMedClient())
        cached = self.service.article("12345")
        self.assertTrue(cached["cached"])
        exported = self.service.export_bundle(bundle["bundle_id"])
        self.assertIn("40 participants", exported["evidence_csv"])
        self.assertIsNone(exported["audit"]["scientifically_verified"])
        self.assertFalse(exported["audit"]["semantic_entailment_verified"])
        self.assertEqual(exported["review_status"], "pending_human_review")
        self.assertEqual(self.service.list_records()["records"], [bundle["bundle_id"]])

    def test_hallucinated_or_mislocated_quote_fails(self):
        _, _, claim = self.prepare()
        for update in ({"quote": "400 participants completed follow-up."}, {"section": 1}, {"pmid": "54321"},
                       {"source_sha256": "0" * 64}, {"section": True}, {"stance": "proven"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.service.create_bundle("Question", [{**claim, **update}], ["Abstract only"])

    def test_unsupported_or_approval_fields_rejected(self):
        _, _, claim = self.prepare()
        with self.assertRaises(ValueError):
            self.service.create_bundle("Question", [{**claim, "approved": True}], ["Abstract only"])
        with self.assertRaises(ValueError):
            self.service.create_bundle("Question", [claim], [])

    def test_quote_match_does_not_certify_inference(self):
        _, _, claim = self.prepare()
        claim["text"] = "An intentionally unsupported interpretation requiring reviewer rejection."
        bundle = self.service.create_bundle("Question", [claim], ["Quote matching cannot verify this inference"])
        self.assertTrue(bundle["audit"]["citation_quote_matches"])
        self.assertTrue(bundle["audit"]["human_review_required"])
        self.assertIsNone(bundle["audit"]["scientifically_verified"])

    def test_source_tampering_is_detected(self):
        _, source, _ = self.prepare()
        altered = copy.deepcopy(source)
        altered["content"]["title"] = "Changed after retrieval"
        self.service._save("source", source["content_sha256"], altered)
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.service.article("12345")

    def test_manifest_tampering_is_detected(self):
        _, _, claim = self.prepare()
        bundle = self.service.create_bundle("Question", [claim], ["Abstract only"])
        body = self.service._load("bundle", bundle["bundle_id"])
        body["manifest"]["question"] = "Modified after draft"
        self.service._save("bundle", bundle["bundle_id"], body)
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.service.audit_bundle(bundle["bundle_id"])

    def test_csv_formula_escaping(self):
        _, _, claim = self.prepare()
        claim["text"] = "  =HYPERLINK(\"https://example.invalid\")"
        bundle = self.service.create_bundle("Question", [claim], ["Fixture"])
        rows = list(csv.reader(io.StringIO(self.service.export_bundle(bundle["bundle_id"])["evidence_csv"])))
        self.assertTrue(rows[1][0].startswith("'=HYPERLINK"))

    def test_record_path_traversal_and_bad_ids(self):
        for identifier in ("../secrets", "0", "https://example.com", 12345, "000123"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.service.article(identifier)
        with self.assertRaises(ValueError):
            self.service.export_bundle("../secrets")

    def test_missing_abstract_does_not_get_invented(self):
        self.service.client = fixture_client([ARTICLE.replace(b'<Abstract>', b'<Other>').replace(b'</Abstract>', b'</Other>')])
        source = self.service.article("12345")
        self.assertEqual(source["content"]["abstract_sections"], [])


class NetworkTests(unittest.TestCase):
    def test_disabled_network_makes_no_requests(self):
        opener = Opener([])
        client = PubMedClient(opener=opener)
        with self.assertRaisesRegex(ValueError, "disabled"):
            client.search("public topic")
        self.assertEqual(opener.calls, [])

    def test_transient_failure_retries_and_fixed_source_only(self):
        error = urllib.error.HTTPError("https://redacted", 429, "limited", {"Retry-After": "1"}, None)
        opener = Opener([error, SEARCH])
        delays = []
        client = PubMedClient(enabled=True, opener=opener, sleep=delays.append)
        self.assertEqual(client.search("synthetic & public")["pmids"], ["12345"])
        self.assertEqual(len(opener.calls), 2)
        self.assertTrue(all(url.startswith("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?") for url, _ in opener.calls))
        self.assertIn(1, delays)

    def test_auth_failure_not_retried(self):
        opener = Opener([urllib.error.HTTPError("https://redacted", 403, "denied", {}, None)])
        with self.assertRaisesRegex(ValueError, "HTTP 403"):
            PubMedClient(enabled=True, opener=opener).search("synthetic")
        self.assertEqual(len(opener.calls), 1)

    def test_oversized_and_malicious_xml_rejected(self):
        for data in (b"a" * (MAX_RESPONSE + 1), b'<!DOCTYPE x [<!ENTITY y "x">]><x>&y;</x>', b'<eSearchResult><ERROR>bad</ERROR></eSearchResult>', b'<invalid'):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                xml_root(data)

    def test_wrong_article_rejected(self):
        with self.assertRaisesRegex(ValueError, "requested"):
            fixture_client([ARTICLE]).fetch("54321")


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = ResearchService(self.temp.name, fixture_client())
        self.server = Server(self.service)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def call(self, method, params=None, ident=1):
        return self.server.dispatch({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}})

    def initialize(self):
        self.call("initialize", {"protocolVersion": "2024-11-05"})
        self.server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def test_lifecycle_and_no_model_approval_tool(self):
        self.assertIn("error", self.call("tools/list"))
        self.initialize()
        tools = self.call("tools/list")["result"]["tools"]
        self.assertEqual(len(tools), 18)
        self.assertFalse(any("approve" in t["name"] for t in tools))
        self.assertIn("error", self.call("tools/call", {"name": "health_approve_bundle"}))

    def test_invalid_arguments_and_redacted_log(self):
        self.initialize()
        for arguments in ({"query": "public", "limit": True}, {"query": "public", "url": "https://evil.invalid"}):
            result = self.call("tools/call", {"name": "health_search_pubmed", "arguments": arguments})
            self.assertTrue(result["result"]["isError"])
        rows = self.service.db.execute("SELECT * FROM events").fetchall()
        self.assertNotIn("evil", str(rows))

    def test_real_stdio_process_is_offline_by_default_and_recovers_after_bad_input(self):
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        env.pop("ARIA_HEALTH_ALLOW_NETWORK", None)
        messages = ['{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
                    '{"jsonrpc":"2.0","method":"notifications/initialized"}',
                    '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"health_capabilities"}}',
                    '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"health_search_pubmed","arguments":{"query":"public"}}}',
                    '{"id":4,"id":5}',
                    '{"jsonrpc":"2.0","id":6,"method":"ping"}']
        p = subprocess.run([sys.executable, "-m", "aria_healthcare.server", "--workspace", self.temp.name + "/child"],
                           input="\n".join(messages) + "\n", text=True, capture_output=True, env=env, timeout=10, check=True)
        replies = [json.loads(line) for line in p.stdout.splitlines()]
        capabilities = json.loads(replies[1]["result"]["content"][0]["text"])
        self.assertFalse(capabilities["network_enabled"])
        self.assertTrue(replies[2]["result"]["isError"])
        self.assertEqual(replies[3]["error"]["code"], -32700)
        self.assertEqual(replies[4]["id"], 6)
        self.assertEqual(p.stderr, "")


if __name__ == "__main__":
    unittest.main()
