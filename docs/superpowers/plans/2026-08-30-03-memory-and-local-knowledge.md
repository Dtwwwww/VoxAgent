# Memory and Local Knowledge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add transparent, editable long-term companion memory and local document retrieval without sending personal data off the computer.

**Architecture:** SQLite is the source of truth for conversations, durable memories, document chunks, and embedding metadata. A small BGE encoder produces 512-dimensional vectors stored as float32 blobs; NumPy performs cosine ranking over the bounded local corpus. A policy layer decides what may be proposed for memory, while the user-facing API exposes review, edit, delete, export, and full reset.

**Tech Stack:** Python 3.12, SQLite, sqlite-utils-free stdlib access, sentence-transformers-compatible ONNX inference, BAAI/bge-small-zh-v1.5, NumPy, pypdf, python-docx, FastAPI, React, pytest, Vitest.

## Global Constraints

- Complete Plan 02 first; keep its WebSocket event names and turn IDs unchanged.
- SQLite lives at `D:\VoxAgentData\data\voxagent.db`; embeddings and source documents never enter Git.
- Use BAAI/bge-small-zh-v1.5 with 512 dimensions and normalized float32 vectors.
- Do not add a vector database, telemetry SDK, or cloud embedding API.
- Durable memory is never created solely because the LLM emits a tool-shaped string.
- Passwords, tokens, payment data, government IDs, private keys, and health diagnoses are blocked from automatic durable memory.
- Users can inspect the exact text and source behind every retrieved memory/document chunk.
- Deletion must remove rows, embeddings, and cached source copies in one transaction or report failure.
- Retrieval is bounded to 2,000 memory rows and 20,000 document chunks for this device class.

---

## Planned File Structure

```text
backend/src/voxagent/
  db/connection.py
  db/migrations.py
  memory/embedder.py
  memory/models.py
  memory/policy.py
  memory/repository.py
  memory/retrieval.py
  knowledge/extract.py
  knowledge/ingest.py
  conversation/context.py
  conversation/persona.py
  db/backup.py
  api/memory.py
  api/knowledge.py
backend/tests/
  db/test_migrations.py
  memory/test_policy.py
  memory/test_repository.py
  memory/test_retrieval.py
  knowledge/test_extract.py
  knowledge/test_ingest.py
  conversation/test_context.py
  conversation/test_persona.py
  db/test_backup.py
  api/test_memory_api.py
frontend/src/
  memory/MemoryPanel.tsx
  knowledge/KnowledgePanel.tsx
  persona/PersonaPanel.tsx
  memory/__tests__/MemoryPanel.test.tsx
```

### Task 1: Transactional SQLite foundation

**Files:**
- Create: `backend/src/voxagent/db/connection.py`
- Create: `backend/src/voxagent/db/migrations.py`
- Create: `backend/tests/db/test_migrations.py`

**Interfaces:**
- Produces: `open_database(path: Path) -> sqlite3.Connection`
- Produces: `migrate(connection: sqlite3.Connection) -> int`
- Produces schema tables: `schema_version`, `conversations`, `messages`, `memories`, `documents`, `document_chunks`, `audit_events`

- [ ] **Step 1: Write migration tests**

Test a new database reaches schema version 1, running migration twice is idempotent, foreign keys are on, journal mode is WAL, and deleting a document cascades to its chunks. Assert all user text columns are `TEXT`, timestamps are UTC ISO-8601 strings, and vector blobs carry an explicit dimension column.

- [ ] **Step 2: Create migration 001 in code**

Use one `BEGIN IMMEDIATE` transaction. Required fields are:

- `messages(id, conversation_id, turn_id, role, content, created_at_utc)`
- `memories(id, kind, content, normalized_content, importance, source_message_id, embedding, embedding_dim, created_at_utc, updated_at_utc)`
- `documents(id, display_name, source_path, sha256, mime_type, imported_at_utc)`
- `document_chunks(id, document_id, ordinal, content, page_number, embedding, embedding_dim)`
- `audit_events(id, event_type, entity_type, entity_id, detail_json, created_at_utc)`

Create indexes on message turn ID, normalized memory text, document SHA-256, and document-chunk parent/ordinal.

- [ ] **Step 3: Implement safe connection settings**

