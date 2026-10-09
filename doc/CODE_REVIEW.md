# เอกสารการตรวจทานโค้ด (Code Review Document) — Lamaru AI Gateway

> **โปรเจกต์:** Lamaru AI Gateway and Agent Runtime  
> **เวอร์ชันปัจจุบัน:** `0.1.0`  
> **ความคืบหน้าการพัฒนา:** ผ่าน Phase 0 ถึง Phase 6 (Unit Tests ผ่าน 45/45, 1 skipped)  
> **วันที่บันทึกเอกสาร:** 9 ตุลาคม 2026  
> **ขอบเขตการรีวิว:** ซอร์สโค้ดทั้งหมดภายในโฟลเดอร์ `src/lamaru/`

---

## 1. ภาพรวมของโปรเจกต์ (Project Overview)

**Lamaru AI Gateway** เป็นระบบ Gateway และ Agent Runtime ท้องถิ่น (Local AI Gateway & Runtime) ออกแบบมาเพื่อเป็นตัวกลางเชื่อมต่อระหว่าง IDE/Client (เช่น VS Code Extension) กับ AI Model Providers หลากหลายค่าย (เช่น Google Gemini, Local Models) 

### วัตถุประสงค์หลักของระบบ:
1. **Multi-Provider & Multi-Model:** เชื่อมต่อหลายค่ายและหลายโมเดลผ่าน Interface มาตรฐานชุดเดียวกัน
2. **Multi-Key Management & Secret Protection:** รองรับการใส่ API Keys หลายคีย์ต่อหนึ่ง Provider เพื่อสลับการใช้งาน ป้องกัน Rate Limit และป้องกันไม่ให้ Secret รั่วไหลใน Log หรือ UI
3. **State Preservation:** เก็บสถานะบริบทของ Agent (`AgentState`) อย่างอิสระ ช่วยให้สามารถสลับโมเดลกลางคันได้โดย Context ไม่สูญหาย
4. **Standardized Error Classification & Policy:** จำแนก Error ของทุก Provider ออกมาเป็น 10 หมวดหมู่มาตรฐาน พร้อมระบุ Action อัตโนมัติ (เช่น Retry, สลับ Key, สลับ Provider)

---

## 2. โครงสร้างโฟลเดอร์และไฟล์ (Source Tree Structure)

```text
src/lamaru/
├── __init__.py                # รูทแพ็กเกจ Lamaru และระบุเวอร์ชัน 0.1.0
├── domain/                    # โดเมนโมเดล สัญญาข้อมูล (Contracts) และ Abstract Interfaces
│   ├── __init__.py
│   ├── agent.py               # State machine, Plan, และ AgentState
│   ├── errors.py              # ErrorCategory (10 หมวด) และ LamaruError
│   ├── messages.py            # AIRequest, AIResponse, Message, ToolCall, Usage
│   ├── models.py              # Normalized Model, ModelCapability, TaskRequirement
│   ├── provider.py            # AIProvider (Abstract Base Class) และ ProviderHealth
│   └── tools.py               # Tool (ABC), ToolDefinition, ToolResult, PermissionLevel
├── errors/                    # ระบบจำแนก Error และ Error Handling Policies
│   ├── __init__.py
│   ├── classifier.py          # ErrorClassifier Engine และ Factory สร้าง Fake Responses
│   └── policy.py              # ErrorPolicy, ErrorAction, DEFAULT_POLICIES (10 หมวด)
├── keys/                      # ระบบจัดการ API Key Pool และความปลอดภัยของ Secrets
│   ├── __init__.py
│   ├── manager.py             # APIKeyManager (Pool, Rotation, Cooldown, Env Discovery)
│   └── models.py              # SecretReference (Redaction), APIKeyMetadata, KeyStatus
├── models/                    # ระบบค้นหา จัดการ และ Overrides ข้อมูลโมเดล
│   ├── __init__.py
│   └── registry.py            # ModelRegistry (Catalog, Capability Filtering, Sync)
└── providers/                 # ตัวเชื่อมต่อ Provider จริงและ Mock
    ├── __init__.py
    ├── mock.py                # MockProvider สำหรับ Unit Testing & Simulation
    ├── registry.py            # ProviderRegistry (รวบรวม Provider, Health Check, Lifecycle)
    └── google/                # Adapter สำหรับ Google Gemini API
        ├── __init__.py
        ├── errors.py          # Gemini Error Parser & Network Exception Mapper
        ├── mapper.py          # แปลง Payload ไป-กลับ ระหว่าง Lamaru Domain กับ Gemini REST
        └── provider.py        # GoogleProvider (list_models, generate, stream, health_check)
```

---

## 3. เจาะลึกรายโมดูล: หน้าที่, ฟังก์ชัน, พารามิเตอร์ และการคืนค่า

---

### โมดูล 1: `lamaru.domain` (Domain Core)

โฟลเดอร์นี้เป็นศูนย์กลางของ Business Models และ Core Abstractions ทั้งหมด โดยไม่มี dependency ผูกมัดกับภายนอก

#### 1.1 `lamaru.domain.models`
ทำหน้าที่กำหนดนิยามของ AI Model และสเปกความต้องการของงาน (Task Requirements)

* **`ModelCapability(str, Enum)`**
  * ค่านิยามความสามารถของโมเดล: `CODING`, `REASONING`, `TOOL_CALLING`, `VISION`, `LONG_CONTEXT`, `RESEARCH`, `FAST`
