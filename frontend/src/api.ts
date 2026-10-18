// Capa de acceso al backend. Centraliza las llamadas HTTP en un solo lugar.
import { auth } from "./firebase";
import type { Company, IncomeStatement, Transaction, SheetRow, SheetLabel } from "./types";

// URL del backend según entorno: en dev, localhost; en prod, el Cloud Run (VITE_API_URL).
const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// --- Identidad / sesión (FASE 7) ---------------------------------------------------------------
// Cada petición lleva el ID token de Firebase del usuario actual. El backend lo verifica
// (firebase-admin) y de ahí saca la identidad — nunca de la URL. El `company_id` sale de /api/me.
// Costura de DESARROLLO, espejo de `AUTH_DEV_MODE` en backend/auth.py: con `VITE_DEV_UID` en un
// `.env.local`, se manda ese uid en `X-Dev-Uid` en vez del token de Firebase. Permite trabajar en la
// UI sin login real. Doble candado: solo existe si la variable está puesta Y solo en `import.meta.env.DEV`
// (Vite hornea las variables al compilar, así que un build de producción NUNCA puede activarlo).
const DEV_UID = import.meta.env.DEV ? (import.meta.env.VITE_DEV_UID as string | undefined) : undefined;

async function authHeaders(): Promise<Record<string, string>> {
  if (DEV_UID) return { "X-Dev-Uid": DEV_UID };
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
// `trade`: a qué se dedica el negocio cuando el sector es "other" (texto libre; "" lo borra).
export async function updateCompany(payload: { name?: string; sector?: string; trade?: string }): Promise<Company> {
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

export async function getTransactions(): Promise<Transaction[]> {
  const res = await fetch(`${API}/api/companies/${cid()}/transactions`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudieron cargar las transacciones");
  return res.json();
}

/** Transacciones + sus etiquetas y su formato de celda (la hoja, FASE 10). Sin el auditor desde el
 *  13-oct: la marca de revisión de cada fila sale de `GET /transactions/flags`. */
export async function getSheetRows(): Promise<SheetRow[]> {
  const res = await fetch(`${API}/api/companies/${cid()}/transactions?for_sheet=true`, {
    headers: await authHeaders(),
  });
  if (!res.ok) throw new Error("No se pudieron cargar las transacciones");
  return res.json();
}

/** VOLVER A SEGUIR LA REGLA: el inverso de escribir a mano. La celda deja de ser un valor
 *  escrito y vuelve a ser "fórmula" — busca la regla que cubre esta descripción y la aplica. */
export async function followRule(txId: number): Promise<{
  id: number; followed_rule?: { id: number; match_text: string; category: string };
}> {
  const res = await fetch(`${API}/api/transactions/${txId}`, {
    method: "PATCH",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ rule_action: "follow_rule" }),
  });
  if (res.status === 404) throw new Error("no-rule");
  if (!res.ok) throw new Error("No se pudo volver a seguir la regla");
  return res.json();
}

// ── EL PIVOTE (FASE 10 paso 7): categoría → subcategoría → mes → transacciones ────────────────

export interface PivotNode {
  key: string;
  level: "category" | "subcategory" | "month";
  section?: string;
  label: string;
  amount: string;   // Decimal como string
  count: number;
  children: PivotNode[] | null;   // null en el nivel mes: las hojas se piden aparte
}

export interface PivotLeaf {
  id: number; date: string; description: string;
  amount: string; is_inflow: boolean | null; subcategory: string | null;
}

export async function getPivot(accounts: string[] = []): Promise<PivotNode[]> {
  // Varias cuentas: ?account=A&account=B (el filtro con casillas de la hoja).
  const qs = accounts.length ? "?" + new URLSearchParams(accounts.map((a) => ["account", a])).toString() : "";
  const res = await fetch(`${API}/api/companies/${cid()}/pivot${qs}`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo cargar el pivote");
  return res.json();
}

/** El último `+`: solo se pide cuando el bookkeeper abre ese mes. */
export async function getPivotLeaves(
  section: string, category: string, subcategory: string, month: string, accounts: string[] = [],
): Promise<PivotLeaf[]> {
  const qs = new URLSearchParams({ section, category, subcategory, month });
  for (const a of accounts) qs.append("account", a);
  const res = await fetch(`${API}/api/companies/${cid()}/pivot/leaves?${qs}`, {
    headers: await authHeaders(),
  });
  if (!res.ok) throw new Error("No se pudieron cargar las transacciones");
  return res.json();
}

// ── LA HOJA: etiquetas (dato) y formato de celda (presentación) ────────────────────────────────

export async function getLabels(): Promise<SheetLabel[]> {
  const res = await fetch(`${API}/api/companies/${cid()}/labels`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudieron cargar las etiquetas");
  return res.json();
}

export async function createLabel(name: string, color: string): Promise<SheetLabel> {
  const res = await fetch(`${API}/api/companies/${cid()}/labels`, {
    method: "POST", headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ name, color }),
  });
  if (!res.ok) throw new Error("No se pudo crear la etiqueta");
  return res.json();
}

export async function deleteLabel(id: number): Promise<void> {
  const res = await fetch(`${API}/api/labels/${id}`, { method: "DELETE", headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo borrar la etiqueta");
}

/** PUT = estado completo: marcar y desmarcar son la misma operación. */
export async function setTransactionLabels(txId: number, label_ids: number[]): Promise<void> {
  const res = await fetch(`${API}/api/transactions/${txId}/labels`, {
    method: "PUT", headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ label_ids }),
  });
  if (!res.ok) throw new Error("No se pudieron guardar las etiquetas");
}

/** Aplica formato a una o muchas celdas de una vez (un rango seleccionado). */
export async function applyCellFormat(payload: {
  transaction_ids: number[]; column?: string | null;
  bold?: boolean; italic?: boolean; bg?: string | null; fg?: string | null;
  font_size?: number | null; font_family?: string | null; clear?: boolean;
}): Promise<{ changed: number }> {
  const res = await fetch(`${API}/api/companies/${cid()}/cell-formats`, {
    method: "POST", headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("No se pudo aplicar el formato");
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
  subcategory: string | null; // comercio presentable que asigna la regla (null → el match_text)
  priority: number | null;    // prioridad manual (1 = primero); null → orden automático
  matches: number; // cuántas transacciones afecta
  scope: "COMPANY" | "SECTOR";  // de ESTE libro, o compartida por todo un sector
  sector: string | null;        // el sector, cuando scope === "SECTOR"
  direction?: "in" | "out" | "mixed" | null;  // hacia dónde va el dinero de lo que caza
  style?: RuleStyle | null;     // resaltado de su fila en la hoja de Reglas
  // Ventana de fechas (4-oct): la regla solo vale entre estos días, INCLUSIVOS ("YYYY-MM-DD"; null = sin límite).
  valid_from?: string | null;
  valid_to?: string | null;
  // Condición de monto (8-oct): la MAGNITUD del movimiento entre estos dos, inclusivos (null = sin límite).
  amount_min?: number | null;
  amount_max?: number | null;
  // Transparencia del orden (4-oct): en qué lugar corre de verdad, en cuántas filas gana, y con qué
  // otras reglas caza los mismos movimientos (a quién le gana y contra quién pierde, con cuántas filas).
  order?: number | null;
  origin?: "agent" | "import" | null;  // de dónde salió (8-oct): la propuso el agente, o vino de un archivo
  wins?: number;
  lost_to?: RuleClash[];
  beats?: RuleClash[];
}
export interface RuleClash { id: number; match_text: string; rows: number }
export interface RuleStyle { bg: string | null; bold: boolean; italic: boolean }

// Vista previa de una regla ANTES de crearla (la ventana "Crear regla"). Sin keyword y con la fila,
// el backend sugiere una palabra clave limpia (sin fechas, IDs ni números de rastreo).
export interface RulePreview {
  keyword: string;
  matches: number; pending: number; manual: number; inflow: number; outflow: number;
  examples: { id: number; date: string; description: string; amount: number; is_inflow: boolean;
              category: string | null; source: string | null }[];
  duplicate: { id: number; match_text: string; category: string } | null;
  seed_matched: boolean | null;
}
export async function previewRule(payload: { keyword?: string; transaction_id?: number }): Promise<RulePreview> {
  const res = await fetch(`${API}/api/companies/${cid()}/rules/preview`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("preview_failed");
  return res.json();
}

export async function getRules(): Promise<Rule[]> {
  const res = await fetch(`${API}/api/companies/${cid()}/rules`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudieron cargar las reglas");
  return res.json();
}

// Regla creada A MANO desde la tabla (el reglamento también se escribe directo — hallazgo 8.10).
export async function createRule(payload: {
  match_text: string; account_type: string; category: string; subcategory?: string;
  valid_from?: string | null; valid_to?: string | null; priority?: number | null;
  amount_min?: number | null; amount_max?: number | null;
}): Promise<{ id: number; applied: number }> {
  const res = await fetch(`${API}/api/companies/${cid()}/rules`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.status === 409) throw new Error("dup");
  if (!res.ok) throw new Error("No se pudo crear la regla");
  return res.json();
}

export async function updateRule(
  id: number,
  payload: { match_text?: string; account_type?: string; category?: string; subcategory?: string; priority?: number | null;
             style?: RuleStyle | null; valid_from?: string | null; valid_to?: string | null;
             amount_min?: number | null; amount_max?: number | null },
): Promise<{ applied: number; reassigned: number; released?: number }> {
  const res = await fetch(`${API}/api/companies/${cid()}/rules/${id}`, {
    method: "PATCH",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.status === 409) throw new Error("dup");
  if (!res.ok) throw new Error("No se pudo actualizar la regla");
  return res.json();
}

// `revert`: DESHACER la creación de una regla — devuelve las transacciones que movió a como estaban.
// Borrar una regla devuelve sus movimientos al MOTOR (Jose, 4-oct): otra regla → transferencia →
// Pendiente. Lo escrito a mano no se toca.
export interface RuleDeleteResult { deleted: number; reverted: number; to_rule: number; to_transfer: number; to_pending: number }
export async function deleteRule(id: number, revert = false): Promise<RuleDeleteResult> {
  const res = await fetch(`${API}/api/companies/${cid()}/rules/${id}${revert ? "?revert=true" : ""}`, {
    method: "DELETE",
    headers: await authHeaders(),
  });
  if (!res.ok) throw new Error("No se pudo eliminar la regla");
  return res.json();
}

/** ANTES de cambiar la palabra clave (14-oct): qué filas conserva, cuáles suelta (y a dónde van) y cuáles
 *  toma (y de dónde vienen). Lo escrito a mano no se toca: `manual` = las que la palabra nueva cazaría. */
export interface RuleEditPreview {
  id: number; before: string; match_text: string;
  keep: number; release: number; to_transfer: number; to_pending: number;
  to_rules: { id: number; match_text: string; category: string; rows: number }[];
  take: number; from_pending: number; from_transfer: number; from_other: number;
  from_rules: { id: number; match_text: string; category: string; rows: number }[];
  manual: number; release_examples: string[]; take_examples: string[];
  duplicate: { id: number; match_text: string; category: string } | null;
}
export async function previewRuleEdit(id: number, match_text: string): Promise<RuleEditPreview> {
  const res = await fetch(`${API}/api/companies/${cid()}/rules/${id}/edit-preview`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ match_text }),
  });
  if (!res.ok) throw new Error("No se pudo revisar la regla");
  return res.json();
}

/** ANTES de borrar: cuántos movimientos decide la regla y a dónde irían. No toca nada. */
export interface RuleDeletePreview {
  id: number; match_text: string; rows: number;
  to_rules: { id: number; match_text: string; category: string; rows: number }[];
  to_transfer: number; to_pending: number;
}
export async function previewRuleDelete(id: number): Promise<RuleDeletePreview> {
  const res = await fetch(`${API}/api/companies/${cid()}/rules/${id}/delete-preview`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo revisar la regla");
  return res.json();
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

// --- Pagos (Stripe Checkout, modelo M5 por volumen) ---
// Crea la sesión de pago para un tier (cupo de tx/mes) y devuelve la URL de Stripe Checkout.
export async function createCheckout(quota: number): Promise<{ url: string }> {
  const res = await fetch(`${API}/api/companies/${cid()}/checkout`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ quota }),
  });
  if (!res.ok) throw new Error("No se pudo iniciar el pago");
  return res.json();
}

// Tras el redirect de éxito de Stripe: confirma el pago y deja la empresa suscrita.
export async function confirmCheckout(
  session_id: string,
): Promise<{ ok: boolean; status?: string; tx_quota?: number }> {
  const res = await fetch(`${API}/api/companies/${cid()}/checkout/confirm`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ session_id }),
  });
  if (!res.ok) throw new Error("No se pudo confirmar el pago");
  return res.json();
}