Set a 5-second busy timeout, `foreign_keys=ON`, `journal_mode=WAL`, `synchronous=NORMAL`, and `row_factory=sqlite3.Row`. Refuse a database path outside `AppPaths.data` after resolving symlinks/junctions.

- [ ] **Step 4: Verify and commit**

```powershell
cd backend
uv run pytest tests/db/test_migrations.py -v
uv run ruff check src tests
cd ..
git add backend/src/voxagent/db backend/tests/db
git commit -m "feat: add transactional local data store"
```

### Task 2: Durable-memory policy and repository

**Files:**
- Create: `backend/src/voxagent/memory/models.py`
- Create: `backend/src/voxagent/memory/policy.py`
- Create: `backend/src/voxagent/memory/repository.py`
- Create: `backend/tests/memory/test_policy.py`
- Create: `backend/tests/memory/test_repository.py`

**Interfaces:**
- Produces: `MemoryCandidate(kind, content, importance, source_message_id)`
- Produces: `MemoryPolicy.evaluate(candidate) -> PolicyDecision`
- Produces: `MemoryRepository.create/list/update/delete/find_duplicate`

- [ ] **Step 1: Encode the policy as failing table tests**

Allow explicit preferences, stable profile facts, recurring habits, and relationship preferences. Reject one-time logistics, assistant guesses, secrets, payment data, ID numbers, medical diagnoses, and strings longer than 500 characters. Mark sensitive-but-user-explicit non-secret facts as `REQUIRES_CONFIRMATION`; never automatically save them.

- [ ] **Step 2: Implement deterministic secret/sensitivity filters**

Normalize full-width characters and whitespace. Use named rules for API-key/token prefixes, private-key headers, bank-card-like digit runs, PRC resident ID patterns, password phrases, and diagnostic-health phrases. Store only the rule name in audit details; never duplicate the matched secret.

- [ ] **Step 3: Test repository transactions and deduplication**

Require exact normalized matches to update `updated_at_utc` instead of inserting duplicates. Require cosine similarity above 0.94 plus the same `kind` to return a possible duplicate for user review. Test update/delete audit rows and transaction rollback after an injected database error.

- [ ] **Step 4: Implement repository methods with parameterized SQL**

Every mutating method accepts a caller-supplied UTC clock for deterministic tests. Return immutable domain models rather than `sqlite3.Row`. Limit list queries to 100 rows per page.

- [ ] **Step 5: Verify and commit**

```powershell
cd backend
uv run pytest tests/memory/test_policy.py tests/memory/test_repository.py -v
uv run ruff check src tests
cd ..
git add backend/src/voxagent/memory backend/tests/memory
git commit -m "feat: add reviewable companion memory"
```

### Task 3: Local BGE embeddings and NumPy retrieval

**Files:**
- Create: `backend/src/voxagent/memory/embedder.py`
- Create: `backend/src/voxagent/memory/retrieval.py`
- Create: `backend/tests/memory/test_retrieval.py`
- Create: `scripts/download_embedding_model.ps1`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `Embedder.encode(texts: tuple[str, ...]) -> np.ndarray`
- Produces: `BgeSmallZhEmbedder.from_path(path: Path, threads: int = 4)`
- Produces: `cosine_top_k(query, matrix, ids, k, minimum_score) -> tuple[RankedHit, ...]`

- [ ] **Step 1: Add dependencies and exact model location**

Add `onnxruntime>=1.22,<2` and `tokenizers>=0.21,<1`. The downloader installs an ONNX export of `BAAI/bge-small-zh-v1.5` under `D:\VoxAgentData\models\embeddings\bge-small-zh-v1.5`, validates a committed SHA-256 manifest, and is idempotent.

- [ ] **Step 2: Write retrieval tests with known vectors**

Assert L2 normalization, descending cosine order, minimum-score filtering, deterministic ID tie-breaking, empty corpus behavior, dimension mismatch failure, and top-k clamping. Include one Chinese semantic smoke case behind a `model` pytest marker.

- [ ] **Step 3: Implement the ONNX embedder**

Use tokenizer padding/truncation at 512 tokens, attention-mask mean pooling, and final L2 normalization. Run on CPU with four intra-op threads so Ollama retains the GPU. Reject output dimensions other than 512.

- [ ] **Step 4: Implement bounded retrieval**