* **`Model(BaseModel)`**
  * ข้อมูลโมเดลที่ผ่านการ Normalize แล้ว ประกอบด้วย:
    * `provider: str` — ชื่อผู้ให้บริการ เช่น `"google"`
    * `model_id: str` — รหัสโมเดล เช่น `"gemini-2.5-flash"`
    * `display_name: str` — ชื่อที่แสดงผลให้อ่านเข้าใจง่าย
    * `capabilities: Set[ModelCapability]` — ชุดความสามารถที่โมเดลรองรับ
    * `context_window: int` — ขนาดบริบทสูงสุด (Tokens) ต้อง > 0
    * `priority: int` — คะแนนลำดับความสำคัญในการเลือก Route (ค่ามาก = เลือกก่อน)
    * `stability: str` — ระดับเสถียรภาพ เช่น `"stable"`, `"experimental"`
    * `enabled: bool` — เปิด/ปิด การใช้งานโมเดลนี้
  * **ฟังก์ชันในคลาส:**
    * **`has_capability(capability: ModelCapability) -> bool`**
      * **หน้าที่:** ตรวจสอบว่าโมเดลนี้มีความสามารถที่ระบุหรือไม่
      * **รับค่า:** `capability (ModelCapability)`
      * **ส่งค่ากลับ:** `bool` (True หากโมเดลมี capability นั้น)
* **`TaskRequirement(BaseModel)`**
  * สเปกความต้องการที่ Agent วิเคราะห์ได้จากงานของผู้ใช้:
    * `task_type: str` (default: `"general_chat"`)
    * `complexity: str` (default: `"medium"`)
    * `requires_tools: bool` (default: False)
    * `requires_reasoning: bool` (default: False)
    * `requires_long_context: bool` (default: False)
    * `required_capabilities: Set[ModelCapability]`

---

#### 1.2 `lamaru.domain.messages`
กำหนดโครงสร้างข้อความ Request, Response, Streaming Chunk และ Token Usage

* **`MessageRole(str, Enum)`**: บทบาทผู้ส่งข้อความ (`SYSTEM`, `USER`, `ASSISTANT`, `TOOL`)
* **`ToolCall(BaseModel)`**: ข้อมูลการเรียก Tool ที่โมเดลร้องขอ (`id: str`, `name: str`, `arguments: Dict[str, Any]`)
* **`Message(BaseModel)`**: ข้อความเดี่ยวในประวัติการสนทนา (`role`, `content`, `name`, `tool_call_id`, `tool_calls`)
* **`Usage(BaseModel)`**: สถิติ Token (`input_tokens`, `output_tokens`, `total_tokens`)
* **`FinishReason(str, Enum)`**: สาเหตุที่การสร้างข้อความสิ้นสุด (`STOP`, `TOOL_CALLS`, `LENGTH`, `CONTENT_FILTER`, `ERROR`)
* **`AIRequest(BaseModel)`**: Request มาตรฐานที่ส่งให้ Provider
  * `messages: List[Message]` — ประวัติและข้อความ
  * `model: str` — ชื่อโมเดลเป้าหมาย
  * `provider: Optional[str]` — ชื่อ Provider (ถ้าต้องการเจาะจง)
  * `key_id: Optional[str]` — คีย์ที่ต้องการใช้งาน
  * `temperature: float` — ค่าอุณหภูมิความคิดสร้างสรรค์ (0.0 ถึง 2.0, default: 0.7)
  * `max_tokens: Optional[int]` — ลิมิตจำนวน token ขาออก
  * `tools: List[ToolDefinition]` — รายการ Tool ที่อนุญาตให้โมเดลเรียกใช้
  * `stream: bool` — เปิด/ปิด โหมด Streaming (default: False)
  * `system_prompt: Optional[str]` — System Instruction เพิ่มเติม
* **`AIResponse(BaseModel)`**: ผลลัพธ์มาตรฐานที่ได้กลับมาจาก Provider
  * `provider: str`, `model: str`, `content: Optional[str]`, `tool_calls: List[ToolCall]`, `finish_reason: FinishReason`, `usage: Usage`, `latency_ms: float`, `raw_error_category: Optional[ErrorCategory]`
* **`AIStreamChunk(BaseModel)`**: ก้อนข้อมูลที่ส่งมาแบบสตรีมมิ่ง (`content_delta: str`, `tool_calls: List[ToolCall]`, `finish_reason: Optional[FinishReason]`, `usage: Optional[Usage]`)

---

#### 1.3 `lamaru.domain.tools`
กำหนดสเปกของ Tools และ Abstract Class สำหรับเขียนเครื่องมือขยายขีดความสามารถ

* **`PermissionLevel(str, Enum)`**: ระดับความปลอดภัย (`ALLOWED`, `CONFIRMATION_REQUIRED`, `BLOCKED`)
* **`ToolDefinition(BaseModel)`**: สเปก metadata ของ Tool (`name`, `description`, `input_schema: Dict[str, Any]`, `permission`, `timeout_seconds`)
* **`ToolResult(BaseModel)`**: ผลลัพธ์จากการรันเครื่องมือ (`success: bool`, `output: Any`, `error: Optional[str]`, `metadata: Dict[str, Any]`)
* **`Tool(ABC)`**
  * คลาสแม่สำหรับ Tool ทุกตัวในระบบ
  * **Properties & Methods:**
    * **`definition -> ToolDefinition`** (Abstract Property) — คืนค่าสเปกของเครื่องมือ
    * **`name -> str`** (Property) — คืนค่าชื่อของเครื่องมือจากนิยาม
    * **`async execute(input_args: Dict[str, Any]) -> ToolResult`** (Abstract Method)
      * **หน้าที่:** รันตรรกะของเครื่องมือตาม Arguments ที่ได้รับ
      * **รับค่า:** `input_args (Dict[str, Any])` — อาร์กิวเมนต์ที่ผ่านการ validate ตาม schema
      * **ส่งค่ากลับ:** `ToolResult`

---

#### 1.4 `lamaru.domain.errors`
กำหนดหมวดหมู่ข้อผิดพลาดมาตรฐาน และ Exception กลาง