// --- Agente auditor: escanea categorizaciones sospechosas (1 click, determinista) ---
export interface AuditFinding {
  type: string;                 // inconsistent_merchant | sign_mismatch | uncategorized
  severity: string;             // high | medium | low
  transaction_ids: number[];
  detail?: string | string[];
  merchant?: string;
  suggestion?: string;
  suggested_account_type?: string; // inconsistencia: categoría dominante (para el fix de 1 toque)
  suggested_category?: string;
  odd_ids?: number[];           // inconsistencia: las tx que difieren de la mayoría
  examples?: { id: number; description: string; is_inflow: boolean }[]; // tx señaladas + dirección
}

export async function getAudit(): Promise<AuditFinding[]> {
  const res = await fetch(`${API}/api/companies/${cid()}/audit`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("No se pudo auditar la categorización");
  return res.json();
}

// (El viejo proponedor — `generateRuleProposals`, `validateRuleProposal` — se borró el 13-oct, FASE 11:
// lo reemplaza el agente ④ en Auditoría, `runFormulaDrafter`.)

// --- Importar el banco de reglas del usuario (sube su Excel/CSV; la IA mapea las columnas) ---
export interface RuleImportRow {
  keyword: string;
  account_type: string;       // "" si no se pudo resolver la sección
  category: string;
  subcategory: string;        // "" si el archivo no trae esa columna
  priority: number | null;    // columna "Priority" de la exportación (null si no viene)
  status: string;             // ok | exists | duplicate | needs_section | bad_date
  valid_from?: string | null; // columnas "From"/"To" de la exportación (ventana de fechas)
  valid_to?: string | null;
  amount_min?: number | null; // columnas "Min amount"/"Max amount" (condición de monto)
  amount_max?: number | null;
}

export async function importRules(
  file: File,
): Promise<{ mapping: Record<string, string>; rules: RuleImportRow[] }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API}/api/companies/${cid()}/rules/import`, {
    method: "POST",
    headers: await authHeaders(), // sin Content-Type: el navegador pone el boundary de multipart
    body: form,
  });
  if (!res.ok) throw new Error("No se pudo importar el archivo de reglas");
  return res.json();
}

// Crea las reglas APROBADAS por el dueño y las aplica a lo existente.
export async function applyRuleProposals(
  rules: { keyword: string; account_type: string; category: string; subcategory?: string;
           priority?: number | null; valid_from?: string | null; valid_to?: string | null;
           origin?: "agent" | "import"; amount_min?: number | null; amount_max?: number | null }[],
): Promise<{ created: number; applied: number }> {
  const res = await fetch(`${API}/api/companies/${cid()}/rule-proposals/apply`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ rules }),
  });
  if (!res.ok) throw new Error("No se pudieron aplicar las reglas");
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
  // Movimientos Pendientes del período (FASE 12). `null`/ausente = no se sabe: un reporte cerrado
  // antes de la FASE 12 congeló un snapshot sin esa cuenta. 0 = no hay Pendiente.
  pending_count?: number | null;
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
  payload: Partial<{
    account_type: string;
    category: string;
    subcategory: string;
    note: string;
    description_extra: string;
    needs_review: boolean;
    // Intención sobre el REGLAMENTO al corregir (FASE 9 Paso 2):
    // "only_this" = excepción (no toca reglas) · "update_rule" = edita la regla que la cubría ·
    // sin enviar = comportamiento clásico (aprende regla por frase específica).
    rule_action: "only_this" | "update_rule";
  }>,
): Promise<{ id: number; applied: number; rule_updated: boolean; rule_skipped_generic?: boolean }> {
  const res = await fetch(`${API}/api/transactions/${id}`, {
    method: "PATCH",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("No se pudo actualizar la transacción");
  return res.json(); // { id, applied, rule_updated } — applied = otras tx que alineó la regla
}

// DESHACER (Ctrl+Z / botón): devuelve la transacción a como estaba antes de su último cambio de
// categoría, con su procedencia exacta. 409 = alguien la cambió después (no se pisa).
export async function undoTransactionChange(id: number): Promise<void> {
  const res = await fetch(`${API}/api/transactions/${id}/undo`, { method: "POST", headers: await authHeaders() });
  if (res.status === 409) throw new Error("changed_since");
  if (!res.ok) throw new Error("undo_failed");
}

// El banco vinculado retiró este movimiento: el bookkeeper decide si se queda o se elimina (4-oct).
export async function decideBankRemoved(id: number, action: "keep" | "delete"): Promise<void> {
  const res = await fetch(`${API}/api/companies/${cid()}/transactions/${id}/bank-removed`, {
    method: "POST", headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  if (!res.ok) throw new Error("bank_removed_failed");
}

// Una explicación que aplica a VARIAS transacciones de la misma contraparte (renta mensual, etc.).
// Ya no categoriza con IA (1-oct): la respuesta queda como NOTA de cada línea y la pregunta se cierra.
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
  total?: number; // filas del archivo (subida por lotes)
  processed?: number; // filas cubiertas por este lote
  audit_findings?: number; // hallazgos del AUDITOR automático (solo en el último lote — FASE 9)
}

// Sube un lote del archivo: rows[offset : offset+chunk]. chunk=0 → archivo completo (1 sola llamada).
export async function uploadCsv(file: File, offset = 0, chunk = 0): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  if (chunk > 0) {
    form.append("offset", String(offset));
    form.append("chunk", String(chunk));
  }
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

// ==================================================================================================
// FASE 13 — DÍAS DE ESPERA DEL LIBRO (área Firm / bookkeepers). 🔄 1-oct, decisión de Jose: fuera los
// ajustes por monto; los días de espera se quedan como un ajuste simple del libro.
// Errores: llega un CÓDIGO (`wait_days_range`) que la pantalla traduce con `settings.recent.err.*`.
// ==================================================================================================
export async function updateSettlement(waitDays: number): Promise<Company> {
  const res = await fetch(`${API}/api/companies/${cid()}/settlement`, {
    method: "PATCH",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ wait_days: waitDays }),
  });
  if (!res.ok) {
    const detail = await res.json().then((j) => j?.detail).catch(() => null);
    throw new Error(typeof detail === "string" ? detail : "error");
  }
  return res.json();
}

// ==================================================================================================
// FASE 10 — BANCOS VINCULADOS (área Movimientos, 3-oct). Proveedor: Plaid. El access_token NUNCA
// llega aquí: el navegador solo ve link_tokens de un solo uso y el public_token de Link, que el
// backend canjea. Errores: un CÓDIGO estable (`bank_not_configured`, `bank_disconnected`, `bank_error`)
// que la pantalla traduce con `bank.err.*`.
// ==================================================================================================
export interface BankAccount { id: string; name: string | null; mask: string | null; type: string | null; subtype: string | null }
export interface BankConnection {
  id: number;
  environment: string;
  institution_name: string | null;
  accounts: BankAccount[];
  status: "active" | "disconnected";
  last_error: string | null;
  last_synced_at: string | null;
}
export interface BankSync {
  accounts: number; fetched: number; inserted: number; skipped_duplicates: number;
  held_pending: number; by_rule: number; by_transfer: number; pending: number; not_ready: boolean;
  // Avisos del banco y solape con CSV (4-oct): lo que el banco cambió o retiró, y lo que ya cubría un CSV.
  skipped_before_csv?: number; modified_applied?: number; modified_recategorized?: number;
  removed_marked?: number; removed_ignored?: number;
}
export interface BankConfig { enabled: boolean; provider: string; environment: string }

export class BankApiError extends Error {
  code: string;
  reason?: string;
  constructor(code: string, reason?: string) { super(code); this.code = code; this.reason = reason; }
}

async function bankFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}/api/companies/${cid()}/bank-connections${path}`, {
    ...init,
    headers: { ...(await authHeaders()), "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    const detail = await res.json().then((j) => j?.detail).catch(() => null);
    throw new BankApiError(detail?.code ?? "bank_error", detail?.reason);
  }
  return res.json();
}

