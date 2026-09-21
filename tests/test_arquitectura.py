"""Las capas no se importan al revés ni en círculo.

Un refactor como éste se deshace solo si nadie vigila las flechas: basta un
`from app.routers... import` dentro de `app/calculos/` para volver al fichero
de 2.000 líneas por la puerta de atrás, y nada lo delataría porque los tests
seguirían pasando.

Las capas, de dentro a fuera:

    app/db.py     ← el engine, no depende de nada
    app/calculos/ ← HOJA PURA: no importa nada de la app salvo a sí misma
    app/erp/      ← lee del ERP; puede usar el vocabulario de calculos
    app/services/ ← combina lectura y cálculo
    app/routers/  ← HTTP
    app/main.py   ← monta la app

`erp → calculos` está PERMITIDO y es deliberado: `leer_ausencias` necesita
saber a qué hora empieza y acaba la jornada para decidir si una ausencia
parcial la tapa entera, y duplicar el 7 y el 15 en dos sitios es peor que la
flecha. Lo que no se permite es la dirección contraria —que el cálculo dependa
de la lectura—, porque es la que haría imposible probar el motor sin un ERP.
"""
import ast
import collections
import pathlib

RAIZ = pathlib.Path(__file__).resolve().parent.parent / "app"

#  Cuanto más alto, más "de fuera" es la capa. Una capa solo puede importar
#  de las suyas o de más abajo.
CAPA = {
    "app.db": 0,
    "app.calculos": 1,
    "app.erp": 2,
    "app.services": 3,
    "app.routers": 4,
    "app.main": 5,
}


def _capa(modulo: str) -> int:
    for prefijo, nivel in CAPA.items():
        if modulo == prefijo or modulo.startswith(prefijo + "."):
            return nivel
    return 99


def _grafo() -> dict:
    grafo = collections.defaultdict(set)
    for fichero in RAIZ.rglob("*.py"):
        modulo = ".".join(fichero.relative_to(RAIZ.parent).with_suffix("").parts)
        modulo = modulo.removesuffix(".__init__")
        arbol = ast.parse(fichero.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.module.startswith("app"):
                grafo[modulo].add(nodo.module)
            elif isinstance(nodo, ast.Import):
                for alias in nodo.names:
                    if alias.name.startswith("app"):
                        grafo[modulo].add(alias.name)
    return grafo


def test_ninguna_capa_importa_de_una_mas_externa():
    invertidas = [f"{o} -> {d}" for o, destinos in _grafo().items()
                  for d in destinos if _capa(d) > _capa(o)]
    assert not invertidas, "capas invertidas: " + ", ".join(sorted(invertidas))


def test_los_calculos_no_saben_que_existe_el_erp():
    """La regla que sostiene todo lo demás: el motor se prueba sin ERP."""
    malas = [f"{o} -> {d}" for o, destinos in _grafo().items()
             if o.startswith("app.calculos")
             for d in destinos if not d.startswith("app.calculos")]
    assert not malas, "app/calculos/ solo puede importar de sí mismo: " + ", ".join(sorted(malas))


def test_no_hay_ciclos_de_importacion():
    grafo, ciclos, visto, pila = _grafo(), [], set(), []

    def recorrer(nodo):
        if nodo in pila:
            ciclos.append(" -> ".join(pila[pila.index(nodo):] + [nodo]))
            return
        if nodo in visto:
            return
        visto.add(nodo)
        pila.append(nodo)
        for destino in sorted(grafo.get(nodo, ())):
            recorrer(destino)
        pila.pop()

    for nodo in sorted(grafo):
        recorrer(nodo)
    assert not ciclos, "ciclos de importación: " + ", ".join(sorted(set(ciclos)))


def test_los_routers_no_se_llaman_entre_si():
    """`/api/plan` llamaba a la función del endpoint `/api/items`. Ahora las dos
    pasan por `app.services.produccion` y ninguna depende de ejecutar la otra."""
    entre_routers = [f"{o} -> {d}" for o, destinos in _grafo().items()
                     if o.startswith("app.routers")
                     for d in destinos if d.startswith("app.routers")]
    assert not entre_routers, "routers acoplados: " + ", ".join(sorted(entre_routers))
