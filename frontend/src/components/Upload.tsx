import { useRef, useState } from "react";
import { uploadCsv } from "../api";

export default function Upload({ onDone }: { onDone: () => void }) {
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
      setError(e instanceof Error ? e.message : "Error desconocido");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
        <h2 className="font-semibold text-slate-900">Sube tu extracto bancario</h2>
        <p className="text-sm text-slate-500 mt-1">
          Sube un CSV de tu banco. La IA categoriza cada movimiento por ti.
        </p>

        {/* Zona de selección */}
        <button
          onClick={() => inputRef.current?.click()}
          className="w-full mt-4 rounded-2xl border-2 border-dashed border-slate-300 hover:border-brand-green hover:bg-brand-green/5 transition-colors py-10 flex flex-col items-center gap-2 text-slate-500"
        >
          <svg viewBox="0 0 24 24" className="size-8 text-brand-green" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2" />
          </svg>
          <span className="text-sm font-medium text-slate-700">
            {file ? file.name : "Toca para elegir tu archivo CSV"}
          </span>
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
          {loading ? <span className="loading loading-spinner" /> : "Subir y categorizar"}
        </button>

        {error && <div className="alert alert-error mt-4 text-sm">{error}</div>}
      </div>

      {result && (
        <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 text-center">
          <div className="size-12 rounded-full bg-brand-green/10 text-brand-green flex items-center justify-center mx-auto">
            <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5" /></svg>
          </div>
          <p className="font-semibold text-slate-900 mt-3">¡Listo!</p>
          <p className="text-sm text-slate-500">
            {result.inserted} movimientos categorizados{result.errors ? ` · ${result.errors} con error` : ""}.
          </p>
          <button onClick={onDone} className="btn mt-4 bg-brand-green text-white border-none hover:bg-brand-green-dark">
            Ver mi resumen →
          </button>
        </div>
      )}
    </div>
  );
}
