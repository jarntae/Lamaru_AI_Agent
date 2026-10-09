# Lamaru AI Gateway Documentation

This directory contains the architectural and development contracts for Lamaru AI Gateway.

## Purpose

Lamaru is a local AI Gateway and Agent framework that sits between development clients such as VS Code and multiple AI providers.

The system must:

- support multiple providers;
- support multiple API keys/accounts;
- support multiple models;
- automatically route requests;
- detect rate limits and failures;
- retry and fail over safely;
- preserve Agent state during model switching;
- provide coding tools;
- provide an OpenAI-compatible local HTTP API;
- provide MCP integration;
- provide a VS Code extension;
- persist state locally with SQLite.

## Documents

- `ARCHITECTURE.md` — system boundaries and component responsibilities.
- `PROVIDERS.md` — provider adapter contract.
- `ROUTING.md` — model selection, scoring, retry and fallback.
- `AGENT.md` — Agent loop and state machine.
- `CONTEXT.md` — context collection, compression and budgeting.
- `TOOLS.md` — tool system and execution rules.
- `SECURITY.md` — secrets, permissions and command safety.
- `API.md` — local HTTP API contract.
- `DATABASE.md` — SQLite data model and persistence rules.
- `DEVELOPMENT.md` — implementation workflow and rules for AI coding agents.

## Source of truth

These documents describe intended architecture.

If implementation and documentation disagree, do not silently choose one. Update the relevant documentation after deciding the intended behavior.

## AI Agent rule

Before making architectural changes, an AI coding agent must read:

1. `ARCHITECTURE.md`
2. the document relevant to the subsystem being changed
3. `DEVELOPMENT.md`

Do not introduce a new subsystem merely because it is convenient for one feature.
