import { useState } from "react";
import { useAuth } from "../auth";
import type { Role } from "../api";

type Mode = "login" | "signup";

export default function Login() {
  const { login, signup } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [role, setRole] = useState<Role>("patient");
  const [name, setName] = useState("");
  const [dob, setDob] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (mode === "signup" && role === "patient" && !dob) {
      setError("Date of birth is required.");
      return;
    }
    setBusy(true);
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await signup(email, password, name, role, role === "patient" ? { dob } : {});
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-screen">
      <div className="auth-aside">
        <div className="auth-brand">
          <span className="brand-mark" aria-hidden>
            ✚
          </span>
          <span className="brand-name">HealthCompanion</span>
        </div>
        <p className="auth-pitch">
          A private, searchable home for every patient's records —
          <em> ask, and it answers from the documents.</em>
        </p>
        <ul className="auth-points">
          <li>Upload reports &amp; prescriptions — scans and handwriting welcome.</li>
          <li>Ask plain questions; get answers with their sources.</li>
          <li>Each patient's records stay strictly their own.</li>
        </ul>
      </div>

      <div className="auth-form-wrap">
        <form className="auth-form" onSubmit={submit}>
          <div className="auth-tabs">
            <button
              type="button"
              className={mode === "login" ? "on" : ""}
              onClick={() => setMode("login")}
            >
              Sign in
            </button>
            <button
              type="button"
              className={mode === "signup" ? "on" : ""}
              onClick={() => setMode("signup")}
            >
              Create account
            </button>
          </div>

          {mode === "signup" && (
            <>
              <label className="field">
                <span>I am a</span>
                <div className="role-track wide">
                  <button
                    type="button"
                    className={role === "patient" ? "on" : ""}
                    onClick={() => setRole("patient")}
                  >
                    Patient
                  </button>
                  <button
                    type="button"
                    className={role === "doctor" ? "on" : ""}
                    onClick={() => setRole("doctor")}
                  >
                    Doctor
                  </button>
                </div>
              </label>
              <label className="field">
                <span>Full name</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Ravi Kumar"
                  required
                />
              </label>
              {role === "patient" && (
                <label className="field">
                  <span>Date of birth</span>
                  <input
                    type="date"
                    value={dob}
                    onChange={(e) => setDob(e.target.value)}
                    required
                  />
                </label>
              )}
            </>
          )}

          <label className="field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              required
            />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === "signup" ? "At least 6 characters" : "••••••••"}
              required
            />
          </label>

          {error && <div className="auth-error">{error}</div>}

          <button className="auth-submit" type="submit" disabled={busy}>
            {busy
              ? "…"
              : mode === "login"
                ? "Sign in"
                : `Create ${role} account`}
          </button>

          <p className="auth-switch">
            {mode === "login" ? "New here? " : "Already have an account? "}
            <button
              type="button"
              onClick={() => setMode(mode === "login" ? "signup" : "login")}
            >
              {mode === "login" ? "Create an account" : "Sign in"}
            </button>
          </p>
        </form>
      </div>
    </div>
  );
}
