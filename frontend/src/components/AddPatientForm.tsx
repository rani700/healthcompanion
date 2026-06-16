import { useState } from "react";
import type { NewPatient } from "../api";

type Props = { onCreate: (p: NewPatient) => Promise<void> };

export default function AddPatientForm({ onCreate }: Props) {
  const [name, setName] = useState("");
  const [dob, setDob] = useState("");
  const [sex, setSex] = useState("");
  const [phone, setPhone] = useState("");
  const [address, setAddress] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      const payload: NewPatient = { name: trimmed };
      if (dob) payload.dob = dob;
      if (sex) payload.sex = sex;
      if (phone.trim()) payload.phone = phone.trim();
      if (address.trim()) payload.address = address.trim();
      await onCreate(payload);
      setName("");
      setDob("");
      setSex("");
      setPhone("");
      setAddress("");
      setShowDetails(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="welcome-add" onSubmit={submit}>
      <div className="welcome-add-main">
        <input
          id="welcome-name-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Patient's full name"
          aria-label="Patient's full name"
        />
        <input
          type="date"
          value={dob}
          max={new Date().toISOString().slice(0, 10)}
          onChange={(e) => setDob(e.target.value)}
          aria-label="Date of birth (required)"
          title="Date of birth (required)"
          required
        />
        <button type="submit" disabled={busy || !name.trim() || !dob}>
          {busy ? "…" : "Add patient"}
        </button>
      </div>
      <div className="welcome-add-hint">
        Date of birth is required — it distinguishes patients with the same name.
      </div>

      <button
        type="button"
        className="welcome-add-toggle"
        onClick={() => setShowDetails((v) => !v)}
      >
        {showDetails ? "− Hide details" : "+ Add contact details (optional)"}
      </button>

      {showDetails && (
        <div className="welcome-add-details">
          <label>
            <span>Sex</span>
            <select value={sex} onChange={(e) => setSex(e.target.value)}>
              <option value="">—</option>
              <option value="M">Male</option>
              <option value="F">Female</option>
              <option value="Other">Other</option>
            </select>
          </label>
          <label>
            <span>Phone</span>
            <input value={phone} onChange={(e) => setPhone(e.target.value)} />
          </label>
          <label className="wide">
            <span>Address</span>
            <input value={address} onChange={(e) => setAddress(e.target.value)} />
          </label>
        </div>
      )}
    </form>
  );
}
