"""El calendario de taller: jornada de 07:00 a 15:00, de lunes a viernes.

Es la base de toda proyección: sin él, una barra que empieza a las 14:50 y dura
una hora acabaría a las 15:50, de noche y con la planta vacía.
"""
from datetime import datetime, timedelta

JORNADA_INICIO = 7
#  Medido sobre 6 meses de fichajes: el ultimo cierre del dia es 15:01 en 38
#  dias, 15:02 en 17, 15:00 en 12 y 15:03 en 10 -- 77 de 110. Por minuto, las
#  15:00 concentran 506 cierres y las 16:00 solo 98. La jornada acaba a las 15,
#  no a las 16: con 16 la app daba 540 min/dia cuando son 480, un 12,5% de
#  capacidad inflada en toda proyeccion. Debe coincidir con WORK_FIN en app.js
#  o las barras se pintan en el pixel equivocado.
JORNADA_FIN    = 15


def siguiente_hueco(dt: datetime) -> datetime:
    """El primer instante laborable a partir de `dt` (07:00–15:00, L-V)."""
    t = dt
    for _ in range(14):
        if t.weekday() >= 5:
            t = (t + timedelta(days=1)).replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
            continue
        if t.hour < JORNADA_INICIO:
            return t.replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
        if t.hour >= JORNADA_FIN:
            t = (t + timedelta(days=1)).replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
            continue
        return t
    return t


def sumar_laborables(inicio: datetime, minutos: float) -> datetime:
    """Avanza `minutos` de trabajo desde `inicio` sin salirse de la jornada.

    Cuenta la jornada entera (07:00–15:00 = 480 min) sin descontar el descanso
    de 11:00–11:15 a propósito: el eje del Gantt tampoco lo comprime, lo pinta
    como una banda. Descontarlo aquí desalinearía las barras del eje."""
    t = siguiente_hueco(inicio)
    restante = float(minutos)
    for _ in range(400):
        fin_jornada = t.replace(hour=JORNADA_FIN, minute=0, second=0, microsecond=0)
        hueco = (fin_jornada - t).total_seconds() / 60
        if restante <= hueco:
            return t + timedelta(minutes=restante)
        restante -= hueco
        t = siguiente_hueco(fin_jornada)
    return t


def minutos_laborables_entre(inicio: datetime, fin: datetime) -> float:
    """Mide el mismo calendario que usa la proyección, sin noches ni fines de semana."""
    t, total = inicio, 0.0
    while t < fin:
        apertura = t.replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
        cierre = t.replace(hour=JORNADA_FIN, minute=0, second=0, microsecond=0)
        if t.weekday() < 5:
            total += max(0.0, (min(fin, cierre) - max(t, apertura)).total_seconds() / 60)
        t = (t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return total
