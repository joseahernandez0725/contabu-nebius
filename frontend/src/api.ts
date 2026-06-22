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
  roles?: Record<number, string>; // company_id → rol del usuario (OWNER/MEMBER/VIEWER)
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

// Multi-empresa: todas las empresas de la cuenta (con el rol del usuario). Fija la activa si no hay.
export async function getCompanies(): Promise<Company[]> {
  const res = await fetch(`${API}/api/me`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo iniciar sesión");
  const me: Me = await res.json();
  if (companyId == null) companyId = me.companies[0]?.id ?? null;
  const roles = me.roles || {};
  return me.companies.map((c) => ({ ...c, role: roles[c.id] as Company["role"] }));
}

// --- Equipo: roles y permisos por empresa ---
export interface Member {
  id: number | null; // null = el dueño (OWNER)
  email: string | null;
  role: "OWNER" | "MEMBER" | "VIEWER";
  is_owner: boolean;
  pending: boolean; // invitado que aún no inicia sesión
}

export async function getMembers(): Promise<Member[]> {
  const res = await fetch(`${API}/api/companies/${cid()}/members`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo cargar el equipo");
  return res.json();
}

export async function inviteMember(email: string, role: string): Promise<void> {
  const res = await fetch(`${API}/api/companies/${cid()}/members`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ email, role }),
  });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail || "No se pudo invitar");
  }
}

export async function updateMemberRole(id: number, role: string): Promise<void> {
  const res = await fetch(`${API}/api/companies/${cid()}/members/${id}`, {
    method: "PATCH",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
  if (!res.ok) throw new Error("No se pudo cambiar el rol");
}

export async function removeMember(id: number): Promise<void> {
  const res = await fetch(`${API}/api/companies/${cid()}/members/${id}`, {
    method: "DELETE",
    headers: await authHeaders(),
  });
  if (!res.ok) throw new Error("No se pudo quitar al miembro");
}

// Cambia la empresa ACTIVA (la que usan todas las llamadas vía cid()).
export function setActiveCompany(id: number) {
  companyId = id;
}

// Crea una empresa nueva bajo la misma cuenta (se crea sin sector → onboarding).
export async function createCompany(name?: string): Promise<Company> {
  const res = await fetch(`${API}/api/companies`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ name: name || null }),
  });
  if (!res.ok) throw new Error("No se pudo crear la empresa");
  return res.json();
}

export async function getCompany(): Promise<Company> {
  const res = await fetch(`${API}/api/companies/${cid()}`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo cargar la empresa");
  return res.json();
}

// Edita el perfil de la empresa (nombre/sector). Lo usa el onboarding y el renombrar.
export async function updateCompany(payload: { name?: string; sector?: string }): Promise<Company> {
  const res = await fetch(`${API}/api/companies/${cid()}`, {
    method: "PATCH",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("No se pudo actualizar la empresa");
  return res.json();
}

// Elimina una empresa y todos sus datos. El backend rechaza borrar la única.
export async function deleteCompany(id: number): Promise<void> {
  const res = await fetch(`${API}/api/companies/${id}`, {
    method: "DELETE",
    headers: await authHeaders(),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || "No se pudo eliminar la empresa");
  }
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

// --- Categorías personalizadas (P&L personalizable v1) ---
export interface CustomCategory {
  id: number;
  account_type: string;
  name: string;
}

export async function getCustomCategories(): Promise<CustomCategory[]> {
  const res = await fetch(`${API}/api/companies/${cid()}/custom-categories`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudieron cargar las categorías personalizadas");
  return res.json();
}

export async function addCustomCategory(account_type: string, name: string): Promise<CustomCategory> {
  const res = await fetch(`${API}/api/companies/${cid()}/custom-categories`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ account_type, name }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || "No se pudo agregar la categoría");
  }
  return res.json();
}

export async function deleteCustomCategory(id: number): Promise<void> {
  const res = await fetch(`${API}/api/companies/${cid()}/custom-categories/${id}`, {
    method: "DELETE",
    headers: await authHeaders(),
  });
  if (!res.ok) throw new Error("No se pudo eliminar la categoría");
}

// --- Tabla de reglas editable (comercio → categoría) ---
export interface Rule {
  id: number;
  match_text: string;
  account_type: string | null;
  category: string;
  matches: number; // cuántas transacciones afecta
}

export async function getRules(): Promise<Rule[]> {
  const res = await fetch(`${API}/api/companies/${cid()}/rules`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudieron cargar las reglas");
  return res.json();
}

export async function updateRule(
  id: number,
  payload: { account_type: string; category: string },
): Promise<{ applied: number }> {
  const res = await fetch(`${API}/api/companies/${cid()}/rules/${id}`, {
    method: "PATCH",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("No se pudo actualizar la regla");
  return res.json();
}

export async function deleteRule(id: number): Promise<void> {
  const res = await fetch(`${API}/api/companies/${cid()}/rules/${id}`, {
    method: "DELETE",
    headers: await authHeaders(),
  });
  if (!res.ok) throw new Error("No se pudo eliminar la regla");
}

// Higiene: borra las reglas que no matchean ninguna transacción (afecta 0). Devuelve cuántas borró.
export async function cleanupRules(): Promise<{ removed: number }> {
  const res = await fetch(`${API}/api/companies/${cid()}/rules/cleanup`, {
    method: "POST",
    headers: await authHeaders(),
  });
  if (!res.ok) throw new Error("No se pudieron limpiar las reglas");
  return res.json();
}

// --- P&L como proyectos guardados (draft = recalcula · closed = congelado) ---
export interface PnlReportMeta {
  id: number;
  name: string;
  start: string | null;
  end: string | null;
  status: "draft" | "closed";
  closed_at?: string | null;
  net_income?: number;
}
export interface PnlReportDetail extends PnlReportMeta {
  pnl: IncomeStatement;
}

const RBASE = () => `${API}/api/companies/${cid()}/pnl-reports`;

export async function listPnlReports(): Promise<PnlReportMeta[]> {
  const res = await fetch(RBASE(), { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudieron cargar los reportes");
  return res.json();
}

export async function createPnlReport(name: string, start?: string, end?: string): Promise<PnlReportMeta> {
  const res = await fetch(RBASE(), {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ name, start: start || null, end: end || null }),
  });
  if (!res.ok) throw new Error("No se pudo crear el reporte");
  return res.json();
}

export async function getPnlReport(id: number): Promise<PnlReportDetail> {
  const res = await fetch(`${RBASE()}/${id}`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo abrir el reporte");
  return res.json();
}

export async function closePnlReport(id: number): Promise<PnlReportMeta> {
  const res = await fetch(`${RBASE()}/${id}/close`, { method: "POST", headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo cerrar el reporte");
  return res.json();
}

export async function reopenPnlReport(id: number): Promise<PnlReportMeta> {
  const res = await fetch(`${RBASE()}/${id}/reopen`, { method: "POST", headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo reabrir el reporte");
  return res.json();
}

export async function deletePnlReport(id: number): Promise<void> {
  const res = await fetch(`${RBASE()}/${id}`, { method: "DELETE", headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo eliminar el reporte");
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
