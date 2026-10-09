# Lamaru AI Gateway — Master Project Plan

> หลักการ: **AI Coding Agent เป็นผู้ช่วยเขียนโค้ด แต่เจ้าของโปรเจกต์เป็นผู้ตัดสินใจ architecture**
>
> ห้ามข้าม Phase โดยไม่ผ่าน checklist ของ Phase ก่อนหน้า

## 0. เป้าหมาย

สร้าง Local AI Gateway + Agent สำหรับ VS Code ที่สามารถ:
- รวม AI providers หลายรายไว้หลัง API เดียว
- รองรับหลาย API keys/accounts ต่อ provider
- รองรับหลาย models ต่อ provider
- route / retry / cooldown / fallback อัตโนมัติ
- รักษา Agent state เมื่อเปลี่ยน model
- มี Context Manager
- มี filesystem/search/terminal/Git tools
- มี SQLite persistence
- มี OpenAI-compatible local API
- มี MCP integration
- มี VS Code extension

## 1. กฎทอง

1. AI ห้ามเปลี่ยน architecture เอง
2. ถ้าจะเปลี่ยน architecture ต้องอธิบายเหตุผล ทางเลือก และรอเจ้าของอนุมัติ
3. สิ่งที่เป็น OPTIONAL/FUTURE ห้ามใส่ใน implementation หลักโดยอัตโนมัติ
4. dependency ใหม่ทุกตัวต้องอธิบายชื่อ, version, หน้าที่, เหตุผล และทางเลือก
5. unit tests ห้ามเรียก API จริง
6. ห้าม hard-code API keys
7. ห้ามรัน destructive commands โดยอัตโนมัติ
8. อย่าแก้หลาย subsystem พร้อมกันโดยไม่มีเหตุผล
9. ทุก Phase ต้องผ่าน tests ก่อนขึ้น Phase ถัดไป

## 2. Technology

### Required
- Python 3.12+
- FastAPI
- Pydantic
- httpx
- asyncio
- SQLite
- pytest
- Git
- VS Code
- Node.js/npm สำหรับ VS Code extension

### Optional
- Typer (ถ้า argparse ไม่พอ)
- SQLAlchemy หรือ SQLModel (เลือกเพียงหนึ่งถ้าจำเป็น)
- MCP SDK

### ยังไม่ต้องใช้
- Docker
- PostgreSQL
- Redis
- Kubernetes
- Cloud deployment

## 3. Repository

```text
lamaru-ai/
├── doc/
├── src/
├── tests/
├── config/
├── scripts/
├── apps/
├── .env.example
├── .gitignore
├── README.md
└── pyproject.toml
```

---

# Phase 0 — Preparation

## งาน
- [ ] สร้าง Git repository
- [ ] Python virtual environment
- [ ] `.gitignore`
- [ ] `.env.example`
- [ ] `pyproject.toml`
- [ ] `doc/`
- [ ] pytest
- [ ] initial commit

## ตรวจ
```bash
pytest
```

ต้องทำงานได้ก่อนเริ่ม Phase 1

---

# Phase 1 — Domain Model + Interfaces

## สร้าง
- Provider interface
- Model representation
- AI request/response
- Tool representation
- AgentState
- Error categories

## ห้ามทำ
- Google API
- Agent loop
- VS Code
- MCP
- complex database

## Tests
- domain validation
- AgentState serialization
- Provider interface

## Stop
Core types ต้อง stable และ tests ผ่าน

---

# Phase 2 — Provider Abstraction

สร้าง:
- `AIProvider`
- Provider registry
- lifecycle
- MockProvider
- health status

### Acceptance
เพิ่ม Provider C ได้โดยไม่ต้องแก้ routing logic

---

# Phase 3 — Google Provider

สร้าง:
- authentication
- model discovery
- normalized model metadata
- generation
- streaming
- normalized errors

API key ต้องมาจาก environment/secure storage

Integration tests ต้องแยกจาก unit tests

ทดสอบ:
- model list
- prompt
- streaming
- invalid key
- invalid model
- rate-limit response

---

# Phase 4 — Model Registry

Metadata:
- provider
- model_id
- display_name
- capabilities
- context_window
- priority
- stability
- enabled

