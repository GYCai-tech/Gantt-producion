/* Hoja del día — qué tiene que hacer cada operario un día concreto.

   Se imprime la víspera y se cuelga para toda la planta: una o dos hojas con
   todos los operarios, para que cada uno vea lo suyo y lo de los demás.

   Lee /api/plan, el mismo que la rejilla de carga, así que no hay un segundo
   cálculo que pueda discrepar del Gantt. JS nativo, sin dependencias. */
const Hoja = (() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtH = m => m >= 60 ? `${Math.floor(m / 60)}h${m % 60 ? String(m % 60).padStart(2, '0') : ''}`
                            : `${m}m`;

  //  Las fechas y horas se leen del texto ISO que manda la API, sin pasar por
  //  `new Date(iso)`: el servidor manda hora local sin zona, y según el
  //  navegador eso se interpretaba como UTC y movía todas las horas.
  const hora = iso => String(iso).slice(11, 16);
  const fechaLarga = f => {
    const [y, m, d] = String(f).slice(0, 10).split('-').map(Number);
    const t = new Date(y, m - 1, d).toLocaleDateString('es-ES',
      { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
    return t.charAt(0).toUpperCase() + t.slice(1);
  };

  //  Hoy y los cinco laborables siguientes. Más allá el plan es una conjetura
  //  encadenada (ver el aviso de app/routers/plan.py) y no merece imprimirse.
  const DIAS = 6;

  //  Hasta cuántos bonos va un operario entero en la misma hoja al imprimir.
  const BONOS_BLOQUE_ENTERO = 8;

  let datos = null;
  let dia = null;        // índice dentro de datos.dias

  /* Un mismo bono puede llegar en dos trozos el mismo día: el montaje que está
     en marcha y la fabricación que va detrás. En la hoja es UNA línea, de la
     primera hora a la última. */
  function juntar(bonos) {
    const porBono = new Map();
    for (const b of bonos) {
      const clave = `${b.idorden}/${b.idbono}`;
      const a = porBono.get(clave);
      if (!a) {
        porBono.set(clave, { ...b, estados: new Set([b.estado]), tipos: new Set([b.tipo]) });
        continue;
      }
      if (b.inicio < a.inicio) a.inicio = b.inicio;
      if (b.fin > a.fin) a.fin = b.fin;
      a.min += b.min;
      a.min_operario += b.min_operario;
      a.automatica = a.automatica || b.automatica;
      if (b.op_inicio && (!a.op_inicio || b.op_inicio < a.op_inicio)) a.op_inicio = b.op_inicio;
      if (b.op_fin && (!a.op_fin || b.op_fin > a.op_fin)) a.op_fin = b.op_fin;
      a.estados.add(b.estado);
      a.tipos.add(b.tipo);
      a.viene = a.viene || b.viene;
      a.sigue = a.sigue || b.sigue;
      a.dia_n = Math.max(a.dia_n, b.dia_n);
      a.dias_n = Math.max(a.dias_n, b.dias_n);
      a.fin_indeterminado = a.fin_indeterminado || b.fin_indeterminado;
    }
    //  Las horas ISO tienen el mismo formato, así que se ordenan como texto.
    return [...porBono.values()].sort((x, y) => (x.inicio < y.inicio ? -1 : x.inicio > y.inicio ? 1 : 0));
  }

  //  Hora de fin con "≈" cuando la duración no tiene datos fiables. Decirlo con
  //  una palabra por línea partía cada fila en tres renglones y la hoja no
  //  cabía en dos páginas; al operario le basta saber que esa hora es orientativa.
  const aproxFin = b => (b.fin_indeterminado || b.estados.has('sin-estimar') ? '≈' : '');

  //  Máquina automática: al operario solo le toca el montaje, así que la hora
  //  que manda es la suya, y sin montaje en el día no tiene hora ("—").
  function horario(b) {
    if (!b.automatica) return `${hora(b.inicio)}–${aproxFin(b)}${hora(b.fin)}`;
    return b.op_inicio ? `${hora(b.op_inicio)}–${hora(b.op_fin)}` : '—';
  }

  //  Lo que hace la máquina sola va debajo de su nombre: el operario tiene que
  //  saber que está en marcha aunque no le cuente como trabajo. Va aquí y no
  //  en la columna de hora porque es la columna ancha, y en la estrecha partía
  //  en tres renglones y la hoja pasaba de dos páginas.
  function maquina(b) {
    if (!b.automatica) return esc(b.maquina);
    const texto = b.op_inicio
      ? `montaje; luego sola hasta ${aproxFin(b)}${hora(b.fin)}`
      : `sola de ${hora(b.inicio)} a ${aproxFin(b)}${hora(b.fin)}`;
    return `${esc(b.maquina)}<small class="hoja__maq">${texto}</small>`;
  }

  //  El operario va en una fila de cabecera propia y no en una columna a la
  //  izquierda: esa columna le quitaba ancho al artículo y a la máquina, que
  //  partían en dos renglones casi todas las filas, y la hoja no cabía en dos
  //  páginas. En una hoja colgada, además, así se encuentra cada uno antes.
  function bloque(p, c, bonos) {
    //  Un bloque largo puede partirse entre dos hojas; los demás van enteros.
    //  Si todos fueran enteros, el hueco que deja uno grande al pie de la
    //  primera página basta para llevar la hoja a tres (el viernes 25, con 67
    //  bonos, pasaba). El nombre nunca se queda solo al pie: ver el CSS.
    const largo = bonos.length > BONOS_BLOQUE_ENTERO ? ' hoja__op--largo' : '';
    return `<tbody class="hoja__op${largo}">
      <tr class="hoja__quien"><th colspan="4" scope="rowgroup">
        ${esc(p.nombre)}<span>${fmtH(c.min_operario)} de trabajo de ${fmtH(c.disponible_operario)}</span></th></tr>` +
      bonos.map(b => `<tr>
      <td class="hoja__hora">${horario(b)}</td>
      <td class="hoja__bono">${b.idorden}<span>·${b.idbono}</span></td>
      <td>${esc(b.art)}</td>
      <td>${maquina(b)}</td>
    </tr>`).join('') + `</tbody>`;
  }

  function pintarDias() {
    $('hoja-dias').innerHTML = datos.dias.map((x, i) =>
      `<button class="${i === dia ? 'is-active' : ''}" onclick="Hoja.setDia(${i})">${
        i === 0 ? 'Hoy' : esc(x.etiqueta)}</button>`).join('');
  }

  function pintar() {
    const d = datos, x = d.dias[dia];
    pintarDias();
    $('hoja-fecha').textContent = fechaLarga(x.fecha);
    //  Quien falta el día entero (baja, vacaciones) no sale: no se le puede
    //  contar trabajo a quien no va a estar. Y quien no tiene nada tampoco,
    //  porque la hoja es de lo que HAY que hacer.
    //  Un bono con menos de un minuto ese día no es trabajo de ese día: es el
    //  que está en marcha ahora sin fin estimado (el plan lo corta en "ahora")
    //  o el sobrante de uno que acaba nada más abrir. Salía como "08:06–08:06".
    const con = d.personas
      .filter(p => !p.dias[dia].ausencia)
      .map(p => ({ p, c: p.dias[dia], bonos: juntar(p.dias[dia].bonos).filter(b => b.min >= 1) }))
      .filter(x => x.bonos.length);
    //  Por nombre y no por carga: es una hoja colgada en la pared, y lo
    //  primero que hace cada uno es buscarse.
    con.sort((a, b) => a.p.nombre.localeCompare(b.p.nombre, 'es', { sensitivity: 'base' }));

    const tabla = $('hoja-tabla');
    tabla.querySelectorAll('tbody').forEach(t => t.remove());
    tabla.insertAdjacentHTML('beforeend', con.length
      ? con.map(({ p, c, bonos }) => bloque(p, c, bonos)).join('')
      : '<tbody><tr><td colspan="4" class="hoja__vacio">No hay trabajo previsto para este día.</td></tr></tbody>');
  }

  async function cargar() {
    const t = ApiCliente.turno('hoja');
    $('hoja-error').hidden = true;
    if (!datos) $('hoja-cargando').hidden = false;
    try {
      const d = await ApiCliente.cargar(`/api/plan?dias=${DIAS}&vista=empleado&ausencias=true`, { señal: t.señal });
      if (!t.vigente()) return;
      datos = d;
      //  Por defecto, el siguiente día laborable: la hoja se imprime la víspera.
      if (dia == null || dia >= d.dias.length) dia = d.dias.length > 1 ? 1 : 0;
      $('hoja-cargando').hidden = true;
      $('hoja-papel').hidden = false;
      pintar();
    } catch (e) {
      if (ApiCliente.cancelada(e)) return;
      $('hoja-cargando').hidden = true;
      $('hoja-error').hidden = false;
      //  Si ya había una hoja pintada se deja a la vista: vale más la última
      //  previsión buena que una pantalla vacía.
      $('hoja-error').textContent = 'No se pudo proyectar el plan: ' + e.message +
        (datos ? '. Se muestra la última hoja calculada.' : '.');
    } finally {
      t.soltar();
    }
  }

  function setDia(i) {
    dia = i;
    pintar();
  }

  document.addEventListener('DOMContentLoaded', cargar);
  return { cargar, setDia };
})();
