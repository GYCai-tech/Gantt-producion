"""El SQL que la app le hace al ERP, y las constantes que lo parametrizan.

Aquí NO se ejecuta nada: este módulo es texto. Quien lo ejecuta es
`app.erp.cliente`, y quien lo interpreta, `app.erp.lecturas` y
`app.erp.cache`.

El texto de cada consulta es IDÉNTICO al que vivía en `app/routers/api.py`,
incluida la indentación de las que estaban escritas dentro de una función. No
se ha reformateado ni una línea a propósito: los tests de caracterización
reconocen algunas de estas consultas por subcadenas de su texto, y el ERP es
de solo lectura y no perdona un cambio silencioso.
"""

# ─────────────────────────────────────────────────────────────────────
#  Constantes que parametrizan las consultas
# ─────────────────────────────────────────────────────────────────────

#  Una línea abierta más vieja que esto es fantasma, no trabajo. La usan la
#  consulta de líneas abiertas, la del avance y la de la cola.
HORAS_LINEA_VIVA = 24

#  El departamento de planta en `Empleados_Datos`. Es el que separa a los
#  operarios del resto: 25 de los 29 que salían en el Gantt están aquí.
DEPARTAMENTO_PRODUCCION = 6

#  Ventana de actividad para deducir el área de un operario.
AREAS_RECIENTES_DIAS = 90

#  Cuántos días sin tocar un bono arrancado antes de darlo por abandonado.
#  Con 10 entran 14 bonos y quedan fuera 7: cuatro de mayo a julio y tres de
#  finales de agosto.
DIAS_BONO_ARRANCADO = 10

#  Cuánto histórico se mira para las medias y los montajes.
HIST_MESES = 18

#  Cuánto histórico se mira para medir la atención que pide cada máquina.
#  Seis meses y no dieciocho como las medias: el ritmo de una máquina no
#  cambia, pero la forma de repartir el trabajo sí, y lo que hace falta saber
#  aquí es cómo se trabaja AHORA, no cómo se trabajaba hace año y medio.
ATENCION_MESES = 6

#  Horas fichadas por debajo de las cuales no se mide la atención de una
#  máquina. Con menos, un par de días raros mandan sobre el dato y el plan
#  acabaría soltando al operario por una casualidad. Sin medida se le trata
#  como atendida, que es como se comporta hoy: nunca se inventa capacidad.
ATENCION_MIN_HORAS = 20


# ─────────────────────────────────────────────────────────────────────
#  Líneas de bono: la actividad real que acompaña a la previsión de cola
# ─────────────────────────────────────────────────────────────────────
#  Traducción a T-SQL de la consulta de Access que define esta pantalla:
#  las líneas de bono en estado 1, con el operario que las fichó, la máquina
#  en la que se hicieron y el artículo que producen.
#
#  Dos columnas se añaden sobre la consulta original porque el Gantt no se
#  puede pintar sin ellas:
#    · Hinicial/Hfinal — inicio y fin reales de la línea (la barra). `Fecha`
#      es solo el instante en que el ERP grabó la línea, no su duración.
#    · Apellidos — `Nombre` a secas repite mucho (hay varios "José"), y las
#      filas del Gantt son personas: necesitan un nombre distinguible.
#
#  El JOIN con Ordenes_Bonos_Salidas puede duplicar una línea si un bono
#  declara más de un artículo de salida (medido: 3 bonos de 16.386, pero en
#  el Gantt saldría como dos barras idénticas superpuestas). Se deduplica en
#  Python por (orden, bono, línea), que es la identidad real de una barra.
# ─────────────────────────────────────────────────────────────────────

