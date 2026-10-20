import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  updateSettlement, updateCompany, deleteCompany,
  getMembers, inviteMember, updateMemberRole, removeMember,
  getChart, createChartAccount, updateChartAccount, deleteChartAccount,
  type ChartAccount, type ChartData, type Member, type TaxForm,
} from "../api";
import { SECTION_LABEL } from "../accounting";
import { DEMO_MODE } from "../demo";
import type { Company } from "../types";
import { PageHeader, Stat } from "./PageKit";

// Las secciones del plan, en el orden del P&L; NON_PL al final (lo que no entra al estado de resultados).
const SECTIONS = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"];
const TAX_FORMS: TaxForm[] = ["schedule_c", "1120s"];

export default function Settings({
  company,
  onSaved,
  onDeleted,
  canDelete,
}: {
  company: Company | null;
  onSaved: (c: Company) => void;
  onDeleted: () => void;
  canDelete: boolean;
}) {
  const { t } = useTranslation();
  const role = company?.role ?? "OWNER";
  const isOwner = role === "OWNER";
  const canEdit = role !== "VIEWER";

  // LA LÍNEA DE ESTADO (13-oct, Firm: diseño de página compartido — `PageKit.tsx`). Ajustes no lleva lista +
  // detalle: son pocos bloques que se editan en su sitio. El título lo pone la barra de arriba.
  const [people, setPeople] = useState<number | null>(null);
  const [own, setOwn] = useState<number | null>(null);
  useEffect(() => {
    if (isOwner) getMembers().then((m) => setPeople(m.length)).catch(() => setPeople(null));
    getChart().then((c) => setOwn(c.accounts.filter((a) => a.custom && !a.archived).length)).catch(() => setOwn(null));
  }, [isOwner, company?.tax_form]);
  const form = company?.tax_form ?? "schedule_c";

  return (
    <div className="space-y-3">
      <PageHeader meta={t("settings.subtitle")}>
        {company && <Stat value={t(`sectors.${company.sector}`, { defaultValue: company.sector })} label={t("page.settings.sector")} />}
        {company && !DEMO_MODE && <Stat value={t(`chart.formShort.${form}`)} label={t("page.settings.taxForm")} />}
        {company && <Stat value={company.settlement_buffer_days ?? 5} label={t("page.settings.waitDays", { count: company.settlement_buffer_days ?? 5 })} />}
        {isOwner && <Stat value={people ?? undefined} label={t("page.settings.people", { count: people ?? 0 })} />}
        <Stat value={own ?? undefined} label={t("page.settings.ownAccounts", { count: own ?? 0 })} />
      </PageHeader>

      {isOwner && company && (
        <BusinessProfile company={company} canDelete={canDelete} onSaved={onSaved} onDeleted={onDeleted} />
      )}

      {canEdit && (
        <>
          {/* 🔄 1-oct, decisión de Jose: fuera los ajustes por monto (supermercado, gasolinera,
              tarjetas propias). Lo que no decida una REGLA queda Pendiente; el criterio del bookkeeper
              vive en las notas de Auditoría (F11). Los días de espera se quedan: no son un monto. */}
          {company && <RecentWait company={company} onSaved={onSaved} />}

          {/* El plan del libro (Movimientos, 18-oct): se recarga si el libro cambia de formulario. */}
          <ChartEditor key={`${company?.id}-${form}`} />
        </>
      )}

      {isOwner && company && <TeamSection />}

      {!canEdit && (
        <div className="bg-white rounded-xl border border-slate-200 p-3 text-sm text-slate-500">
          {t("settings.viewerNote", { defaultValue: "You have view-only access to this business." })}
        </div>
      )}
    </div>
  );
}

