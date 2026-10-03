import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getPivot, getPivotLeaves, type PivotNode, type PivotLeaf } from "../api";

/**
 * EL PIVOTE (FASE 10, paso 7) — el drill-down que pidió Jose:
 *   categoría → (+) subcategoría → (+) meses → (+) día exacto + descripción
 *
 * Los totales vienen agregados de SQL (regla de oro: la IA no calcula, y Python tampoco re-suma).
 * El último nivel se pide bajo demanda: cargar todas las transacciones de todos los meses sería
 * enorme y, casi siempre, desperdiciado.
 */

/** Secciones que RESTAN en el estado de resultados. */
const SUBTRACTS = new Set(["COGS", "OPERATING_EXPENSE", "OTHER_EXPENSE"]);

/** Convención contable: lo que resta va entre PARÉNTESIS — $(1,200.00) — no con signo menos.
 *  Es como se lee un P&L de verdad, y de un vistazo se distingue qué suma y qué resta. */
const money = (v: string | number, subtracts = false) => {
  const n = Number(v ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return subtracts ? `$(${n})` : `$${n}`;
};

const SECTION_COLOR: Record<string, string> = {
  REVENUE: "#15803d",            // verde: entra
  COGS: "#b45309",               // ámbar tostado: costo directo
  OPERATING_EXPENSE: "#0369a1",  // azul: overhead — antes era casi negro y se confundía con el texto
  OTHER_INCOME: "#15803d",
  OTHER_EXPENSE: "#991b1b",      // rojo: otros gastos
  UNCATEGORIZED: "#5b6b62",
};
const SECTION_LABEL: Record<string, string> = {
  REVENUE: "Revenue", COGS: "COGS", OPERATING_EXPENSE: "Operating expenses",
  OTHER_INCOME: "Other income", OTHER_EXPENSE: "Other expenses", UNCATEGORIZED: "Uncategorized",
};


/** Una fila del árbol. Va FUERA de `Pivot` a propósito: definida adentro, cada render creaba un
 *  tipo de componente nuevo y React desmontaba el subárbol recursivo — los hijos nunca aparecían
 *  al expandir (bug encontrado 27-sep). */
function Row({ node, depth, path, open, leaves, onToggle, t, section }: {
  node: PivotNode; depth: number; path: string[]; section: string;
  open: Set<string>; leaves: Record<string, PivotLeaf[] | "loading">;
  onToggle: (node: PivotNode, path: string[]) => void;
  t: (k: string, o?: any) => string;
}) {
  const isOpen = open.has(node.key);
  const kids = node.children;
  const hasKids = node.level === "month" || (kids && kids.length > 0);
  const leafRows = leaves[node.key];
  const subtracts = SUBTRACTS.has(section);
  const color = SECTION_COLOR[section] ?? "#0f172a";

  return (
    <>
      <div
        onClick={() => hasKids && onToggle(node, path)}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: `${depth === 0 ? 8 : 5}px 12px`,
          paddingInlineStart: 12 + depth * 22,
          borderBottom: "1px solid #f0ece2",
          cursor: hasKids ? "pointer" : "default",
          background: depth === 0 ? "#fff" : depth === 1 ? "#fdfcf8" : "#FBF7EE",
          fontWeight: depth === 0 ? 600 : 400,
        }}
      >
        <span style={{
          width: 16, flex: "none", textAlign: "center", color: "#5b6b62",
          fontSize: 11, visibility: hasKids ? "visible" : "hidden",
        }}>{isOpen ? "−" : "+"}</span>

        <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis",
                       whiteSpace: "nowrap", fontSize: depth === 0 ? 14 : 13.5 }}>
          {node.label}
          {depth === 0 && node.section && (
            <span style={{ marginInlineStart: 8, fontSize: 10.5, fontWeight: 700,
                           letterSpacing: ".05em", textTransform: "uppercase",
                           color: SECTION_COLOR[node.section] ?? "#5b6b62" }}>
              {t(`sheet.section.${node.section}`, { defaultValue: SECTION_LABEL[node.section] ?? node.section })}
            </span>
          )}
        </span>

        <span style={{ fontSize: 11.5, color: "#5b6b62", flex: "none" }}>
          {t("pivot.txs", { defaultValue: "txs", count: node.count })}
        </span>
        <span style={{ fontVariantNumeric: "tabular-nums", minWidth: 110, textAlign: "right",
                       flex: "none", fontSize: depth === 0 ? 14 : 13.5, color }}>
          {money(node.amount, subtracts)}
        </span>
      </div>

      {isOpen && kids?.map((k) => (
        <Row key={k.key} node={k} depth={depth + 1} path={[...path, k.label]}
             open={open} leaves={leaves} onToggle={onToggle} t={t} section={section} />
      ))}

      {isOpen && node.level === "month" && (
        leafRows === "loading"
          ? <div style={{ padding: `4px 12px 4px ${12 + (depth + 1) * 22}px`, fontSize: 12.5, color: "#5b6b62" }}>…</div>
          : (leafRows ?? []).map((tx) => (
            <div key={tx.id} style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "4px 12px", paddingInlineStart: 12 + (depth + 1) * 22,
              borderBottom: "1px solid #f5f2e9", background: "#F7F3E8", fontSize: 12.5,
            }}>
              <span style={{ width: 16, flex: "none" }} />
              <span style={{ width: 92, flex: "none", color: "#5b6b62", fontVariantNumeric: "tabular-nums" }}>
                {new Date(tx.date + "T00:00:00").toLocaleDateString("en-US",
                  { month: "short", day: "2-digit", year: "numeric" })}
              </span>
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis",
                             whiteSpace: "nowrap" }}>{tx.description}</span>
              <span style={{ fontVariantNumeric: "tabular-nums", minWidth: 110, textAlign: "right",
                             flex: "none", color: tx.is_inflow ? "#15803d" : "#0f172a" }}>
                {money(tx.amount)}
              </span>
            </div>
          ))
      )}
    </>
  );
}

