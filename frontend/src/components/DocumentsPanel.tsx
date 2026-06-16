import { useEffect, useRef, useState } from "react";
import type {
  Doctor,
  Document,
  Medication,
  PrescriptionDraft,
  User,
  Visit,
} from "../api";

// A patient may delete their own upload only within this window (mirror of the
// server rule) — used to decide whether to show the delete control.
const DELETE_WINDOW_MS = 60 * 60 * 1000;

type Props = {
  documents: Document[];
  visits: Visit[];
  activeVisitId: string | null;
  busy: boolean;
  currentUser: User;
  doctors: Doctor[];
  sharesByDoc: Record<string, string[]>;
  onUpload: (
    files: File[],
    docType: string,
    docDate: string,
    visitId: string,
  ) => Promise<void>;
  onPrescribe: (draft: PrescriptionDraft) => Promise<void>;
  onView: (docId: string) => Promise<void>;
  onMove: (docId: string, visitId: string | null) => Promise<void>;
  onDelete: (docId: string) => Promise<void>;
  onShare: (docId: string, doctorId: string) => Promise<void>;
  onUnshare: (docId: string, doctorId: string) => Promise<void>;
};

const DOC_TYPES = [
  { value: "rx", label: "Prescription" },
  { value: "lab", label: "Lab report" },
  { value: "note", label: "Clinical note" },
  { value: "other", label: "Other" },
];

const TYPE_GLYPH: Record<string, string> = {
  rx: "℞",
  lab: "⚗",
  note: "✎",
  other: "▤",
};

