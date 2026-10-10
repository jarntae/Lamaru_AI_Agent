# Security Specification & Hardening Policy

**วันที่ตรวจสอบและอัปเดตล่าสุด:** 10 ตุลาคม 2026  
**สถานะการตรวจสอบ:** ผ่านการทดสอบความปลอดภัยครบถ้วน (99/99 Unit Tests Passed)  
**ขอบเขต:** สถาปัตยกรรมความปลอดภัยหลัก, การจัดการ API Key, การควบคุมสิทธิ์ของเครื่องมือ (Tool Security), การแยกสภาพแวดล้อมคำสั่ง (Subprocess Isolation), และการรักษาความปลอดภัยของ Gateway Server

---

## 1. Threat Model & Security Invariants

Lamaru AI Gateway ทำงานบนเครื่อง Local ของผู้ใช้ แต่มีหน้าที่จัดการทรัพยากรที่มีความอ่อนไหวสูง ได้แก่:
* API Keys และ Token ของผู้ให้บริการ AI (Google Gemini, OpenAI ฯลฯ)
* ซอร์สโค้ดและไฟล์ภายใน Workspace
* การรันคำสั่งเชลล์ (Shell Execution)
* การเข้าถึงและจัดการไฟล์ (Filesystem Access)
* การดำเนินงานเกี่ยวกับ Git (Git Operations)

### กฎความปลอดภัยพื้นฐาน (Security Invariants)
1. **Model instructions are untrusted:** ข้อความที่ส่งมาจากโมเดลหรือข้อมูลภายนอก (Web, Repository files, Tool Output) ต้องถือว่าเป็น Untrusted Data เสมอ ห้ามรันโดยตรงโดยไม่ผ่านการกลั่นกรอง
2. **Permission Authority:** โมเดลไม่สามารถ Override หรือเปลี่ยนแปลงระดับสิทธิ์ (Permission Tier) ของระบบรักษาความปลอดภัยได้
3. **Defense-in-Depth:** ระบบต้องมีมาตรการป้องกันหลายชั้น (Multi-layer Defense) ตั้งแต่ Filesystem Guard, Command Guard, Subprocess Scrubbing ไปจนถึง Output Redaction

---

## 2. API Key Management & Secret Storage Policy

### 2.1 ทำไมจึงไม่ใช้การ Hash ในการจัดเก็บ API Key
* **ฟังก์ชันทางเดียว (One-Way Cryptographic Function):** การ Hash (เช่น SHA-256, bcrypt) ไม่สามารถกู้คืนข้อมูลเดิมได้
* **บทบาทของ Lamaru เป็น Outbound Client:** Lamaru ต้องส่ง Plaintext Secret ตัวจริงไปยัง AI Provider ภายนอก (เช่น ส่ง `x-goog-api-key: AIzaSy...` ไปยัง Google Gemini) หากทำการ Hash คีย์ Lamaru จะไม่สามารถแปลงกลับเป็นคีย์จริงได้ ทำให้การเชื่อมต่อกับผู้ให้บริการภายนอกล้มเหลว 100%
* *(หมายเหตุ: การ Hash API Key จะใช้เฉพาะกรณี Inbound Client Authentication ที่ผู้ใช้ภายนอกส่งคีย์มาขอยืนยันตัวตนกับ Lamaru เท่านั้น)*

### 2.2 นโยบายการจัดเก็บ Secret (Secret Reference Pattern)
* **ห้ามบันทึก Raw Secret ในฐานข้อมูล SQLite หรือ Git Repository โดยเด็ดขาด:**
  ไฟล์ฐานข้อมูล SQLite (`lamaru.db`) เป็นไฟล์ Binary ธรรมดาที่เสี่ยงต่อการถูก dump ข้อมูลหรือเผลอคัดลอก/แชร์
* **มาตรฐานการจัดเก็บ:**
  1. **Development & Standard Mode:** จัดเก็บผ่าน Environment Variables (`.env`) ซึ่งจะถูกโหลดไว้ใน Process Memory เท่านั้น และได้รับการปกป้องผ่าน `.gitignore`
  2. **Production Vault Mode (ทางเลือกขั้นสูง):** จัดเก็บใน OS Credential Manager (เช่น Windows DPAPI, macOS Keychain, Linux Secret Service) ผ่าน Python `keyring`
