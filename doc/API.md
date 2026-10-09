# Local HTTP API

## 1. Purpose

The Gateway exposes a local HTTP API for VS Code, CLI clients and compatible tools.

Default:

```text
http://127.0.0.1:<port>
```

## 2. Health

```http
GET /health
```

Returns service health.

## 3. Models

```http
GET /v1/models
```

Returns normalized available models.

## 4. Chat

```http
POST /v1/chat/completions
```

The endpoint should be compatible enough with common OpenAI-style clients to support basic chat and streaming.

The Gateway is still responsible for routing to the actual provider.

## 5. Agent

```http
POST /v1/agent/run
```

Runs an Agent task.

Input should include:

- task;
- workspace;
- optional model policy;
- optional tool policy.

## 6. Agent streaming

```http
POST /v1/agent/stream
```

Streams events such as:

- started
- planning
- model_selected
- text_delta
- tool_call
- tool_result
- retry
- model_switch
- completed
- error

## 7. Administration

Suggested endpoints:

```text
GET  /api/providers
GET  /api/models
GET  /api/keys
GET  /api/usage
GET  /api/health

POST /api/providers/sync
POST /api/keys/test
POST /api/models/refresh
```

Secrets must never be returned.

## 8. Errors

Use structured errors:

```json
{
  "error": {
    "code": "RATE_LIMIT",
    "message": "Provider rate limit reached",
    "request_id": "..."
  }
}
```

Do not expose provider secrets or internal stack traces in normal responses.

## 9. Request IDs

Every request should have a request ID.

Clients may provide one; otherwise the Gateway generates it.

## 10. Streaming

Use a standard streaming mechanism appropriate for the API implementation.

The client should be able to observe Agent progress without waiting for completion.
