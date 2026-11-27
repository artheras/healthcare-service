"""Tests for aria-healthcare Streamable HTTP MCP transport."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import threading
import urllib.request

from aria_healthcare.pubmed import PubMedClient
from aria_healthcare.server import Server, build_arg_parser, make_http_handler
from aria_healthcare.service import ResearchService


def test_arg_parser_defaults_and_http_flags():
    d = build_arg_parser().parse_args([])
    assert d.transport == "stdio"
    h = build_arg_parser().parse_args(["--transport", "streamable-http", "--port", "9002"])
    assert h.transport == "streamable-http"
    assert h.port == 9002


def test_streamable_http_health_and_tools_list(tmp_path):
    svc = ResearchService(tmp_path, PubMedClient(enabled=False))
    mcp_srv = Server(svc, transport="streamable-http")
    http_srv = ThreadingHTTPServer(("127.0.0.1", 0), make_http_handler(mcp_srv))
    port = http_srv.server_address[1]
    t = threading.Thread(target=http_srv.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as r:
            assert r.status == 200
            health = json.loads(r.read().decode())
            assert health["status"] == "ok"
            assert health["transport"] == "streamable-http"

        def _rpc(payload):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/mcp",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                raw = r.read().decode()
                return r.status, json.loads(raw) if raw else None

        status, init_body = _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
        assert status == 200 and "result" in init_body
        assert "skills" in init_body["result"]["capabilities"]
        assert "prompts" in init_body["result"]["capabilities"]
        _rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        status, body = _rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert status == 200
        names = {tool["name"] for tool in body["result"]["tools"]}
        assert {"health_doctor", "health_search_pubmed", "health_create_bundle"}.issubset(names)

        status, skills_body = _rpc({"jsonrpc": "2.0", "id": 3, "method": "skills/list", "params": {}})
        assert status == 200
        skill_names = {s["name"] for s in skills_body["result"]["skills"]}
        assert "biomedical-evidence-research" in skill_names

        status, skill_detail = _rpc({"jsonrpc": "2.0", "id": 4, "method": "skills/get", "params": {"name": "biomedical-evidence-research"}})
        assert status == 200
        assert "Biomedical Evidence" in skill_detail["result"]["content"]

        status, prompts_body = _rpc({"jsonrpc": "2.0", "id": 5, "method": "prompts/list", "params": {}})
        assert status == 200
        assert any(p["name"] == "biomedical-evidence-research" for p in prompts_body["result"]["prompts"])

        status, prompt_detail = _rpc({"jsonrpc": "2.0", "id": 6, "method": "prompts/get", "params": {"name": "biomedical-evidence-research"}})
        assert status == 200
        assert "Biomedical Evidence" in prompt_detail["result"]["messages"][0]["content"]["text"]
    finally:
        http_srv.shutdown()
        http_srv.server_close()
        svc.close()
