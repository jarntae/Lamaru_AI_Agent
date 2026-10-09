# Lamaru AI Gateway — Architecture

## 1. Mission

Lamaru is a local AI Gateway and Agent runtime.

Its primary responsibility is to separate:

- Agent behavior
- context management
- model selection
- provider communication
- tool execution
- persistence
- security

The architecture must remain provider-agnostic.

## 2. High-level architecture

```text
VS Code Extension / CLI / External Client
                 |
                 v
          Local HTTP Gateway
                 |
       +---------+---------+
       |                   |
       v                   v
 Agent Orchestrator   OpenAI-Compatible API
       |
       +--> Context Manager
       |
       +--> Model Router
       |
       +--> Tool Manager
       |
       +--> Agent State
       |
       v
 Provider Manager
       |
 +-----+---------+---------+
 |               |         |
Google         Future     Local
Provider       Providers  Models
       |
       v
     SQLite
```

## 3. Core principle

The model is a replaceable worker.

The Agent owns the task state.

Never make conversation or Agent state belong to a specific model.

If model A fails:

```text
AgentState -> Router -> Model B
```

Model B must continue from the same state.

## 4. Component responsibilities

### Agent Orchestrator

Responsible for:

- task execution;
- planning;
- tool-call loop;
- iteration limits;
- state transitions;
- completion and failure.

It must not contain provider-specific code.

### Context Manager

Responsible for:

- finding relevant files;
- collecting relevant context;
- context prioritization;
- context budgeting;
- summarization;
- compression.

It must not decide which provider to use.

### Model Router

Responsible for:

- candidate selection;
- capability matching;
- scoring;
- retry;
- fallback;
- model/key cooldown awareness.

It must not directly implement provider-specific HTTP calls.

### Provider Manager

Responsible for:

- provider registration;
- provider health;
- provider adapter lifecycle;
- access to provider implementations.

### Quota Manager

Responsible for:

- observed request usage;
- rate-limit events;
- cooldown;
- circuit breaker state;
- per-key and per-model health.

It must distinguish observed values from estimates.

### Tool Manager

Responsible for:

- tool registration;
- tool schemas;
- permission checks;
- tool execution;
- structured tool results.

### Persistence

SQLite stores durable local state.

## 5. Dependency direction

Preferred direction:

```text
Application
   |
   v
Core
   |
   +--> Interfaces
   |
   +--> Domain types
   |
   v
Adapters
   |
   +--> Google
   +--> Local
   +--> HTTP
   +--> MCP
```

Core must not import Google-specific implementation details.

## 6. Project boundaries

Suggested structure:

```text
apps/
  gateway/
  cli/
  vscode-extension/

core/
  agent/
  router/
  providers/
  quota/
  context/
  tools/
  security/

providers/
  google/
  local/

mcp/

database/

config/

tests/

doc/
```

Exact directories may change if there is a strong architectural reason, but responsibilities must remain separated.

## 7. Request lifecycle

```text
Request
  |
  v
Validate
  |
  v
Classify task
  |
  v
Build context
  |
  v
Find model candidates
  |
  v
Score candidates
  |
  v
Execute request
  |
  +--> success -> return
  |
  +--> failure -> classify error
                    |
                    +--> retry
                    +--> switch key
                    +--> switch model
                    +--> switch provider
                    +--> fail
```

## 8. Non-goals

The first version does not need:

- distributed deployment;
- Kubernetes;
- cloud databases;
- multi-user authentication;
- remote public hosting.

This is primarily a local developer tool.

## 9. Architectural invariants

1. Provider-specific logic stays inside provider adapters.
2. Agent state survives model switching.
3. Secrets never enter logs.
4. Router does not contain giant hard-coded model chains.
5. Tools require explicit permission policy.
6. Context is not automatically equal to entire repository contents.
7. Quota is not assumed to be unlimited.
8. Every major subsystem is testable independently.