Capabilities:
- coding
- reasoning
- tool_calling
- vision
- long_context
- research
- fast

Model list ไม่ควร hard-code ทั้งหมด

---

# Phase 5 — API Key Manager

รองรับ:

```text
Google
├── key-01
├── key-02
└── key-03
```

เก็บ:
- internal key id
- enabled
- health
- success/failure
- cooldown
- secret reference

ห้ามแสดง raw secret ใน UI/log/database โดยไม่มีการป้องกัน

---

# Phase 6 — Error Classifier

Categories:

```text
AUTH_ERROR
RATE_LIMIT
QUOTA_EXCEEDED
TEMPORARY_SERVER_ERROR
MODEL_NOT_FOUND
INVALID_REQUEST
CONTEXT_TOO_LARGE
SAFETY_BLOCK
NETWORK_ERROR
UNKNOWN
```

ทุก category ต้องมี retryability/permanence policy

สร้าง fake responses สำหรับ test ทุกประเภท

---

# Phase 7 — Retry + Cooldown + Circuit Breaker

Retry ต้องมี:
- max attempts
- exponential backoff
- jitter
- error-specific policy

Cooldown:

```text
HEALTHY -> COOLDOWN -> PROBE -> HEALTHY
```

Circuit breaker:

```text
CLOSED -> OPEN -> HALF_OPEN -> CLOSED
```

ทดสอบ:
- 429
- 500
- network failure
- permanent error
- recovery

ระบบต้องไม่ยิง candidate ที่อยู่ cooldown ซ้ำ

---

# Phase 8 — Model Router

Pipeline:

```text
Task
↓
Required capabilities
↓
Candidates
↓
Remove unavailable
↓
Score
↓
Select
```

Score ใช้แนวคิด:

```text
capability_match
+ task_match
+ priority
+ health
+ quota_score
+ latency_score
- failure_penalty
```

ห้ามสร้าง fallback chain แบบ hard-coded `if A -> B -> C`

---

# Phase 9 — Fallback Engine

ลำดับที่แนะนำ:

```text
same model + another key
↓
same provider + compatible model
↓
another provider
↓
local model
```

Scenario test:

```text
A/key1 -> 429
A/key2 -> 429
B/key1 -> success
```

ต้องได้:

```text
A/key1 -> A/key2 -> B/key1
```

โดย Agent state ไม่หาย

---

# Phase 10 — HTTP Gateway

Endpoints:

```text
GET  /health
GET  /v1/models
POST /v1/chat/completions
POST /v1/agent/run
POST /v1/agent/stream
```

ทดสอบด้วย curl ก่อนทำ VS Code extension

---

# Phase 11 — OpenAI-Compatible API

ขั้นต่ำ:

```text
/v1/models
/v1/chat/completions
```

Client ควรเปลี่ยนเพียง `base_url` เพื่อชี้มายัง Lamaru ได้

---

# Phase 12 — Agent State

States:

```text
CREATED
PLANNING
RUNNING
WAITING_TOOL
WAITING_USER
COMPLETED
FAILED
CANCELLED
```

ต้องมี:
- state transitions
- serialization
- persistence
- recovery

ทดสอบ restart แล้ว resume ได้

---

# Phase 13 — Context Manager

Pipeline:

```text
task
↓
search
↓
relevant files
↓
read
↓
rank
↓
context budget
↓
compress
```

ห้ามส่งทั้ง repository ทุก request โดยไม่จำเป็น

ต้อง track:
- input estimate
- output budget
- total context
- model context limit

---

# Phase 14 — Tool System

## Filesystem
- read_file
- write_file
- edit_file
- delete_file
- list_directory

## Search
- search_files
- search_text

## Terminal
- run_command

## Git
- git_status
- git_diff
- git_log
- git_branch
- git_commit

ทุก tool ต้องมี:
- name
- description
- input schema
- permission
- timeout
- execute()

---

# Phase 15 — Tool Security

Permission:

```text
ALLOWED
CONFIRMATION_REQUIRED
BLOCKED
```

ค่าเริ่มต้น:
- read: ALLOWED
- edit: ALLOWED
- delete: CONFIRMATION_REQUIRED
- destructive shell: CONFIRMATION_REQUIRED หรือ BLOCKED

