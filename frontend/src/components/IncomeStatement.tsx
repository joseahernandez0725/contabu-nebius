import { useState, type ReactNode } from "react";
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
// En `compact` se reusa tal cual dentro de la sección desplegada: la traducción se queda (18-oct,
// ronda de diseño 2) porque es justo donde el lector en español más la necesita — las cabeceras
// plegadas de alrededor son contables y SIEMPRE en inglés; truncar ya la recorta primero que el
// nombre de la cuenta, así que nunca rompe el ancho de ~600px.
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
function PendingDetail({ u }: { u: Uncategorized }) {
  const { t } = useTranslation();
  return (
    <>
      <p className="text-xs text-amber-900/80">
        {t("pnl.pendingLead", { count: u.count })} {t("pnl.pendingWhy")}
      </p>
      <div className="mt-2 space-y-1">
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
    </>
  );
}

function PendingBlock({ u }: { u: Uncategorized }) {
  const { t } = useTranslation();
  if (!u || u.count === 0) return null;
  return (
    <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50/60 p-4">
      <div className="flex items-center gap-2">
        <span className="size-2 rounded-full bg-amber-500" aria-hidden />
        <h3 className="font-semibold text-amber-900 text-sm">{t("pnl.pendingTitle")}</h3>
      </div>
      <div className="mt-1">
        <PendingDetail u={u} />
      </div>
    </div>
  );
}

// Lo Pendiente en `compact`: se declara SIEMPRE visible, en una sola línea con el efecto neto en
// caja; el detalle (Entradas · Salidas · lo que el banco no reportó) es un desplegable, nunca se
// esconde del todo (regla 4 de CLAUDE.md).
function CompactPending({ u, open, onToggle }: { u: Uncategorized; open: boolean; onToggle: () => void }) {
  const { t } = useTranslation();
  if (!u || u.count === 0) return null;
  return (
    <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50/60">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left hover:bg-amber-50 rounded-lg"
      >
        <span className="flex items-center gap-1.5 text-sm text-amber-900">
          <span className="text-amber-500 text-xs w-3 inline-block">{open ? "▾" : "▸"}</span>
          <span className="size-2 rounded-full bg-amber-500 inline-block shrink-0" aria-hidden />
          {t("pnl.pendingTitle")}
          <span className="text-amber-900/60">· {u.count}</span>
        </span>
        <span className="tabular-nums font-semibold text-amber-900">{money(u.net_cash)}</span>
      </button>
      {open && (
        <div className="px-3 pb-3">
          <PendingDetail u={u} />
        </div>
      )}
    </div>
  );
}

// Cabecera plegada de una sección con cuentas (Revenue, COGS, Operating expenses): el total se ve
// siempre, un clic despliega sus cuentas (`CatLines`). Sin cuentas que mostrar, no hay nada que
// desplegar: la fila queda como un total fijo, sin la flecha.
function CompactSection({ label, value, rows, open, onToggle }: {
  label: string; value: number; rows?: CategoryAmount[]; open: boolean; onToggle: () => void;
}) {
  const foldable = !!rows && rows.length > 0;
  return (
    <div className="border-b border-slate-100 last:border-b-0">
      <button
        type="button"
        onClick={foldable ? onToggle : undefined}
        aria-expanded={foldable ? open : undefined}
        disabled={!foldable}
        className={[
          "w-full flex items-center justify-between gap-2 py-1.5 text-sm text-left",
          foldable ? "cursor-pointer hover:bg-slate-50 rounded" : "cursor-default",
        ].join(" ")}
      >
        <span className="flex items-center gap-1.5 text-slate-700">
          <span className="text-slate-400 text-xs w-3 inline-block" aria-hidden>
            {foldable ? (open ? "▾" : "▸") : ""}
          </span>
          {label}
        </span>
        <span className="tabular-nums text-slate-900">{money(value)}</span>
      </button>
      {foldable && open && <CatLines rows={rows} />}
    </div>
  );
}