* **`ErrorCategory(str, Enum)`**: ข้อผิดพลาดทั้ง 10 ประเภท
  1. `AUTH_ERROR` (Authentication/API Key ผิดพลาด)
  2. `RATE_LIMIT` (คำขอเกินต่อนาที 429 RPM/TPM)
  3. `QUOTA_EXCEEDED` (โควต้ารายวัน/เดือนหมด)
  4. `TEMPORARY_SERVER_ERROR` (เซิร์ฟเวอร์ปลายทางขัดข้อง 500, 502, 503, 504)
  5. `MODEL_NOT_FOUND` (ไม่พบโมเดล 404)
  6. `INVALID_REQUEST` (พารามิเตอร์ผิด 400)
  7. `CONTEXT_TOO_LARGE` (Context ยาวเกินรองรับ)
  8. `SAFETY_BLOCK` (ถูกกรองด้วย Safety Filter)
  9. `NETWORK_ERROR` (การเชื่อมต่อหลุด/Timeout)
  10. `UNKNOWN` (ไม่สามารถระบุได้)
  * **Properties ใน Enum:**
    * **`is_retryable -> bool`**: เป็นจริงเมื่อเป็น `RATE_LIMIT`, `TEMPORARY_SERVER_ERROR`, `NETWORK_ERROR`
    * **`is_permanent -> bool`**: เป็นจริงเมื่อเป็น `AUTH_ERROR`, `MODEL_NOT_FOUND`, `INVALID_REQUEST`, `SAFETY_BLOCK`
* **`LamaruError(Exception)`**
  * Domain Exception หลักของโปรเจกต์
  * **Constructor:**
    * `__init__(message: str, category: ErrorCategory = UNKNOWN, provider: Optional[str] = None, model: Optional[str] = None, key_id: Optional[str] = None, details: Optional[dict[str, Any]] = None)`
  * **ฟังก์ชันในคลาส:**
    * **`to_dict() -> dict[str, Any]`**
      * **หน้าที่:** แปลง Exception ออกมาเป็น Dictionary ปลอดภัย (เหมาะสำหรับ Structured Logging และ API Response)

---

#### 1.5 `lamaru.domain.agent`
จัดการ Lifecycle, State Machine, แผนงาน (Plan) และสถานะของ Agent Session

* **`AgentStatus(str, Enum)`**: สถานะของ Agent
  * สถานะที่กำลังทำงาน: `CREATED`, `PLANNING`, `RUNNING`, `WAITING_TOOL`, `WAITING_USER`
  * สถานะสิ้นสุด (Terminal): `COMPLETED`, `FAILED`, `CANCELLED`
  * **Property:** `is_terminal -> bool` (คืนค่า True หากเข้าสู่สถานะสิ้นสุด)
* **`Plan(BaseModel)`**: แผนงาน
  * `steps: List[str]`, `current_step_index: int`
  * **Property:** `current_step -> Optional[str]` (คืนชื่อ Step ปัจจุบัน)
* **`AgentState(BaseModel)`**: State กลางที่บันทึกและถ่ายโอนได้
  * ประกอบด้วย Fields ครบถ้วน: `session_id`, `task`, `plan`, `current_step`, `messages`, `context_summary`, `relevant_files`, `tool_history`, `tool_results`, `errors`, `model_history`, `iteration_count`, `max_iterations`, `status`, `created_at`, `updated_at`
  * **ฟังก์ชันในคลาส:**
    * **`transition_to(new_status: AgentStatus) -> None`**
      * **หน้าที่:** เปลี่ยนสถานะของ Agent พร้อมตรวจสอบความถูกต้อง ป้องกันการเปลี่ยนสถานะซ้ำหลังจากเข้าสู่ Terminal State ไปแล้ว
      * **รับค่า:** `new_status (AgentStatus)`
      * **ส่งค่ากลับ:** `None` (โยน `ValueError` ถ้าปัจจุบันเป็น terminal อยู่แล้ว)
    * **`to_json() -> str`**
      * **หน้าที่:** แปลง AgentState ทั้งหมดเป็นข้อความ JSON แบบ Indented
      * **ส่งค่ากลับ:** `str`
    * **`from_json(cls, json_str: str) -> AgentState`** (Class Method)
      * **หน้าที่:** กู้คืน AgentState จากข้อความ JSON
      * **รับค่า:** `json_str (str)`
      * **ส่งค่ากลับ:** `AgentState`

---

#### 1.6 `lamaru.domain.provider`
กำหนดสัญญาการเชื่อมต่อ (Contract) สำหรับ AI Provider ทุกตัว

* **`ProviderHealth(BaseModel)`**: ผลการตรวจวัดสุขภาพ Provider (`provider: str`, `status: str`, `latency_ms: float`, `message: Optional[str]`, `checked_at: datetime`)
  * **Property:** `is_healthy -> bool` (status == "healthy")
* **`AIProvider(ABC)`**: Abstract Interface บังคับการทำงานของ Provider Adapter
  * **Properties:**
    * `provider_id -> str` (Abstract) — คืนค่า ID เช่น `"google"`, `"mock"`
    * `is_enabled -> bool` (Abstract) — สถานะการเปิดใช้งาน
  * **ฟังก์ชันในคลาส (Abstract Methods):**
    * **`async list_models() -> List[Model]`**: เรียกรายชื่อโมเดลทั้งหมดที่รองรับ
    * **`async get_model(model_id: str) -> Optional[Model]`**: ดึงข้อมูลโมเดลเฉพาะเจาะจง
    * **`async generate(request: AIRequest) -> AIResponse`**: รันการตอบกลับแบบสมบูรณ์รอบเดียว
    * **`async stream(request: AIRequest) -> AsyncIterator[AIStreamChunk]`**: สตรีมผลลัพธ์แบบ Chunk
    * **`async health_check() -> ProviderHealth`**: ตรวจสอบการเชื่อมต่อและวัด Latency

---

### โมดูล 2: `lamaru.errors` (Error Classifier & Policy)

รับผิดชอบการดักจับข้อผิดพลาด จัดกลุ่มให้อยู่ใน 10 หมวดหมู่ และระบุกลยุทธ์การแก้ไขปัญหา (Action)

