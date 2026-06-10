import { useEffect, useRef, useState } from "react";
import type { Role, Source } from "../api";

export type Message = {
  id: number;
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
  pending?: boolean;
};

type Props = {
  messages: Message[];
  role: Role;
  disabled: boolean;
  onAsk: (question: string) => void;
};

const SUGGESTIONS: Record<Role, string[]> = {
  patient: [
    "Which medicines do I take, and when?",
    "What did the last report say?",
    "Are there any tests I should repeat?",
  ],
  doctor: [
    "Summarize this patient's medication history.",
    "List abnormal lab values with dates.",
    "What was prescribed and at what dosage?",
  ],
};

export default function AskPanel({ messages, role, disabled, onAsk }: Props) {
  const [text, setText] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = text.trim();
    if (!q || disabled) return;
    onAsk(q);
    setText("");
  }

  return (
    <section className="panel ask">
      <div className="panel-head">
        <h2>Ask the record</h2>
        <span className="panel-sub">grounded in this patient's documents</span>
      </div>

      <div className="thread" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="thread-empty">
            <p className="thread-empty-lead">
              Ask anything about {role === "doctor" ? "this patient" : "your"}{" "}
              records.
            </p>
            <div className="suggestions">
              {SUGGESTIONS[role].map((s) => (
                <button
                  key={s}
                  disabled={disabled}
                  onClick={() => onAsk(s)}
                  className="chip"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m) => (
          <div key={m.id} className={`bubble ${m.role}`}>
            {m.pending ? (
              <span className="typing">
                <i></i>
                <i></i>
                <i></i>
              </span>
            ) : (
              <>
                <p className="bubble-text">{m.text}</p>
                {m.sources && m.sources.length > 0 && (
                  <ul className="sources">
                    {m.sources.map((s, i) => (
                      <li key={i}>
                        <span className="src-dot" aria-hidden />
                        {s.filename}
                        <span className="src-meta">
                          {" "}
                          {s.doc_type} · {s.doc_date || "undated"}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        ))}
      </div>

      <form className="composer" onSubmit={submit}>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={
            disabled ? "Select a patient first…" : "Type a question…"
          }
          disabled={disabled}
        />
        <button type="submit" disabled={disabled || !text.trim()}>
          Ask
        </button>
      </form>
    </section>
  );
}
