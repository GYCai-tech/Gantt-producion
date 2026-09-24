"""Qué hace `app/erp/` cuando el ERP contesta y qué hace cuando no.

Lo que se vigila aquí no es el SQL —que no se puede probar sin ERP— sino el
REPARTO DE ERRORES, que es lo que distingue una pantalla degradada de una
pantalla rota, y la caché, que si se duplica hace el doble de consultas sin
que nadie lo note.

No se toca el ERP: se le pone delante un engine de mentira.
"""
from datetime import date, datetime

import pytest
from sqlalchemy.exc import OperationalError

from app.erp import cache, consultas, lecturas
from app.erp.cliente import ErpNoDisponible

FALLO = OperationalError("select 1", {}, Exception("el ERP no responde"))


# ─────────────────────────────────────────────────────────────────────
#  Un ERP de mentira
# ─────────────────────────────────────────────────────────────────────

class _Mappings(list):
    def all(self):
        return list(self)


class _Resultado:
    def __init__(self, filas):
        self._filas = filas

    def mappings(self):
        return _Mappings(self._filas)


class _Conexion:
    def __init__(self, responder, registro):
        self._responder, self._registro = responder, registro

    def execute(self, sentencia, params=None):
        self._registro.append(str(sentencia))
        return _Resultado(self._responder(str(sentencia), params or {}))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Engine:
    def __init__(self, responder, registro):
        self._responder, self._registro = responder, registro

    def connect(self):
        return _Conexion(self._responder, self._registro)


@pytest.fixture
def erp(monkeypatch):
    """Deja `app/erp/` hablando con un ERP de mentira y la caché en blanco."""
    estado = {"responder": lambda sql, params: [], "consultas": []}

    import app.erp.cliente as cliente
    monkeypatch.setattr(
        cliente, "get_erp_engine",
        lambda: _Engine(lambda s, p: estado["responder"](s, p), estado["consultas"]))

    cache._cache_estima.update(
        ts=None, teoricos={}, medias={"articulo": {}, "trabajo": {}, "maquina": {}},
        montajes={"trabajo": {}, "maquina": {}})
    cache._cache_semaforo.update(ts=None, mapa={})
    yield estado
    cache._cache_estima.update(
        ts=None, teoricos={}, medias={"articulo": {}, "trabajo": {}, "maquina": {}},
        montajes={"trabajo": {}, "maquina": {}})
    cache._cache_semaforo.update(ts=None, mapa={})


def _revienta(sql, params):
    raise FALLO


# ─────────────────────────────────────────────────────────────────────
#  Lo que NO se puede degradar sube como ErpNoDisponible (hoy: 503)
# ─────────────────────────────────────────────────────────────────────

def test_sin_erp_las_lecturas_imprescindibles_no_se_inventan_nada(erp):
    """Sin líneas, sin cola y sin censo no hay pantalla que pintar: el fallo
    tiene que subir para que la capa HTTP conteste 503."""
    erp["responder"] = _revienta
    for llamada in (lambda: lecturas.leer_lineas(date(2026, 9, 7), date(2026, 9, 7)),
                    lambda: lecturas.leer_abiertas(datetime(2026, 9, 7, 12)),
                    lambda: lecturas.leer_cola(),
                    lambda: lecturas.leer_censo_empleados(),
                    lambda: lecturas.leer_censo_maquinas(),
                    lambda: lecturas.leer_areas_empleado()):
        with pytest.raises(ErpNoDisponible):
            llamada()


def test_el_fallo_del_avance_se_distingue_del_resto(erp):
    """El avance tiene su propio mensaje: sin él, lo que queda por hacer sería
    una cifra inventada, y el usuario tiene que saber cuál de las dos falló."""
    erp["responder"] = _revienta
    with pytest.raises(ErpNoDisponible) as e:
        lecturas.leer_avance([{"idorden": 1}], datetime(2026, 9, 7, 12))
    assert str(e.value) == "No se pudo consultar el avance del ERP"


def test_el_avance_sin_lineas_no_pregunta_al_erp(erp):
    """Sin órdenes no hay nada que agregar, y el `IN ()` ni siquiera es SQL
    válido."""
    erp["responder"] = _revienta
    assert lecturas.leer_avance([], datetime(2026, 9, 7, 12)) == {}
    assert erp["consultas"] == []


# ─────────────────────────────────────────────────────────────────────
#  Lo que SÍ se degrada
# ─────────────────────────────────────────────────────────────────────

def test_sin_paradas_el_gantt_se_pinta_igual(erp):
    """Una anotación que no se puede leer es una marca de menos."""
    erp["responder"] = _revienta
    assert lecturas.leer_paradas() == {}


def test_sin_ausencias_el_gantt_se_pinta_igual(erp):
    """PORTALHR es otra base y puede faltar el permiso; eso no rompe la vista."""
    erp["responder"] = _revienta
    assert lecturas.leer_ausencias(date(2026, 9, 7)) == {}


