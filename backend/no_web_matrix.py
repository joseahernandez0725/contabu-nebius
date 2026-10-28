"""
ContaBu — ¿cuánto aporta Tavily? El mismo examen de `real_matrix.py`, con el verificador ② APAGADO (revisión externa,
26-oct). No cambia el producto ni los archivos medidos: solo, mientras corre, el ② no busca y la memoria web de la oficina
se ve vacía, así que ningún agente recibe un tipo de comercio de internet (el ⑤, el ①, el auditor ni el redactor).

Con `--only-personal` (28-oct) la web queda ENCENDIDA para todos menos el ⑤: el ② y la memoria funcionan como siempre,
y solo al mensaje del ⑤ le falta el tipo verificado. Es la propuesta de Jose ("el ⑤ con Tavily apagado"), medida
directamente en vez de deducirla de las otras dos condiciones.

La comparación justa es del MISMO día y con el MISMO código: se corren las condiciones con etiquetas propias.

    U=$(grep ^DATABASE_URL .env.real | cut -d= -f2-)
    R2="${U%/*}/contabu_real_examen2"
    # el libro real (R2, el examen del bookkeeper): con la web, sin la web, y sin la web solo para el ⑤:
    DATABASE_URL="$R2" .venv/bin/python real_matrix.py exam --exam 2 --n 3 --tag w28 --diag
    DATABASE_URL="$R2" .venv/bin/python no_web_matrix.py exam --exam 2 --n 3 --tag nw28 --diag
    DATABASE_URL="$R2" .venv/bin/python no_web_matrix.py --only-personal exam --exam 2 --n 3 --tag p28 --diag
    # los libros del demo (D1 y D2, el ciego), las mismas tres condiciones:
    .venv/bin/python real_matrix.py demo --n 3 --ns w28
    .venv/bin/python no_web_matrix.py demo --n 3 --ns nw28
    .venv/bin/python no_web_matrix.py --only-personal demo --n 3 --ns p28
    # el Excel para etiquetar (sin IA), y con las etiquetas puestas, la precisión del ⑤:
    .venv/bin/python real_matrix.py diag --tags w28 nw28 p28
    .venv/bin/python real_matrix.py diag --tags w28 nw28 p28 --score

Los argumentos son los de `real_matrix.py`; las filas quedan en el mismo registro privado, con su etiqueta y su
`web_condition`. `--diag` guarda aparte (600) lo que el ⑤ recibió y opinó: trae comercios y la clave, solo para Jose.
"""
import sys
from collections import Counter

import merchant_verifier as mv
import personal_use
import real_matrix


def web_off() -> None:
    """El ② apagado: ni búsquedas ni memoria. `web_by_tx` lee la memoria con `cached`, así que llega vacía a todos."""
    mv.cached = lambda session, company: {}

    def no_verify(session, company, **_kw):
        return mv.VerifyResult(items=[], searched=0, from_cache=0, skipped=Counter(), credits=0, outcome="web_off")

    mv.verify_flagged = no_verify
    mv.search_one = None          # si algo intentara buscar uno solo, que falle en vez de buscar
    real_matrix.CONDITION = "without"


def web_off_for_personal() -> None:
    """Solo el ⑤ sin el tipo: su mensaje se arma con la evidencia web de la vista vacía; los demás la siguen viendo."""
    inner = personal_use._user

    def _user(company, view, todo):
        web, view.web = view.web, {}
        try:
            return inner(company, view, todo)
        finally:
            view.web = web
    personal_use._user = _user
    real_matrix.CONDITION = "personal_off"


if __name__ == "__main__":
    if "--web" in sys.argv:
        sys.exit("⛔ sin el ② no hay modo web: quita --web")
    if "--only-personal" in sys.argv:
        sys.argv.remove("--only-personal")
        web_off_for_personal()
    else:
        web_off()
    sys.exit(real_matrix.main())