LINEAS_SELECT = """
SELECT
    obl.IdOrden       AS idorden,
    obl.IdBono        AS idbono,
    obl.IdLinea       AS idlinea,
    obl.IdOperacion   AS idoperacion,
    obl.IdEmpleado    AS idempleado,
    ed.Nombre         AS nombre,
    ed.Apellidos      AS apellidos,
    obl.Fecha         AS fecha,
    obl.Hinicial      AS hinicial,
    obl.Hfinal        AS hfinal,
    obl.Matricula     AS matricula,
    ob.IdTrabajo      AS idtrabajo,
    a_maq.Descrip     AS descrip_maquina,
    am.Area           AS area,
    COALESCE(NULLIF(obs.Cantidad, 0), ob.CantidadTotal) AS piezas_a_fabricar,
    obs.IdArticulo    AS idarticulo_salida,
    a_sal.Descrip     AS descrip_salida
FROM Ordenes_Bonos_Lineas obl
    JOIN Ordenes_Bonos ob          ON obl.IdOrden     = ob.IdOrden
                                  AND obl.IdBono      = ob.IdBono
    JOIN Articulos_Maquinas am     ON obl.Matricula   = am.IdArticulo
    JOIN Articulos a_maq           ON am.IdArticulo   = a_maq.IdArticulo
    JOIN Ordenes_Bonos_Salidas obs ON obl.IdOrden     = obs.IdOrden
                                  AND obl.IdBono      = obs.IdBono
    JOIN Articulos a_sal           ON obs.IdArticulo  = a_sal.IdArticulo
    JOIN Empleados_Datos ed        ON obl.IdEmpleado  = ed.IdEmpleado
-- Una línea es pintable si tiene hora de inicio. NO se filtra por
-- `obl.IdEstado`: ahí el estado es TRANSITORIO —1 mientras el fichaje está
-- abierto, 2 al cerrarlo— y no significa "línea válida". Filtrando por 1 el
-- Gantt solo enseñaba lo que estaba abierto en ese instante y tiraba todo el
-- trabajo ya terminado: ETT3 pintaba 1 barra de las 9 que hizo hoy, y un día
-- pasado enseñaba 7 líneas de las 126 que hubo.
WHERE obl.Hinicial IS NOT NULL
"""

LINEAS_ORDEN = """
ORDER BY obl.Fecha
"""

#  Los dos filtros que se le cuelgan. La consulta se COMPONE con ellos en vez
#  de parchear el WHERE con un `replace`: si el texto buscado dejara de
#  encajar, un replace no falla — se queda sin sustituir y la consulta sale sin
#  filtrar o revienta con los parámetros sin bindear, que es un 503 opaco.
FILTRO_RANGO    = "CAST(obl.Fecha AS date) BETWEEN :desde AND :hasta"
#  «Abierta» es siempre «abierta EN :ahora», no «abierta ahora mismo». Con
#  el reloj en vivo son lo mismo; con el reloj congelado para la foto de las
#  06:55 no: a las 12:56 la línea que estaba en marcha a las 06:55 ya tiene
#  Hfinal, y un `Hfinal IS NULL` a secas la tiraba. La foto salía sin una
#  sola barra de trabajo real.
FILTRO_ABIERTAS = ("(obl.Hfinal IS NULL OR obl.Hfinal > :ahora)"
                   " AND obl.Hinicial BETWEEN :limite AND :ahora")


def consulta_lineas(filtro: str) -> str:
    """La consulta de líneas con el filtro que toque, sin duplicar el SELECT."""
    return LINEAS_SELECT + "  AND " + filtro + LINEAS_ORDEN


# ─────────────────────────────────────────────────────────────────────
#  GRUPOS: el censo de filas del Gantt
# ─────────────────────────────────────────────────────────────────────
#  El frontend carga los grupos UNA vez (y al cambiar de vista), no al
#  navegar entre días. Por eso la lista no puede depender de la ventana
#  visible: se toman todos los operarios/máquinas que alguna vez han tenido
#  una línea de bono (29 y 103 respectivamente — el censo es pequeño).
# ─────────────────────────────────────────────────────────────────────

SQL_CENSO_MAQUINAS = """
            SELECT DISTINCT
                obl.Matricula  AS id,
                a.Descrip      AS nombre,
                am.Area        AS area
            FROM Ordenes_Bonos_Lineas obl
                JOIN Articulos_Maquinas am ON obl.Matricula = am.IdArticulo
                JOIN Articulos a           ON am.IdArticulo = a.IdArticulo
            ORDER BY a.Descrip
        """

