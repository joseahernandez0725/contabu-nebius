import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import Dashboard from "./components/Dashboard";
import Transactions from "./components/Transactions";
import Upload from "./components/Upload";
import Review from "./components/Review";
import LanguageSwitcher from "./components/LanguageSwitcher";
import { getCompany, getReviewTransactions } from "./api";
import type { Company, Transaction } from "./types";

type View = "dashboard" | "transactions" | "review" | "upload";

function Logo() {
  return (
    <div
      className="size-9 rounded-xl flex items-center justify-center shadow-sm"
      style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
    >
      <svg viewBox="0 0 24 24" className="size-5 text-white" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
        <path d="M4 20V13M10 20V5M16 20v-9M22 20H2" />
      </svg>
    </div>
  );
}

const ICONS: Record<View, ReactNode> = {
  dashboard: (
    <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  ),
  transactions: (
    <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <line x1="8" y1="7" x2="20" y2="7" /><line x1="8" y1="12" x2="20" y2="12" /><line x1="8" y1="17" x2="20" y2="17" />
      <circle cx="4" cy="7" r="1" /><circle cx="4" cy="12" r="1" /><circle cx="4" cy="17" r="1" />
    </svg>
  ),
  review: (
    <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 4h6v3H9z" /><path d="M9 13l2 2 4-4" />
    </svg>
  ),
  upload: (
    <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2" />
    </svg>
  ),
};

function App() {
  const { t } = useTranslation();
  const [view, setView] = useState<View>("dashboard");
  const [company, setCompany] = useState<Company | null>(null);
  const [reviewTxs, setReviewTxs] = useState<Transaction[]>([]);

  useEffect(() => {
    getCompany().then(setCompany).catch(() => setCompany(null));
  }, []);

  const reloadReview = useCallback(() => {
    getReviewTransactions().then(setReviewTxs).catch(() => setReviewTxs([]));
  }, []);
  useEffect(() => { reloadReview(); }, [reloadReview]);

  const views: View[] = ["dashboard", "transactions", "review", "upload"];

  return (
    <div className="min-h-screen flex flex-col bg-slate-50 text-slate-900">
      <header className="sticky top-0 z-10 bg-white/80 backdrop-blur-md border-b border-slate-200">
        <div className="max-w-2xl mx-auto px-4 h-14 flex items-center gap-3">
          <Logo />
          <div className="flex-1 leading-tight min-w-0">
            <div className="font-bold tracking-tight">
              <span className="text-brand-amber">Conta</span>
              <span className="text-brand-green">Bu</span>
            </div>
            <div className="text-xs text-slate-500 truncate">{company?.name ?? "…"}</div>
          </div>
          <LanguageSwitcher />
        </div>
      </header>

      <main className="flex-1 w-full max-w-2xl mx-auto px-4 py-5 pb-24">
        {view === "dashboard" && (
          <Dashboard onUpload={() => setView("upload")} onSeeAll={() => setView("transactions")} />
        )}
        {view === "transactions" && <Transactions />}
        {view === "review" && <Review items={reviewTxs} reload={reloadReview} />}
        {view === "upload" && (
          <Upload onDone={() => { reloadReview(); setView("dashboard"); }} />
        )}
      </main>

      <nav className="fixed bottom-0 inset-x-0 bg-white/90 backdrop-blur-md border-t border-slate-200">
        <div className="max-w-2xl mx-auto grid grid-cols-4">
          {views.map((id) => {
            const active = view === id;
            const count = id === "review" ? reviewTxs.length : 0;
            return (
              <button
                key={id}
                onClick={() => setView(id)}
                className={`flex flex-col items-center gap-1 py-2.5 text-[11px] transition-colors ${
                  active ? "text-brand-green font-semibold" : "text-slate-400 hover:text-slate-600"
                }`}
              >
                <div className="relative">
                  {ICONS[id]}
                  {count > 0 && (
                    <span className="absolute -top-1.5 -end-2 bg-rose-500 text-white text-[9px] leading-none rounded-full px-1 py-0.5 min-w-4 text-center">
                      {count}
                    </span>
                  )}
                </div>
                {t(`nav.${id}`)}
              </button>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

export default App;
