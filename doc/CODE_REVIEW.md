# เอกสารการตรวจทานโค้ดฉบับสมบูรณ์ (Comprehensive Code Review Document) — Lamaru AI Gateway

> **โปรเจกต์:** Lamaru AI Gateway and Agent Runtime  
> **เวอร์ชันปัจจุบัน:** `0.1.0`  
> **ความคืบหน้าการพัฒนา:** ผ่าน Phase 0 ถึง Phase 15 สมบูรณ์  
> **ผลการรันชุดทดสอบ (Unit Tests):** ผ่าน 89/90 รายการ (Passed 89, Skipped 1 สำหรับ Real API Integration)  
> **วันที่อัปเดตเอกสาร:** 10 ตุลาคม 2026  
> **ขอบเขตการรีวิว:** ซอร์สโค้ดและโมดูลทั้งหมดภายในโฟลเดอร์ `src/lamaru/`

---

## 1. ภาพรวมของโปรเจกต์ (Project Overview)

**Lamaru AI Gateway** คือระบบ Local AI Gateway และ Agent Runtime สำหรับเชื่อมต่อระหว่างเครื่องมือของนักพัฒนา (เช่น VS Code Extension หรือ Client Apps) กับ AI Model Providers หลายค่าย (เช่น Google Gemini, Local Models) 

### ขีดความสามารถหลักที่สร้างเสร็จแล้ว (Phase 0 – Phase 15):
1. **Domain Abstraction:** โครงสร้างข้อมูลกลาง (`AIRequest`, `AIResponse`, `Message`, `Tool`, `Model`, `ErrorCategory`) โดยไม่มี Dependency ภายนอก
2. **Provider Adapters:** มี `MockProvider` สำหรับทดสอบ และ `GoogleProvider` (Gemini REST API รองรับ Function Calling และ SSE Streaming)
3. **Multi-Key Management & Secret Protection:** รองรับคีย์สำรองหลายตัวต่อ Provider สลับคีย์อัตโนมัติ และปกปิด Secret ด้วย `SecretReference`
4. **Resilience Engine:** ระบบ Retry พร้อม Exponential Backoff & Jitter, Cooldown Tracker (`HEALTHY -> COOLDOWN -> PROBE -> HEALTHY`), และ Circuit Breaker (`CLOSED -> OPEN -> HALF_OPEN -> CLOSED`)
5. **Dynamic Model Router:** ให้คะแนนและจัดอันดับโมเดลแบบพลวัต (Multi-criteria Scoring) ตามความเหมาะสมของงาน ลำดับความสำคัญ ความเร็ว สุขภาพ และประวัติความล้มเหลว
6. **Fallback Engine:** สลับคีย์/โมเดล/ค่ายอัตโนมัติเมื่อเกิดข้อผิดพลาด โดยรักษา AgentState และประวัติ Request ไว้อย่างสมบูรณ์
7. **Local HTTP Gateway (FastAPI):** เปิด OpenAI-compatible API (`/v1/chat/completions`, `/v1/models`) และ Agent Endpoints (`/v1/agent/run`, `/v1/agent/stream`)
8. **Agent State Manager:** ดูแลวงจรชีวิตของ Agent บันทึกสถานะลงดิสก์แบบทนทาน (Persistence) และกู้คืนเซสชันเมื่อโปรเซสหยุดทำงานได้
9. **Context Manager:** คำนวณ Token Budget, จัดลำดับความสำคัญของบริบท 5 ระดับ (5 Tiers), บีบอัดประวัติ และตัดทอนไฟล์ขนาดใหญ่ไม่ให้เกินขีดจำกัดโมเดล
10. **Native Tool System & Tool Security:** เครื่องมือ Filesystem, Search, Terminal, Git พร้อมระบบความปลอดภัย `WorkspaceGuard` (ขังใน Workspace), `CommandGuard` (บล็อกคำสั่งทำลายล้าง), และ Permission Tiers (`ALLOWED`, `CONFIRMATION_REQUIRED`, `BLOCKED`) ที่ AI ไม่สามารถแก้ไขเองได้

---

## 2. โครงสร้างโฟลเดอร์และไฟล์ (Source Tree Structure)