#  Operarios: el censo completo, sin depender de fechas. Solo los de
#  PRODUCCIÓN: `Empleados_Datos.IdDepartamento = 6`. Los otros cuatro que
#  aparecían tienen fichajes en el histórico pero no son gente de planta
#  -- Gilberto está en el 3 y es quien mantiene el escandallo -- y ocupaban
#  fila en el Gantt y en la rejilla de carga sin trabajo que planificar.
SQL_CENSO_EMPLEADOS = """
        SELECT DISTINCT
            obl.IdEmpleado AS idempleado,
            ed.Nombre      AS nombre,
            ed.Apellidos   AS apellidos
        FROM Ordenes_Bonos_Lineas obl
            JOIN Empleados_Datos ed ON obl.IdEmpleado = ed.IdEmpleado
        WHERE ed.IdDepartamento = :departamento
          --  El 0 es el comodín del ERP, "Empleado Prueba0 (Sin Definir)": no es
          --  una persona. Salía en el Gantt, en la carga y en la hoja del día como
          --  un operario más sin trabajo. Producción pidió quitarlo (2026-09-24).
          AND obl.IdEmpleado <> 0
    """

#  El área de un operario no es su departamento del ERP sino la de las
#  máquinas en las que trabaja: es lo que agrupa de verdad en planta. Solo
#  se mira la actividad reciente, para que quien cambió de sección no
#  arrastre para siempre las áreas de su puesto anterior.
SQL_AREAS_EMPLEADO = """
        SELECT DISTINCT obl.IdEmpleado AS idempleado, am.Area AS area
        FROM Ordenes_Bonos_Lineas obl
            JOIN Articulos_Maquinas am ON obl.Matricula = am.IdArticulo
        WHERE obl.Fecha >= DATEADD(day, :dias, GETDATE())
          AND am.Area IS NOT NULL
    """


# ─────────────────────────────────────────────────────────────────────
#  ESCANDALLO, MEDIAS Y MONTAJES
# ─────────────────────────────────────────────────────────────────────

SQL_TEORICO = """
WITH mano_obra AS (
    SELECT IdTrabajo, SUM(Duracion) * 1440.0 AS MinPieza
    FROM Trabajos_ManoObra
    WHERE Duracion > 0        -- 0 no es una medición, es la casilla sin rellenar
    GROUP BY IdTrabajo
)
SELECT
    ob.IdOrden AS idorden,
    ob.IdBono  AS idbono,
    -- Duracion viene en DÍAS (IdUnidadDuracion='D'): x1440 -> min/pieza.
    -- Se prefiere el detalle de Trabajos_ManoObra sobre Trabajos_Fases.TiempoMO,
    -- que es la copia desnormalizada y a veces está sin recalcular.
    COALESCE(mo.MinPieza, NULLIF(tf.TiempoMO, 0) * 1440.0) AS min_pieza,
    COALESCE(NULLIF(ob.TiempoMontaje, 0), 0)
      + COALESCE(NULLIF(ob.TiempoDesMontaje, 0), 0)        AS setup_min
FROM Ordenes_Bonos ob
    LEFT JOIN mano_obra mo      ON mo.IdTrabajo = ob.IdTrabajo
    LEFT JOIN Trabajos_Fases tf ON tf.IdTrabajo = ob.IdTrabajo
WHERE ob.IdEstado IN (0, 1, 3)   -- solo bonos abiertos; los cerrados ya tienen minutos reales
  -- Basta con que el ERP declare UNA de las dos cosas. Antes se exigía el
  -- min/pieza, y como el escandallo (10 bonos) y la preparación (18) casi no
  -- se solapan -- solo 1 bono tiene ambas --, 17 de los 18 tiempos de montaje
  -- declarados se tiraban a la basura: el bono caía a la media histórica y
  -- perdía su setup por el camino.
  AND (COALESCE(mo.MinPieza, NULLIF(tf.TiempoMO, 0) * 1440.0) > 0
       OR COALESCE(NULLIF(ob.TiempoMontaje, 0), 0)
        + COALESCE(NULLIF(ob.TiempoDesMontaje, 0), 0) > 0)
"""

