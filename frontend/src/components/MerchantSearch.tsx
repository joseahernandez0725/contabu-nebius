import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AgentError, searchMerchant, type AgentSpent } from "../api";
import { LiveOnly } from "./DemoDoors";   // visita guiada del demo (Firm, 23-oct)

// "BUSCAR ESTE COMERCIO" (FASE 11, 14-oct — Tavily rediseñado para gastar poco): el ② busca en internet
// el comercio de UN movimiento, al instante, y solo si la oficina no lo buscó ya. Primero una búsqueda
// básica (1 crédito); si la confianza no sale alta, una a fondo (2 créditos más). Si el comercio tiene
// hallazgos, el ① los vuelve a juzgar con la evidencia nueva (la ida y vuelta). Nunca busca personas.
// Se usa en Auditoría (hallazgo y grupo de Pendientes) y sirve igual para el panel de la transacción.

export default function MerchantSearch({ transactionId, onDone }: {
  transactionId: number;
  onDone?: (spent?: AgentSpent) => void;
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { setMsg(null); setError(null); }, [transactionId]);

  async function run() {
    setBusy(true); setMsg(null); setError(null);
    try {
      const r = await searchMerchant(transactionId, lang);
      const it = r.item;
      let text: string;
      if (r.skipped || !it) text = t("auditView.search.skipped");
      else if (!r.searched) text = t("auditView.search.cached");
      else if (it.business_type) text = t("auditView.search.found", {
        type: t(`agents.webType.${it.business_type}`, { defaultValue: it.business_type }),
        confidence: t(`agents.confidence.${it.confidence}`, { defaultValue: it.confidence ?? "" }),
        depth: t(`auditView.search.depth.${it.search_depth ?? "basic"}`), credits: it.credits,
      });
      else text = t("auditView.search.notFound", { credits: it.credits });
      if (r.rejudge?.rejudged) text += " " + t("auditView.search.rejudged", { count: r.rejudge.rejudged });
      setMsg(text);
      onDone?.(r.spent);
    } catch (e) {
      setError(e instanceof AgentError
        ? t(`agents.error.${e.code}`, { defaultValue: t("agents.error.agent_error") })
        : t("agents.error.agent_error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
      <LiveOnly compact><button onClick={run} disabled={busy} title={t("auditView.search.hint")}
              className="px-2.5 py-1 rounded-lg font-semibold text-sky-800 ring-1 ring-sky-200 hover:bg-sky-50 disabled:opacity-50">
        {busy ? t("auditView.search.searching") : t("auditView.search.button")}
      </button></LiveOnly>
      {!msg && !error && <span className="text-slate-400">{t("auditView.search.hint")}</span>}
      {msg && <span className="text-slate-700">{msg}</span>}
      {error && <span className="text-rose-700">{error}</span>}
    </div>
  );
}
