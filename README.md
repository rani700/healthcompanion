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

**HealthCompanion** gives every patient a private, searchable medical record. Doctors
and patients upload documents — prescriptions, lab reports, clinical notes, **including
scanned and handwritten ones** — and ask natural-language questions that are answered
**only from that patient's documents**, with source citations. It models how care
actually works: authentication with roles, **visits/episodes** (a patient treated by
different doctors for different problems over time), a doctor directory, AI summaries,
and content guardrails — running live on Kubernetes with full CI/CD.

> ⚠️ Built for **synthetic / de-identified data** as a portfolio project. Real patient
> PHI would require Vertex AI under a BAA, audit logging, and stricter controls.

## 🔗 Live demo

**https://healthcompanion.codeshare.co.in** — sign up as a **doctor** (see all patients)
or a **patient** (your own record).

| Login | Portal |
|---|---|
| ![Login](docs/screenshots/login.png) | ![Portal](docs/screenshots/portal.png) |

## ✨ Highlights

- **Reads scans & handwriting** — Gemini vision transcribes images/PDFs directly; no
  separate OCR engine.
- **Grounded, cited answers** — every answer comes from the patient's own documents and
  cites the source; says *"I couldn't find that in your records"* instead of hallucinating.
- **Strict per-patient isolation** — each patient has a dedicated vector collection *and*
  a metadata filter on every query; a patient can never reach another's data (enforced
  server-side, tested).
- **Visits / episodes of care** — documents and Q&A are organized into visits, each
  attributed to a doctor (or self), building a timeline across multiple problems and
  doctors over time.
- **Role-aware** — the same records read as clinical notes for a doctor and plain-language
  guidance for a patient.
- **Content guardrail** — non-medical uploads (marksheets, selfies) are rejected before
  storage.
- **Cached AI summaries** — an at-a-glance clinical summary per patient and per visit,
  regenerated only when documents change.
- **Production-grade delivery** — single-container build, GitHub Actions CI/CD →
  GHCR, ArgoCD GitOps on Kubernetes, Let's Encrypt TLS, persistent storage.

## 🏗️ Architecture

```
                         ┌─────────────────────── Single container ───────────────────────┐
   Browser ── HTTPS ──►  │  FastAPI  ──serves──►  React/Vite SPA (same origin, no CORS)    │
   (React UI)            │     │                                                            │
                         │     ├─ Auth: JWT + scrypt, role-based access control            │
                         │     ├─ SQLite  (patients, documents, users, visits, care team)  │
                         │     ├─ ChromaDB (per-patient vector collections)                │
                         │     └─ Google Gemini ── vision OCR · embeddings · generation    │
                         └────────────────────────── /data (persistent volume) ────────────┘

   INGEST:  file ─► Gemini vision (text) ─► medical guardrail ─► chunk ─► embed ─► Chroma (+ visit tag)
   ASK:     question ─► embed ─► top-k retrieve (this patient / visit) ─► grounded, cited Gemini answer
```

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
POST /auth/signup · /auth/login        GET /auth/me · /doctors
GET/POST /patients                     GET/PATCH /patients/{id}
POST /patients/{id}/documents          PATCH /documents/{id}   (re-file into a visit)
POST /patients/{id}/ask                GET  /patients/{id}/summary
GET/POST /patients/{id}/visits         POST /visits/{id}/close · GET /visits/{id}/summary
```

Interactive OpenAPI docs are served at `/docs`.

## 🧪 Tests

```bash
pytest        # 34 tests; all Gemini calls mocked → offline, zero API quota
```

Covers the RAG pipeline, **patient isolation**, auth & access control, the medical
guardrail, summary caching, and the visits/care-team model.

## 📦 Project structure

```
api.py · cli.py · config.py · Dockerfile
src/healthcompanion/   gemini_client · extract · chunk · embed · vectorstore
                       guardrails · ingest · rag · security · auth · patients
frontend/src/          api.ts · auth.tsx · App.tsx · components/*
tests/                 pytest suite (mocked Gemini)
.github/workflows/     release-and-publish.yml (CI/CD → GHCR)
```

## 🚢 Deployment

Every push to `main` triggers GitHub Actions to **version, build, and publish** the
Docker image to GHCR and cut a GitHub Release. The app runs on a Kubernetes cluster via
**ArgoCD GitOps** (auto-sync + self-heal), exposed over HTTPS with an automatic Let's
Encrypt certificate and a persistent volume for patient data.

## 🔒 Security & compliance

- Secrets (Gemini key, JWT secret) are **never committed** — `.env` is git-ignored;
  production uses Kubernetes Secrets.
- Passwords hashed with `scrypt`; sessions are signed JWTs.
- Patient isolation is **defense-in-depth** (per-patient collection + metadata filter).
- Intended for **synthetic/de-identified data**; production PHI needs Vertex AI under a
  BAA, encryption at rest, and audit logging.

---

<div align="center">
Built as a full-stack + applied-AI portfolio project. Feedback welcome.
</div>
