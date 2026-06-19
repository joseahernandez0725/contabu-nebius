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

function GroupCard({ group, onDone }: { group: Transaction[]; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const [answer, setAnswer] = useState("");
  const [saving, setSaving] = useState(false);

  const rep = group[0];
  const name = rep.subcategory ?? rep.description;
  const count = group.length;
  const total = group.reduce((s, tx) => s + Number(tx.amount), 0);
  const dates = group.map((tx) => tx.date).sort();
  const income = isIncome(rep);

  // Pregunta natural y específica (nombre + monto + fecha/rango + dirección).
  const question =
    count === 1
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
    try {
      await clarifyGroup(group.map((tx) => tx.id), answer.trim());
      onDone(); // recarga: el grupo entero queda categorizado y sale de revisión
    } finally {
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
      const key = (tx.subcategory ?? tx.description ?? "—").trim().toUpperCase();
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
