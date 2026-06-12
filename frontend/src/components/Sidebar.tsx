import { useState } from "react";
import type { Patient, Scope, User } from "../api";

type Props = {
  user: User;
  patients: Patient[];
  selectedId: string | null;
  scope: Scope;
  onScopeChange: (s: Scope) => void;
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
  scope,
  onScopeChange,
  onSelect,
  onCreate,
  onLogout,
  onHome,
}: Props) {
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const isDoctor = user.role === "doctor";

  const q = query.trim().toLowerCase();
  const visible = q
    ? patients.filter(
        (p) => p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q),
      )
    : patients;

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
          {isDoctor && (
            <span className="count">
              {q ? `${visible.length}/${patients.length}` : patients.length}
            </span>
          )}
        </div>

        {isDoctor && (
          <div className="scope-toggle" role="group" aria-label="Patient scope">
            <button
              className={scope === "mine" ? "on" : ""}
              onClick={() => onScopeChange("mine")}
            >
              My patients
            </button>
            <button
              className={scope === "all" ? "on" : ""}
              onClick={() => onScopeChange("all")}
            >
              All
            </button>
          </div>
        )}

        {isDoctor && patients.length > 0 && (
          <div className="patient-search">
            <span className="search-icon" aria-hidden>
              ⌕
            </span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search patients…"
              aria-label="Search patients"
            />
            {query && (
              <button
                className="search-clear"
                onClick={() => setQuery("")}
                aria-label="Clear search"
                title="Clear"
              >
                ×
              </button>
            )}
          </div>
        )}

        <ul className="patient-list">
          {visible.map((p) => (
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
          {patients.length > 0 && visible.length === 0 && (
            <li className="empty-hint">No patients match “{query}”.</li>
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
