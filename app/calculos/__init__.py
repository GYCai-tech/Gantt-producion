"""Cálculo puro: calendario de taller, estimación, cola y fusión de barras.

Todo lo de aquí es una FUNCIÓN PURA de sus parámetros: nada abre conexiones,
nada lee la caché del ERP y nada llama a `datetime.now()`. La hora de
referencia entra siempre por parámetro (`ahora`), que es lo que permite probar
estas reglas con datos sintéticos y lo que hace reproducibles los golden.

Quien orquesta (la capa HTTP) lee del ERP y le pasa aquí los diccionarios ya
cargados: `teoricos`, `medias`, `montajes` y `avance`.
"""

from app.calculos.calendario import (JORNADA_FIN, JORNADA_INICIO,
                                     minutos_laborables_entre, siguiente_hueco,
                                     sumar_laborables)
from app.calculos.cola import (MIN_BLOQUE_SIN_TIEMPO, hueco_para, ocupacion_actual,
                               planificar_cola, sin_salida)
from app.calculos.estimacion import (OPERACION_MONTAJE, avisar_escandallo, estimar,
                                     minutos_montaje, proyectar)
from app.calculos.fusion import (continuar, dur_min, fundir_montaje,
                                 fundir_troceados)

__all__ = [
    "JORNADA_INICIO", "JORNADA_FIN",
    "siguiente_hueco", "sumar_laborables", "minutos_laborables_entre",
    "estimar", "proyectar", "minutos_montaje", "avisar_escandallo",
    "planificar_cola", "ocupacion_actual", "hueco_para", "sin_salida",
    "fundir_troceados", "fundir_montaje", "continuar", "dur_min",
    "OPERACION_MONTAJE", "MIN_BLOQUE_SIN_TIEMPO",
]