#### 2.1 `lamaru.errors.policy`
* **`ErrorAction(str, Enum)`**: ทางเลือกการแก้ไขปัญหา:
  * `RETRY_WITH_BACKOFF` — ลองส่งคำขอใหม่พร้อมหน่วงเวลา
  * `SWITCH_KEY` — สลับไปใช้ API Key อื่นใน Pool
  * `SWITCH_MODEL` — สลับไปใช้ Model อื่นที่ใกล้เคียงกัน
  * `SWITCH_PROVIDER` — สลับค่าย Provider
  * `REDUCE_CONTEXT` — ตัดทอนหรือสรุป Context ให้สั้นลง
  * `DISABLE_KEY` — ปิดการใช้งาน Key นั้น (เช่น กรณี Key ไม่ถูกต้อง)
  * `FAIL` — ยุติและรายงานความผิดพลาดทันที
* **`ErrorPolicy(BaseModel)`**: กฎควบคุมข้อผิดพลาดแต่ละประเภท กำหนดค่า `is_retryable`, `is_permanent`, `requires_key_rotation`, `requires_model_rotation`, `requires_provider_rotation`, `requires_context_reduction`, `default_cooldown_seconds`, `max_retry_attempts`, `suggested_action`
* **`DEFAULT_POLICIES: Dict[ErrorCategory, ErrorPolicy]`**: ตารางแมปทั้ง 10 ประเภทข้อผิดพลาดเข้ากับ Policy เริ่มต้นตามสถาปัตยกรรม

#### 2.2 `lamaru.errors.classifier`
* **`ErrorClassifier`**: เครื่องมือวิเคราะห์ Error
  * **ฟังก์ชันในคลาส:**
    * **`__init__(custom_policies: Optional[Dict[ErrorCategory, ErrorPolicy]] = None)`**
      * โหลด `DEFAULT_POLICIES` และรวม Policy กำหนดเองเข้าด้วยกัน
    * **`get_policy(category: ErrorCategory) -> ErrorPolicy`**
      * **รับค่า:** `category (ErrorCategory)`
      * **ส่งค่ากลับ:** `ErrorPolicy` ที่ผูกกับหมวดหมู่นั้น
    * **`parse_retry_after(headers: Optional[Dict[str, str]]) -> Optional[float]`**
      * **หน้าที่:** ดึงค่าตัวเลขวินาทีจาก Header `Retry-After` (แบบ Case-Insensitive)
      * **รับค่า:** `headers (Optional[Dict[str, str]])`
      * **ส่งค่ากลับ:** `Optional[float]` (จำนวนวินาที หรือ `None` หากไม่มี)
    * **`classify_http(status_code: int, response_body: Optional[Any] = None, headers: Optional[Dict[str, str]] = None, message: Optional[str] = None) -> ErrorClassificationResult`**
      * **หน้าที่:** วิเคราะห์ HTTP Status Code, เนื้อหา Body และ Header เพื่อจัดหมวดหมู่และคำนวณ Cooldown
      * **รับค่า:** 
        * `status_code: int`
        * `response_body: Optional[Any]` (Dict หรือ String ของ Error Body)
        * `headers: Optional[Dict[str, str]]`
        * `message: Optional[str]`
      * **ส่งค่ากลับ:** `ErrorClassificationResult`
    * **`classify_exception(exc: Exception) -> ErrorClassificationResult`**
      * **หน้าที่:** วิเคราะห์ Python Exception (ตรวจเช็คว่าเป็น `LamaruError`, ปัญหา Network/Timeout หรือปัญหา Auth)
      * **รับค่า:** `exc: Exception`
      * **ส่งค่ากลับ:** `ErrorClassificationResult`
* **`create_fake_error_response(category: ErrorCategory, retry_after_seconds: Optional[int] = None) -> Tuple[int, Dict[str, Any], Dict[str, str]]`**
  * **หน้าที่:** Factory สร้างข้อมูล HTTP Response จำลองที่สมจริงสำหรับทดสอบระบบครอบคลุมทั้ง 10 หมวดหมู่
  * **รับค่า:** `category (ErrorCategory)`, `retry_after_seconds (Optional[int])`
  * **ส่งค่ากลับ:** `Tuple[status_code, json_body, headers]`

---

### โมดูล 3: `lamaru.keys` (API Key Manager & Secret Protection)

รับผิดชอบการจัดการ API Key หลายตัวต่อ Provider, คูลดาวน์ (Cooldown), ติดตามความสำเร็จ/ล้มเหลว และซ่อน Secret ป้องกันการรั่วไหล

#### 3.1 `lamaru.keys.models`
* **`KeyStatus(str, Enum)`**: สถานะของ Key (`HEALTHY`, `COOLDOWN`, `DISABLED`, `QUOTA_EXHAUSTED`, `AUTH_FAILED`)
* **`SecretReference`**:
  * Wrapper ครอบ Secret ไม่ให้หลุดออกมาทาง `__repr__`, `__str__` หรือ Log เด็ดขาด (จะแสดงผลเป็น `***REDACTED***` หรือ `(protected)`)
  * **ฟังก์ชันในคลาส:**
    * **`from_env(cls, env_var: str) -> SecretReference`** (Class Method): สร้างตัวอ้างอิงจากตัวแปรสภาพแวดล้อม (Environment Variable)
    * **`from_raw(cls, secret: str, label: str = "in-memory") -> SecretReference`** (Class Method): สร้างตัวอ้างอิงจาก Plain Secret โดยตรง
    * **`resolve() -> str`**: ดึง Plain Secret ออกมาเมื่อถึงเวลาเชื่อมต่อจริง
