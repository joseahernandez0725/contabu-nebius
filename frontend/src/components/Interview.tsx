import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { answerInterview, getInterview } from "../api";
import type { InterviewQuestion } from "../types";

const money = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

// Roles que el dueño puede asignar (los valores viajan en inglés al backend).
const ROLES = ["subcontractor", "customer", "owner", "skip"] as const;

// ENTREVISTA — RONDA 2 (FASE 9 Paso 5): el agente pregunta por las personas de los pagos
// Zelle/Venmo recurrentes — exactamente las que se niega a adivinar ("nunca en silencio").
// Anclada en los datos: nombre, nº de pagos y cuánto entró/salió (lo suma Python, no la IA).
// Una respuesta corrige las transacciones de esa persona al instante y deja el HECHO en el
// perfil (la IA lo recibe como verdad y el proponedor ya puede proponer su regla).
export default function Interview({ onApplied }: { onApplied?: () => void }) {
  const { t } = useTranslation();
  const [questions, setQuestions] = useState<InterviewQuestion[]>([]);
  const [busy, setBusy] = useState<string | null>(null); // key de la pregunta en curso
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    getInterview().then((r) => setQuestions(r.questions)).catch(() => setQuestions([]));
  }, []);

  async function answer(q: InterviewQuestion, role: string) {
    setBusy(q.key);
    setNote(null);
    try {
      const r = await answerInterview([{ name: q.key, role }]);
      setQuestions((prev) => prev.filter((p) => p.key !== q.key));
      if (r.updated > 0) {
        setNote(t("interview.r2Applied", { defaultValue: "{{count}} transactions updated", count: r.updated }));
        onApplied?.(); // las categorías cambiaron → refrescar review/auditor
      }
    } catch {
      /* si falla, la pregunta se queda y el dueño puede reintentar */
    } finally {
      setBusy(null);
    }
  }

  if (questions.length === 0 && !note) return null;

  return (
    <div className="bg-white rounded-2xl border border-slate-200 p-4 shadow-sm">
      <h2 className="font-semibold text-slate-900">
        {t("interview.r2Title", { defaultValue: "Your agent has a few questions" })}
      </h2>
      <p className="text-sm text-slate-500 mt-0.5">
        {t("interview.r2Subtitle", { defaultValue: "Recurring person-to-person payments it refuses to guess — tell it once and it fixes them all." })}
      </p>
      {note && <p className="text-sm text-brand-green mt-2">{note}</p>}

      <div className="mt-3 space-y-3">
        {questions.map((q) => (
          <div key={q.key} className="rounded-xl border border-slate-100 bg-slate-50/60 p-3">
            <p className="text-sm text-slate-800">
              {t("interview.r2Question", {
                defaultValue: "I see {{count}} person-to-person payments with “{{name}}” — who is this?",
                count: q.count, name: q.name,
              })}
            </p>
            <p className="text-xs text-slate-500 mt-0.5">
              {q.total_in > 0 && <span className="text-emerald-600">↓ {money(q.total_in)} {t("audit.in", { defaultValue: "in" })}</span>}
              {q.total_in > 0 && q.total_out > 0 && " · "}
              {q.total_out > 0 && <span className="text-rose-500">↑ {money(q.total_out)} {t("audit.out", { defaultValue: "out" })}</span>}
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {ROLES.map((role) => (
                <button
                  key={role}
                  onClick={() => void answer(q, role)}
                  disabled={busy === q.key}
                  className={`px-3 py-1.5 rounded-lg border text-xs font-medium disabled:opacity-50 ${
                    role === "skip"
                      ? "border-slate-200 text-slate-500 hover:bg-slate-100"
                      : "border-brand-green/40 text-brand-green hover:bg-brand-green/5"
                  }`}
                >
                  {t(`interview.r2Roles.${role}`, { defaultValue: role })}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
