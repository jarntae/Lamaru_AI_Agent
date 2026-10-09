# Model Routing

## 1. Goal

The router selects the best currently usable model/key/provider for a task.

It must optimize for task compatibility, availability and reliability rather than simply selecting the largest model.

## 2. Candidate pipeline

```text
Task
 |
 v
Required capabilities
 |
 v
Model registry
 |
 v
Remove disabled models
 |
 v
Remove incompatible models
 |
 v
Remove cooldown models/keys
 |
 v
Remove unhealthy candidates
 |
 v
Score remaining candidates
 |
 v
Select highest score
```

## 3. Task requirements

A task may contain:

```json
{
  "task_type": "coding",
  "complexity": "high",
  "requires_tools": true,
  "requires_reasoning": true,
  "requires_long_context": true
}
```

Possible task types:

- coding
- debugging
- refactoring
- explanation
- planning
- research
- documentation
- summarization
- general_chat

## 4. Model capabilities

Possible capabilities:

- coding
- reasoning
- tool_calling
- vision
- long_context
- research
- fast

## 5. Scoring

Conceptual score:

```text
score =
    capability_match
  + task_match
  + model_priority
  + health_score
  + quota_score
  + latency_score
  - failure_penalty
```

The implementation should use configurable weights.

Do not hard-code a chain such as:

```text
if A fails -> B
if B fails -> C
```

Use candidate metadata and policies.

## 6. Fallback order

Preferred order:

1. Same model, different healthy API key.
2. Equivalent model, same provider.
3. Compatible model, same provider.
4. Equivalent model, another provider.
5. Local model if configured.

Do not select a fallback that cannot satisfy the task requirements unless the policy explicitly permits degraded operation.

## 7. Error policies

### RATE_LIMIT

- record event;
- cooldown current key/model;
- retry with another candidate.

### QUOTA_EXCEEDED

- record event;
- disable candidate until quota recovery if appropriate;
- select another candidate.

### AUTH_ERROR

- disable affected key;
- do not repeatedly retry it.

### TEMPORARY_SERVER_ERROR

- limited exponential retry;
- then fallback.

### MODEL_NOT_FOUND

- mark model unavailable;
- refresh model registry if appropriate.

### CONTEXT_TOO_LARGE

- ask Context Manager to reduce context;
- retry;
- do not immediately rotate provider.

### INVALID_REQUEST

- return structured error;
- do not blindly retry.

### SAFETY_BLOCK

- do not repeatedly retry the same request unchanged.

### NETWORK_ERROR

- limited retry;
- fallback if repeated.

## 8. Retry

Retries must have:

- maximum attempts;
- exponential backoff;
- jitter;
- error-specific policy.

Do not retry permanent failures.

## 9. Cooldown

Cooldown state is tracked per:

- API key;
- model;
- optionally provider.

A cooldown candidate must not be selected unless it enters a controlled probe state.

## 10. Circuit breaker

States:

```text
CLOSED
OPEN
HALF_OPEN
```

Repeated failures move to OPEN.

After cooldown, move to HALF_OPEN.

Successful probe -> CLOSED.

Failed probe -> OPEN.

## 11. Model switching

When switching models:

```text
AgentState
   |
   v
new model
```

Do not rebuild the task from scratch.

The new model receives the required state/context according to Context Manager policy.

## 12. Quota assumptions

Never assume:

```text
number of API keys = unlimited quota
```

Provider-level limits may still apply.

Track observed behavior and provider-reported values separately.
