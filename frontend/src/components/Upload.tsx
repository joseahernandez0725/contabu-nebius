import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { uploadCsv } from "../api";

export default function Upload({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ inserted: number; errors: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleUpload() {
    if (!file) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await uploadCsv(file));
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
          <p className="font-semibold text-slate-900 mt-3">{t("upload.done")}</p>
          <p className="text-sm text-slate-500">{t("upload.result", { count: result.inserted })}</p>
          <button onClick={onDone} className="btn mt-4 bg-brand-green text-white border-none hover:bg-brand-green-dark">
            {t("upload.seeSummary")}
          </button>
        </div>
      )}
    </div>
  );
}
