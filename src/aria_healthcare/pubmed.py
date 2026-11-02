"""Bounded, read-only NCBI E-utilities client. No arbitrary URLs or full-text scraping."""
from __future__ import annotations

import hashlib
import json
import re
import os
import ssl
import sys
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
MAX_RESPONSE = 4_000_000


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def pmid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,8}", value):
        raise ValueError("Expected a numeric PMID with 1..9 digits")
    return value


def bounded_text(value, label, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"Invalid {label}")
    return value.strip()


def xml_root(data):
    if not isinstance(data, bytes) or len(data) > MAX_RESPONSE:
        raise ValueError("Invalid source response size")
    # PubMed emits an external DOCTYPE declaration. ElementTree does not fetch it.
    # Internal entity declarations/subsets are unnecessary and forbidden.
    if re.search(br"<!ENTITY|<!DOCTYPE[^>]*\[", data, re.I):
        raise ValueError("XML entity declarations are not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("Invalid PubMed XML") from exc
    if root.find(".//ERROR") is not None:
        raise ValueError("PubMed returned an error")
    return root


def plain(node):
    return "" if node is None else "".join(node.itertext()).strip()


class PubMedClient:
    def __init__(self, *, enabled=False, email="", opener=None, sleep=time.sleep, clock=time.monotonic):
        self.enabled, self.email = enabled, email
        ca = None
        if (sys.platform == "darwin" and not os.getenv("SSL_CERT_FILE")
                and not os.getenv("SSL_CERT_DIR") and ssl.get_default_verify_paths().cafile is None
                and Path("/etc/ssl/cert.pem").is_file()):
            ca = "/etc/ssl/cert.pem"
        self.opener = opener or urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)))
        self.sleep, self.clock = sleep, clock
        self.last_request = None
        self.lock = threading.Lock()

    def _get(self, endpoint, params):
        if not self.enabled:
            raise ValueError("Public search is disabled; set ARIA_HEALTH_ALLOW_NETWORK=1 locally")
        params = {**params, "db": "pubmed", "tool": "aria_healthcare"}
        if self.email:
            params["email"] = self.email
        url = BASE + endpoint + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "AriaHealthcare/0.1.0", "Accept": "application/xml"})
        with self.lock:
            for attempt in range(3):
                if self.last_request is not None:
                    self.sleep(max(0, 0.4 - (self.clock() - self.last_request)))
                self.last_request = self.clock()
                try:
                    with self.opener.open(request, timeout=15) as response:
                        if response.geturl() != url:
                            raise ValueError("Unexpected source redirect")
                        data = response.read(MAX_RESPONSE + 1)
                        if len(data) > MAX_RESPONSE:
                            raise ValueError("Source response exceeds size limit")
                        return data
                except urllib.error.HTTPError as exc:
                    retry = exc.code in {429, 500, 502, 503, 504}
                    delay = exc.headers.get("Retry-After", "") if exc.headers else ""
                    exc.close()
                    if not retry or attempt == 2:
                        raise ValueError(f"PubMed HTTP {exc.code}; no result was fabricated") from None
                    self.sleep(min(30, max(1, int(delay))) if delay.isdigit() else 2 ** attempt)
                except (urllib.error.URLError, TimeoutError, OSError):
                    if attempt == 2:
                        raise ValueError("PubMed network unavailable; retry later") from None
                    self.sleep(2 ** attempt)

    def search(self, query, limit=10):
        query = bounded_text(query, "public literature query", 1000)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit must be 1..20")
        root = xml_root(self._get("esearch.fcgi", {"term": query, "retmax": limit, "retmode": "xml", "sort": "pub date"}))
        ids = [pmid(plain(n)) for n in root.findall("./IdList/Id")]
        return {"query": query, "pmids": ids, "total": int(root.findtext("Count", "0")),
                "query_translation": root.findtext("QueryTranslation", ""),
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "source": "PubMed",
                "scope": "search_results_only", "exhaustive": False}

    def fetch(self, identifier):
        identifier = pmid(identifier)
        root = xml_root(self._get("efetch.fcgi", {"id": identifier, "retmode": "xml", "rettype": "abstract"}))
        articles = root.findall("./PubmedArticle")
        if len(articles) != 1 or articles[0].findtext("./MedlineCitation/PMID") != identifier:
            raise ValueError("PubMed did not return the requested article")
        node = articles[0]
        article = node.find("./MedlineCitation/Article")
        if article is None:
            raise ValueError("Article metadata unavailable")
        sections = [{"label": n.get("Label", ""), "text": plain(n)} for n in article.findall("./Abstract/AbstractText")]
        identifiers = {n.get("IdType"): plain(n) for n in node.findall("./PubmedData/ArticleIdList/ArticleId")}
        content = {"pmid": identifier, "title": plain(article.find("ArticleTitle")),
                   "journal": article.findtext("./Journal/Title", ""),
                   "publication_date": plain(article.find("./Journal/JournalIssue/PubDate")),
                   "doi": identifiers.get("doi"), "pmcid": identifiers.get("pmc"),
                   "publication_types": [plain(n) for n in article.findall("./PublicationTypeList/PublicationType")],
                   "corrections": [{"type": n.get("RefType"), "pmid": n.findtext("PMID")}
                                   for n in node.findall("./MedlineCitation/CommentsCorrectionsList/CommentsCorrections")],
                   "abstract_sections": sections, "scope": "abstract_only", "full_text_fetched": False,
                   "url": f"https://pubmed.ncbi.nlm.nih.gov/{identifier}/"}
        if not content["title"]:
            raise ValueError("Article title is missing")
        return {"content": content, "content_sha256": digest(content),
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "provider": "NCBI PubMed"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
