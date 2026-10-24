import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { uploadCsv, setTransactionsAccount, type BankConnection, type UploadResult } from "../api";
import { SyncSummary, useBanks } from "./BankConnections";
import { LiveOnly } from "./DemoDoors";   // visita guiada del demo: vincular un banco, solo en vivo (Firm, 23-oct)
import {
  BTN_GHOST, BTN_PRIMARY, BTN_SMALL_PRIMARY, DOT, DetailHeader, DetailSection, EmptyRow, ListCard, ListDetail, ListRow,
  PageHeader, Stat, TONE, agoText, secsSince,
} from "./PageKit";

export default function Upload({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Cuando la IA no detecta la cuenta del archivo, pedimos al usuario que la escriba.
  const [askAccount, setAskAccount] = useState<UploadResult | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Subida por LOTES: trocea el archivo (cada lote bajo el límite de ~300s del navegador) y muestra
  // progreso. El backend procesa rows[offset:offset+CHUNK]; iteramos hasta cubrir el total.
  const CHUNK = 100;
  async function handleUpload() {
    if (!file) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setProgress(null);
    try {
      const agg: UploadResult = {
        inserted: 0, errors: 0, skipped_duplicates: 0, held_pending: 0,
        account_detected: false, transaction_ids: [],
      };
      let offset = 0;
      let total = Infinity;
      do {
        const res = await uploadCsv(file, offset, CHUNK);
        if (res.total == null) { // archivo vacío / error de mapeo → no es lote
          setResult(res);
          return;
        }
        total = res.total;
        agg.inserted += res.inserted;
        agg.errors += res.errors;
        agg.skipped_duplicates += res.skipped_duplicates;
        agg.held_pending += res.held_pending;
        agg.account_detected = agg.account_detected || res.account_detected;
        agg.transaction_ids.push(...res.transaction_ids);
        // El AUDITOR automático corre en el ÚLTIMO lote → solo ese trae el conteo (FASE 9 Paso 4).
        if (res.audit_findings != null) agg.audit_findings = res.audit_findings;
        offset += CHUNK;
        setProgress({ done: Math.min(offset, total), total });
      } while (offset < total);

      if (!agg.account_detected && agg.transaction_ids.length > 0) {
        setAskAccount(agg); // abrir el modal para pedir la cuenta
      } else {
        setResult(agg);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setLoading(false);
      setProgress(null);
    }
  }

  // ── BANCOS (13-oct, Firm: diseño de página compartido — `PageKit.tsx`) ──────────────────────────
  // Línea de estado (bancos, cuentas, hace cuánto se actualizó) con UNA acción principal (vincular un
  // banco) y el resto en "⋯"; a la izquierda los bancos vinculados y el extracto CSV, a la derecha el
  // detalle del elegido. La lógica de Plaid y la de subida no cambiaron (`useBanks`, `handleUpload`).
  const { i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const banks = useBanks();
  const [sel, setSel] = useState<number | "csv" | null>(null);
  const current: number | "csv" = sel ?? (banks.conns[0]?.id ?? "csv");
  const conn = current === "csv" ? null : banks.conns.find((c) => c.id === current) ?? null;
  const accounts = banks.conns.reduce((n, c) => n + c.accounts.length, 0);
  const down = banks.conns.filter((c) => c.status === "disconnected").length;
  const stamps = banks.conns.map((c) => c.last_synced_at);
  const oldest = stamps.includes(null) ? null : stamps.reduce<string | null>((m, x) => (m === null || (x as string) < m ? x : m), null);
  const ago = (iso: string | null) => (iso ? agoText(lang, secsSince(iso)) : t("bank.neverSynced"));
  const dotOf = (c: BankConnection) => (c.status === "disconnected" ? DOT.problem : c.last_synced_at ? DOT.ok : DOT.off);

  return (
    <div className="space-y-3">
      {/* 1. LA LÍNEA DE ESTADO */}
      <PageHeader
        meta={banks.conns.length > 0 ? t("page.banks.updated", { when: ago(oldest) }) : undefined}
        primary={banks.config?.enabled && (
          <LiveOnly><button onClick={() => void banks.openLink()} disabled={banks.busy !== null} className={BTN_PRIMARY}>
            {banks.busy === "new" ? t("page.banks.working") : t("bank.link")}
          </button></LiveOnly>
        )}
        more={[
          ...(banks.conns.some((c) => c.status !== "disconnected")
            ? [{ label: t("page.banks.updateAll"), onClick: () => void banks.syncAll(), disabled: banks.busy !== null }] : []),
          { label: t("page.banks.uploadCsv"), onClick: () => setSel("csv") },
        ]}
        moreLabel={t("page.more")}
      >
        <Stat value={banks.loaded ? banks.conns.length : undefined} label={t("page.banks.linked", { count: banks.conns.length })} />
        <Stat value={banks.loaded ? accounts : undefined} label={t("page.banks.accounts", { count: accounts })} />
        {down > 0 && <Stat value={down} label={t("page.banks.down", { count: down })} tone={TONE.problem} />}
      </PageHeader>

      {banks.last && <SyncSummary s={banks.last} />}
      {banks.error && <div className="alert alert-error text-sm">{banks.error}</div>}

      {/* 2. LOS BANCOS Y EL EXTRACTO (lista) + EL ELEGIDO (detalle). 60/40 como Auditoría (Jose, 19-oct). */}
      <ListDetail
        wide="split"
        selKey={sel ?? undefined}
        list={
          <ListCard
            header={<div className="px-3 py-2 border-b border-slate-200 text-xs font-semibold uppercase tracking-wide text-slate-400">{t("page.banks.listTitle")}</div>}
            footer={banks.config?.enabled && banks.config.environment === "sandbox" ? t("bank.sandboxHint") : undefined}>
            {banks.conns.map((c) => (
              <ListRow key={c.id} active={current === c.id} onClick={() => setSel(c.id)} dot={dotOf(c)}
                       title={c.institution_name ?? t("bank.unnamed")}
                       sub={c.accounts.map((a) => `${a.name ?? ""}${a.mask ? ` ••${a.mask}` : ""}`).join(" · ")}
                       right={<span className="text-[11px] text-slate-400">{c.status === "disconnected" ? t("page.banks.statusDown") : ago(c.last_synced_at)}</span>} />
            ))}
            {banks.loaded && banks.conns.length === 0 && <EmptyRow text={banks.config?.enabled ? t("page.banks.none") : t("bank.notConfigured")} />}
            <ListRow active={current === "csv"} onClick={() => setSel("csv")} dot={DOT.off}
                     title={t("page.banks.csvTitle")} sub={t("page.banks.csvSub")} />
          </ListCard>
        }
        detail={conn ? (
          <div className="space-y-3">
            <DetailHeader dot={dotOf(conn)}
                          kicker={conn.status === "disconnected" ? t("page.banks.statusDown") : t("page.banks.statusOk")}
                          kickerTone={conn.status === "disconnected" ? "text-rose-700" : "text-emerald-700"}
                          title={conn.institution_name ?? t("bank.unnamed")}
                          sub={conn.last_synced_at
                            ? t("bank.lastSync", { date: new Date(conn.last_synced_at + "Z").toLocaleString() })
                            : t("bank.neverSynced")} />
            {conn.status === "disconnected" && (
              <p className="text-xs text-rose-800 bg-rose-50 rounded-lg px-2 py-1">{t("bank.err.bank_disconnected")}</p>
            )}
            <DetailSection title={t("page.banks.accountsTitle")}>
              <ul className="text-sm text-slate-800 space-y-0.5">
                {conn.accounts.map((a, k) => (
                  <li key={k} className="flex gap-2"><span className="truncate">{a.name ?? "—"}</span>
                    {a.mask && <span className="text-slate-400 tabular-nums">••{a.mask}</span>}</li>
                ))}
              </ul>
            </DetailSection>
            <DetailSection>
              <div className="flex flex-wrap gap-2">
                {conn.status === "disconnected" ? (
                  <button onClick={() => void banks.openLink(conn)} disabled={banks.busy !== null} className={BTN_SMALL_PRIMARY}>{t("bank.repair")}</button>
                ) : (
                  <button onClick={() => void banks.sync(conn)} disabled={banks.busy !== null} className={BTN_SMALL_PRIMARY}>
                    {banks.busy === conn.id ? t("page.banks.working") : t("bank.refresh")}
                  </button>
                )}
                <button onClick={() => void banks.remove(conn)} disabled={banks.busy !== null}
                        className={`${BTN_GHOST} text-rose-700`}>{t("bank.remove")}</button>
              </div>
            </DetailSection>
          </div>
        ) : (
          <div className="space-y-3">
            <DetailHeader dot={DOT.off} kicker={t("page.banks.csvTitle")} title={t("upload.title")} sub={t("upload.subtitle")} />
            <button
              onClick={() => inputRef.current?.click()}
              className="w-full rounded-xl border-2 border-dashed border-slate-300 hover:border-emerald-600 hover:bg-emerald-50/50 transition-colors py-8 flex flex-col items-center gap-2 text-slate-500"
            >
              <svg viewBox="0 0 24 24" className="size-7 text-emerald-700" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2" />
              </svg>
              <span className="text-sm font-medium text-slate-700">{file ? file.name : t("upload.choose")}</span>
            </button>
            <input
              ref={inputRef}
              type="file"
              accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              className="hidden"
              onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); setError(null); }}
            />
            <button className={BTN_PRIMARY} disabled={!file || loading} onClick={handleUpload}>
              {loading ? t("page.banks.working") : t("upload.submit")}
            </button>

            {progress && (
              <div>
                <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                  <div className="h-full bg-emerald-600 transition-all"
                       style={{ width: `${Math.round((progress.done / progress.total) * 100)}%` }} />
                </div>
                <p className="text-xs text-slate-500 mt-1 tabular-nums">
                  {t("upload.progress", { defaultValue: "Categorizing {{done}} / {{total}}…", done: progress.done, total: progress.total })}
                </p>
              </div>
            )}
            {error && <div className="alert alert-error text-sm">{error}</div>}

            {result && (
              <DetailSection>
                <p className="text-sm font-semibold text-slate-900">
                  ✓ {result.inserted === 0 && result.skipped_duplicates > 0 ? t("upload.allDup") : t("upload.done")}
                </p>
                <p className="text-sm text-slate-600">{t("upload.result", { count: result.inserted })}</p>
                {result.skipped_duplicates > 0 && <p className="text-sm text-amber-700">{t("upload.skipped", { count: result.skipped_duplicates })}</p>}
                {result.held_pending > 0 && <p className="text-sm text-slate-500">{t("upload.held", { count: result.held_pending })}</p>}
                {/* AUDITOR automático (FASE 9): el agente revisó los libros al terminar la subida. */}
                {(result.audit_findings ?? 0) > 0 && (
                  <p className="text-sm text-amber-700">
                    {t("upload.audit", { defaultValue: "The auditor checked your books and found {{count}} things to look at — see the Review tab.", count: result.audit_findings })}
                  </p>
                )}
                <button onClick={onDone} className={BTN_SMALL_PRIMARY}>{t("upload.seeSummary")}</button>
              </DetailSection>
            )}
          </div>
        )}
      />

      {askAccount && (
        <AskAccountModal
          uploadResult={askAccount}
          onDone={(res) => {
            setAskAccount(null);
            setResult(res);
          }}
        />
      )}
    </div>
  );
}