* **`APIKeyMetadata(BaseModel)`**:
  * บันทึกข้อมูลเมทาดาทาของ Key แต่ละตัว (`key_id`, `provider`, `ref_string`, `enabled`, `status`, `success_count`, `failure_count`, `cooldown_until`, `last_error`, `last_used_at`)
  * **ฟังก์ชันในคลาส:**
    * **`resolve_secret() -> str`**: ปลดล็อกข้อความ Secret เพื่อนำไปใช้ยิง API
    * **`is_available(now: Optional[datetime] = None) -> bool`**: ตรวจสอบว่าพร้อมใช้งานหรือไม่ ถ้าอยู่ในสถานะ COOLDOWN แต่พ้นเวลาที่กำหนดแล้ว จะปรับสถานะกลับเป็น HEALTHY ให้อัตโนมัติ
    * **`set_cooldown(seconds: float, now: Optional[datetime] = None) -> None`**: ตั้งค่าให้คีย์เข้าสู่ Cooldown เป็นเวลาที่กำหนด
    * **`record_success(now: Optional[datetime] = None) -> None`**: บันทึกสถิติเมื่อเรียกใช้สำเร็จ และปลดสถานะ Cooldown
    * **`record_failure(error_msg: str, cooldown_seconds: Optional[float] = None, now: Optional[datetime] = None) -> None`**: บันทึกสถิติความล้มเหลว และตั้งเวลา Cooldown หากระบุ
    * **`to_safe_dict() -> Dict[str, Any]`**: แปลงข้อมูลเป็น Dict สำหรับแสดงผล ปราศจาก Plain Secret 100%

#### 3.2 `lamaru.keys.manager`
* **`APIKeyManager`**: ตัวจัดการ Pool คีย์กลาง
  * **ฟังก์ชันในคลาส:**
    * **`register_key(key: APIKeyMetadata, overwrite: bool = False) -> None`**
      * **หน้าที่:** ลงทะเบียน API Key เข้าสู่ระบบ (ป้องกันการลงทะเบียน ID ซ้ำถ้า overwrite=False)
      * **รับค่า:** `key (APIKeyMetadata)`, `overwrite (bool)`
    * **`unregister_key(key_id: str) -> Optional[APIKeyMetadata]`**
      * **หน้าที่:** ถอดถอนคีย์ออกจากระบบ
      * **รับค่า:** `key_id (str)`
      * **ส่งค่ากลับ:** `APIKeyMetadata` ที่ถูกนำออก หรือ `None`
    * **`get_key(key_id: str) -> Optional[APIKeyMetadata]`**: ดึงเมทาดาทาของคีย์ตาม ID
    * **`get_secret(key_id: str) -> Optional[str]`**: ดึง Plain Secret สำหรับ Authorization
    * **`list_keys(provider: Optional[str] = None, enabled_only: bool = False) -> List[APIKeyMetadata]`**: แสดงรายการคีย์ โดยเลือกกรองตาม Provider หรือเฉพาะที่ Enabled ได้
    * **`get_available_keys(provider: str, now: Optional[datetime] = None) -> List[APIKeyMetadata]`**: คืนค่าเฉพาะคีย์ที่พร้อมใช้งาน (ไม่ติด Cooldown/Disabled)
    * **`record_key_success(key_id: str) -> None`**: บันทึกผลสำเร็จให้แก่คีย์
    * **`record_key_failure(key_id: str, error_msg: str, cooldown_seconds: Optional[float] = None, now: Optional[datetime] = None) -> None`**: บันทึกผลความล้มเหลว
    * **`load_from_env(provider: str, prefix: str = "GEMINI_API_KEY") -> int`**
      * **หน้าที่:** สแกนหา Environment Variables อัตโนมัติ ทั้งแบบหลายคีย์ (เช่น `GEMINI_API_KEY_01`, `GEMINI_API_KEY_02`) และแบบคีย์เดี่ยว (`GEMINI_API_KEY`)
      * **รับค่า:** `provider (str)`, `prefix (str)`
      * **ส่งค่ากลับ:** `int` (จำนวนคีย์ที่ค้นพบและลงทะเบียนสำเร็จ)

---

### โมดูล 4: `lamaru.models` (Model Registry)

ทำหน้าที่เป็นแค็ตตาล็อกเก็บข้อมูลโมเดลทั้งหมด ค้นหาตามความสามารถ ปรับแต่งการ Overwrite และซิงก์โมเดลจาก Provider