#  PARADAS ANOTADAS sobre una línea de fichaje. Salen de
#  `Ordenes_Bonos_Lineas_Inc` con su catálogo `Incidencias_Tipos` (Avería
#  Máquina, Avería Utillaje, Calidad, Esperar Carretilla, Otros Paros...).
#
#  OJO con la unidad: el minuto de parada NO está en `Minutos` —que viene a 0—
#  sino en `Duracion`, y en DÍAS, igual que el escandallo. La avería de utillaje
#  del 6490/10 guarda 0,054861 = 79 minutos, que son exactamente los que duró su
#  línea 3. Leer `Minutos` a secas daría cero en todas.
#
#  La tabla es diminuta (36 filas) y no se filtra por fecha: cuesta menos
#  traerla entera que acotarla, y así una anotación vieja aparece igual si se
#  navega a ese día.
SQL_PARADAS = """
SELECT i.IdOrden AS idorden, i.IdBono AS idbono, i.IdLinea AS idlinea,
       i.TipoIncidencia AS tipo, t.Descrip AS motivo,
       i.Duracion * 1440.0 AS minutos,
       NULLIF(LTRIM(RTRIM(i.Observaciones)), '') AS observaciones
FROM Ordenes_Bonos_Lineas_Inc i
    LEFT JOIN Incidencias_Tipos t ON t.TipoIncidencia = i.TipoIncidencia
"""

#  OJO con el CASE por IdOperacion: sumar todos los minutos y dividirlos entre
#  las piezas mete el montaje dentro del ritmo. Y como el montaje no produce
#  nada, se reparte entre las piezas del lote: en un bono de 3 piezas con 22 min
#  de montaje y 6 de fabricación salía un "min/pieza" de 9,3 cuando el ritmo real
#  es 1,9 -- cinco veces inflado. Ese número contaminaba la media del artículo y
#  luego se aplicaba a lotes de miles de piezas. El ritmo se mide solo con
#  producción; la preparación va aparte, en su propia columna.
SQL_MEDIAS = """
WITH bono_min AS (
    SELECT obl.IdOrden, obl.IdBono,
           SUM(CASE WHEN obl.IdOperacion = 0
                    THEN DATEDIFF(minute, obl.Hinicial, obl.Hfinal) ELSE 0 END) AS min_produccion,
           SUM(CASE WHEN obl.IdOperacion IN (1, 2)
                    THEN DATEDIFF(minute, obl.Hinicial, obl.Hfinal) ELSE 0 END) AS min_montaje
    FROM Ordenes_Bonos_Lineas obl
    WHERE obl.Hinicial IS NOT NULL
      AND obl.Hfinal   IS NOT NULL
      AND obl.Hfinal   > obl.Hinicial
      AND obl.Fecha   >= DATEADD(month, :meses, GETDATE())
    GROUP BY obl.IdOrden, obl.IdBono
)
SELECT
    obs.IdArticulo AS idarticulo,
    ob.IdTrabajo   AS idtrabajo,
    ob.Matricula   AS matricula,
    COUNT(*)                    AS n,
    SUM(bm.min_produccion)      AS minutos,
    SUM(bm.min_montaje)         AS minutos_montaje,
    SUM(obs.Cantidad)           AS piezas
FROM bono_min bm
    JOIN Ordenes_Bonos ob          ON ob.IdOrden  = bm.IdOrden AND ob.IdBono  = bm.IdBono
    JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden = bm.IdOrden AND obs.IdBono = bm.IdBono
WHERE ob.IdEstado = 2            -- solo bonos terminados: los abiertos aún no miden nada
  AND obs.Cantidad > 0
  -- Un bono con montaje pero sin producción aportaría piezas con cero minutos
  -- y desinflaría el ritmo de toda la clave.
  AND bm.min_produccion > 0
GROUP BY obs.IdArticulo, ob.IdTrabajo, ob.Matricula
"""