export const getBankConnections = () =>
  bankFetch<{ config: BankConfig; connections: BankConnection[] }>("");

export const createBankLinkToken = (language: string, connectionId?: number) =>
  bankFetch<{ link_token: string }>("/link-token", {
    method: "POST", body: JSON.stringify({ language, connection_id: connectionId ?? null }),
  });

/** Lo que Plaid Link devuelve en `metadata` y reconoce un banco ya vinculado (14-oct). */
export interface BankLinkMeta { institution_id: string | null; institution_name: string | null; account_masks: string[] }
export interface BankDuplicate {
  connection_id: number; company_id: number; company_name: string | null; institution_name: string | null; same_book: boolean;
}
/** ANTES de canjear el public_token: ¿ya está vinculado (en este libro u otro de la oficina)? No toca nada. */
export const checkBankConnection = (meta: BankLinkMeta) =>
  bankFetch<{ duplicate: BankDuplicate | null }>("/check", { method: "POST", body: JSON.stringify(meta) });

export const createBankConnection = (publicToken: string, meta: BankLinkMeta) =>
  bankFetch<{ connection: BankConnection; sync: BankSync }>("", {
    method: "POST", body: JSON.stringify({ public_token: publicToken, ...meta }),
  });

export const syncBankConnection = (id: number) =>
  bankFetch<{ connection: BankConnection; sync: BankSync }>(`/${id}/sync`, { method: "POST" });

