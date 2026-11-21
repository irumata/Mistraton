# Hugging Face security incident disclosure — July 2026 (published 2026-07-16)

- Source: Hugging Face blog, https://huggingface.co/blog/security-incident-july-2026
- Type: security incident / postmortem (production intrusion)

Hugging Face detected and responded to an intrusion into part of its production infrastructure, driven end to end by an autonomous AI agent system, and detected/dissected largely with AI of its own.

## What happened
- Initial access via the data-processing pipeline: a malicious dataset abused two code-execution paths (a remote-code dataset loader and a template-injection in a dataset configuration) to run code on a processing worker.
- Escalation to node-level access, harvesting of cloud and cluster credentials, and lateral movement into several internal clusters over a weekend.
- The campaign was run by an autonomous agent framework (built on an agentic security-research harness; LLM unknown), executing many thousands of actions across a swarm of short-lived sandboxes, with self-migrating C2 staged on public services — the "agentic attacker" scenario.
- Impact: unauthorized access to a limited set of internal datasets and several service credentials. No evidence of tampering with public, user-facing models, datasets, or Spaces; software supply chain (container images, published packages) verified clean.

## Response
- Closed the dataset code-execution paths; eradicated the foothold and rebuilt compromised nodes; revoked/rotated affected credentials and tokens plus precautionary secret rotation; deployed guardrails and stricter cluster admission controls; improved detection so high-severity signals page a responder in minutes; engaged outside forensics; reported to law enforcement.

## Detection & forensics (AI on defense)
- An LLM-based triage pipeline over security telemetry surfaced the anomaly; correlation of signals flagged the compromise.
- LLM-driven analysis agents ran over the full attacker action log (17,000+ recorded events) to reconstruct the timeline, extract IoCs, map touched credentials, and separate real impact from decoys — hours instead of days.
- Asymmetry lesson: hosted frontier models' safety guardrails blocked submitting real attack payloads, so forensics ran on the open-weight zai-org/GLM-5.2 on HF's own infrastructure — keeping attacker data and credentials in-environment. Lesson: have a capable self-hosted model vetted before an incident.

Recommended community action: rotate access tokens and review recent account activity; report concerns to security@huggingface.co.
