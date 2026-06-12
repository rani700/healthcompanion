import { useCallback, useEffect, useState } from "react";
import {
  api,
  AuthExpired,
  type CareTeamMember,
  type Doctor,
  type Document,
  type NewPatient,
  type Patient,
  type Scope,
  type Visit,
} from "./api";
import { useAuth } from "./auth";
import Login from "./components/Login";
import Sidebar from "./components/Sidebar";
import DocumentsPanel from "./components/DocumentsPanel";
import AskPanel, { type Message } from "./components/AskPanel";
import PatientSummary from "./components/PatientSummary";
import AddPatientForm from "./components/AddPatientForm";
import VisitsPanel from "./components/VisitsPanel";

export default function App() {
  const { user, ready, logout, updateProfile } = useAuth();

  const [patients, setPatients] = useState<Patient[]>([]);
  const [scope, setScope] = useState<Scope>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [visits, setVisits] = useState<Visit[]>([]);
  const [careTeam, setCareTeam] = useState<CareTeamMember[]>([]);
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [selectedVisitId, setSelectedVisitId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [docVersion, setDocVersion] = useState(0); // bumps when docs change

  // Centralized error handling — drop the session on auth failures.
  const handle = useCallback(
    (e: unknown) => {
      if (e instanceof AuthExpired) {
        logout();
        return;
      }
      setError(describe(e));
    },
    [logout],
  );

  // Load the roster once authenticated. Patients are auto-scoped to themselves.
  useEffect(() => {
    if (!user) return;
    api
      .listPatients(scope)
      .then((ps) => {
        setPatients(ps);
        if (user.role === "patient") {
          setSelectedId(user.patient_id ?? ps[0]?.id ?? null);
        }
      })
      .catch(handle);
  }, [user, scope, handle]);

  const loadDocuments = useCallback(
    (id: string) => {
      api.listDocuments(id).then(setDocuments).catch(handle);
    },
    [handle],
  );

  const loadVisits = useCallback(
    (id: string) => {
      api.listVisits(id).then(setVisits).catch(handle);
      api.careTeam(id).then(setCareTeam).catch(handle);
    },
    [handle],
  );

  useEffect(() => {
    if (!selectedId) return;
    setMessages([]);
    setSelectedVisitId(null); // reset visit focus when switching patients
    loadDocuments(selectedId);
    loadVisits(selectedId);
  }, [selectedId, loadDocuments, loadVisits]);

  // Doctor directory (patients use it to request a doctor).
  useEffect(() => {
    if (!user) return;
    api.listDoctors().then(setDoctors).catch(() => setDoctors([]));
  }, [user]);

  // --- actions --------------------------------------------------------------
  async function createPatient(payload: NewPatient) {
    try {
      const p = await api.createPatient(payload);
      setPatients((prev) => [...prev, p]);
      setSelectedId(p.id);
    } catch (e) {
      handle(e);
    }
  }

  async function upload(
    file: File,
    docType: string,
    docDate: string,
    visitId: string,
  ) {
    if (!selectedId) return;
    setUploading(true);
    setError(null);
    try {
      await api.uploadDocument(selectedId, file, docType, docDate, visitId);
      loadDocuments(selectedId);
      loadVisits(selectedId); // visit doc-counts changed
      setDocVersion((v) => v + 1); // records changed -> refresh summary
    } catch (e) {
      handle(e);
    } finally {
      setUploading(false);
    }
  }

  async function moveDoc(docId: string, visitId: string | null) {
    if (!selectedId) return;
    try {
      await api.moveDocument(docId, visitId);
      loadDocuments(selectedId);
      loadVisits(selectedId); // visit counts changed
      setDocVersion((v) => v + 1); // summaries may change
    } catch (e) {
      handle(e);
    }
  }

  async function createVisit(title: string, doctorId?: string) {
    if (!selectedId) return;
    try {
      await api.createVisit(selectedId, title, doctorId);
      loadVisits(selectedId);
    } catch (e) {
      handle(e);
    }
  }

  async function closeVisit(visitId: string) {
    if (!selectedId) return;
    try {
      await api.closeVisit(visitId);
      loadVisits(selectedId);
    } catch (e) {
      handle(e);
    }
  }

  function ask(question: string) {
    if (!selectedId) return;
    const userMsg: Message = { id: Date.now(), role: "user", text: question };
    const pendingId = Date.now() + 1;
    setMessages((m) => [
      ...m,
      userMsg,
      { id: pendingId, role: "assistant", text: "", pending: true },
    ]);

    api
      .ask(selectedId, question, selectedVisitId ?? undefined)
      .then((res) =>
        setMessages((m) =>
          m.map((msg) =>
            msg.id === pendingId
              ? { id: pendingId, role: "assistant", text: res.answer, sources: res.sources }
              : msg,
          ),
        ),
      )
      .catch((e) => {
        if (e instanceof AuthExpired) return handle(e);
        setMessages((m) =>
          m.map((msg) =>
            msg.id === pendingId
              ? { id: pendingId, role: "assistant", text: `⚠ ${describe(e)}` }
              : msg,
          ),
        );
      });
  }

  // --- gates ----------------------------------------------------------------
  if (!ready) {
    return <div className="splash">Loading…</div>;
  }
  if (!user) {
    return <Login />;
  }

  const selected = patients.find((p) => p.id === selectedId) ?? null;

  return (
    <div className="app">
      <Sidebar
        user={user}
        patients={patients}
        selectedId={selectedId}
        scope={scope}
        onScopeChange={setScope}
        onSelect={setSelectedId}
        onCreate={createPatient}
        onUpdateProfile={updateProfile}
        onLogout={logout}
        onHome={() => setSelectedId(null)}
      />

      <main className="stage">
        {error && (
          <div className="banner" onClick={() => setError(null)}>
            {error} <span className="banner-x">dismiss</span>
          </div>
        )}

        {selected ? (
          <>
            <header className="patient-header">
              <div>
                {user.role === "doctor" && (
                  <button
                    className="back-btn"
                    onClick={() => setSelectedId(null)}
                  >
                    ← All patients
                  </button>
                )}
                <h1>{selected.name}</h1>
                <span className="patient-id">{selected.id}</span>
              </div>
              <span className={`role-tag ${user.role}`}>{user.role} view</span>
            </header>

            <PatientSummary
              patient={selected}
              refreshSignal={docVersion}
              onSaved={(u) =>
                setPatients((prev) =>
                  prev.map((p) => (p.id === u.id ? u : p)),
                )
              }
            />

            <VisitsPanel
              visits={visits}
              careTeam={careTeam}
              role={user.role}
              doctors={doctors}
              selectedVisitId={selectedVisitId}
              onSelectVisit={setSelectedVisitId}
              onCreate={createVisit}
              onClose={closeVisit}
            />

            {selectedVisitId && (
              <div className="scope-banner">
                Focused on visit:{" "}
                <strong>
                  {visits.find((v) => v.id === selectedVisitId)?.title}
                </strong>{" "}
                — records &amp; questions are limited to this visit.
                <button onClick={() => setSelectedVisitId(null)}>
                  View all records
                </button>
              </div>
            )}

            <div className="workspace">
              <DocumentsPanel
                documents={
                  selectedVisitId
                    ? documents.filter((d) => d.visit_id === selectedVisitId)
                    : documents
                }
                visits={visits}
                activeVisitId={selectedVisitId}
                busy={uploading}
                onUpload={upload}
                onMove={moveDoc}
              />
              <AskPanel
                messages={messages}
                role={user.role}
                disabled={!selectedId}
                onAsk={ask}
              />
            </div>
          </>
        ) : (
          <div className="welcome">
            {user.role === "doctor" ? (
              <button
                className="welcome-mark clickable"
                title="Add a patient"
                onClick={() =>
                  document.getElementById("welcome-name-input")?.focus()
                }
              >
                ✚
              </button>
            ) : (
              <button
                className="welcome-mark clickable"
                title="Open my record"
                onClick={() =>
                  user.patient_id && setSelectedId(user.patient_id)
                }
              >
                ✚
              </button>
            )}
            <h1>{user.role === "doctor" ? "Select a patient" : "Welcome"}</h1>
            <p>
              {user.role === "doctor"
                ? "Choose someone from the roster, or add a new patient, to view their records and ask grounded questions."
                : "Open your record to add documents, start a visit, and ask questions."}
            </p>
            {user.role === "patient" && user.patient_id && (
              <button
                className="welcome-cta"
                onClick={() => setSelectedId(user.patient_id!)}
              >
                Open my record
              </button>
            )}
            {user.role === "doctor" && (
              <AddPatientForm onCreate={createPatient} />
            )}
          </div>
        )}
      </main>
    </div>
  );
}

function describe(e: unknown): string {
  if (e instanceof Error) {
    if (e.message === "Failed to fetch") {
      return "Can't reach the backend. Is the API running on :8000?";
    }
    return e.message;
  }
  return String(e);
}
