<div align="center">

# 🏥 HealthCompanion

### A per-patient medical RAG system — upload records, ask grounded questions, get cited answers.

[![Live Demo](https://img.shields.io/badge/Live_Demo-healthcompanion.codeshare.co.in-1f6b4f?style=for-the-badge)](https://healthcompanion.codeshare.co.in)

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-TS-61DAFB?logo=react&logoColor=white)
![Gemini](https://img.shields.io/badge/Google_Gemini-8E75B2?logo=googlegemini&logoColor=white)
![ChromaDB](https://img.shields.io/badge/ChromaDB-vector_search-FF6B6B)
![Kubernetes](https://img.shields.io/badge/Kubernetes-ArgoCD-326CE5?logo=kubernetes&logoColor=white)
![CI/CD](https://img.shields.io/badge/CI%2FCD-GitHub_Actions-2088FF?logo=githubactions&logoColor=white)

</div>

---

**HealthCompanion** gives every patient a private, searchable medical record. Patients
and doctors upload documents — prescriptions, lab reports, clinical notes, **including
scanned and handwritten ones** — and ask natural-language questions that are answered
**only from that patient's documents**, with source citations. It models how care
actually works: authentication with roles, **visits/episodes** (a patient treated by
different doctors for different problems over time), a doctor directory, **patient-controlled
document sharing**, in-portal **prescription drafting**, AI summaries, content guardrails,
and **deletion/retention rules** — running live on Kubernetes with full CI/CD.

> ⚠️ Built for **synthetic / de-identified data** as a portfolio project. Real patient
> PHI would require Vertex AI under a BAA, audit logging, and stricter controls.

## 🔗 Live demo

**https://healthcompanion.codeshare.co.in** — sign up as a **patient** (your own record)
or a **doctor** (only the patients in your care + documents they share with you).

| Login | Portal |
|---|---|
| ![Login](docs/screenshots/login.png) | ![Portal](docs/screenshots/portal.png) |

## ✨ Highlights

- **Reads scans & handwriting** — Gemini vision transcribes images/PDFs directly (no
  separate OCR engine), auto-detecting the document's **date** and **type**. Handwritten
  drug names are transcribed as-written and flagged when uncertain — never "auto-corrected"
  into a confident wrong name.
- **Hybrid retrieval** — semantic vector search **+** keyword search, fused with
  Reciprocal Rank Fusion, then **MMR** for diversity and a **relevance gate** — broad
  questions summarize the whole record, specific ones do precise top-k retrieval.
- **Grounded, cited answers** — every answer comes from the patient's own documents and
  cites the source; it **reports, doesn't interpret** (won't invent a diagnosis from an
  unlabelled value) and says *"I couldn't find that in your records"* instead of hallucinating.
- **Strict per-patient isolation** — each patient has a dedicated vector collection *and*
  a metadata filter on every query *and* API access control; a patient can never reach
  another's data (enforced server-side, tested).
- **Privacy by design** — doctors see only their own patients/visits **plus documents the
  patient explicitly shares**; chat is **never stored** and is invisible to the other party.
- **Visits / episodes of care** — documents and Q&A are organized into visits, each
  attributed to a doctor (or self), building a timeline across problems and doctors over time.
- **In-portal prescriptions** — a doctor can draft a structured prescription and add it
  straight to the patient's record (searchable like any document).
- **View the original** — open the actual uploaded scan/PDF to verify a handwritten name
  or read a graph the OCR can't reproduce.
- **Deletion & retention rules** — doctors can never delete a record; a patient can delete
  their own upload while it's private, but it **locks** a few hours after being shared;
  long-inactive doctor-created patients age out of doctors' views.
- **Role-aware** — the same records read as clinical notes for a doctor and plain-language
  guidance for a patient.
- **Content guardrail** — non-medical uploads (marksheets, selfies) are rejected before storage.
- **Cached AI summaries** — an at-a-glance clinical summary per patient and per visit,
  regenerated only when documents change.
- **Production-grade delivery** — single-container build, GitHub Actions CI/CD → GHCR,
  ArgoCD GitOps on Kubernetes, Let's Encrypt TLS, persistent storage, health/readiness probes.

## 🏗️ Architecture

```mermaid
flowchart TB
  U["Patient / Doctor — Browser<br/>React SPA (Vite/TS) · JWT"]
  CF["Cloudflare DNS<br/>*.codeshare.co.in"]
  IGX["nginx Ingress + cert-manager TLS"]

  subgraph POD["healthcompanion Pod · Kubernetes (1 replica, Recreate)"]
    API["FastAPI (api.py)<br/>serves React build + REST API"]
    AUTH["Auth & Access Control<br/>JWT HS256 · scrypt · per-patient authz"]
    subgraph INGEST["RAG — Ingestion (on upload)"]
      direction TB
      OCR["extract.py<br/>Gemini vision OCR → text + date"]
      GD["guardrails.py<br/>medical? + auto type"]
      CK["chunk.py<br/>section-aware chunks"]
      EM["embed.py<br/>768-d · batched"]
      OCR --> GD --> CK --> EM
    end
    subgraph QUERY["RAG — Query (on ask)"]
      direction TB
      RT{"overview?"}
      EQ["embed_query"]
      HY["hybrid retrieve<br/>vector + keyword (RRF)"]
      GT["relevance gate"]
      MM["MMR top-k (λ=0.6)"]
      WH["whole record<br/>newest-first"]
      GEN["grounded answer<br/>+ citations"]
      RT -->|specific| EQ --> HY --> GT --> MM --> GEN
      RT -->|broad| WH --> GEN
    end
  end

  subgraph PV["PersistentVolume (RWO)"]
    CH["ChromaDB<br/>collection per patient"]
    DB["SQLite catalog<br/>patients/docs/visits/shares"]
    UP["uploads/ originals"]
  end

  subgraph GEM["Google Gemini API"]
    GF["gemini-2.5-flash<br/>OCR + generation"]
    GE["gemini-embedding-001<br/>768-d embeddings"]
  end

  U -->|HTTPS| CF --> IGX --> API --> AUTH
  AUTH -->|upload| OCR
  AUTH -->|ask| RT
  EM --> CH
  EM --> DB
  API --> UP
  HY --> CH
  RT --> DB
  OCR -.->|OCR| GF
  EM -.->|embed docs| GE
  EQ -.->|embed query| GE
  GEN -.->|generate| GF
```

<details>
<summary>Container view &amp; data-flow (text)</summary>

```
                         ┌─────────────────────── Single container ───────────────────────┐
   Browser ── HTTPS ──►  │  FastAPI  ──serves──►  React/Vite SPA (same origin, no CORS)    │
   (React UI)            │     │                                                            │
                         │     ├─ Auth: JWT + scrypt, role-based access control            │
                         │     ├─ SQLite  (patients, documents, users, visits, care team)  │
                         │     ├─ ChromaDB (per-patient vector collections)                │
                         │     └─ Google Gemini ── vision OCR · embeddings · generation    │
                         └────────────────────────── /data (persistent volume) ────────────┘

   INGEST:  file ─► Gemini vision OCR (text + date) ─► medical guardrail (+ auto type)
                 ─► section-aware chunk ─► embed ─► Chroma (per-patient, + visit tag) & SQLite
   ASK:     question ─► route (overview vs specific) ─► embed ─► hybrid retrieve
                 (vector + keyword RRF, scoped to patient/visit/shared) ─► relevance gate
                 ─► MMR top-k ─► grounded, cited Gemini answer
```

</details>

## 🛠️ Tech stack

| Layer | Choice | Why |
|---|---|---|
| AI | **Google Gemini** (`google-genai`) | One provider for vision OCR + embeddings + generation |
| Vector store | **ChromaDB** | Local, zero-ops, metadata filtering for isolation |
| Catalog / accounts | **SQLite** | Zero-ops relational store |
| Backend | **FastAPI** | Async, validation, dependency-injected auth |
| Frontend | **React + Vite + TypeScript** | Typed, fast, component-based portal |
| Auth | **JWT + scrypt** | Standard, no native deps |
| Packaging | **Docker** (multi-stage) | One image serves API + SPA |
| CI/CD | **GitHub Actions → GHCR** | Auto-versioned image on every push |
| Deploy | **Kubernetes + ArgoCD** | GitOps, self-healing, TLS, persistent volume |

## 🚀 Run locally

```bash
# Backend
pip install -r requirements.txt
cp .env.example .env            # add your Gemini key (aistudio.google.com/apikey)
uvicorn api:app --reload        # http://localhost:8000  (API + /docs)

# Frontend (separate terminal)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

There's also a CLI for the core pipeline:

```bash
python cli.py add-patient --name "Jane Doe"
python cli.py ingest <id> ./prescription.jpg --type rx
python cli.py ask <id> "Which medicines do I take at night?" --role patient
```

## 🔌 API (selected)

All `/patients*` routes require `Authorization: Bearer <jwt>`.

```
POST /auth/signup · /auth/login        GET /auth/me · /doctors · PATCH /auth/profile
GET/POST /patients                     GET/PATCH /patients/{id} · GET /patients/{id}/care-team
POST /patients/{id}/documents          GET /patients/{id}/documents
PATCH/DELETE /documents/{id}           GET /documents/{id}/file        (view original scan/PDF)
GET/POST/DELETE /documents/{id}/share  GET /documents/{id}/shares      (patient-controlled sharing)
POST /patients/{id}/prescriptions      (doctor drafts a prescription)
POST /patients/{id}/ask                GET /patients/{id}/summary
GET/POST /patients/{id}/visits         POST /visits/{id}/close · GET /visits/{id}/summary
GET /healthz · /readyz                 (liveness / readiness probes)
```

Interactive OpenAPI docs are served at `/docs`.

## 🧪 Tests

```bash
pytest        # 67 tests; all Gemini calls mocked → offline, zero API quota
```

Covers the RAG pipeline, **patient isolation & privacy scoping**, auth & access control,
the medical guardrail, document sharing, in-portal prescriptions, the deletion/retention
rules, summary caching, and the visits/care-team model. A separate retrieval **eval
harness** (`scripts/eval_retrieval.py`) measures hit-rate against the real model.

## 📦 Project structure

```
api.py · cli.py · config.py · Dockerfile
src/healthcompanion/   gemini_client · extract · chunk · embed · vectorstore
                       guardrails · ingest · rag · security · auth · patients · retention
frontend/src/          api.ts · auth.tsx · App.tsx · components/*
tests/                 pytest suite (mocked Gemini)
eval/ · scripts/       retrieval eval harness
.github/workflows/     release-and-publish.yml (CI/CD → GHCR)
```

## 🚢 Deployment

Every push to `main` triggers GitHub Actions to **version, build, and publish** the
Docker image to GHCR and cut a GitHub Release. The app runs on a Kubernetes cluster via
**ArgoCD GitOps** (auto-sync + self-heal), exposed over HTTPS with an automatic Let's
Encrypt certificate and a persistent volume for patient data.

## 🔒 Security & compliance

- Secrets (Gemini key, JWT secret) are **never committed** — `.env` is git-ignored;
  production uses Kubernetes Secrets, and the app refuses to boot in production with the
  default dev JWT secret.
- Passwords hashed with `scrypt` (constant-time verify); sessions are single-algorithm
  signed JWTs re-validated against the DB on every request.
- Patient isolation is **defense-in-depth** (per-patient collection + metadata filter +
  API access control). SQLite runs in WAL mode for safe concurrent access.
- **Privacy model:** doctors see only their own patients/visits plus explicitly shared
  documents; chat is never stored or shared with the other party.
- **Deletion/retention:** doctors can never delete a record; a patient may delete their
  own upload while private, locking a few hours after sharing; long-inactive doctor-created
  patients age out of doctors' views and orphans are purged.
- Intended for **synthetic/de-identified data**; production PHI needs Vertex AI under a
  BAA, encryption at rest, and audit logging.

---

<div align="center">
Built as a full-stack + applied-AI portfolio project. Feedback welcome.
</div>
