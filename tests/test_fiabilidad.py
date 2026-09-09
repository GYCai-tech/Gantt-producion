"""El backtest de las estimaciones, con bonos sinteticos y sin tocar el ERP.

Lo que se comprueba no es el numero que sale —eso depende del taller— sino que
la mecanica es honesta: que un bono no se juzgue a si mismo, que el sesgo
apunte en el sentido correcto y que un origen que no llega a usarse se vea.
"""
import pytest

from app.routers import fiabilidad as fi


def bono(idbono, real_min, piezas=100, articulo='A', trabajo=1, maquina='M1',
         teorico=None):
    return {'idorden': 1, 'idbono': idbono, 'fecha': None,
            'real_min': float(real_min), 'articulo': articulo,
            'descrip_articulo': 'Articulo ' + str(articulo), 'trabajo': trabajo,
            'maquina': maquina, 'piezas': float(piezas), 'teorico': teorico}


def analizar(filas, monkeypatch):
    monkeypatch.setattr(fi, '_leer_bonos', lambda: filas)
    monkeypatch.setattr(fi, '_cobertura_hoy', lambda: [])
    return fi.analizar()


def test_un_bono_no_se_juzga_a_si_mismo(monkeypatch):
    """Sin leave-one-out el bono raro entra en su propia media y se tapa.

    Tres bonos de 100 min y uno de 220, todos de 100 piezas. Descontandolo, el
    raro se mide contra 1,0 min/pieza y sale a 2,2x: se ve. Metiendolo en su
    media, esta sube a 1,3 y el mismo bono baja a 1,69x, un tramo mas abajo.
    """
    filas = [bono(i, 100) for i in range(3)] + [bono(3, 220)]
    d = analizar(filas, monkeypatch)
    art = next(o for o in d['origenes'] if o['origen'] == 'media_articulo')

    assert art['bonos'] == 4
    assert art['histograma'][6]['bonos'] == 1     # "> 2x", no el "1,5 - 2x"
    assert art['histograma'][5]['bonos'] == 0


def test_el_bono_raro_contamina_la_media_de_sus_companeros(monkeypatch):
    """Efecto real y no deseado, pero es el que tiene la app: la media es de
    minutos totales entre piezas totales, asi que un bono con el fichaje roto
    sube el listón de todos los demas del articulo. Por eso existe la lista de
    "por donde empezar": no se arregla con estadistica, se arregla en el ERP."""
    filas = [bono(i, 100) for i in range(4)] + [bono(4, 500)]
    d = analizar(filas, monkeypatch)
    art = next(o for o in d['origenes'] if o['origen'] == 'media_articulo')

    # Los cuatro normales salen a 0,5x -- parecen ir al doble de rapido de lo
    # esperado-- solo porque el quinto arrastra la media a 2 min/pieza.
    assert art['sesgo'] == 0.5
    assert art['histograma'][6]['bonos'] == 1     # y el raro, a 5x


def test_el_sesgo_dice_si_nos_quedamos_cortos(monkeypatch):
    # El escandallo dice 0,5 min/pieza y de verdad se tarda 1: la estimacion
    # se queda a la mitad en todos los bonos.
    filas = [bono(i, 100, teorico=0.5) for i in range(5)]
    d = analizar(filas, monkeypatch)
    teo = next(o for o in d['origenes'] if o['origen'] == 'teorico')

    assert teo['sesgo'] == 2.0       # el trabajo dura el doble de lo estimado
    assert d['global']['sesgo'] == 2.0


def test_el_escandallo_gana_a_la_media_tambien_en_el_backtest(monkeypatch):
    filas = [bono(i, 100, teorico=1.0) for i in range(5)]
    d = analizar(filas, monkeypatch)

    teo = next(o for o in d['origenes'] if o['origen'] == 'teorico')
    art = next(o for o in d['origenes'] if o['origen'] == 'media_articulo')
    assert teo['bonos'] == 5 and art['bonos'] == 0
    assert teo['mdape'] == 0          # 100 piezas x 1 min = los 100 reales


def test_un_escalon_que_no_llega_a_usarse_se_reporta_vacio(monkeypatch):
    filas = [bono(i, 100) for i in range(5)]
    d = analizar(filas, monkeypatch)

    vacio = next(o for o in d['origenes'] if o['origen'] == 'media_maquina')
    assert vacio['bonos'] == 0
    assert 'mdape' not in vacio       # la pagina lo pinta como "no se uso"


def test_sin_historico_suficiente_el_bono_no_se_juzga(monkeypatch):
    # Dos bonos: quitando el propio quedan 1, por debajo de _MIN_BONOS_MEDIA.
    d = analizar([bono(0, 100), bono(1, 100)], monkeypatch)
    assert d['sin_estimacion'] == 2
    assert d['global'] is None


def test_los_peores_dejan_fuera_lo_que_solo_es_grande(monkeypatch):
    """Un articulo bien estimado acumula horas de error solo por volumen."""
    # 'GRANDE' clava el ritmo; 'MALO' se equivoca por un factor de 3.
    filas = ([bono(i, 6000, piezas=6000, articulo='GRANDE') for i in range(5)]
             + [bono(i, 100, articulo='MALO') for i in range(10, 14)]
             + [bono(14, 900, articulo='MALO')])
    d = analizar(filas, monkeypatch)

    assert [p['articulo'] for p in d['peores']] == ['MALO']


@pytest.mark.parametrize('parte,total,esperado', [(52, 10707, 0.5), (5672, 10707, 53)])
def test_un_origen_minoritario_no_se_redondea_a_cero(parte, total, esperado):
    assert fi._pct(parte, total) == esperado
