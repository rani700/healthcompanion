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

from healthcompanion import auth, patients
from healthcompanion.ingest import ingest_document
from healthcompanion.rag import ask as rag_ask
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


class LoginBody(BaseModel):
    email: str
    password: str


class CreatePatient(BaseModel):
    name: str


class AskRequest(BaseModel):
    question: str


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
        user = auth.signup(body.email, body.password, body.name, body.role)
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


# --- Patient routes ----------------------------------------------------------
@app.get("/patients")
def list_patients(user: dict = Depends(current_user)):
    if user["role"] == "doctor":
        return patients.list_patients()
    # Patients see only themselves.
    p = patients.get_patient(user.get("patient_id") or "")
    return [p] if p else []


@app.post("/patients")
def create_patient(body: CreatePatient, _: dict = Depends(require_doctor)):
    pid = patients.create_patient(body.name)
    return {"id": pid, "name": body.name}


@app.get("/patients/{patient_id}/documents")
def list_documents(patient_id: str, user: dict = Depends(current_user)):
    _authorize_patient(user, patient_id)
    return patients.list_documents(patient_id)


@app.post("/patients/{patient_id}/documents")
async def upload_document(
    patient_id: str,
    file: UploadFile = File(...),
    doc_type: str = Form("other"),
    doc_date: str | None = Form(None),
    user: dict = Depends(current_user),
):
    _authorize_patient(user, patient_id)
    config.ensure_dirs()

    safe_name = Path(file.filename or "upload").name
    dest = config.UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}_{safe_name}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    try:
        return ingest_document(patient_id, dest, doc_type=doc_type, doc_date=doc_date)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/patients/{patient_id}/ask")
def ask(patient_id: str, body: AskRequest, user: dict = Depends(current_user)):
    _authorize_patient(user, patient_id)
    # Answer style follows the caller's role.
    return rag_ask(patient_id, body.question, role=user["role"])


# --- Static frontend (production single-container) ---------------------------
# When HC_STATIC_DIR points at a built React bundle, serve it from the same
# origin as the API. Mounted last so it never shadows the API routes above.
import os

_static_dir = os.getenv("HC_STATIC_DIR")
if _static_dir and Path(_static_dir).is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
