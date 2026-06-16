// Thin client over the HealthCompanion FastAPI backend.

// API base resolution:
//  - explicit VITE_API_BASE wins (any environment)
//  - else on the Vite dev server (:5173) target the local backend on :8000
//  - else (production single-container) use same-origin relative URLs
const envBase = import.meta.env.VITE_API_BASE;
const BASE =
  envBase && envBase.length > 0
    ? envBase
    : location.port === "5173"
      ? "http://localhost:8000"
      : "";

export type Role = "patient" | "doctor";

export type User = {
  id: string;
  email: string;
  role: Role;
  patient_id: string | null;
  name: string;
  specialty: string | null;
  clinic: string | null;
};

export type ProfileFields = { specialty?: string; clinic?: string };

export type AuthResponse = { token: string; user: User };

export type Patient = {
  id: string;
  name: string;
  dob: string | null;
  sex: string | null;
  phone: string | null;
  address: string | null;
  created_at: string;
};

export type Demographics = {
  name?: string;
  dob?: string;
  sex?: string;
  phone?: string;
  address?: string;
};

export type NewPatient = { name: string } & Demographics;

export type PatientSummary = {
  summary: string;
  has_records: boolean;
  cached?: boolean;
};

export type Scope = "mine" | "all";

export type Document = {
  id: string;
  patient_id: string;
  filename: string;
  doc_type: string;
  doc_date: string | null;
  ingested_at: string;
  n_chunks: number;
  visit_id: string | null;
  uploaded_by: string | null;
};

export type Visit = {
  id: string;
  patient_id: string;
  doctor_id: string | null;
  doctor_name: string;
  title: string;
  status: "open" | "closed";
  started_at: string;
  closed_at: string | null;
  n_docs: number;
};

export type CareTeamMember = {
  doctor_id: string;
  doctor_name: string;
  visits: number;
  last_seen: string;
};

export type Doctor = {
  id: string;
  name: string;
  specialty: string | null;
  clinic: string | null;
};

export type Source = {
  filename: string;
  doc_type: string;
  doc_date: string;
};

export type AskResult = {
  answer: string;
  sources: Source[];
  used_chunks: number;
};

// --- token handling ----------------------------------------------------------
let authToken: string | null = null;
export function setAuthToken(token: string | null) {
  authToken = token;
}

/** Thrown on 401 so the app can drop the session and show login. */
export class AuthExpired extends Error {}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  // A 401 only means "session expired" when we actually had a token. A 401 on a
  // login/signup attempt (no token) is a credentials error — surface its message.
  if (res.status === 401 && authToken) {
    throw new AuthExpired("Your session has expired. Please sign in again.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

function jsonBody(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const api = {
  // auth
  signup(
    email: string,
    password: string,
    name: string,
    role: Role,
    extra: Demographics & ProfileFields = {},
  ) {
    return request<AuthResponse>(
      "/auth/signup",
      jsonBody("POST", { email, password, name, role, ...extra }),
    );
  },
  login(email: string, password: string) {
    return request<AuthResponse>(
      "/auth/login",
      jsonBody("POST", { email, password }),
    );
  },
  me() {
    return request<User>("/auth/me");
  },
  updateProfile(fields: ProfileFields) {
    return request<User>("/auth/profile", jsonBody("PATCH", fields));
  },

  // patients (doctors get only their care patients; patients get themselves)
  listPatients() {
    return request<Patient[]>("/patients");
  },
  createPatient(payload: NewPatient) {
    return request<Patient>("/patients", jsonBody("POST", payload));
  },
  updatePatient(id: string, fields: Demographics) {
    return request<Patient>(`/patients/${id}`, jsonBody("PATCH", fields));
  },
  getSummary(id: string, refresh = false) {
    return request<PatientSummary>(
      `/patients/${id}/summary?refresh=${refresh}`,
    );
  },
  listDocuments(patientId: string, visitId?: string) {
    const q = visitId ? `?visit_id=${visitId}` : "";
    return request<Document[]>(`/patients/${patientId}/documents${q}`);
  },
  moveDocument(docId: string, visitId: string | null) {
    return request<Document>(
      `/documents/${docId}`,
      jsonBody("PATCH", { visit_id: visitId }),
    );
  },
  deleteDocument(docId: string) {
    return request<{ deleted: string }>(`/documents/${docId}`, {
      method: "DELETE",
    });
  },
  documentShares(docId: string) {
    return request<string[]>(`/documents/${docId}/shares`);
  },
  shareDocument(docId: string, doctorId: string) {
    return request(`/documents/${docId}/share`, jsonBody("POST", { doctor_id: doctorId }));
  },
  unshareDocument(docId: string, doctorId: string) {
    return request(`/documents/${docId}/share/${doctorId}`, { method: "DELETE" });
  },
  uploadDocument(
    patientId: string,
    file: File,
    docType: string,
    docDate: string,
    visitId?: string,
  ) {
    const form = new FormData();
    form.append("file", file);
    form.append("doc_type", docType);
    if (docDate) form.append("doc_date", docDate);
    if (visitId) form.append("visit_id", visitId);
    return request<Document>(`/patients/${patientId}/documents`, {
      method: "POST",
      body: form,
    });
  },

  // visits / episodes
  listVisits(patientId: string) {
    return request<Visit[]>(`/patients/${patientId}/visits`);
  },
  createVisit(patientId: string, title: string, doctorId?: string) {
    return request<Visit>(
      `/patients/${patientId}/visits`,
      jsonBody("POST", { title, doctor_id: doctorId ?? null }),
    );
  },
  closeVisit(visitId: string) {
    return request<Visit>(`/visits/${visitId}/close`, { method: "POST" });
  },
  visitSummary(visitId: string) {
    return request<PatientSummary>(`/visits/${visitId}/summary`);
  },
  careTeam(patientId: string) {
    return request<CareTeamMember[]>(`/patients/${patientId}/care-team`);
  },
  listDoctors() {
    return request<Doctor[]>("/doctors");
  },
  ask(
    patientId: string,
    question: string,
    visitId?: string,
    history?: { role: string; text: string }[],
  ) {
    return request<AskResult>(
      `/patients/${patientId}/ask`,
      jsonBody("POST", {
        question,
        visit_id: visitId ?? null,
        history: history ?? null,
      }),
    );
  },
};
