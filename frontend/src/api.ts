// Capa de acceso al backend. Centraliza las llamadas HTTP en un solo lugar.
import type { Company, IncomeStatement, Transaction } from "./types";

const API = "http://localhost:8000";
// Sin autenticación todavía: empresa fija. Se reemplazará con auth.
const COMPANY_ID = 2;

export async function getCompany(): Promise<Company> {
  const res = await fetch(`${API}/api/companies/${COMPANY_ID}`);
  if (!res.ok) throw new Error("No se pudo cargar la empresa");
  return res.json();
}

export async function updateThresholds(
  payload: Partial<{
    supermarket_threshold: number;
    gas_station_threshold: number;
    settlement_buffer_days: number;
  }>,
): Promise<Company> {
  const res = await fetch(`${API}/api/companies/${COMPANY_ID}/thresholds`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("No se pudieron actualizar los umbrales");
  return res.json();
}

export async function getTransactions(): Promise<Transaction[]> {
  const res = await fetch(`${API}/api/companies/${COMPANY_ID}/transactions`);
  if (!res.ok) throw new Error("No se pudieron cargar las transacciones");
  return res.json();
}

// Plan de cuentas del sector: { account_type: [categorías] }.
export type ChartOfAccounts = Record<string, string[]>;
export async function getCategories(): Promise<ChartOfAccounts> {
  const res = await fetch(`${API}/api/companies/${COMPANY_ID}/categories`);
  if (!res.ok) throw new Error("No se pudieron cargar las categorías");
  return res.json();
}

export async function getIncomeStatement(): Promise<IncomeStatement> {
  const res = await fetch(`${API}/api/companies/${COMPANY_ID}/income-statement`);
  if (!res.ok) throw new Error("No se pudo cargar el estado de resultados");
  return res.json();
}

export async function getReviewTransactions(): Promise<Transaction[]> {
  const res = await fetch(`${API}/api/companies/${COMPANY_ID}/transactions/review`);
  if (!res.ok) throw new Error("No se pudieron cargar las revisiones");
  return res.json();
}

export async function updateTransaction(
  id: number,
  payload: Partial<{ account_type: string; category: string; subcategory: string; needs_review: boolean }>,
): Promise<Transaction> {
  const res = await fetch(`${API}/api/transactions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("No se pudo actualizar la transacción");
  return res.json();
}

export async function clarifyTransaction(id: number, answer: string): Promise<Transaction> {
  const res = await fetch(`${API}/api/transactions/${id}/clarify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
  if (!res.ok) throw new Error("No se pudo procesar la respuesta");
  return res.json();
}

export interface UploadResult {
  inserted: number;
  errors: number;
  skipped_duplicates: number; // filas omitidas por ya existir (re-subida / rango solapado)
  held_pending: number; // filas retenidas por pending / aún no asentadas
  account_detected: boolean; // false → hay que pedirle la cuenta al usuario
  transaction_ids: number[]; // transacciones creadas en esta subida
}

export async function uploadCsv(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(
    `${API}/api/companies/${COMPANY_ID}/transactions/upload`,
    { method: "POST", body: form },
  );
  if (!res.ok) throw new Error("Falló la subida del archivo");
  return res.json();
}

// Asigna la cuenta (que indicó el usuario) a las transacciones recién subidas.
// El backend la enmascara a los últimos 4 dígitos.
export async function setTransactionsAccount(
  transactionIds: number[],
  account: string,
): Promise<{ updated: number; bank_account: string }> {
  const res = await fetch(`${API}/api/companies/${COMPANY_ID}/transactions/set-account`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transaction_ids: transactionIds, account }),
  });
  if (!res.ok) throw new Error("No se pudo guardar la cuenta");
  return res.json();
}

// Formatea un número como dinero en USD.
export const money = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD" });
