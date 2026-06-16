// Capa de acceso al backend. Centraliza las llamadas HTTP en un solo lugar.
import type { IncomeStatement, Transaction } from "./types";

const API = "http://localhost:8000";
// Sin autenticación todavía: usamos la empresa demo (id 1). Se reemplazará con auth.
const COMPANY_ID = 1;

export async function getTransactions(): Promise<Transaction[]> {
  const res = await fetch(`${API}/api/companies/${COMPANY_ID}/transactions`);
  if (!res.ok) throw new Error("No se pudieron cargar las transacciones");
  return res.json();
}

export async function getIncomeStatement(): Promise<IncomeStatement> {
  const res = await fetch(`${API}/api/companies/${COMPANY_ID}/income-statement`);
  if (!res.ok) throw new Error("No se pudo cargar el estado de resultados");
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
