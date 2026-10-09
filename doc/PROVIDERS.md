# Provider System

## 1. Purpose

Providers adapt external or local AI systems to the common Lamaru interface.

The core system must not depend on a specific vendor.

## 2. Provider interface

Conceptually:

```python
class AIProvider:
    async def list_models(self): ...
    async def get_model(self, model_id): ...
    async def generate(self, request): ...
    async def stream(self, request): ...
    async def health_check(self): ...
```

The exact Python types may evolve, but the responsibility boundary must remain.

## 3. Provider adapter

Each provider adapter owns:

- authentication;
- HTTP/API protocol;
- provider-specific request format;
- provider-specific response parsing;
- provider-specific error parsing;
- model discovery;
- streaming implementation.

It must return normalized Lamaru domain objects.

## 4. Normalized response

The core should receive normalized information such as:

```text
provider
model
finish_reason
content
tool_calls
usage
latency
raw_error_category
```

Raw provider responses may be retained for diagnostics, but must not leak secrets.

## 5. Google provider

Google is the first implementation.

Keep all Google-specific code under:

```text
providers/google/
```

Do not spread Google model IDs or request formats through the core.

The Google adapter should support model discovery where available.

## 6. Local providers

Local models should use the same interface.

Examples:

- local HTTP inference server;
- local model runtime;
- future local adapters.

A local provider must not require the router to know that it is local.

## 7. Adding a provider

To add a provider:

1. Implement the provider interface.
2. Implement model discovery.
3. Implement generation.
4. Implement streaming.
5. Normalize provider errors.
6. Add health checks.
7. Add tests with mocked responses.
8. Register the provider.
9. Add configuration documentation.

Do not modify router logic specifically for the new provider.

## 8. API key identity

The router should use an internal `key_id`.

Never expose the actual secret.

Example:

```text
google-key-01
google-key-02
```

is allowed.

```text
AIza...actual-secret...
```

must never appear in logs or UI.

## 9. Provider errors

Provider adapters should normalize errors into common categories:

- AUTH_ERROR
- RATE_LIMIT
- QUOTA_EXCEEDED
- TEMPORARY_SERVER_ERROR
- MODEL_NOT_FOUND
- INVALID_REQUEST
- CONTEXT_TOO_LARGE
- SAFETY_BLOCK
- NETWORK_ERROR
- UNKNOWN

The router decides what to do with the category.