```text
src/lamaru/
├── __init__.py                     # รูทแพ็กเกจ Lamaru เวอร์ชัน 0.1.0
├── agent/                          # จัดการ State Machine และ Disk Persistence ของ Agent
│   ├── __init__.py
│   └── state_manager.py            # AgentStateManager, ALLOWED_TRANSITIONS, Session Resume
├── context/                        # จัดการ Token Budget, ลำดับชั้น Context และการบีบอัด
│   ├── __init__.py
│   ├── manager.py                  # ContextManager (estimate_tokens, compress_history, assemble_context)
│   └── models.py                   # ContextTier, ContextItem, ContextBudget, AssembledContext
├── domain/                         # โดเมนโมเดล สัญญาข้อมูล (Contracts) และ Interfaces
│   ├── __init__.py
│   ├── agent.py                    # AgentStatus, Plan, AgentState
│   ├── errors.py                   # ErrorCategory (10 หมวด), LamaruError
│   ├── messages.py                 # Message, MessageRole, ToolCall, AIRequest, AIResponse, AIStreamChunk, Usage
│   ├── models.py                   # Model, ModelCapability, TaskRequirement
│   ├── provider.py                 # AIProvider (ABC), ProviderHealth
│   └── tools.py                    # Tool (ABC), ToolDefinition, ToolResult, PermissionLevel
├── errors/                         # การจำแนก Error และนโยบายรับมือ
│   ├── __init__.py
│   ├── classifier.py               # ErrorClassifier, parse_retry_after, classify_http, classify_exception
│   └── policy.py                   # ErrorPolicy, ErrorAction, DEFAULT_POLICIES (10 หมวด)
├── gateway/                        # FastAPI HTTP Server & OpenAI-compatible Endpoints
│   ├── __init__.py
│   ├── app.py                      # create_app factory, RequestIDMiddleware, Chat Completions, Agent API
│   └── models.py                   # Pydantic Schemas สำหรับ HTTP Request/Response
├── keys/                           # ระบบจัดการ API Key Pool และความปลอดภัย
│   ├── __init__.py
│   ├── manager.py                  # APIKeyManager (Pool, Rotation, Cooldown, Env Discovery)
│   └── models.py                   # SecretReference (Redaction), APIKeyMetadata, KeyStatus
├── models/                         # แค็ตตาล็อกและการค้นหาโมเดล
│   ├── __init__.py
│   └── registry.py                 # ModelRegistry (Catalog, Filtering, Overrides, Sync)
├── providers/                      # Provider Adapters
│   ├── __init__.py
│   ├── mock.py                     # MockProvider สำหรับทดสอบ
│   ├── registry.py                 # ProviderRegistry (รวม Provider, Health Check, Lifecycle)
│   └── google/                     # Google Gemini REST Adapter
│       ├── __init__.py
│       ├── errors.py               # Gemini Error Parser & Network Exception Mapper
│       ├── mapper.py               # แปลง Domain <-> Gemini JSON Payload
│       └── provider.py             # GoogleProvider (list_models, generate, stream, health_check)
├── resilience/                     # เสถียรภาพ: Retry, Cooldown, Circuit Breaker
│   ├── __init__.py
│   ├── circuit_breaker.py          # CircuitBreaker (CLOSED, OPEN, HALF_OPEN)
│   ├── cooldown.py                 # CooldownTracker (HEALTHY, COOLDOWN, PROBE)
│   └── retry.py                    # execute_with_retry, RetryPolicy (Exponential Backoff + Jitter)
├── router/                         # การเลือกโมเดลและ Fallback Engine
│   ├── __init__.py
│   ├── fallback.py                 # FallbackEngine (สลับ Key/Model/Provider อัตโนมัติ)
│   ├── models.py                   # CandidateTarget, RouterScoringWeights, RoutingResult
│   └── router.py                   # ModelRouter (Dynamic Scoring 7 มิติ)
└── tools/                          # Native Tools และระบบความปลอดภัย
    ├── __init__.py
    ├── filesystem.py               # ReadFileTool, WriteFileTool, EditFileTool, DeleteFileTool, ListDirectoryTool
    ├── git.py                      # GitStatusTool, GitDiffTool, GitLogTool, GitBranchTool, GitCommitTool
    ├── registry.py                 # ToolRegistry, create_default_tool_registry
    ├── search.py                   # SearchFilesTool (Glob), SearchTextTool (Grep)
    ├── security.py                 # WorkspaceGuard, CommandGuard, ToolSecurityPolicy
    └── terminal.py                 # RunCommandTool (Subprocess execution with timeout)
```

