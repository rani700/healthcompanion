import { useState } from "react";
import type { CareTeamMember, Visit } from "../api";

type Props = {
  visits: Visit[];
  careTeam: CareTeamMember[];
  onCreate: (title: string) => Promise<void>;
  onClose: (visitId: string) => Promise<void>;
};

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

export default function VisitsPanel({
  visits,
  careTeam,
  onCreate,
  onClose,
}: Props) {
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const t = title.trim();
    if (!t) return;
    setBusy(true);
    try {
      await onCreate(t);
      setTitle("");
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
        <button type="submit" disabled={busy || !title.trim()}>
          {busy ? "…" : "Start visit"}
        </button>
      </form>

      <ul className="visit-list">
        {visits.map((v) => (
          <li key={v.id} className="visit">
            <span className={`visit-badge ${v.status}`}>{v.status}</span>
            <div className="visit-body">
              <span className="visit-title">{v.title}</span>
              <span className="visit-meta">
                {v.doctor_name} · {fmtDate(v.started_at)} · {v.n_docs} doc
                {v.n_docs === 1 ? "" : "s"}
              </span>
            </div>
            {v.status === "open" && (
              <button className="visit-close" onClick={() => onClose(v.id)}>
                Close
              </button>
            )}
          </li>
        ))}
        {visits.length === 0 && (
          <li className="empty-hint">
            No visits yet — start one to group this episode's documents.
          </li>
        )}
      </ul>
    </section>
  );
}
