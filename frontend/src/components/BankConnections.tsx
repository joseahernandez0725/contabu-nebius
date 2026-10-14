import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BankApiError, createBankConnection, createBankLinkToken, deleteBankConnection,
  getBankConnections, syncBankConnection,
  type BankConfig, type BankConnection, type BankSync,
} from "../api";

/**
 * BANCOS VINCULADOS (FASE 10, Movimientos · 3-oct-2026) — Plaid Link.
 *
 * El backend crea un link_token de un solo uso → Plaid Link abre → devuelve un public_token → el
 * backend lo canjea, guarda el access_token CIFRADO y trae los movimientos por el mismo motor que un
 * CSV (regla → transferencia → Pendiente). Aquí nunca pasa el access_token.
 *
 * Reparar: si el banco cortó la conexión, Link se abre en modo actualización (link_token con la
 * conexión) y, al terminar, se vuelve a sincronizar.
 */

const PLAID_SCRIPT = "https://cdn.plaid.com/link/v2/stable/link-initialize.js";

type PlaidHandler = { open: () => void; destroy: () => void };
declare global {
  interface Window {
    Plaid?: { create: (cfg: {
      token: string;
      onSuccess: (publicToken: string, metadata: { institution?: { name?: string } | null }) => void;
      onExit?: (err: unknown) => void;
    }) => PlaidHandler };
  }
}

/** Suma los resúmenes de varias conexiones (la hoja sincroniza TODAS de una vez). */
export function sumSyncs(list: BankSync[]): BankSync {
  const n = (k: keyof BankSync) => list.reduce((acc, r) => acc + (Number(r[k]) || 0), 0);
  return {
    accounts: n("accounts"), fetched: n("fetched"), inserted: n("inserted"),
    skipped_duplicates: n("skipped_duplicates"), held_pending: n("held_pending"), by_rule: n("by_rule"),
    by_transfer: n("by_transfer"), pending: n("pending"), not_ready: list.length > 0 && list.every((r) => r.not_ready),
    skipped_before_csv: n("skipped_before_csv"), modified_applied: n("modified_applied"),
    modified_recategorized: n("modified_recategorized"), removed_marked: n("removed_marked"),
    removed_ignored: n("removed_ignored"),
  };
}

/** El resumen de un sync: "N nuevos · N ya estaban · …" + lo que el banco cambió o retiró. Lo usan
 *  esta tarjeta y el botón "↻ Actualizar banco" de la hoja, para que digan lo mismo. */
export function SyncSummary({ s, className = "" }: { s: BankSync; className?: string }) {
  const { t } = useTranslation();
  return (
    <div className={`alert text-sm bg-brand-green/5 text-slate-700 border-brand-green/20 ${className}`}>
      <div>
        {s.not_ready ? t("bank.notReady") : t("bank.synced", {
          inserted: s.inserted, skipped: s.skipped_duplicates, held: s.held_pending, pending: s.pending,
        })}
        {!s.not_ready && ((s.skipped_before_csv ?? 0) > 0) && (
          <div className="mt-1">{t("bank.beforeCsv", { count: s.skipped_before_csv })}</div>
        )}
        {!s.not_ready && ((s.modified_applied ?? 0) > 0) && (
          <div className="mt-1">{t("bank.modified", { count: s.modified_applied, recat: s.modified_recategorized ?? 0 })}</div>
        )}
        {!s.not_ready && ((s.removed_marked ?? 0) > 0) && (
          <div className="mt-1 text-amber-700">{t("bank.removed", { count: s.removed_marked })}</div>
        )}
      </div>
    </div>
  );
}

let scriptPromise: Promise<void> | null = null;
function loadPlaid(): Promise<void> {
  if (window.Plaid) return Promise.resolve();
  scriptPromise ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = PLAID_SCRIPT;
    s.onload = () => resolve();
    s.onerror = () => { scriptPromise = null; reject(new BankApiError("bank_script")); };
    document.body.appendChild(s);
  });
  return scriptPromise;
}

/** Los bancos vinculados del libro y sus acciones (vincular, actualizar, reparar, desvincular).
 *  13-oct (Firm, diseño de página compartido): la LÓGICA es la misma de antes; solo salió del componente
 *  a un hook para que la página Bancos (`Upload.tsx`) la pinte como lista + detalle. */
export function useBanks() {
  const { t, i18n } = useTranslation();
  const [config, setConfig] = useState<BankConfig | null>(null);
  const [conns, setConns] = useState<BankConnection[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<number | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<BankSync | null>(null);

  const errText = useCallback((e: unknown) => {
    const code = e instanceof BankApiError ? e.code : "bank_error";
    return t(`bank.err.${code}`, { defaultValue: t("bank.err.bank_error") });
  }, [t]);

  const load = useCallback(() => {
    getBankConnections()
      .then((r) => { setConfig(r.config); setConns(r.connections); })
      .catch((e) => setError(errText(e)))
      .finally(() => setLoaded(true));
  }, [errText]);
  useEffect(() => { load(); }, [load]);

  /** Abre Plaid Link. Sin `repair` vincula un banco nuevo; con `repair`, repara esa conexión. */
  async function openLink(repair?: BankConnection) {
    setBusy(repair ? repair.id : "new"); setError(null); setLast(null);
    try {
      const lang = (i18n.language || "en").split("-")[0];
      const [{ link_token }] = await Promise.all([createBankLinkToken(lang, repair?.id), loadPlaid()]);
      const handler = window.Plaid!.create({
        token: link_token,
        onSuccess: async (publicToken, metadata) => {
          try {
            const r = repair
              ? await syncBankConnection(repair.id)   // modo actualización: el token no cambia
              : await createBankConnection(publicToken, metadata?.institution?.name);
            setLast(r.sync);
          } catch (e) { setError(errText(e)); }
          finally { handler.destroy(); setBusy(null); load(); }
        },
        onExit: () => { handler.destroy(); setBusy(null); },
      });
      handler.open();
    } catch (e) {
      setError(errText(e)); setBusy(null);
    }
  }

  async function sync(c: BankConnection) {
    setBusy(c.id); setError(null); setLast(null);
    try { setLast((await syncBankConnection(c.id)).sync); }
    catch (e) { setError(errText(e)); }
    finally { setBusy(null); load(); }
  }

  /** Actualiza TODAS las conexiones activas (el mismo resumen sumado que usa la hoja). */
  async function syncAll() {
    const active = conns.filter((c) => c.status !== "disconnected");
    if (!active.length) return;
    setBusy("new"); setError(null); setLast(null);
    const results: BankSync[] = [];
    for (const c of active) {
      try { results.push((await syncBankConnection(c.id)).sync); }
      catch (e) { setError(errText(e)); }
    }
    if (results.length) setLast(sumSyncs(results));
    setBusy(null); load();
  }

  async function remove(c: BankConnection) {
    if (!window.confirm(t("bank.confirmRemove", { name: c.institution_name ?? t("bank.unnamed") }))) return;
    setBusy(c.id); setError(null); setLast(null);
    try { await deleteBankConnection(c.id); }
    catch (e) { setError(errText(e)); }
    finally { setBusy(null); load(); }
  }

  return { config, conns, loaded, busy, error, last, openLink, sync, syncAll, remove };
}