export const deleteBankConnection = (id: number) =>
  bankFetch<{ deleted: number; revoked: boolean }>(`/${id}`, { method: "DELETE" });

// --- NOTAS DEL LIBRO (FASE 11, Auditoría / IA · 4-oct-2026) ------------------------------------
// Lo que el bookkeeper le escribe al agente sobre este cliente. Una nota nunca categoriza: es la
// entrada del agente ④, que solo PROPONE filas de la fórmula del libro.
export type BookNoteKind = "keep_in_mind" | "dont_report";
export type BookNotePriority = "high" | "normal";

export interface BookNote {
  id: number;
  kind: BookNoteKind;
  priority: BookNotePriority;
  text: string;
  merchant: string | null;
  valid_from: string | null; // ISO yyyy-mm-dd
  valid_to: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export type BookNoteInput = Partial<Pick<BookNote, "kind" | "priority" | "text" | "merchant" | "valid_from" | "valid_to">>;

/** Error de validación del backend: `code` es estable (en inglés) y se traduce con `notes.error.<code>`. */
export class BookNoteError extends Error {
  code: string;
  constructor(code: string) { super(code); this.code = code; }
}

async function notesFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}/api/companies/${cid()}/notes${path}`, {
    ...init,
    headers: { ...(await authHeaders()), "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    const detail = await res.json().then((j) => j?.detail).catch(() => null);
    throw new BookNoteError(detail?.code ?? (res.status === 403 ? "forbidden" : "notes_error"));
  }
  return res.json();
}

export const getBookNotes = () => notesFetch<BookNote[]>("");

export const createBookNote = (note: BookNoteInput) =>
  notesFetch<BookNote>("", { method: "POST", body: JSON.stringify(note) });

export const updateBookNote = (id: number, note: BookNoteInput) =>
  notesFetch<BookNote>(`/${id}`, { method: "PATCH", body: JSON.stringify(note) });

export const deleteBookNote = (id: number) =>
  notesFetch<{ deleted: number }>(`/${id}`, { method: "DELETE" });

// --- LOS AGENTES DE LA FÓRMULA (FASE 11, Auditoría / IA · 7-oct-2026) — Nemotron sobre Nebius ----
// ④ propone filas de la fórmula (se aprueban con `createRule`), ① revisa los hallazgos del auditor.
// Cada llamada se mide (tokens, costo) y cuenta contra el tope diario de la oficina.
export interface AgentUsage {
  model: string; reasoning: boolean; prompt_tokens: number; completion_tokens: number;
  reasoning_tokens_est: number; cost_usd: number | null; latency_ms: number; finish_reason: string | null;
}

export interface FormulaProposal {
  keyword: string; valid_from: string | null; valid_to: string | null; priority: number | null;
  account_type: string; category: string; subcategory: string | null; explanation: string;
  from_notes: number[]; from_changes: number[]; matches: number;
  /** Evidencia del ② verificador (Tavily), adjuntada por el código: lo que de verdad se encontró. */
  web_evidence: WebEvidence[];
  /** Avisos calculados por el código (no rechazan la fila): p. ej. "web_says_personal". */
  warnings: string[];
  amount_min: number | null; amount_max: number | null;
  notes_used: { id: number; text: string }[];
  corrections_used: number;
  test: {
    changes: number; changed_ids: number[]; by_pair: { from: string; to: string; rows: number }[];
    protected_manual: number; agrees_with_corrections: number; disagrees_with_corrections: number;
    realigned: number;
    changed: (TxSummary & { from: string; to: string })[];
  };
}

export interface WebEvidence {
  query: string; business_type: string; confidence: "high" | "medium" | "low" | null;
  source_url: string | null; source_domain: string | null;
}

export interface MerchantCheck {
  merchant: string; rows: number; status: string; business_type: string | null;
  confidence: "high" | "medium" | "low" | null; source_url: string | null; source_domain: string | null;
}

/** Lo que costó UNA acción (14-oct): llamadas a Nemotron, dólares, búsquedas y créditos de Tavily. */
export interface AgentSpent { calls: number; cost_usd: number; searches: number; credits: number }

/** Lo que costaría, ANTES de correrla (14-oct). Cada búsqueda: 1 crédito, o 3 si se repite a fondo. */
export interface AgentEstimate {
  action: string; calls: number; searches: number; credits_min: number; credits_max: number; cost_usd_max: number;
}

export interface VerifyResponse {
  outcome: string; items: MerchantCheck[]; searched: number; from_cache: number;
  skipped: Record<string, number>; credits: number;
  rejudge: { rejudged: number; outcome: string } | null; spent?: AgentSpent;
}

export interface SearchMerchantResponse {
  outcome: string; item: (MerchantCheck & { search_depth: string | null; credits: number }) | null;
  skipped: string | null; searched: number; from_cache: number;
  rejudge: { rejudged: number; outcome: string } | null; spent?: AgentSpent;
}

export interface DraftResponse {
  outcome: string; calls: number; proposals: FormulaProposal[];
  rejected: { keyword: string | null; errors: string[] }[]; usage: AgentUsage | null; spent?: AgentSpent;
}

export interface AgentReview {
  ref: string; type: string; merchant: string; transaction_ids: number[];
  verdict: "problem" | "legitimate" | "unsure"; observation: string; answered: boolean;
}

export interface ReviewResponse {
  outcome: string; silenced: number; reviews: AgentReview[]; usage: AgentUsage | null; spent?: AgentSpent;
}

export interface AgentUsageSummary {
  by_agent: Record<string, { calls: number; ok: number; prompt_tokens: number; completion_tokens: number;
    reasoning_tokens_est: number; cost_usd: number }>;
  total_cost_usd: number; calls_today: number; daily_cap: number; left_today: number;
  searches_today: number; daily_search_cap: number; searches_left_today: number;
}

/** Error de un agente: `code` estable (en inglés), se traduce con `agents.error.<code>`. */
export class AgentError extends Error {
  code: string;
  constructor(code: string) { super(code); this.code = code; }
}

async function agentsFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}/api/companies/${cid()}/agents${path}`, {
    ...init,
    headers: { ...(await authHeaders()), "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    const detail = await res.json().then((j) => j?.detail).catch(() => null);
    throw new AgentError(detail?.code ?? (res.status === 403 ? "forbidden" : "agent_error"));
  }
  return res.json();
}

