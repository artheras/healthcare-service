"""Evidence-linked service-navigation simulations, never patient-specific triage."""
from __future__ import annotations

import re
import uuid
from .pubmed import bounded_text, canonical, digest
from .service import now, record_id

OUTCOMES = {"information_only", "request_professional_review", "request_more_information"}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", value):
        raise ValueError("Expected a short symbolic identifier")
    return value


class NavigationService:
    def __init__(self, research):
        self.research = research

    def create(self, title, fields, rules, limitations):
        title = bounded_text(title, "policy title")
        if not isinstance(fields, dict) or not 1 <= len(fields) <= 20:
            raise ValueError("Expected 1..20 enumerated fields")
        for key, options in fields.items():
            identifier(key)
            if not isinstance(options, list) or not 2 <= len(options) <= 12:
                raise ValueError("Each field needs 2..12 symbolic options")
            for option in options:
                identifier(option)
            if len(set(options)) != len(options):
                raise ValueError("Duplicate option")
        if not isinstance(rules, list) or not 1 <= len(rules) <= 30:
            raise ValueError("Expected 1..30 rules")
        if not isinstance(limitations, list) or not 1 <= len(limitations) <= 20:
            raise ValueError("Explicit policy limitations are required")
        limitations = [bounded_text(x, "policy limitation") for x in limitations]
        seen = set()
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) != {"id", "when", "outcome", "rationale", "bundle_id"}:
                raise ValueError("Invalid rule fields")
            ident = identifier(rule["id"])
            if ident in seen:
                raise ValueError("Duplicate rule ID")
            seen.add(ident)
            self._facts(rule["when"], fields, partial=True)
            if not rule["when"] or rule["outcome"] not in OUTCOMES:
                raise ValueError("Rule requires conditions and a nonclinical workflow outcome")
            bounded_text(rule["rationale"], "rule rationale")
            self.research.audit_bundle(record_id(rule["bundle_id"]))
        policy = {"version": 1, "title": title, "fields": fields, "rules": rules,
                  "limitations": limitations, "created_at": now(), "simulation_only": True,
                  "clinical_review": "not_established", "production_enabled": False}
        value = {"policy": policy, "digest": digest(policy)}
        policy_id = uuid.uuid4().hex
        self.research._save("navigation_policy", policy_id, value)
        return {"policy_id": policy_id, **value}

    @staticmethod
    def _facts(facts, fields, partial=False):
        if not isinstance(facts, dict) or set(facts) - set(fields):
            raise ValueError("Unknown input fields; no free-text or patient records accepted")
        for key, value in facts.items():
            if not isinstance(value, str) or value not in fields[key]:
                raise ValueError("Input value is outside the declared enumeration")
        return sorted(set(fields) - set(facts))

    def get(self, policy_id):
        value = self.research._load("navigation_policy", record_id(policy_id))
        p = value["policy"]
        if digest(p) != value["digest"] or p.get("simulation_only") is not True or p.get("production_enabled") is not False:
            raise ValueError("Policy integrity or simulation boundary mismatch")
        for rule in p["rules"]:
            self.research.audit_bundle(rule["bundle_id"])
        return value

    def simulate(self, policy_id, expected_digest, facts, synthetic):
        if synthetic is not True:
            raise ValueError("Only explicitly synthetic simulations are supported")
        value = self.get(policy_id)
        if value["digest"] != expected_digest:
            raise ValueError("Policy changed; reload before simulation")
        p = value["policy"]
        missing = self._facts(facts, p["fields"])
        matched = [] if missing else [r for r in p["rules"] if all(facts[k] == v for k, v in r["when"].items())]
        outcomes = {r["outcome"] for r in matched}
        reason = "missing_inputs" if missing else "conflicting_rules" if len(outcomes) > 1 else "no_matching_rule" if not matched else "matched"
        result = {"policy_id": policy_id, "policy_digest": value["digest"],
                  "simulation_only": True, "status": "simulated_match" if reason == "matched" else "abstain",
                  "reason": reason, "missing_fields": missing,
                  "matched_rules": [{"id": r["id"], "rationale": r["rationale"], "bundle_id": r["bundle_id"]} for r in matched],
                  "simulated_workflow_outcome": next(iter(outcomes)) if reason == "matched" else None,
                  "human_review_required": True, "clinical_validation": "not_established",
                  "patient_care_recommendation": None}
        return result

    def evaluate(self, policy_id, expected_digest, cases):
        if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
            raise ValueError("Expected 1..100 synthetic test cases")
        self.get(policy_id)
        rows = []
        seen = set()
        for case in cases:
            if not isinstance(case, dict) or set(case) != {"id", "facts", "synthetic", "expected_status", "expected_rule_ids"}:
                raise ValueError("Invalid synthetic test case")
            ident = identifier(case["id"])
            if ident in seen:
                raise ValueError("Duplicate case ID")
            seen.add(ident)
            if case["expected_status"] not in {"abstain", "simulated_match"} or not isinstance(case["expected_rule_ids"], list):
                raise ValueError("Invalid expected software result")
            for rule_id in case["expected_rule_ids"]:
                identifier(rule_id)
            result = self.simulate(policy_id, expected_digest, case["facts"], case["synthetic"])
            actual_ids = sorted(r["id"] for r in result["matched_rules"])
            rows.append({"case_id": ident, "passed": result["status"] == case["expected_status"] and actual_ids == sorted(case["expected_rule_ids"]),
                         "result": result})
        report = {"policy_id": policy_id, "policy_digest": expected_digest, "cases_digest": digest(cases),
                  "total": len(rows), "passed": sum(r["passed"] for r in rows), "results": rows,
                  "metric_scope": "agreement_with_declared_synthetic_software_expectations_only",
                  "clinical_accuracy": None, "clinical_validation": "not_established", "simulation_only": True}
        evaluation_id = uuid.uuid4().hex
        self.research._save("navigation_evaluation", evaluation_id, report)
        return {"evaluation_id": evaluation_id, **report}

    def review_packet(self, policy_id):
        value = self.get(policy_id)
        return {"policy_id": policy_id, **value, "human_review_required": True,
                "review_questions": ["Does each cited source support the exact condition and proposed workflow?",
                    "Who is the intended user, population and service setting?", "Which missing inputs or exclusions require abstention?",
                    "Which red flags, escalation routes and local services require a licensed clinician's specification?",
                    "What independent clinical cases, subgroup checks and prospective validation are required?"],
                "clinical_signoff": None, "production_enabled": False,
                "notice": "This packet is for professional review. Software test success or local operator approval cannot enable patient-care use."}
