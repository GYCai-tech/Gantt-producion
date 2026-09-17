"""Ausencias de operario en las filas del Gantt, sin tocar PORTALHR.

El dato vive en PORTALHR (otra base del mismo servidor) y se cruza por
`Conf_Empleados.codigotag = Employees.AccessId`. Aqui no se consulta: se fija el
contrato del mapeo, que es lo que el front consume.

Los casos estan tomados de datos reales del 16-09-2026, cuando produccion tenia
cuatro ausencias de tres formas distintas: vacaciones de dia completo, una
consulta medica de 07:00 a 10:00, y dos bajas largas de rango.
"""
from datetime import date

from fastapi import HTTPException

from app.routers import api

DIA = date(2026, 9, 16)


def fila(idempleado, motivo, parcial=False, hora_ini=None, hora_fin=None,
         desde=DIA, hasta=DIA):
    return {"idempleado": idempleado, "motivo": motivo, "desde": desde,
            "hasta": hasta, "parcial": parcial,
            "hora_ini": hora_ini, "hora_fin": hora_fin}


def fingir_erp(monkeypatch, filas):
    monkeypatch.setattr(api, "_erp", lambda *a, **k: filas)


def test_sin_consulta_configurada_no_se_toca_el_erp(monkeypatch):
    """La consulta vacia es el interruptor de apagado: ni se intenta leer.

    Fue el estado real mientras faltaban permisos en PORTALHR, y sigue siendo
    la via de escape si algun dia hay que apagar esto sin desplegar."""
    monkeypatch.setattr(api, "_SQL_AUSENCIAS", "")

    def no_deberia_llamarse(*a, **k):
        raise AssertionError("no hay que consultar el ERP si la consulta esta vacia")

    monkeypatch.setattr(api, "_erp", no_deberia_llamarse)
    assert api._ausencias(DIA) == {}


def test_mapea_por_idempleado_en_texto(monkeypatch):
    """Las filas del Gantt se indexan por id de empleado en STRING (`grp.id`)."""
    fingir_erp(monkeypatch, [fila(17, "Vacaciones")])
    assert api._ausencias(DIA) == {
        "17": {"motivo": "Vacaciones", "desde": DIA, "hasta": DIA,
               "parcial": False, "cuando": None},
    }


def test_una_parcial_dice_como_afecta_a_la_jornada(monkeypatch):
    """Quien falta de 07:00 a 10:00 trabaja el resto: lo que hay que saber no es
    la franja en crudo, es que ESE DIA entra a las 10:00."""
    fingir_erp(monkeypatch, [fila(15, "Ausencia", True, "07:00", "10:00")])
    parcial = api._ausencias(DIA)["15"]
    assert parcial["parcial"] is True
    assert parcial["cuando"] == "entra a las 10:00"


def test_las_cuatro_formas_de_una_parcial():
    """Medido sobre las 255 parciales aprobadas: 109 franja intermedia, 97 al
    cierre, 46 a la apertura y 3 que cubren la jornada entera."""
    assert api._cuando_falta("07:00", "10:00") == "entra a las 10:00"
    assert api._cuando_falta("13:00", "15:00") == "se marcha a las 13:00"
    assert api._cuando_falta("09:00", "11:00") == "fuera de 09:00 a 11:00"
    # Marcada como parcial pero de apertura a cierre: no es parcial de verdad.
    assert api._cuando_falta("07:00", "15:00") is None
    assert api._cuando_falta(None, "10:00") is None


def test_una_parcial_que_cubre_la_jornada_es_dia_completo(monkeypatch):
    """Las 3 filas de 07:00 a 15:00 vienen con PartialDay=1 y no lo son. Si se
    respetara esa marca, la fila saldria sin rayar como si hubiera venido."""
    fingir_erp(monkeypatch, [fila(9, "Vacaciones", True, "07:00", "15:00")])
    assert api._ausencias(DIA)["9"]["parcial"] is False


def test_una_baja_larga_conserva_su_rango(monkeypatch):
    """Las bajas vienen de Employees_Leaves y son intervalos, no dias sueltos."""
    fingir_erp(monkeypatch, [
        fila(31, "De baja", desde=date(2026, 6, 2), hasta=date(2026, 9, 30)),
    ])
    victor = api._ausencias(DIA)["31"]
    assert (victor["desde"], victor["hasta"]) == (date(2026, 6, 2), date(2026, 9, 30))


def test_un_motivo_vacio_no_deja_la_fila_muda(monkeypatch):
    """La consulta ya solo devuelve literales, asi que esto no deberia pasar.
    El respaldo existe por si algun dia deja de garantizarlo: marcar la fila sin
    decir nada seria peor que no marcarla."""
    fingir_erp(monkeypatch, [fila(3, "   "), fila(4, None)])
    assert [a["motivo"] for a in api._ausencias(DIA).values()] == ["Ausencia", "Ausencia"]


def test_la_consulta_no_lee_el_motivo_escrito_en_el_portal():
    """`Reason` es texto libre y la gente escribe ahi datos de salud suyos o de
    sus hijos. El Gantt lo pintaba en la fila del operario, a la vista de toda
    la planta.

    Se corta en la CONSULTA, no en el front: si la columna no se selecciona, el
    dato no sale del servidor ni puede reaparecer por un descuido al pintar.
    Este test es el guardarrail de eso."""
    assert "Reason" not in api._SQL_AUSENCIAS

    #  Y la etiqueta sale de un literal, no de nada que haya tecleado una
    #  persona: las tres categorias son las unicas salidas posibles.
    for etiqueta in ("'De baja'", "'Vacaciones'", "'Ausencia'"):
        assert etiqueta in api._SQL_AUSENCIAS


def test_si_el_erp_falla_el_gantt_se_pinta_igual(monkeypatch):
    """Degrada como el semaforo o el escandallo: informacion de menos, no un 503."""
    def denegado(*a, **k):
        raise HTTPException(status_code=503, detail="No se pudo consultar el ERP")

    monkeypatch.setattr(api, "_erp", denegado)
    assert api._ausencias(DIA) == {}


def test_en_la_vista_de_maquinas_no_hay_ausencias(monkeypatch):
    """Una maquina no se va al medico: la clave viaja igual, pero vacia, para
    que el front no tenga que distinguir dos formas de respuesta."""
    monkeypatch.setattr(api, "_leer_abiertas", lambda *a: [])
    monkeypatch.setattr(api, "_leer_cola", lambda *a: [])
    assert api.get_avisos(vista="maquina", dia=DIA) == {"sin_salida": {}, "ausencias": {}}
