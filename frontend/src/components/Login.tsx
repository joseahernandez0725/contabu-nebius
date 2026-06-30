import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  signInWithPopup,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
} from "firebase/auth";
import { auth, googleProvider } from "../firebase";
import LanguageSwitcher from "./LanguageSwitcher";

// Pantalla de acceso. Al autenticar, `onAuthStateChanged` (en App) detecta la sesión y
// renderiza la app — por eso aquí no navegamos a mano.
export default function Login() {
  const { t } = useTranslation();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Muestra el CÓDIGO real de Firebase (auth/…) para diagnosticar, no un mensaje opaco.
  function fail(base: string, e: unknown) {
    const code = (e as { code?: string })?.code;
    setError(code ? `${base} (${code})` : base);
    setBusy(false);
  }

  async function withGoogle() {
    setBusy(true);
    setError(null);
    try {
      await signInWithPopup(auth, googleProvider);
    } catch (e) {
      fail(t("login.error"), e);
    }
  }

  async function withEmail(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "signup") await createUserWithEmailAndPassword(auth, email, password);
      else await signInWithEmailAndPassword(auth, email, password);
    } catch (err) {
      fail(mode === "signup" ? t("login.signupError") : t("login.error"), err);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      <div className="self-end p-4">
        <LanguageSwitcher />
      </div>
      <div className="flex-1 flex items-center justify-center px-4 pb-16">
        <div className="w-full max-w-sm bg-white rounded-3xl shadow-sm border border-slate-100 p-7 space-y-5">
          <div className="text-center space-y-2">
            <img src="/icon.svg" alt="ContaBu" className="size-14 rounded-2xl mx-auto shadow-sm" />
            <div className="font-bold text-xl tracking-tight">
              <span className="text-brand-amber">Conta</span>
              <span className="text-brand-green">Bu</span>
            </div>
            <p className="text-sm text-slate-500">{t("login.subtitle")}</p>
          </div>

          <button
            onClick={withGoogle}
            disabled={busy}
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl border border-slate-200 font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
          >
            <svg viewBox="0 0 24 24" className="size-5"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1Z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23Z"/><path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.07H2.18a11 11 0 0 0 0 9.86l3.66-2.84Z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1A11 11 0 0 0 2.18 7.07l3.66 2.84C6.71 7.3 9.14 5.38 12 5.38Z"/></svg>
            {t("login.google")}
          </button>

          <div className="flex items-center gap-3 text-xs text-slate-400">
            <div className="h-px flex-1 bg-slate-200" />
            {t("login.or")}
            <div className="h-px flex-1 bg-slate-200" />
          </div>

          <form onSubmit={withEmail} className="space-y-3">
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={t("login.email")}
              className="w-full rounded-xl border border-slate-200 px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-brand-green/40"
            />
            <input
              type="password"
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t("login.password")}
              className="w-full rounded-xl border border-slate-200 px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-brand-green/40"
            />
            {error && <p className="text-sm text-rose-600">{error}</p>}
            <button
              type="submit"
              disabled={busy}
              className="w-full py-2.5 rounded-xl font-semibold text-white shadow-sm disabled:opacity-60"
              style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
            >
              {busy ? t("login.loading") : mode === "signup" ? t("login.signUp") : t("login.signIn")}
            </button>
          </form>

          <p className="text-center text-sm text-slate-500">
            {mode === "signup" ? t("login.haveAccount") : t("login.noAccount")}{" "}
            <button
              onClick={() => { setMode(mode === "signup" ? "signin" : "signup"); setError(null); }}
              className="font-semibold text-brand-green hover:underline"
            >
              {mode === "signup" ? t("login.signIn") : t("login.signUp")}
            </button>
          </p>
        </div>
      </div>
    </div>
  );
}
