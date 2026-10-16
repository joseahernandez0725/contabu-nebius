import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getTransactionInsight, money, resolveFinding, type FlagLevel, type TxInsight } from "../api";
import { useAuditText, VERDICT_STYLE } from "./AuditWorkspace";
import RuleTrace from "./RuleTrace";
import {
  BTN_GHOST, BTN_SECONDARY, Badge, DOT, DetailCard, DetailEmpty, DetailHeader, DetailSection,
} from "./PageKit";

// EL PANEL LATERAL DE LA TRANSACCIÓN (FASE 13, área Firm · 13-oct-2026). Decisión de Jose: en Bookkeeping,
// seleccionar un movimiento abre a la derecha TODO lo que se sabe de él — reemplaza a la celda Observación.
// Usa el MISMO detalle que la pestaña Auditoría (`DetailCard` de PageKit) y sus mismos textos
// (`useAuditText`), para que un hallazgo se diga igual en las dos pantallas.
//
// 🔴 Solo lectura, más dos acciones que ya existían: "Está bien" (el mismo resolver de Auditoría) y
// "Ver en Auditoría". El panel NO categoriza ni toca lo escrito a mano: eso se hace en la celda.

export const LEVEL_DOT: Record<FlagLevel, string> = { problem: DOT.problem, review: DOT.review, info: DOT.info };