---

## 3. เจาะลึกราย Subsystem: หน้าที่, เมธอด, พารามิเตอร์ และการคืนค่า

---

### Subsystem 1: `lamaru.domain` (Domain Core)
ทำหน้าที่กำหนด Core Data Contracts ทั้งหมด ปราศจาก Dependency ภายนอก

#### 1.1 `domain/models.py`
* **`ModelCapability(str, Enum)`**: `CODING`, `REASONING`, `TOOL_CALLING`, `VISION`, `LONG_CONTEXT`, `RESEARCH`, `FAST`
* **`Model(BaseModel)`**:
  * Fields: `provider: str`, `model_id: str`, `display_name: str`, `capabilities: Set[ModelCapability]`, `context_window: int`, `priority: int`, `stability: str`, `enabled: bool`
  * `has_capability(capability: ModelCapability) -> bool`: ตรวจสอบว่าโมเดลมี capability หรือไม่ (ส่งกลับ `bool`)
* **`TaskRequirement(BaseModel)`**:
  * Fields: `task_type: str`, `complexity: str`, `requires_tools: bool`, `requires_reasoning: bool`, `requires_long_context: bool`, `required_capabilities: Set[ModelCapability]`

#### 1.2 `domain/messages.py`
* **`MessageRole(str, Enum)`**: `SYSTEM`, `USER`, `ASSISTANT`, `TOOL`
* **`ToolCall(BaseModel)`**: `id: str`, `name: str`, `arguments: Dict[str, Any]`
* **`Message(BaseModel)`**: `role: MessageRole`, `content: Optional[str]`, `name: Optional[str]`, `tool_call_id: Optional[str]`, `tool_calls: List[ToolCall]`
* **`Usage(BaseModel)`**: `input_tokens: int`, `output_tokens: int`, `total_tokens: int`
* **`FinishReason(str, Enum)`**: `STOP`, `TOOL_CALLS`, `LENGTH`, `CONTENT_FILTER`, `ERROR`
* **`AIRequest(BaseModel)`**: คำขอมาตรฐาน (`messages`, `model`, `provider`, `key_id`, `temperature`, `max_tokens`, `tools`, `stream`, `system_prompt`)
* **`AIResponse(BaseModel)`**: คำตอบมาตรฐาน (`provider`, `model`, `content`, `tool_calls`, `finish_reason`, `usage`, `latency_ms`, `raw_error_category`)
* **`AIStreamChunk(BaseModel)`**: ก้อนข้อมูลสตรีม (`content_delta`, `tool_calls`, `finish_reason`, `usage`)

#### 1.3 `domain/tools.py`
* **`PermissionLevel(str, Enum)`**: `ALLOWED`, `CONFIRMATION_REQUIRED`, `BLOCKED`
* **`ToolDefinition(BaseModel)`**: `name: str`, `description: str`, `input_schema: Dict[str, Any]`, `permission: PermissionLevel`, `timeout_seconds: float`
* **`ToolResult(BaseModel)`**: `success: bool`, `output: Any`, `error: Optional[str]`, `metadata: Dict[str, Any]`
* **`Tool(ABC)`**: คลาสแม่ของเครื่องมือทุกตัว
  * Properties: `definition -> ToolDefinition`, `name -> str`
  * `async execute(input_args: Dict[str, Any]) -> ToolResult`: รันตรรกะเครื่องมือ

#### 1.4 `domain/errors.py`
* **`ErrorCategory(str, Enum)`**: 10 หมวดหมู่ (`AUTH_ERROR`, `RATE_LIMIT`, `QUOTA_EXCEEDED`, `TEMPORARY_SERVER_ERROR`, `MODEL_NOT_FOUND`, `INVALID_REQUEST`, `CONTEXT_TOO_LARGE`, `SAFETY_BLOCK`, `NETWORK_ERROR`, `UNKNOWN`)
  * Properties: `is_retryable -> bool`, `is_permanent -> bool`
