import { useEffect, useState } from "react";
import { api, type CareTeamMember, type Doctor, type Role, type Visit } from "../api";

type Props = {
  visits: Visit[];
  careTeam: CareTeamMember[];
  role: Role;
  doctors: Doctor[];
  selectedVisitId: string | null;
  onSelectVisit: (id: string | null) => void;
  onCreate: (title: string, doctorId?: string) => Promise<void>;
  onClose: (visitId: string) => Promise<void>;
};

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

export default function VisitsPanel({
  visits,
  careTeam,
  role,
  doctors,
  selectedVisitId,
  onSelectVisit,
  onCreate,
  onClose,
}: Props) {
  const [title, setTitle] = useState("");
  const [doctorId, setDoctorId] = useState("");
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<string>("");
  const [summaryLoading, setSummaryLoading] = useState(false);

  // Fetch the per-visit summary when a visit is selected.
  useEffect(() => {
    if (!selectedVisitId) {
      setSummary("");
      return;
    }
    let live = true;
    setSummaryLoading(true);
    api
      .visitSummary(selectedVisitId)
      .then((r) => live && setSummary(r.has_records ? r.summary : "No documents in this visit yet."))
      .catch(() => live && setSummary(""))
      .finally(() => live && setSummaryLoading(false));
    return () => {
      live = false;
    };
  }, [selectedVisitId]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const t = title.trim();
    if (!t) return;
    setBusy(true);
    try {
      await onCreate(t, doctorId || undefined);
      setTitle("");
      setDoctorId("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="visits-panel">
      <div className="visits-head">
        <h2>Visits / episodes</h2>
        {careTeam.length > 0 && (
          <div className="care-team">
            <span className="care-team-label">Care team:</span>
            {careTeam.map((d) => (
              <span key={d.doctor_id} className="care-chip" title={`${d.visits} visit(s)`}>
                {d.doctor_name}
              </span>
            ))}
          </div>
        )}
      </div>

      <form className="visit-create" onSubmit={submit}>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="New visit — reason / health issue (e.g. fever & cough)"
          aria-label="Reason for visit"
        />
        {role === "patient" && (
          <select
            value={doctorId}
            onChange={(e) => setDoctorId(e.target.value)}
            aria-label="Doctor"
            title="Choose a doctor or self-record"
          >
            <option value="">Self-recorded</option>
            {doctors.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
                {d.specialty ? ` — ${d.specialty}` : ""}
                {d.clinic ? ` (${d.clinic})` : ""}
              </option>
            ))}
          </select>
        )}
        <button type="submit" disabled={busy || !title.trim()}>
          {busy ? "…" : role === "patient" && doctorId ? "Request visit" : "Start visit"}
        </button>
      </form>

      <ul className="visit-list">
        {visits.map((v) => {
          const active = v.id === selectedVisitId;
          return (
            <li key={v.id} className={`visit ${active ? "active" : ""}`}>
              <button
                className="visit-main"
                onClick={() => onSelectVisit(active ? null : v.id)}
                title={active ? "Click to view all records" : "Click to focus this visit"}
              >
                <span className={`visit-badge ${v.status}`}>{v.status}</span>
                <span className="visit-body">
                  <span className="visit-title">{v.title}</span>
                  <span className="visit-meta">
                    {v.doctor_name} · {fmtDate(v.started_at)} · {v.n_docs} doc
                    {v.n_docs === 1 ? "" : "s"}
                  </span>
                </span>
              </button>
              {v.status === "open" && (
                <button className="visit-close" onClick={() => onClose(v.id)}>
                  Close
                </button>
              )}
            </li>
          );
        })}
        {visits.length === 0 && (
          <li className="empty-hint">
            No visits yet — start one to group this episode's documents.
          </li>
        )}
      </ul>

      {selectedVisitId && (
        <div className="visit-summary">
          <span className="visit-summary-label">Visit summary</span>
          {summaryLoading ? (
            <span className="visit-summary-loading">Generating…</span>
          ) : (
            <p>{summary}</p>
          )}
        </div>
      )}
    </section>
  );
}