// Equipo de la empresa (roles y permisos). Solo el OWNER. Invitar por email, cambiar rol, quitar.
function TeamSection() {
  const { t } = useTranslation();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("MEMBER");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = () => getMembers().then(setMembers).catch(() => setMembers([]));
  useEffect(() => { load(); }, []);

  async function invite() {
    if (!email.trim()) return;
    setBusy(true); setErr(null);
    try { await inviteMember(email.trim(), role); setEmail(""); await load(); }
    catch (e) { setErr(e instanceof Error ? e.message : "Error"); }
    finally { setBusy(false); }
  }

  const roleLabel = (r: string) => t(`settings.role.${r}`, { defaultValue: r });

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-3 space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">{t("settings.team", { defaultValue: "Team" })}</h3>
        <p className="text-xs text-slate-500 mt-1">
          {t("settings.teamHelp", { defaultValue: "Invite your team. Member = can edit · Viewer = read-only." })}
        </p>
      </div>

      <div className="space-y-1">
        {(members ?? []).map((m) => (
          <div key={m.id ?? "owner"} className="flex items-center justify-between gap-2 py-1.5 text-sm">
            <span className="min-w-0 flex-1 truncate text-slate-700">
              {m.email ?? "—"}
              {m.pending && <span className="ms-1 text-[10px] uppercase text-amber-600 bg-amber-50 rounded px-1">{t("settings.pending", { defaultValue: "pending" })}</span>}
            </span>
            {m.is_owner ? (
              <span className="text-xs text-slate-500">{roleLabel("OWNER")}</span>
            ) : (
              <>
                <select
                  value={m.role}
                  onChange={async (e) => { await updateMemberRole(m.id!, e.target.value); load(); }}
                  className="text-xs rounded-lg border border-slate-200 px-2 py-1 focus:outline-none focus:ring-2 focus:ring-brand-green/40"
                >
                  <option value="MEMBER">{roleLabel("MEMBER")}</option>
                  <option value="VIEWER">{roleLabel("VIEWER")}</option>
                </select>
                <button onClick={async () => { await removeMember(m.id!); load(); }}
                  className="text-slate-400 hover:text-rose-600" aria-label="remove">
                  <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
                  </svg>
                </button>
              </>
            )}
          </div>
        ))}
      </div>

      {err && <div className="text-sm text-rose-600">{err}</div>}

      <div className="flex items-center gap-2 border-t border-slate-100 pt-3">
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder={t("settings.invitePlaceholder", { defaultValue: "teammate@email.com" })}
          className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-green/40"
        />
        <select value={role} onChange={(e) => setRole(e.target.value)}
          className="text-sm rounded-lg border border-slate-200 px-2 py-2 focus:outline-none focus:ring-2 focus:ring-brand-green/40">
          <option value="MEMBER">{roleLabel("MEMBER")}</option>
          <option value="VIEWER">{roleLabel("VIEWER")}</option>
        </select>
        <button onClick={invite} disabled={busy || !email.trim()}
          className="px-3 py-2 rounded-lg text-sm font-semibold text-white bg-emerald-700 hover:bg-emerald-800 disabled:opacity-50">
          {t("settings.invite", { defaultValue: "Invite" })}
        </button>
      </div>
    </div>
  );
}

// Perfil de la empresa activa: renombrar + eliminar (con todos sus datos).
function BusinessProfile({ company, canDelete, onSaved, onDeleted }: {
  company: Company; canDelete: boolean; onSaved: (c: Company) => void; onDeleted: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(company.name);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function rename() {
    if (!name.trim() || name.trim() === company.name) return;
    setSaving(true); setErr(null);
    try { onSaved(await updateCompany({ name: name.trim() })); }
    catch (e) { setErr(e instanceof Error ? e.message : "Error"); }
    finally { setSaving(false); }
  }
  async function remove() {
    if (!confirm(t("settings.confirmDelete", { defaultValue: "Delete this business and ALL its data? This can't be undone." }))) return;
    setDeleting(true); setErr(null);
    try { await deleteCompany(company.id); onDeleted(); }
    catch (e) { setErr(e instanceof Error ? e.message : "Error"); setDeleting(false); }
  }

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-3 space-y-4">
      <h3 className="text-sm font-semibold text-slate-900">{t("settings.business", { defaultValue: "Business" })}</h3>
      <div>
        <label className="block text-sm font-medium text-slate-800 mb-1">{t("settings.nameLabel", { defaultValue: "Name" })}</label>
        <div className="flex gap-2">
          <input value={name} onChange={(e) => setName(e.target.value)}
            className="flex-1 rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-green/40" />
          <button onClick={rename} disabled={saving || !name.trim() || name.trim() === company.name}
            className="px-4 rounded-lg text-sm font-semibold text-white bg-emerald-700 hover:bg-emerald-800 disabled:opacity-50">
            {saving ? t("settings.saving") : t("settings.rename", { defaultValue: "Rename" })}
          </button>
        </div>
      </div>
      {/* CÓMO TRIBUTA (Movimientos, 18-oct). En el demo no se pregunta: todos los libros son Schedule C. */}
      {!DEMO_MODE && <TaxFormPicker company={company} onSaved={onSaved} />}
      {err && <div className="text-sm text-rose-600">{err}</div>}
      <div className="border-t border-slate-100 pt-3">
        <button onClick={remove} disabled={!canDelete || deleting}
          className="text-sm text-rose-600 font-medium disabled:opacity-40 disabled:cursor-not-allowed">
          {deleting ? t("settings.deleting", { defaultValue: "Deleting…" }) : t("settings.deleteBusiness", { defaultValue: "Delete this business" })}
        </button>
        {!canDelete && <p className="text-xs text-slate-400 mt-1">{t("settings.cantDeleteLast", { defaultValue: "You can't delete your only business." })}</p>}
      </div>
    </div>
  );
}

