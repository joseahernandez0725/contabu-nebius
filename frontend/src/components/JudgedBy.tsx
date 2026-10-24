import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { currentCompanyId, getFindingJudges, type FindingJudge } from "../api";

// QUIÉN JUZGÓ ESTE HALLAZGO, DE VERDAD (FASE 13, Firm · 23-oct, para el recorrido guiado del demo): el modelo
// sale de `agentcall`, no de una tabla escrita a mano. El ① juzga casi todo; el de una nota lo arma el ⑥
// traductor de notas, el de "¿personal?" el ⑤, y la nómina o la cobertura las encuentra el auditor (código, sin IA).
// Se pide una vez por libro y se vuelve a pedir si aparece un hallazgo que no estaba (una revisión nueva).

let cache: { company: number | null; judges: Record<string, FindingJudge> } = { company: null, judges: {} };
let pending: Promise<void> | null = null;

function load(force: boolean): Promise<void> {
  const company = currentCompanyId();
  if (!force && cache.company === company) return Promise.resolve();
  if (!pending) {
    pending = getFindingJudges()
      .then((judges) => { cache = { company, judges }; })
      .catch(() => { cache = { company, judges: {} }; })
      .finally(() => { pending = null; });
  }
  return pending;
}

export default function JudgedBy({ findingId }: { findingId: number }) {
  const { t } = useTranslation();
  const [judge, setJudge] = useState<FindingJudge | undefined>(() =>
    cache.company === currentCompanyId() ? cache.judges[String(findingId)] : undefined);

  useEffect(() => {
    let alive = true;
    const stale = cache.company !== currentCompanyId() || !(String(findingId) in cache.judges);
    void load(stale).then(() => { if (alive) setJudge(cache.judges[String(findingId)]); });
    return () => { alive = false; };
  }, [findingId]);

  if (!judge) return null;
  const text = judge.by === "code" ? t("demoGuide.judged.code")
    : t(`demoGuide.judged.${judge.by}`, { model: judge.model ?? "Nemotron" });
  return (
    <span className="inline-block text-[11px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600" data-tour="judged-by">
      {text}
    </span>
  );
}
