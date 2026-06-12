import { useEffect, useState } from "react";
import { api, type Demographics, type Patient } from "../api";

type Props = {
  patient: Patient;
  onSaved: (p: Patient) => void;
};

function ageFromDob(dob: string | null): string | null {
  if (!dob) return null;
  const d = new Date(dob);
  if (isNaN(d.getTime())) return null;
  const now = new Date();
  let age = now.getFullYear() - d.getFullYear();
  const m = now.getMonth() - d.getMonth();
  if (m < 0 || (m === 0 && now.getDate() < d.getDate())) age--;
  return age >= 0 && age < 150 ? `${age}` : null;
}

export default function PatientSummary({ patient, onSaved }: Props) {
  const [summary, setSummary] = useState<string>("");
  const [hasRecords, setHasRecords] = useState(true);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);

  // Fetch the AI summary whenever the patient changes.
  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .getSummary(patient.id)
      .then((r) => {
        if (!live) return;
        setSummary(r.summary);
        setHasRecords(r.has_records);
      })
      .catch(() => live && setSummary(""))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [patient.id]);

  const age = ageFromDob(patient.dob);

  return (
    <section className="summary-card">
      <div className="summary-demographics">
        <div className="sd-head">
          <h2>Patient details</h2>
          <button className="sd-edit" onClick={() => setEditing((v) => !v)}>
            {editing ? "Cancel" : "Edit"}
          </button>
        </div>

        {editing ? (
          <EditForm
            patient={patient}
            onSaved={(p) => {
              onSaved(p);
              setEditing(false);
            }}
          />
        ) : (
          <dl className="sd-grid">
            <Field label="Name" value={patient.name} />
            <Field
              label="Age / DOB"
              value={
                patient.dob ? `${age ? age + " yrs · " : ""}${patient.dob}` : null
              }
            />
            <Field label="Sex" value={patient.sex} />
            <Field label="Phone" value={patient.phone} />
            <Field label="Address" value={patient.address} wide />
          </dl>
        )}
      </div>

      <div className="summary-ai">
        <h2>Summary</h2>
        {loading ? (
          <div className="summary-loading">
            <span className="typing">
              <i></i>
              <i></i>
              <i></i>
            </span>
            Generating summary…
          </div>
        ) : !hasRecords ? (
          <p className="summary-empty">
            No records yet — upload a document to generate a summary.
          </p>
        ) : (
          <p className="summary-text">{summary}</p>
        )}
      </div>
    </section>
  );
}

function Field({
  label,
  value,
  wide,
}: {
  label: string;
  value: string | null;
  wide?: boolean;
}) {
  return (
    <div className={`sd-field ${wide ? "wide" : ""}`}>
      <dt>{label}</dt>
      <dd className={value ? "" : "muted"}>{value || "—"}</dd>
    </div>
  );
}

function EditForm({
  patient,
  onSaved,
}: {
  patient: Patient;
  onSaved: (p: Patient) => void;
}) {
  const [form, setForm] = useState<Demographics>({
    name: patient.name,
    dob: patient.dob ?? "",
    sex: patient.sex ?? "",
    phone: patient.phone ?? "",
    address: patient.address ?? "",
  });
  const [saving, setSaving] = useState(false);

  function set<K extends keyof Demographics>(k: K, v: string) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await api.updatePatient(patient.id, form);
      onSaved(updated);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="sd-edit-form" onSubmit={save}>
      <label>
        <span>Name</span>
        <input value={form.name} onChange={(e) => set("name", e.target.value)} />
      </label>
      <label>
        <span>Date of birth</span>
        <input
          type="date"
          value={form.dob}
          onChange={(e) => set("dob", e.target.value)}
        />
      </label>
      <label>
        <span>Sex</span>
        <select value={form.sex} onChange={(e) => set("sex", e.target.value)}>
          <option value="">—</option>
          <option value="M">Male</option>
          <option value="F">Female</option>
          <option value="Other">Other</option>
        </select>
      </label>
      <label>
        <span>Phone</span>
        <input value={form.phone} onChange={(e) => set("phone", e.target.value)} />
      </label>
      <label className="wide">
        <span>Address</span>
        <input
          value={form.address}
          onChange={(e) => set("address", e.target.value)}
        />
      </label>
      <button type="submit" disabled={saving}>
        {saving ? "Saving…" : "Save details"}
      </button>
    </form>
  );
}
