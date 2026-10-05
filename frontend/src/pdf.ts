// Exporta el estado de resultados (P&L) a PDF, en el cliente (jsPDF).
// El documento va en INGLÉS a propósito: es un documento contable/fiscal y la regla de oro
// mantiene los datos contables en inglés (la UI traducida es solo una capa visual).
import { jsPDF } from "jspdf";
import type { IncomeStatement } from "./types";

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD" });

// Monto en estilo contable: los gastos entre paréntesis. (1234.5) para restas.
const acct = (n: number, expense = false) => (expense ? `(${usd(Math.abs(n))})` : usd(n));

export function exportIncomeStatementPdf(
  companyName: string,
  is: IncomeStatement,
  start?: string,
  end?: string,
) {
  const doc = new jsPDF({ unit: "pt", format: "letter" });
  const L = 56; // margen izquierdo
  const R = 556; // borde derecho (612 - 56)
  let y = 64;

  // Encabezado estándar de 3 líneas (empresa · título · periodo) — ver REFERENCIA CONTABLE §4.
  doc.setFont("helvetica", "bold").setFontSize(20).setTextColor(21, 128, 61); // brand green
  doc.text("ContaBu", L, y);
  doc.setFont("helvetica", "bold").setFontSize(14).setTextColor(20);
  doc.text(companyName, L, y + 26);
  doc.setFont("helvetica", "normal").setFontSize(13).setTextColor(60);
  doc.text("Income Statement (Profit & Loss)", L, y + 44);
  // El income statement cubre un PERIODO, no una fecha puntual.
  const period =
    start && end ? `For the period ${start} to ${end}`
    : end ? `For the period ended ${end}`
    : start ? `For the period from ${start}`
    : "For all available transactions";
  doc.setTextColor(110).setFontSize(10);
  doc.text(period, L, y + 60);
  doc.text("Amounts in US dollars (USD) · Cash basis", L, y + 74);
  doc.text(`Generated: ${new Date().toISOString().slice(0, 10)}`, R, y + 74, { align: "right" });
  y += 14; // el encabezado creció una línea

  y += 90;
  doc.setDrawColor(220).line(L, y, R, y);
  y += 26;

  // Una fila: etiqueta a la izquierda, monto a la derecha. `strong` = subtotal (negrita + línea).
  const row = (
    label: string,
    value: number,
    opts: { strong?: boolean; expense?: boolean; big?: boolean } = {},
  ) => {
    if (opts.strong) {
      doc.setDrawColor(220).line(L, y - 14, R, y - 14);
    }
    doc.setFont("helvetica", opts.strong ? "bold" : "normal");
    doc.setFontSize(opts.big ? 13 : 11);
    // Color: Net Income destacado verde (positivo) o rojo (negativo); el resto gris oscuro.
    let rgb: [number, number, number] = [40, 40, 40];
    if (opts.big) rgb = value < 0 ? [220, 38, 38] : [21, 128, 61];
    doc.setTextColor(rgb[0], rgb[1], rgb[2]);
    doc.text(label, L, y);
    doc.text(acct(value, opts.expense), R, y, { align: "right" });
    y += opts.big ? 28 : 22;
  };

  // Desglose por categoría de una sección (indentado, gris, más pequeño). Salto de página si hace falta.
  const catLines = (section: string, expense = false) => {
    const rows = is.breakdown?.[section] ?? [];
    for (const r of rows) {
      if (y > 720) { doc.addPage(); y = 64; }
      doc.setFont("helvetica", "normal").setFontSize(9).setTextColor(120);
      doc.text(r.category, L + 16, y);
      doc.text(acct(r.amount, expense), R, y, { align: "right" });
      y += 15;
    }
    if (rows.length) y += 4;
  };

  row("Revenue", is.revenue);
  catLines("REVENUE");
  row("Cost of Goods Sold (COGS)", is.cogs, { expense: true });
  catLines("COGS", true);
  row("Gross Profit", is.gross_profit, { strong: true });
  y += 6;
  row("Operating Expenses", is.operating_expenses, { expense: true });
  catLines("OPERATING_EXPENSE", true);
  row("Operating Income", is.operating_income, { strong: true });
  y += 6;
  row("Other Income", is.other_income);
  catLines("OTHER_INCOME");
  row("Other Expense", is.other_expense, { expense: true });
  catLines("OTHER_EXPENSE", true);
  // Lo PENDIENTE (FASE 12): si existe, el resultado NO es final y el documento lo dice EN la línea,
  // no en una nota al pie que nadie lee.
  const u = is.uncategorized;
  const pending = u?.count ?? 0;
  row(pending > 0 ? "Net Income (before pending items)" : "Net Income",
      is.net_income, { strong: true, big: true });

  // Bloque de lo Pendiente: fuera de la aritmética, en CAJA (entró/salió). Sin `account_type` no se
  // puede saber si suma o resta, así que no se presenta como resultado ni se reparte por sección.
  if (u && pending > 0) {
    if (y > 650) { doc.addPage(); y = 64; }
    y += 8;
    doc.setDrawColor(245, 158, 11).setLineWidth(1).line(L, y, R, y);
    doc.setLineWidth(0.2);
    y += 18;

    doc.setFont("helvetica", "bold").setFontSize(11).setTextColor(146, 64, 14);
    doc.text(`PENDING — NOT CLASSIFIED YET (${pending} movement${pending === 1 ? "" : "s"})`, L, y);
    y += 15;
    doc.setFont("helvetica", "normal").setFontSize(8.5).setTextColor(120);
    doc.text(
      "Not included in the result above. These movements have no P&L section yet, so there is no way to",
      L, y,
    );
    doc.text("tell whether they add to or subtract from it. Shown here as cash in and out.", L, y + 11);
    y += 28;

    const pendingRow = (label: string, n: number, amount: number, strong = false) => {
      doc.setFont("helvetica", strong ? "bold" : "normal").setFontSize(10).setTextColor(60);
      doc.text(n >= 0 ? `${label}  (${n})` : label, L + 16, y);
      doc.text(usd(amount), R, y, { align: "right" });
      y += strong ? 20 : 16;
    };
    pendingRow("Money in", u.count_in, u.money_in);
    pendingRow("Money out", u.count_out, u.money_out);
    if (u.count_unknown > 0) {
      pendingRow("Direction not reported by the bank", u.count_unknown, u.money_unknown);
    }
    doc.setDrawColor(220).line(L + 16, y - 11, R, y - 11);
    pendingRow("Net effect on cash", -1, u.net_cash, true);
  }

  // Pie
  doc.setFont("helvetica", "normal").setFontSize(8).setTextColor(150);
  doc.text(
    pending > 0
      ? "Cash basis. Figures computed from classified bank transactions. Pending movements are listed separately and are NOT included in the result. Generated by ContaBu."
      : "Cash basis. Figures computed from classified bank transactions. Generated by ContaBu.",
    L,
    pending > 0 ? 752 : 760,
    { maxWidth: R - L },
  );

  const tag = start || end ? `${start || "start"}_${end || "end"}` : "all-time";
  doc.save(`ContaBu_PnL_${tag}.pdf`);
}