Load at most 2,000 memory vectors or 20,000 document vectors per query, convert blobs with `np.frombuffer(dtype=np.float32)`, stack once, and compute one matrix-vector product. Stable-sort by negative score and ID. Defaults are `top_k=5` and `minimum_score=0.55`.

- [ ] **Step 5: Benchmark and commit**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/download_embedding_model.ps1 -DataRoot 'D:\VoxAgentData'
cd backend
uv run pytest tests/memory/test_retrieval.py -v
uv run pytest -m model tests/memory/test_retrieval.py -v
uv run ruff check src tests
cd ..
git add backend scripts/download_embedding_model.ps1
git commit -m "feat: add local semantic retrieval"
```

Expected: a 2,000-row memory query completes in under 100 ms on the target CPU after warm-up.

### Task 4: TXT, Markdown, PDF, and DOCX knowledge ingestion

**Files:**
- Create: `backend/src/voxagent/knowledge/extract.py`
- Create: `backend/src/voxagent/knowledge/ingest.py`
- Create: `backend/tests/knowledge/test_extract.py`
- Create: `backend/tests/knowledge/test_ingest.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `extract_document(path: Path) -> ExtractedDocument`
- Produces: `chunk_document(document, target_chars=600, overlap_chars=80)`
- Produces: `KnowledgeIngestor.import_file(path: Path) -> ImportResult`

- [ ] **Step 1: Add parsers and fixtures**

Add `pypdf>=5.9,<7` and `python-docx>=1.2,<2`. Commit synthetic UTF-8 TXT, Markdown, two-page text PDF, and DOCX fixtures containing no personal data.

- [ ] **Step 2: Write extraction and chunking tests**

Require extension and MIME allowlisting, a 20 MB file limit, page preservation for PDF, heading preservation for Markdown, paragraph preservation for DOCX, deterministic 600/80 character chunks, and rejection of image-only PDFs with a Chinese explanation.

- [ ] **Step 3: Implement import as a two-phase operation**

First read/extract/hash/embed without database mutation. Then insert document and chunks in one transaction. Identical SHA-256 imports return the existing document ID; changed content creates a new document. Store the resolved source path for traceability but do not copy source files by default.

- [ ] **Step 4: Test cancellation and deletion**

Inject cancellation between embedding batches and prove no partial database rows remain. Delete an imported document and prove its chunk vectors are gone and no other document is affected.

- [ ] **Step 5: Verify and commit**

```powershell
cd backend
uv run pytest tests/knowledge -v
uv run ruff check src tests
cd ..
git add backend
git commit -m "feat: ingest local reference documents"
```

### Task 5: Prompt context assembly and memory proposals

**Files:**
- Create: `backend/src/voxagent/conversation/context.py`
- Create: `backend/src/voxagent/conversation/persona.py`
- Create: `backend/tests/conversation/test_context.py`
- Create: `backend/tests/conversation/test_persona.py`
- Modify: `backend/src/voxagent/conversation/orchestrator.py`

**Interfaces:**
- Produces: `ContextAssembler.build(user_text, recent_messages) -> ContextBundle`
- Produces: `PersonaConfig(name, user_address, background, traits, relationship, style, initiative, boundaries, default_reply_length)`
- Produces: `MemoryProposalParser.parse(llm_json) -> tuple[MemoryCandidate, ...]`
- Extends: assistant turn completion with optional `memory.proposed` event

- [ ] **Step 1: Define and test one coherent persona**

Validate every persona field with explicit maximum lengths, reject control characters and attempts to change tool/privacy policy, and provide one neutral Chinese default. There is exactly one active persona; “companion” and “assistant” are intent-dependent behaviors, not separate modes. Test that changing the name/style affects the next prompt while safety, confirmation, and privacy instructions remain immutable and higher priority.

- [ ] **Step 2: Test context ordering and size limits**

The assembled order is system persona, up to five durable memories, up to four knowledge chunks, recent conversation, then current user text. Cap inserted memory text at 1,000 characters, knowledge at 2,400, recent history at 3,500, and total estimated context at 7,500 tokens. Lower-ranked items are removed first.

- [ ] **Step 3: Separate response generation from memory extraction**

