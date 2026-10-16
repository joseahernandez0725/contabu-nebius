import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getRuleTrace, money, type RuleTrace as Trace, type RuleTraceStep } from "../api";

/**
 * POR QUÉ TIENE ESTA CATEGORÍA (FASE 10, Movimientos · Jose, 14-oct-2026). "Ver como fórmula" es el libro
 * entero como una cadena de SI; esto es la misma fórmula para UNA fila: las reglas que mencionan su
 * descripción, en el orden real, y qué pasó con cada una, hasta la que decide (o transferencia / Pendiente).
 * Va dentro del panel de la transacción (`TransactionPanel`, de Firm). Solo lectura.
 */
export default function RuleTrace({ txId, refreshKey, onGoRule, label, fmtDate }: {
  txId: number; refreshKey: unknown; onGoRule: (id: number) => void;
  label: (s: string) => string; fmtDate: (s: string) => string;
}) {
  const { t } = useTranslation();
  const [tr, setTr] = useState<Trace | null>(null);
  useEffect(() => {
    let alive = true;
    setTr(null);
    getRuleTrace(txId).then((d) => { if (alive) setTr(d); }).catch(() => {});
    return () => { alive = false; };
  }, [txId, refreshKey]);
  if (!tr) return null;

  // Un solo SI y es el que decide: no hay nada que explicar más allá de la procedencia de arriba.
  const lone = !tr.manual && tr.steps.length === 1 && tr.steps[0].result === "wins";
  const cond = (s: RuleTraceStep) => [
    s.valid_from || s.valid_to ? `${s.valid_from ? fmtDate(s.valid_from) : "…"} → ${s.valid_to ? fmtDate(s.valid_to) : "…"}` : null,
    s.amount_min != null ? `≥ ${money(s.amount_min)}` : null,
    s.amount_max != null ? `≤ ${money(s.amount_max)}` : null,
  ].filter(Boolean).join(" · ");
  const out: Record<RuleTraceStep["result"], string> = {
    wins: "text-emerald-800 font-semibold", date: "text-slate-500", amount: "text-slate-500", after: "text-slate-400",
  };

  return (
    <div className="mt-1.5 rounded-lg bg-slate-50 px-2 py-1.5 text-[11.5px] space-y-0.5">
      <div className="text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">{t("rules.trace.title")}</div>
      {tr.manual && <div className="text-amber-800">✋ {t("rules.trace.manual")}</div>}
      {tr.bare_check && <div className="text-slate-600">{t("rules.trace.bareCheck")}</div>}
      {lone ? (
        <div className="text-slate-500">{t("rules.trace.lone", { count: tr.checked })}</div>
      ) : (
        <>
          {tr.steps.map((s) => (
            <div key={s.id} className={`flex flex-wrap items-baseline gap-x-1.5 ${out[s.result]}`}>
              <span className="tabular-nums text-slate-400">#{s.order}</span>
              <button onClick={() => onGoRule(s.id)} className="font-mono hover:underline">{s.keyword}</button>
              {cond(s) && <span className="text-slate-400">({cond(s)})</span>}
              <span>→ {s.result === "wins" ? `${s.account_type ? label(`${s.account_type}/${s.category}`) : s.category} ✓`
                        : t(`rules.trace.${s.result}`)}</span>
            </div>
          ))}
          {tr.winner_id == null && (
            <>
              <div className="text-slate-500">
                {tr.steps.length ? t("rules.trace.noneFits") : t("rules.trace.noneMentions", { count: tr.checked })}
              </div>
              <div className={tr.transfer ? "text-slate-700" : "text-rose-800 font-semibold"}>
                {tr.transfer
                  ? `⇄ ${t("rules.trace.transferYes")} → ${label(`${tr.transfer.account_type}/${tr.transfer.category}`)}`
                  : t("rules.trace.transferNo")}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
