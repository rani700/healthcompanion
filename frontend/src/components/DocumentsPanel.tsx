import { useEffect, useRef, useState } from "react";
import type { Document, Visit } from "../api";

type Props = {
  documents: Document[];
  visits: Visit[];
  activeVisitId: string | null;
  busy: boolean;
  onUpload: (
    file: File,
    docType: string,
    docDate: string,
    visitId: string,
  ) => Promise<void>;
  onMove: (docId: string, visitId: string | null) => Promise<void>;
  onDelete: (docId: string) => Promise<void>;
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
  onUpload,
  onMove,
  onDelete,
}: Props) {
  const [docType, setDocType] = useState("rx");
  const [docDate, setDocDate] = useState("");
  const [visitId, setVisitId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // When a visit is focused, default uploads to that visit.
  useEffect(() => {
    setVisitId(activeVisitId ?? "");
  }, [activeVisitId]);

  // Visits selectable for an upload: open ones, plus the focused visit.
  const selectable = visits.filter(
    (v) => v.status === "open" || v.id === activeVisitId,
  );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    await onUpload(file, docType, docDate, visitId);
    setFile(null);
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
          className={`dropzone ${dragOver ? "over" : ""} ${file ? "has-file" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            const f = e.dataTransfer.files?.[0];
            if (f) setFile(f);
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.png,.jpg,.jpeg,.webp,.heic,.heif,.txt"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            hidden
          />
          {file ? (
            <span className="drop-file">{file.name}</span>
          ) : (
            <span className="drop-cue">
              Drop a report or prescription
              <em>scans &amp; handwriting welcome</em>
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
          <button type="submit" disabled={!file || busy}>
            {busy ? "Reading…" : "Ingest"}
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

      <ul className="doc-list">
        {documents.map((d) => (
          <li key={d.id} className="doc">
            <span className="doc-glyph">{TYPE_GLYPH[d.doc_type] ?? "▤"}</span>
            <span className="doc-meta">
              <span className="doc-name">{d.filename}</span>
              <span className="doc-sub">
                {d.doc_type} · {d.doc_date || "undated"}
              </span>
            </span>
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
            <button
              className="doc-delete"
              onClick={() => onDelete(d.id)}
              title="Delete document"
              aria-label="Delete document"
            >
              ✕
            </button>
          </li>
        ))}
        {documents.length === 0 && (
          <li className="empty-hint">No records ingested for this patient.</li>
        )}
      </ul>
    </section>
  );
}
