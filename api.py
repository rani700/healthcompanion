"""FastAPI over the RAG core, with authentication and access control.

Auth model:
  - Doctors access ONLY patients they have a care relationship with (created the
    record, or the patient requested them). They never see other patients.
  - Patients access only their own linked record.
  - Chat (ask) is never stored: neither party can see the other's chatbot history;
    they share only uploaded documents.
  - Doctors can never delete documents; a patient may delete their OWN upload only
    within a short window (accidental upload).

    uvicorn api:app --reload
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

# Make the src/ package importable.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import config
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from healthcompanion import auth, patients, retention, vectorstore
from healthcompanion.guardrails import NotMedicalDocument
from healthcompanion.ingest import ingest_document, ingest_text
from healthcompanion.rag import ask as rag_ask
from healthcompanion.rag import summarize_patient as rag_summarize
from healthcompanion.rag import summarize_visit as rag_summarize_visit
from healthcompanion.security import create_token, decode_token


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Daily retention sweep: purge inactive patients with no self-account.
    async def _loop():
        while True:
            with contextlib.suppress(Exception):
                await run_in_threadpool(retention.purge_inactive)
            await asyncio.sleep(24 * 3600)

    task = asyncio.create_task(_loop())
    yield
    task.cancel()


app = FastAPI(title="HealthCompanion", version="0.4.0", lifespan=lifespan)

# Fail fast if deployed to production without a real JWT secret.
config.assert_secure_for_production()

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

bearer = HTTPBearer(auto_error=True)


# --- Schemas -----------------------------------------------------------------
class SignupBody(BaseModel):
    email: str
    password: str
    name: str
    role: str = "patient"
    dob: str | None = None
    sex: str | None = None
    phone: str | None = None
    address: str | None = None
    specialty: str | None = None
    clinic: str | None = None


class LoginBody(BaseModel):
    email: str
    password: str


class ProfileBody(BaseModel):
    specialty: str | None = None
    clinic: str | None = None


class CreatePatient(BaseModel):
    name: str
    dob: str | None = None
    sex: str | None = None
    phone: str | None = None
    address: str | None = None


class UpdatePatient(BaseModel):
    name: str | None = None
    dob: str | None = None
    sex: str | None = None
    phone: str | None = None
    address: str | None = None


class AskRequest(BaseModel):
    question: str
    visit_id: str | None = None
    history: list[dict[str, str]] | None = None  # recent {role, text} turns; not stored


class CreateVisit(BaseModel):
    title: str
    doctor_id: str | None = None  # a patient may request a specific doctor


class MoveDoc(BaseModel):
    visit_id: str | None = None  # None/"" -> general (no visit)


class ShareBody(BaseModel):
    doctor_id: str


class Medication(BaseModel):
    name: str
    dosage: str | None = None
    frequency: str | None = None
    duration: str | None = None


class PrescriptionBody(BaseModel):
    medications: list[Medication]
    diagnosis: str | None = None
    advice: str | None = None
    doc_date: str | None = None
    visit_id: str | None = None


def _render_prescription(body: "PrescriptionBody", doctor_name: str, specialty: str | None) -> str:
    by = doctor_name + (f", {specialty}" if specialty else "")
    lines = ["Prescription"]
    if body.doc_date:
        lines.append(f"Date: {body.doc_date}")
    lines.append(f"Prescribed by: Dr {by}")
    if body.diagnosis:
        lines.append(f"\nDiagnosis: {body.diagnosis}")
    lines.append("\nMedications:")
    for i, m in enumerate(body.medications, 1):
        seg = " ".join(p for p in [m.name, m.dosage] if p)
        extra = " — ".join(x for x in [m.frequency, m.duration] if x)
        lines.append(f"{i}. {seg}" + (f" — {extra}" if extra else ""))
    if body.advice:
        lines.append(f"\nAdvice: {body.advice}")
    return "\n".join(lines)


def _link_if_doctor(user: dict, patient_id: str) -> None:
    """Record a care relationship when a doctor works with a patient."""
    if user["role"] == "doctor":
        patients.link_doctor_patient(user["id"], patient_id)


def _gemini_guard(fn):
    """Run a Gemini-backed call, turning provider errors into clean HTTP errors."""
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg or "quota" in msg.lower():
            raise HTTPException(
                status_code=503,
                detail="The AI is rate-limited right now (Gemini quota reached). "
                "Please try again in a minute.",
            )
        raise HTTPException(
            status_code=502,
            detail="The AI service had a problem. Please try again.",
        )


# --- Auth dependencies -------------------------------------------------------
def current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    try:
        payload = decode_token(creds.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    user = auth.get_user(payload.get("sub", ""))
    if user is None:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    return user


def require_doctor(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "doctor":
        raise HTTPException(status_code=403, detail="Doctors only.")
    return user


def _authorize_patient(user: dict, patient_id: str) -> None:
    """Patients access only their own record; doctors only patients they have a
    care relationship with (created or were requested by)."""
    if patients.get_patient(patient_id) is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    if user["role"] == "doctor":
        if patients.has_care_relationship(user["id"], patient_id):
            return
        raise HTTPException(status_code=403, detail="This patient is not in your care.")
    if user.get("patient_id") != patient_id:
        raise HTTPException(status_code=403, detail="You can only access your own records.")


def _touch(patient_id: str) -> None:
    """Record activity on a patient (resets the inactivity/retention clock)."""
    patients.touch_patient(patient_id)


def _check_dob(dob: str | None) -> None:
    """Reject malformed or future dates of birth."""
    if not dob:
        return
    try:
        d = date.fromisoformat(dob)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date of birth.")
    if d > datetime.now(timezone.utc).date():
        raise HTTPException(status_code=400, detail="Date of birth can't be in the future.")


def _doc_scope(user: dict, patient_id: str):
    """None for a patient (their full record); for a doctor, the list of document
    ids they may see (in their visits, uploaded by them, or shared with them)."""
    if user["role"] == "doctor":
        return patients.visible_doc_ids_for_doctor(patient_id, user["id"])
    return None


def _token_response(user: dict) -> dict:
    token = create_token(user["id"], user["role"], user.get("patient_id"))
    return {"token": token, "user": user}


# --- Login throttle (in-memory; single-replica) ------------------------------
_login_attempts: dict[str, list[float]] = {}


def _check_login_throttle(email: str) -> None:
    key = (email or "").strip().lower()
    now = time.time()
    window = config.LOGIN_WINDOW_SECONDS
    hits = [t for t in _login_attempts.get(key, []) if now - t < window]
    _login_attempts[key] = hits
    if len(hits) >= config.LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please wait a few minutes and try again.",
        )


def _record_login_failure(email: str) -> None:
    key = (email or "").strip().lower()
    _login_attempts.setdefault(key, []).append(time.time())


def _clear_login_failures(email: str) -> None:
    _login_attempts.pop((email or "").strip().lower(), None)


# --- Auth routes -------------------------------------------------------------
@app.post("/auth/signup")
def signup(body: SignupBody):
    _check_dob(body.dob)
    try:
        user = auth.signup(
            body.email, body.password, body.name, body.role,
            dob=body.dob, sex=body.sex, phone=body.phone, address=body.address,
            specialty=body.specialty, clinic=body.clinic,
        )
    except auth.AuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _token_response(user)


@app.post("/auth/login")
def login(body: LoginBody):
    _check_login_throttle(body.email)
    try:
        user = auth.login(body.email, body.password)
    except auth.AuthError as e:
        _record_login_failure(body.email)
        raise HTTPException(status_code=401, detail=str(e))
    _clear_login_failures(body.email)
    return _token_response(user)


@app.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return auth._public(user)


@app.patch("/auth/profile")
def update_profile(body: ProfileBody, user: dict = Depends(require_doctor)):
    return auth.update_profile(user["id"], body.specialty, body.clinic)


@app.get("/doctors")
def list_doctors(user: dict = Depends(current_user)):
    """Directory of doctors (for patients to find/request a doctor)."""
    return auth.list_doctors()


# --- Patient routes ----------------------------------------------------------
@app.get("/patients")
def list_patients(user: dict = Depends(current_user)):
    if user["role"] == "doctor":
        # Only this doctor's patients, and only those active within retention.
        return patients.list_patients_for_doctor(
            user["id"], active_since=retention.active_since()
        )
    # Patients see only themselves.
    p = patients.get_patient(user.get("patient_id") or "")
    return [p] if p else []


@app.post("/patients")
def create_patient(body: CreatePatient, user: dict = Depends(require_doctor)):
    if not body.dob:
        raise HTTPException(
            status_code=400,
            detail="Date of birth is required (used to distinguish patients).",
        )
    _check_dob(body.dob)
    pid = patients.create_patient(
        body.name, dob=body.dob, sex=body.sex, phone=body.phone, address=body.address
    )
    patients.link_doctor_patient(user["id"], pid)  # creator is dealing with them
    return patients.get_patient(pid)


@app.get("/patients/{patient_id}")
def get_patient(patient_id: str, user: dict = Depends(current_user)):
    _authorize_patient(user, patient_id)
    _touch(patient_id)
    return patients.get_patient(patient_id)


@app.patch("/patients/{patient_id}")
def update_patient(
    patient_id: str, body: UpdatePatient, user: dict = Depends(current_user)
):
    _authorize_patient(user, patient_id)
    _check_dob(body.dob)
    _touch(patient_id)
    return patients.update_patient(patient_id, body.model_dump(exclude_none=True))


@app.get("/patients/{patient_id}/summary")
def patient_summary(
    patient_id: str, refresh: bool = False, user: dict = Depends(current_user)
):
    _authorize_patient(user, patient_id)
    return _gemini_guard(
        lambda: rag_summarize(patient_id, refresh=refresh, doc_ids=_doc_scope(user, patient_id))
    )


@app.get("/patients/{patient_id}/care-team")
def care_team(patient_id: str, user: dict = Depends(current_user)):
    """Doctors who have treated this patient (from their visits)."""
    _authorize_patient(user, patient_id)
    return patients.list_care_team(patient_id)


# --- visits / episodes -------------------------------------------------------
@app.get("/patients/{patient_id}/visits")
def list_visits(patient_id: str, user: dict = Depends(current_user)):
    _authorize_patient(user, patient_id)
    # A doctor sees only their own visits; the patient sees all of theirs.
    doctor_id = user["id"] if user["role"] == "doctor" else None
    return patients.list_visits(patient_id, doctor_id=doctor_id)


@app.post("/patients/{patient_id}/visits")
def create_visit(
    patient_id: str, body: CreateVisit, user: dict = Depends(current_user)
):
    _authorize_patient(user, patient_id)
    _touch(patient_id)
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="A reason for the visit is required.")
    if user["role"] == "doctor":
        doctor_id, doctor_name = user["id"], user["name"]
        patients.link_doctor_patient(user["id"], patient_id)
    elif body.doctor_id:
        # Patient requested a specific doctor.
        doc = auth.get_user(body.doctor_id)
        if doc is None or doc["role"] != "doctor":
            raise HTTPException(status_code=400, detail="Unknown doctor.")
        doctor_id, doctor_name = doc["id"], doc["name"]
        patients.link_doctor_patient(doctor_id, patient_id)
    else:
        doctor_id, doctor_name = None, "Self-recorded"
    return patients.create_visit(patient_id, body.title.strip(), doctor_id, doctor_name)


@app.post("/visits/{visit_id}/close")
def close_visit(visit_id: str, user: dict = Depends(current_user)):
    visit = patients.get_visit(visit_id)
    if visit is None:
        raise HTTPException(status_code=404, detail="Visit not found")
    _authorize_patient(user, visit["patient_id"])
    if user["role"] == "doctor" and visit.get("doctor_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your visit.")
    _touch(visit["patient_id"])
    return patients.set_visit_status(visit_id, "closed")


@app.get("/visits/{visit_id}/summary")
def visit_summary(visit_id: str, user: dict = Depends(current_user)):
    visit = patients.get_visit(visit_id)
    if visit is None:
        raise HTTPException(status_code=404, detail="Visit not found")
    _authorize_patient(user, visit["patient_id"])
    if user["role"] == "doctor" and visit.get("doctor_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your visit.")
    return _gemini_guard(lambda: rag_summarize_visit(visit["patient_id"], visit_id))


@app.get("/patients/{patient_id}/documents")
def list_documents(
    patient_id: str, visit_id: str | None = None, user: dict = Depends(current_user)
):
    _authorize_patient(user, patient_id)
    # A doctor sees only documents in their visits / uploaded by them / shared.
    doctor_id = user["id"] if user["role"] == "doctor" else None
    return patients.list_documents(patient_id, visit_id=visit_id, doctor_id=doctor_id)


@app.patch("/documents/{doc_id}")
def move_document(doc_id: str, body: MoveDoc, user: dict = Depends(current_user)):
    """Re-file an existing document under a visit (or general)."""
    doc = patients.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    _authorize_patient(user, doc["patient_id"])
    vid = body.visit_id or None
    if vid:
        visit = patients.get_visit(vid)
        if visit is None or visit["patient_id"] != doc["patient_id"]:
            raise HTTPException(status_code=400, detail="Visit not found for this patient.")
    old_vid = doc.get("visit_id")
    _touch(doc["patient_id"])
    patients.set_document_visit(doc_id, vid)
    try:
        vectorstore.update_doc_visit(doc["patient_id"], doc_id, vid)
    except Exception:
        # Keep the catalog and vector store consistent on partial failure.
        patients.set_document_visit(doc_id, old_vid)
        raise HTTPException(status_code=502, detail="Could not move the document; no change made.")
    return patients.get_document(doc_id)


# --- document sharing (patient controls what a doctor can see) ---------------
def _own_document_or_403(user: dict, doc: dict) -> None:
    if user["role"] != "patient" or user.get("patient_id") != doc["patient_id"]:
        raise HTTPException(
            status_code=403, detail="Only the patient can manage document sharing."
        )


@app.get("/documents/{doc_id}/shares")
def document_shares(doc_id: str, user: dict = Depends(current_user)):
    doc = patients.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    _own_document_or_403(user, doc)
    return patients.list_doc_shares(doc_id)


@app.post("/documents/{doc_id}/share")
def share_document(doc_id: str, body: ShareBody, user: dict = Depends(current_user)):
    doc = patients.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    _own_document_or_403(user, doc)
    d = auth.get_user(body.doctor_id)
    if d is None or d["role"] != "doctor":
        raise HTTPException(status_code=400, detail="Unknown doctor.")
    patients.share_document(doc_id, body.doctor_id)
    patients.link_doctor_patient(body.doctor_id, doc["patient_id"])  # so they can open the patient
    return {"shared": doc_id, "doctor_id": body.doctor_id}


@app.delete("/documents/{doc_id}/share/{doctor_id}")
def unshare_document(doc_id: str, doctor_id: str, user: dict = Depends(current_user)):
    doc = patients.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    _own_document_or_403(user, doc)
    patients.unshare_document(doc_id, doctor_id)
    return {"unshared": doc_id, "doctor_id": doctor_id}


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str, user: dict = Depends(current_user)):
    """Delete a document. Doctors can NEVER delete; a patient may delete their
    OWN upload only within DOC_DELETE_WINDOW_SECONDS (accidental upload)."""
    doc = patients.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    _authorize_patient(user, doc["patient_id"])

    if user["role"] == "doctor":
        raise HTTPException(
            status_code=403,
            detail="Documents are part of the medical record and can't be deleted by a doctor.",
        )
    # Patient: only their own upload, and only within the time window.
    if doc.get("uploaded_by") != user["id"]:
        raise HTTPException(status_code=403, detail="You can only delete documents you uploaded.")
    ingested = doc.get("ingested_at") or ""
    try:
        ts = datetime.fromisoformat(ingested)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        age = config.DOC_DELETE_WINDOW_SECONDS + 1  # unparseable -> treat as expired
    if age > config.DOC_DELETE_WINDOW_SECONDS:
        mins = config.DOC_DELETE_WINDOW_SECONDS // 60
        raise HTTPException(
            status_code=403,
            detail=f"This document can no longer be deleted (only within {mins} minutes of upload).",
        )

    vectorstore.delete_doc_chunks(doc["patient_id"], doc_id)
    patients.delete_document(doc_id)
    return {"deleted": doc_id}


@app.post("/patients/{patient_id}/documents")
def upload_document(
    patient_id: str,
    file: UploadFile = File(...),
    doc_type: str = Form("other"),
    doc_date: str | None = Form(None),
    visit_id: str | None = Form(None),
    user: dict = Depends(current_user),
):
    # NOTE: a plain `def` (not `async`) so Starlette runs this in a threadpool.
    # Ingestion makes blocking Gemini calls; running it on the event loop would
    # stall health probes and every other request.
    _authorize_patient(user, patient_id)
    _link_if_doctor(user, patient_id)
    _touch(patient_id)
    config.ensure_dirs()

    safe_name = Path(file.filename or "upload").name
    dest = config.UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}_{safe_name}"

    # Stream to disk, enforcing the size cap WHILE writing so an oversized body
    # can't fill the disk before we'd otherwise check it.
    limit = config.MAX_UPLOAD_BYTES
    written = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise ValueError("too-large")
                out.write(chunk)
    except ValueError:
        dest.unlink(missing_ok=True)
        mb = limit // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"File too large (max {mb} MB).")

    try:
        return ingest_document(
            patient_id, dest, doc_type=doc_type, doc_date=doc_date,
            visit_id=visit_id, uploaded_by=user["id"],
        )
    except NotMedicalDocument as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        dest.unlink(missing_ok=True)
        msg = str(e)
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg or "quota" in msg.lower():
            raise HTTPException(
                status_code=503,
                detail="The AI is rate-limited right now (Gemini quota reached). "
                "Please try again in a minute.",
            )
        raise HTTPException(status_code=422, detail=msg)


@app.post("/patients/{patient_id}/prescriptions")
def create_prescription(
    patient_id: str, body: PrescriptionBody, user: dict = Depends(require_doctor)
):
    """A doctor drafts a prescription in-portal; it's stored as a document in the
    patient's record (searchable like any upload), attributed to the doctor."""
    _authorize_patient(user, patient_id)
    meds = [m for m in body.medications if m.name and m.name.strip()]
    if not meds:
        raise HTTPException(status_code=400, detail="Add at least one medication.")
    body.medications = meds
    text = _render_prescription(body, user["name"], user.get("specialty"))
    date_label = body.doc_date or datetime.now(timezone.utc).date().isoformat()
    filename = f"Prescription {date_label}.txt"
    _touch(patient_id)
    return _gemini_guard(
        lambda: ingest_text(
            patient_id, text, filename, doc_type="rx",
            doc_date=body.doc_date, visit_id=body.visit_id, uploaded_by=user["id"],
        )
    )


@app.post("/patients/{patient_id}/ask")
def ask(patient_id: str, body: AskRequest, user: dict = Depends(current_user)):
    _authorize_patient(user, patient_id)
    _touch(patient_id)
    # Answer style follows the caller's role; optionally scoped to one visit.
    # NOTE: questions/answers are NOT stored — chat is private to this session and
    # is never visible to the other party (doctor or patient).
    return _gemini_guard(
        lambda: rag_ask(
            patient_id, body.question, role=user["role"],
            visit_id=body.visit_id, history=body.history,
            doc_ids=_doc_scope(user, patient_id),
        )
    )


# --- Static frontend (production single-container) ---------------------------
# When HC_STATIC_DIR points at a built React bundle, serve it from the same
# origin as the API. Mounted last so it never shadows the API routes above.
import os

_static_dir = os.getenv("HC_STATIC_DIR")
if _static_dir and Path(_static_dir).is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
