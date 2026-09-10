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
