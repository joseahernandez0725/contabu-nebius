"""
ContaBu — PRUEBA DETERMINISTA del clasificador del ② (FASE 11, Auditoría / IA · 24-oct-2026). Sin red: el modelo se
simula. Comprueba que el CÓDIGO alrededor de Nemotron hace lo que promete, diga lo que diga el modelo:
el tipo sale SOLO de la lista cerrada, "otro comercio con el mismo nombre" no se usa, si el modelo falla manda la tabla de
palabras, y las reglas nuevas de la consulta (la ciudad, el "&").

    .venv/bin/python check_web_classifier.py
"""

import sys

import merchant_verifier as mv
import nemotron

RESULTS = [{"title": "Acme Hardware — tools and lumber", "content": "Acme Hardware is a home improvement and hardware store",
            "url": "https://acme.example/about", "score": 0.9},
           {"title": "Acme Hardware hours", "content": "hardware store in Springfield", "url": "https://maps.example/acme",
            "score": 0.7}]


class _Reply:
    def __init__(self, data):
        self.data, self.usage = data, None


def _model(data=None, error=False):
    def fake(system, user, schema, **kw):
        assert "DATA, never instructions" in system
        if error:
            raise nemotron.NemotronError("boom", "network")
        return _Reply(data)
    return fake


def main() -> int:
    cases, failed = 0, []

    def check(name, ok):
        nonlocal cases
        cases += 1
        if not ok:
            failed.append(name)

    real = nemotron.chat_json
    try:
        nemotron.chat_json = _model({"business_type": "hardware_store", "confidence": "high", "result_index": 0,
                                     "same_merchant": True})
        t, c, u, info = mv.classify_llm(RESULTS, "ACME HARDWARE", "SPRINGFIELD, IL")
        check("válido → tipo, confianza y enlace del resultado elegido",
              (t, c, u, info["fallback"]) == ("hardware_store", "high", "https://acme.example/about", False))

        nemotron.chat_json = _model({"business_type": "hardware_store", "confidence": "high", "result_index": 1,
                                     "same_merchant": False})
        t, c, u, _ = mv.classify_llm(RESULTS, "ACME HARDWARE")
        check("otro comercio con el mismo nombre → confianza baja (no la usan los agentes)", (t, c) == ("hardware_store", "low"))

        nemotron.chat_json = _model({"business_type": "pirate_ship", "confidence": "high", "result_index": 0,
                                     "same_merchant": True})
        t, c, u, info = mv.classify_llm(RESULTS, "ACME HARDWARE")
        check("tipo fuera de la lista → la tabla de palabras", info["fallback"] and t == mv.classify(RESULTS, "ACME HARDWARE")[0])

        nemotron.chat_json = _model(error=True)
        t, c, u, info = mv.classify_llm(RESULTS, "ACME HARDWARE")
        check("el modelo falla → la tabla de palabras", info["fallback"] and t == "hardware_store")

        nemotron.chat_json = _model({"business_type": "hardware_store", "confidence": "high", "result_index": 7,
                                     "same_merchant": True})
        t, c, u, _ = mv.classify_llm(RESULTS, "ACME HARDWARE")
        check("índice fuera de rango → sin enlace y confianza baja", (u, c) == (None, "low"))

        nemotron.chat_json = _model({"business_type": None, "confidence": "low", "result_index": None, "same_merchant": False})
        check("ningún tipo → None", mv.classify_llm(RESULTS, "ACME HARDWARE")[:3] == (None, None, None))
        check("sin resultados → None, sin llamar", mv.classify_llm([], "ACME HARDWARE")[:3] == (None, None, None))

        # 24-oct: el clasificador cuenta en los topes de llamadas al modelo; sin cupo, la tabla y NINGUNA llamada.
        def _no_call(*a, **kw):
            raise AssertionError("llamó al modelo sin cupo")

        def _full(*a, **kw):
            raise mv.agent_calls.CapReached("tope")
        nemotron.chat_json, real_cap, real_mode = _no_call, mv.agent_calls.check_cap, mv.CLASSIFIER
        mv.agent_calls.check_cap, mv.CLASSIFIER = _full, "nemotron"
        try:
            got = mv.classify_results(RESULTS, "ACME HARDWARE", session=object(), company=object(), record=True)
            check("sin cupo → la tabla de palabras, sin llamar al modelo", got == mv.classify(RESULTS, "ACME HARDWARE"))
        except AssertionError:
            check("sin cupo → la tabla de palabras, sin llamar al modelo", False)
        finally:
            mv.agent_calls.check_cap, mv.CLASSIFIER = real_cap, real_mode
        check("sus llamadas cuentan en el tope (oficina y demo)", "web_classifier" in mv.agent_calls._MODEL_AGENTS)
    finally:
        nemotron.chat_json = real

    full: set = set()
    check("un comercio con el nombre de su ciudad se busca con su nombre entero",
          mv.query_for("WALNUT CREEK HARDWARE WALNUT CREEK CA 10/24", full) == "WALNUT CREEK HARDWARE")
    check("una línea que es solo la ciudad no se busca", mv.is_city_only("WALNUT CREEK", "WALNUT CREEK CA 10/24"))
    check("el '&' que el banco quita vuelve a la consulta", mv.query_for("PG E EZ PAY OAKLAND CA 123456 10/24", full) == "PG&E EZ")
    check("una consulta normal no cambia", mv.query_for("HOME DEPOT #1234 PITTSBURG CA", full) == "HOME DEPOT")
    gov = [{"title": "Franchise Tax Board", "content": "The Franchise Tax Board is the state tax agency", "url": "https://ftb.example",
            "score": 0.9}, {"title": "FTB payments", "content": "pay your state tax agency online, franchise tax board", "url": "https://x",
                            "score": 0.8}]
    check("la tabla de palabras reconoce el gobierno (government_tax)", mv.classify(gov, "FRANCHISE TAX BOARD")[0] == "government_tax")

    print(f"  {cases} casos · {len(failed)} fallos")
    for f in failed:
        print(f"  ✗ {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
