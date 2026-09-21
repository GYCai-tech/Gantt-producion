"""Consultor de Bonos: qué bonos hay por máquina, con su estado.

Es la pantalla de la v1 (rama `legacy/planificador-v1`) traída de vuelta. Dos
cambios respecto al original, y los dos por el mismo motivo —la v2 solo habla
con el ERP—:

· **Sin PostgreSQL.** La v1 marcaba el bono "sin fichar" consultando
  `analytics.v_asignaciones_empleado` en la réplica. Aquí eso sale del ERP en
  vivo: un bono tiene fichaje activo si alguna de sus líneas sigue abierta
  (`Hfinal IS NULL`). Además de quitar una dependencia, evita el desfase del
  ETL, que es justo lo que esta pantalla no puede permitirse.

· **Sin columna Cliente.** `Ordenes.IdCliente` está relleno en 48 de los 822
  bonos de órdenes activas (5,8%): sería una fila de guiones, como pasó con
  `ModeloArticulo` en la pestaña de órdenes.

Se lee del ERP y no de la réplica por lo mismo que decía la v1: `fact_ordenes`
va detrás de un ETL incremental que deja órdenes zombi (idestado desincronizado)
y `fact_bonos` excluye los bonos sin fichaje, que son casi todos los bloqueados.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.erp.cliente import ErpNoDisponible
from app.erp.cliente import consultar as _erp
from app.erp.consultas import HORAS_LINEA_VIVA as _HORAS_LINEA_VIVA
from app.erp.lecturas import nombre_completo as _nombre_completo

router = APIRouter(prefix="/api")

#  Estados de ORDEN que mira esta pantalla: 1 activa, 3 bloqueada.
_ORDEN_ACTIVA = 1
_ORDEN_BLOQUEADA = 3

#  LA CONSULTA DE LA v1, TAL CUAL. Se restauro literal por peticion expresa:
#  la pantalla tiene que enseñar exactamente lo que enseñaba antes.
#
#  Dos consecuencias que trae de serie y que conviene tener presentes, porque
#  llegue a cambiarlas y se revirtieron a proposito:
#
#  · El `JOIN` a `a_matricula` (y no LEFT) DEJA FUERA los bonos que no declaran
#    matricula: 17 de 822 no salen en la pantalla.
#  · Sin GROUP BY, un bono que declara el mismo articulo mas de una vez en
#    `Ordenes_Bonos_Salidas` aparece repetido.
#
#  UNICO añadido a la consulta de la v1: `a_orden`, para poder enseñar y buscar
#  el ARTICULO FINAL de la orden. La v1 ya traia su codigo (`o.IdArticulo`) pero
#  no su descripcion, y la columna "Articulo" enseñaba en realidad la del
#  articulo que sale del BONO —una pieza intermedia—, que en 625 de las 783
#  filas (80%) no es el mismo. Es LEFT y no INNER por costumbre defensiva; no
#  puede duplicar ni perder filas porque `IdArticulo` es la PK de `Articulos` y
#  las 167 ordenes activas apuntan a uno que existe.
_BONOS_QUERY = """
SELECT
    o.IdOrden                AS idorden,
    ob.IdBono                AS idbono,
    ob.Matricula             AS matricula,
    a_matricula.Descrip      AS descrip_matricula,
    ob.IdEstado              AS estado_bono,
    o.IdCliente              AS idcliente,
    o.IdArticulo             AS idarticulo_orden,
    a_orden.Descrip          AS descrip_articulo_orden,
    a_salida.Descrip         AS descrip_articulo,
    ob.Area                  AS area,
    o.Usuario                AS usuario
FROM Ordenes_Bonos_Salidas obs
    JOIN Ordenes o              ON obs.IdOrden     = o.IdOrden
    JOIN Ordenes_Bonos ob       ON obs.IdOrden     = ob.IdOrden
                               AND obs.IdBono      = ob.IdBono
    JOIN Articulos a_salida     ON obs.IdArticulo  = a_salida.IdArticulo
    JOIN Articulos a_matricula  ON ob.Matricula    = a_matricula.IdArticulo
    LEFT JOIN Articulos a_orden ON o.IdArticulo    = a_orden.IdArticulo
WHERE o.IdEstado  = :estado_orden
  {filtro_bono}
  {filtro_matricula}
ORDER BY o.IdOrden DESC
"""

#  Un bono con una línea abierta desde hace días es un fichaje que nadie cerró,
#  no trabajo en marcha. Mismo criterio que el Gantt (`_HORAS_LINEA_VIVA`).
_FICHAJE_ACTIVO_QUERY = """
SELECT DISTINCT obl.IdOrden AS idorden, obl.IdBono AS idbono
FROM Ordenes_Bonos_Lineas obl
WHERE obl.Hfinal IS NULL
  AND obl.Hinicial BETWEEN :limite AND :ahora
