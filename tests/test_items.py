"""La ventana consultada no cambia la ocupación real que necesita la cola."""
from datetime import date, datetime

from app.routers import api
from tests.test_cola import bono, MEDIAS

AHORA = datetime(2026, 9, 7, 12)


class Reloj(datetime):
    @classmethod
    def now(cls, tz=None):
        return AHORA


class Fecha(date):
    @classmethod
    def today(cls):
        return AHORA.date()


def test_navegar_a_manana_conserva_el_bono_activo_de_hoy(monkeypatch):
    monkeypatch.setattr(api, 'datetime', Reloj)
    monkeypatch.setattr(api, 'date', Fecha)
    monkeypatch.setattr(api, '_leer_lineas', lambda *args: [])
    activa = dict(bono(), idlinea=1, idoperacion=0, abierta=True,
                  fecha=AHORA, inicio=AHORA.replace(hour=11), fin=None)
    monkeypatch.setattr(api, '_leer_abiertas', lambda *args: [activa])
    monkeypatch.setattr(api, '_cargar_estimaciones', lambda: ({(1, 10): (1, 5), (2, 10): (1, 1)}, MEDIAS))
    monkeypatch.setattr(api, '_avance_por_bono', lambda *args: {
        (1, 10): {'minutos': 70, 'min_produccion': 10, 'min_montaje': 60,
                  'piezas': 10, 'operarios': 1, 'montando': 1},
    })
    monkeypatch.setattr(api, '_leer_cola', lambda: [bono(2)])
    for vista in ('empleado', 'maquina'):
        items = api.get_items(vista=vista, desde=datetime(2026, 9, 8),
                              hasta=datetime(2026, 9, 9))
        real = next(i for i in items if i['tipo'] == 'real')
        previsto = next(i for i in items if i['tipo'] == 'programado')
        assert real['end'] == datetime(2026, 9, 8, 11, 30)
        assert previsto['start'] == real['end']


def test_abiertas_usa_hora_real_y_no_fecha_de_grabacion(monkeypatch):
    capturado = {}
    def leer(query, params):
        capturado.update(query=query, params=params)
        return []
    monkeypatch.setattr(api, '_erp', leer)
    assert api._leer_abiertas(AHORA) == []
    assert 'obl.Hfinal IS NULL' in capturado['query']
    # El IdEstado de la LINEA es transitorio (1 abierta, 2 cerrada), no dice si
    # la linea vale: filtrando por el se perdia todo el trabajo ya terminado.
    assert 'obl.IdEstado =' not in capturado['query']
    assert 'obl.Hinicial BETWEEN :limite AND :ahora' in capturado['query']
    assert 'CAST(obl.Fecha AS date) BETWEEN' not in capturado['query']
    assert capturado['params']['limite'] == datetime(2026, 9, 6, 12)


def test_fichaje_antiguo_sin_cerrar_no_se_proyecta_como_trabajo_actual(monkeypatch):
    monkeypatch.setattr(api, 'datetime', Reloj)
    monkeypatch.setattr(api, 'date', Fecha)
    antigua = dict(bono(), idlinea=1, idoperacion=0, abierta=True,
                   fecha=datetime(2026, 9, 4, 11), inicio=datetime(2026, 9, 4, 11), fin=None)
    monkeypatch.setattr(api, '_leer_lineas', lambda *args: [antigua])
    monkeypatch.setattr(api, '_leer_abiertas', lambda *args: [])
    monkeypatch.setattr(api, '_cargar_estimaciones', lambda: ({(1, 10): (1, 5)}, MEDIAS))
    monkeypatch.setattr(api, '_avance_por_bono', lambda *args: {})
    monkeypatch.setattr(api, '_leer_cola', lambda: [])
    items = api.get_items(vista='empleado', desde=datetime(2026, 9, 4),
                          hasta=datetime(2026, 9, 9))
    assert items[0]['tipo'] == 'parcial'
    assert items[0]['en_curso'] is False
    assert items[0]['end'] == datetime(2026, 9, 4, 15)
    assert 'fin_estimado' not in items[0]


def test_navegar_al_futuro_no_devuelve_barras_que_ya_terminaron(monkeypatch):
    monkeypatch.setattr(api, 'datetime', Reloj)
    monkeypatch.setattr(api, 'date', Fecha)
    monkeypatch.setattr(api, '_leer_lineas', lambda *args: [])
    activa = dict(bono(cantidad=10), idlinea=1, idoperacion=0, abierta=True,
                  fecha=AHORA, inicio=AHORA.replace(hour=11), fin=None)
    monkeypatch.setattr(api, '_leer_abiertas', lambda *args: [activa])
    monkeypatch.setattr(api, '_cargar_estimaciones', lambda: ({(1, 10): (1, 1)}, MEDIAS))
    monkeypatch.setattr(api, '_avance_por_bono', lambda *args: {
        (1, 10): {'minutos': 1, 'min_produccion': 1, 'min_montaje': 0,
                  'piezas': 1, 'operarios': 1, 'montando': 1},
    })
    monkeypatch.setattr(api, '_leer_cola', lambda: [])
    assert api.get_items(vista='empleado', desde=datetime(2026, 9, 8),
                         hasta=datetime(2026, 9, 9)) == []