/** ④ — `noteId` (14-oct): solo las filas de ESA nota, recién guardada. */
export const runFormulaDrafter = (lang: string, focusTxIds?: number[], noteId?: number) =>
  agentsFetch<DraftResponse>("/draft", {
    method: "POST", body: JSON.stringify({ lang, focus_tx_ids: focusTxIds ?? null, note_id: noteId ?? null }),
  });

export const runReviewer = (lang: string) =>
  agentsFetch<ReviewResponse>("/review", { method: "POST", body: JSON.stringify({ lang }) });

/** "Auditar" (14-oct): auditor + notas + "¿personal?" + ①. Sin redactar. */
export const runAudit = (lang: string) =>
  agentsFetch<ReviewResponse>("/audit", { method: "POST", body: JSON.stringify({ lang }) });

/** ② — solo los comercios que la auditoría marcó; el ① vuelve a juzgar los que traen evidencia nueva. */
export const runMerchantVerifier = (lang = "es") =>
  agentsFetch<VerifyResponse>("/verify", { method: "POST", body: JSON.stringify({ lang }) });

/** "Buscar este comercio" (14-oct): el de UN movimiento, al instante; memoria de la oficina primero. */
export const searchMerchant = (transactionId: number, lang: string) =>
  agentsFetch<SearchMerchantResponse>("/verify-merchant", {
    method: "POST", body: JSON.stringify({ transaction_id: transactionId, lang }),
  });

