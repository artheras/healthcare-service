"""Loopback-only research workbench. No diagnosis, clinical rules or external uploads."""
from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .pubmed import PubMedClient
from .service import ResearchService
from .tasks import TaskService


def esc(value):
    return html.escape(str(value), quote=True)


class Workbench(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, workspace, port=0):
        self.workspace = workspace
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not log cookies, bootstrap tokens, questions or evidence.

    def send(self, code, body="", location=None, cookie=False):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        if location:
            self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", f"aria_health_session={self.server.token}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        try:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            value = c.get("aria_health_session")
            return bool(value and secrets.compare_digest(value.value, self.server.token))
        except Exception:
            return False

    def valid_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def form(self, action, label, fields=""):
        return (f'<form method="post" action="{action}"><input type="hidden" name="csrf" value="{self.server.token}">'
                + fields + f'<button>{label}</button></form>')

    def field(self, name, label, value="", hidden=False):
        if hidden:
            return f'<input type="hidden" name="{name}" value="{esc(value)}">'
        return f'<label>{label}<textarea name="{name}" required maxlength="4000">{esc(value)}</textarea></label>'

    def page(self, content):
        return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                '<title>Aria Research Workbench</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px;background:#f6f8fa;color:#182532}section,form{background:white;padding:20px;margin:16px 0;border:1px solid #ccd5df;border-radius:12px}label{display:block;margin:12px 0}textarea{display:block;width:95%;min-height:60px}button{background:#164e63;color:white;padding:12px;border:0;border-radius:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}.notice{border-left:5px solid #b65d12;padding:16px}a{color:#155e75}</style>'
                '<h1><a href="/">Aria Research Workbench</a></h1><p class="notice">Public-literature research only. All findings require professional review. Source matching is not scientific or clinical validation. Do not enter patient records. Local reviewer identity is asserted, not authenticated.</p>' + content + '</html>')

    def service(self):
        return ResearchService(self.server.workspace, PubMedClient(enabled=os.getenv("ARIA_HEALTH_ALLOW_NETWORK") == "1"))

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, "Invalid host")
        parsed = urlsplit(self.path)
        if parsed.path == "/" and parse_qs(parsed.query).get("token") == [self.server.token]:
            return self.send(303, location="/", cookie=True)
        if not self.authorized():
            return self.send(401, "Open the startup URL from the local terminal to start your session.")
        svc = self.service()
        try:
            if parsed.path == "/":
                content = '<h2>Research tasks</h2>'
                for ident in svc.list_records("task")["records"]:
                    t = TaskService(svc).get(ident)
                    content += f'<section><a href="/task?id={ident}">{esc(t["snapshot"]["question"])}</a><p>{esc(t["snapshot"]["stage"])} · revision {t["snapshot"]["revision"]}</p></section>'
                content += '<h2>Create a task</h2>' + self.form('/create', 'Create research task', self.field('question', 'Research question') + self.field('inclusion', 'Inclusion criteria, one per line') + self.field('exclusion', 'Exclusion criteria, one per line'))
                return self.send(200, self.page(content))
            if parsed.path != "/task":
                return self.send(404, "Not found")
            ident = parse_qs(parsed.query).get('id', [''])[0]
            task = TaskService(svc).get(ident)
            s = task['snapshot']
            content = f'<h2>{esc(s["question"])}</h2><p>Stage: {esc(s["stage"])} · revision {s["revision"]}</p>'
            content += '<h3>Screening criteria and decisions</h3><pre>' + esc(json.dumps({k: s[k] for k in ('inclusion', 'exclusion', 'screening')}, indent=2)) + '</pre>'
            for bundle_id in s['bundle_ids']:
                audit = svc.audit_bundle(bundle_id)
                bundle = svc.export_bundle(bundle_id)
                content += '<section><h3>Evidence bundle</h3><pre>' + esc(json.dumps(bundle, indent=2)) + '</pre></section>'
            fields = self.field('task_id', '', ident, True) + self.field('expected_digest', '', task['digest'], True)
            content += self.form('/update', 'Save changed scope and invalidate prior review', fields + self.field('question', 'Research question', s['question']) + self.field('inclusion', 'Inclusion criteria, one per line', '\n'.join(s['inclusion'])) + self.field('exclusion', 'Exclusion criteria, one per line', '\n'.join(s['exclusion'])))
            content += self.form('/screen', 'Record draft screening decision', fields + self.field('pmid', 'Retrieved PMID') + '<label>Decision<select name="decision"><option>uncertain</option><option>include</option><option>exclude</option></select></label>' + self.field('reason', 'Reason'))
            content += self.form('/attach', 'Attach evidence bundle', fields + self.field('bundle_id', 'Existing bundle ID'))
            content += self.form('/request-review', 'Request human review', fields)
            if s['stage'] == 'pending_human_review':
                content += '<h3>Record local operator review</h3><p>Review the evidence above. This records your assertion and does not authenticate professional credentials.</p>'
                content += self.form('/review', 'Record this review', fields + self.field('reviewer', 'Reviewer name') + '<label>Decision<select name="decision"><option>reject</option><option>approve</option></select></label>' + self.field('note', 'Review rationale') + self.field('confirmation', 'Type the complete task digest to confirm: ' + task['digest']))
            if s['review']:
                content += '<h3>Review record</h3><pre>' + esc(json.dumps(s['review'], indent=2)) + '</pre>'
            return self.send(200, self.page(content))
        except (ValueError, KeyError, TypeError):
            self.send(400, self.page('<p>Task or evidence unavailable. Reload the task or check its stored records.</p>'))
        finally:
            svc.close()

    def do_POST(self):
        if not self.valid_host() or not self.authorized() or self.headers.get('Origin') != self.server.origin:
            return self.send(403, 'Local session and same-origin form submission required')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 50000 or self.headers.get('Content-Type', '').split(';')[0] != 'application/x-www-form-urlencoded':
                return self.send(400, 'Invalid form')
            fields = parse_qs(self.rfile.read(length).decode(), strict_parsing=True)
            if any(len(v) != 1 for v in fields.values()):
                return self.send(400, 'Duplicate form field')
            data = {k: v[0] for k, v in fields.items()}
            if not secrets.compare_digest(data.pop('csrf', ''), self.server.token):
                return self.send(403, 'Invalid form token')
        except (ValueError, UnicodeError):
            return self.send(400, 'Invalid form')
        with self.server.lock:
            svc = self.service()
            try:
                tasks = TaskService(svc)
                if self.path == '/create':
                    t = tasks.create(data['question'], data['inclusion'].splitlines(), data['exclusion'].splitlines())
                else:
                    common = (data['task_id'], data['expected_digest'])
                    if self.path == '/update':
                        t = tasks.update(*common, data['question'], data['inclusion'].splitlines(), data['exclusion'].splitlines())
                    elif self.path == '/screen':
                        t = tasks.screen(*common, data['pmid'], data['decision'], data['reason'])
                    elif self.path == '/attach':
                        t = tasks.attach(*common, data['bundle_id'])
                    elif self.path == '/request-review':
                        t = tasks.request_review(*common)
                    elif self.path == '/review':
                        if data['confirmation'] != data['expected_digest']:
                            raise ValueError('Confirmation mismatch')
                        t = tasks.record_operator_review(*common, data['reviewer'], data['decision'], data['note'])
                    else:
                        return self.send(404, 'Not found')
                return self.send(303, location='/task?id=' + t['task_id'])
            except (ValueError, KeyError, TypeError) as e:
                message = str(e) if type(e) is ValueError else 'Missing form fields'
                return self.send(400, self.page('<p>' + esc(message) + '</p><a href="/">Reload tasks</a>'))
            finally:
                svc.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    server = Workbench(args.workspace, args.port)
    print(f'Open locally: {server.origin}/?token={server.token}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