// Modal que pide la cuenta bancaria cuando la IA no pudo detectarla del archivo.
// Se guarda enmascarada a los últimos 4 dígitos (lo hace el backend).
function AskAccountModal({
  uploadResult,
  onDone,
}: {
  uploadResult: UploadResult;
  onDone: (res: UploadResult) => void;
}) {
  const { t } = useTranslation();
  const [account, setAccount] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const digits = account.replace(/\D/g, "");
  const valid = digits.length >= 4;

  async function save() {
    if (!valid) return;
    setSaving(true);
    setError(null);
    try {
      await setTransactionsAccount(uploadResult.transaction_ids, account);
      onDone(uploadResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-20 bg-black/40 flex items-end sm:items-center justify-center sm:p-4">
      <div className="bg-white w-full sm:max-w-md rounded-t-2xl sm:rounded-2xl p-5 space-y-4">
        <div>
          <h3 className="font-bold text-slate-900">{t("upload.account.title")}</h3>
          <p className="text-sm text-slate-500 mt-1">{t("upload.account.subtitle")}</p>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-800 mb-1">{t("upload.account.label")}</label>
          <input
            value={account}
            onChange={(e) => setAccount(e.target.value)}
            inputMode="numeric"
            autoFocus
            placeholder={t("upload.account.placeholder")}
            className="w-full rounded-lg border border-slate-200 px-3 py-2 tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-green/40"
          />
          {valid && (
            <p className="text-xs text-slate-400 mt-1">
              {t("upload.account.preview", { masked: `****${digits.slice(-4)}` })}
            </p>
          )}
        </div>

        {error && <div className="alert alert-error text-sm">{error}</div>}

        <button
          onClick={save}
          disabled={saving || !valid}
          className="w-full py-2.5 rounded-xl font-semibold text-white shadow-sm disabled:opacity-60"
          style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
        >
          {saving ? t("upload.account.saving") : t("upload.account.save")}
        </button>
      </div>
    </div>
  );
}