Filesystem จำกัด workspace เป็นค่าเริ่มต้น

Model ไม่สามารถ override permission system

---

# Phase 16 — Agent Loop

```text
User
↓
AgentState
↓
Plan
↓
Router
↓
Model
↓
Tool?
├── No -> Final
└── Yes
      ↓
Permission
      ↓
Tool
      ↓
Result
      ↓
Context
      ↓
Model
```

ต้องมี:
- max iterations
- timeout
- cancellation
- tool timeout
- model failure recovery

---

# Phase 17 — Model Switching During Agent

Scenario:

```text
Agent
↓
Model A
↓
tool call
↓
Model A -> RATE_LIMIT
↓
Router
↓
Model B
↓
continue
```

AgentState ต้องรักษา:
- task
- plan
- tool history
- relevant files
- previous results
- current step

ห้ามเริ่ม task ใหม่จากศูนย์

---

# Phase 18 — SQLite Persistence

Tables อย่างน้อย:

```text
providers
provider_accounts
api_keys
models
model_capabilities
routing_rules
usage_records
health_states
conversations
messages
tool_calls
agent_sessions
```

ใช้ migrations และ repository/data-access layer

ปิด Gateway แล้วเปิดใหม่ข้อมูลสำคัญต้องไม่หาย

---

# Phase 19 — CLI

Commands:

```bash
lamaru status
lamaru models
lamaru providers
lamaru keys
lamaru usage
lamaru doctor
lamaru agent
```

`doctor` ตรวจ:
- Python
- config
- database
- providers
- API keys
- connectivity
- port
- MCP
- workspace

---

# Phase 20 — MCP

MCP = tool protocol ไม่ใช่ model router

เริ่ม expose:
- project search
- read file
- Git status
- Gateway status

อย่า expose dangerous tools ทั้งหมดทันที

---

# Phase 21 — VS Code Extension

Architecture:

```text
VS Code
↓
Extension
↓
Local Gateway
↓
Agent
↓
Router
↓
Provider
```

Features:
- chat
- agent mode
- model/provider status
- routing status
- tool approval
- streaming
- cancel
- session history

Extension ไม่ควรเรียก provider API โดยตรง

---

# Phase 22 — Observability

เก็บ:
- request ID
- latency
- model usage
- provider health
- fallback count
- retry count
- rate-limit count
- tool execution time

เริ่มจาก CLI ก่อน dashboard

---

# Phase 23 — Real-world Testing

ทดสอบ:

### A
key 1 healthy -> key 1 selected

### B
key 1 rate limited -> key 1 cooldown, key 2 selected

### C
all keys model A unavailable -> model B selected

### D
Google unavailable -> another provider selected

### E
context too large -> Context Manager reduces context

### F
Agent model fails halfway -> AgentState preserved, new model continues

---

# Phase 24 — Performance

หลัง functionality stable เท่านั้น

วัด:
- routing latency
- database latency
- tool latency
- context construction time
- memory usage

ห้าม optimize ก่อนมี measurement

---

# Phase 25 — Cleanup

ตรวจ:
- unused dependencies
- dead code
- duplicate abstractions
- naming
- security
- tests
- docs

---

# Phase 26 — Documentation Freeze

ตรวจให้ docs ตรงกับ code:

```text
ARCHITECTURE.md
PROVIDERS.md
ROUTING.md
AGENT.md
CONTEXT.md
TOOLS.md
SECURITY.md
API.md
DATABASE.md
DEVELOPMENT.md
```

ถ้าไม่ตรง:
1. แก้ code ให้ตรง
หรือ
2. เปลี่ยน architecture โดยตั้งใจ แล้วแก้ docs

ห้ามปล่อยให้สองอย่างขัดกัน

---

# Phase 27 — Release 0.1

ต้องมี:
- Google provider
- multiple API keys
- model registry
- router
- rate-limit handling
- retry
- cooldown
- fallback
- AgentState
- basic Agent
- filesystem tools
- terminal permissions
- SQLite
- localhost API
- CLI
- tests

