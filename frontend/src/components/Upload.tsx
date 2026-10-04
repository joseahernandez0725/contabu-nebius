import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { uploadCsv, setTransactionsAccount, type UploadResult } from "../api";
import BankConnections from "./BankConnections";

export default function Upload({ onDone, onProposeRules }: {
  onDone: () => void;
  // La IA categorizó movimientos → paso siguiente del agente: proponer el REGLAMENTO (lleva a
  // Reglas y genera las propuestas solo). Sin esto el proponedor queda escondido tras un botón.
  onProposeRules?: () => void;
}) {
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
        account_detected: false, transaction_ids: [], quota_blocked: 0, by_ai: 0,
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
        agg.quota_blocked = (agg.quota_blocked ?? 0) + (res.quota_blocked ?? 0);
        agg.by_ai = (agg.by_ai ?? 0) + (res.by_ai ?? 0);
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

  return (
    <div className="space-y-4">
      {/* BANCOS (Jose, 4-oct): primero los bancos vinculados (Plaid; los movimientos entran solos, por
          el mismo motor) y debajo, la otra puerta: el extracto en CSV. */}
      <BankConnections />
      <div className="flex items-center gap-3 text-xs text-slate-400">
        <span className="h-px flex-1 bg-slate-200" />
        {t("upload.orCsv")}
        <span className="h-px flex-1 bg-slate-200" />
      </div>
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
        <h2 className="font-semibold text-slate-900">{t("upload.title")}</h2>
        <p className="text-sm text-slate-500 mt-1">{t("upload.subtitle")}</p>

        <button
          onClick={() => inputRef.current?.click()}
          className="w-full mt-4 rounded-2xl border-2 border-dashed border-slate-300 hover:border-brand-green hover:bg-brand-green/5 transition-colors py-10 flex flex-col items-center gap-2 text-slate-500"
        >
          <svg viewBox="0 0 24 24" className="size-8 text-brand-green" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
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

        <button
          className="btn w-full mt-4 bg-brand-green text-white border-none hover:bg-brand-green-dark disabled:opacity-50"
          disabled={!file || loading}
          onClick={handleUpload}
        >
          {loading ? <span className="loading loading-spinner" /> : t("upload.submit")}
        </button>

        {progress && (
          <div className="mt-4">
            <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
              <div
                className="h-full bg-brand-green transition-all"
                style={{ width: `${Math.round((progress.done / progress.total) * 100)}%` }}
              />
            </div>
            <p className="text-xs text-slate-500 mt-1 text-center tabular-nums">
              {t("upload.progress", {
                defaultValue: "Categorizing {{done}} / {{total}}…",
                done: progress.done, total: progress.total,
              })}
            </p>
          </div>
        )}

        {error && <div className="alert alert-error mt-4 text-sm">{error}</div>}
      </div>

      {result && (
        <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 text-center">
          <div className="size-12 rounded-full bg-brand-green/10 text-brand-green flex items-center justify-center mx-auto">
            <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5" /></svg>
          </div>
          <p className="font-semibold text-slate-900 mt-3">
            {result.inserted === 0 && result.skipped_duplicates > 0 ? t("upload.allDup") : t("upload.done")}
          </p>
          <p className="text-sm text-slate-500">{t("upload.result", { count: result.inserted })}</p>
          {result.skipped_duplicates > 0 && (
            <p className="text-sm text-amber-600 mt-1">
              {t("upload.skipped", { count: result.skipped_duplicates })}
            </p>
          )}
          {result.held_pending > 0 && (
            <p className="text-sm text-slate-500 mt-1">
              {t("upload.held", { count: result.held_pending })}
            </p>
          )}
          {(result.quota_blocked ?? 0) > 0 && (
            <p className="text-sm text-rose-600 mt-1">
              {t("upload.quotaBlocked", {
                defaultValue: "{{count}} transactions weren't categorized — you've hit your AI quota. Upgrade your plan to categorize them.",
                count: result.quota_blocked,
              })}
            </p>
          )}
          {/* AUDITOR automático (FASE 9): el agente revisó los libros al terminar la subida. */}
          {(result.audit_findings ?? 0) > 0 && (
            <p className="text-sm text-amber-600 mt-1">
              {t("upload.audit", {
                defaultValue: "The auditor checked your books and found {{count}} things to look at — see the Review tab.",
                count: result.audit_findings,
              })}
            </p>
          )}
          {/* PASO SIGUIENTE del agente: la IA categorizó → convertir lo aprendido en REGLAS que el
              dueño aprueba (a la pantalla de propuestas, generándolas solas). */}
          {(result.by_ai ?? 0) > 0 && onProposeRules && (
            <div className="mt-4 text-start bg-brand-green/5 border border-brand-green/20 rounded-xl px-4 py-3">
              <p className="text-sm text-slate-700">
                {t("upload.proposeHint", {
                  defaultValue: "The AI categorized {{count}} transactions. Turn what it learned into rules you approve — next time they won't need AI.",
                  count: result.by_ai,
                })}
              </p>
              <button
                onClick={onProposeRules}
                className="mt-2 px-4 py-2 rounded-xl font-semibold text-white shadow-sm"
                style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
              >
                {t("upload.proposeCta", { defaultValue: "Create my rules" })}
              </button>
            </div>
          )}
          <button onClick={onDone} className="btn mt-4 bg-brand-green text-white border-none hover:bg-brand-green-dark">
            {t("upload.seeSummary")}
          </button>
        </div>
      )}

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