#  Cuánto se tarda en montar el utillaje, por máquina y por trabajo. Un montaje
#  no produce piezas, así que no se puede estimar con el modelo de piezas: sin
#  esto, la barra de Elías preparando la 001 se estiraba hasta las 20:08 porque
#  se le aplicaba el tiempo de fabricar el bono entero.
#
#  Varía muchísimo entre máquinas y por eso se guarda por matrícula: la 107 monta
#  en 11 min de media y la 001 en 113. El tope de 480 min (una jornada) descarta
#  las líneas fantasma que nadie cerró.
SQL_MONTAJE = """
SELECT
    obl.Matricula AS matricula,
    ob.IdTrabajo  AS idtrabajo,
    COUNT(*)      AS n,
    AVG(CAST(DATEDIFF(minute, obl.Hinicial, obl.Hfinal) AS float)) AS media
FROM Ordenes_Bonos_Lineas obl
    JOIN Ordenes_Bonos ob ON ob.IdOrden = obl.IdOrden AND ob.IdBono = obl.IdBono
WHERE obl.IdOperacion IN (1, 2)
  AND obl.Hfinal > obl.Hinicial
  AND DATEDIFF(minute, obl.Hinicial, obl.Hfinal) BETWEEN 1 AND 480
  AND obl.Hinicial >= DATEADD(month, :meses, GETDATE())
GROUP BY obl.Matricula, ob.IdTrabajo
"""


# ─────────────────────────────────────────────────────────────────────
#  ATENCIÓN: qué parte del tiempo de una máquina necesita a alguien encima
# ─────────────────────────────────────────────────────────────────────
#  Una inyectora cicla sola: el operario monta el molde, arranca y se va a
#  otra cosa. El fichaje sigue abierto porque mide el BONO, no a la persona,
#  y de ahí salía que el planificador diera por ocupado a quien no lo está.
#
#  Esto se mide, no se declara: para cada línea cerrada, qué parte de su
#  duración transcurrió mientras ese MISMO operario tenía otra línea abierta.
#  Si la máquina trabaja sola, su tiempo aparecerá solapado una y otra vez.
#
#  Medido sobre 6 meses (11.923 líneas): el 12,9% de lo fichado es trabajo
#  simultáneo, y sale concentrado en máquinas concretas —inyectoras, la
#  Trumpf TruPunch, la célula robotizada, las enderezadoras—, no repartido.
#  El reparto es bimodal: trece máquinas por debajo del 40% de atención y el
#  resto al 100%, sin apenas nada en medio.
#
#  Se traen las líneas en crudo porque la cuenta es una UNIÓN de intervalos:
#  sumar los solapes por pares contaría dos veces al operario que lleva tres
#  máquinas a la vez y daría atenciones negativas. La unión la hace
#  `app.calculos.cola.medir_atencion`; aquí solo se lee.
#
#  El tope de 960 minutos descarta el fichaje fantasma que nadie cerró, igual
#  que el de 480 en SQL_MONTAJE.
# ─────────────────────────────────────────────────────────────────────

SQL_ATENCION = """
SELECT
    obl.IdEmpleado AS idempleado,
    obl.Matricula  AS matricula,
    obl.Hinicial   AS inicio,
    obl.Hfinal     AS fin
FROM Ordenes_Bonos_Lineas obl
WHERE obl.IdEmpleado IS NOT NULL
  AND obl.Matricula  IS NOT NULL
  AND obl.Hinicial   IS NOT NULL
  AND obl.Hfinal     IS NOT NULL
  AND obl.Hfinal     > obl.Hinicial
  AND DATEDIFF(minute, obl.Hinicial, obl.Hfinal) BETWEEN 1 AND 960
  AND obl.Hinicial  >= DATEADD(month, :meses, GETDATE())
"""


