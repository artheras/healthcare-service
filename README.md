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

## Versioned research tasks

Use `health_create_task` with a question and explicit inclusion/exclusion criteria.
`health_get_task` resumes the task. Every mutation requires the latest `expected_digest`.
`health_screen_article` records a draft include/exclude/uncertain decision and reason
against a retrieved source snapshot. `health_attach_bundle` links evidence;
`health_request_review` requires all cited snapshots to have include decisions.
Changing scope clears screening and bundle links. Any mutation invalidates prior review.
Task revisions persist; concurrent changes reject stale digests atomically.

`health_doctor` checks local SQLite storage and reports Python TLS configuration without
making external requests. It does not establish online connectivity or model quality.

Review is not an MCP tool. A local operator can run:

```sh
python -I -m aria_healthcare.review --workspace ./research-workspace \
  --task-id TASK_ID --expected-digest DIGEST --reviewer NAME \
  --decision approve --note 'Review findings'
```

This requires an interactive terminal and confirmation of the complete digest.
The record states `identity_verified=false`: it records the operator's assertion,
not authenticated clinician identity, scientific validity or clinical authorization.
Hosts must enforce their own human approval and workspace access policies.

## Local research workbench

```sh
python -I -m aria_healthcare.workbench --workspace ./research-workspace
```

Open the private startup URL printed in your local terminal. The workbench binds
only to `127.0.0.1` on a randomly assigned port. It provides task creation, scope
editing, draft source screening, evidence-bundle inspection, review requests and
local operator review forms. Use the MCP tools to retrieve sources and build bundles
in the same workspace before linking them in the workbench. Network access stays
off unless the operator explicitly enables it for public queries.

A changed task scope clears evidence selections and invalidates previous review.
Review requires the complete displayed version digest. It records an operator
assertion, not authenticated professional identity or clinical authorization.
Do not enter patient records. No diagnosis or medical triage rules are included.

Sessions require a random startup token. Requests check the exact local Host;
mutations additionally require the session cookie, same Origin and a form token.
Source text is HTML-escaped, embedded scripts/frames are disallowed and pages are
not cached. The server does not log tokens or research data. Keep the startup URL
private and stop the server when finished. This is a local workbench, not a
production multi-user or internet-facing clinical application.

## Evidence-linked navigation simulations

Draft navigation rules are for software research only. There is no production
activation path and no patient-care recommendation. Every rule links to an audited
evidence bundle; quotation matching alone does not establish medical support.

- `health_create_navigation_policy(policy_json)` accepts JSON with `title`, `fields`,
  `rules`, and `limitations`. Fields map symbolic names to enumerated options.
  Each rule has `id`, `when` (field/value conditions), `outcome`, `rationale` and
  `bundle_id`. Outcomes are limited to `information_only`,
  `request_professional_review`, and `request_more_information` as simulated workflow labels.
- `health_simulate_navigation(policy_id, expected_digest, facts_json, synthetic)`
  requires `synthetic=true`. Missing fields, no matching rule or conflicting outcomes
  produce abstention. Unknown fields and values are rejected.
- `health_evaluate_navigation(policy_id, expected_digest, cases_json)` persists a
  software evaluation report. Each case has `id`, `facts`, `synthetic`,
  `expected_status` (`abstain` or `simulated_match`) and `expected_rule_ids`.
  Counts measure agreement with declared synthetic software expectations, not
  clinical accuracy, sensitivity, fairness, or efficacy.
- `health_navigation_review_packet(policy_id)` exports the draft and professional
  review questions. Clinical signoff remains unset. Local operator review cannot
  enable patient-care use.

Discover policy/evaluation IDs with `health_list_records`. The local workbench
lists policies and provides source-linked review packets and synthetic simulation
forms. Build evidence bundles using the research tools first. These generic
synthetic fixtures contain no disease thresholds or competition materials.

Before any clinical workflow could be considered, qualified professionals would
need to specify intended use, population, evidence support, exclusions, escalation
routes, independent clinical cases and validation plans. This software does not
claim that such review or validation has happened.