export type EstimateAction = "audit" | "verify" | "draft" | "review_book" | "note" | "search";
export const getAgentEstimate = (action: EstimateAction, opts: { noteId?: number; transactionId?: number; lang?: string } = {}) => {
  const q = new URLSearchParams({ action, lang: opts.lang ?? "es" });
  if (opts.noteId != null) q.set("note_id", String(opts.noteId));
  if (opts.transactionId != null) q.set("transaction_id", String(opts.transactionId));
  return agentsFetch<AgentEstimate>(`/estimate?${q}`);
};

export const getAgentUsage = () => agentsFetch<AgentUsageSummary>("/usage");

/** "El equipo trabajando" (18-oct): qué hizo cada agente en la ÚLTIMA corrida del libro, leído de `agentcall`.
 *  `run: null` = ese agente no corrió en esa corrida; `last_seconds_ago: null` = nunca corrió en este libro. */
export type TeamAgentKey = "note_checker" | "personal_judge" | "reviewer" | "verifier" | "drafter";
export interface TeamAgent {
  agent: TeamAgentKey;
  model: { key: string; label: string; reasoning: boolean };
  last_seconds_ago: number | null;
  run: { calls: number; ok: number; items_out: number; items_valid: number; latency_ms: number;
         cost_usd: number; credits: number } | null;
}
export interface AgentTeam {
  max_id: number;   // la última llamada del libro: pásala como `sinceId` para ver solo lo de la acción que empieza
  run: { seconds_ago: number; calls: number; cost_usd: number; credits: number } | null;
  agents: TeamAgent[];
}
/** `sinceId` (18-oct): solo las llamadas de ESTA acción; la pantalla la sondea mientras corre (no gasta IA). */
export const getAgentTeam = (sinceId?: number | null) =>
  agentsFetch<AgentTeam>(sinceId != null ? `/team?since_id=${sinceId}` : "/team");


