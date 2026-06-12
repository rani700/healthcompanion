import { useState } from "react";
import type { NewPatient, Patient, ProfileFields, Scope, User } from "../api";

type Props = {
  user: User;
  patients: Patient[];
  selectedId: string | null;
  scope: Scope;
  onScopeChange: (s: Scope) => void;
  onSelect: (id: string) => void;
  onCreate: (payload: NewPatient) => Promise<void>;
  onUpdateProfile: (fields: ProfileFields) => Promise<void>;
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
  onUpdateProfile,
  onLogout,
  onHome,
}: Props) {
  const [name, setName] = useState("");
  const [dob, setDob] = useState("");
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [editingProfile, setEditingProfile] = useState(false);
  const [specialty, setSpecialty] = useState(user.specialty ?? "");
  const [clinic, setClinic] = useState(user.clinic ?? "");
  const [savingProfile, setSavingProfile] = useState(false);
  const isDoctor = user.role === "doctor";

  async function saveProfile(e: React.FormEvent) {
    e.preventDefault();
    setSavingProfile(true);
    try {
      await onUpdateProfile({ specialty, clinic });
      setEditingProfile(false);
    } finally {
      setSavingProfile(false);
    }
  }

  const q = query.trim().toLowerCase();
  const visible = q
    ? patients.filter(
        (p) => p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q),
      )
    : patients;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || !dob) return;
    setAdding(true);
    try {
      await onCreate({ name: trimmed, dob });
      setName("");
      setDob("");
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
            <div className="add-patient-row">
              <input
                type="date"
                value={dob}
                onChange={(e) => setDob(e.target.value)}
                aria-label="Date of birth (required)"
                title="Date of birth (required)"
              />
              <button type="submit" disabled={adding || !name.trim() || !dob}>
                {adding ? "…" : "Add"}
              </button>
            </div>
          </form>
        )}
      </div>

      <div className="user-block">
        <div className="user-card">
          <span className="avatar small">{initials(user.name)}</span>
          <span className="user-meta">
            <span className="user-name">{user.name}</span>
            <span className="user-sub">
              {isDoctor && (user.specialty || user.clinic)
                ? [user.specialty, user.clinic].filter(Boolean).join(" · ")
                : user.role}
            </span>
          </span>
          {isDoctor && (
            <button
              className="profile-edit"
              onClick={() => setEditingProfile((v) => !v)}
              title="Edit profile"
            >
              ✎
            </button>
          )}
          <button className="logout" onClick={onLogout} title="Sign out">
            ⏻
          </button>
        </div>

        {isDoctor && editingProfile && (
          <form className="profile-form" onSubmit={saveProfile}>
            <input
              value={specialty}
              onChange={(e) => setSpecialty(e.target.value)}
              placeholder="Specialty (e.g. Cardiology)"
              aria-label="Specialty"
            />
            <input
              value={clinic}
              onChange={(e) => setClinic(e.target.value)}
              placeholder="Clinic / hospital"
              aria-label="Clinic"
            />
            <button type="submit" disabled={savingProfile}>
              {savingProfile ? "Saving…" : "Save profile"}
            </button>
          </form>
        )}
      </div>
    </aside>
  );
}
