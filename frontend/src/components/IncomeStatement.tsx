import { useTranslation } from "react-i18next";
import { money } from "../api";
import { PNL } from "../accounting";
import type { CategoryAmount, IncomeStatement as IS, Uncategorized } from "../types";

function Row({ label, value, strong, indent }: {
  label: string; value: number; strong?: boolean; indent?: boolean;
}) {
  return (
    <div
      className={[
        "flex justify-between py-1.5 text-sm",
        strong ? "font-semibold border-t border-slate-200 pt-2 text-slate-900" : "text-slate-600",
        indent ? "ps-3" : "",
      ].join(" ")}
    >
      <span>{label}</span>
      <span className="tabular-nums">{money(value)}</span>
    </div>
  );
}

// Líneas de categoría debajo de una sección. El nombre contable va en inglés (regla de oro);
// si el idioma ≠ inglés, se muestra una traducción translúcida al lado.
function CatLines({ rows }: { rows?: CategoryAmount[] }) {
  const { t, i18n } = useTranslation();
  const showTr = (i18n.language || "en").split("-")[0] !== "en";
  if (!rows || rows.length === 0) return null;
  return (
    <div className="ps-5 pb-1">
      {rows.map((r) => {
        const tr = showTr ? t(`cat.${r.category}`, { defaultValue: "" }) : "";
        return (
          <div key={r.category} className="flex justify-between py-1 text-xs text-slate-500">
            <span className="truncate">
              {r.category}
              {tr && <span className="ms-1 text-slate-400/60 italic">· {tr}</span>}
            </span>
            <span className="tabular-nums">{money(r.amount)}</span>
          </div>
        );
      })}
    </div>
  );
}

// LO PENDIENTE (FASE 12) — el bloque que hace honesto al reporte.
// Va DEBAJO del Net Income y fuera de la aritmética: estas transacciones no tienen `account_type`,
// así que no se sabe si suman o restan. Se declaran como CAJA (entró / salió), nunca como resultado.
// Con 0 pendientes no se dibuja nada: un P&L completo se ve como siempre.
function PendingBlock({ u }: { u: Uncategorized }) {
  const { t } = useTranslation();
  if (!u || u.count === 0) return null;
  return (
    <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50/60 p-4">
      <div className="flex items-center gap-2">
        <span className="size-2 rounded-full bg-amber-500" aria-hidden />
        <h3 className="font-semibold text-amber-900 text-sm">{t("pnl.pendingTitle")}</h3>
      </div>
      <p className="text-xs text-amber-900/80 mt-1">
        {t("pnl.pendingLead", { count: u.count })} {t("pnl.pendingWhy")}
      </p>

      <div className="mt-3 space-y-1">
        <div className="flex justify-between text-sm text-amber-900">
          <span>{t("pnl.pendingIn")} <span className="text-amber-900/60">· {u.count_in}</span></span>
          <span className="tabular-nums">{money(u.money_in)}</span>
        </div>
        <div className="flex justify-between text-sm text-amber-900">
          <span>{t("pnl.pendingOut")} <span className="text-amber-900/60">· {u.count_out}</span></span>
          <span className="tabular-nums">{money(u.money_out)}</span>
        </div>
        {u.count_unknown > 0 && (
          <div className="flex justify-between text-sm text-amber-900">
            <span>{t("pnl.pendingUnknown")} <span className="text-amber-900/60">· {u.count_unknown}</span></span>
            <span className="tabular-nums">{money(u.money_unknown)}</span>
          </div>
        )}
        <div className="flex justify-between text-sm font-semibold text-amber-900 border-t border-amber-200 pt-1.5">
          <span>{t("pnl.pendingNetCash")}</span>
          <span className="tabular-nums">{money(u.net_cash)}</span>
        </div>
      </div>

      <p className="text-xs text-amber-900/70 mt-2.5">{t("pnl.pendingHint")}</p>
    </div>
  );
}

// Estado de resultados (P&L) DETALLADO: cada sección con su desglose por categoría.
// Términos contables en inglés (no se traducen); los montos los calcula SQL en el backend.
// `bare` (13-oct, Firm): sin su propia tarjeta, para ir DENTRO del detalle del diseño compartido (`PageKit`).
export default function IncomeStatement({ data, bare = false }: { data: IS; bare?: boolean }) {
  const { t } = useTranslation();
  const b = data.breakdown ?? {};
  const u = data.uncategorized;
  const hasPending = (u?.count ?? 0) > 0;
  return (
    <div className={bare ? "" : "bg-white rounded-2xl shadow-sm border border-slate-100 p-5"}>
      <h2 className="font-semibold text-slate-900 mb-3">{PNL.title}</h2>

      <Row label={PNL.revenue} value={data.revenue} />
      <CatLines rows={b.REVENUE} />

      <Row label={PNL.cogs} value={data.cogs} indent />
      <CatLines rows={b.COGS} />

      <Row label={PNL.grossProfit} value={data.gross_profit} strong />

      <Row label={PNL.opex} value={data.operating_expenses} indent />
      <CatLines rows={b.OPERATING_EXPENSE} />

      <Row label={PNL.operatingIncome} value={data.operating_income} strong />

      <Row label={PNL.otherIncome} value={data.other_income} indent />
      <CatLines rows={b.OTHER_INCOME} />

      <Row label={PNL.otherExpense} value={data.other_expense} indent />
      <CatLines rows={b.OTHER_EXPENSE} />

      <div className="flex justify-between items-center mt-3 pt-3 border-t-2 border-brand-green">
        <span className="font-bold text-brand-green">
          {PNL.netIncome}
          {/* Con pendientes, el resultado NO es final: se dice en la etiqueta, no en una nota al pie. */}
          {hasPending && (
            <span className="ms-1 font-normal text-sm text-amber-700">· {t("pnl.beforePending")}</span>
          )}
        </span>
        <span className="font-bold text-brand-green text-lg tabular-nums">{money(data.net_income)}</span>
      </div>

      {u && <PendingBlock u={u} />}
    </div>
  );
}
