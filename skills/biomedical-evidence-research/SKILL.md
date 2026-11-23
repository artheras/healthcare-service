---
name: biomedical-evidence-research
description: >
  Public biomedical literature search, verifiable evidence bundle synthesis,
  and audit-gated research workflows via aria-healthcare MCP.
---

# Biomedical Evidence & Literature Research SOP

Use this workflow when synthesizing public biomedical literature (PubMed) into
verifiable evidence bundles. This service does not accept patient records and
does not diagnose or prescribe.

## Non-Negotiable Evidence & Safety Rules

1. **Public literature only**: Never pass PHI or patient-identifying records.
2. **Exact provenance**: Every claim in an evidence bundle must reference its
   exact `pmid`, `source_sha256`, `section`, `quote`, and `stance`.
3. **Audit gate**: Run `health_audit_bundle` before exporting any bundle.

## Standard Tool Sequence

1. **Doctor & Capabilities**: Call `health_doctor` to verify storage and network
   policy (`ARIA_HEALTH_ALLOW_NETWORK`).
2. **Literature Search & Fetch**: Call `health_search_pubmed` and
   `health_fetch_article` to retrieve public abstracts and hashes.
3. **Bundle & Audit**: Call `health_create_bundle`, `health_audit_bundle`, and
   `health_export_bundle` to produce a verifiable evidence artifact.
