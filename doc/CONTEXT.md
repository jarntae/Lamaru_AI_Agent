# Context Management

## 1. Goal

Context Manager controls what information is sent to models.

The entire repository must not be sent automatically.

## 2. Context tiers

### Tier 1 — Current task

Highest priority.

Contains the current user request and immediate constraints.

### Tier 2 — Relevant files

Files directly related to the task.

### Tier 3 — Project knowledge

Architecture, conventions, important interfaces and persistent project facts.

### Tier 4 — Recent conversation

Recent messages relevant to the current task.

### Tier 5 — Compressed history

Older information represented as summaries.

## 3. Context pipeline

```text
Task
 |
 v
Identify relevant symbols/files
 |
 v
Search
 |
 v
Read
 |
 v
Rank relevance
 |
 v
Build context
 |
 v
Budget tokens
 |
 v
Compress if necessary
```

## 4. Relevance

Relevant context may include:

- directly referenced files;
- imports/dependencies;
- tests;
- configuration;
- interfaces;
- architecture documents;
- recent tool results.

Avoid unrelated files.

## 5. Compression

When context exceeds budget:

1. Preserve current task.
2. Preserve active code sections.
3. Preserve tool results that affect decisions.
4. Preserve errors and unresolved issues.
5. Summarize older conversation.
6. Remove redundant content.

## 6. Model switching

Context must be model-neutral.

Do not store provider-specific prompt assumptions in the Agent state.

When switching models, rebuild the request from normalized state and context.

## 7. Context budget

The system should track:

- estimated input tokens;
- estimated output budget;
- total context size;
- model context limit.

If the request exceeds the model limit, reduce context before retrying.

## 8. Repository awareness

Prefer search and targeted reads over full-repository loading.

For coding tasks, relevant tests should be included whenever possible.

## 9. Persistence

Persist summaries and important project facts when useful.

Do not persist secrets into context memory.
