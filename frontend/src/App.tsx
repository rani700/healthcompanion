import { useCallback, useEffect, useState } from "react";
import { api, AuthExpired, type Document, type Patient } from "./api";
import { useAuth } from "./auth";
import Login from "./components/Login";
import Sidebar from "./components/Sidebar";
import DocumentsPanel from "./components/DocumentsPanel";
import AskPanel, { type Message } from "./components/AskPanel";

export default function App() {
  const { user, ready, logout } = useAuth();

  const [patients, setPatients] = useState<Patient[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      .listPatients()
      .then((ps) => {
        setPatients(ps);
        if (user.role === "patient") {
          setSelectedId(user.patient_id ?? ps[0]?.id ?? null);
        }
      })
      .catch(handle);
  }, [user, handle]);

  const loadDocuments = useCallback(
    (id: string) => {
      api.listDocuments(id).then(setDocuments).catch(handle);
    },
    [handle],
  );

  useEffect(() => {
    if (!selectedId) return;
    setMessages([]);
    loadDocuments(selectedId);
  }, [selectedId, loadDocuments]);

  // --- actions --------------------------------------------------------------
  async function createPatient(name: string) {
    try {
      const p = await api.createPatient(name);
      setPatients((prev) => [...prev, p]);
      setSelectedId(p.id);
    } catch (e) {
      handle(e);
    }
  }

  async function upload(file: File, docType: string, docDate: string) {
    if (!selectedId) return;
    setUploading(true);
    setError(null);
    try {
      await api.uploadDocument(selectedId, file, docType, docDate);
      loadDocuments(selectedId);
    } catch (e) {
      handle(e);
    } finally {
      setUploading(false);
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
      .ask(selectedId, question)
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
        onSelect={setSelectedId}
        onCreate={createPatient}
        onLogout={logout}
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
                <h1>{selected.name}</h1>
                <span className="patient-id">{selected.id}</span>
              </div>
              <span className={`role-tag ${user.role}`}>{user.role} view</span>
            </header>

            <div className="workspace">
              <DocumentsPanel documents={documents} busy={uploading} onUpload={upload} />
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
            <span className="welcome-mark" aria-hidden>
              ✚
            </span>
            <h1>{user.role === "doctor" ? "Select a patient" : "Welcome"}</h1>
            <p>
              {user.role === "doctor"
                ? "Choose someone from the roster, or add a new patient, to view their records and ask grounded questions."
                : "Your record is loading. Upload a document to get started."}
            </p>
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