// MOVIMIENTOS RECIENTES: cuántos días esperar antes de registrarlos, por si el banco todavía los
// está procesando (base caja: un "pending" puede cambiar de monto o desaparecer). Se guarda al tocar.
const WAIT_CHOICES = [0, 3, 5, 7];

function RecentWait({ company, onSaved }: { company: Company; onSaved: (c: Company) => void }) {
  const { t } = useTranslation();
  const current = company.settlement_buffer_days ?? 5;
  const [err, setErr] = useState<string | null>(null);
  // Si el libro ya tenía otro valor (p. ej. 10 días), también se muestra, marcado.
  const choices = [...new Set([...WAIT_CHOICES, current])].sort((a, b) => a - b);

  async function pick(n: number) {
    try { onSaved(await updateSettlement(n)); setErr(null); }
    catch (e) {
      setErr(t(`settings.recent.err.${e instanceof Error ? e.message : "error"}`,
        { defaultValue: t("settings.recent.err.error") }));
    }
  }

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-3 space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">{t("settings.recent.title")}</h3>
        <p className="text-xs text-slate-500 mt-1">{t("settings.recent.help")}</p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {choices.map((n) => (
          <button key={n} onClick={() => pick(n)}
            className={`px-3 py-1 rounded-lg text-xs border transition-colors ${
              current === n ? "border-brand-green text-brand-green bg-brand-green/5 font-medium"
                : "border-slate-200 text-slate-500 hover:bg-slate-50"}`}>
            {n === 0 ? t("settings.recent.none") : t("settings.recent.days", { count: n })}
          </button>
        ))}
      </div>
      {err && <div className="text-xs text-rose-600">{err}</div>}
    </div>
  );
}


// CÓMO TRIBUTA EL LIBRO (Movimientos, 18-oct): Schedule C o Form 1120-S. Cambiarlo pasa el libro al plan por
// defecto del otro formulario sin perder nada (lo que está en uso se queda para renombrarlo o fusionarlo).
function TaxFormPicker({ company, onSaved }: { company: Company; onSaved: (c: Company) => void }) {
  const { t } = useTranslation();
  const current = company.tax_form ?? "schedule_c";
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function pick(f: TaxForm) {
    if (f === current || !confirm(t("chart.formConfirm", { form: t(`chart.form.${f}`) }))) return;
    setBusy(true); setErr(null);
    try { onSaved(await updateCompany({ tax_form: f })); }
    catch { setErr(t("chart.err.error")); }
    finally { setBusy(false); }
  }

  return (
    <div>
      <label className="block text-sm font-medium text-slate-800 mb-1">{t("chart.formLabel")}</label>
      <div role="radiogroup" className="flex flex-wrap gap-1.5">
        {TAX_FORMS.map((f) => (
          <button key={f} role="radio" aria-checked={current === f} disabled={busy} onClick={() => pick(f)}
            className={`px-3 py-1.5 rounded-lg text-sm border transition-colors disabled:opacity-50 ${
              current === f ? "border-brand-green text-brand-green bg-brand-green/5 font-medium"
                : "border-slate-200 text-slate-600 hover:bg-slate-50"}`}>
            {t(`chart.form.${f}`)}
          </button>
        ))}
      </div>
      <p className="text-xs text-slate-500 mt-1">{t("chart.formHelp")}</p>
      {err && <div className="text-xs text-rose-600 mt-1">{err}</div>}
    </div>
  );
}