export default function Pivot({ account }: { account?: string }) {
  const { t } = useTranslation();
  const [nodes, setNodes] = useState<PivotNode[] | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [leaves, setLeaves] = useState<Record<string, PivotLeaf[] | "loading">>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPivot(account).then(setNodes).catch((e) => setError(String(e?.message ?? e)));
  }, [account]);

  const toggle = useCallback(async (node: PivotNode, path: string[]) => {
    const key = node.key;
    setOpen((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
    // Nivel mes: al abrirlo se piden sus transacciones (una sola vez).
    if (node.level === "month" && !leaves[key]) {
      setLeaves((l) => ({ ...l, [key]: "loading" }));
      try {
        const [section, category, subcategory] = path;
        const rows = await getPivotLeaves(section, category, subcategory, node.label);
        setLeaves((l) => ({ ...l, [key]: rows }));
      } catch {
        setLeaves((l) => ({ ...l, [key]: [] }));
      }
    }
  }, [leaves]);

  if (error) return <p style={{ color: "#991b1b", padding: 16 }}>{error}</p>;
  if (!nodes) return <p style={{ color: "#5b6b62", padding: 16 }}>{t("common.loading", { defaultValue: "Loading…" })}</p>;
  if (!nodes.length)
    return <p style={{ color: "#5b6b62", padding: 16 }}>
      {t("pivot.empty", { defaultValue: "No categorized transactions yet." })}
    </p>;

  return (
    <div style={{ border: "1px solid #e3ded2", borderRadius: 10, overflow: "hidden", background: "#fff" }}>
      <div style={{ display: "flex", padding: "7px 12px", background: "#FBF7EE",
                    borderBottom: "1px solid #e3ded2", fontSize: 11, fontWeight: 700,
                    letterSpacing: ".06em", textTransform: "uppercase", color: "#5b6b62" }}>
        <span style={{ flex: 1 }}>{t("pivot.account", { defaultValue: "Account" })}</span>
        <span style={{ minWidth: 110, textAlign: "right" }}>{t("pivot.total", { defaultValue: "Total" })}</span>
      </div>
      {nodes.map((n) => (
        <Row key={n.key} node={n} depth={0} path={[n.section ?? "", n.label]}
             open={open} leaves={leaves} onToggle={toggle} t={t} section={n.section ?? ""} />
      ))}
    </div>
  );
}