// --- LA REVISIÓN DEL LIBRO (FASE 11, Auditoría / IA · 8-oct-2026): la lista ÚNICA de hallazgos ---
// guardados (auditor + veredicto del ① + evidencia del ②), lo Pendiente agrupado y el encabezado.
export interface TxSummary {
  id: number; merchant: string; date: string; amount: number; is_inflow: boolean; account: string | null;
  description: string; category: string | null; account_type: string | null; source: string | null;
}

export interface BoardFinding {
  id: number; key: string; type: string; severity: "high" | "medium" | "low" | "info"; merchant: string | null;
  payload: Record<string, unknown>; status: string;
  agent_verdict: "problem" | "legitimate" | "unsure" | null; agent_observation: string | null;
  observation_stale: boolean;
  agent_reviewed_at: string | null;
  web: { business_type: string; confidence: string | null; source_url: string | null; source_domain: string | null } | null;
  transactions: TxSummary[]; rows: number; is_info: boolean;
}

export interface PendingGroup {
  merchant: string; count: number; total: number; direction: "in" | "out"; first: string; last: string;
  accounts: string[]; person_payment: boolean;
  web: BoardFinding["web"]; transactions: TxSummary[];
}

export interface AuditBoard {
  stats: { to_review: number; info: number; problems: number; pending: number; pending_merchants: number;
           reviewed_at: string | null; reviewed_seconds_ago: number | null; silenced: number };
  findings: BoardFinding[];
  pending_groups: PendingGroup[];
}