#### 4.1 `lamaru.models.registry`
* **`ModelRegistry`**:
  * **ฟังก์ชันในคลาส:**
    * **`register(model: Model, overwrite: bool = False) -> None`**
      * **หน้าที่:** บันทึกโมเดลเข้าแค็ตตาล็อก (คีย์จัดเก็บคือ `(provider, model_id)`)
      * **รับค่า:** `model (Model)`, `overwrite (bool)`
    * **`unregister(provider: str, model_id: str) -> Optional[Model]`**
      * **หน้าที่:** ลบโมเดลออกจาก Registry
      * **รับค่า:** `provider (str)`, `model_id (str)`
    * **`get(provider: str, model_id: str) -> Optional[Model]`**
      * **หน้าที่:** ดึงข้อมูลโมเดล พร้อมนำการตั้งค่า Overrides มาผสานผลลัพธ์
      * **รับค่า:** `provider (str)`, `model_id (str)`
      * **ส่งค่ากลับ:** `Optional[Model]`
    * **`get_by_id(model_id: str) -> Optional[Model]`**
      * **หน้าที่:** ค้นหาโมเดลแรกที่ตรงกับ ID ข้ามทุก Provider โดยอัตโนมัติตัด Prefix `models/` ให้
      * **รับค่า:** `model_id (str)`
    * **`list_models(enabled_only: bool = True, provider: Optional[str] = None) -> List[Model]`**
      * **หน้าที่:** เรียกดูรายการโมเดลทั้งหมดตามเงื่อนไขที่กำหนด
      * **รับค่า:** `enabled_only (bool)`, `provider (Optional[str])`
      * **ส่งค่ากลับ:** `List[Model]`
    * **`find_by_capabilities(capabilities: Set[ModelCapability], enabled_only: bool = True) -> List[Model]`**
      * **หน้าที่:** ค้นหาโมเดลที่มีความสามารถครบตาม Subset ที่ระบุ
      * **รับค่า:** `capabilities (Set[ModelCapability])`, `enabled_only (bool)`
      * **ส่งค่ากลับ:** `List[Model]`
    * **`filter_candidates(requirement: TaskRequirement, min_context_window: Optional[int] = None, enabled_only: bool = True) -> List[Model]`**
      * **หน้าที่:** คัดกรองโมเดลที่ตอบโจทย์สเปกงาน `TaskRequirement` (เช่น ต้องการ Tool Calling, Reasoning, Long Context) และผ่านเกณฑ์ Context Window ขั้นต่ำ
      * **รับค่า:** `requirement (TaskRequirement)`, `min_context_window (Optional[int])`, `enabled_only (bool)`
      * **ส่งค่ากลับ:** `List[Model]`
    * **`set_override(provider: str, model_id: str, enabled: Optional[bool] = None, priority: Optional[int] = None, context_window: Optional[int] = None) -> None`**
      * **หน้าที่:** ปรับแต่งค่าของโมเดลเป็นการเฉพาะโดยไม่ต้องแก้ข้อมูลดิบจาก Provider (เช่น บังคับเปลี่ยน Priority หรือ Context Window)
      * **รับค่า:** `provider (str)`, `model_id (str)`, `enabled`, `priority`, `context_window`
    * **`clear_override(provider: str, model_id: str) -> None`**
      * **หน้าที่:** ล้างค่า Override ของโมเดลนั้นทิ้ง
    * **`async sync_from_providers(provider_registry: ProviderRegistry, enabled_only: bool = True) -> int`**
      * **หน้าที่:** สอบถามรายชื่อโมเดลจาก Provider ทุกตัวที่เปิดใช้งานผ่าน `ProviderRegistry` และอัปเดตลงในแค็ตตาล็อกอัตโนมัติ
      * **รับค่า:** `provider_registry (ProviderRegistry)`, `enabled_only (bool)`
      * **ส่งค่ากลับ:** `int` (จำนวนโมเดลที่ซิงก์สำเร็จ)

---

### โมดูล 5: `lamaru.providers` (Provider Implementations & Registry)

จัดการ Lifecycle ของ Provider และสร้าง Adapter เชื่อมต่อกับ Provider จริงและ Mock

#### 5.1 `lamaru.providers.registry`
* **`ProviderRegistry`**: ศูนย์รวม Provider Adapters ทั้งหมด
  * **ฟังก์ชันในคลาส:**
    * **`register(provider: AIProvider, overwrite: bool = False) -> None`**: ลงทะเบียน Adapter
    * **`unregister(provider_id: str) -> Optional[AIProvider]`**: ยกเลิกการลงทะเบียน
    * **`get(provider_id: str) -> Optional[AIProvider]`**: ค้นหา Provider ตาม ID
    * **`list_providers(enabled_only: bool = False) -> List[AIProvider]`**: แสดงรายการ Providers
    * **`async list_all_models(enabled_only: bool = True) -> List[Model]`**
      * **หน้าที่:** รวบรวมโมเดลจาก Provider ทุกตัวมารวมกันเป็นรายการเดียว
      * **ส่งค่ากลับ:** `List[Model]`
    * **`async check_health_all() -> Dict[str, ProviderHealth]`**
      * **หน้าที่:** ยิงคำขอทดสอบสุขภาพ (Health Check) ไปยัง Provider ทุกตัวพร้อมกัน
      * **ส่งค่ากลับ:** `Dict[str, ProviderHealth]`
    * **`async initialize_all() -> None`**: สั่งรันฟังก์ชัน `initialize()` ของทุก Provider (ถ้ามี)
    * **`async shutdown_all() -> None`**: สั่งรันฟังก์ชัน `shutdown()` เพื่อปิด Connection ของทุก Provider ให้เรียบร้อย

---

#### 5.2 `lamaru.providers.mock`
* **`MockProvider(AIProvider)`**: Mock จำลอง Provider สำหรับใช้เขียน Unit Tests โดยไม่ต้องต่ออินเทอร์เน็ตหรือเสียค่า API
  * **Constructor:** `__init__(provider_id="mock", enabled=True, models=None, default_response="Mock generation response", latency_ms=1.0)`
  * **ฟังก์ชันในคลาส:**
    * **`set_enabled(enabled: bool) -> None`**: สลับสถานะเปิด/ปิด
    * **`add_model(model: Model) -> None`**: เพิ่มโมเดลเข้า Mock
    * **`set_simulated_error(error: Optional[LamaruError]) -> None`**: จำลองการเกิด Error เพื่อทดสอบการรับมือของระบบ
    * **`clear_simulated_error() -> None`**: เคลียร์ Error จำลองออก
    * **`set_health_status(status: str) -> None`**: เปลี่ยนสถานะจำลอง (healthy, unhealthy)
    * **`async list_models() -> List[Model]`**: คืนค่ารายการโมเดลจำลอง
    * **`async get_model(model_id: str) -> Optional[Model]`**: ค้นหาโมเดลจำลอง
    * **`async generate(request: AIRequest) -> AIResponse`**: ตอบกลับข้อความแบบ Mock พร้อมคำนวณ Token สถิติ
    * **`async stream(request: AIRequest) -> AsyncIterator[AIStreamChunk]`**: จำลองการส่ง Streaming Chunk ทีละคำ
    * **`async health_check() -> ProviderHealth`**: ส่งผลลัพธ์ Health Check จำลอง

---

#### 5.3 `lamaru.providers.google` (Google Gemini Adapter)

##### ก. `lamaru.providers.google.mapper`
ทำหน้าที่แปลง Data Structure ระหว่าง Lamaru Domain กับ JSON Payload ของ Google Gemini REST API