* **`LamaruError(Exception)`**: Domain Exception กลาง
  * `to_dict() -> dict[str, Any]`: คืนค่า dict ที่ปลอดภัยต่อการแสดงผลและบันทึก log

#### 1.5 `domain/agent.py`
* **`AgentStatus(str, Enum)`**: `CREATED`, `PLANNING`, `RUNNING`, `WAITING_TOOL`, `WAITING_USER`, `COMPLETED`, `FAILED`, `CANCELLED`
  * Property: `is_terminal -> bool`
* **`Plan(BaseModel)`**: `steps: List[str]`, `current_step_index: int`
* **`AgentState(BaseModel)`**: ข้อมูล Session กลาง
  * `transition_to(new_status: AgentStatus) -> None`: เปลี่ยนสถานะพร้อมกันข้อผิดพลาดกรณีจบไปแล้ว
  * `to_json() -> str`: Export ออกเป็น JSON
  * `from_json(cls, json_str: str) -> AgentState`: โหลดคืนสถานะจาก JSON

#### 1.6 `domain/provider.py`
* **`ProviderHealth(BaseModel)`**: `provider: str`, `status: str`, `latency_ms: float`, `message: Optional[str]`, `checked_at: datetime`
* **`AIProvider(ABC)`**: อินเตอร์เฟซผู้ให้บริการ (`list_models`, `get_model`, `generate`, `stream`, `health_check`)

---

### Subsystem 2: `lamaru.resilience` (Retry, Cooldown & Circuit Breaker)
ดูแลความทนทานต่อข้อผิดพลาด ป้องกัน Cascading Failures

#### 2.1 `resilience/retry.py`
* **`RetryPolicy(BaseModel)`**:
  * Fields: `max_attempts: int = 3`, `initial_backoff_seconds: float = 0.5`, `backoff_multiplier: float = 2.0`, `max_backoff_seconds: float = 10.0`, `jitter: bool = True`
  * `calculate_backoff(attempt: int) -> float`: คำนวณเวลาถอยหน่วงแบบ Exponential ผสมการสุ่ม Jitter
* **`async execute_with_retry(operation, error_classifier, retry_policy, on_retry, sleep_func) -> Any`**:
  * **หน้าที่:** รันคำสั่ง Asynchronous พร้อม Retry อัตโนมัติ
  * **ตรรกะ:** ถ้าเจอข้อผิดพลาดถาวร (`AUTH_ERROR`, `INVALID_REQUEST`, `SAFETY_BLOCK`) จะ Abort ทันที ไม่ Retry, ถ้าเป็น Error ที่ Retry ได้ จะหน่วงเวลาตาม `Retry-After` หรือ Backoff จนครบโควต้า

#### 2.2 `resilience/cooldown.py`
* **`CooldownState(str, Enum)`**: `HEALTHY`, `COOLDOWN`, `PROBE`
* **`CooldownTracker`**:
  * `get_state(target_id: str, now: Optional[datetime]) -> CooldownState`: เช็คสถานะ หากครบกำหนด Cooldown จะปรับเข้าสู่ `PROBE`
  * `is_available(target_id: str, now: Optional[datetime]) -> bool`: เช็คความพร้อม (ห้ามเลือกเป้าหมายที่ติด `COOLDOWN` เด็ดขาด)
  * `mark_cooldown(target_id: str, duration_seconds: float)`: สั่งพักใช้งาน
  * `acquire_probe(target_id: str) -> bool`: อนุญาตให้ยิงทดสอบได้ครั้งละ 1 คำขอ
  * `mark_healthy(target_id: str)`: กู้คืนสถานะกลับมาเป็น `HEALTHY`

