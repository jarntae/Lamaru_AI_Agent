# Database

## 1. Purpose

SQLite stores persistent local state.

The database is not the source of truth for provider APIs. It stores Lamaru's observed state and configuration.

## 2. Suggested tables

### providers

- id
- name
- type
- enabled
- created_at
- updated_at

### provider_accounts

- id
- provider_id
- name
- status
- created_at
- updated_at

### api_keys

- id
- provider_account_id
- secret_ref
- enabled
- status
- failure_count
- success_count
- cooldown_until
- last_error
- last_used_at

### models

- id
- provider_id
- model_id
- display_name
- enabled
- priority
- context_window
- stability

### model_capabilities

- model_id
- capability
- score

### routing_rules

- id
- name
- configuration

### usage_records

- id
- request_id
- provider
- model
- key_id
- input_tokens
- output_tokens
- latency_ms
- success
- error_category
- created_at

### health_states

- target_type
- target_id
- state
- failure_count
- success_count
- cooldown_until
- updated_at

### conversations

- id
- workspace
- title
- status
- created_at
- updated_at

### messages

- id
- conversation_id
- role
- content
- metadata
- created_at

### tool_calls

- id
- conversation_id
- tool_name
- input
- output
- status
- created_at

### agent_sessions

- id
- conversation_id
- state
- current_model
- iteration_count
- status
- created_at
- updated_at

## 3. Secrets

Do not store raw API secrets in normal database columns.

Store a secret reference such as:

```text
env:GEMINI_API_KEY_01
```

or a secure keychain reference.

## 4. Migrations

Schema changes must use migrations.

Do not modify production tables silently at startup.

## 5. Repository pattern

Prefer a repository/data-access layer.

Core domain logic should not contain raw SQL everywhere.

## 6. Observed vs authoritative data

Examples:

- provider-reported usage is authoritative when available;
- locally estimated quota is an observation;
- local health state is Lamaru's observation.

Do not present estimates as exact provider quota.

## 7. Cleanup

Provide policies for:

- old request logs;
- old tool output;
- old conversations;
- old health records.

Do not allow the database to grow forever without controls.
