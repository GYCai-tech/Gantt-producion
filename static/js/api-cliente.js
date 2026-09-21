/* ============================================================
   GYC · Cliente de peticiones compartido

   Todas las pantallas leen del mismo sitio y fallan de la misma
   manera. Aqui viven las dos cosas que se olvidaban una y otra vez
   al escribir `fetch(...).then(r => r.json())`:

   1. Mirar `response.ok`. Cuando el ERP esta caido la API contesta
      503 con `{"detail": "..."}`. Eso es JSON perfectamente valido,
      asi que `r.json()` no protesta y el objeto de error acaba
      ocupando el sitio de los datos. El TypeError salta despues, en
      el primer `.forEach`, lejos de donde estaba el problema.

   2. No dejar que una respuesta vieja pise a una nueva. El usuario
      cambia de dia o de zoom mas rapido de lo que responde el ERP, y
      la peticion anterior puede llegar la ultima y pintar el dia
      equivocado. `turno()` reparte controladores por nombre: pedir
      turno cancela el anterior del mismo nombre.

   JS nativo, sin dependencias ni paso de compilacion. Se carga antes
   que la pantalla que lo usa.
   ============================================================ */
window.ApiCliente = (() => {
  'use strict';

  // nombre de la cola -> controlador de la peticion que sigue viva
  const controladores = new Map();

  // Una cancelacion no es un error: quiere decir que hay una peticion
  // mas nueva en camino y que esta ya no interesa a nadie.
  const cancelada = e =>
    !!e && (e.name === 'AbortError' || e.cancelada === true);

  /* Pide el turno de la cola `nombre` y cancela el anterior.
     Devuelve la señal que hay que pasarle a `cargar` y `vigente()`,
     que dice si este turno sigue siendo el ultimo. Hace falta
     comprobarlo ADEMAS de cancelar: si la respuesta ya habia llegado
     cuando se cancelo, su continuacion todavia se ejecuta. */
  function turno(nombre) {
    const previo = controladores.get(nombre);
    if (previo) previo.abort();
    const ctrl = new AbortController();
    controladores.set(nombre, ctrl);
    return {
      señal: ctrl.signal,
      vigente: () => controladores.get(nombre) === ctrl,
      soltar: () => { if (controladores.get(nombre) === ctrl) controladores.delete(nombre); },
    };
  }

  /* Lee JSON de `url` y lanza con un mensaje que se pueda enseñar.
     El texto de los errores HTTP es el mismo que ya usan las otras
     pantallas: 'El ERP respondió ' + codigo. */
  async function cargar(url, { señal } = {}) {
    let r;
    try {
      r = await fetch(url, { signal: señal });
    } catch (e) {
      // `fetch` solo rechaza si la peticion no llego a completarse:
      // red caida, servidor apagado o cancelacion. Un 500 o un 503 SI
      // resuelven, y se miran justo debajo.
      if (cancelada(e)) throw e;
      const err = new Error('No se pudo contactar con el servidor');
      err.red = true;
      throw err;
    }
    if (!r.ok) {
      const err = new Error('El ERP respondió ' + r.status);
      err.status = r.status;
      throw err;
    }
    try {
      return await r.json();
    } catch (e) {
      if (cancelada(e)) throw e;
      throw new Error('La respuesta del servidor no es válida');
    }
  }

  /* Para datos de adorno: si fallan se devuelve `porDefecto` y la
     pantalla se pinta igual. Una cancelacion SI se propaga, porque
     significa que hay una navegacion posterior y no hay nada que
     pintar todavia. */
  function opcional(url, porDefecto, opciones) {
    return cargar(url, opciones).catch(e => {
      if (cancelada(e)) throw e;
      return porDefecto;
    });
  }

  return { cargar, opcional, turno, cancelada };
})();