#### 2.3 `resilience/circuit_breaker.py`
* **`CircuitState(str, Enum)`**: `CLOSED` (ปกติ), `OPEN` (ตัดวงจร), `HALF_OPEN` (ทดลองฟื้น)
* **`CircuitBreaker`**:
  * `can_execute(now: Optional[datetime]) -> bool`: ตรวจสอบว่าวงจรยอมให้คำขอผ่านหรือไม่ (เมื่อครบเวลา Cooldown จะเปลี่ยนเป็น `HALF_OPEN`)
  * `record_success()`: เมื่อยิงผ่านใน `HALF_OPEN` สำเร็จ จะ Reset สถิติและปรับเป็น `CLOSED`
  * `record_failure(cooldown_seconds, now)`: หากล้มเหลวติดต่อกันถึงเกณฑ์ `failure_threshold` จะดีดวงจรเป็น `OPEN`

---

### Subsystem 3: `lamaru.router` (Model Router & Fallback Engine)
เลือกโมเดลที่ดีที่สุดและสลับตัวสำรองเมื่อเกิดปัญหา

#### 3.1 `router/models.py`
* **`CandidateTarget(BaseModel)`**: เป้าหมายตัวเลือก (`provider`, `model_id`, `key_id`, `model`, `score`, `score_breakdown`)
  * Property: `target_key -> str` (เช่น `"google:gemini-2.5-flash:google-key-01"`)
* **`RouterScoringWeights(BaseModel)`**: น้ำหนักเกณฑ์การให้คะแนน 7 มิติ (ความสามารถ, ประเภทงาน, Priority, สุขภาพ, ความน่าเชื่อถือ, Latency, และแต้มหักความผิดพลาด)
* **`RoutingResult(BaseModel)`**: ผลลัพธ์การคัดเลือก (`selected_candidate`, `ranked_candidates`, `task_requirement`, `evaluated_count`)

#### 3.2 `router/router.py`
* **`ModelRouter`**:
  * `calculate_score(candidate, task, health_latency_ms, provider_health_status, failure_count, success_count) -> Tuple[float, Dict[str, float]]`: คำนวณคะแนนอย่างเป็นกลาง ไม่มี Hard-code if-else
  * `route(task: TaskRequirement, min_context_window, exclude_candidates, now) -> RoutingResult`: คัดกรองโมเดลที่เข้ากันได้, ตัดตัวที่ติด Cooldown/Circuit Breaker ออก, ให้คะแนน และจัดอันดับเรียงจากมากไปน้อย

#### 3.3 `router/fallback.py`
* **`FallbackEngine`**:
  * `async execute_with_fallback(request: AIRequest, task, max_fallbacks=5, on_fallback, now) -> FallbackResult`:
    * รันคำขอตามอันดับโมเดลที่ดีที่สุด
    * หากเจอปัญหา (เช่น Rate Limit 429) จะบันทึก Cooldown ให้คีย์นั้น และหมุนเวียนไปเรียกผู้สมัครลำดับถัดไปทันที (ตามลำดับ: สลับ Key เดิม -> สลับ Model ค่ายเดิม -> สลับ Provider อื่น)
    * หากเป็นข้อผิดพลาดที่คำขอเอง (เช่น `SAFETY_BLOCK`, `INVALID_REQUEST`) จะหยุดทันที ไม่สลับโมเดลให้เสียโควต้าโดยเปล่าประโยชน์

---

### Subsystem 4: `lamaru.gateway` (FastAPI HTTP Gateway)
เป็น API Gateway ท้องถิ่น เชื่อมต่อกับ IDE และ Client

#### 4.1 `gateway/models.py`
* โครงสร้างข้อมูลจำลองตามมาตรฐาน OpenAI:
  * `ChatCompletionRequest`, `ChatMessagePayload`, `ChatCompletionResponse`, `ChatChoice`, `ChatUsage`, `ModelListResponse`, `ModelItem`
* โครงสร้างสำหรับ Agent Runtime:
  * `AgentRunRequest`, `AgentRunResponse`

#### 4.2 `gateway/app.py`
* **`create_app(fallback_engine, model_registry, provider_registry) -> FastAPI`**:
  * **Middleware:** `RequestIDMiddleware` บรรจุ `x-request-id` ให้ทุกคำขอสำหรับ Tracing
  * **Exception Handlers:** ดักจับ `LamaruError` และแปลงเป็น HTTP Status Code ที่เหมาะสม (401, 404, 429, 503)
  * **Endpoints:**
    * `GET /health` — ตรวจสอบสถานะเซิร์ฟเวอร์
    * `GET /v1/models` — คืนรายการโมเดลที่เปิดใช้งานในรูปแบบ OpenAI format
    * `POST /v1/chat/completions` — รองรับทั้ง Non-streaming JSON และ Server-Sent Events (SSE) Streaming
    * `POST /v1/agent/run` — รัน Agent Execution Loop รอบเดียว
    * `POST /v1/agent/stream` — สตรีม Agent Execution Events ผ่าน SSE

