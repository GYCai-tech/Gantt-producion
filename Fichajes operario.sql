DECLARE @IdEmpleado int =32;

SELECT
    v.IdEmpleado,
    LTRIM(RTRIM(ed.Nombre + ' ' + ISNULL(ed.Apellidos, ''))) AS empleado,
    v.ordenar,
    v.idorden,
    v.IdBono,
    v.Maquina,
    v.Area,
    v.ArtFabricar,
    obl.IdEstado,
    obl.IdOperacion,
    obl.Hinicial ,
    obl.Hfinal 
FROM GOMEZYCRESPO.dbo.persV_DatosAsociadoEmpleado v
    JOIN GOMEZYCRESPO.dbo.Ordenes_Bonos ob   ON ob.IdOrden    = v.idorden AND ob.IdBono = v.IdBono
    JOIN GOMEZYCRESPO.dbo.Ordenes_Bonos_Lineas obl   ON obl.IdOrden    = v.idorden AND obl.IdBono = v.IdBono
    JOIN GOMEZYCRESPO.dbo.Empleados_Datos ed ON ed.IdEmpleado = v.IdEmpleado
WHERE v.IdEmpleado = @IdEmpleado


