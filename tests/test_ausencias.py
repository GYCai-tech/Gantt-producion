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
               "parcial": False, "hora_ini": None, "hora_fin": None},
    }


def test_la_franja_de_una_parcial_llega_entera(monkeypatch):
    """Elias tiene consulta de 07:00 a 10:00 y trabaja el resto de la jornada.

    Sin la franja, el front no puede distinguirlo de quien no vino, y le
    pintaria el dia entero como ausente."""
    fingir_erp(monkeypatch, [fila(15, "Consulta médica", True, "07:00", "10:00")])
    elias = api._ausencias(DIA)["15"]
    assert elias["parcial"] is True
    assert (elias["hora_ini"], elias["hora_fin"]) == ("07:00", "10:00")


def test_una_baja_larga_conserva_su_rango(monkeypatch):
    """Las bajas vienen de Employees_Leaves y son intervalos, no dias sueltos."""
    fingir_erp(monkeypatch, [
        fila(31, "ACCIDENTE MOTO", desde=date(2026, 6, 2), hasta=date(2026, 9, 30)),
    ])
    victor = api._ausencias(DIA)["31"]
    assert (victor["desde"], victor["hasta"]) == (date(2026, 6, 2), date(2026, 9, 30))


def test_un_motivo_vacio_no_deja_la_fila_muda(monkeypatch):
    """`Reason` es texto libre y llega vacio a menudo. Marcar la fila sin decir
    por que seria peor que no marcarla."""
    fingir_erp(monkeypatch, [fila(3, "   "), fila(4, None)])
    assert [a["motivo"] for a in api._ausencias(DIA).values()] == ["Ausente", "Ausente"]


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