// EL PLAN DE CUENTAS DEL LIBRO (Movimientos, 18-oct; antes solo agregar/borrar las propias). Cada cuenta con
// su sección, su línea del IRS y cuántos movimientos y reglas la usan. Renombrar (o fusionar con otra) y
// mover de sección arrastran sus movimientos y sus reglas; quitar una en uso la archiva.
function ChartEditor() {
  const { t, i18n } = useTranslation();
  const [data, setData] = useState<ChartData | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [adding, setAdding] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const showTr = (i18n.language || "en").split("-")[0] !== "en";

  const load = () => getChart().then(setData).catch(() => setMsg({ ok: false, text: t("chart.err.error") }));
  useEffect(() => { load(); }, []);

  const lineLabel = (code: string | null) => lineText(t, code, data?.lines ?? []);
  const errText = (e: unknown) =>
    t(`chart.err.${e instanceof Error ? e.message : "error"}`, { defaultValue: t("chart.err.error") });

  async function run(fn: () => Promise<string | null>) {
    setBusy(true); setMsg(null);
    try {
      const ok = await fn();
      await load();
      if (ok) setMsg({ ok: true, text: ok });
    } catch (e) { setMsg({ ok: false, text: errText(e) }); }
    finally { setBusy(false); }
  }

  async function save(a: ChartAccount, draft: { name: string; section: string; line: string }) {
    const name = draft.name.trim();
    const body: { name?: string; account_type?: string; irs_line?: string } = {};
    if (name && name !== a.name) body.name = name;
    if (draft.section !== a.account_type) body.account_type = draft.section;
    if (draft.line !== (a.irs_line ?? "")) body.irs_line = draft.line;
    if (!Object.keys(body).length) { setEditing(null); return; }
    const uses = { tx: a.tx_count, rules: a.rule_count };
    if (body.name && (a.tx_count || a.rule_count) &&
        !confirm(t("chart.confirmRename", { from: a.name, to: name, ...uses }))) return;
    if (body.account_type && (a.tx_count || a.rule_count) &&
        !confirm(t("chart.confirmMove", { name: a.name, section: SECTION_LABEL[draft.section] ?? draft.section,
                                          from: SECTION_LABEL[a.account_type] ?? a.account_type, ...uses }))) return;
    await run(async () => {
      try {
        const r = await updateChartAccount(a.id, body);
        setEditing(null);
        return r.moved ? t("chart.moved", { count: r.moved }) : null;
      } catch (e) {
        // El nombre ya existe en el plan: ofrecer FUSIONAR (todo pasa a la otra y esta sale del plan).
        const d = (e as Error & { detail?: { code?: string; name?: string } }).detail;
        if (d?.code === "exists" && d.name) {
          if (!confirm(t("chart.confirmMerge", { from: a.name, to: d.name }))) return null;
          const r = await updateChartAccount(a.id, { name: d.name, merge: true });
          setEditing(null);
          return t("chart.merged", { from: a.name, to: d.name, count: r.moved ?? 0 });
        }
        throw e;
      }
    });
  }

  async function remove(a: ChartAccount) {
    const used = a.tx_count > 0 || a.rule_count > 0;
    if (!confirm(used ? t("chart.confirmArchive", { name: a.name }) : t("chart.confirmDelete", { name: a.name }))) return;
    await run(async () => {
      const r = await deleteChartAccount(a.id);
      setEditing(null);
      return r.archived ? t("chart.archivedOk", { name: a.name }) : null;
    });
  }

  const restore = (a: ChartAccount) => run(async () => { await updateChartAccount(a.id, { archived: false }); return null; });

  async function add(section: string, name: string, line: string) {
    await run(async () => {
      await createChartAccount({ name: name.trim(), account_type: section, irs_line: line || null });
      setAdding(null);
      return null;
    });
  }

  if (!data) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 p-3 text-sm text-slate-400">
        {msg ? <span className="text-rose-600">{msg.text}</span> : t("chart.loading")}
      </div>
    );
  }
  const active = data.accounts.filter((a) => !a.archived);
  const archived = data.accounts.filter((a) => a.archived);
  const tr = (name: string) => (showTr ? t(`cat.${name}`, { defaultValue: "" }) : "");

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-900">{t("chart.title")}</h3>
        {!DEMO_MODE && <span className="text-xs text-slate-500">{t(`chart.form.${data.tax_form}`)}</span>}
      </div>
      <p className="text-sm text-slate-500 mt-1 mb-3">{t("chart.lead")}</p>

      {msg && <div className={`text-sm mb-2 ${msg.ok ? "text-emerald-700" : "text-rose-600"}`}>{msg.text}</div>}

      <div className="space-y-4">
        {SECTIONS.map((section) => (
          <div key={section}>
            <div className="text-[11px] uppercase tracking-wide text-slate-400 mb-1">{SECTION_LABEL[section] ?? section}</div>
            <div className="divide-y divide-slate-100">
              {active.filter((a) => a.account_type === section).map((a) =>
                editing === a.id ? (
                  <AccountEditor key={a.id} account={a} lines={data.lines} busy={busy}
                    onSave={(d) => save(a, d)} onRemove={() => remove(a)} onCancel={() => setEditing(null)} />
                ) : (
                  <div key={a.id} className="flex items-start gap-2 py-1.5 text-sm">
                    <div className="min-w-0 flex-1">
                      <span className="text-slate-800">{a.name}</span>
                      {tr(a.name) && <span className="ms-1 text-slate-400/70 italic">· {tr(a.name)}</span>}
                      {a.custom && (
                        <span className="ms-2 text-[10px] uppercase tracking-wide text-brand-green/80 bg-brand-green/10 rounded px-1 py-0.5">
                          {t("chart.custom")}
                        </span>
                      )}
                      {a.help && <p className="text-xs text-slate-500 mt-0.5">{t(`chart.help.${a.help}`)}</p>}
                    </div>
                    <span className="shrink-0 max-w-[16rem] truncate text-[11px] text-slate-500 bg-slate-50 border border-slate-200 rounded px-1.5 py-0.5"
                      title={lineLabel(a.irs_line)}>
                      {lineLabel(a.irs_line)}
                    </span>
                    <span className="shrink-0 w-32 text-end text-xs text-slate-400 tabular-nums">
                      {a.tx_count || a.rule_count
                        ? [a.tx_count ? t("chart.txCount", { count: a.tx_count }) : null,
                           a.rule_count ? t("chart.ruleCount", { count: a.rule_count }) : null].filter(Boolean).join(" · ")
                        : t("chart.unused")}
                    </span>
                    {a.locked ? (
                      <span className="shrink-0 w-14 text-end text-slate-300" title={t("chart.locked")} aria-label={t("chart.locked")}>
                        <svg viewBox="0 0 24 24" className="size-4 inline" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 018 0v4" />
                        </svg>
                      </span>
                    ) : (
                      <button onClick={() => { setEditing(a.id); setAdding(null); setMsg(null); }} disabled={busy}
                        className="shrink-0 w-14 text-end text-xs font-medium text-brand-green hover:underline disabled:opacity-50">
                        {t("chart.edit")}
                      </button>
                    )}
                  </div>
                ))}
            </div>
            {adding === section ? (
              <NewAccount section={section} lines={data.lines} suggested={data.suggested[section] ?? ""} busy={busy}
                onAdd={(n, l) => add(section, n, l)} onCancel={() => setAdding(null)} />
            ) : (
              <button onClick={() => { setAdding(section); setEditing(null); setMsg(null); }}
                className="mt-1 text-sm font-medium text-brand-green hover:underline">
                {t("chart.add")}
              </button>
            )}
          </div>
        ))}
      </div>

      {archived.length > 0 && (
        <div className="mt-4 border-t border-slate-100 pt-3">
          <button onClick={() => setShowArchived((v) => !v)} className="text-xs font-medium text-slate-500 hover:text-slate-700">
            {showArchived ? "▾" : "▸"} {t("chart.archived", { count: archived.length })}
          </button>
          {showArchived && (
            <div className="mt-1">
              <p className="text-xs text-slate-400 mb-1">{t("chart.archivedHelp")}</p>
              {archived.map((a) => (
                <div key={a.id} className="flex items-center gap-2 py-1 text-sm">
                  <span className="min-w-0 flex-1 truncate text-slate-500">
                    {a.name} <span className="text-xs text-slate-400">· {SECTION_LABEL[a.account_type] ?? a.account_type}</span>
                  </span>
                  <span className="text-xs text-slate-400">{t("chart.txCount", { count: a.tx_count })}</span>
                  <button onClick={() => restore(a)} disabled={busy}
                    className="text-xs font-medium text-brand-green hover:underline disabled:opacity-50">
                    {t("chart.restore")}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// LA LÍNEA EN PALABRAS (Jose, 19-oct): "Línea 20 · Other deductions", "Parte V · Other expenses", "1125-A, línea 5 ·
// Other costs", "Schedule K, 16d · Distributions". En la base se queda el código ("1120S:20", "SchC:PartV"…).
function lineText(t: (k: string, o?: Record<string, unknown>) => string, code: string | null, lines: ChartData["lines"]): string {
  if (!code) return t("chart.noLine");
  const label = lines.find((l) => l.code === code)?.label;
  if (!label) return `${code} ⚠`;   // una línea de otro formulario (no debería pasar: el backend la rechaza)
  const [form, n] = code.split(":");
  const kind =
    form === "SchC" && n === "PartV" ? "partV"
      : form === "SchC" && ["36", "37", "38", "39"].includes(n) ? "partIII"
      : form === "1125A" ? "f1125a"
      : form === "K" ? "schK"
      : form === "L" ? "schL"
      : "line";
  return t(`chart.lineFmt.${kind}`, { n, label });
}

// La línea del IRS: un selector con las del formulario del libro (o ninguna).
function LineSelect({ value, lines, onChange }: {
  value: string; lines: ChartData["lines"]; onChange: (v: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={t("chart.line")}
      className="min-w-0 max-w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-green/40">
      <option value="">{t("chart.noLine")}</option>
      {lines.map((l) => <option key={l.code} value={l.code}>{lineText(t, l.code, lines)}</option>)}
    </select>
  );
}

function AccountEditor({ account, lines, busy, onSave, onRemove, onCancel }: {
  account: ChartAccount; lines: ChartData["lines"]; busy: boolean;
  onSave: (d: { name: string; section: string; line: string }) => void; onRemove: () => void; onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(account.name);
  const [section, setSection] = useState(account.account_type);
  const [line, setLine] = useState(account.irs_line ?? "");
  const submit = () => onSave({ name, section, line });
  return (
    <div className="py-2 space-y-2 bg-slate-50/60 -mx-1 px-1 rounded-lg">
      <div className="flex flex-wrap items-center gap-2">
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} maxLength={80}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); if (e.key === "Escape") onCancel(); }}
          aria-label={t("chart.name")}
          className="min-w-0 flex-1 basis-56 rounded-lg border border-slate-200 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-green/40" />
        <select value={section} onChange={(e) => setSection(e.target.value)} aria-label={t("chart.section")}
          className="rounded-lg border border-slate-200 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-green/40">
          {SECTIONS.map((s) => <option key={s} value={s}>{SECTION_LABEL[s] ?? s}</option>)}
        </select>
        <LineSelect value={line} lines={lines} onChange={setLine} />
      </div>
      <div className="flex items-center gap-2">
        <button onClick={submit} disabled={busy || !name.trim()}
          className="px-3 py-1.5 rounded-lg text-sm font-semibold text-white bg-emerald-700 hover:bg-emerald-800 disabled:opacity-50">
          {t("chart.save")}
        </button>
        <button onClick={onCancel} className="px-2 py-1.5 rounded-lg text-sm text-slate-500">{t("chart.cancel")}</button>
        <button onClick={onRemove} disabled={busy}
          className="ms-auto text-sm text-rose-600 font-medium disabled:opacity-40">
          {account.tx_count || account.rule_count ? t("chart.archive") : t("chart.remove")}
        </button>
      </div>
    </div>
  );
}

function NewAccount({ section, lines, suggested, busy, onAdd, onCancel }: {
  section: string; lines: ChartData["lines"]; suggested: string; busy: boolean;
  onAdd: (name: string, line: string) => void; onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [line, setLine] = useState(suggested);
  return (
    <div className="mt-2 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} maxLength={80}
          onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) onAdd(name, line); if (e.key === "Escape") onCancel(); }}
          placeholder={t("chart.namePlaceholder")}
          className="min-w-0 flex-1 basis-56 rounded-lg border border-slate-200 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-green/40" />
        <LineSelect value={line} lines={lines} onChange={setLine} />
      </div>
      <p className="text-xs text-slate-400">{t("chart.addHelp", { section: SECTION_LABEL[section] ?? section })}</p>
      <div className="flex items-center gap-2">
        <button onClick={() => onAdd(name, line)} disabled={busy || !name.trim()}
          className="px-3 py-1.5 rounded-lg text-sm font-semibold text-white bg-emerald-700 hover:bg-emerald-800 disabled:opacity-50">
          {t("chart.addButton")}
        </button>
        <button onClick={onCancel} className="px-2 py-1.5 rounded-lg text-sm text-slate-500">{t("chart.cancel")}</button>
      </div>
    </div>
  );
}
