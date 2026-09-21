/* ============================================================
   GYC · Seguimiento de Producción — motor del Gantt
   Muestra actividad real: bonos en curso y completados.
   Eje en horas de trabajo (7–15, descanso 11:00–11:15).
   ============================================================ */
const App = (() => {
  'use strict';

  // ── Configuración ──────────────────────────────────────────────────
  const RAIL    = 232;
  const BAR_H   = 36, LANE_GAP = 7, ROW_PAD = 10;
  // 07:00-15:00. Medido sobre los fichajes: las 15:00 concentran 506 cierres
  // y las 16:00 solo 98. Debe coincidir con JORNADA_FIN en api.py.
  const WORK_INI = 7, WORK_FIN = 15;
  const VIS_MIN  = (WORK_FIN - WORK_INI) * 60;       // 480 min/día
  const BREAK    = { ini: 11 * 60, fin: 11 * 60 + 15 };

  const ZOOM = [
    { key: 'Día',       days:  1, tick: 1 },
    { key: '3 días',    days:  3, tick: 2 },
    { key: 'Semana',    days:  5, tick: 2 },
    { key: '2 semanas', days: 10, tick: 4 },
  ];
  const MIN_PPH = 8;
  let _pph = 70;
  // Cuanto se agranda la escala sobre el ancho que cabe en pantalla. 1 es
  // "que entre entero"; por encima, el Gantt desborda y se navega con scroll.
  // Hace falta porque en 2 semanas cada dia queda en ~90 px y una barra de 20
  // minutos mide 4: se ve que hay algo pero no que es.
  const ZOOM_PASO = 1.5, ZOOM_MAX = 8;
  let _escala = 1;

  // Escalones de rejilla en minutos, de grueso a fino. Se coge el MAS FINO que
  // deje al menos `MIN_PX_LINEA` pixeles entre lineas, asi que la rejilla se
  // afina sola segun se acerca: a 70 px/hora sale de 10 en 10 minutos y al
  // 800% de minuto en minuto, sin que haya que elegir nada a mano.
  const PASOS_REJILLA = [60, 30, 15, 10, 5, 2, 1];
  const MIN_PX_LINEA  = 9;
  // Las etiquetas del eje piden mucho mas sitio que una linea: "07:30" ocupa
  // unos 30 px y hay que dejarlas respirar o se pisan.
  const PASOS_ETIQUETA = [240, 120, 60, 30, 15, 10, 5];
  const MIN_PX_ETIQUETA = 42;

  function _paso(escalones, minPx) {
    const pxMin = pph() / 60;
    let elegido = null;
    for (const p of escalones) if (p * pxMin >= minPx) elegido = p;
    return elegido;
  }
  const pasoRejilla  = () => _paso(PASOS_REJILLA, MIN_PX_LINEA);
  // Si no cabe ni el escalon mas grueso, la ventana ancha conserva su paso
  // fijo: en 2 semanas son las 07 y las 11, que es lo que habia.
  const pasoEtiqueta = () => _paso(PASOS_ETIQUETA, MIN_PX_ETIQUETA) || cfg().tick * 60;

  // "disponible" viene del semáforo per-operario del ERP (verde) cuando hay
  // dato; "parada" ahora también cubre el rojo de ese mismo semáforo (antes
  // se pintaba como un punto aparte -- un bono bloqueado para ESE operario
  // tiene el mismo resultado práctico que uno bloqueado a nivel de bono).
  const ST_LABEL = {
    plazo: 'En curso', completado: 'Completado',
    retrasada: 'Retrasada', riesgo: 'En riesgo', 'sin-estimar': 'Sin datos fiables',
    //  Dos cosas distintas y el nombre importa en planta: BLOQUEADA es un
    //  bono que aun no se ha hecho y no se puede empezar; PARADA es uno que
    //  tuvo a alguien fichando y se quedo a medias.
    parada: 'Parada', bloqueada: 'Bloqueada',
    pausada: 'Pausada', parcial: 'Pausado (bono abierto)',
    programado: 'En espera', disponible: 'Disponible',
    'pendiente-cierre': 'Pendiente de cerrar',
    // Lo que queda por fabricar de un bono del que solo esta fichada la
    // preparacion. No es cola: el bono ya esta arrancado y ese tiempo ya
    // tiene reservados al operario y a la maquina.
    continuacion: 'Fabricación pendiente',
  };
  const ST_COLOR = {
    plazo: '#128fa6', completado: '#6b7689',
    //  "sin-estimar" era este mismo naranja que "riesgo", y no habia forma de
    //  distinguirlos. Ahora es gris, que es lo que ya usaban sus etiquetas
    //  (.tag--sin-estimar) y lo que de verdad dice: falta informacion, no hay
    //  un problema en el bono. El naranja queda para lo que pide accion.
    retrasada: '#d83b46', riesgo: '#c4710c', 'sin-estimar': '#79859a',
    //  La parada hereda el ambar del trabajo interrumpido; el granate se
    //  queda para lo que ni ha empezado.
    parada: '#b5651d', bloqueada: '#9a4b52',
    pausada: '#5b6b8a', parcial: '#c77b1f',
    programado: '#5b63b0', disponible: '#1f9254',
    'pendiente-cierre': '#3f7d9e',
    continuacion: '#128fa6',
  };

  // De donde ha salido el tiempo estimado de un bono (ver /api/items).
  const ORIGEN = {
    teorico:        'tiempo teorico',
    media_articulo: 'media del articulo',
    media_trabajo:  'media del trabajo',
    media_maquina:  'media de la maquina',
  };

  // ── Estado ─────────────────────────────────────────────────────────
  let vista = 'empleado';
  let zi = 0;                          // zoom por defecto: Día
  let winStart, winEnd, days = [];
  let allGrupos = [], grupos = [], items = [];
  const itemMap = new Map();
  let areaActive = 'todos', cargaFilter = 'con', selectedId = null, searchTerm = '';
  let soloSinTiempo = false;   // lo enciende el contador de aviso de la cabecera
  // {idempleado: nº de bonos} de quien tiene TODA su cola bloqueada en el
  // programa de produccion. Viene de /api/avisos, no de las barras: el aviso
  // es del operario y no debe depender de lo que quepa en la ventana.
  let sinSalida = {};
  // {idempleado: {motivo, desde, hasta}} de quien no esta en planta el dia
  // visible. `motivo` es SIEMPRE una de tres categorias -- "De baja",
  // "Vacaciones" o "Ausencia" --: el porque concreto es dato de salud y el
  // backend no lo manda, asi que aqui no hay nada que filtrar ni ocultar.
  // Viene del mismo /api/avisos y por el
  // mismo motivo que sinSalida: la ausencia es de la persona, no de sus barras,
  // y tiene que verse aunque ese dia no tenga ninguna asignada.
  let ausencias = {};

  // ── Utilidades de fecha ────────────────────────────────────────────
  const DAY = 86400000;
  const pad = n => String(n).padStart(2, '0');
  // YYYY-MM-DD en hora LOCAL. Con toISOString() saldria el dia ANTERIOR: la
  // medianoche local de Madrid son las 22:00Z de la vispera. Es el mismo
  // desfase que corrige _dia_local en el backend.
  const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const startOfDay = d => { const r = new Date(d); r.setHours(0,0,0,0); return r; };
  const addDays = (d, n) => new Date(+d + n * DAY);
  const isWeekend = d => d.getDay() === 0 || d.getDay() === 6;
  const fmtDt = s => s ? new Date(s).toLocaleString('es-ES',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
  const fmtDate = s => s ? new Date(s).toLocaleDateString('es-ES',{day:'2-digit',month:'2-digit',year:'numeric'}) : '—';
  const clamp = (v, a, b) => Math.min(Math.max(v, a), b);
  const fmtMin = m => m < 60 ? `${Math.round(m)} min`
                             : `${Math.floor(m / 60)} h ${pad(Math.round(m % 60))} min`;
  const fmtNum = n => Number(n).toLocaleString('es-ES', { maximumFractionDigits: 2 });

  const cfg = () => ZOOM[zi];
  const pph = () => _pph;
  const dayWidth = () => (VIS_MIN / 60) * pph();
  const timelineW = () => days.length * dayWidth();

  function computePph() {
    const avail = $('gantt').clientWidth - RAIL - 1;
    const horas = cfg().days * (WORK_FIN - WORK_INI);
    _pph = Math.max(MIN_PPH, avail / horas) * _escala;
  }

  // `anclaX` es el punto de la ventana que NO se debe mover, en pixeles desde
  // el borde izquierdo del Gantt. Con la rueda es el cursor —lo natural es que
  // se acerque hacia donde se esta mirando— y con los botones, el centro.
  function setEscala(factor, anclaX) {
    const nueva = Math.min(ZOOM_MAX, Math.max(1, factor));
    if (Math.abs(nueva - _escala) < 1e-9) return;
    const g = $('gantt');
    const vx = anclaX != null ? anclaX : (RAIL + (g.clientWidth - RAIL) / 2);
    // Punto de la linea de tiempo que hay ahora bajo ese pixel, en tanto por
    // uno del ancho total. El carril de nombres es sticky y se descuenta.
    const prop = (g.scrollLeft + vx - RAIL) / Math.max(1, timelineW());
    _escala = nueva;
    render();
    g.scrollLeft = prop * timelineW() - vx + RAIL;
    renderEscala();
  }
  const zoomIn  = () => setEscala(_escala * ZOOM_PASO);
  const zoomOut = () => setEscala(_escala / ZOOM_PASO);

  // Ctrl/Cmd + rueda hace zoom; la rueda sola sigue desplazando las filas, que
  // son 25 y hay que poder recorrerlas. `passive: false` es obligatorio para
  // poder cancelar el zoom del navegador.
  function montarRueda() {
    $('gantt').addEventListener('wheel', e => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const r = $('gantt').getBoundingClientRect();
      // Un paso mas suave que el de los botones: la rueda dispara muchos
      // eventos seguidos y con 1,5 por golpe se pasa de largo enseguida.
      setEscala(_escala * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX - r.left);
    }, { passive: false });
  }

  function renderEscala() {
    const b = $('zoom-nivel');
    if (!b) return;
    b.querySelector('[data-z="out"]').disabled = _escala <= 1;
    b.querySelector('[data-z="in"]').disabled  = _escala >= ZOOM_MAX;
    b.querySelector('[data-z="pct"]').textContent = Math.round(_escala * 100) + '%';
  }

  const $ = id => document.getElementById(id);

  function addWorkingDays(date, n) {
    let d = new Date(date), step = n >= 0 ? 1 : -1, cnt = 0;
    if (n === 0) { while (isWeekend(d)) d = addDays(d, 1); return startOfDay(d); }
    while (cnt < Math.abs(n)) { d = addDays(d, step); if (!isWeekend(d)) cnt++; }
    return startOfDay(d);
  }

  function buildDays() {
    while (isWeekend(winStart)) winStart = addDays(winStart, 1);
    days = [];
    let d = new Date(winStart);
    while (days.length < cfg().days) {
      if (!isWeekend(d)) days.push(startOfDay(d));
      d = addDays(d, 1);
    }
    winEnd = addDays(days[days.length - 1], 1);
  }

  // Tiempo → x en píxeles (solo jornada comprimida)
  function workX(dt) {
    const d = new Date(dt), d0 = startOfDay(d);
    let idx = days.findIndex(x => +x === +d0);
    let mins;
    if (idx === -1) {
      if (+d < +days[0]) return 0;
      let last = -1;
      for (let i = 0; i < days.length; i++) if (+days[i] <= +d0) last = i;
      if (last === -1) return 0;
      idx = last; mins = VIS_MIN;
    } else {
      mins = clamp((+d - (+d0 + WORK_INI * 3600000)) / 60000, 0, VIS_MIN);
    }
    return idx * dayWidth() + (mins / 60) * pph();
  }

  // ── Arranque ───────────────────────────────────────────────────────
  function init() {
    const now = new Date();
    winStart = startOfDay(now);
    buildDays();
    renderZoom();
    renderEscala();
    montarRueda();
    tickClock(); setInterval(tickClock, 30000);
    // Si el arranque falla no hay nada que enseñar, asi que lo unico que se
    // hace es contarlo: antes quedaba la pantalla en blanco y sin mensaje.
    loadGrupos()
      .then(ok => ok ? loadItems() : false)
      .then(ok => { if (ok) { setTimeout(scrollToNow, 100); maybeAutoRefresh(); } })
      .catch(marcarFallo);
    // El auto-refresco de 5 minutos es el que mas daño hacia: la excepcion se
    // tragaba y en pantalla se quedaba el Gantt ANTERIOR, que ademas tickClock
    // sigue repintando cada 30 s. El usuario veia datos de hace horas creyendo
    // que eran de ahora. Ahora `loadItems` nunca se rompe en silencio.
    setInterval(() => loadItems(), 300000);
  }

  const REFRESH_COOLDOWN_MIN = 5;
  function maybeAutoRefresh() {
    const last = +(localStorage.getItem('gyc_last_refresh') || 0);
    if (Date.now() - last < REFRESH_COOLDOWN_MIN * 60000) return;
    refrescar(true);
  }

  function tickClock() {
    $('clock').textContent = new Date().toLocaleString('es-ES',
      { weekday: 'long', day: '2-digit', month: 'long', hour: '2-digit', minute: '2-digit' });
    // El aviso de datos caducados envejece con el reloj: si no, diria "hace 1
    // min" durante toda la tarde.
    if (fallo) pintarAviso();
    if (items.length) render();
  }

  // ── Ultima lectura buena ───────────────────────────────────────────
  // Que se esta enseñando AHORA y de cuando es. Si una recarga falla se
  // vuelve a esto: un Gantt viejo pero coherente --dia, zoom, vista y barras
  // del mismo momento-- sirve para algo; uno con el dia nuevo y las barras
  // viejas, no. Lo que NO puede pasar es que se vea viejo sin avisar.
  let ultimoBueno = null;    // Date de la ultima carga que trajo datos
  let ventanaBuena = null;   // {vista, zi, winStart, allGrupos} de esa carga
  let fallo = null;          // {mensaje} mientras el aviso este puesto

  function guardarLecturaBuena() {
    ultimoBueno = new Date();
    ventanaBuena = { vista, zi, winStart: new Date(winStart), allGrupos };
    fallo = null;
    pintarAviso();
  }

  function marcarFallo(e) {
    fallo = { mensaje: (e && e.message) || 'Error desconocido' };
    volverALoBueno();
    pintarAviso();
  }

  // Deshace la navegacion que no se ha podido cargar. No toca nada si aun no
  // hay ninguna lectura buena: en el arranque no hay a donde volver.
  function volverALoBueno() {
    if (!ventanaBuena) return;
    const cambiaVista = ventanaBuena.vista !== vista;
    vista = ventanaBuena.vista;
    zi = ventanaBuena.zi;
    winStart = new Date(ventanaBuena.winStart);
    allGrupos = ventanaBuena.allGrupos;
    if (cambiaVista) {
      [...$('vista-tabs').children].forEach(b => b.classList.toggle('is-active', b.dataset.v === vista));
      $('gantt-corner').textContent = vista === 'maquina' ? 'Máquinas' : 'Operarios';
    }
    buildDays();
    renderZoom();
    renderEscala();
    renderAreas();
    applyArea();
    render();
  }

  function pintarAviso() {
    const el = $('gantt-error');
    if (!el) return;
    if (!fallo) { el.hidden = true; el.textContent = ''; return; }
    el.hidden = false;
    if (!ultimoBueno) {
      el.textContent = `No se pudieron cargar los datos: ${fallo.mensaje}. `
        + 'No hay nada que mostrar; vuelve a intentarlo con «Actualizar».';
      return;
    }
    const min = (Date.now() - +ultimoBueno) / 60000;
    const hace = min < 1 ? 'hace menos de un minuto' : 'hace ' + fmtMin(min);
    const hora = ultimoBueno.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
    el.textContent = `Sin contacto con el ERP: ${fallo.mensaje}. `
      + `Lo que ves es la lectura de las ${hora} (${hace}) y NO se está actualizando.`;
  }

  // ── Carga de datos ─────────────────────────────────────────────────
  async function loadGrupos() {
    const t = ApiCliente.turno('grupos');
    try {
      const datos = await ApiCliente.cargar(`/api/grupos?vista=${vista}`, { señal: t.señal });
      if (!t.vigente()) return false;
      allGrupos = datos;
      renderAreas();
      applyArea();
      return true;
    } catch (e) {
      // Una cancelacion es una navegacion posterior, no un fallo: quien la
      // provoco ya esta pintando lo suyo.
      if (ApiCliente.cancelada(e) || !t.vigente()) return false;
      marcarFallo(e);
      return false;
    } finally {
      t.soltar();
    }
  }

  // Devuelve si la carga acabo pintando datos nuevos. Nunca lanza: el que
  // llama no tiene que acordarse de poner un .catch() para que la pantalla
  // diga la verdad.
  async function loadItems() {
    buildDays();
    // Un solo turno para las dos peticiones: si el usuario cambia de dia, de
    // zoom o de vista mientras esta en vuelo, se cancela entera y la respuesta
    // lenta ya no puede pintar el dia equivocado.
    const t = ApiCliente.turno('items');
    const url = `/api/items?vista=${vista}&desde=${days[0].toISOString()}&hasta=${winEnd.toISOString()}`;
    try {
      // Si el aviso falla, el Gantt se pinta igual: es informacion de mas, no
      // la pantalla.
      const [its, avisos] = await Promise.all([
        ApiCliente.cargar(url, { señal: t.señal }),
        ApiCliente.opcional(`/api/avisos?vista=${vista}&dia=${ymd(days[0])}`, {}, { señal: t.señal }),
      ]);
      if (!t.vigente()) return false;
      items = its;
      sinSalida = avisos.sin_salida || {};
      ausencias = avisos.ausencias || {};
      itemMap.clear();
      items.forEach(i => itemMap.set(String(i.id), i));
      // Las áreas salen de las barras, así que se recalculan con cada carga.
      renderAreas();
      applyArea();
      render();
      updateSummary();
      guardarLecturaBuena();
      return true;
    } catch (e) {
      if (ApiCliente.cancelada(e) || !t.vigente()) return false;
      marcarFallo(e);
      return false;
    } finally {
      t.soltar();
    }
  }

  // ── Áreas ──────────────────────────────────────────────────────────
  // El área es del BONO, no de la persona: viene con cada barra y es la de su
  // máquina, igual que `PersVTrazaordenesOperarios.Area` (212 de 212 filas
  // coinciden). Antes se filtraba por las áreas del OPERARIO —las máquinas
  // que hubiera tocado en 90 días— y eso colaba barras ajenas: Javier Atanes
  // tiene 799 líneas de CHAPA y 8 de ESTRUCTURAS, así que al filtrar por
  // ESTRUCTURAS salía su fila entera y con ella el bono 6552/60, de CHAPA.
  const enArea = lista =>
    areaActive === 'todos' ? lista : lista.filter(i => i.area === areaActive);

  function renderAreas() {
    // Las áreas que de verdad tienen trabajo en la ventana, no el historial de
    // la plantilla: un área sin bonos no es un filtro que sirva de nada.
    const areas = ['todos', ...[...new Set(items.map(i => i.area).filter(Boolean))].sort()];
    // Si al cambiar de ventana el área elegida se queda sin barras, el filtro
    // dejaría la pantalla en blanco sin decir por qué.
    if (!areas.includes(areaActive)) areaActive = 'todos';
    $('areas').innerHTML = areas.map(a =>
      `<button class="area-pill ${a === areaActive ? 'is-active' : ''}" onclick="App.setArea('${a.replace(/'/g,"\\'")}')">${a === 'todos' ? 'Todas las áreas' : a}</button>`
    ).join('');
  }
  function setArea(a) { areaActive = a; renderAreas(); applyArea(); render(); }
  function applyArea() {
    if (areaActive === 'todos') { grupos = allGrupos; return; }
    // Solo las filas que tengan alguna barra DE ESA ÁREA. Quien no tiene nada
    // ahí no aparece, aunque sepa trabajar en esa sección.
    const conBarras = new Set(enArea(items).map(i => String(i.recurso_id)));
    grupos = allGrupos.filter(g => conBarras.has(String(g.id)));
  }

  // ── Render principal ───────────────────────────────────────────────
  function render() {
    buildDays();
    computePph();
    const g = $('gantt');
    const sl = g.scrollLeft, st = g.scrollTop;
    const W = timelineW();
    $('axis').style.width = W + 'px';
    renderAxis(W);
    renderBg(W);
    renderRows(W);
    updateRangeLabel();
    g.scrollLeft = sl; g.scrollTop = st;
  }

  function renderAxis(W) {
    const ax = $('axis'); ax.innerHTML = '';
    let lastMonth = -1;
    days.forEach((day, i) => {
      const left = i * dayWidth();
      const cell = document.createElement('div');
      cell.className = 'axis__day';
      cell.style.left = left + 'px'; cell.style.width = dayWidth() + 'px';
      const wdName = day.toLocaleDateString('es-ES', { weekday: 'short' }).replace('.', '');
      const mon = day.getMonth() !== lastMonth ? `<span class="mon">${day.toLocaleDateString('es-ES',{month:'short'}).replace('.','')}</span>` : '';
      lastMonth = day.getMonth();
      cell.innerHTML = dayWidth() > 60 ? `${wdName} ${day.getDate()}${mon}` : `${day.getDate()}`;
      ax.appendChild(cell);

      // Las horas del eje siguen el mismo escalon que la rejilla, redondeado a
      // lo que cabe escrito: una rejilla de minutos sin ninguna referencia
      // numerica no dice donde estas.
      const paso = pasoEtiqueta();
      if (paso > 0) {
        for (let m = 0; m < VIS_MIN; m += paso) {
          const t = document.createElement('div');
          t.className = 'axis__tick' + (m % 60 === 0 ? '' : ' is-min');
          t.style.left = (left + (m / 60) * pph()) + 'px';
          const h = WORK_INI + Math.floor(m / 60);
          t.textContent = paso >= 60 ? pad(h) : `${pad(h)}:${pad(m % 60)}`;
          ax.appendChild(t);
        }
      }
    });
  }

  function renderBg(W) {
    const bg = $('gantt-bg');
    bg.style.left = RAIL + 'px'; bg.style.width = W + 'px';
    bg.innerHTML = '';
    days.forEach((day, i) => {
      const left = i * dayWidth();
      const ln = document.createElement('div');
      ln.className = 'bg-dayline'; ln.style.left = left + 'px';
      bg.appendChild(ln);
      // Rejilla. El paso se afina al acercarse; la hora en punto se marca mas
      // fuerte que sus divisiones para no perder la referencia gruesa.
      const paso = pasoRejilla();
      if (paso) {
        for (let m = paso; m < VIS_MIN; m += paso) {
          const ln = document.createElement('div');
          ln.className = m % 60 === 0 ? 'bg-hourline' : 'bg-minline';
          ln.style.left = (left + (m / 60) * pph()) + 'px';
          bg.appendChild(ln);
        }
      }
      const br = document.createElement('div');
      br.className = 'bg-break';
      br.style.left = (left + (BREAK.ini / 60 - WORK_INI) * pph()) + 'px';
      br.style.width = ((BREAK.fin - BREAK.ini) / 60) * pph() + 'px';
      br.title = 'Descanso 11:00–11:15';
      bg.appendChild(br);
    });
    const end = document.createElement('div');
    end.className = 'bg-dayline'; end.style.left = W + 'px';
    bg.appendChild(end);

    const now = new Date();
    if (!isWeekend(now) && +now >= +days[0] && +now < +winEnd) {
      const nl = document.createElement('div');
      nl.className = 'bg-now'; nl.style.left = workX(now) + 'px';
      bg.appendChild(nl);
    }
  }

  function renderRows(W) {
    const cont = $('gantt-rows');
    cont.innerHTML = '';
    const recursoLabel = vista === 'maquina' ? 'máquinas' : 'operarios';
    if (!grupos.length) {
      cont.innerHTML = `<div class="gantt__empty">No hay ${recursoLabel} para esta área.</div>`;
      return;
    }
    // El contador "sin tiempo" de la cabecera filtra a solo esos bonos.
    const visibles = enArea(soloSinTiempo ? items.filter(i => i.sin_tiempo) : items);
    const byRes = new Map();
    visibles.forEach(i => {
      const k = String(i.recurso_id);
      if (!byRes.has(k)) byRes.set(k, []);
      byRes.get(k).push(i);
    });

    // Mirando HOY, "con actividad" es tener un fichaje ABIERTO ahora mismo
    // (Hfinal NULL, que el API manda como en_curso): quien cerro su bono a las
    // 9 ya no esta trabajando. En un dia PASADO no hay fichajes abiertos por
    // definicion, asi que ahi la pregunta util es otra —quien trabajo ese dia—
    // y vale cualquier barra; si no, el filtro dejaria la pantalla en blanco.
    const ventanaIncluyeHoy = days.some(d => +d === +startOfDay(new Date()));
    let lista = grupos;
    if (cargaFilter !== 'todos') {
      lista = grupos.filter(g => {
        const barras = byRes.get(String(g.id)) || [];
        // Quien lo tiene todo bloqueado cuenta como "con carga" aunque no
        // tenga ningun fichaje abierto ni barra ese dia: es precisamente a
        // quien hay que ver, y el filtro por defecto lo escondia. Solo sin
        // filtro de area: su bono puede ser de otra seccion y entonces la fila
        // no pertenece a la que se esta mirando.
        const atascado = areaActive === 'todos' && !!sinSalida[String(g.id)];
        // OJO: un ausente NO cuenta como "con actividad". Estar de baja o de
        // vacaciones es justo lo contrario de tener trabajo en marcha, asi que
        // su fila sale en "Todos" --donde no se filtra nada-- y en "Sin
        // actividad", que es donde toca buscarlo. El atasco si cuenta porque
        // ese si tiene bonos asignados, solo que no puede empezarlos.
        const has = ventanaIncluyeHoy
          ? (barras.some(i => i.en_curso) || atascado)
          : barras.length > 0;
        return cargaFilter === 'con' ? has : !has;
      });
    }
    if (searchTerm) {
      const t = searchTerm.toLowerCase();
      lista = lista.filter(grp => {
        if ((grp.nombre || '').toLowerCase().includes(t)) return true;
        if (String(grp.id).toLowerCase().includes(t)) return true;
        return (byRes.get(String(grp.id)) || []).some(it =>
          String(it.idorden).includes(t) ||
          // El CODIGO del articulo, no solo su descripcion: en planta se pide
          // "el 11703101", que es lo que lleva el plano, no "contorno tolva".
          (it.art_id || '').toLowerCase().includes(t) ||
          (it.art || '').toLowerCase().includes(t) ||
          (it.operacion || '').toLowerCase().includes(t)
        );
      });
    }
    if (!lista.length) {
      cont.innerHTML = `<div class="gantt__empty">${
        searchTerm ? `Sin resultados para "<b>${esc(searchTerm)}</b>".` :
        cargaFilter === 'con'
          ? `Ningún${vista === 'maquina' ? 'a máquina' : ' operario'} ${ventanaIncluyeHoy ? 'con un fichaje abierto ahora mismo' : 'con actividad ese día'}.`
          : `Tod${vista === 'maquina' ? 'as las máquinas' : 'os los operarios'} ${ventanaIncluyeHoy ? 'tienen un fichaje abierto' : 'tuvieron actividad'}.`}</div>`;
      return;
    }

    // En la vista de MAQUINAS, cada maquina se parte en una fila por operario:
    // la de operarios ya ensena una maquina por barra, y esta es su simetrica
    // -- quien esta en cada maquina, sin tener que leer barra por barra.
    //
    // Una CUADRILLA -varios operarios en el mismo bono- se reparte: su barra
    // sale en la fila de cada uno de ellos, no en una fila conjunta con los
    // nombres juntos. La fila responde "que ha hecho esta persona en esta
    // maquina", y estando los tres en ese bono, a los tres les toca. Eso si:
    // la misma barra aparece varias veces, asi que sumar anchos por pantalla
    // no da horas-hombre.
    const filas = [];
    lista.forEach(grp => {
      const barras = byRes.get(String(grp.id)) || [];
      if (vista !== 'maquina' || !barras.length) {
        filas.push({ grp, barras, sub: grp.sub });
        return;
      }
      const porOperario = new Map();
      barras.forEach(it => {
        //  `operarios` viene del backend como "A, B, C" (un join por comas),
        //  y los nombres no llevan coma: partir por ella es seguro.
        const nombres = (it.operarios || '').split(',')
          .map(s => s.trim()).filter(Boolean);
        (nombres.length ? nombres : ['Sin operario']).forEach(op => {
          if (!porOperario.has(op)) porOperario.set(op, []);
          porOperario.get(op).push(it);
        });
      });
      [...porOperario.entries()]
        .sort((a, b) => a[0].localeCompare(b[0], 'es', { sensitivity: 'base' }))
        .forEach(([op, its]) => filas.push({ grp, barras: its, sub: op }));
    });

    filas.forEach(({ grp, barras, sub }) => {
      // Orden cronológico por inicio: el algoritmo de carriles de abajo es un
      // *greedy interval scheduling* y solo es correcto si los intervalos se
      // procesan en ese orden. Antes se ordenaba primero por tipo (real antes
      // que trabajado/programado/parcial) — eso hacía que la barra "real" de
      // ahora reservara el carril 0 antes de procesar sesiones pasadas del
      // mismo bono que no se solapan con ella, empujándolas a otro carril sin
      // motivo (el bono "saltaba" de fila en vez de seguir contiguo).
      const TIPO_PRIO = { real: 0, trabajado: 1, programado: 2 };
      const its = barras.slice()
        .sort((a, b) => {
          const d = new Date(a.start) - new Date(b.start);
          return d !== 0 ? d : (TIPO_PRIO[a.tipo] ?? 3) - (TIPO_PRIO[b.tipo] ?? 3);
        });

      // Las dos vistas usan los mismos intervalos calculados por el servidor.
      //
      // El carril se reparte POR BONO, no por barra. Un bono llega partido en
      // varias barras --lo trabajado ayer, lo que esta en curso, lo proyectado,
      // cada sesion de fichaje-- y repartiendolas de una en una cada trozo
      // cogia el primer carril libre: el mismo bono aparecia arriba en un
      // tramo y abajo en el siguiente, y no habia forma de seguirlo en
      // horizontal. Ordenar por hora de inicio arreglaba solo el caso de la
      // barra "real"; el salto seguia en cuanto habia un hueco entre sesiones
      // y otro bono se colaba en medio.
      //
      // Ahora el bono reserva su carril desde su PRIMERA barra hasta la ULTIMA
      // y no lo suelta. Cuesta algun carril de mas --un bono con un hueco
      // grande lo mantiene ocupado-- y ese es justo el precio de que la fila
      // se lea de izquierda a derecha.
      const porBono = new Map();
      its.forEach(it => {
        //  Sin bono identificable, cada barra va por su cuenta: mejor eso que
        //  amontonar cosas sin relacion en el mismo carril.
        const k = it.idbono != null ? `${it.idorden}/${it.idbono}` : `_${it.id ?? Math.random()}`;
        const s = +new Date(it.start), e = +new Date(it.end);
        const g = porBono.get(k);
        if (g) { g.start = Math.min(g.start, s); g.end = Math.max(g.end, e); g.items.push(it); }
        else porBono.set(k, { start: s, end: e, items: [it] });
      });

      //  El carril se guarda APARTE y no en el propio item (`it._lane`): con
      //  las cuadrillas, la misma barra esta en la fila de varios operarios y
      //  escribirlo dentro haria que la ultima fila en calcularse le pisara el
      //  carril a las anteriores.
      const laneDe = new Map();
      const laneEnd = [];
      [...porBono.values()].sort((a, b) => a.start - b.start).forEach(g => {
        let lane = laneEnd.findIndex(end => end <= g.start);
        if (lane === -1) { lane = laneEnd.length; laneEnd.push(g.end); }
        else laneEnd[lane] = g.end;
        g.items.forEach(it => laneDe.set(it, lane));
      });
      const lanes = Math.max(1, laneEnd.length);
      const rowH = ROW_PAD * 2 + lanes * BAR_H + (lanes - 1) * LANE_GAP;

      // Todos los bonos de este operario en rojo en el programa de produccion:
      // no es que vaya justo, es que no puede empezar ninguno. El numero sale
      // de la cola entera, no de las barras que se ven.
      const atascado = sinSalida[String(grp.id)] || 0;
      // Quien no ha venido no esta "atascado": la ausencia manda sobre el aviso
      // de cola bloqueada. Claro que no puede empezar nada -- no esta -- y las
      // dos marcas a la vez solo dirian lo mismo dos veces, en dos colores.
      const ausente = ausencias[String(grp.id)] || null;

      // Una ausencia PARCIAL no es "no vino": una consulta medica de 07:00 a
      // 10:00 deja media jornada trabajada. Se marca la fila igual, pero sin
      // rayar la pista y diciendo COMO afecta a la jornada ("entra a las
      // 10:00"), no la franja en crudo: lo que hace falta saber es que ese dia
      // llego mas tarde. La frase viene resuelta del backend, que es quien
      // tiene el horario de jornada.
      const franja = (ausente && ausente.cuando) || '';

      const row = document.createElement('div');
      row.className = 'row'
        + (ausente ? ' is-ausente' + (ausente.parcial ? ' is-ausente--parcial' : '')
           : atascado ? ' is-sin-salida' : '');
      row.style.height = rowH + 'px';

      const label = document.createElement('div');
      label.className = 'row__label';
      if (ausente) {
        label.title = ausente.motivo
          + (franja ? ` · ${franja}` : '')
          + (ausente.desde && ausente.hasta && ausente.desde !== ausente.hasta
             ? ` · del ${fmtDate(ausente.desde)} al ${fmtDate(ausente.hasta)}` : '');
      } else if (atascado) {
        label.title = (atascado === 1
          ? 'El único bono que tiene asignado está bloqueado'
          : `Sus ${atascado} bonos asignados están bloqueados`)
          + ' en el programa de producción: ahora mismo no puede empezar nada.';
      }
      //  En maquinas, el subtitulo es el OPERARIO de esta fila (la matricula se
      //  pierde ahi, pero la maquina ya la nombra la linea de arriba y quien
      //  esta en ella es lo que no se sabia).
      label.innerHTML = `<div class="row__name">${esc(grp.nombre)}</div>` +
                        `<div class="row__sub">${esc(sub || '')}${lanes > 1 ? ` · ${lanes} paralelos` : ''}</div>` +
                        (ausente
                          ? `<div class="row__aviso row__aviso--ausente">${esc(ausente.motivo)}${franja ? ` · ${franja}` : ''}</div>`
                          : atascado
                          ? `<div class="row__aviso">Todo bloqueado · ${atascado} bono${atascado > 1 ? 's' : ''}</div>`
                          : '');

      const track = document.createElement('div');
      track.className = 'row__track'; track.style.width = W + 'px';
      track.dataset.rid = grp.id;

      its.forEach(it => {
        const top = ROW_PAD + (laneDe.get(it) || 0) * (BAR_H + LANE_GAP);
        const bar = buildBar(it, W, top);
        if (bar) track.appendChild(bar);
      });

      row.appendChild(label); row.appendChild(track);
      cont.appendChild(row);
    });
  }

  function buildBar(it, W, top) {
    let lx = workX(it.start), rx = workX(it.end);
    if (rx <= 0 || lx >= W) return null;
    lx = clamp(lx, 0, W); rx = clamp(rx, 0, W);
    const w = Math.max(rx - lx, 6);

    const bar = document.createElement('div');
    //  `has-paro` tiñe la barra de amarillo cuando esa linea de fichaje tiene
    //  una parada anotada. Va APARTE del estado: un bono puede ir en plazo y
    //  haber tenido una averia igualmente, asi que no es un estado mas.
    bar.className = `bar bar--${it.tipo} st-${it.estado}`
                  + (it.estimado ? ' is-estimado' : '')
                  + (it.paro ? ' has-paro' : '');
    bar.style.left = lx + 'px'; bar.style.width = w + 'px';
    bar.style.top = top + 'px'; bar.style.height = BAR_H + 'px';
    bar.dataset.id = it.id;
    if (String(it.id) === String(selectedId)) bar.classList.add('is-selected');

    const sub = it.operacion || it.art || '';
    const bonoLabel = it.idbono != null ? `·${it.idbono}` : '';
    bar.innerHTML = (it.tipo === 'real' && it.en_curso ? '<span class="bar__live"></span>' : '') +
                    (it.tipo === 'parcial' ? '<span class="bar__pause" title="Sesión cerrada; el bono sigue abierto">⏸</span>' : '') +
                    // El engranaje marca que hay montaje, tanto si la barra es
                    // solo montaje como si lo lleva dentro (barra fundida).
                    (it.es_montaje || it.min_montaje
                      ? `<span class="bar__setup" title="${it.es_montaje ? 'Montaje de utillaje: preparando la máquina, no fabricando' : 'Incluye ' + it.min_montaje + ' min de montaje de utillaje'}">⚙</span>` : '') +
                    //  El motivo va DENTRO de la barra, no solo en el tooltip:
                    //  es lo que distingue este amarillo del de "en riesgo",
                    //  que comparte color pero significa otra cosa.
                    (it.paro
                      ? `<span class="bar__paro" title="Parada anotada: ${esc(it.paro.motivo)}${
                          it.paro.minutos ? ' · ' + esc(fmtMin(it.paro.minutos)) : ''}">⏻</span>` : '') +
                    `<span class="bar__id">${esc(it.idorden)}<span class="bar__bono">${esc(bonoLabel)}</span></span>` +
                    (w > 60 ? `<span class="bar__sub">${esc(String(sub).slice(0, 30))}</span>` : '') +
                    // El numero de la barra es el exceso DE ESTA SESION, no el
                    // del bono: el del bono puede venir de otro dia y de otra
                    // persona, y escrito aqui no cuadra con nada de lo que se
                    // ve. El acumulado va en el tooltip.
                    (it.min_exceso_barra && w > 150
                      ? `<span class="bar__exceso-num">+${esc(fmtMin(it.min_exceso_barra))}</span>` : '');
    if (it.tipo === 'real' && it.fin_estimado != null) {
      const p = document.createElement('div');
      p.className = 'bar__prog';
      // Porcentaje de la barra VISIBLE, sin contar noches o fines de semana.
      p.style.width = (100 * clamp((workX(new Date()) - lx) / w, 0, 1)) + '%';
      bar.appendChild(p);
    }
    // Tramo de preparación dentro de la barra del bono: el ERP lo graba como
    // línea aparte y el backend las funde cuando van pegadas.
    if (it.pct_montaje > 0) {
      const m = document.createElement('div');
      m.className = 'bar__montaje';
      const finMontaje = new Date(+new Date(it.start) + it.min_montaje * 60000);
      m.style.width = (100 * clamp((workX(finMontaje) - lx) / w, 0, 1)) + '%';
      // Sin `title`: el div lleva pointer-events:none para no robarle el hover
      // a la barra, asi que un title aqui no se mostraria nunca. El dato va en
      // el tooltip de la barra (ver showTip).
      bar.appendChild(m);
    }
    // Lo que se ha pasado del tiempo teorico. `fin_teorico` es el INSTANTE en
    // que esta sesion agota el presupuesto del bono (el backend ya descuenta lo
    // gastado en sesiones anteriores). Si cae dentro de la barra, todo lo que
    // hay a su derecha es exceso. Si no se ha pasado, `fin_teorico` queda en el
    // borde o mas alla y no se pinta nada.
    // Se exige `min_exceso` y no solo que `fin_teorico` caiga dentro: como el
    // fin de una barra abierta es "ahora", el corte teorico queda unos segundos
    // por detras y pintaba una astilla roja en bonos que no se han pasado.
    if (it.fin_teorico && it.min_exceso_barra) {
      const xTeorico = workX(new Date(it.fin_teorico));
      // El exceso llega hasta AHORA, no hasta el final de la barra: en una
      // barra abierta el final es el fin PROYECTADO, y pintar de rojo trabajo
      // que aun no ha ocurrido hacia que el tramo midiera 155 min mientras la
      // etiqueta decia "+18". Ahora el largo del rojo y la cifra coinciden.
      const xAhora = Math.min(lx + w, workX(new Date()));
      if (xTeorico < xAhora - 1) {
        const ex = document.createElement('div');
        ex.className = 'bar__exceso';
        const desde = clamp((xTeorico - lx) / w, 0, 1);
        const hasta = clamp((xAhora   - lx) / w, 0, 1);
        ex.style.left  = (100 * desde) + '%';
        ex.style.width = (100 * (hasta - desde)) + '%';
        // Sin texto dentro: el tramo acaba en "ahora", asi que queda en MITAD
        // de la barra, justo encima del nombre. La cifra va en el flujo normal
        // (bar__exceso-num), empujada a la derecha, donde no puede solaparse.
        bar.appendChild(ex);
      }
    }

    bar.addEventListener('mouseenter', e => showTip(e, it));
    bar.addEventListener('mousemove', moveTip);
    bar.addEventListener('mouseleave', hideTip);
    bar.addEventListener('click', () => openDetalle(it.id));
    return bar;
  }

  // ── Datos clave de una barra ───────────────────────────────────────
  //  Los mismos ocho datos, en el mismo orden, al pasar el raton y al pinchar.
  //  Antes cada sitio ensenaba lo suyo: el tooltip traia la estimacion y el
  //  modal no, asi que pinchar una barra daba MENOS informacion que rozarla.
  //
  //  Devuelve pares [etiqueta, html] y no marcado: el tooltip los pinta como
  //  filas y el modal como lista de definicion, cada uno con su estilo.
  //
  //  Ojo con dos campos, que no estan en todas las barras (medido sobre los
  //  datos reales del ERP):
  //
  //    · `min_estimados` no existe en las programadas. En una barra que aun no
  //      ha empezado, lo que va a tardar ES `min_restantes`: no hay nada
  //      consumido que restar.
  //    · las `trabajado` no tienen estimacion de nada -- ya terminaron -- y en
  //      su lugar tienen el tiempo real medido.
  function datosClave(it) {
    const filas = [];
    const fin = it.fin_indeterminado
      ? `<span class="es-indet">Indeterminado</span>`
      : fmtDt(it.end) + (it.estimado ? ' ~' : '');

    filas.push(['Orden / Bono', `${esc(it.idorden)} / ${it.idbono || '—'}`]);
    filas.push(['Estado',
      `<span style="color:${ST_COLOR[it.estado] || '#79859a'}">●</span> ${ST_LABEL[it.estado] || it.estado_label || it.estado}`]);
    filas.push(['Artículo', esc([it.art_id, it.art].filter(Boolean).join(' · ') || '—')]);

    //  Que se esta haciendo, que NO es `it.operacion`: ese campo trae la
    //  maquina en la vista de operarios y el operario en la de maquinas. El
    //  tipo de trabajo real solo lo hay cuando alguien ha fichado; en una
    //  barra de cola todavia no se sabe, y decir "fabricacion" seria inventar.
    filas.push(['Operación', it.es_montaje ? '⚙ Montaje de utillaje'
      : it.tipo_trabajo === 'produccion' ? 'Fabricación'
      : '<span class="es-indet">Sin fichar todavía</span>']);
    //  Y la maquina/operario con su nombre verdadero, segun la vista.
    if (it.operacion) filas.push([vista === 'empleado' ? 'Máquina' : 'Operario', esc(it.operacion)]);

    if (it.tipo === 'trabajado') {
      //  Terminada: no hay nada que estimar, hay algo medido.
      //
      //  `min_real` son los minutos de FABRICACION. Cuando la barra lleva la
      //  preparacion fundida dentro, decir solo eso parece un error de la
      //  pantalla: 6751/10 ensenaba "1 min" ocupando de 07:51 a 08:09, porque
      //  los otros 17 son el montaje. Se dice entero o no se dice.
      filas.push(['Tiempo real', it.min_real == null ? '—'
        : it.min_montaje ? `${fmtMin(it.min_real)} de fabricación + ${fmtMin(it.min_montaje)} de preparación`
        : fmtMin(it.min_real)]);
      //  El resto de la columna dice DE DONDE sale el numero -- tiempo
      //  teorico, media del articulo, media de la maquina -- asi que aqui hay
      //  que decir lo mismo y no "medido, no estimado", que contestaba a otra
      //  pregunta y no se podia comparar con las demas.
      filas.push(['Origen del dato', 'fichaje del operario']);
      filas.push(['Tiempo restante', 'terminado']);
    } else {
      const total = it.min_estimados != null ? it.min_estimados : it.min_restantes;
      filas.push(['Tiempo estimado', it.sin_tiempo
        ? '<span class="es-indet">Sin tiempo teórico ni media</span>'
        : total != null ? fmtMin(total) : '—']);
      filas.push(['Origen del dato', it.sin_tiempo
        ? '<span class="es-indet">ninguno</span>'
        : (ORIGEN[it.origen_estimado] || it.origen_estimado || '—')]);
      filas.push(['Tiempo restante', it.min_restantes != null ? fmtMin(it.min_restantes) : '—']);
    }

    filas.push(['Hora inicial', fmtDt(it.start)]);
    filas.push([it.estimado ? 'Fin estimado' : 'Fin', fin]);
    return filas;
  }

  // ── Tooltip ────────────────────────────────────────────────────────
  function showTip(e, it) {
    const tip = $('tip');
    const rows = datosClave(it).map(([k, v]) => `<div class="tip__row">${k} <span>${v}</span></div>`);
    //  Debajo de los datos clave, el detalle de siempre.
    rows.push('<hr>');
    //  La parada explica por que esa barra esta en amarillo. Va arriba, junto
    //  al bono, y no perdida entre los tiempos: es el motivo de la marca.
    if (it.paro) rows.push(`<div class="tip__row">Parada <span>⏻ ${esc(it.paro.motivo)}${
      it.paro.minutos ? ' · ' + fmtMin(it.paro.minutos) : ''}</span></div>`);
    // El montaje ya sale arriba, en Operación. Aqui solo por que hay una barra
    // proyectada de un bono que ya esta en marcha.
    if (it.continuacion) rows.push(`<div class="tip__row">Tipo <span>⏭ Sigue a la preparación en curso</span></div>`);
    if (it.min_montaje) rows.push(`<div class="tip__row">Preparación <span>${fmtMin(it.min_montaje)} · ${it.pct_montaje}% de la barra</span></div>`);
    // El teorico contra lo que de verdad esta costando, que es la lectura que
    // pide el tramo rojo de la barra.
    if (it.min_exceso) {
      rows.push(`<div class="tip__row">Teórico <span>${fmtMin(it.min_estimados)}</span></div>`);
      // "Del bono" y no a secas: estos minutos son de todas sus sesiones, y
      // pueden ser de otro dia y de otra persona. El "+" de la barra es solo
      // el de esta sesion, y sin decirlo los dos numeros se leen como uno mal
      // calculado (6447/180: +23 min en la barra, 4 h 54 el bono).
      rows.push(`<div class="tip__row">Real <span style="color:#ff9a9a">${fmtMin(it.min_consumidos)} · ${fmtMin(it.min_exceso)} de más en el bono</span></div>`);
      if (it.min_exceso_barra && it.min_exceso_barra < it.min_exceso) {
        rows.push(`<div class="tip__row">De esta sesión <span>${fmtMin(it.min_exceso_barra)} de más</span></div>`);
      }
    }
    if (it.tipo === 'real') {
      if (it.progreso_piezas != null) rows.push(`<div class="tip__row">Progreso <span>${it.progreso_piezas}% de las piezas</span></div>`);
      if (it.operarios) rows.push(`<div class="tip__row">Operarios <span>${it.operarios}</span></div>`);
    }
    if (it.tipo === 'trabajado' || it.tipo === 'parcial') {
      // El tiempo real ya sale arriba en las `trabajado`; aqui solo para las
      // `parcial`, que no pasan por esa rama de `datosClave`.
      if (it.tipo === 'parcial' && it.min_real != null) rows.push(`<div class="tip__row">Tiempo real <span>${Math.round(it.min_real)} min</span></div>`);
      if (it.piezas)           rows.push(`<div class="tip__row">Piezas <span>${it.piezas}</span></div>`);
    }
    if (!it.sin_tiempo && it.min_pieza != null) {
      if (it.base_estimacion === 'piezas') {
        rows.push(`<div class="tip__row">Piezas <span>${fmtNum(it.piezas_hechas)} de ${fmtNum(it.piezas_objetivo)} · quedan ${fmtNum(it.piezas_pendientes)}</span></div>`);
        // Un bono que aun no ha empezado no tiene ritmo real que comparar.
        rows.push(it.min_pieza_real != null
          ? `<div class="tip__row">Ritmo <span>${fmtNum(it.min_pieza_real)} min/pieza · esperado ${fmtNum(it.min_pieza)}${it.excedido ? ' (va lento)' : ''}</span></div>`
          : `<div class="tip__row">Ritmo <span>${fmtNum(it.min_pieza)} min/pieza esperado</span></div>`);
      } else {
        rows.push(`<div class="tip__row">Piezas <span>${it.piezas_objetivo ? fmtNum(it.piezas_objetivo) : '—'} · ninguna declarada</span></div>`);
        rows.push(`<div class="tip__row">Estimado <span>${it.min_estimados} min en total</span></div>`);
        rows.push(`<div class="tip__row">Producción consumida <span>${it.min_consumidos} min${it.excedido ? ' · se ha pasado' : ''}</span></div>`);
      }
      // Sin esto la cuenta no cuadra a la vista: 60 piezas a 5,18 min/pieza
      // son 331 minutos y la barra mide 83, porque el tiempo estimado son
      // minutos-HOMBRE y el eje es un reloj.
      if (it.a_la_vez > 1) rows.push(`<div class="tip__row">Cuadrilla <span>${it.a_la_vez} operarios a la vez · ${fmtMin(it.min_hombre)} de trabajo</span></div>`);
    }
    // Trabajo a medias: sin esto no se entiende que un bono de 1080 piezas
    // solo tenga 396 por hacer sin que nadie lo haya empezado hoy.
    if (it.reanudado) {
      rows.push(`<div class="tip__row">A medias <span>${
        it.ultimo_fichaje ? 'ultimo fichaje ' + fmtDate(it.ultimo_fichaje) : 'sin fichaje abierto'
      }</span></div>`);
    }
    //  Inicio, fin y estado ya van arriba, en los datos clave.
    if (it.prev) rows.push(`<div class="tip__row">Prevista <span>${fmtDate(it.prev)}</span></div>`);
    //  Si no hubo nada que anadir al detalle, fuera el separador: un <hr> al
    //  final del tooltip deja una raya colgando sin nada debajo.
    if (rows[rows.length - 1] === '<hr>') rows.pop();
    const MARK = { real: '▶ ', trabajado: '✓ ', parcial: '⏸ ' };
    // El codigo va en la cabecera junto a la descripcion: en la barra no cabe
    // -- son 8 digitos fijos y se cortaba a la mitad, que es peor que no
    // ponerlo-- y aqui identifica el articulo sin robarle sitio a nada.
    const art = [it.art_id, it.art].filter(Boolean).map(esc).join(' · ');
    tip.innerHTML = `<b>${MARK[it.tipo] || ''}${esc(it.idorden)}</b>${art ? ' — ' + art : ''}<hr>${rows.join('')}`;
    tip.classList.add('is-visible');
    moveTip(e);
  }
  function moveTip(e) {
    const tip = $('tip');
    let x = e.clientX + 14, y = e.clientY + 14;
    const r = tip.getBoundingClientRect();
    if (x + r.width > innerWidth - 10)  x = e.clientX - r.width - 14;
    if (y + r.height > innerHeight - 10) y = e.clientY - r.height - 14;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  function hideTip() { $('tip').classList.remove('is-visible'); }

  // ── Modal detalle (solo lectura) ───────────────────────────────────
  function openDetalle(id) {
    const it = itemMap.get(String(id)); if (!it) return;
    selectedId = id;
    document.querySelectorAll('.bar.is-selected').forEach(b => b.classList.remove('is-selected'));
    const bar = document.querySelector(`.bar[data-id="${CSS.escape(String(id))}"]`);
    if (bar) bar.classList.add('is-selected');

    const grp = grupos.find(g => String(g.id) === String(it.recurso_id));
    $('d-orden').textContent = it.idorden;
    const AVISO = {
      real:      `▶ En curso · fin estimado`,
      trabajado: `✓ Completado`,
      parcial:   `⏸ Sesión cerrada · el bono sigue abierto, continúa en la cola`,
    };
    const aviso = it.continuacion
      ? '⏭ Queda por fabricar · el bono está arrancado y de momento solo tiene fichada la preparación'
      : (AVISO[it.tipo] || '');
    //  Los MISMOS datos clave que el tooltip, en el mismo orden: pinchar una
    //  barra no puede dar menos informacion que rozarla, que es lo que pasaba.
    const clave = datosClave(it)
      .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
    //  Lo que solo tiene sentido aqui, con sitio de sobra.
    const extra = [
      [vista === 'empleado' ? 'Operario' : 'Recurso', esc(grp ? grp.nombre : it.recurso_id)],
      it.operarios && it.a_la_vez > 1 ? ['Cuadrilla', `${it.a_la_vez} a la vez · ${esc(it.operarios)}`] : null,
      it.piezas_objetivo != null
        ? ['Piezas', `${fmtNum(it.piezas_hechas || 0)} de ${fmtNum(it.piezas_objetivo)}` +
            (it.piezas_pendientes != null ? ` · quedan ${fmtNum(it.piezas_pendientes)}` : '')]
        : (it.piezas != null ? ['Piezas', String(it.piezas)] : null),
      it.min_pieza != null ? ['Ritmo', `${fmtNum(it.min_pieza)} min/pieza esperado` +
        (it.min_pieza_real != null ? ` · real ${fmtNum(it.min_pieza_real)}` : '')] : null,
      it.min_montaje ? ['Preparación', `${fmtMin(it.min_montaje)} · ${it.pct_montaje}% de la barra`] : null,
      it.paro ? ['Parada', `⏻ ${esc(it.paro.motivo)}${it.paro.minutos ? ' · ' + fmtMin(it.paro.minutos) : ''}`] : null,
      it.reanudado ? ['A medias', it.ultimo_fichaje ? 'último fichaje ' + fmtDate(it.ultimo_fichaje) : 'sin fichaje abierto'] : null,
      it.prev ? ['Prevista', fmtDate(it.prev)] : null,
    ].filter(Boolean).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');

    $('d-body').innerHTML =
      `<div class="notice">${aviso}</div>` +
      `<dl class="dl">${clave}${extra}</dl>`;
    openModal('ov-detalle');
  }

  // ── Navegación / zoom ──────────────────────────────────────────────
  function nav(dir) {
    winStart = addWorkingDays(days[0], dir * cfg().days);
    buildDays();
    loadItems();
  }
  function today() {
    const now = new Date();
    if (cfg().days === 1) {
      winStart = startOfDay(now);
    } else {
      const dow = now.getDay();
      const daysToMon = dow === 0 ? 6 : dow - 1;
      winStart = startOfDay(addDays(now, -daysToMon));
    }
    buildDays();
    loadItems();
    setTimeout(scrollToNow, 120);
  }
  function setSearch(v) {
    searchTerm = v.trim().toLowerCase();
    render();
  }
  function setVista(v) {
    vista = v;
    areaActive = 'todos';
    searchTerm = '';
    const si = $('search-gantt'); if (si) si.value = '';
    [...$('vista-tabs').children].forEach(b => b.classList.toggle('is-active', b.dataset.v === v));
    $('gantt-corner').textContent = v === 'maquina' ? 'Máquinas' : 'Operarios';
    // Si el censo no llega, no se piden sus barras: quedarian las de la vista
    // anterior colgadas de unas filas que no son suyas.
    loadGrupos().then(ok => { if (ok) loadItems(); });
  }
  function setZoom(i) {
    zi = i;
    winStart = days[0];
    // La escala vuelve a 1: se acaba de pedir otra ventana, y conservar un
    // 400% de la anterior deja la pantalla en un sitio que nadie ha pedido.
    _escala = 1;
    buildDays();
    renderZoom();
    renderEscala();
    loadItems();
  }
  function renderZoom() {
    $('zoom').innerHTML = ZOOM.map((z, i) =>
      `<button class="${i === zi ? 'is-active' : ''}" onclick="App.setZoom(${i})">${z.key}</button>`).join('');
  }
  function scrollToNow() {
    const now = new Date();
    if (isWeekend(now) || +now < +days[0] || +now >= +winEnd) { $('gantt').scrollLeft = 0; return; }
    $('gantt').scrollLeft = Math.max(0, workX(now) - 220);
  }
  function updateRangeLabel() {
    const o = { day: '2-digit', month: 'short' };
    const a = days[0].toLocaleDateString('es-ES', o);
    const b = days[days.length - 1].toLocaleDateString('es-ES', o);
    $('range-label').textContent = days.length === 1 ? a : `${a} — ${b}`;
  }
  function updateSummary() {
    const en_curso   = items.filter(i => i.tipo === 'real').length;
    const trabajado  = items.filter(i => i.tipo === 'trabajado').length;
    const programado = items.filter(i => i.tipo === 'programado').length;
    const sinTiempo = items.filter(i => i.sin_tiempo).length;
    $('summary').innerHTML =
      `<span><span class="dot" style="background:var(--verde)"></span><b>${en_curso}</b> en curso</span>` +
      `<span><span class="dot" style="background:#6b7689"></span><b>${trabajado}</b> completadas</span>` +
      `<span><span class="dot" style="background:#8a93d8"></span><b>${programado}</b> en espera</span>` +
      (sinTiempo || soloSinTiempo
        ? `<span class="summary__warn${soloSinTiempo ? ' is-active' : ''}" onclick="App.toggleSinTiempo()"
                 title="Bonos sin tiempo teorico ni media. Pincha para ver solo esos.">` +
          //  Sin el triangulo: ahora es marca exclusiva de "en riesgo".
          `<b>${sinTiempo}</b> sin tiempo</span>`
        : '');
  }

  function toggleSinTiempo() {
    soloSinTiempo = !soloSinTiempo;
    render();
    updateSummary();
  }

  function setCarga(v) {
    cargaFilter = v;
    [...$('carga').children].forEach(b => b.classList.toggle('is-active',
      b.textContent.trim().toLowerCase().startsWith(
        v === 'todos' ? 'todos' : v === 'con' ? 'con' : 'sin')));
    render();
  }

  // ── Refresco ETL (Prefect) ─────────────────────────────────────────
  const _ESTADO_LBL = {
    SCHEDULED: 'En cola…', PENDING: 'Preparando…', RUNNING: 'Ejecutando…',
    COMPLETED: 'Listo', PAUSED: 'En pausa…', CANCELLING: 'Cancelando…',
  };
  let _refreshing = false;
  async function refrescar(auto = false) {
    if (_refreshing) return;
    const btn = $('btn-refresh'), lbl = $('refresh-label');
    _refreshing = true; btn.classList.add('is-busy'); lbl.textContent = 'Lanzando…';
    try {
      const r = await fetch('/api/refrescar', { method: 'POST' });
      if (r.status === 503 && auto) return;
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || 'No se pudo lanzar el flujo'); }
      const { flow_run_id, estado: est0 } = await r.json();
      if (!flow_run_id) throw new Error('Prefect no devolvió un id de ejecución');

      const FIN = ['COMPLETED', 'FAILED', 'CRASHED', 'CANCELLED'];
      const deadline = Date.now() + 180000;
      let estado = est0 || 'SCHEDULED';
      lbl.textContent = _ESTADO_LBL[estado] || 'Actualizando…';
      while (Date.now() < deadline) {
        await new Promise(res => setTimeout(res, 1500));
        try {
          const s = await (await fetch(`/api/refrescar/${flow_run_id}`)).json();
          if (s.estado) estado = s.estado;
        } catch { /* reintentar */ }
        lbl.textContent = _ESTADO_LBL[estado] || 'Actualizando…';
        if (FIN.includes(estado)) break;
      }
      if (estado === 'COMPLETED') {
        localStorage.setItem('gyc_last_refresh', String(Date.now()));
        lbl.textContent = 'Recargando…';
        // El ETL ha terminado, pero leer lo que ha dejado puede fallar igual.
        // Si falla, el aviso de la pantalla ya lo cuenta con detalle.
        const ok = await loadGrupos() && await loadItems();
        if (ok) toast(auto ? 'Datos actualizados al entrar' : 'Datos actualizados desde el ERP');
        else if (!auto) toast('El ETL terminó, pero no se pudieron leer los datos', true);
      } else if (FIN.includes(estado)) {
        if (!auto) toast('El flujo terminó en estado ' + estado, true);
      } else {
        if (!auto) toast('El flujo sigue ejecutándose; recarga al terminar', true);
      }
    } catch (e) {
      if (!auto) toast(e.message || 'Error al actualizar', true);
    } finally {
      _refreshing = false; btn.classList.remove('is-busy'); lbl.textContent = 'Actualizar';
    }
  }

  // ── Utilidades UI ──────────────────────────────────────────────────
  function openModal(id)  { $(id).classList.add('is-open'); }
  function closeModal(id) { $(id).classList.remove('is-open'); }
  let _toastT;
  function toast(msg, err) {
    const t = $('toast'); t.textContent = msg;
    t.className = 'toast is-visible' + (err ? ' is-error' : '');
    clearTimeout(_toastT); _toastT = setTimeout(() => t.classList.remove('is-visible'), 2600);
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c]));
  }

  document.addEventListener('click', e => { if (e.target.classList.contains('overlay')) e.target.classList.remove('is-open'); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') document.querySelectorAll('.overlay.is-open').forEach(o => o.classList.remove('is-open')); });
  let _rsT;
  window.addEventListener('resize', () => { clearTimeout(_rsT); _rsT = setTimeout(() => { if (grupos.length || items.length) render(); }, 150); });

  return {
    setArea, setCarga, setVista, setSearch, nav, today, setZoom, refrescar, openModal, closeModal, init,
    toggleSinTiempo, zoomIn, zoomOut,
  };
})();

document.addEventListener('DOMContentLoaded', App.init);