def test_el_escandallo_caido_reutiliza_el_ultimo_leido(erp):
    """Es mejor estimar con el escandallo de hace un rato que marcar de golpe
    todas las barras como "sin tiempo"."""
    erp["responder"] = lambda sql, params: [
        {"idorden": "1", "idbono": "10", "min_pieza": 2.0, "setup_min": 31}]
    assert cache.cargar_teoricos() == {(1, 10): (31.0, 2.0)}

    erp["responder"] = _revienta
    assert cache.cargar_teoricos() == {(1, 10): (31.0, 2.0)}


def test_los_historicos_caidos_reutilizan_la_cache_caducada(erp):
    """Y caducada de verdad: se fuerza el TTL para que no sea el atajo del TTL
    el que salve la llamada, sino la degradación."""
    def responder(sql, params):
        if "WITH mano_obra AS" in sql:
            return [{"idorden": 1, "idbono": 10, "min_pieza": 2.0, "setup_min": 0}]
        if "WITH bono_min AS" in sql:
            return [{"idarticulo": " A ", "idtrabajo": 1, "matricula": "M1",
                     "n": 5, "minutos": 500, "piezas": 250}]
        if "AVG(CAST(DATEDIFF" in sql:
            return [{"matricula": "M1", "idtrabajo": 1, "n": 4, "media": 11.0}]
        return []

    erp["responder"] = responder
    _, medias, montajes = cache.cargar_estimaciones()
    assert medias["articulo"]["A"]["piezas"] == 250.0   # la clave llega sin espacios
    assert montajes["maquina"]["M1"] == {"n": 4, "minutos": 44.0}

    erp["responder"] = _revienta
    cache._cache_estima["ts"] = datetime(2000, 1, 1)
    teoricos, medias2, montajes2 = cache.cargar_estimaciones()
    assert medias2 == medias and montajes2 == montajes
    assert teoricos == {(1, 10): (0.0, 2.0)}           # el escandallo también


def test_el_semaforo_caido_reutiliza_la_cache_caducada(erp):
    """Mejor ordenar la cola con colores de hace unos minutos que servirla en
    un orden que el operario no puede seguir."""
    erp["responder"] = lambda sql, params: [
        {"idorden": 5, "idbono": 10, "idempleado": 3, "color": "255051051"}]
    assert cache.cargar_semaforo() == {(5, 10, 3): "bloqueada"}

    erp["responder"] = _revienta
    cache._cache_semaforo["ts"] = datetime(2000, 1, 1)
    assert cache.cargar_semaforo() == {(5, 10, 3): "bloqueada"}


def test_un_color_desconocido_no_esconde_trabajo(erp):
    """Es preferible ofrecer trabajo de más que esconderlo porque la función
    del ERP devolvió algo que no está en el catálogo."""
    erp["responder"] = lambda sql, params: [
        {"idorden": 5, "idbono": 10, "idempleado": 3, "color": "vete a saber"}]
    assert cache.cargar_semaforo() == {(5, 10, 3): "disponible"}


# ─────────────────────────────────────────────────────────────────────
#  La caché es UNA
# ─────────────────────────────────────────────────────────────────────

def test_dentro_del_ttl_no_se_vuelve_a_preguntar(erp):
    """Si alguien acaba con una copia de la caché, esto se dispara: la app
    haría el doble de consultas al ERP sin que nadie lo note."""
    erp["responder"] = lambda sql, params: []
    cache.cargar_estimaciones()
    consultas_primera = list(erp["consultas"])
    erp["consultas"].clear()

    cache.cargar_estimaciones()
    # El escandallo SÍ se vuelve a leer (va en vivo a propósito); las medias y
    # los montajes, no.
    assert any("WITH mano_obra AS" in q for q in consultas_primera)
    assert [q for q in erp["consultas"] if "WITH mano_obra AS" in q]
    assert not [q for q in erp["consultas"] if "WITH bono_min AS" in q]
    assert not [q for q in erp["consultas"] if "AVG(CAST(DATEDIFF" in q]


def test_cargar_estimaciones_devuelve_tambien_los_montajes(erp):
    """Los montajes salen por la firma y no por una global: `minutos_montaje`
    los recibe como parámetro."""
    erp["responder"] = lambda sql, params: []
    devuelto = cache.cargar_estimaciones()
    assert len(devuelto) == 3
    teoricos, medias, montajes = devuelto
    assert set(medias) == {"articulo", "trabajo", "maquina"}
    assert set(montajes) == {"trabajo", "maquina"}


# ─────────────────────────────────────────────────────────────────────
#  Forma de los datos
# ─────────────────────────────────────────────────────────────────────

