import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getCompany, getTransactionInsight, money, resolveFinding, updateTransaction,
  type FlagLevel, type TxInsight,
} from "../api";
import type { Company } from "../types";
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
//
// 🔄 19-oct: "Nota de este movimiento" SÍ se edita aquí (`NoteEditor`), justo debajo del encabezado —
// la Hoja quitó su columna "Note" (Movimientos). Se guarda sola al salir del campo o con Ctrl+Enter. Un
// visor (rol VIEWER) la ve, no la edita (mismo candado que `Settings.tsx`: `role !== "VIEWER"`).
//
// 🔄 19-oct (ronda de uso): el monto se mudó al encabezado (junto al comercio y la fecha; ya no vive
// aparte) · fuera la "Línea del banco" — ya se ve en la Hoja · "Lo que encontraron los agentes" sube,
// antes de Categoría, con más aire: es lo más importante del panel · Categoría se comprime a una línea
// (ƒ/✎/⇄ + la cuenta); el "por qué" (de dónde salió + la fórmula) se pliega, se abre con un clic.

export const LEVEL_DOT: Record<FlagLevel, string> = { problem: DOT.problem, review: DOT.review, info: DOT.info };

// El ícono de la línea comprimida de Categoría (19-oct): de dónde salió la categoría, de un vistazo.
// "pending" y "other" no tienen ícono propio (no hay categoría que mostrar, o la fuente es rara).
const PROV_ICON: Partial<Record<TxInsight["provenance"]["kind"], string>> = { rule: "ƒ", manual: "✎", transfer: "⇄" };
const PROV_ICON_LABEL: Partial<Record<TxInsight["provenance"]["kind"], string>> = {
  rule: "panel.prov.rule", manual: "panel.prov.manual", transfer: "panel.prov.transfer",
};

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
  const [busy, setBusy] = useState<number | null>(null);
  // Candado de "Nota de este movimiento" (19-oct): visor (rol VIEWER) la ve, no la edita. Mismo
  // criterio que `Settings.tsx` (`canEdit = role !== "VIEWER"`); se pide una sola vez, no por movimiento.
  const [company, setCompany] = useState<Company | null>(null);
  useEffect(() => { getCompany().then(setCompany).catch(() => {}); }, []);
  const canEditNote = (company?.role ?? "OWNER") !== "VIEWER";

  useEffect(() => {
    let alive = true;
    setError(false);
    getTransactionInsight(txId, lang)
      .then((d) => { if (alive) setData(d); })
      .catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, [txId, refreshKey, lang]);

  async function markOk(findingId: number) {
    setBusy(findingId);
    try {
      await resolveFinding(findingId, "ok");
      setData(await getTransactionInsight(txId, lang));
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
         className="relative h-full w-1/4 min-w-[320px] shrink-0">
      {/* 21-oct (2), pedido de Movimientos (📬 Buzón): el botón de RECOGER se mudó afuera de
          DetailCard — montado sobre el borde izquierdo del panel, centrado en su alto (ya no es la ×
          de la esquina). DetailCard ya no recibe `onClose`: su × interno no se dibuja. */}
      <button onClick={onClose} aria-label={t("panel.collapse")} title={t("panel.collapse")}
        className="absolute left-0 top-1/2 -translate-x-1/2 -translate-y-1/2 z-10 size-[30px] rounded-full
                   bg-white border border-slate-200 shadow flex items-center justify-center
                   text-slate-500 hover:text-slate-800 hover:bg-slate-50">
        <span aria-hidden="true">›</span>
      </button>
      <DetailCard className="h-full overflow-y-auto space-y-2.5">
        {!x && !error && <DetailEmpty text={t("panel.loading")} />}
        {error && !x && <DetailEmpty text={t("panel.error")} />}
        {x && tx && (
          <>
            {/* 1. EL MOVIMIENTO, con el monto en el encabezado (19-oct: antes vivía aparte, ahora junto
                al comercio y la fecha). */}
            <DetailHeader
              // Sin hallazgos, una fila sin categoría sigue siendo trabajo: se dice "Pendiente", no "nada que revisar".
              dot={x.level ? LEVEL_DOT[x.level] : !tx.category ? DOT.pending : DOT.ok}
              kicker={x.level ? t(`panel.level.${x.level}`) : !tx.category ? t("panel.level.pending") : t("panel.level.none")}
              kickerTone={x.level === "problem" ? "text-rose-700" : x.level === "review" || (!x.level && !tx.category) ? "text-amber-800" : "text-slate-600"}
              title={x.merchant}
              sub={`${fmtDate(tx.date)}${tx.account ? ` · ${tx.account}` : ""}`}
              amount={
                <div className={`text-end ${tx.is_inflow ? "text-emerald-700" : "text-slate-900"}`}>
                  <div className="text-lg font-bold tabular-nums leading-tight">
                    {tx.is_inflow ? "+" : "−"}{money(Math.abs(tx.amount))}
                  </div>
                  <div className="text-[10px] font-normal text-slate-400">{t(tx.is_inflow ? "panel.in" : "panel.out")}</div>
                </div>
              }
            />

            {/* 2. LA NOTA DE ESTE MOVIMIENTO (19-oct, Firm, arriba por pedido de Jose): editable, distinta
                de las notas del libro de abajo — esa es la del bookkeeper sobre ESTA fila; "panel.notes"
                son las del libro entero que la tocan. Antes solo lectura, al final del panel. */}
            <DetailSection title={t("panel.note")}>
              <NoteEditor txId={tx.id} note={tx.note} canEdit={canEditNote} onSaved={onChanged} />
            </DetailSection>

            {/* Fuera la "Línea del banco" (19-oct): ya se ve en la Hoja. Lo que queda es solo lo que
                NO se ve ahí: el texto que agregó el bookkeeper al importar y el aviso si el banco retiró
                el movimiento después de registrarlo. */}
            {(tx.description_extra || tx.bank_removed_at) && (
              <div className="text-xs space-y-1">
                {tx.description_extra && (
                  <div className="text-slate-500"><span className="text-slate-400">{t("panel.yourText")}:</span> {tx.description_extra}</div>
                )}
                {tx.bank_removed_at && (
                  <p className="text-rose-800 bg-rose-50 rounded-lg px-2 py-1">{t("panel.bankRemoved")}</p>
                )}
              </div>
            )}

            {/* 3. LO QUE ENCONTRARON LOS AGENTES (19-oct: sube antes de Categoría) — es lo más
                importante del panel, se ve primero y con más aire que las demás secciones. */}
            <section className="border-t border-slate-100 pt-3 space-y-2.5">
              <h4 className="text-sm font-bold text-slate-900">{t("panel.agents")}</h4>
              {x.findings.length === 0 && <p className="text-xs text-slate-400">{t("panel.noFindings")}</p>}
              {x.findings.map((f) => (
                <div key={f.id} className="rounded-lg border border-slate-100 p-3 space-y-2">
                  <div className="flex items-center gap-1.5 text-xs">
                    <span className={`w-2 h-2 rounded-full ${LEVEL_DOT[f.level]}`} />
                    <span className="font-semibold text-slate-800">{t(`auditView.type.${f.type}`, { defaultValue: f.type })}</span>
                    {f.rows > 1 && <span className="text-slate-400">· {t("panel.rows", { count: f.rows })}</span>}
                  </div>
                  <p className="text-xs text-slate-700">{detectorSentence(f)}</p>
                  {!f.is_info && (f.agent_verdict ? (
                    <div className="flex items-start gap-1.5">
                      <Badge cls={VERDICT_STYLE[f.agent_verdict]}>{t(`agents.verdict.${f.agent_verdict}`)}</Badge>
                      {f.observation_stale
                        ? <span className="text-[11px] text-slate-400 italic">{t("auditView.staleVerdict")}</span>
                        : f.agent_observation && <span className="text-xs text-slate-600">{f.agent_observation}</span>}
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
            </section>

            {/* 4. LA CATEGORÍA, comprimida en una línea (19-oct): el ícono dice de dónde salió (ƒ de una
                regla · ✎ a mano · ⇄ transferencia), la cuenta y su sección. El "por qué" (de dónde
                exactamente y la fórmula que la toca) se pliega; se abre con un clic. */}
            <DetailSection title={t("panel.category")}>
              <div className="flex items-center gap-1.5 text-sm text-slate-900">
                {tx.category && tx.account_type ? (
                  <>
                    {PROV_ICON[x.provenance.kind] && (
                      <span className="shrink-0 font-bold italic text-emerald-700" title={t(PROV_ICON_LABEL[x.provenance.kind]!)}>
                        {PROV_ICON[x.provenance.kind]}
                      </span>
                    )}
                    <span className="truncate">
                      {label(`${tx.account_type}/${tx.category}`)}
                      {tx.subcategory && <span className="text-slate-500"> · {tx.subcategory}</span>}
                    </span>
                  </>
                ) : (
                  <span className="inline-block text-xs font-semibold px-2 py-0.5 rounded bg-rose-50 text-rose-800 ring-1 ring-inset ring-rose-200">
                    {t("agents.pending")}
                  </span>
                )}
              </div>
              <details className="text-xs">
                <summary className="cursor-pointer list-none text-slate-500 hover:text-slate-800">
                  <span className="text-slate-400">▸</span> {t("panel.why")}
                </summary>
                <div className="mt-1.5 space-y-1.5">
                  <Provenance x={x} onGoRule={onGoRule} label={label} fmtDate={fmtDate} />
                  {/* Por qué (14-oct, Movimientos): la fórmula de ESTA fila — las reglas que la mencionan, en orden. */}
                  <RuleTrace txId={tx.id} refreshKey={refreshKey} onGoRule={onGoRule} label={label} fmtDate={fmtDate} />
                </div>
              </details>
            </DetailSection>

            {/* 5. LAS NOTAS DEL LIBRO QUE LE APLICAN */}
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

            {/* 6. EL HISTORIAL DE CORRECCIONES */}
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

          </>
        )}
      </DetailCard>
    </div>
  );
}

/** La nota de ESTE movimiento: se guarda sola al salir del campo o con Ctrl+Enter (19-oct). Visor = solo lectura. */
function NoteEditor({ txId, note, canEdit, onSaved }: {
  txId: number; note: string | null; canEdit: boolean; onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState(note ?? "");
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  useEffect(() => { setValue(note ?? ""); setStatus("idle"); }, [txId, note]);

  async function save() {
    const next = value.trim();
    if (next === (note ?? "")) return;
    setStatus("saving");
    try {
      await updateTransaction(txId, { note: next });
      setStatus("saved");
      onSaved();
    } catch {
      setStatus("error");
    }
  }

  if (!canEdit) {
    return value
      ? <p className="text-xs text-slate-700 whitespace-pre-wrap">{value}</p>
      : <p className="text-xs text-slate-400 italic">{t("panel.noteEmpty")}</p>;
  }
  return (
    <div>
      <textarea
        className="w-full border border-slate-200 rounded-lg px-2 py-1.5 text-xs bg-white focus:outline-none focus:ring-2 focus:ring-emerald-200"
        rows={3}
        value={value}
        onChange={(e) => { setValue(e.target.value); setStatus("idle"); }}
        onBlur={() => void save()}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void save(); } }}
        placeholder={t("panel.notePlaceholder")}
      />
      {status === "saving" && <p className="text-[11px] text-slate-400 mt-0.5">{t("panel.noteSaving")}</p>}
      {status === "saved" && <p className="text-[11px] text-emerald-700 mt-0.5">{t("panel.noteSaved")}</p>}
      {status === "error" && <p className="text-[11px] text-rose-700 mt-0.5">{t("panel.noteError")}</p>}
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
      🔎 Tavily: {webLabel(w)}{w.source_domain ? ` · ${w.source_domain}` : ""}
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