# ─────────────────────────────────────────────────────────────────────
#  AVANCE POR BONO
# ─────────────────────────────────────────────────────────────────────
#  Se cuentan TODAS las líneas del bono, no solo las de la ventana visible:
#  un bono que empezó ayer ya lleva tiempo consumido y lo que queda por hacer
#  hoy es menos. Son minutos-hombre (dos operarios a la vez gastan dos
#  minutos por cada minuto de reloj), igual que la media histórica.
#
#  OJO con las líneas fantasma: el ERP tiene líneas abiertas que nadie cerró
#  hace meses o años. Contarlas hasta GETDATE() dispara el consumo (medido:
#  un bono con 3.383 min "gastados" que en realidad lleva unas horas) y además
#  infla el recuento de operarios activos, que es el divisor del tiempo que
#  queda. Una línea abierta solo cuenta si empezó en las últimas
#  HORAS_LINEA_VIVA horas; el resto aporta cero.
#
#  `ordenes` es un bindparam EXPANDING: hay que prepararlo con
#  `.bindparams(bindparam("ordenes", expanding=True))` antes de ejecutarlo.
#  Lo hace `app.erp.lecturas.leer_avance`.
SQL_AVANCE = """
        WITH consumo AS (
            SELECT *, DATEDIFF(minute, Hinicial,
                CASE WHEN Hfinal >= Hinicial THEN Hfinal
                     WHEN Hfinal IS NULL AND Hinicial BETWEEN :limite AND :ahora
                     THEN :ahora ELSE Hinicial END) AS minutos_linea
            FROM Ordenes_Bonos_Lineas
            WHERE Hinicial IS NOT NULL AND IdOrden IN :ordenes
        )
        SELECT IdOrden AS idorden, IdBono AS idbono,
               SUM(minutos_linea) AS minutos,
               SUM(CASE WHEN IdOperacion = 0 THEN minutos_linea ELSE 0 END) AS min_produccion,
               SUM(CASE WHEN IdOperacion IN (1, 2) THEN minutos_linea ELSE 0 END) AS min_montaje,
               COUNT(DISTINCT CASE
                     WHEN Hfinal IS NULL
                      AND IdOperacion = 0
                      AND Hinicial BETWEEN :limite AND :ahora
                     THEN IdEmpleado END) AS operarios_activos,
               -- Los que están MONTANDO. Mientras solo hay preparación fichada
               -- no existe ninguna línea de producción abierta y el recuento de
               -- arriba da cero, así que no habría por quién dividir la
               -- fabricación que viene detrás: en 6469/40 los tres montadores
               -- recibían cada uno los 453 minutos del bono entero.
               COUNT(DISTINCT CASE
                     WHEN Hfinal IS NULL
                      AND IdOperacion IN (1, 2)
                      AND Hinicial BETWEEN :limite AND :ahora
                     THEN IdEmpleado END) AS operarios_montando,
               SUM(ISNULL(TotalPiezas, 0)) AS piezas
        FROM consumo
        GROUP BY IdOrden, IdBono
    """


# ─────────────────────────────────────────────────────────────────────
#  COLA: los bonos asignados que están por hacer
# ─────────────────────────────────────────────────────────────────────
#  Son los que nadie ha empezado (IdEstado = 0) y los que están a medias sin
#  nadie fichando, siempre que se hayan tocado hace poco: si no, se arrastra
#  para siempre trabajo que nadie va a retomar.
# ─────────────────────────────────────────────────────────────────────

