import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { signOut } from "firebase/auth";
import { auth } from "../firebase";
import { goLive, inTour } from "../demo";
import { DemoCodeError, redeemDemoCode, refillDemoPool } from "../api";
import LanguageSwitcher from "./LanguageSwitcher";

// LAS DOS PUERTAS DEL DEMO (FASE 13, área Firm · 23-oct; docs/DEMO Y ENTREGA.md → "Las dos puertas").
// Solo se montan en el despliegue de demo. La visita guiada es de solo lectura y no gasta IA; el demo en vivo
// es "Entrar con Google" + un CÓDIGO DE ACCESO (no "contraseña": acaba de escribir la de Google).

/** El texto del enlace para pasar al demo en vivo: el anónimo entra con Google; quien ya entró con Google
 *  (sin código) va a escribir su código. */
function useGoLiveLabel() {
  const { t } = useTranslation();
  return auth.currentUser && !auth.currentUser.isAnonymous ? t("demoDoor.enterCode") : t("demoDoor.signInGoogle");
}

/** Un botón que gasta IA. En la visita guiada no corre: dice "Solo en el demo en vivo" y ofrece entrar.
 *  Fuera de la visita (o del demo) pinta el botón de siempre. Para las páginas de otras áreas es una línea. */
export function LiveOnly({ children, compact = false }: { children: ReactNode; compact?: boolean }) {
  const { t } = useTranslation();
  const label = useGoLiveLabel();
  if (!inTour()) return <>{children}</>;
  if (compact) {
    // Donde cabía un botón chico: una sola píldora, que es a la vez el aviso y el enlace.
    return (
      <button type="button" onClick={() => void goLive()} title={`${t("demoDoor.liveOnlyHint")} → ${label}`}
              className="shrink-0 max-w-[8.5rem] leading-tight text-center px-2.5 py-1 rounded-lg border border-dashed border-slate-300 text-xs text-slate-500 hover:text-brand-green hover:border-brand-green">
        {t("demoDoor.liveOnly")} ↗
      </button>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
      <span className="px-2.5 py-1 rounded-lg border border-dashed border-slate-300 text-slate-500 cursor-not-allowed"
            title={t("demoDoor.liveOnlyHint")}>
        {t("demoDoor.liveOnly")}
      </span>
      <button type="button" onClick={() => void goLive()} className="font-semibold text-brand-green hover:underline">
        {label}
      </button>
    </span>
  );
}

/** El aviso fijo de la visita guiada, dentro de la barra de arriba (que mide 56 px justos: la Hoja cuenta con
 *  eso, así que el aviso no puede agregar una franja). En celular, solo lo corto. */
export function TourBanner() {
  const { t } = useTranslation();
  const label = useGoLiveLabel();
  return (
    <div className="ms-auto min-w-0 flex items-center gap-2 rounded-full bg-amber-50 border border-amber-200 px-3 py-1 text-xs text-amber-900">
      {/* En el celular no cabe todo: el título se recorta y el enlace queda siempre entero. */}
      <span className="font-semibold min-w-0 truncate">{t("demoDoor.banner.title")}</span>
      <span className="hidden lg:inline truncate">{t("demoDoor.banner.text")}</span>
      <button type="button" onClick={() => void goLive()} className="shrink-0 font-semibold text-brand-green hover:underline">
        {label}
      </button>
    </div>
  );
}

function DoorCard({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      <div className="self-end p-4"><LanguageSwitcher /></div>
      <div className="flex-1 flex items-center justify-center px-4 pb-16">
        <div className="w-full max-w-sm bg-white rounded-3xl shadow-sm border border-slate-100 p-7 space-y-5">
          <div className="text-center space-y-2">
            <img src="/icon.svg" alt="ContaBu" className="size-14 rounded-2xl mx-auto shadow-sm" />
            <div className="font-bold text-xl tracking-tight">
              <span className="text-brand-amber">Conta</span>
              <span className="text-brand-green">Bu</span>
            </div>
          </div>
          {children}
        </div>
      </div>
    </div>
  );
}

/** Después de "Entrar con Google", si la cuenta no tiene acceso: el código. Lo verifica el backend. */
export function AccessCodeScreen({ email, onDone, onBrowseTour }: {
  email: string | null;
  onDone: () => void;
  onBrowseTour: () => void;
}) {
  const { t } = useTranslation();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mientras escribe el código, se deja lista una oficina en espera (Neon ya está despierto: no cuesta nada).
  useEffect(() => { refillDemoPool(); }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await redeemDemoCode(code);
      refillDemoPool();   // repone la que se acaba de llevar
      onDone();
    } catch (err) {
      const c = err instanceof DemoCodeError ? err.code : "network";
      setError(t(`demoDoor.code.error.${c}`, { defaultValue: t("demoDoor.code.error.demo_code_invalid") }));
      setBusy(false);
    }
  }

  return (
    <DoorCard>
      <div className="text-center space-y-1.5">
        <h1 className="font-semibold text-slate-900">{t("demoDoor.code.title")}</h1>
        <p className="text-sm text-slate-500">{t("demoDoor.code.lead")}</p>
        {email && <p className="text-xs text-slate-400">{t("demoDoor.code.signedInAs", { email })}</p>}
      </div>
      <form onSubmit={submit} className="space-y-3">
        <input
          type="text"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder={t("demoDoor.code.placeholder")}
          aria-label={t("demoDoor.code.placeholder")}
          className="w-full rounded-xl border border-slate-200 px-3 py-2.5 font-mono focus:outline-none focus:ring-2 focus:ring-brand-green/40"
        />
        {error && <p className="text-sm text-rose-600">{error}</p>}
        <button
          type="submit"
          disabled={busy || !code.trim()}
          className="w-full py-2.5 rounded-xl font-semibold text-white shadow-sm disabled:opacity-60"
          style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
        >
          {busy ? t("demoDoor.code.preparing") : t("demoDoor.code.submit")}
        </button>
      </form>
      <div className="space-y-2 text-center text-sm">
        <button type="button" onClick={onBrowseTour} className="font-semibold text-brand-green hover:underline">
          {t("demoDoor.code.browseTour")}
        </button>
        <div>
          <button type="button" onClick={() => void signOut(auth)} className="text-slate-500 hover:underline">
            {t("login.logout")}
          </button>
        </div>
      </div>
    </DoorCard>
  );
}

/** La visita guiada todavía no existe en esta base (falta correr `demo.py tour`). */
export function TourMissing() {
  const { t } = useTranslation();
  return (
    <DoorCard>
      <p className="text-sm text-center text-slate-600">{t("demoDoor.tourMissing")}</p>
      <div className="text-center space-y-2 text-sm">
        {auth.currentUser?.isAnonymous && (
          <button type="button" onClick={() => void goLive()} className="font-semibold text-brand-green hover:underline">
            {t("demoDoor.signInGoogle")}
          </button>
        )}
        <div>
          <button type="button" onClick={() => void signOut(auth)} className="text-slate-500 hover:underline">
            {t("login.logout")}
          </button>
        </div>
      </div>
    </DoorCard>
  );
}
