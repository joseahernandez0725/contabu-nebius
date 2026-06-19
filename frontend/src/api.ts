// Capa de acceso al backend. Centraliza las llamadas HTTP en un solo lugar.
import { auth } from "./firebase";
import type { Company, IncomeStatement, Transaction } from "./types";

// URL del backend según entorno: en dev, localhost; en prod, el Cloud Run (VITE_API_URL).
const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// --- Identidad / sesión (FASE 7) ---------------------------------------------------------------
// Cada petición lleva el ID token de Firebase del usuario actual. El backend lo verifica
// (firebase-admin) y de ahí saca la identidad — nunca de la URL. El `company_id` sale de /api/me.
async function authHeaders(): Promise<Record<string, string>> {
  const u = auth.currentUser;
  if (!u) throw new Error("No autenticado");
  return { Authorization: `Bearer ${await u.getIdToken()}` };
}

let companyId: number | null = null;
function cid(): number {
  if (companyId == null) throw new Error("Sesión no iniciada (llama a getMe primero)");
  return companyId;
}

interface Me {
  user: { id: number; firebase_uid: string; email: string | null };
  companies: Company[];
}

// Inicia la sesión: trae el usuario y sus empresas; fija la empresa activa. Devuelve la empresa.
export async function getMe(): Promise<Company> {
  const res = await fetch(`${API}/api/me`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo iniciar sesión");
  const me: Me = await res.json();
  companyId = me.companies[0]?.id ?? null;
  if (companyId == null) throw new Error("El usuario no tiene empresa");
  return me.companies[0];
}

export async function getCompany(): Promise<Company> {
  const res = await fetch(`${API}/api/companies/${cid()}`, { headers: await authHeaders() });
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
  const res = await fetch(`${API}/api/companies/${cid()}/thresholds`, {
    method: "PATCH",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("No se pudieron actualizar los umbrales");
  return res.json();
}

export async function getTransactions(): Promise<Transaction[]> {
  const res = await fetch(`${API}/api/companies/${cid()}/transactions`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudieron cargar las transacciones");
  return res.json();
}

// Plan de cuentas del sector: { account_type: [categorías] }.
export type ChartOfAccounts = Record<string, string[]>;
export async function getCategories(): Promise<ChartOfAccounts> {
  const res = await fetch(`${API}/api/companies/${cid()}/categories`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudieron cargar las categorías");
  return res.json();
}

// P&L, opcionalmente acotado por rango de fechas (YYYY-MM-DD).
export async function getIncomeStatement(start?: string, end?: string): Promise<IncomeStatement> {
  const q = new URLSearchParams();
  if (start) q.set("start", start);
  if (end) q.set("end", end);
  const qs = q.toString() ? `?${q.toString()}` : "";
  const res = await fetch(`${API}/api/companies/${cid()}/income-statement${qs}`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo cargar el estado de resultados");
  return res.json();
}

export async function getReviewTransactions(): Promise<Transaction[]> {
  const res = await fetch(`${API}/api/companies/${cid()}/transactions/review`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudieron cargar las revisiones");
  return res.json();
}

export async function updateTransaction(
  id: number,
  payload: Partial<{ account_type: string; category: string; subcategory: string; needs_review: boolean }>,
): Promise<Transaction> {
  const res = await fetch(`${API}/api/transactions/${id}`, {
    method: "PATCH",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("No se pudo actualizar la transacción");
  return res.json();
}

export async function clarifyTransaction(id: number, answer: string): Promise<Transaction> {
  const res = await fetch(`${API}/api/transactions/${id}/clarify`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
  if (!res.ok) throw new Error("No se pudo procesar la respuesta");
  return res.json();
}

// Una explicación que aplica a VARIAS transacciones de la misma contraparte (renta mensual, etc.).
export async function clarifyGroup(transactionIds: number[], answer: string): Promise<{ updated: number }> {
  const res = await fetch(`${API}/api/transactions/clarify-group`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ transaction_ids: transactionIds, answer }),
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
    `${API}/api/companies/${cid()}/transactions/upload`,
    { method: "POST", headers: await authHeaders(), body: form },
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
  const res = await fetch(`${API}/api/companies/${cid()}/transactions/set-account`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ transaction_ids: transactionIds, account }),
  });
  if (!res.ok) throw new Error("No se pudo guardar la cuenta");
  return res.json();
}

// Formatea un número como dinero en USD.
export const money = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD" });