async function auditFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}/api/companies/${cid()}${path}`, {
    ...init,
    headers: { ...(await authHeaders()), "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    const detail = await res.json().then((j) => j?.detail).catch(() => null);
    throw new AgentError(detail?.code ?? (res.status === 403 ? "forbidden" : "agent_error"));
  }
  return res.json();
}

/** `lang`: el idioma de la pantalla — un veredicto del ① guardado en otro idioma llega marcado viejo. */
export const getAuditBoard = (lang?: string) =>
  auditFetch<AuditBoard>(`/audit/board${lang ? `?lang=${encodeURIComponent(lang)}` : ""}`);
export const getAuditSummary = () => auditFetch<AuditBoard["stats"]>("/audit/summary");

/** "Está bien" (no vuelve a salir) o reabrir. */
export const resolveFinding = (id: number, status: "ok" | "open", note?: string) =>
  auditFetch<{ id: number; status: string }>(`/audit/findings/${id}/resolve`, {
    method: "POST", body: JSON.stringify({ status, note: note ?? null }),
  });

export type DismissReason = "wrong_category" | "too_broad" | "one_off" | "not_now" | "other";

/** Descartar una fila del ④ con su motivo: no la vuelve a proponer. */
export const dismissProposal = (p: FormulaProposal, reason_code: DismissReason, reason_text?: string) =>
  auditFetch<{ id: number }>("/agents/dismissals", {
    method: "POST",
    body: JSON.stringify({ keyword: p.keyword, valid_from: p.valid_from, valid_to: p.valid_to,
                           account_type: p.account_type, category: p.category, reason_code,
                           reason_text: reason_text || null }),
  });

// ── EL PANEL DE LA TRANSACCIÓN (FASE 13, área Firm · 13-oct) ────────────────────────────────────
// Lo que se sabe de UN movimiento (procedencia, historial, hallazgos del auditor con el ① y el ②,
// notas del libro) y el puntito de cada fila de la hoja. Solo lectura: el panel no categoriza.
export type FlagLevel = "problem" | "review" | "info";

export interface TxChange {
  id: number; changed_at: string; via: string; by: string | null; by_system: boolean;
  before: { account_type: string | null; category: string | null; subcategory: string | null; source: string | null };
  after: { account_type: string | null; category: string | null; subcategory: string | null; source: string | null };
  reason: string | null;
}

export interface TxInsight {
  transaction: TxSummary & {
    subcategory: string | null; note: string | null; description_extra: string | null;
    matched_rule_id: number | null; bank_removed_at: string | null;
  };
  merchant: string;
  provenance: {
    kind: "rule" | "manual" | "transfer" | "pending" | "other"; source?: string | null;
    rule: {
      id: number; keyword: string; account_type: string | null; category: string; subcategory: string | null;
      priority: number | null; valid_from: string | null; valid_to: string | null;
      amount_min: number | null; amount_max: number | null; origin: string | null;
    } | null;
  };
  history: TxChange[];
  findings: (BoardFinding & { level: FlagLevel })[];
  web: BoardFinding["web"];
  notes: { id: number; kind: string; priority: string; text: string; merchant: string | null;
           valid_from: string | null; valid_to: string | null }[];
  level: FlagLevel | null;
}

/** {transaction_id: nivel}. Pone los hallazgos al día antes (como abrir Auditoría). */
export const getTransactionFlags = () => auditFetch<Record<string, FlagLevel>>("/transactions/flags");
export const getTransactionInsight = (id: number) => auditFetch<TxInsight>(`/transactions/${id}/insight`);

/** POR QUÉ tiene su categoría (14-oct, Movimientos): las reglas que mencionan la descripción, en el orden
 *  real, y qué pasó con cada una (`wins` decide · `date`/`amount` no cumple la condición · `after` va detrás). */
export interface RuleTraceStep {
  id: number; order: number; keyword: string; result: "wins" | "date" | "amount" | "after";
  account_type: string | null; category: string;
  valid_from: string | null; valid_to: string | null; amount_min: number | null; amount_max: number | null;
}
export interface RuleTrace {
  steps: RuleTraceStep[]; checked: number; winner_id: number | null;
  decision: "rule" | "transfer" | "pending";
  transfer: { account_type: string; category: string } | null;
  manual: boolean; bare_check: boolean;
}
export const getRuleTrace = (id: number) => auditFetch<RuleTrace>(`/transactions/${id}/rule-trace`);