export default function DocumentsPanel({
  documents,
  visits,
  activeVisitId,
  busy,
  currentUser,
  doctors,
  sharesByDoc,
  onUpload,
  onPrescribe,
  onView,
  onMove,
  onDelete,
  onShare,
  onUnshare,
}: Props) {
  const isPatient = currentUser.role === "patient";
  const doctorName = (id: string) =>
    doctors.find((d) => d.id === id)?.name ?? "doctor";
  // Doctors can never delete; a patient may delete their own recent upload only.
  const canDelete = (d: Document) =>
    currentUser.role === "patient" &&
    d.uploaded_by === currentUser.id &&
    Date.now() - new Date(d.ingested_at).getTime() < DELETE_WINDOW_MS;
  const [docType, setDocType] = useState("rx");
  const [docDate, setDocDate] = useState("");
  const [visitId, setVisitId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Doctor-only in-portal prescription composer.
  const isDoctor = currentUser.role === "doctor";
  const blankMed = (): Medication => ({ name: "", dosage: "", frequency: "", duration: "" });
  const [composing, setComposing] = useState(false);
  const [diagnosis, setDiagnosis] = useState("");
  const [advice, setAdvice] = useState("");
  const [rxDate, setRxDate] = useState("");
  const [meds, setMeds] = useState<Medication[]>([blankMed()]);

  const setMed = (i: number, field: keyof Medication, value: string) =>
    setMeds((rows) => rows.map((m, j) => (j === i ? { ...m, [field]: value } : m)));
  const addMed = () => setMeds((rows) => [...rows, blankMed()]);
  const removeMed = (i: number) =>
    setMeds((rows) => (rows.length > 1 ? rows.filter((_, j) => j !== i) : rows));

  function resetRx() {
    setComposing(false);
    setDiagnosis("");
    setAdvice("");
    setRxDate("");
    setMeds([blankMed()]);
  }

  async function submitRx(e: React.FormEvent) {
    e.preventDefault();
    const clean = meds
      .map((m) => ({
        name: m.name.trim(),
        dosage: m.dosage?.trim() || undefined,
        frequency: m.frequency?.trim() || undefined,
        duration: m.duration?.trim() || undefined,
      }))
      .filter((m) => m.name);
    if (clean.length === 0) return;
    await onPrescribe({
      medications: clean,
      diagnosis: diagnosis.trim() || undefined,
      advice: advice.trim() || undefined,
      doc_date: rxDate || undefined,
      visit_id: visitId || null,
    });
    resetRx();
  }

  // Visits selectable for an upload: open ones, plus the focused visit.
  const selectable = visits.filter(
    (v) => v.status === "open" || v.id === activeVisitId,
  );
  const openVisitsKey = visits
    .filter((v) => v.status === "open")
    .map((v) => v.id)
    .join(",");

  // Default the upload to the focused visit, else the most recent open visit, so
  // documents land in the episode you're working on (not stranded in "General").
  useEffect(() => {
    const firstOpen = visits.find((v) => v.status === "open")?.id ?? "";
    setVisitId(activeVisitId ?? firstOpen);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeVisitId, openVisitsKey]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (files.length === 0) return;
    await onUpload(files, docType, docDate, visitId);
    setFiles([]);
    setDocDate("");
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <section className="panel documents">
      <div className="panel-head">
        <h2>Records</h2>
        <span className="panel-sub">{documents.length} on file</span>
      </div>

      <form className="uploader" onSubmit={submit}>
        <label
          className={`dropzone ${dragOver ? "over" : ""} ${files.length ? "has-file" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            const dropped = Array.from(e.dataTransfer.files ?? []);
            if (dropped.length) setFiles(dropped);
          }}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,.png,.jpg,.jpeg,.webp,.heic,.heif,.txt"
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            hidden
          />
          {files.length === 1 ? (
            <span className="drop-file">{files[0].name}</span>
          ) : files.length > 1 ? (
            <span className="drop-file">{files.length} files selected</span>
          ) : (
            <span className="drop-cue">
              Drop reports or prescriptions
              <em>several at once · scans &amp; handwriting welcome</em>
            </span>
          )}
        </label>

        <div className="upload-controls">
          <select value={docType} onChange={(e) => setDocType(e.target.value)}>
            {DOC_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          <input
            type="date"
            value={docDate}
            onChange={(e) => setDocDate(e.target.value)}
            aria-label="Document date"
          />
          <button type="submit" disabled={files.length === 0 || busy}>
            {busy
              ? "Reading…"
              : files.length > 1
                ? `Ingest ${files.length}`
                : "Ingest"}
          </button>
        </div>
        <label className="visit-attach">
          <span>File under</span>
          <select
            className="visit-select"
            value={visitId}
            onChange={(e) => setVisitId(e.target.value)}
            aria-label="Attach to visit"
          >
            <option value="">General (no visit)</option>
            {selectable.map((v) => (
              <option key={v.id} value={v.id}>
                Visit: {v.title}
                {v.status === "closed" ? " (closed)" : ""}
              </option>
            ))}
          </select>
        </label>
      </form>

      {isDoctor && !composing && (
        <button
          type="button"
          className="rx-open"
          onClick={() => setComposing(true)}
        >
          ℞ Draft a prescription
        </button>
      )}

      {isDoctor && composing && (
        <form className="rx-composer" onSubmit={submitRx}>
          <div className="rx-head">
            <h3>℞ New prescription</h3>
            <button type="button" className="rx-cancel" onClick={resetRx}>
              Cancel
            </button>
          </div>

          <input
            className="rx-field"
            placeholder="Diagnosis (optional)"
            value={diagnosis}
            onChange={(e) => setDiagnosis(e.target.value)}
          />

          <div className="rx-meds">
            {meds.map((m, i) => (
              <div className="rx-med" key={i}>
                <input
                  className="rx-med-name"
                  placeholder="Medication *"
                  value={m.name}
                  onChange={(e) => setMed(i, "name", e.target.value)}
                />
                <input
                  placeholder="Dosage (e.g. 500 mg)"
                  value={m.dosage}
                  onChange={(e) => setMed(i, "dosage", e.target.value)}
                />
                <input
                  placeholder="Frequency (e.g. twice daily)"
                  value={m.frequency}
                  onChange={(e) => setMed(i, "frequency", e.target.value)}
                />
                <input
                  placeholder="Duration (e.g. 7 days)"
                  value={m.duration}
                  onChange={(e) => setMed(i, "duration", e.target.value)}
                />
                <button
                  type="button"
                  className="rx-med-del"
                  onClick={() => removeMed(i)}
                  disabled={meds.length === 1}
                  aria-label="Remove medication"
                  title="Remove"
                >
                  ✕
                </button>
              </div>
            ))}
            <button type="button" className="rx-add" onClick={addMed}>
              + Add medication
            </button>
          </div>

          <textarea
            className="rx-field"
            placeholder="Advice / instructions (optional)"
            rows={2}
            value={advice}
            onChange={(e) => setAdvice(e.target.value)}
          />

          <div className="rx-foot">
            <label className="rx-date">
              <span>Date</span>
              <input
                type="date"
                max={new Date().toISOString().slice(0, 10)}
                value={rxDate}
                onChange={(e) => setRxDate(e.target.value)}
              />
            </label>
            <label className="rx-attach">
              <span>File under</span>
              <select
                value={visitId}
                onChange={(e) => setVisitId(e.target.value)}
                aria-label="Attach prescription to visit"
              >
                <option value="">General (no visit)</option>
                {selectable.map((v) => (
                  <option key={v.id} value={v.id}>
                    Visit: {v.title}
                    {v.status === "closed" ? " (closed)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              className="rx-save"
              disabled={busy || !meds.some((m) => m.name.trim())}
            >
              {busy ? "Saving…" : "Add to record"}
            </button>
          </div>
        </form>
      )}

      <ul className="doc-list">
        {documents.map((d) => {
          const shared = sharesByDoc[d.id] || [];
          const shareable = doctors.filter((dr) => !shared.includes(dr.id));
          return (
            <li key={d.id} className="doc">
              <div className="doc-top">
                <span className="doc-glyph">{TYPE_GLYPH[d.doc_type] ?? "▤"}</span>
                <span className="doc-meta">
                  <span className="doc-name">{d.filename}</span>
                  <span className="doc-sub">
                    {d.doc_type} · {d.doc_date || "undated"}
                  </span>
                </span>
                {d.has_file && (
                  <button
                    type="button"
                    className="doc-view"
                    onClick={() => onView(d.id)}
                    title="Open the original report / image"
                  >
                    View
                  </button>
                )}
                <select
                  className="doc-move"
                  value={d.visit_id ?? ""}
                  onChange={(e) => onMove(d.id, e.target.value || null)}
                  aria-label="File this document under a visit"
                  title="Move to a visit"
                >
                  <option value="">General</option>
                  {visits.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.title}
                      {v.status === "closed" ? " (closed)" : ""}
                    </option>
                  ))}
                </select>
                {canDelete(d) && (
                  <button
                    className="doc-delete"
                    onClick={() => onDelete(d.id)}
                    title="Delete (within 1 hour of upload)"
                    aria-label="Delete document"
                  >
                    ✕
                  </button>
                )}
              </div>

              {isPatient && (
                <div className="doc-share">
                  <span className="doc-share-label">🔒 Shared with</span>
                  {shared.length === 0 && (
                    <span className="doc-share-none">no one (private)</span>
                  )}
                  {shared.map((docId) => (
                    <span key={docId} className="share-chip">
                      {doctorName(docId)}
                      <button
                        onClick={() => onUnshare(d.id, docId)}
                        title="Stop sharing"
                        aria-label="Stop sharing"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                  {shareable.length > 0 && (
                    <select
                      className="doc-share-add"
                      value=""
                      onChange={(e) =>
                        e.target.value && onShare(d.id, e.target.value)
                      }
                      aria-label="Share with a doctor"
                    >
                      <option value="">+ Share with…</option>
                      {shareable.map((dr) => (
                        <option key={dr.id} value={dr.id}>
                          {dr.name}
                          {dr.specialty ? ` — ${dr.specialty}` : ""}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              )}
            </li>
          );
        })}
        {documents.length === 0 && (
          <li className="empty-hint">No records ingested for this patient.</li>
        )}
      </ul>
    </section>
  );
}