def test_el_area_de_una_barra_es_la_de_su_bono_no_la_de_quien_lo_hace(monkeypatch):
    """Javier Atanes tiene 799 lineas de CHAPA y 8 de ESTRUCTURAS en 90 dias,
    asi que el filtro por areas del OPERARIO le colaba en ESTRUCTURAS la barra
    del bono 6552/60, que es de CHAPA. El area viaja con la barra."""
    monkeypatch.setattr(api, 'datetime', Reloj)
    monkeypatch.setattr(api, 'date', Fecha)
    abierta = dict(bono(), idlinea=1, idoperacion=0, abierta=True, area='CHAPA',
                   fecha=AHORA, inicio=AHORA.replace(hour=11), fin=None)
    monkeypatch.setattr(api, '_leer_lineas', lambda *a: [])
    monkeypatch.setattr(api, '_leer_abiertas', lambda *a: [abierta])
    monkeypatch.setattr(api, '_cargar_estimaciones', lambda: ({(1, 10): (1, 5)}, MEDIAS))
    monkeypatch.setattr(api, '_avance_por_bono', lambda *a: {
        (1, 10): {'minutos': 10, 'min_produccion': 10, 'min_montaje': 0,
                  'piezas': 99, 'operarios': 1, 'montando': 1},     # casi terminada: deja hueco hoy
    })
    monkeypatch.setattr(api, '_leer_cola', lambda: [dict(bono(2), cantidad=5, area='ESTRUCTURAS')])

    items = api.get_items(vista='empleado')
    real = next(i for i in items if i['tipo'] == 'real')
    cola = next(i for i in items if i['tipo'] == 'programado')

    # Mismo operario, dos areas distintas: cada barra lleva la suya.
    assert real['recurso_id'] == cola['recurso_id']
    assert real['area'] == 'CHAPA'
    assert cola['area'] == 'ESTRUCTURAS'


def _trozo(inicio, fin, montaje=False, abierta=False):
    return dict(bono(), idlinea=hash((inicio, fin)) % 9999, idoperacion=1 if montaje else 0,
                abierta=abierta, fecha=inicio, inicio=inicio, fin=None if abierta else fin)


def _montar(monkeypatch, lineas, abiertas=()):
    monkeypatch.setattr(api, 'datetime', Reloj)
    monkeypatch.setattr(api, 'date', Fecha)
    monkeypatch.setattr(api, '_leer_lineas', lambda *a: list(lineas))
    monkeypatch.setattr(api, '_leer_abiertas', lambda *a: list(abiertas))
    monkeypatch.setattr(api, '_cargar_estimaciones', lambda: ({(1, 10): (1, 5)}, MEDIAS))
    monkeypatch.setattr(api, '_avance_por_bono', lambda *a: {})
    monkeypatch.setattr(api, '_leer_cola', lambda: [])


def test_los_trocitos_seguidos_del_mismo_bono_se_funden(monkeypatch):
    """6243/70: 30 minutos de produccion y detras dos de UN minuto, pegados. Es
    la misma sesion partida por el terminal, no tres trabajos."""
    _montar(monkeypatch, [
        _trozo(datetime(2026, 9, 7, 7, 50), datetime(2026, 9, 7, 8, 20)),
        _trozo(datetime(2026, 9, 7, 8, 20), datetime(2026, 9, 7, 8, 21)),
        _trozo(datetime(2026, 9, 7, 8, 21), datetime(2026, 9, 7, 8, 22)),
    ])
    items = api.get_items(vista='empleado')

    assert len(items) == 1
    assert items[0]['start'] == datetime(2026, 9, 7, 7, 50)
    assert items[0]['end'] == datetime(2026, 9, 7, 8, 22)
    assert items[0]['min_real'] == 32          # 30 + 1 + 1


def test_un_trocito_lejano_no_se_funde(monkeypatch):
    """Dos fichajes de un minuto separados por horas son dos visitas al bono,
    no una sesion: fundirlos pintaria una barra de la que casi todo es hueco."""
    _montar(monkeypatch, [
        _trozo(datetime(2026, 9, 7, 8, 0), datetime(2026, 9, 7, 8, 1)),
        _trozo(datetime(2026, 9, 7, 11, 0), datetime(2026, 9, 7, 11, 1)),
    ])
    assert len(api.get_items(vista='empleado')) == 2


def test_dos_trabajos_largos_seguidos_no_se_funden(monkeypatch):
    """La regla es para trocitos. Dos sesiones de media hora son dos barras
    aunque vayan pegadas."""
    _montar(monkeypatch, [
        _trozo(datetime(2026, 9, 7, 8, 0), datetime(2026, 9, 7, 8, 30)),
        _trozo(datetime(2026, 9, 7, 8, 30), datetime(2026, 9, 7, 9, 0)),
    ])
    assert len(api.get_items(vista='empleado')) == 2


def test_un_trocito_de_montaje_no_se_traga_la_produccion(monkeypatch):
    """Fundir preparacion con fabricacion es el otro problema y lo resuelve
    `_fundir_montaje`, que ademas marca que parte fue preparar."""
    _montar(monkeypatch, [
        _trozo(datetime(2026, 9, 7, 7, 40), datetime(2026, 9, 7, 7, 42), montaje=True),
        _trozo(datetime(2026, 9, 7, 7, 42), datetime(2026, 9, 7, 8, 20)),
    ])
    items = api.get_items(vista='empleado')

    assert len(items) == 1
    assert items[0]['min_montaje'] == 2        # fundida, pero por la otra via
    assert items[0]['pct_montaje'] > 0