---

### Subsystem 5: `lamaru.agent` (Agent State Manager)
ดูแลวงจรชีวิตและการเก็บกู้สถานะของ Agent

#### 5.1 `agent/state_manager.py`
* **`ALLOWED_TRANSITIONS`**: กราฟจำกัดทิศทางการเปลี่ยนสถานะอย่างเข้มงวด ป้องกันสถานะกระโดดข้ามขั้น
* **`AgentStateManager`**:
  * `create_session(task: str, session_id, max_iterations=25) -> AgentState`: สร้างเซสชันใหม่ในสถานะ `CREATED`
  * `save_session(state: AgentState) -> None`: บันทึกข้อมูลลง Memory และเขียนไฟล์ JSON ลงดิสก์อย่างปลอดภัย
  * `get_session(session_id: str) -> Optional[AgentState]`: โหลดข้อมูลเซสชันจาก Memory หรืออ่านจากดิสก์
  * `transition_state(session_id: str, new_status: AgentStatus) -> AgentState`: ตรวจสอบและเปลี่ยนสถานะตามกฎ หากไม่ถูกต้องจะโยน `ValueError`
  * `resume_session(session_id: str) -> AgentState`: กู้คืนเซสชันหลังโปรเซสถูกรีสตาร์ท ตรวจสอบว่าไม่อยู่ใน Terminal State
  * `list_sessions() -> List[AgentState]`: คืนรายการเซสชันทั้งหมด

---

### Subsystem 6: `lamaru.context` (Context Manager & Budgeting)
จัดสรรงบประมาณ Token ไม่ให้ล้นและบีบอัด Context อัตโนมัติ

#### 6.1 `context/models.py`
* **`ContextTier(str, Enum)`**: ระดับความสำคัญ 5 ชั้น
  * `TIER_1_TASK`: งานและเงื่อนไขปัจจุบัน (สำคัญสูงสุด ห้ามตัดทิ้ง)
  * `TIER_2_FILES`: ซอร์สโค้ดและไฟล์ที่เกี่ยวข้องโดยตรง
  * `TIER_3_KNOWLEDGE`: เอกสาร สถาปัตยกรรม และกฎของโปรเจกต์
  * `TIER_4_RECENT_MESSAGES`: บทสนทนาล่าสุด
  * `TIER_5_COMPRESSED_HISTORY`: ประวัติการคุยเก่าที่ถูกสรุปย่อ
* **`ContextBudget(BaseModel)`**: คำนวณงบประมาณ (`model_context_limit`, `output_budget`, `max_input_budget`, `input_estimate`, `total_context`)
* **`AssembledContext(BaseModel)`**: ข้อมูล Context ที่ประกอบเสร็จสมบูรณ์พร้อมส่งให้โมเดล

#### 6.2 `context/manager.py`
* **`ContextManager`**:
  * `estimate_tokens(text: Optional[str]) -> int`: ประมาณการ Token จากจำนวนตัวอักษร (Heuristic ~3.8 ตัวอักษร/Token)
  * `calculate_budget(model_context_limit, output_budget=4096, safety_margin_ratio=0.05) -> ContextBudget`: คำนวณเพดาน Input ปลอดภัย
  * `compress_history(messages: List[Message], budget_tokens: int) -> Tuple[List[Message], bool]`: เดินย้อนกลับเก็บข้อความล่าสุด และแทนที่ข้อความเก่าที่ล้นงบด้วย Marker ข้อความสรุป
  * `assemble_context(task, relevant_files, project_knowledge, messages, model_context_limit, output_budget) -> AssembledContext`: นำบริบททุก Tier มาเรียงลำดับความสำคัญ ตัดทอนไฟล์ขนาดยักษ์ และประกอบเป็น `system_instruction`