After a successful assistant turn, call the same local model with a strict JSON schema for zero or more candidates. Validate with Pydantic, pass each candidate through `MemoryPolicy`, and emit `memory.proposed` only for `ALLOW` or `REQUIRES_CONFIRMATION`. A parser error must never affect the spoken reply.

- [ ] **Step 4: Prevent prompt injection from retrieved documents**

Wrap retrieved text as quoted reference data and state that it is untrusted content, not instructions. Strip NUL/control characters. Include source IDs but never raw filesystem paths in the LLM prompt.

- [ ] **Step 5: Verify and commit**

```powershell
cd backend
uv run pytest tests/conversation/test_persona.py tests/conversation/test_context.py tests/conversation/test_orchestrator.py -v
uv run ruff check src tests
cd ..
git add backend
git commit -m "feat: ground voice turns in local memory"
```

### Task 6: Memory and knowledge management API and Web panels

**Files:**
- Create: `backend/src/voxagent/api/memory.py`
- Create: `backend/src/voxagent/api/knowledge.py`
- Create: `backend/src/voxagent/db/backup.py`
- Create: `backend/tests/api/test_memory_api.py`
- Create: `backend/tests/db/test_backup.py`
- Create: `frontend/src/memory/MemoryPanel.tsx`
- Create: `frontend/src/knowledge/KnowledgePanel.tsx`
- Create: `frontend/src/persona/PersonaPanel.tsx`
- Create: `frontend/src/memory/__tests__/MemoryPanel.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Produces: authenticated CRUD routes under `/v1/memories`
- Produces: import/list/delete routes under `/v1/knowledge`
- Produces: authenticated read/update routes under `/v1/persona`
- Produces: `POST /v1/data/export` and confirmation-protected `DELETE /v1/data`

- [ ] **Step 1: Write authorization and CRUD tests**

All routes require the same session token as the voice socket in an `Authorization: Bearer` header. Test pagination, validation, duplicate proposals, optimistic update conflicts, deletion, export, and full reset. Full reset requires the exact phrase `删除声灵全部本地数据` in the body.

- [ ] **Step 2: Implement APIs without exposing source paths**

Return document display names, hashes, page numbers, and chunk excerpts; omit absolute source paths. Export creates a ZIP under `AppPaths.data/exports` containing UTF-8 JSON plus schema version, then returns a one-time localhost download ID valid for ten minutes.

- [ ] **Step 3: Add seven-copy local database backup rotation**

On the first clean application exit of each local calendar day, use SQLite's online backup API to create `D:\VoxAgentData\data\backups\voxagent-YYYY-MM-DD.db`, run `PRAGMA integrity_check` on the copy, then retain the newest seven valid daily files. Never copy a live database/WAL with filesystem copy. Test one backup per day, integrity failure cleanup, seven-file rotation, and that memory/document deletion present in the source also remains deleted in every newly created backup. Existing older backups are visible to the user and can be deleted from the data panel.

- [ ] **Step 4: Test the Web panels**

Verify a user can edit the single persona, accept/reject a memory proposal, edit and delete saved memory, import supported documents, inspect retrieval source excerpts, view/delete dated backups, and invoke full reset only after typing the confirmation phrase. No data is loaded before the user opens the panel.

- [ ] **Step 5: Implement and verify**

```powershell
cd backend
uv run pytest tests/api/test_memory_api.py tests/db/test_backup.py -v
uv run ruff check src tests
cd ../frontend
pnpm test -- --run
pnpm exec tsc --noEmit
pnpm build
```

- [ ] **Step 6: Commit the completed memory feature**

```powershell
cd ..
git add backend frontend
git commit -m "feat: expose editable local memory and knowledge"
git status --short
```

## Plan 03 Completion Gate

- Durable memories are policy-filtered, attributable, editable, and deletable.
- One configurable persona consistently covers both companionship and assistant intents without weakening fixed safety policy.
- Sensitive automatic memories are blocked or require explicit confirmation.
- BGE embedding and cosine retrieval run entirely on CPU and locally.
- TXT, Markdown, text PDF, and DOCX import is transactional and cancellable.
- Retrieved material is bounded, source-labelled, and treated as untrusted data.
- The user can export or fully reset all stored information.
- The first clean exit each day creates an integrity-checked local backup and retains the newest seven copies.
- Backend and frontend automated verification commands pass.
