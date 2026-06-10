# HealthCompanion — Per-Patient Medical RAG Core

A backend that gives **each patient a private, searchable knowledge base** built from
their own medical documents (lab reports, prescriptions, discharge notes — including
**scanned and handwritten** ones). Doctors and patients can ask natural-language
questions and get answers grounded *only* in that patient's records.

Powered entirely by **Google Gemini**: multimodal vision reads scanned/handwritten
documents (no separate OCR engine), `gemini-embedding-001` produces embeddings, and
`gemini-2.5-flash` answers questions. Vectors live in a local **ChromaDB**; a small
**SQLite** catalog tracks patients and documents.

> This is the **RAG core** (no web portal UI yet). It's exposed via a thin FastAPI so a
> React frontend can be added later.

## Pipeline

```
Ingest:  file ─► extract (Gemini vision) ─► chunk ─► embed[RETRIEVAL_DOCUMENT] ─► Chroma(per-patient)
                                                                          └─► register in SQLite
Ask:     question ─► embed[RETRIEVAL_QUERY] ─► Chroma where{patient_id} top-k ─► Gemini answer (cited, role-aware)
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then put your Gemini key in .env
```

Get a key at https://aistudio.google.com/apikey.

## Usage (CLI)

```bash
python cli.py add-patient --name "Jane Doe"
python cli.py ingest <patient_id> ./sample_prescription.jpg --type rx --date 2026-05-01
python cli.py ask <patient_id> "Which medicines were prescribed and at what time?" --role patient
python cli.py list-patients
python cli.py list-docs <patient_id>
```

## Authentication & access control

The API is protected by token-based auth (JWT). Two roles:

- **Patient** — signing up creates their own linked patient record; they can only
  access *their own* documents and ask about themselves.
- **Doctor** — can see every patient, create patient records, upload, and ask.

Access is enforced server-side on every patient endpoint (a patient requesting
another patient's data gets `403`). Passwords are hashed with `scrypt`; sessions are
signed JWTs (set `HC_JWT_SECRET` in production — the default is a dev placeholder).

## Usage (API)

```bash
uvicorn api:app --reload
# POST /auth/signup                   {"email","password","name","role":"patient"|"doctor"}  -> {token, user}
# POST /auth/login                    {"email","password"}                                    -> {token, user}
# GET  /auth/me                       (Bearer token)
# GET  /patients                      doctors: all; patients: self only
# POST /patients                      {"name": "..."}  (doctors only)
# POST /patients/{id}/documents       multipart upload (+ doc_type, doc_date)
# GET  /patients/{id}/documents
# POST /patients/{id}/ask             {"question": "..."}   (answer style follows role)
```

All `/patients*` routes require an `Authorization: Bearer <token>` header.
Interactive docs at http://localhost:8000/docs.

## Web portal (React)

A React + Vite frontend lives in `frontend/`. Run the backend and the portal together:

```bash
# terminal 1 — backend
uvicorn api:app --reload

# terminal 2 — frontend
cd frontend
npm install
cp .env.example .env        # VITE_API_BASE defaults to http://localhost:8000
npm run dev                 # opens http://localhost:5173
```

The portal opens to a **login / signup** screen. Sign up as a **patient** (you get
your own record) or a **doctor** (you see all patients). Then drag-and-drop documents
to ingest them and chat with the records — answers show their source citations, and a
patient can only ever reach their own data. Sessions persist across reloads; sign out
from the user card in the sidebar.

## Roles

`ask(..., role=...)` switches the system prompt: **doctor** → clinical detail and
terminology; **patient** → plain-language explanations. Answers always cite the source
document and date, and say *"not found in your records"* when the answer isn't present.

## Tests

```bash
pytest
```

Tests mock all Gemini calls, so they run offline without consuming API quota.

## ⚠️ Security & compliance

- The Gemini API key is read from `.env`, which is **git-ignored**. Never commit it.
- This prototype targets the **Gemini Developer API** and is intended for
  **synthetic / de-identified** data only.
- **Real patient PHI in production requires Vertex AI under a BAA**
  (`genai.Client(vertexai=True, project=..., location=...)`, same SDK) plus auth,
  access control, encryption, and audit logging — none of which are built here.
- Patient isolation is defense-in-depth: a **dedicated Chroma collection per patient**
  *and* a `patient_id` metadata filter on every query.