def _fila_linea(idlinea, hfinal=None, area=" CHAPA "):
    return {"idorden": 1, "idbono": 10, "idlinea": idlinea, "idoperacion": 0,
            "idempleado": 7, "nombre": " Ana ", "apellidos": "Boo",
            "fecha": "F", "hinicial": "HI", "hfinal": hfinal, "matricula": "M1",
            "idtrabajo": 3, "descrip_maquina": "maq", "area": area,
            "piezas_a_fabricar": 10, "idarticulo_salida": "A",
            "descrip_salida": "art"}


def test_una_linea_con_dos_articulos_de_salida_es_UNA_barra():
    """El JOIN con Ordenes_Bonos_Salidas repite fila; en el Gantt saldrían dos
    barras idénticas superpuestas."""
    lineas = lecturas.normalizar_lineas([_fila_linea(1), _fila_linea(1), _fila_linea(2)])
    assert [l["idlinea"] for l in lineas] == [1, 2]


def test_una_linea_sin_hora_de_fin_sigue_abierta():
    [abierta] = lecturas.normalizar_lineas([_fila_linea(1, hfinal=None)])
    [cerrada] = lecturas.normalizar_lineas([_fila_linea(2, hfinal="HF")])
    assert abierta["abierta"] is True and abierta["fin"] is None
    assert cerrada["abierta"] is False and cerrada["fin"] == "HF"


def test_el_area_en_blanco_no_es_un_area():
    [l] = lecturas.normalizar_lineas([_fila_linea(1, area="   ")])
    assert l["area"] is None


def test_quien_no_tiene_nombre_en_la_ficha_sale_por_su_numero():
    assert lecturas.nombre_completo(
        {"nombre": None, "apellidos": "  ", "idempleado": 42}) == "#42"
    assert lecturas.nombre_completo(
        {"nombre": " Ana ", "apellidos": "Boo", "idempleado": 1}) == "Ana Boo"


@pytest.mark.parametrize("ini, fin, esperado", [
    (None, None,       None),                        # sin horas: día entero
    ("06:00", "15:30", None),                        # tapa la jornada: día entero
    ("07:00", "10:00", "entra a las 10:00"),
    ("10:00", "15:00", "se marcha a las 10:00"),
    ("09:00", "11:00", "fuera de 09:00 a 11:00"),
])
def test_una_ausencia_parcial_se_cuenta_por_lo_que_queda_de_jornada(ini, fin, esperado):
    """"07:00–10:00" describe el hueco; lo que hace falta saber es que ese día
    el operario entra a las 10:00."""
    assert lecturas.cuando_falta(ini, fin) == esperado


def test_la_cola_se_deduplica_por_bono_y_operario(erp):
    def responder(sql, params):
        if "persFTrazaordenesOperariosColor" in sql:
            return []
        fila = {"idorden": 5, "idbono": 10, "idempleado": 3, "nombre": "chus",
                "apellidos": "Ávila", "ordenar": None, "matricula": " M1 ",
                "descrip_maquina": "maq", "area": " CHAPA ", "descrip_salida": "art",
                "idarticulo_salida": "A", "idtrabajo": 1, "objetivo": 100,
                "fabricadas": None, "idestado": 1, "ultimo_fichaje": None,
                "montajes": 2}
        return [fila, dict(fila, idarticulo_salida="B")]

    erp["responder"] = responder
    [bono] = lecturas.leer_cola()
    assert bono["semaforo"] == "disponible"    # sin color, se ofrece igual
    assert bono["ordenar"] == 0                # `ordenar` a NULL es la posición 0
    assert bono["matricula"] == "M1"
    assert bono["fabricadas"] == 0.0
    assert bono["arrancado"] is True           # idestado = 1
    assert bono["montado"] is True             # ya se fichó la preparación


def test_las_consultas_llevan_las_constantes_que_toca(erp):
    """Los signos de los parámetros de fecha son fáciles de perder al mover
    código: van en negativo porque son DATEADD hacia atrás."""
    vistos = {}

    def responder(sql, params):
        vistos.update(params)
        return []

    erp["responder"] = responder
    lecturas.leer_areas_empleado()
    assert vistos == {"dias": -consultas.AREAS_RECIENTES_DIAS}

    vistos.clear()
    lecturas.leer_censo_empleados()
    assert vistos == {"departamento": consultas.DEPARTAMENTO_PRODUCCION}

    vistos.clear()
    lecturas.leer_cola()
    assert vistos == {"dias_arrancado": -consultas.DIAS_BONO_ARRANCADO,
                      "horas_viva": -consultas.HORAS_LINEA_VIVA}


def test_el_empleado_comodin_del_erp_no_es_un_operario():
    """El IdEmpleado 0 es "Empleado Prueba0 (Sin Definir)", el comodín de AHORA.
    Salía en todas las pantallas como un operario más; producción pidió quitarlo."""
    from app.erp import consultas
    assert "IdEmpleado <> 0" in consultas.SQL_CENSO_EMPLEADOS