---

### Subsystem 7: `lamaru.tools` (Native Tools & Security System)
เครื่องมือปฏิสัมพันธ์กับระบบและกลไกความปลอดภัย

#### 7.1 `tools/security.py`
* **`WorkspaceGuard`**:
  * `validate_path(target_path: str) -> Path`: ตรวจสอบและแปลง Path ให้แน่ใจว่าอยู่ใต้ `workspace_root` อย่างเข้มงวด หากพบ Path Traversal (`../`) ออกนอกโฟลเดอร์ จะโยน `SecurityViolationError` ทันที
* **`CommandGuard`**:
  * `check_command(command: str) -> Optional[str]`: ตรวจจับ Regular Expression คำสั่งอันตราย เช่น `rm -rf /`, `rmdir /s /q c:`, `format`, `mkfs`, หรือ Fork Bomb
* **`ToolSecurityPolicy`**:
  * บังคับใช้ Permission Tiers:
    * `ALLOWED` — เครื่องมือปลอดภัย (อ่านไฟล์, ค้นหา, อ่าน git log)
    * `CONFIRMATION_REQUIRED` — มีผลกระทบ (ลบไฟล์ `delete_file`, รันคำสั่ง `run_command`)
    * `BLOCKED` — ถูกปิดการใช้งานถาวร
  * `authorize_execution(tool_definition, arguments, is_user_confirmed) -> Tuple[bool, Optional[str]]`: จุดตรวจความปลอดภัยชั้นสุดท้ายก่อนยอมให้ Tool ทำงาน

