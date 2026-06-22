import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { clarifyGroup, money } from "../api";
import type { Transaction } from "../types";

// "2026-06-08" → "8 de junio" (en el idioma del usuario, sin desfase de zona horaria).
function fmtDay(iso: string, lang: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y) return iso;
  return new Date(y, m - 1, d).toLocaleDateString(lang, { day: "numeric", month: "long" });
}

const isIncome = (tx: Transaction) =>
  tx.account_type === "REVENUE" || tx.account_type === "OTHER_INCOME";

// Un cheque es un método de pago, no una contraparte: cada uno puede ser distinto → no agrupar.
const isCheck = (tx: Transaction) => /\b(check|chk|cheque|e-?check)\b/i.test(tx.description);

function GroupCard({ group, onDone }: { group: Transaction[]; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const [answer, setAnswer] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rep = group[0];
  // Cheque: el banco da el NÚMERO pero no el beneficiario → la pregunta se ancla al número
  // ("¿a qué corresponde el cheque #X?") para que el usuario lo identifique en su registro.
  const checkNo = isCheck(rep) ? rep.check_number : null;
  const name = checkNo
    ? `${t("review.checkLabel", { defaultValue: "Check" })} #${checkNo}`
    : (rep.subcategory ?? rep.description);
  const count = group.length;
  const total = group.reduce((s, tx) => s + Number(tx.amount), 0);
  const dates = group.map((tx) => tx.date).sort();
  // Dirección REAL del banco (si la tenemos); si no, caer al account_type. Así "te pagaron" vs
  // "pagaste" sale de si entró/salió plata, no de una categorización incierta.
  const income = rep.is_inflow ?? isIncome(rep);

  // Pregunta natural y específica (nombre + monto + fecha/rango + dirección). Cheque = por número.
  const question = checkNo
    ? t("review.q.check", {
        defaultValue: "Who was check #{{number}} paid to, and what for? ({{amount}} on {{date}})",
        number: checkNo,
        amount: money(total),
        date: fmtDay(rep.date, lang),
      })
    : count === 1
      ? t(income ? "review.q.singleIn" : "review.q.singleOut", {
          name,
          amount: money(total),
          date: fmtDay(rep.date, lang),
        })
      : t(income ? "review.q.groupIn" : "review.q.groupOut", {
          name,
          count,
          amount: money(total),
          from: fmtDay(dates[0], lang),
          to: fmtDay(dates[dates.length - 1], lang),
        });

  async function submit() {
    if (!answer.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await clarifyGroup(group.map((tx) => tx.id), answer.trim());
      onDone(); // recarga: el grupo entero queda categorizado y sale de revisión
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
      setSaving(false);
    }
  }

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5">
      <div className="flex justify-between items-start gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-800 truncate uppercase">{name}</div>
          <div className="text-xs text-slate-400">
            {count > 1 ? t("review.count", { count }) : rep.date}
          </div>
        </div>
        <div className="text-sm font-semibold tabular-nums text-slate-700 shrink-0">{money(total)}</div>
      </div>

      {/* Pregunta de la IA, natural y en el idioma del usuario */}
      <div className="mt-3 bg-amber-50 text-amber-800 text-sm rounded-xl p-3">{question}</div>

      {/* El dueño responde en sus palabras — la IA hace la contabilidad de TODO el grupo */}
      <textarea
        className="textarea textarea-bordered w-full mt-3 text-sm"
        rows={2}
        placeholder={t("review.answerPlaceholder")}
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
      />

      {error && <p className="text-sm text-rose-600 mt-2">{error}</p>}

      <button
        onClick={submit}
        disabled={saving || !answer.trim()}
        className="btn btn-sm w-full mt-3 bg-brand-green text-white border-none hover:bg-brand-green-dark disabled:opacity-50"
      >
        {saving ? <span className="loading loading-spinner loading-sm" /> : t("review.confirm")}
      </button>
    </div>
  );
}

export default function Review({ items, reload }: { items: Transaction[]; reload: () => void }) {
  const { t } = useTranslation();

  // Agrupa por contraparte (subcategoría/comercio normalizado): una sola pregunta por contraparte.
  const groups = useMemo(() => {
    const map = new Map<string, Transaction[]>();
    for (const tx of items) {
      // Los cheques quedan cada uno solo (clave única por id); el resto se agrupa por contraparte.
      const key = isCheck(tx)
        ? `chk-${tx.id}`
        : (tx.subcategory ?? tx.description ?? "—").trim().toUpperCase();
      const arr = map.get(key);
      if (arr) arr.push(tx);
      else map.set(key, [tx]);
    }
    return [...map.values()];
  }, [items]);

  if (items.length === 0) {
    return (
      <div className="text-center py-12 text-slate-500">
        <p className="font-medium">{t("review.empty")}</p>
        <p className="text-sm mt-1">{t("review.emptyHint")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-500">{t("review.intro", { count: groups.length })}</p>
      {groups.map((group) => (
        <GroupCard key={group[0].id} group={group} onDone={reload} />
      ))}
    </div>
  );
}
