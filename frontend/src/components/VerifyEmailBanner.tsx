import { useState } from "react";
import { useTranslation } from "react-i18next";
import { sendEmailVerification } from "firebase/auth";
import { auth } from "../firebase";

// Aviso NO bloqueante: cuenta de correo/contraseña sin verificar. La verificación importa (un typo
// en el correo = cuenta huérfana e irrecuperable), pero bloquear la entrada mata la activación —
// por eso es un banner, no un muro. Se muestra en la app y en el onboarding.
export default function VerifyEmailBanner() {
  const { t } = useTranslation();
  const [hidden, setHidden] = useState(false);
  const [sent, setSent] = useState(false);
  const [stillUnverified, setStillUnverified] = useState(false);
  const [busy, setBusy] = useState(false);

  const user = auth.currentUser;
  const needs =
    !!user && !user.emailVerified &&
    user.providerData.some((p) => p.providerId === "password"); // Google ya viene verificado
  if (!needs || hidden) return null;

  async function resend() {
    if (!user) return;
    setBusy(true);
    try {
      await sendEmailVerification(user);
      setSent(true);
    } catch { /* límite de reenvíos de Firebase: silencio, el botón queda */ }
    setBusy(false);
  }

  // "Ya lo verifiqué": recarga el usuario de Firebase; si el enlace ya se abrió, el banner muere.
  async function check() {
    if (!user) return;
    setBusy(true);
    setStillUnverified(false);
    try {
      await user.reload();
      if (auth.currentUser?.emailVerified) setHidden(true);
      else setStillUnverified(true);
    } catch { /* sin red: se reintenta con otro clic */ }
    setBusy(false);
  }

  return (
    <div className="flex items-center justify-between gap-3 flex-wrap text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2.5">
      <span>
        {t("verify.title", {
          defaultValue: "Verify your email — we sent a link to {{email}}.",
          email: user.email,
        })}
        {stillUnverified && (
          <> {t("verify.still", { defaultValue: "Not verified yet — check your spam folder." })}</>
        )}
      </span>
      <span className="flex items-center gap-3 shrink-0">
        <button onClick={resend} disabled={busy || sent} className="underline disabled:opacity-60">
          {sent ? t("verify.sent", { defaultValue: "Sent ✓" }) : t("verify.resend", { defaultValue: "Resend" })}
        </button>
        <button onClick={check} disabled={busy} className="font-semibold underline disabled:opacity-60">
          {t("verify.check", { defaultValue: "I verified it" })}
        </button>
      </span>
    </div>
  );
}