#### 7.2 เครื่องมือในระบบ (`tools/*.py`)
* **[`tools/filesystem.py`](file:///c:/Poramet-SharFile/github/Lamaru_Ai_Agent/src/lamaru/tools/filesystem.py)**
  * `read_file` (`ReadFileTool`): อ่านเนื้อหาไฟล์ พร้อมระบุช่วงบรรทัด (`start_line`, `end_line`) ได้
  * `write_file` (`WriteFileTool`): สร้างไฟล์ใหม่ (หากมีไฟล์เดิมอยู่ต้องระบุ `overwrite=True`)
  * `edit_file` (`EditFileTool`): ค้นหาและแทนที่ข้อความเฉพาะจุดในไฟล์
  * `delete_file` (`DeleteFileTool`): ลบไฟล์ (ต้องการ Confirmation)
  * `list_directory` (`ListDirectoryTool`): แสดงรายชื่อไฟล์และโฟลเดอร์ (รองรับ Recursive)
* **[`tools/search.py`](file:///c:/Poramet-SharFile/github/Lamaru_Ai_Agent/src/lamaru/tools/search.py)**
  * `search_files` (`SearchFilesTool`): ค้นหาชื่อไฟล์ตาม Glob pattern (ตัดโฟลเดอร์ขยะ เช่น `.git`, `.venv` อัตโนมัติ)
  * `search_text` (`SearchTextTool`): ค้นหาข้อความข้างในไฟล์ (แบบ grep)
* **[`tools/terminal.py`](file:///c:/Poramet-SharFile/github/Lamaru_Ai_Agent/src/lamaru/tools/terminal.py)**
  * `run_command` (`RunCommandTool`): รันคำสั่ง Shell ภายใต้ Subprocess พร้อม Timeout และดักจับ stdout/stderr
* **[`tools/git.py`](file:///c:/Poramet-SharFile/github/Lamaru_Ai_Agent/src/lamaru/tools/git.py)**
  * `git_status`, `git_diff`, `git_log`, `git_branch`, `git_commit`
* **[`tools/registry.py`](file:///c:/Poramet-SharFile/github/Lamaru_Ai_Agent/src/lamaru/tools/registry.py)**
  * `ToolRegistry`: ลงทะเบียนและเรียกใช้งาน Tool พร้อมเชื่อมโยงกับ `ToolSecurityPolicy`
  * `create_default_tool_registry(workspace_root, security_policy) -> ToolRegistry`: ฟังก์ชันสำเร็จรูปสำหรับโหลด Tools ทั้งหมดพร้อมตั้งค่าความปลอดภัย

---

## 4. ผลการทดสอบ Unit Tests ฉบับล่าสุด (Test Execution Report)

ผลการทดสอบผ่านคำสั่ง `.\.venv\Scripts\pytest.exe -v`:
* **ชุดทดสอบทั้งหมด:** 90 รายการ
* **สถานะ:** Passed 89 รายการ, Skipped 1 รายการ (`test_google_provider_real_api_integration`)
* **เวลาที่ใช้:** ~3.42 วินาที

### สรุปผลแยกตามหมวดการทดสอบ:
1. **Agent State & Persistence:** ผ่าน 5 รายการ (`test_agent_state.py`, `test_agent_state_manager.py`)
2. **API Key & Secrets:** ผ่าน 6 รายการ (`test_api_key_manager.py`)
3. **Context Manager & Budgeting:** ผ่าน 6 รายการ (`test_context_manager.py`)
4. **Domain Validation:** ผ่าน 7 รายการ (`test_domain_validation.py`)
5. **Error Classification & Policy:** ผ่าน 3 รายการ (`test_error_classifier.py`)
6. **Fallback Engine & State Preservation:** ผ่าน 4 รายการ (`test_fallback_engine.py`)
7. **HTTP Gateway (FastAPI & OpenAI Compat):** ผ่าน 6 รายการ (`test_gateway.py`)
8. **Providers (Google & Mock):** ผ่าน 12 รายการ (`test_google_provider.py`, `test_mock_provider.py`)
9. **Model Registry & Routing Pipeline:** ผ่าน 11 รายการ (`test_model_registry.py`, `test_model_router.py`)
10. **Provider Interface & Registry:** ผ่าน 6 รายการ (`test_provider_interface.py`, `test_provider_registry.py`)
11. **Resilience (Retry, Cooldown, Circuit Breaker):** ผ่าน 9 รายการ (`test_resilience.py`)
12. **Native Tools & Security Enforcement:** ผ่าน 11 รายการ (`test_tools.py`, `test_tool_security.py`)
13. **Smoke Tests:** ผ่าน 2 รายการ (`test_smoke.py`)

---

## 5. การปฏิบัติตามกฎสถาปัตยกรรม (Architecture Compliance Check)

| กฎสถาปัตยกรรม (Architectural Invariant) | สถานะ | กลไกที่ใช้รับรอง |
| :--- | :---: | :--- |
| **Domain Purity** | ผ่าน | `lamaru.domain` ไม่มี Dependency ผูกกับภายนอก |
| **Secret Masking** | ผ่าน | `SecretReference` ปกป้องค่าดิบไม่ให้หลุดใน `repr/str/logs` |
| **No Hard-coded Fallback Chains** | ผ่าน | `ModelRouter` และ `FallbackEngine` ใช้ Dynamic Scoring และ Cooldown |
| **Fail-safe Tool Execution** | ผ่าน | มี Timeout และ Exception Handling กำกับทุก Tool ใน `ToolRegistry` |
| **Workspace Boundary Containment** | ผ่าน | `WorkspaceGuard` สกัดกั้น Directory Traversal 100% |
| **Destructive Command Guard** | ผ่าน | `CommandGuard` สกัด Regex คำสั่งเสี่ยงภัยก่อนถึง Shell |
| **Context Budget Safety** | ผ่าน | `ContextManager` มี Safety Margin และตัดทอน Context อัตโนมัติ |
| **Resilient State Persistence** | ผ่าน | `AgentStateManager` เขียน JSON ลงดิสก์ และป้องกัน Invalid State Transition |

---

## 6. แผนการพัฒนา Phase ถัดไป (Next Steps According to Master Plan)

* **Phase 16 — SQLite Persistence:** ย้ายการจัดเก็บจาก JSON ไปเป็น SQLite Database เพื่อรองรับประวัติขนาดใหญ่และดัชนีการค้นหา
* **Phase 17 — MCP (Model Context Protocol) Integration:** รองรับการเชื่อมต่อเครื่องมือภายนอกผ่าน MCP Server
* **Phase 18 — VS Code Extension:** พัฒนา Extension เชื่อมต่อกับ Local Gateway ผ่าน OpenAI API และ SSE Stream
