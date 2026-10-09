# Development Rules

## 1. Goal

This document defines how humans and AI coding agents must modify Lamaru.

## 2. Before coding

An AI coding agent must:

1. Read `ARCHITECTURE.md`.
2. Read the relevant subsystem document.
3. Inspect the existing implementation.
4. Identify affected interfaces.
5. Plan the smallest safe change.

Do not rewrite unrelated components.

## 3. Incremental implementation

Recommended order:

### Phase 1
Project skeleton and domain interfaces.

### Phase 2
Google provider.

### Phase 3
Model registry.

### Phase 4
Router.

### Phase 5
Quota, cooldown and circuit breaker.

### Phase 6
HTTP Gateway.

### Phase 7
Agent runtime.

### Phase 8
Tools.

### Phase 9
Context Manager.

### Phase 10
MCP.

### Phase 11
VS Code extension.

### Phase 12
Dashboard and advanced features.

## 4. Tests

Every feature should include tests.

Run tests after each meaningful change.

Do not continue while core tests are failing unless the failure is explicitly understood and documented.

## 5. Mocking providers

Use mock providers for router and Agent tests.

Tests must not require real provider API calls.

Real API integration tests should be separate and opt-in.

## 6. Dependency injection

Prefer dependency injection for:

- providers;
- router;
- database;
- tools;
- clocks/timers where useful.

This makes tests deterministic.

## 7. No giant files

Split responsibilities.

Avoid one `agent.py` containing:

- routing;
- provider calls;
- tool execution;
- database;
- context management.

## 8. No magic behavior

Do not hide routing decisions in random helper functions.

Important policies must be explicit and testable.

## 9. Documentation updates

If behavior changes, update the relevant document.

Examples:

- router behavior -> `ROUTING.md`
- tool permission -> `TOOLS.md` / `SECURITY.md`
- provider interface -> `PROVIDERS.md`
- database schema -> `DATABASE.md`

## 10. Git discipline & PR Workflow

Always develop features in dedicated branches — never commit directly to `main`.

### Workflow per Phase / Feature:
1. **Branch creation:** Create a dedicated branch from up-to-date `main`:
   ```bash
   git checkout main
   git pull origin main
   git checkout -b feat/phase-X-<feature-name>
   ```
2. **Focused commits:** Keep changes focused with conventional commits:
   ```text
   feat: add provider interface
   feat: add google provider
   feat: add model registry
   test: add fallback tests
   ```
3. **Verify tests:** All unit tests must pass before pushing:
   ```bash
   .\.venv\Scripts\pytest.exe -v
   ```
4. **Push branch for GitHub review:**
   ```bash
   git push origin feat/phase-X-<feature-name>
   ```
5. **PR & Merge:** The project owner reviews the code diff on GitHub and merges into `main`.

## 11. AI agent restrictions

An AI coding agent must not:

- delete the project architecture without explanation;
- replace a subsystem with a new framework without justification;
- add dependencies unnecessarily;
- hard-code API keys;
- disable security checks to make tests pass;
- silently change public API behavior.

## 12. When uncertain

If a requirement conflicts with architecture:

1. identify the conflict;
2. inspect the relevant documentation;
3. propose the smallest change;
4. update documentation if the architecture is intentionally changed.

Do not silently invent architecture.

## 13. Definition of done

A feature is done only when:

- implementation exists;
- tests exist;
- tests pass;
- error handling exists;
- security implications were considered;
- relevant documentation is updated.