* **SecretReference Implementation (`src/lamaru/keys/models.py`):**
  * คลาส `SecretReference` อ้างอิง Secret ผ่าน Reference String เช่น `env:GEMINI_API_KEY_01`
  * เมธอด `__repr__` และ `__str__` จะ Mask ค่า Secret เป็น `***REDACTED***` เสมอ เพื่อป้องกันไม่ให้คีย์หลุดไปใน Logs หรือ Exception Traces
  * `APIKeyMetadata.to_safe_dict()` จะแปลงข้อมูลเป็น Dictionary ปลอดภัยโดยไม่มี Secret ปนเปื้อน

---

## 3. Tool Security & Workspace Guard

### 3.1 Permission Tiers
ทุก Tool ในระบบถูกควบคุมสิทธิ์อย่างเข้มงวดผ่าน `ToolSecurityPolicy`:
* **ALLOWED (ทำงานได้อัตโนมัติ):** เครื่องมือที่ไม่อันตราย เช่น `read_file`, `list_directory`, `search_files`, `search_text`, `git_status`, `git_diff`, `git_log`
* **CONFIRMATION_REQUIRED (ต้องได้รับการยืนยันจาก User):** เครื่องมือที่อาจส่งผลกระทบ เช่น `delete_file`, `run_command`
* **BLOCKED (ห้ามทำงานโดยเด็ดขาด):** ปิดการใช้งานอย่างถาวรตาม Security Policy

