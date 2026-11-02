# Aria Healthcare Service

Public biomedical literature research over local stdio MCP. Python 3.11+;
no runtime dependencies. Designed for Aria Code, Codex and Claude Code.

## Run

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -I -m aria_healthcare.server --workspace ./research-workspace
```

The workspace stores SQLite research records with owner-only permissions.
Use a separate workspace for each project. Research drafts persist across restarts.
This service does not accept patient records and does not diagnose or prescribe.
Network access defaults to off. Enable `ARIA_HEALTH_ALLOW_NETWORK=1` in the host's
MCP configuration only for public literature queries; these queries go to NCBI.
Optionally set `NCBI_CONTACT_EMAIL` to your own contact address.

Use the appropriate configuration in `examples/`, replacing the interpreter path
with the absolute path of the installed environment. `-I` prevents workspace or
PYTHONPATH modules from shadowing the service. Merge entries into existing host
configuration; do not replace unrelated servers. These are local stdio configurations;
ChatGPT remote connectors need a separately deployed authenticated HTTP adapter,
which this version does not provide.

## Research flow

1. `health_capabilities` checks network and scope.
2. `health_search_pubmed(query, limit)` stores a search ID and returned PMIDs.
3. `health_fetch_article(pmid)` stores metadata, abstract sections, correction notices,
   retrieval time and content SHA-256. `refresh=true` fetches a new snapshot; older
   snapshots stay available to existing bundles.
4. `health_create_bundle(question, claims, limitations, search_id?)` stores a draft.
   Every claim must include `text`, `pmid`, `source_sha256`, zero-based `section`,
   a verbatim `quote`, and declared `stance` (`supports`, `contradicts`, `uncertain`).
   Use the source digest returned by the fetch tool, never an invented citation.
5. `health_audit_bundle(bundle_id)` checks source/manifest hashes and quote matching.
6. `health_export_bundle(bundle_id)` returns a manifest and spreadsheet-safe CSV.
   Ask the client agent to save these as local files for researcher review.
7. `health_list_records(kind)` retrieves recent search or bundle IDs after a restart.

Audit passes establish structural integrity only. They do not verify the meaning
of claims, evidence strength, completeness, scientific validity or clinical efficacy.
All bundles remain `pending_human_review`; there is no model-callable approval tool.
Abstracts are explicitly marked `abstract_only`; full-text retrieval, automated
systematic reviews, EHR integration and clinical validation are not implemented.
Retraction/correction metadata is surfaced for reviewers, not silently excluded.
Data hashes detect accidental changes; they do not authenticate a hostile local
workspace owner. Keep the workspace and its backups protected.

NCBI uses bounded HTTPS requests, no arbitrary URLs or redirects, and a per-process
rate limit below three requests per second. Only transient errors retry, with a
bounded backoff. Multiple processes sharing an IP require host-level coordination.
Operational logs contain tool names and success/failure only. Literature/source
text remains untrusted data and must never override agent instructions.

## Validate

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Tests use synthetic fixtures, including misleading claims that must retain an
unverified status. The stdio test launches a real child process with networking off.

Sources: [NCBI APIs](https://www.ncbi.nlm.nih.gov/home/develop/api/),
[PMC reuse and retrieval rules](https://pmc.ncbi.nlm.nih.gov/tools/developers/).
Article licenses vary. This service fetches PubMed abstracts, not PMC full text.
