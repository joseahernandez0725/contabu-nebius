import { useState } from "react";
import { useTranslation } from "react-i18next";
import { clarifyTransaction, money } from "../api";
import type { Transaction } from "../types";

function ReviewCard({ tx, onDone }: { tx: Transaction; onDone: () => void }) {
  const { t } = useTranslation();
  const [answer, setAnswer] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit() {
    if (!answer.trim()) return;
    setSaving(true);
    try {
      await clarifyTransaction(tx.id, answer.trim());
      onDone(); // recarga la lista (la transacción ya queda categorizada y sale de revisión)
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5">
      <div className="flex justify-between items-start gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-800 truncate">{tx.subcategory ?? tx.description}</div>
          <div className="text-xs text-slate-400">{tx.date}</div>
        </div>
        <div className="text-sm font-semibold tabular-nums text-slate-700 shrink-0">{money(Number(tx.amount))}</div>
      </div>

      {/* La pregunta de la IA, en lenguaje simple */}
      {tx.review_question && (
        <div className="mt-3 bg-amber-50 text-amber-800 text-sm rounded-xl p-3">{tx.review_question}</div>
      )}

      {/* El dueño responde en sus palabras — la IA hace la contabilidad */}
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
      <p className="text-sm text-slate-500">{t("review.intro", { count: items.length })}</p>
      {items.map((tx) => (
        <ReviewCard key={tx.id} tx={tx} onDone={reload} />
      ))}
    </div>
  );
}
