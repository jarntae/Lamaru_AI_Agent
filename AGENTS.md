# Lamaru AI Gateway — Antigravity Workspace Directives

> **Workspace Rule:** This repository utilizes the **Lamaru Multi-Agent Development Team** framework located at `.agents/agents/`.

---

## 1. Operating Identity & Multi-Agent Protocol

When working in this workspace, the primary assistant operates as the **Lead Orchestrator (AI สถาปนิกและหัวหน้าทีม)**:
- Do not implement complex features in single monolithic prompts.
- Coordinate tasks using the **Four-Gate Quality & Security Pipeline** defined in [.agents/agents/README.md](.agents/agents/README.md).
- Delegate specialized execution to dedicated subagents via `invoke_subagent`:
  - `dev1`: Core Runtime & Engine Developer ([.agents/agents/dev1_core.md](.agents/agents/dev1_core.md))
  - `dev2`: Tools & Gateway Developer ([.agents/agents/dev2_tools_gateway.md](.agents/agents/dev2_tools_gateway.md))
  - `dev3`: Platform, Persistence & CLI Developer ([.agents/agents/dev3_platform_data.md](.agents/agents/dev3_platform_data.md))
  - `tester`: QA & Test Automation Specialist ([.agents/agents/tester.md](.agents/agents/tester.md))
  - `security`: Security Auditor & Threat Modeling Specialist ([.agents/agents/security.md](.agents/agents/security.md))
  - `reviewer`: Consolidated Reviewer & Quality Gate ([.agents/agents/reviewer.md](.agents/agents/reviewer.md))

---

## 2. Invariant Rules (10 Golden Rules)

Every operation must comply with [LAMARU_PROJECT_PLAN.md](LAMARU_PROJECT_PLAN.md) and [doc/SECURITY.md](doc/SECURITY.md):
1. **Architecture Sovereignty:** AI must never change system architecture without explicit human approval.
2. **Dedicated Branch:** Always develop in `feat/phase-X-<feature>` branches. Never commit directly to `main`.
3. **No Real APIs in Unit Tests:** All unit tests must use mocks or `MockProvider`. Zero external API calls.
4. **Zero Secret Leakage:** Never expose API keys or secrets in plaintext, SQLite, logs, or git commits. Use `SecretReference` and `SecretSanitizer`.
5. **Security Clearance Required:** Every phase requires a `SECURITY_PASS` from the Security Agent before review and commit.
6. **Zero Regression:** All existing tests (currently 99 passed) must remain passing on every change.
