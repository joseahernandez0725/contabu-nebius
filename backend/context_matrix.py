"""
ContaBu — el ⑤ con el contexto de la compra (28-oct, después de `no_web_matrix.py`). No cambia el producto ni los
archivos medidos: solo, mientras corre, el mensaje del ⑤ lleva además, por comercio, `booked_as` (cómo registró el
bookkeeper sus compras: la sección y la categoría, nunca un monto, una descripción ni un nombre) y una línea más en sus
instrucciones. La web queda ENCENDIDA para todos, incluido el ⑤: sigue viendo el tipo verificado.

Por qué: con la web, el ⑤ juzga el comercio y no la compra. Un tipo como "supermercado" hace creíble que "el negocio
podría comprar aquí" y la alerta se cae (7 de 12 en el libro real; 12 de 12 sin el tipo, pero con 8–10 falsas alarmas
etiquetadas, 28-oct). Aquí el tipo se queda y se le dice al ⑤ qué se registró.

🔴 La regla, escrita ANTES de correr (28-oct), contra las corridas del mismo día con la web (`w28`):
  1. Demo, 3 corridas: ninguna por debajo de la peor de `w28` en el examen ciego (14 de 22), el primer examen (30 de 32)
     y los Pendientes con regla correcta (41 de 91); falsas alarmas del ⑤ ≤ 2 en cada corrida.
  2. Libro real (examen 2), 3 corridas: más de 7 de los 12 errores escondidos en CADA corrida.
  3. Libro real: con las etiquetas de Jose (comercios nuevos se etiquetan antes de puntuar), la mediana de falsas
     alarmas del ⑤ ≤ 5, la de `nw28` (la web apagada para todos).
Si cumple las tres, queda como el siguiente paso validado; el producto NO cambia antes de la entrega.

    U=$(grep ^DATABASE_URL .env.real | cut -d= -f2-)
    DATABASE_URL="${U%/*}/contabu_real_examen2" .venv/bin/python context_matrix.py exam --exam 2 --n 3 --tag c28 --diag
    .venv/bin/python context_matrix.py demo --n 3 --ns c28
    .venv/bin/python real_matrix.py diag --tags w28 nw28 p28 c28 --score
"""
import json
import sys

import personal_use
import real_matrix

EXTRA_RULE = """
- "booked_as" says how the bookkeeper booked this merchant's purchases (section/category). A verified_business_type
  says what the store is, not what was bought: judge whether THIS business would buy what it was booked as at this
  kind of store. A general store (supermarket, department store, online marketplace) does not make a purchase
  "business" by itself."""


def purchase_context_on() -> None:
    inner = personal_use._user
    head, sep, tail = personal_use._SYSTEM.partition("- confidence:")
    personal_use._SYSTEM = head + EXTRA_RULE.lstrip("\n") + "\n" + sep + tail

    def _user(company, view, todo):
        user, refs = inner(company, view, todo)
        top, marker, rest = user.partition("merchants: ")
        merchants = json.loads(rest)
        groups = {f"M{i}": group for i, (_key, group) in enumerate(todo, 1)}
        for item in merchants:
            item["booked_as"] = sorted({f"{t.account_type.value}/{t.category}" for t in groups[item["ref"]]
                                        if t.account_type is not None})
        return top + marker + json.dumps(merchants, ensure_ascii=False), refs
    personal_use._user = _user
    real_matrix.CONDITION = "purchase_context"


if __name__ == "__main__":
    if "--web" in sys.argv:
        sys.exit("⛔ sin modo web aquí: quita --web")
    purchase_context_on()
    sys.exit(real_matrix.main())