* **`build_gemini_payload(request: AIRequest) -> Dict[str, Any]`**
  * **หน้าที่:** แปลง `AIRequest` ของ Lamaru เป็น JSON สำหรับ Endpoint `generateContent`
  * **รายละเอียดการแปลง:**
    * จัดการ `system_instruction` จาก Request หรือจาก Message ที่มี Role เป็น `SYSTEM`
    * แปลง Message บทบาท `USER` -> `role: "user"`
    * แปลง Message บทบาท `ASSISTANT` -> `role: "model"` (รวมถึง `functionCall`)
    * แปลง Message บทบาท `TOOL` -> `role: "function"` พร้อมบรรจุ `functionResponse`
    * แมปพารามิเตอร์ `temperature`, `maxOutputTokens`
    * แมปรายการ Tools เป็น `functionDeclarations`
  * **รับค่า:** `request (AIRequest)`
  * **ส่งค่ากลับ:** `Dict[str, Any]`
* **`parse_gemini_candidate(candidate: Dict[str, Any], model: str) -> Tuple[Optional[str], List[ToolCall], FinishReason]`**
  * **หน้าที่:** แยกส่วน Text, Tool Calls และ Finish Reason ออกจาก Candidate ของ Gemini
  * **การจัดการข้อผิดพลาด:** หาก finishReason คือ `"SAFETY"` จะโยน `LamaruError(ErrorCategory.SAFETY_BLOCK)` ทันที
  * **รับค่า:** `candidate (Dict[str, Any])`, `model (str)`
  * **ส่งค่ากลับ:** `Tuple[text, tool_calls, finish_reason]`
* **`parse_gemini_response(response_data: Dict[str, Any], model: str, latency_ms: float = 0.0) -> AIResponse`**
  * **หน้าที่:** แปลง Response JSON เต็มรูปแบบของ Gemini ออกมาเป็น `AIResponse` พร้อมสกัด Token Usage จาก `usageMetadata`
  * **รับค่า:** `response_data (Dict[str, Any])`, `model (str)`, `latency_ms (float)`
  * **ส่งค่ากลับ:** `AIResponse`
* **`parse_gemini_model(model_data: Dict[str, Any]) -> Model`**
  * **หน้าที่:** แปลง Descriptor จาก Gemini `listModels` API ให้กลายเป็น `Model` มาตรฐานของระบบ
  * **การอนุมาน Capability อัตโนมัติ:**
    * ถ้าชื่อมีคำว่า `flash` หรือ `fast` -> เพิ่ม `ModelCapability.FAST` และตั้ง Priority = 10
    * ถ้าชื่อมีคำว่า `pro` -> เพิ่ม `ModelCapability.REASONING` และตั้ง Priority = 5
    * ถ้า `inputTokenLimit` >= 100,000 -> เพิ่ม `ModelCapability.LONG_CONTEXT`
  * **รับค่า:** `model_data (Dict[str, Any])`
  * **ส่งค่ากลับ:** `Model`

##### ข. `lamaru.providers.google.errors`
* **`parse_gemini_error(status_code: int, error_data: Optional[Dict[str, Any]] = None, key_id: Optional[str] = None, model: Optional[str] = None) -> LamaruError`**
  * **หน้าที่:** แปลง HTTP Status Code และ JSON Error จาก Google Gemini ให้เป็น `LamaruError` ในหมวดหมู่ที่ถูกต้อง:
    * 401 / 403 -> `AUTH_ERROR`
    * 404 -> `MODEL_NOT_FOUND`
    * 429 -> `RATE_LIMIT`
    * 400 -> `CONTEXT_TOO_LARGE` (เมื่อพบข้อความ context/too large) หรือ `INVALID_REQUEST`
    * 500 / 502 / 503 / 504 -> `TEMPORARY_SERVER_ERROR`
  * **รับค่า:** `status_code (int)`, `error_data (Optional[Dict[str, Any]])`, `key_id (Optional[str])`, `model (Optional[str])`
  * **ส่งค่ากลับ:** `LamaruError`
* **`handle_network_error(exc: Exception, key_id: Optional[str] = None, model: Optional[str] = None) -> LamaruError`**
  * **หน้าที่:** ครอบ `httpx.RequestError` หรือ Network Failure ให้กลายเป็น `LamaruError(ErrorCategory.NETWORK_ERROR)`
  * **รับค่า:** `exc (Exception)`, `key_id`, `model`
  * **ส่งค่ากลับ:** `LamaruError`

##### ค. `lamaru.providers.google.provider`
* **`GoogleProvider(AIProvider)`**: Concrete Implementation สำหรับเชื่อมต่อกับ Google Gemini REST API
  * **Constructor:**
    * `__init__(api_key: Optional[str] = None, key_id: str = "google-key-01", base_url: str = "https://generativelanguage.googleapis.com/v1beta", client: Optional[httpx.AsyncClient] = None, enabled: bool = True, timeout_seconds: float = 60.0)`
  * **Internal Helper Methods:**
    * `_get_headers() -> dict[str, str]`: คืนค่า Headers รวมถึง `x-goog-api-key`
    * `_get_client() -> httpx.AsyncClient`: คืนค่า HTTP Async Client ที่เตรียมไว้ หรือสร้างขึ้นใหม่
  * **Implemented Interface Methods:**
    * **`async list_models() -> List[Model]`**
      * เรียก GET `https://.../models`
      * คัดกรองเฉพาะโมเดลที่รองรับ `generateContent`
      * แปลงเป็น `Model` ผ่าน `parse_gemini_model`
    * **`async get_model(model_id: str) -> Optional[Model]`**
      * ค้นหาโมเดลจากรายการที่ดึงมา
    * **`async generate(request: AIRequest) -> AIResponse`**
      * ส่งคำขอ POST `https://.../models/{model}:generateContent`
      * ดักจับ Error แปลงเป็น `LamaruError`
      * บันทึกเวลา Latency และแปลงคำตอบเป็น `AIResponse`
    * **`async stream(request: AIRequest) -> AsyncIterator[AIStreamChunk]`**
      * ส่งคำขอ POST `https://.../models/{model}:streamGenerateContent?alt=sse`
      * อ่าน Server-Sent Events (SSE) ทีละบรรทัด (`data: ...`)
      * Yield `AIStreamChunk` ออกมาทีละก้อนแบบ Asynchronous
    * **`async health_check() -> ProviderHealth`**
      * ตรวจสอบว่ามี API Key หรือไม่ และทดลองยิง `list_models()` ขั้นต่ำเพื่อตรวจสอบการเชื่อมต่อ

