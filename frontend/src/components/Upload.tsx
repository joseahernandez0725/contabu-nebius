import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { uploadCsv, setTransactionsAccount, type UploadResult } from "../api";

export default function Upload({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Cuando la IA no detecta la cuenta del archivo, pedimos al usuario que la escriba.
  const [askAccount, setAskAccount] = useState<UploadResult | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleUpload() {
    if (!file) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await uploadCsv(file);
      if (!res.account_detected && res.transaction_ids.length > 0) {
        setAskAccount(res); // abrir el modal para pedir la cuenta
      } else {
        setResult(res);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
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
          accept=".csv"
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
