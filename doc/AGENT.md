# Agent Runtime

## 1. Purpose

The Agent Runtime turns a language model into a controlled development agent.

The model proposes actions.
The runtime validates and executes those actions.

## 2. Agent state

AgentState must contain at least:

```text
session_id
task
plan
current_step
messages
context_summary
relevant_files
tool_history
tool_results
errors
model_history
iteration_count
status
```

The state belongs to the runtime, not to the model.

## 3. Agent loop

```text
User task
 |
 v
Create AgentState
 |
 v
Plan
 |
 v
Call model
 |
 +--> final answer
 |
 +--> tool call
        |
        v
   validate permission
        |
        v
   execute tool
        |
        v
   append result
        |
        v
   call model again
```

## 4. Iteration limit

Every Agent session must have a configurable maximum number of iterations.

The runtime must stop safely when the limit is reached.

## 5. Tool calls

A model tool call is a request, not automatic permission.

Runtime flow:

```text
Model requests tool
        |
        v
Validate schema
        |
        v
Check permission
        |
    +---+---+
    |       |
 allowed  confirmation
    |       |
 execute   ask user
```

Blocked operations must not execute.

## 6. Model failure

If the model fails:

```text
classify error
      |
      v
router
      |
      v
new candidate
      |
      v
continue AgentState
```

Never reset the Agent unless recovery policy explicitly requires it.

## 7. Planning

The first implementation may use a simple plan representation:

```text
1. Inspect relevant files
2. Identify root cause
3. Modify implementation
4. Run tests
5. Fix failures
6. Report result
```

Plans should be mutable.

## 8. Completion

An Agent session is complete when:

- the model explicitly returns a final result;
- required tool operations completed;
- no unresolved blocking error exists.

## 9. Failure

A session should report:

- what failed;
- which model/provider was used;
- whether fallback was attempted;
- whether changes were made;
- what remains unresolved.

Never claim a task succeeded if the runtime did not verify it.

## 10. Coding workflow

For coding tasks, prefer:

```text
inspect
 -> understand
 -> edit
 -> test
 -> inspect failure
 -> fix
 -> test again
```

Do not modify large numbers of unrelated files.

## 11. Human control

Destructive operations require explicit confirmation by default.

The Agent should be autonomous within the permissions granted to it, not beyond them.