ยังไม่ต้องมี:
- cloud deployment
- distributed workers
- multi-user auth
- advanced dashboard

---

# วิธีใช้ AI Agent อย่างปลอดภัย

ทุก Phase ให้ใช้ prompt นี้:

```text
You are working on Lamaru AI Gateway.

Read:
- doc/README.md
- doc/ARCHITECTURE.md
- doc/DEVELOPMENT.md
- the documentation relevant to this phase

We are implementing ONLY:
PHASE: <NUMBER>
NAME: <NAME>

Before coding:
1. Explain what you intend to change.
2. List files to create.
3. List files to modify.
4. List dependencies to add.
5. Explain why each dependency is necessary.
6. Explain architecture impact.
7. List tests.

Do not implement future phases.
Do not change architecture without approval.
Do not add optional features.
Do not use real provider APIs in unit tests.

After implementation:
1. Run tests.
2. Report test results.
3. Report files changed.
4. Report dependencies changed.
5. Report architectural deviations.
6. Stop.
```

---

# Human Review Checklist

ทุกครั้งหลัง AI ทำงาน:

## Code
- [ ] เข้าใจ code ใหม่
- [ ] ไม่มี code ที่ไม่รู้ที่มา
- [ ] ไม่มี abstraction เกินจำเป็น
- [ ] ตรวจ dependency ใหม่
- [ ] ไม่มี network behavior ที่ไม่ตั้งใจ

## Security
- [ ] ไม่มี API key ใน source
- [ ] ไม่มี secret ใน logs
- [ ] shell permission ยังทำงาน
- [ ] filesystem boundary ยังทำงาน

## Architecture
- [ ] provider code ไม่รั่วเข้า core
- [ ] router ไม่ผูกกับ model ใด model หนึ่ง
- [ ] AgentState ไม่ผูกกับ provider
- [ ] Context Manager แยกจาก Router
- [ ] Tool permission แยกจาก model

## Tests
- [ ] tests ผ่าน
- [ ] มี test ของ feature ใหม่
- [ ] ไม่มี test ถูกลบเพื่อให้ผ่าน

---

# Red Flags — ถ้าเจอให้หยุด

- เพิ่ม dependency เยอะโดยไม่อธิบาย
- เปลี่ยน architecture โดยไม่ถาม
- hard-code model names ใน router
- hard-code API key
- ส่ง API key ผ่าน frontend
- shell execution ไม่มี permission layer
- อ่านทั้ง repository ทุก request
- เพิ่ม Redis/PostgreSQL/Docker โดยไม่มี requirement
- fallback เป็น if/else ยาว ๆ
- model switching แล้วสร้าง Agent session ใหม่
- ลบ tests เพื่อให้ผ่าน
- `except Exception: pass`
- log secrets หรือ prompt โดยไม่ตั้งใจ
- เพิ่ม cloud service ทั้งที่ระบบตั้งใจ local

---

# Definition of Done

Feature ถือว่าเสร็จเมื่อ:

- [ ] implementation
- [ ] tests
- [ ] tests ผ่าน
- [ ] error handling
- [ ] security review
- [ ] documentation
- [ ] เจ้าของตรวจ `git diff`
- [ ] เจ้าของอนุมัติ commit

---

# Current Progress

เจ้าของโปรเจกต์เป็นคนอัปเดตส่วนนี้

```text
Current Phase: 0

Completed:
- [ ]

In Progress:
- [ ]

Blocked:
- [ ]

Next:
- [ ]
```

AI ห้ามเลื่อน Phase ให้ตัวเอง

---

# หลักการสุดท้าย

Lamaru ไม่ได้สร้างขึ้นเพื่อให้ AI เป็นเจ้าของระบบ

Lamaru สร้างขึ้นเพื่อให้มนุษย์ควบคุม AI หลายตัวผ่าน architecture เดียว

```text
Human
  ↓
Architecture
  ↓
Agent Runtime
  ↓
Router
  ↓
Provider
  ↓
Model
```

ไม่ใช่:

```text
Model
  ↓
AI Agent
  ↓
สร้างอะไรก็ได้
```

**ความสามารถของ AI ต้องอยู่ใต้ขอบเขตของ architecture เสมอ**
