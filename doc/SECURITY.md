# Security

## 1. Threat model

Lamaru runs locally but still handles:

- API secrets;
- source code;
- shell commands;
- filesystem access;
- Git operations.

Treat model-generated instructions as untrusted input.

## 2. API keys

Never:

- hard-code secrets;
- commit secrets;
- print secrets in logs;
- return secrets through API responses;
- include secrets in model context.

Use environment variables or secure local secret storage.

## 3. Logging

Logs may contain:

- provider;
- model;
- internal key ID;
- request ID;
- latency;
- error category.

Logs must not contain actual API keys.

Prompt/content logging should be disabled by default.

## 4. Command execution

Shell commands require policy evaluation.

Dangerous operations should be:

- blocked;
- or require confirmation.

Examples of high-risk categories:

- recursive deletion;
- disk formatting;
- credential extraction;
- privilege escalation;
- modifying system directories.

Do not maintain a naive blacklist as the only protection.

Use explicit allowed-command policies where practical.

## 5. Filesystem

Default workspace boundary:

```text
workspace root
```

Do not allow arbitrary filesystem access by default.

## 6. HTTP server

The local Gateway should bind to localhost by default.

Do not expose the Gateway publicly without explicit configuration and authentication.

## 7. Prompt injection

Repository files, web content and tool output can contain malicious instructions.

The Agent must treat external content as data, not as trusted system instructions.

Tool permissions must remain authoritative even if a file tells the model to ignore them.

## 8. Model output

Never treat model output as trusted executable code.

Commands and file operations must pass through Tool Manager validation.

## 9. Sensitive data

Do not persist unnecessary secrets or sensitive data.

Provide a clear way to delete local session data and logs.

## 10. Security invariant

No model can override the runtime's permission system.