SQL_COLA = """
SELECT
    v.IdEmpleado                              AS idempleado,
    ed.Nombre                                 AS nombre,
    ed.Apellidos                              AS apellidos,
    v.idorden                                 AS idorden,
    v.IdBono                                  AS idbono,
    v.ordenar                                 AS ordenar,
    v.CdgMaq                                  AS matricula,
    v.Maquina                                 AS descrip_maquina,
    v.Area                                    AS area,
    v.ArtFabricar                             AS descrip_salida,
    TRY_CAST(v.PiezasFabricar AS decimal(18,4)) AS objetivo,
    v.Fabricadas                              AS fabricadas,
    ob.IdTrabajo                              AS idtrabajo,
    ob.IdEstado                               AS idestado,
    obs.IdArticulo                            AS idarticulo_salida,
    fich.ultimo                               AS ultimo_fichaje,
    fich.montajes                             AS montajes
FROM persV_DatosAsociadoEmpleado v
    JOIN Ordenes_Bonos ob            ON ob.IdOrden  = v.idorden AND ob.IdBono  = v.IdBono
    JOIN Empleados_Datos ed          ON ed.IdEmpleado = v.IdEmpleado
    LEFT JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden = v.idorden AND obs.IdBono = v.IdBono
    OUTER APPLY (
        SELECT MAX(l.Fecha) AS ultimo,
               SUM(CASE WHEN l.IdOperacion IN (1, 2) THEN 1 ELSE 0 END) AS montajes
        FROM Ordenes_Bonos_Lineas l
        WHERE l.IdOrden = v.idorden AND l.IdBono = v.IdBono
    ) fich
WHERE (
        ob.IdEstado = 0
     OR (ob.IdEstado = 1 AND fich.ultimo >= DATEADD(day, :dias_arrancado, GETDATE()))
    )
  AND NOT EXISTS (
        SELECT 1 FROM Ordenes_Bonos_Lineas viva
        WHERE viva.IdOrden = v.idorden AND viva.IdBono = v.IdBono
          AND viva.Hfinal IS NULL
          AND viva.Hinicial >= DATEADD(hour, :horas_viva, GETDATE())
    )
"""


# ─────────────────────────────────────────────────────────────────────
#  SEMÁFORO: ¿puede ESTE operario trabajar ESTE bono ahora mismo?
# ─────────────────────────────────────────────────────────────────────
#  `dbo.persFTrazaordenesOperariosColor(idorden, idbono, idempleado)` es la
#  función del ERP que alimenta el semáforo del programa de producción. Es la
#  misma que consume la vista `PersVTrazaordenesOperarios`; se invoca aquí
#  directamente sobre nuestra consulta en vez de usar esa vista, porque la
#  vista además reescribe `ordenar = 0` como 999 — un valor que se colaría
#  como posición real y colocaría los bonos sin secuencia por delante de los
#  que sí la tienen.
#
#  Devuelve un RGB concatenado. El rojo NO es `IdEstado = 3`: de las 86 filas
#  rojas medidas, cero son bonos bloqueados. Es disponibilidad *para esa
#  persona* — falta material, lo tiene cogido otro, falta una fase previa. Por
#  eso el mismo bono puede ser verde para uno y rojo para otro.
#
#  La función está WITH ENCRYPTION: es una caja negra. Sabemos QUE algo está
#  rojo, no POR QUÉ.
# ─────────────────────────────────────────────────────────────────────

SEMAFORO = {
    '153255255': 'en_curso',    # azul  — la está trabajando ahora mismo
    '000204051': 'disponible',  # verde — puede ponerse con ella
    '255051051': 'bloqueada',   # rojo  — la tiene asignada pero no puede
}

SQL_SEMAFORO = """
SELECT v.idorden, v.IdBono AS idbono, v.IdEmpleado AS idempleado, col.color
FROM persV_DatosAsociadoEmpleado v
    JOIN Ordenes_Bonos ob ON ob.IdOrden = v.idorden AND ob.IdBono = v.IdBono
    OUTER APPLY dbo.persFTrazaordenesOperariosColor(v.idorden, v.IdBono, v.IdEmpleado) col
WHERE ob.IdEstado IN (0, 1)
"""


