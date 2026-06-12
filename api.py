"""FastAPI over the RAG core, with authentication and access control.

Auth model:
  - Doctor users can access every patient.
  - Patient users can access only their own linked patient record.

    uvicorn api:app --reload

Routes:
    POST /auth/signup               -> create account (+ token)
    POST /auth/login                -> obtain token
    GET  /auth/me                   -> current user
    GET  /patients                  -> doctors: all; patients: self only
    POST /patients                  -> doctors only (create a bare patient record)
    POST /patients/{id}/documents   -> upload + ingest (multipart)
    GET  /patients/{id}/documents   -> list documents
    POST /patients/{id}/ask         -> grounded question (answer style follows role)
"""

from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

# Make the src/ package importable.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import config
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from healthcompanion import auth, patients, vectorstore
from healthcompanion.guardrails import NotMedicalDocument
from healthcompanion.ingest import ingest_document
from healthcompanion.rag import ask as rag_ask
from healthcompanion.rag import summarize_patient as rag_summarize
from healthcompanion.rag import summarize_visit as rag_summarize_visit
from healthcompanion.security import create_token, decode_token

app = FastAPI(title="HealthCompanion", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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


class CreateVisit(BaseModel):
    title: str
    doctor_id: str | None = None  # a patient may request a specific doctor


class MoveDoc(BaseModel):
    visit_id: str | None = None  # None/"" -> general (no visit)


def _link_if_doctor(user: dict, patient_id: str) -> None:
    """Record a care relationship when a doctor works with a patient."""
    if user["role"] == "doctor":
        patients.link_doctor_patient(user["id"], patient_id)


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
    """Doctors may access anyone; patients only their own record."""
    if patients.get_patient(patient_id) is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    if user["role"] == "doctor":
        return
    if user.get("patient_id") != patient_id:
        raise HTTPException(status_code=403, detail="You can only access your own records.")


def _token_response(user: dict) -> dict:
    token = create_token(user["id"], user["role"], user.get("patient_id"))
    return {"token": token, "user": user}


# --- Auth routes -------------------------------------------------------------
@app.post("/auth/signup")
def signup(body: SignupBody):
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
    try:
        user = auth.login(body.email, body.password)
    except auth.AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
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
def list_patients(scope: str = "all", user: dict = Depends(current_user)):
    if user["role"] == "doctor":
        if scope == "mine":
            return patients.list_patients_for_doctor(user["id"])
        return patients.list_patients()
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
    pid = patients.create_patient(
        body.name, dob=body.dob, sex=body.sex, phone=body.phone, address=body.address
    )
    patients.link_doctor_patient(user["id"], pid)  # creator is dealing with them
    return patients.get_patient(pid)


@app.get("/patients/{patient_id}")
def get_patient(patient_id: str, user: dict = Depends(current_user)):
    _authorize_patient(user, patient_id)
    _link_if_doctor(user, patient_id)
    return patients.get_patient(patient_id)


@app.patch("/patients/{patient_id}")
def update_patient(
    patient_id: str, body: UpdatePatient, user: dict = Depends(current_user)
):
    _authorize_patient(user, patient_id)
    return patients.update_patient(patient_id, body.model_dump(exclude_none=True))


@app.get("/patients/{patient_id}/summary")
def patient_summary(
    patient_id: str, refresh: bool = False, user: dict = Depends(current_user)
):
    _authorize_patient(user, patient_id)
    _link_if_doctor(user, patient_id)
    return rag_summarize(patient_id, refresh=refresh)


@app.get("/patients/{patient_id}/care-team")
def care_team(patient_id: str, user: dict = Depends(current_user)):
    """Doctors who have treated this patient (from their visits)."""
    _authorize_patient(user, patient_id)
    return patients.list_care_team(patient_id)


# --- visits / episodes -------------------------------------------------------
@app.get("/patients/{patient_id}/visits")
def list_visits(patient_id: str, user: dict = Depends(current_user)):
    _authorize_patient(user, patient_id)
    return patients.list_visits(patient_id)


@app.post("/patients/{patient_id}/visits")
def create_visit(
    patient_id: str, body: CreateVisit, user: dict = Depends(current_user)
):
    _authorize_patient(user, patient_id)
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
    return patients.set_visit_status(visit_id, "closed")


@app.get("/visits/{visit_id}/summary")
def visit_summary(visit_id: str, user: dict = Depends(current_user)):
    visit = patients.get_visit(visit_id)
    if visit is None:
        raise HTTPException(status_code=404, detail="Visit not found")
    _authorize_patient(user, visit["patient_id"])
    return rag_summarize_visit(visit["patient_id"], visit_id)


@app.get("/patients/{patient_id}/documents")
def list_documents(
    patient_id: str, visit_id: str | None = None, user: dict = Depends(current_user)
):
    _authorize_patient(user, patient_id)
    return patients.list_documents(patient_id, visit_id=visit_id)


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
    patients.set_document_visit(doc_id, vid)
    vectorstore.update_doc_visit(doc["patient_id"], doc_id, vid)
    return patients.get_document(doc_id)


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
    config.ensure_dirs()

    safe_name = Path(file.filename or "upload").name
    dest = config.UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}_{safe_name}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    # Reject oversized files.
    if dest.stat().st_size > config.MAX_UPLOAD_BYTES:
        dest.unlink(missing_ok=True)
        mb = config.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"File too large (max {mb} MB).")

    try:
        return ingest_document(
            patient_id, dest, doc_type=doc_type, doc_date=doc_date, visit_id=visit_id
        )
    except NotMedicalDocument as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/patients/{patient_id}/ask")
def ask(patient_id: str, body: AskRequest, user: dict = Depends(current_user)):
    _authorize_patient(user, patient_id)
    _link_if_doctor(user, patient_id)
    # Answer style follows the caller's role; optionally scoped to one visit.
    return rag_ask(patient_id, body.question, role=user["role"], visit_id=body.visit_id)


# --- Static frontend (production single-container) ---------------------------
# When HC_STATIC_DIR points at a built React bundle, serve it from the same
# origin as the API. Mounted last so it never shadows the API routes above.
import os

_static_dir = os.getenv("HC_STATIC_DIR")
if _static_dir and Path(_static_dir).is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