---

## 4. ผลการทดสอบ Unit Tests ปัจจุบัน (Test Coverage Report)

ผลการรันชุดทดสอบผ่าน `.venv/Scripts/pytest.exe -v`:
- **จำนวนการทดสอบทั้งหมด:** 46 รายการ
- **ผ่าน (Passed):** 45 รายการ
- **ข้าม (Skipped):** 1 รายการ (`test_google_provider_real_api_integration` ซึ่งถูกออกแบบให้รันเมื่อมีการระบุ API Key จริงในสภาพแวดล้อมทดสอบ)
- **ระยะเวลาการรัน:** ~0.24 วินาที

### สรุปผลการทดสอบแยกตามชุดทดสอบ:
1. `tests/test_agent_state.py` (2 tests): การเปลี่ยนสถานะของ State Machine, การ Serialize/Deserialize JSON ของ `AgentState`
2. `tests/test_api_key_manager.py` (6 tests): การลงทะเบียน Multi-key, การซ่อน Secret ปลอดภัย, การดึงคีย์จาก Env, การตั้ง Cooldown และการคืนชีพของคีย์
3. `tests/test_domain_validation.py` (7 tests): การ Validate ข้อมูล `Model`, `TaskRequirement`, `Message`, `AIRequest`, `ToolDefinition`, `LamaruError`
4. `tests/test_error_classifier.py` (3 tests): ตรวจสอบว่าครบทั้ง 10 หมวดหมู่, ทดสอบจำแนก Fake Response ทั้ง 10 แบบ, การประมวลผล Header `Retry-After`
5. `tests/test_google_provider.py` (8 tests): จำลองการทำงานของ Google Provider ทั้ง List Models, Prompt, Function Call, Streaming, 401, 404, 429, Safety Block, Health Check
6. `tests/test_mock_provider.py` (4 tests): ทดสอบฟังก์ชันของ `MockProvider`
7. `tests/test_model_registry.py` (6 tests): ค้นหาตามความสามารถ, การตั้งค่า Overrides, กรองโมเดลตาม `TaskRequirement`, การซิงก์จาก Provider
8. `tests/test_provider_interface.py` (2 tests): ยืนยันว่า Interface สอดคล้องตามสัญญาและไม่สามารถสร้าง Abstract Class เปล่าได้
9. `tests/test_provider_registry.py` (4 tests): การรวมศูนย์โมเดล, Health Check ทุกตัว, และ **Acceptance Criteria การเพิ่ม Provider C ได้โดยไม่กระทบส่วนอื่น**
10. `tests/test_smoke.py` (2 tests): Smoke test นำเข้าโมดูลและตรวจสอบความสมบูรณ์ของ Package

---

## 5. การปฏิบัติตามกฎสถาปัตยกรรม (Architecture Compliance Check)

| ข้อกำหนดตาม Master Plan | สถานะการทำงานในปัจจุบัน |
| :--- | :--- |
| **ห้ามมี Dependency ภายนอกใน Domain** | ผ่าน: `lamaru.domain` ใช้เพียง Pydantic, Python Standard Library |
| **ห้ามเปิดเผย Plain Secret ใน Logs/UI** | ผ่าน: `SecretReference` ปกป้องค่าใน `__repr__` และ `__str__` |
| **Multi-Key Cooldown และ Recovery** | ผ่าน: มีระบบสลับคีย์อัตโนมัติเมื่อเจอปัญหา และคืนสถานะเมื่อพ้น Cooldown |
| **การจำแนกข้อผิดพลาดครบ 10 หมวดหมู่** | ผ่าน: มีครบทั้ง 10 หมวดหมู่ และทุกหมวดหมู่มี Action/Policy กำกับชัดเจน |
| **Unit Tests ต้องไม่เรียก Network จริง** | ผ่าน: มี `MockProvider` และใช้ Mock Transport ในการทดสอบ Provider |
| **ความยืดหยุ่นในการเพิ่ม Provider ใหม่** | ผ่าน: ผ่าน Acceptance Criteria (เพิ่ม Provider C ได้โดย routing ไม่ต้องแก้ไข) |

---

## 6. แผนงานในลำดับถัดไป (Next Steps According to Master Plan)

ตามแผนแม่บทใน `LAMARU_PROJECT_PLAN.md`:
* **Phase 7 — Retry + Cooldown + Circuit Breaker:** ระบบควบคุม Backoff, Exponential Retry, Circuit Breaker ป้องกันระบบล่ม
* **Phase 8 — Model Router:** อัลกอริทึมเลือกโมเดลที่เหมาะสมที่สุดตาม Task, Cost, Priority และ Availability
* **Phase 9 — Context Manager:** ระบบคำนวณ Token Budget, จัดการประวัติบทสนทนา และบีบอัด Context
* **Phase 10 — Tool Runtime & Security:** รันเครื่องมือ File, Search, Terminal ภายใต้ระบบ Permission Tier
* **Phase 11 — SQLite Persistence:** บันทึก Agent State, Key Pool, Logs ลงฐานข้อมูล SQLite
* **Phase 12 — Local FastAPI (OpenAI Compatible):** เปิด REST API สำหรับ VS Code และภายนอก