# ─────────────────────────────────────────────────────────────────────
#  AUSENCIAS (PORTALHR)
# ─────────────────────────────────────────────────────────────────────
#  Las ausencias no están en el ERP: viven en la base PORTALHR del mismo
#  servidor y se cruzan con `Conf_Empleados.codigotag`.
#
#  La causa concreta de una baja NO se deduce: hay 1.985 aprobadas y
#  `NotContribution` está a False en todas. Deducirla del texto vacío sería
#  acusar a alguien por una casilla en blanco.
#
#  Contrato de salida: una fila por empleado ausente el día `:dia`, con
#  `idempleado` (el del ERP, resuelto vía codigotag), `motivo` ya legible,
#  `desde`/`hasta` y la franja `parcial`/`hora_ini`/`hora_fin`.
SQL_AUSENCIAS = """
WITH ausencia AS (
    SELECT l.EmployeeId,
           CAST(l.[date] AS date)  AS desde,
           CAST(l.dateEnd AS date) AS hasta,
           'De baja'               AS motivo,
           CAST(0 AS bit)             AS parcial,
           CAST(NULL AS nvarchar(10)) AS hora_ini,
           CAST(NULL AS nvarchar(10)) AS hora_fin,
           1 AS prioridad
    FROM PORTALHR.dbo.Employees_Leaves l
    WHERE l.StatusId = 1
      AND :dia BETWEEN CAST(l.[date] AS date) AND CAST(l.dateEnd AS date)

    UNION ALL

    SELECT h.EmployeeId,
           CAST(h.[date] AS date), CAST(h.[date] AS date),
           CASE WHEN h.[Type] IN (0, 1, 2) THEN 'Vacaciones'
                WHEN h.[Type] = 5          THEN 'De baja'
                ELSE 'Ausencia' END,
           ISNULL(h.PartialDay, 0), h.StartTime, h.EndTime,
           2
    FROM PORTALHR.dbo.Employees_Holidays h
    WHERE h.StatusId = 1
      AND CAST(h.[date] AS date) = :dia
), cruzada AS (
    --  El cruce con el ERP es por el código de tarjeta. Si en PORTALHR falta
    --  ese código, por el nombre completo, sin tildes ni mayúsculas, y solo si
    --  el nombre da UN empleado: un homónimo no se adivina.
    --
    --  Es un apaño a un dato mal puesto, no el cruce normal. Ángel Diéguez
    --  tiene en PORTALHR una baja del 01/01/2025 al 31/12/2026 y la ficha sin
    --  AccessId, así que la app nunca le encontraba la baja y la hoja del día
    --  lo contaba como disponible. Lo correcto es rellenar su AccessId en
    --  PORTALHR (en el ERP su codigotag es el de su tarjeta); con eso este
    --  camino deja de usarse solo.
    SELECT COALESCE(por_tag.IdEmpleado,
                    CASE WHEN por_nombre.n = 1 THEN por_nombre.IdEmpleado END) AS idempleado,
           a.motivo, a.desde, a.hasta, a.parcial, a.hora_ini, a.hora_fin, a.prioridad
    FROM ausencia a
        JOIN PORTALHR.dbo.Employees e ON e.EmployeeId = a.EmployeeId
        --  Sin este guardarraíl, un tag vacío casaría '' = '' y cruzaría gente
        --  sin ninguna relación. Hoy no ocurre (medido: 0 casos), pero basta un
        --  codigotag en blanco para que ocurra.
        LEFT JOIN GOMEZYCRESPO.dbo.Conf_Empleados por_tag
               ON por_tag.codigotag = e.AccessId
              AND LTRIM(RTRIM(ISNULL(e.AccessId, ''))) <> ''
        OUTER APPLY (
            SELECT MIN(ed.IdEmpleado) AS IdEmpleado, COUNT(*) AS n
            FROM GOMEZYCRESPO.dbo.Empleados_Datos ed
            WHERE LTRIM(RTRIM(ISNULL(e.AccessId, ''))) = ''
              AND UPPER(LTRIM(RTRIM(ed.Nombre)) + ' ' + LTRIM(RTRIM(ed.Apellidos)))
                  COLLATE Latin1_General_CI_AI
                = UPPER(LTRIM(RTRIM(e.FullName))) COLLATE Latin1_General_CI_AI
        ) por_nombre
), resuelta AS (
    --  Una baja pesa más que un permiso: si alguien tiene las dos el mismo día
    --  manda la baja, que es la que explica de verdad por qué no está.
    SELECT idempleado, motivo, desde, hasta, parcial, hora_ini, hora_fin,
           ROW_NUMBER() OVER (PARTITION BY idempleado ORDER BY prioridad) AS rn
    FROM cruzada
    WHERE idempleado IS NOT NULL
)
SELECT idempleado, motivo, desde, hasta, parcial, hora_ini, hora_fin
FROM resuelta WHERE rn = 1
"""