"""

#  BONOS SIN MATERIAL DISPONIBLE. Portada de la consulta de Access, con tres
#  cambios que no alteran lo que devuelve:
#
#  · Access repetia tres veces la misma condicion cambiando solo el estado
#    (`IdEstado=0 AND ... OR IdEstado=1 AND ... OR IdEstado=3 AND ...`); aqui
#    es un `IN (0,1,3)`. Los finalizados quedan fuera igual que alli, y tiene
#    sentido: un bono terminado ya no necesita material.
#  · `Disponible < "0"` comparaba contra el TEXTO "0". En Access colaba porque
#    convierte sola; `Disponible` es decimal, asi que aqui va `< 0`. Si algun
#    dia esa columna pasara a texto, la comparacion de Access habria empezado a
#    mentir en silencio ('10' < '0' es cierto entre cadenas).
#  · DISTINCT: el JOIN a la vista es por (IdOrden, IdArticulo) sin el bono, asi
#    que un bono con varias entradas del mismo articulo salia repetido. Aqui
#    solo hace falta el conjunto de bonos marcados.
#
#  OJO con la dependencia: `Pers_vOrdenes_Consumos` es una vista PERSONALIZADA
#  del equipo, no del catalogo del ERP. Si alguien la renombra o la borra, esto
#  se cae -- por eso degrada a vacio en vez de tumbar la pantalla.
#
#  La vista trae una fila por almacen (hay 2). No hace falta desambiguar: de los
#  350 pares (orden, articulo) marcados hoy, los 350 estan negativos en todos
#  los almacenes donde aparecen, asi que no hay falsos positivos por ese lado.
_SIN_MATERIAL_QUERY = """
SELECT DISTINCT ob.IdOrden AS idorden, ob.IdBono AS idbono
FROM Ordenes o
    JOIN Ordenes_Bonos ob           ON o.IdOrden      = ob.IdOrden
    JOIN Ordenes_Bonos_Entradas obe ON obe.IdOrden    = ob.IdOrden
                                   AND obe.IdBono     = ob.IdBono
    JOIN Pers_vOrdenes_Consumos pc  ON pc.IdArticulo  = obe.IdArticulo
                                   AND pc.IdOrden     = o.IdOrden
WHERE ob.IdEstado IN (0, 1, 3)
  AND pc.Disponible < 0
"""

#  QUIEN TIENE EL BONO ASIGNADO. Va en consulta aparte y se cruza en Python, no
#  como JOIN dentro de `_BONOS_QUERY`, por dos motivos:
#
#  · La asignacion es 1:N —105 de los 783 bonos tienen mas de un operario, y
#    hasta cuatro—, asi que un JOIN multiplicaria filas en una consulta que ya
#    duplica de por si y que hay que dejar como la dejo la v1.
#  · `Ordenes_Bonos.IdEmpleado` NO sirve: esta a NULL. La asignacion vive en
#    `Pers_EmpleadosOrdenBono` (Orden, Bono -> IdEmpleado), igual que en el
#    Gantt y en la pestaña de ordenes sin asignar.
#
#  El LEFT a `Empleados_Datos` es por si una asignacion apunta a una ficha que
#  ya no esta: mejor "#38" que perder la fila entera.
_ASIGNADOS_QUERY = """
SELECT pe.Orden AS idorden, pe.Bono AS idbono, pe.IdEmpleado AS idempleado,
       ed.Nombre AS nombre, ed.Apellidos AS apellidos
FROM Pers_EmpleadosOrdenBono pe
    LEFT JOIN Empleados_Datos ed ON ed.IdEmpleado = pe.IdEmpleado
