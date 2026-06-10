import { useState } from "react";
import type { Patient, User } from "../api";

type Props = {
  user: User;
  patients: Patient[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: (name: string) => Promise<void>;
  onLogout: () => void;
  onHome: () => void;
};

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

export default function Sidebar({
  user,
  patients,
  selectedId,
  onSelect,
  onCreate,
  onLogout,
  onHome,
}: Props) {
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);
  const isDoctor = user.role === "doctor";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setAdding(true);
    try {
      await onCreate(trimmed);
      setName("");
    } finally {
      setAdding(false);
    }
  }

  return (
    <aside className="rail">
      <button className="brand" onClick={onHome} title="Home">
        <span className="brand-mark" aria-hidden>
          ✚
        </span>
        <div className="brand-text">
          <span className="brand-name">HealthCompanion</span>
          <span className="brand-sub">patient records</span>
        </div>
      </button>

      <div className="roster">
        <div className="roster-head">
          <span>{isDoctor ? "Patients" : "Your record"}</span>
          {isDoctor && <span className="count">{patients.length}</span>}
        </div>

        <ul className="patient-list">
          {patients.map((p) => (
            <li key={p.id}>
              <button
                className={`patient ${p.id === selectedId ? "active" : ""}`}
                onClick={() => onSelect(p.id)}
              >
                <span className="avatar">{initials(p.name)}</span>
                <span className="meta">
                  <span className="pname">{p.name}</span>
                  <span className="pid">{p.id}</span>
                </span>
              </button>
            </li>
          ))}
          {patients.length === 0 && (
            <li className="empty-hint">
              {isDoctor ? "No patients yet — add one below." : "No record found."}
            </li>
          )}
        </ul>

        {isDoctor && (
          <form className="add-patient" onSubmit={submit}>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="New patient name"
              aria-label="New patient name"
            />
            <button type="submit" disabled={adding || !name.trim()}>
              {adding ? "…" : "Add"}
            </button>
          </form>
        )}
      </div>

      <div className="user-card">
        <span className="avatar small">{initials(user.name)}</span>
        <span className="user-meta">
          <span className="user-name">{user.name}</span>
          <span className={`user-role ${user.role}`}>{user.role}</span>
        </span>
        <button className="logout" onClick={onLogout} title="Sign out">
          ⏻
        </button>
      </div>
    </aside>
  );
}