// Other income / Other expense comparten una sola cabecera plegada ("Other") en compact: son la
// misma sección visual en el diseño de 18-oct y, separados, serían dos filas de un solo signo cada
// una — aquí se muestran netas arriba y desglosadas (con su propio +/−) al desplegar.
function CompactOther({ incomeValue, expenseValue, incomeRows, expenseRows, open, onToggle }: {
  incomeValue: number; expenseValue: number;
  incomeRows?: CategoryAmount[]; expenseRows?: CategoryAmount[];
  open: boolean; onToggle: () => void;
}) {
  const net = incomeValue - expenseValue;
  const foldable = (incomeRows?.length ?? 0) > 0 || (expenseRows?.length ?? 0) > 0;
  return (
    <div className="border-b border-slate-100">
      <button
        type="button"
        onClick={foldable ? onToggle : undefined}
        aria-expanded={foldable ? open : undefined}
        disabled={!foldable}
        className={[
          "w-full flex items-center justify-between gap-2 py-1.5 text-sm text-left",
          foldable ? "cursor-pointer hover:bg-slate-50 rounded" : "cursor-default",
        ].join(" ")}
      >
        <span className="flex items-center gap-1.5 text-slate-700">
          <span className="text-slate-400 text-xs w-3 inline-block" aria-hidden>
            {foldable ? (open ? "▾" : "▸") : ""}
          </span>
          {PNL.other}
        </span>
        <span className="tabular-nums text-slate-900">{money(net)}</span>
      </button>
      {foldable && open && (
        <div className="ps-3 pb-1">
          <Row label={PNL.otherIncome} value={incomeValue} />
          <CatLines rows={incomeRows} />
          <Row label={PNL.otherExpense} value={expenseValue} />
          <CatLines rows={expenseRows} />
        </div>
      )}
    </div>
  );
}

// Subtotal sin cuentas propias (Gross profit, Operating income): no hay nada que desplegar, así
// que es una fila fija, igual que en el `Row` de la vista completa pero con el espaciado de compact.
function CompactTotal({ label, value, big, suffix }: {
  label: string; value: number; big?: boolean; suffix?: ReactNode;
}) {
  return (
    <div
      className={[
        "flex items-center justify-between py-1.5 text-sm font-semibold",
        big
          ? "mt-1 pt-2 border-t-2 border-brand-green text-brand-green"
          : "border-t border-slate-200 text-slate-900",
      ].join(" ")}
    >
      <span className="flex items-center">{label}{suffix}</span>
      <span className={["tabular-nums", big ? "text-lg" : ""].join(" ")}>{money(value)}</span>
    </div>
  );
}

// Estado de resultados (P&L) DETALLADO: cada sección con su desglose por categoría.
// Términos contables en inglés (no se traducen); los montos los calcula SQL en el backend.
// `bare` (13-oct, Firm): sin su propia tarjeta, para ir DENTRO del detalle del diseño compartido (`PageKit`).
// `compact` (18-oct, ronda de diseño 2): secciones plegadas por defecto —solo el total—, cuentas y
// montos cerca, para convivir en MEDIA pantalla junto a "Lo que necesita tu atención". Sin la prop,
// nada cambia: la pestaña P&L, el PDF y el Excel siguen con el layout de siempre.
export default function IncomeStatement({ data, bare = false, compact = false }: {
  data: IS; bare?: boolean; compact?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const toggle = (key: string) => setOpen((o) => ({ ...o, [key]: !o[key] }));

  const b = data.breakdown ?? {};
  const u = data.uncategorized;
  const hasPending = (u?.count ?? 0) > 0;

  if (compact) {
    const beforePending = hasPending ? (
      <span className="ms-1 font-normal text-xs text-amber-700">· {t("pnl.beforePending")}</span>
    ) : undefined;
    return (
      <div className={bare ? "" : "bg-white rounded-2xl shadow-sm border border-slate-100 p-4"}>
        <h2 className="font-semibold text-slate-900 mb-1 text-sm">{PNL.title}</h2>
        <CompactSection
          label={PNL.revenue} value={data.revenue} rows={b.REVENUE}
          open={!!open.revenue} onToggle={() => toggle("revenue")}
        />
        <CompactSection
          label={PNL.cogs} value={data.cogs} rows={b.COGS}
          open={!!open.cogs} onToggle={() => toggle("cogs")}
        />
        <CompactTotal label={PNL.grossProfit} value={data.gross_profit} />
        <CompactSection
          label={PNL.opex} value={data.operating_expenses} rows={b.OPERATING_EXPENSE}
          open={!!open.opex} onToggle={() => toggle("opex")}
        />
        <CompactTotal label={PNL.operatingIncome} value={data.operating_income} />
        <CompactOther
          incomeValue={data.other_income} expenseValue={data.other_expense}
          incomeRows={b.OTHER_INCOME} expenseRows={b.OTHER_EXPENSE}
          open={!!open.other} onToggle={() => toggle("other")}
        />
        <CompactTotal big label={PNL.netIncome} value={data.net_income} suffix={beforePending} />
        {u && <CompactPending u={u} open={!!open.pending} onToggle={() => toggle("pending")} />}
      </div>
    );
  }

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
