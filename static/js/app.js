/* ============================================================
   GYC · Producción del día
   ------------------------------------------------------------
   Pinta un Gantt con UNA sola fuente: /api/lineas, que devuelve
   las líneas de bono activas del día tal cual las tiene el ERP.
   No hay nada calculado, estimado ni programado: cada barra es
   una línea real, de su Hinicial a su Hfinal.
   ============================================================ */

const App = (() => {

  const $ = (id) => document.getElementById(id);

  const state = {
    dia:      null,   // 'YYYY-MM-DD'
    lineas:   [],
    ahora:    null,   // hora del servidor en la última carga
    indice:   {},     // idempleado -> barras, para el tooltip
    cargando: false,
  };

  // Jornada de referencia. La ventana se ensancha sola si hay
  // trabajo fuera de estas horas.
  const HORA_MIN = 7;
  const HORA_MAX = 16;

  // Una máquina, un color: la misma matrícula se reconoce de un vistazo.
  const PALETA = [
    '#2f6feb', '#1f9254', '#c4710c', '#7c4dcc', '#0e8f9e',
    '#c0392b', '#4a6fa5', '#2d8659', '#a8562b', '#5d4b9c',
  ];

  // ── utilidades ────────────────────────────────────────────

  /* El ERP devuelve horas locales sin zona ("2026-09-04T07:54:00").
     Se parsean a mano en vez de con new Date(s) para que no haya
     ninguna duda de interpretación: son horas de fábrica, punto. */
  const parseDT = (s) => {
    if (!s) return null;
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/.exec(s);
    return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) : null;
  };

  const dos = (n) => String(n).padStart(2, '0');
  const iso = (d) => d.getFullYear() + '-' + dos(d.getMonth() + 1) + '-' + dos(d.getDate());
  const hhmm = (d) => d ? dos(d.getHours()) + ':' + dos(d.getMinutes()) : '—';

  const dur = (min) => {
    const m = Math.max(0, Math.round(min));
    return m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + dos(m % 60) + ' min';
  };

  const num = (n) => (n === null || n === undefined)
    ? '—'
    : Number(n).toLocaleString('es-ES', { maximumFractionDigits: 2 });

  const esc = (s) => String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  const color = (matricula) => {
    let h = 0;
    const s = String(matricula === null || matricula === undefined ? '' : matricula);
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return PALETA[h % PALETA.length];
  };

  const plural = (n, palabra) => n + ' ' + palabra + (n === 1 ? '' : 's');

  // ── carga ─────────────────────────────────────────────────

  async function cargar() {
    if (state.cargando) return;
    state.cargando = true;
    $('btn-recargar').classList.add('is-busy');
    try {
      const r = await fetch('/api/lineas?dia=' + state.dia);
      if (!r.ok) {
        const cuerpo = await r.json().catch(() => ({}));
        throw new Error(cuerpo.detail || ('HTTP ' + r.status));
      }
      const data = await r.json();
      state.lineas = data.lineas;
      state.ahora  = parseDT(data.ahora);
      render();
    } catch (e) {
      state.lineas = [];
      state.indice = {};
      $('rows').innerHTML = '';
      $('axis').innerHTML = '';
      $('stat').textContent = '';
      pintarAhora(false);
      pintarEstado('error', 'No se pudo leer del ERP', e.message);
    } finally {
      state.cargando = false;
      $('btn-recargar').classList.remove('is-busy');
    }
  }

  // ── render ────────────────────────────────────────────────

  function ventana(barras) {
    const hora = (d) => d.getHours() + d.getMinutes() / 60;
    let primera = HORA_MIN;
    let ultima  = HORA_MAX;
    for (const b of barras) {
      primera = Math.min(primera, Math.floor(hora(b.ini)));
      ultima  = Math.max(ultima,  Math.ceil(hora(b.fin)));
    }
    return [Math.max(0, primera), Math.min(24, Math.max(primera + 1, ultima))];
  }

  /* Un operario puede tener dos líneas solapadas (dos bonos a la vez, o un
     cierre que pisa al siguiente). Cada barra baja al primer carril libre
     para que ninguna tape a otra; la fila crece con el número de carriles. */
  function repartirCarriles(barras) {
    const finDeCarril = [];
    barras.sort((a, b) => a.ini - b.ini);
    for (const b of barras) {
      let i = finDeCarril.findIndex((f) => f <= b.ini);
      if (i === -1) { i = finDeCarril.length; finDeCarril.push(0); }
      finDeCarril[i] = b.fin;
      b.carril = i;
    }
    return Math.max(1, finDeCarril.length);
  }

  function render() {
    const esHoy = state.dia === iso(new Date());
    const ahora = state.ahora || new Date();
    const finDelDia = parseDT(state.dia + 'T23:59:00');

    // Una barra por línea.
    const barras = [];
    for (const l of state.lineas) {
      const ini = parseDT(l.inicio);
      if (!ini) continue;
      // Sin Hfinal la línea sigue abierta: si el día es hoy se estira hasta
      // ahora; si es un día pasado, el ERP nunca la cerró y no sabemos cuándo
      // acabó, así que llega al final del día y el tooltip lo dice.
      let fin = parseDT(l.fin) || (esHoy ? ahora : finDelDia);
      if (fin <= ini) fin = new Date(ini.getTime() + 60000);
      barras.push({ l: l, ini: ini, fin: fin });
    }

    if (!barras.length) {
      state.indice = {};
      $('rows').innerHTML = '';
      $('axis').innerHTML = '';
      $('stat').textContent = '0 líneas';
      pintarAhora(false);
      pintarEstado('vacio', 'Sin líneas de bono activas',
                   'No hay ninguna línea en estado 1 con fecha ' + state.dia + '.');
      return;
    }
    $('state').hidden = true;

    const rango   = ventana(barras);
    const desde   = rango[0];
    const hasta   = rango[1];
    const horas   = hasta - desde;
    const totalMin = horas * 60;
    const pos = (d) => (d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60 - desde * 60) / totalMin;

    // Eje de horas.
    const ticks = [];
    for (let i = 0; i < horas; i++) {
      ticks.push('<div class="axis__tick" style="left:' + (i / horas * 100) + '%;width:' +
                 (100 / horas) + '%">' + dos(desde + i) + ':00</div>');
    }
    $('axis').innerHTML = ticks.join('');

    // Agrupar por operario.
    const porOperario = new Map();
    for (const b of barras) {
      if (!porOperario.has(b.l.idempleado)) porOperario.set(b.l.idempleado, []);
      porOperario.get(b.l.idempleado).push(b);
    }
    const operarios = Array.from(porOperario.entries())
      .map(([id, bs]) => ({ id: id, nombre: bs[0].l.empleado, barras: bs }))
      .sort((a, b) => a.nombre.localeCompare(b.nombre, 'es'));

    $('rows').innerHTML = operarios.map((op) => {
      const carriles = repartirCarriles(op.barras);
      const minutos  = op.barras.reduce((s, b) => s + (b.fin - b.ini) / 60000, 0);

      const bars = op.barras.map((b, i) => {
        const izq = Math.max(0, Math.min(1, pos(b.ini)));
        const der = Math.max(0, Math.min(1, pos(b.fin)));
        const top = 'calc(var(--row-pad) + ' + b.carril + ' * (var(--bar-h) + var(--bar-gap)))';
        return '<div class="bar' + (b.l.abierta ? ' bar--abierta' : '') + '"' +
               ' style="--c:' + color(b.l.matricula) + ';left:' + (izq * 100) + '%;width:' +
               ((der - izq) * 100) + '%;top:' + top + '"' +
               ' data-op="' + op.id + '" data-i="' + i + '"' +
               ' onmousemove="App.tip(event)" onmouseleave="App.tipOff()">' +
                 '<span class="bar__id">' + b.l.idorden + '/' + b.l.idbono + '</span>' +
                 '<span class="bar__txt">' + esc(b.l.descrip_salida) + '</span>' +
               '</div>';
      }).join('');

      return '<div class="row">' +
               '<div class="rail">' +
                 '<span class="row__name">' + esc(op.nombre) + '</span>' +
                 '<span class="row__meta">' + plural(op.barras.length, 'línea') + ' · ' + dur(minutos) + '</span>' +
               '</div>' +
               '<div class="lanes" style="--lanes:' + carriles + ';--horas:' + horas + '">' + bars + '</div>' +
             '</div>';
    }).join('');

    // Línea de "ahora" (solo si el día visible es hoy y cae dentro del rango).
    // Va dentro de #rows para cubrir justo el alto de las filas, así que hay
    // que volver a colgarla después de reescribir el innerHTML.
    const p = pos(ahora);
    pintarAhora(esHoy && p >= 0 && p <= 1, p);

    const abiertas = barras.filter((b) => b.l.abierta).length;
    $('stat').textContent = plural(barras.length, 'línea') + ' · ' +
                            plural(operarios.length, 'operario') +
                            (abiertas ? ' · ' + abiertas + ' en curso' : '');

    // Índice para el tooltip.
    state.indice = {};
    for (const op of operarios) state.indice[op.id] = op.barras;
  }

  let _nowline = null;

  function pintarAhora(visible, p) {
    if (!_nowline) {
      _nowline = document.createElement('div');
      _nowline.className = 'nowline';
    }
    _nowline.hidden = !visible;
    if (!visible) return;
    _nowline.style.left = 'calc(var(--rail-w) + (100% - var(--rail-w)) * ' + p + ')';
    $('rows').appendChild(_nowline);
  }

  function pintarEstado(tipo, titulo, pista) {
    const el = $('state');
    el.hidden = false;
    el.className = 'state' + (tipo === 'error' ? ' state--error' : '');
    el.innerHTML = '<div class="state__title">' + esc(titulo) + '</div>' +
                   '<div class="state__hint">' + esc(pista) + '</div>';
  }

  // ── tooltip ───────────────────────────────────────────────

  const fila = (k, v) => '<div class="tip__row"><span class="tip__k">' + k +
                         '</span><span class="tip__v">' + v + '</span></div>';

  function tip(ev) {
    const el = ev.currentTarget;
    const grupo = state.indice[el.dataset.op];
    const b = grupo && grupo[+el.dataset.i];
    if (!b) return;
    const l = b.l;
    const t = $('tip');

    const fin = l.abierta
      ? (state.dia === iso(new Date()) ? 'en curso' : 'sin hora de fin en el ERP')
      : hhmm(b.fin);

    t.innerHTML =
      '<div class="tip__head">Orden ' + l.idorden + ' · Bono ' + l.idbono +
      ' · Línea ' + l.idlinea + '</div>' +
      fila('Operario',  esc(l.empleado)) +
      fila('Máquina',   esc(l.matricula) + ' — ' + esc(l.descrip_maquina)) +
      fila('Artículo',  esc(l.idarticulo_salida) + ' — ' + esc(l.descrip_salida)) +
      fila('Piezas',    num(l.piezas_a_fabricar)) +
      fila('Operación', l.idoperacion) +
      fila('Horario',   hhmm(b.ini) + ' – ' + fin) +
      fila('Duración',  dur((b.fin - b.ini) / 60000));
    t.hidden = false;

    const m = 14;
    const x = Math.min(ev.clientX + m, window.innerWidth  - t.offsetWidth  - m);
    const y = Math.min(ev.clientY + m, window.innerHeight - t.offsetHeight - m);
    t.style.left = Math.max(m, x) + 'px';
    t.style.top  = Math.max(m, y) + 'px';
  }

  const tipOff = () => { $('tip').hidden = true; };

  // ── navegación ────────────────────────────────────────────

  function setDia(d) {
    if (!d) return;
    state.dia = d;
    $('dia').value = d;
    cargar();
  }

  function nav(delta) {
    const d = parseDT(state.dia + 'T00:00:00');
    d.setDate(d.getDate() + delta);
    setDia(iso(d));
  }

  const hoy = () => setDia(iso(new Date()));

  // ── arranque ──────────────────────────────────────────────

  function reloj() {
    $('clock').textContent = new Date().toLocaleTimeString('es-ES', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  }

  function init() {
    reloj();
    setInterval(reloj, 1000);
    // Refresco automático solo mirando hoy: el ERP se mueve en vivo.
    setInterval(() => {
      if (state.dia === iso(new Date()) && !document.hidden) cargar();
    }, 60000);
    hoy();
  }

  document.addEventListener('DOMContentLoaded', init);

  return { cargar: cargar, setDia: setDia, nav: nav, hoy: hoy, tip: tip, tipOff: tipOff };
})();
