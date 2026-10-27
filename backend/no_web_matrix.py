"""
ContaBu — ¿cuánto aporta Tavily? El mismo examen de `real_matrix.py`, con el verificador ② APAGADO (revisión externa,
26-oct). No cambia el producto ni los archivos medidos: solo, mientras corre, el ② no busca y la memoria web de la oficina
se ve vacía, así que ningún agente recibe un tipo de comercio de internet (el ⑤, el ①, el auditor ni el redactor).

La comparación justa es del MISMO día y con el MISMO código: se corren las dos condiciones con etiquetas propias.

    U=$(grep ^DATABASE_URL .env.real | cut -d= -f2-)
    # el libro real (R2, el examen del bookkeeper), con y sin el ②:
    DATABASE_URL="${U%/*}/contabu_real_examen2" .venv/bin/python real_matrix.py exam --exam 2 --n 3 --tag w26
    DATABASE_URL="${U%/*}/contabu_real_examen2" .venv/bin/python no_web_matrix.py exam --exam 2 --n 3 --tag nw26
    # los libros del demo (D1 y D2, el ciego), con y sin el ②:
    .venv/bin/python real_matrix.py demo --n 3 --ns w26
    .venv/bin/python no_web_matrix.py demo --n 3 --ns nw26

Los argumentos son los de `real_matrix.py`; las filas quedan en el mismo registro privado, con su etiqueta.
"""
import sys
from collections import Counter

import merchant_verifier as mv
import real_matrix


def web_off() -> None:
    """El ② apagado: ni búsquedas ni memoria. `web_by_tx` lee la memoria con `cached`, así que llega vacía a todos."""
    mv.cached = lambda session, company: {}

    def no_verify(session, company, **_kw):
        return mv.VerifyResult(items=[], searched=0, from_cache=0, skipped=Counter(), credits=0, outcome="web_off")

    mv.verify_flagged = no_verify
    mv.search_one = None          # si algo intentara buscar uno solo, que falle en vez de buscar


if __name__ == "__main__":
    if "--web" in sys.argv:
        sys.exit("⛔ sin el ② no hay modo web: quita --web")
    web_off()
    sys.exit(real_matrix.main())