export default function TransactionPanel({ txId, refreshKey, onClose, onGoRule, onOpenAudit, onChanged }: {
  txId: number;
  refreshKey: unknown;               // cambia cuando la hoja recarga: el panel vuelve a leer
  onClose: () => void;
  onGoRule: (ruleId: number) => void;
  onOpenAudit: () => void;
  onChanged: () => void;             // tras "Está bien": la hoja recarga sus puntitos
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const { label, fmtDate, webLabel, detectorSentence } = useAuditText();
  const [data, setData] = useState<TxInsight | null>(null);
  const [error, setError] = useState(false);
  const [rawOpen, setRawOpen] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setError(false);
    getTransactionInsight(txId)
      .then((d) => { if (alive) setData(d); })
      .catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, [txId, refreshKey]);
  useEffect(() => { setRawOpen(false); }, [txId]);

  async function markOk(findingId: number) {
    setBusy(findingId);
    try {
      await resolveFinding(findingId, "ok");
      setData(await getTransactionInsight(txId));
      onChanged();
    } catch { setError(true); } finally { setBusy(null); }
  }

  const x = data && data.transaction.id === txId ? data : null;
  const tx = x?.transaction;
  const dateLong = (iso: string) => new Date(iso.length > 10 ? iso + (iso.endsWith("Z") ? "" : "Z") : iso + "T00:00:00")
    .toLocaleString(lang, iso.length > 10
      ? { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" }
      : { day: "numeric", month: "short", year: "numeric" });

  return (
    // `preventDefault` en mouseup: si no, RevoGrid toma el clic como "afuera" y suelta la celda que se
    // estaba editando (regla de la hoja, `.claude/rules/hoja.md`). Así el teclado sigue en la hoja.
    <div onMouseUp={(e) => e.preventDefault()} onTouchEnd={(e) => e.preventDefault()}
         className="h-full w-[360px] max-w-[45vw] shrink-0">
      <DetailCard onClose={onClose} closeLabel={t("panel.close")} className="h-full overflow-y-auto space-y-2.5">
        {!x && !error && <DetailEmpty text={t("panel.loading")} />}
        {error && !x && <DetailEmpty text={t("panel.error")} />}
        {x && tx && (
          <>
            {/* 1. EL MOVIMIENTO */}
            <DetailHeader
              // Sin hallazgos, una fila sin categoría sigue siendo trabajo: se dice "Pendiente", no "nada que revisar".
              dot={x.level ? LEVEL_DOT[x.level] : !tx.category ? DOT.pending : DOT.ok}
              kicker={x.level ? t(`panel.level.${x.level}`) : !tx.category ? t("panel.level.pending") : t("panel.level.none")}
              kickerTone={x.level === "problem" ? "text-rose-700" : x.level === "review" || (!x.level && !tx.category) ? "text-amber-800" : "text-slate-600"}
              title={x.merchant}
              sub={`${fmtDate(tx.date)}${tx.account ? ` · ${tx.account}` : ""}`}
            />
            <div className={`text-xl font-semibold tabular-nums ${tx.is_inflow ? "text-emerald-700" : "text-slate-900"}`}>
              {tx.is_inflow ? "+" : "−"}{money(Math.abs(tx.amount))}
              <span className="ms-2 text-xs font-normal text-slate-500">{t(tx.is_inflow ? "panel.in" : "panel.out")}</span>
            </div>
            <div className="text-xs">
              <button onClick={() => setRawOpen(!rawOpen)} className="text-slate-500 hover:text-slate-800">
                <span className="text-slate-400">{rawOpen ? "▾" : "▸"}</span> {t("panel.bankLine")}
              </button>
              {rawOpen && <div className="mt-0.5 ps-3 font-mono break-all text-[11px] text-slate-500">{tx.description}</div>}
              {tx.description_extra && (
                <div className="mt-0.5 text-slate-500"><span className="text-slate-400">{t("panel.yourText")}:</span> {tx.description_extra}</div>
              )}
            </div>
            {tx.bank_removed_at && (
              <p className="text-xs text-rose-800 bg-rose-50 rounded-lg px-2 py-1">{t("panel.bankRemoved")}</p>
            )}

            {/* 2. LA CATEGORÍA Y DE DÓNDE SALIÓ */}
            <DetailSection title={t("panel.category")}>
              {tx.category && tx.account_type ? (
                <div className="text-sm text-slate-900">
                  {label(`${tx.account_type}/${tx.category}`)}
                  {tx.subcategory && <span className="text-slate-500"> · {tx.subcategory}</span>}
                </div>
              ) : (
                <span className="inline-block text-xs font-semibold px-2 py-0.5 rounded bg-rose-50 text-rose-800 ring-1 ring-inset ring-rose-200">
                  {t("agents.pending")}
                </span>
              )}
              <Provenance x={x} onGoRule={onGoRule} label={label} fmtDate={fmtDate} />
              {/* Por qué (14-oct, Movimientos): la fórmula de ESTA fila — las reglas que la mencionan, en orden. */}
              <RuleTrace txId={tx.id} refreshKey={refreshKey} onGoRule={onGoRule} label={label} fmtDate={fmtDate} />
            </DetailSection>

            {/* 3. LO QUE ENCONTRARON LOS AGENTES */}
            <DetailSection title={t("panel.agents")}>
              {x.findings.length === 0 && <p className="text-xs text-slate-400">{t("panel.noFindings")}</p>}
              {x.findings.map((f) => (
                <div key={f.id} className="rounded-lg border border-slate-100 p-2 space-y-1.5">
                  <div className="flex items-center gap-1.5 text-xs">
                    <span className={`w-2 h-2 rounded-full ${LEVEL_DOT[f.level]}`} />
                    <span className="font-semibold text-slate-800">{t(`auditView.type.${f.type}`, { defaultValue: f.type })}</span>
                    {f.rows > 1 && <span className="text-slate-400">· {t("panel.rows", { count: f.rows })}</span>}
                  </div>
                  <p className="text-xs text-slate-700">{detectorSentence(f)}</p>
                  {!f.is_info && (f.agent_verdict ? (
                    <div className="flex items-start gap-1.5">
                      <Badge cls={VERDICT_STYLE[f.agent_verdict]}>① {t(`agents.verdict.${f.agent_verdict}`)}</Badge>
                      {f.agent_observation && <span className="text-xs text-slate-600">{f.agent_observation}</span>}
                    </div>
                  ) : <p className="text-[11px] text-slate-400">{t("auditView.notReviewed")}</p>)}
                  {f.web && <WebChip w={f.web} webLabel={webLabel} />}
                  <div className="flex flex-wrap gap-1.5 pt-0.5">
                    <button onClick={() => void markOk(f.id)} disabled={busy !== null} className={BTN_SECONDARY}>
                      {f.is_info ? t("auditView.act.understood") : t("auditView.act.ok")}
                    </button>
                    <button onClick={onOpenAudit} className={BTN_GHOST}>{t("panel.openAudit")} →</button>
                  </div>
                </div>
              ))}
              {/* La evidencia del ② sobre ESTE comercio, aunque no haya hallazgo (tipo de negocio + enlace). */}
              {x.web && !x.findings.some((f) => f.web) && <WebChip w={x.web} webLabel={webLabel} />}
            </DetailSection>

            {/* 4. LAS NOTAS DEL LIBRO QUE LE APLICAN */}
            {x.notes.length > 0 && (
              <DetailSection title={t("panel.notes")}>
                {x.notes.map((n) => (
                  <div key={n.id} className="text-xs text-violet-900 bg-violet-50 rounded-lg px-2 py-1">
                    {n.kind === "dont_report" ? "🚫" : "📌"} {n.text}
                    {(n.valid_from || n.valid_to) && (
                      <span className="text-violet-700/70"> · {n.valid_from ? fmtDate(n.valid_from) : "…"} → {n.valid_to ? fmtDate(n.valid_to) : "…"}</span>
                    )}
                  </div>
                ))}
              </DetailSection>
            )}

            {/* 5. EL HISTORIAL DE CORRECCIONES */}
            <DetailSection title={t("panel.history")}>
              {x.history.length === 0 && <p className="text-xs text-slate-400">{t("panel.noHistory")}</p>}
              <ol className="space-y-1.5">
                {x.history.map((h) => (
                  <li key={h.id} className="text-xs">
                    <div className="text-slate-500">
                      {dateLong(h.changed_at)} · {h.by_system ? t("panel.bySystem") : (h.by ?? t("panel.someone"))}
                      {" · "}<span className="text-slate-700">{t(`panel.via.${h.via}`, { defaultValue: h.via })}</span>
                    </div>
                    <div className="text-slate-800">
                      <span className="text-slate-500">{change(h.before, label, t)}</span>
                      <span className="text-slate-400"> → </span>
                      {change(h.after, label, t)}
                    </div>
                    {h.reason && <div className="text-slate-500 italic">“{h.reason}”</div>}
                  </li>
                ))}
              </ol>
            </DetailSection>

            {tx.note && (
              <DetailSection title={t("panel.note")}>
                <p className="text-xs text-slate-700 whitespace-pre-wrap">{tx.note}</p>
              </DetailSection>
            )}
          </>
        )}
      </DetailCard>
    </div>
  );
}

type T = ReturnType<typeof useTranslation>["t"];

function change(s: { account_type: string | null; category: string | null; subcategory: string | null }, label: (x: string) => string, t: T) {
  if (!s.category) return t("agents.pending");
  return `${label(`${s.account_type ?? ""}/${s.category}`)}${s.subcategory ? ` · ${s.subcategory}` : ""}`;
}

function WebChip({ w, webLabel }: { w: NonNullable<TxInsight["web"]>; webLabel: (w: { business_type: string; confidence: string | null }) => string }) {
  return (
    <a href={w.source_url ?? undefined} target="_blank" rel="noopener noreferrer"
       className="inline-block text-xs px-2 py-0.5 rounded-full bg-sky-50 text-sky-800 hover:underline">
      🔎 ② {webLabel(w)}{w.source_domain ? ` · ${w.source_domain}` : ""}
    </a>
  );
}

/** DE DÓNDE salió la categoría: regla (con "Ir a la regla") · escrita a mano ✋ · transferencia · Pendiente. */
function Provenance({ x, onGoRule, label, fmtDate }: {
  x: TxInsight; onGoRule: (id: number) => void; label: (s: string) => string; fmtDate: (s: string) => string;
}) {
  const { t } = useTranslation();
  const p = x.provenance;
  const line = "flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-600";
  if (p.kind === "rule" && p.rule) {
    const r = p.rule;
    const conds = [
      r.valid_from || r.valid_to ? `${r.valid_from ? fmtDate(r.valid_from) : "…"} → ${r.valid_to ? fmtDate(r.valid_to) : "…"}` : null,
      r.amount_min != null || r.amount_max != null
        ? [r.amount_min != null ? `≥ ${money(r.amount_min)}` : null, r.amount_max != null ? `≤ ${money(r.amount_max)}` : null].filter(Boolean).join(" · ")
        : null,
    ].filter(Boolean);
    return (
      <div className="space-y-1">
        <div className={line}>
          <span className="font-bold italic text-emerald-700">ƒ</span>
          <span>{t("panel.prov.rule")} <span className="font-mono text-slate-800">{r.keyword}</span>{r.origin === "agent" ? ` · ${t("panel.prov.byAgent")}` : ""}</span>
          <button onClick={() => onGoRule(r.id)} className="text-emerald-800 font-semibold hover:underline">{t("panel.goRule")} →</button>
        </div>
        {conds.length > 0 && <div className="text-[11px] text-slate-500 ps-4">{conds.join(" · ")}</div>}
        {r.account_type && (r.category !== x.transaction.category || r.account_type !== x.transaction.account_type) && (
          <div className="text-[11px] text-amber-800 ps-4">{t("panel.prov.ruleSays", { s: label(`${r.account_type}/${r.category}`) })}</div>
        )}
      </div>
    );
  }
  const text = p.kind === "rule" ? t("panel.prov.ruleDeleted")
    : p.kind === "manual" ? `✋ ${t("panel.prov.manual")}`
    : p.kind === "transfer" ? `⇄ ${t("panel.prov.transfer")}`
    : p.kind === "pending" ? t("panel.prov.pending")
    : t("panel.prov.other", { source: p.source ?? "—" });
  return <div className={line}>{text}</div>;
}