"""


@router.get("/bonos")
def get_consultor_bonos(
    matricula: Optional[str] = Query(None, description="Matrícula de máquina (opcional)"),
    estado_bono: Optional[int] = Query(None, description="0=Espera, 1=Activo, 2=Finalizado, 3=Bloqueado. Omitir para todos."),
    estado_orden: int = Query(_ORDEN_ACTIVA, description="Estado de la orden (1=Activa, 3=Bloqueada)"),
):
    #  Llamada DIRECTA (tests, scripts) en vez de por HTTP: ahí FastAPI no ha
    #  resuelto los valores por defecto y estos llegan como el objeto `Query`,
    #  que es truthy. Sin esto, `matricula` se colaba como parámetro de la
    #  consulta y el driver contestaba "Invalid parameter type".
    if not isinstance(matricula, str):
        matricula = None
    if not isinstance(estado_bono, int):
        estado_bono = None
    if not isinstance(estado_orden, int):
        estado_orden = _ORDEN_ACTIVA

    filtro_bono      = "AND ob.IdEstado = :estado_bono" if estado_bono is not None else ""
    filtro_matricula = "AND ob.Matricula = :matricula"  if matricula else ""
    params = {"estado_orden": estado_orden}
    if estado_bono is not None:
        params["estado_bono"] = estado_bono
    if matricula:
        params["matricula"] = matricula

    filas = _erp(
        _BONOS_QUERY.format(filtro_bono=filtro_bono, filtro_matricula=filtro_matricula),
        params,
    )

    bonos = [{
        "idorden":           r["idorden"],
        "idbono":            r["idbono"],
        "matricula":         (r["matricula"] or "").strip(),
        "descrip_matricula": (r["descrip_matricula"] or "").strip(),
        "estado_bono":       r["estado_bono"],
        #  Relleno en 48 de 822 bonos (5,8%), pero la v1 lo enseñaba y la
        #  pantalla tiene que representar lo mismo que antes.
        "idcliente":         (r["idcliente"] or "").strip(),
        "idarticulo_orden":  (r["idarticulo_orden"] or "").strip(),
        #  El artículo FINAL de la orden. Ojo, no es `descrip_articulo`: ese es
        #  el que sale del bono —una pieza intermedia— y en el 80% de las filas
        #  no coinciden.
        "descrip_articulo_orden": (r["descrip_articulo_orden"] or "").strip(),
        "descrip_articulo":  (r["descrip_articulo"] or "").strip(),
        "area":              (r["area"] or "").strip(),
        "usuario":           (r["usuario"] or "").strip(),
    } for r in filas]

    #  Solo se pregunta por los fichajes si hay bonos que marcar: la pantalla
    #  lanza cinco peticiones a la vez (una por pestaña) y cuatro suelen venir
    #  vacías.
    if bonos:
        ahora = datetime.now()
        con_fichaje = {
            (r["idorden"], r["idbono"])
            for r in _erp(_FICHAJE_ACTIVO_QUERY, {
                "ahora": ahora,
                "limite": ahora - timedelta(hours=_HORAS_LINEA_VIVA),
            })
        }
        #  Quién lo tiene asignado. Se lee entera y se agrupa aquí: son ~19.500
        #  filas de todas las órdenes de la historia, pero cruzarlas en memoria
        #  sale más barato que filtrar por los cientos de bonos de la pantalla.
        asignados: dict[tuple, list[str]] = {}
        for r in _erp(_ASIGNADOS_QUERY, {}):
            #  Mismo nombre que en el Gantt: `_nombre_completo` ya resuelve
            #  "Nombre Apellidos" y cae a "#id" si la ficha no tiene nombre.
            asignados.setdefault((r["idorden"], r["idbono"]), []).append(
                _nombre_completo(r))

        #  Falta de material. Degrada a vacio si la vista personalizada no esta
        #  disponible: es una marca de mas, no la pantalla.
        try:
            sin_material = {(r["idorden"], r["idbono"])
                            for r in _erp(_SIN_MATERIAL_QUERY, {})}
        except ErpNoDisponible:
            print("[consultor] sin material no disponible: "
                  "¿falta la vista Pers_vOrdenes_Consumos?")
            sin_material = set()

        for b in bonos:
            b["tiene_fichaje_activo"] = (b["idorden"], b["idbono"]) in con_fichaje
            #  Varios operarios en un bono es normal (105 de 783): van todos,
            #  que para eso se reparten el trabajo.
            b["operarios"] = sorted(asignados.get((b["idorden"], b["idbono"]), []))
            #  NO es un estado, es un eje aparte: 92 bonos estan bloqueados Y
            #  sin material a la vez, 81 sin material sin estar bloqueados y
            #  117 bloqueados con el material puesto. Meterlo en `estado_bono`
            #  obligaria a elegir cual de las dos cosas contar, y se perderia
            #  la otra. Por eso viaja como campo propio.
            b["sin_material"] = (b["idorden"], b["idbono"]) in sin_material

    return {"total": len(bonos), "bonos": bonos}


@router.get("/matriculas")
def get_consultor_matriculas():
    """Las máquinas que tienen bonos en órdenes activas o bloqueadas.

    Sale del universo de la propia pantalla para que el desplegable no ofrezca
    máquinas que al elegirlas no devuelven nada."""
    filas = _erp("""
        SELECT DISTINCT ob.Matricula AS matricula, a.Descrip AS descrip
        FROM Ordenes_Bonos ob
            JOIN Ordenes o   ON ob.IdOrden   = o.IdOrden
            JOIN Articulos a ON ob.Matricula = a.IdArticulo
        WHERE o.IdEstado IN (:activa, :bloqueada)
          AND ob.Matricula IS NOT NULL
          AND LTRIM(RTRIM(ob.Matricula)) <> ''
        ORDER BY a.Descrip
    """, {"activa": _ORDEN_ACTIVA, "bloqueada": _ORDEN_BLOQUEADA})

    return {"matriculas": [{
        "matricula": (r["matricula"] or "").strip(),
        "descrip":   (r["descrip"] or "").strip(),
    } for r in filas]}
