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
  payload: Partial<{ supermarket_threshold: number; gas_station_threshold: number }>,
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

export async function uploadCsv(file: File) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(
    `${API}/api/companies/${COMPANY_ID}/transactions/upload`,
    { method: "POST", body: form },
  );
  if (!res.ok) throw new Error("Falló la subida del archivo");
  return res.json();
}

// Formatea un número como dinero en USD.
export const money = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD" });
