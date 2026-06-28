import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getCompany, createCheckout } from "../api";
import type { Company } from "../types";

// Tiers del modelo M5 (cupo de tx/mes → precio mensual).
const TIERS = [
  { quota: 100, price: 10 },
  { quota: 200, price: 15 },
  { quota: 400, price: 20 },
  { quota: 1000, price: 40 },
];

// Pantalla de planes (Stripe Checkout). Muestra el plan actual y deja suscribirse a un tier.
export default function Billing() {
  const { t } = useTranslation();
  const [company, setCompany] = useState<Company | null>(null);
  const [loading, setLoading] = useState<number | null>(null); // quota en proceso
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { getCompany().then(setCompany).catch(() => {}); }, []);

  const active = company?.subscription_status === "active";

  async function subscribe(quota: number) {
    setLoading(quota); setError(null);
    try {
      const { url } = await createCheckout(quota);
      window.location.href = url; // redirige a la página de pago de Stripe
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
      setLoading(null);
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <h3 className="font-bold text-slate-900">{t("billing.title", { defaultValue: "Plan" })}</h3>
        <p className="text-sm text-slate-500">
          {active
            ? t("billing.current", { defaultValue: "Active plan: {{n}} transactions/month.", n: company?.tx_quota ?? 0 })
            : t("billing.free", { defaultValue: "Free plan (manual only). Subscribe to unlock AI categorization." })}
        </p>
      </div>

      {error && <div className="text-sm text-rose-600">{error}</div>}

      <div className="grid grid-cols-2 gap-2">
        {TIERS.map((tier) => {
          const isCurrent = active && company?.tx_quota === tier.quota;
          return (
            <button
              key={tier.quota}
              onClick={() => subscribe(tier.quota)}
              disabled={loading !== null || isCurrent}
              className={`rounded-2xl border p-3 text-start transition-colors disabled:opacity-60 ${
                isCurrent ? "border-brand-green bg-brand-green/5" : "border-slate-200 hover:border-brand-green"
              }`}
            >
              <div className="text-lg font-bold text-slate-900">${tier.price}<span className="text-sm font-normal text-slate-400">/mo</span></div>
              <div className="text-sm text-slate-600">{t("billing.txMonth", { defaultValue: "{{n}} transactions/mo", n: tier.quota })}</div>
              <div className="mt-2 text-xs font-semibold text-brand-green">
                {isCurrent
                  ? t("billing.yourPlan", { defaultValue: "Your plan" })
                  : loading === tier.quota
                    ? t("billing.redirecting", { defaultValue: "Redirecting…" })
                    : t("billing.choose", { defaultValue: "Choose" })}
              </div>
            </button>
          );
        })}
      </div>
      <p className="text-[11px] text-slate-400">
        {t("billing.securedByStripe", { defaultValue: "Secure payment by Stripe. Cancel anytime." })}
      </p>
    </div>
  );
}