### 3.2 การจำกัดขอบเขต Workspace และไฟล์คุ้มครอง (Protected Files)
[`WorkspaceGuard`](file:///C:/Poramet-SharFile/github/Lamaru_Ai_Agent/src/lamaru/tools/security.py) บังคับใช้ขอบเขตไดเรกทอรี Workspace และปฏิเสธการเข้าถึงไฟล์ที่ตรงกับ [`DEFAULT_PROTECTED_FILE_PATTERNS`](file:///C:/Poramet-SharFile/github/Lamaru_Ai_Agent/src/lamaru/tools/security.py) ทันที:

```python
DEFAULT_PROTECTED_FILE_PATTERNS = [
    # Environment variables & secrets
    r"^\.env(\..+)?$",                         # .env, .env.local, .env.prod
    r".*\.(key|pem|pfx|p12|crt|cer)$",          # Certificates & Private Keys
    r"^id_(rsa|ed25519|ecdsa|dsa).*",          # SSH Private & Public Keys
    r".*\.db$",                                # SQLite databases
    r".*\.sqlite3?$",                          # SQLite databases
    r"^.*\.git[\\/]config$",                   # Git repository credentials
    # Cloud & Service Account credentials
    r".*(service[-_]?account|client[-_]?secret|credentials).*\.json$",
    # Package manager & tool authentication
    r"^\.(netrc|npmrc|pypirc)$",
    r"^.*\.aws[\\/]credentials$",
    r"^.*\.docker[\\/]config\.json$",
]
```

* **ผลการป้องกัน:**
  * `read_file`, `write_file`, `edit_file`, และ `delete_file` ไม่สามารถเข้าถึงหรือแก้ไขไฟล์เหล่านี้ได้
  * `SearchFilesTool` และ `SearchTextTool` จะคัดแยกและข้ามไฟล์เหล่านี้ออกจากการค้นหาทั้งหมดโดยอัตโนมัติ

---

## 4. Subprocess Isolation & Shell Command Guard

### 4.1 Subprocess Environment Sanitization
ใน [`RunCommandTool`](file:///C:/Poramet-SharFile/github/Lamaru_Ai_Agent/src/lamaru/tools/terminal.py) มีการกรองตัวแปร Environment ก่อนส่งให้ Subprocess ด้วย `get_sanitized_env()`:
* **ตัดตัวแปรลับทั้งหมด:** กรองคีย์ที่มีคำว่า `API_KEY`, `SECRET`, `TOKEN`, `PASSWORD`, `CREDENTIAL` ออกจากสภาพแวดล้อมของ Process ลูก
* **ผลลัพธ์:** ป้องกันไม่ให้สคริปต์หรือคำสั่ง shell ที่รันผ่าน terminal สามารถแอบอ่าน API Key ในหน่วยความจำ หรือใช้ `curl` ส่งคีย์ออกนอกเครื่อง

### 4.2 CommandGuard Inspection
[`CommandGuard`](file:///C:/Poramet-SharFile/github/Lamaru_Ai_Agent/src/lamaru/tools/security.py) ทำการตรวจสอบคำสั่งเชลล์ก่อนรัน โดยบล็อก 2 กลุ่มคำสั่งสำคัญ:
1. **Sensitive File Access Commands:** บล็อกคำสั่งที่พยายามเปิดอ่านไฟล์ลับ เช่น `cat .env`, `type .env`, `Get-Content .env`, `gc .env`, `more .env`, `less .env`, `head .env`, `tail .env` รวมถึงคำสั่ง dump environment เช่น `printenv`, `Get-ChildItem env:`, `dir env:`
2. **Destructive Commands:** บล็อกคำสั่งอันตราย เช่น `rm -rf /`, `rmdir /s /q c:`, `format c:`, `mkfs`, `dd`, fork bomb (`:(){ :|:& };:`), และ `shutdown`

---

## 5. Secret Redaction & Sanitization Pipeline

[`SecretSanitizer`](file:///C:/Poramet-SharFile/github/Lamaru_Ai_Agent/src/lamaru/tools/security.py) ทำงานแบบ 2 ชั้น (Dual-layer Redaction):
1. **Dynamic Exact-Match Redaction:**
   * เชื่อมต่ออัตโนมัติกับ [`APIKeyManager.register_key()`](file:///C:/Poramet-SharFile/github/Lamaru_Ai_Agent/src/lamaru/keys/manager.py)
   * ทุก API Key ที่ถูกลงทะเบียนในระบบจะถูกส่งเข้าสู่ `SecretSanitizer.register_secret()`
   * ข้อความใดๆ ที่มีสตริงของคีย์จริงจะถูกแทนที่ด้วย `[REDACTED_SECRET]` เสมอ
2. **Static Regex Pattern Redaction:**
   * ตรวจจับและ Mask รูปแบบคีย์มาตรฐานสากล:
     * Google API Key: `AIza[0-9A-Za-z\-_]{30,40}` -> `[REDACTED_GOOGLE_API_KEY]`
     * OpenAI API Key: `sk-[a-zA-Z0-9]{30,60}`, `sk-proj-...` -> `[REDACTED_OPENAI_API_KEY]`
     * Anthropic API Key: `sk-ant-...` -> `[REDACTED_ANTHROPIC_API_KEY]`

### จุดที่ครอบคลุมการ Sanitize
* **Tool Outputs & Errors:** ใน `ToolRegistry.execute()` ทั้งผลลัพธ์และข้อความผิดพลาดจะถูก Sanitize ก่อนส่งกลับ
* **Session Persistence:** ใน [`AgentStateManager.save_session()`](file:///C:/Poramet-SharFile/github/Lamaru_Ai_Agent/src/lamaru/agent/state_manager.py) ข้อมูล JSON จะถูก Sanitize ก่อนเขียนลงดิสก์ เพื่อป้องกันไม่ให้ประวัติการสนทนามี Plaintext Secret หลุดลงไฟล์

---

## 6. HTTP Gateway Security

Gateway FastAPI (`src/lamaru/gateway/app.py`) มีมาตรการความปลอดภัยระดับเครือข่ายดังนี้:
1. **Localhost Binding:** ผูกกับ `127.0.0.1` เป็นค่าเริ่มต้นเพื่อไม่ให้เปิดสู่ภายนอก
2. **TrustedHostMiddleware:** ป้องกันการโจมตีประเภท **DNS Rebinding** โดยอนุญาตเฉพาะ Host ที่ระบุ (`127.0.0.1`, `localhost`, `testserver`)
3. **Gateway Token Verification (`verify_gateway_token`):**
   * หากมีการกำหนดตัวแปร `LAMARU_GATEWAY_TOKEN` ในระบบ ทุกคำขอที่เรียกเข้าสู่ `/v1/` จะต้องแนบ Header `Authorization: Bearer <TOKEN>`
   * ยกเว้นเฉพาะ Endpoint `/health` สำหรับการทำ Health Probe ในเครื่อง

---

## 7. Version Control & Development Hygiene

* **`.gitignore` Protection:**
  กำหนดการไม่ติดตามไฟล์ลับอย่างเข้มงวด:
  ```gitignore
  .env
  .env*.local
  *.key
  *.pem
  *.db
  *.sqlite
  *.sqlite3
  data/
  .sessions/
  sessions/
  ```
* **Unit Test Isolation:**
  Unit Tests ห้ามเรียก API ภายนอกหรือ Provider จริงเด็ดขาด ต้องใช้ `MockProvider` หรือจำลอง Mock HTTP Responses เสมอ
