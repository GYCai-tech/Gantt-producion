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
    parada: 'Bloqueada', pausada: 'Pausada', parcial: 'Pausado (bono abierto)',
    programado: 'En espera', disponible: 'Disponible',
    'pendiente-cierre': 'Pendiente de cerrar',
    // Lo que queda por fabricar de un bono del que solo esta fichada la
    // preparacion. No es cola: el bono ya esta arrancado y ese tiempo ya
    // tiene reservados al operario y a la maquina.
    continuacion: 'Fabricación pendiente',
  };
  const ST_COLOR = {
    plazo: '#128fa6', completado: '#6b7689',
    retrasada: '#d83b46', riesgo: '#c4710c', 'sin-estimar': '#c4710c',
    parada: '#9a4b52', pausada: '#5b6b8a', parcial: '#c77b1f',
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

  // ── Utilidades de fecha ────────────────────────────────────────────
  const DAY = 86400000;
  const pad = n => String(n).padStart(2, '0');
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
    loadGrupos()
      .then(() => loadItems())
      .then(() => { setTimeout(scrollToNow, 100); maybeAutoRefresh(); });
    setInterval(loadItems, 300000);
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
    if (items.length) render();
  }

  // ── Carga de datos ─────────────────────────────────────────────────
  async function loadGrupos() {
    allGrupos = await (await fetch(`/api/grupos?vista=${vista}`)).json();
    renderAreas();
    applyArea();
  }

  async function loadItems() {
    buildDays();
    const url = `/api/items?vista=${vista}&desde=${days[0].toISOString()}&hasta=${winEnd.toISOString()}`;
    // Si el aviso falla, el Gantt se pinta igual: es informacion de mas, no la
    // pantalla.
    const [its, avisos] = await Promise.all([
      fetch(url).then(r => r.json()),
      fetch(`/api/avisos?vista=${vista}`).then(r => r.json()).catch(() => ({})),
    ]);
    items = its;
    sinSalida = avisos.sin_salida || {};
    itemMap.clear();
    items.forEach(i => itemMap.set(String(i.id), i));
    // Las áreas salen de las barras, así que se recalculan con cada carga.
    renderAreas();
    applyArea();
    render();
    updateSummary();
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

    lista.forEach(grp => {
      // Orden cronológico por inicio: el algoritmo de carriles de abajo es un
      // *greedy interval scheduling* y solo es correcto si los intervalos se
      // procesan en ese orden. Antes se ordenaba primero por tipo (real antes
      // que trabajado/programado/parcial) — eso hacía que la barra "real" de
      // ahora reservara el carril 0 antes de procesar sesiones pasadas del
      // mismo bono que no se solapan con ella, empujándolas a otro carril sin
      // motivo (el bono "saltaba" de fila en vez de seguir contiguo).
      const TIPO_PRIO = { real: 0, trabajado: 1, programado: 2 };
      const its = (byRes.get(String(grp.id)) || []).slice()
        .sort((a, b) => {
          const d = new Date(a.start) - new Date(b.start);
          return d !== 0 ? d : (TIPO_PRIO[a.tipo] ?? 3) - (TIPO_PRIO[b.tipo] ?? 3);
        });

      // Las dos vistas usan los mismos intervalos calculados por el servidor.
      const laneEnd = [];
      its.forEach(it => {
        const s = +new Date(it.start);
        const e = +new Date(it.end);
        let lane = laneEnd.findIndex(end => end <= s);
        if (lane === -1) { lane = laneEnd.length; laneEnd.push(e); }
        else laneEnd[lane] = e;
        it._lane = lane;
      });
      const lanes = Math.max(1, laneEnd.length);
      const rowH = ROW_PAD * 2 + lanes * BAR_H + (lanes - 1) * LANE_GAP;

      // Todos los bonos de este operario en rojo en el programa de produccion:
      // no es que vaya justo, es que no puede empezar ninguno. El numero sale
      // de la cola entera, no de las barras que se ven.
      const atascado = sinSalida[String(grp.id)] || 0;

      const row = document.createElement('div');
      row.className = 'row' + (atascado ? ' is-sin-salida' : '');
      row.style.height = rowH + 'px';

      const label = document.createElement('div');
      label.className = 'row__label';
      if (atascado) {
        label.title = (atascado === 1
          ? 'El único bono que tiene asignado está bloqueado'
          : `Sus ${atascado} bonos asignados están bloqueados`)
          + ' en el programa de producción: ahora mismo no puede empezar nada.';
      }
      label.innerHTML = `<div class="row__name">${esc(grp.nombre)}</div>` +
                        `<div class="row__sub">${esc(grp.sub || '')}${lanes > 1 ? ` · ${lanes} paralelos` : ''}</div>` +
                        (atascado
                          ? `<div class="row__aviso">Todo bloqueado · ${atascado} bono${atascado > 1 ? 's' : ''}</div>`
                          : '');

      const track = document.createElement('div');
      track.className = 'row__track'; track.style.width = W + 'px';
      track.dataset.rid = grp.id;

      its.forEach(it => {
        const top = ROW_PAD + it._lane * (BAR_H + LANE_GAP);
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
    bar.className = `bar bar--${it.tipo} st-${it.estado}` + (it.estimado ? ' is-estimado' : '');
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
                    `<span class="bar__id">${esc(it.idorden)}<span class="bar__bono">${esc(bonoLabel)}</span></span>` +
                    (w > 60 ? `<span class="bar__sub">${esc(String(sub).slice(0, 30))}</span>` : '') +
                    (it.min_exceso && w > 150
                      ? `<span class="bar__exceso-num">+${esc(fmtMin(it.min_exceso))}</span>` : '');
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
    if (it.fin_teorico && it.min_exceso) {
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

  // ── Tooltip ────────────────────────────────────────────────────────
  function showTip(e, it) {
    const tip = $('tip');
    const rows = [];
    if (it.operacion) rows.push(`<div class="tip__row">Operación <span>${esc(it.operacion)}</span></div>`);
    rows.push(`<div class="tip__row">Bono <span>${it.idbono || '—'}</span></div>`);
    if (it.es_montaje) rows.push(`<div class="tip__row">Tipo <span>⚙ Montaje de utillaje</span></div>`);
    // Por que hay una barra proyectada de un bono que ya esta en marcha.
    if (it.continuacion) rows.push(`<div class="tip__row">Tipo <span>⏭ Sigue a la preparación en curso</span></div>`);
    if (it.min_montaje) rows.push(`<div class="tip__row">Preparación <span>${fmtMin(it.min_montaje)} · ${it.pct_montaje}% de la barra</span></div>`);
    // El teorico contra lo que de verdad esta costando, que es la lectura que
    // pide el tramo rojo de la barra.
    if (it.min_exceso) {
      rows.push(`<div class="tip__row">Teórico <span>${fmtMin(it.min_estimados)}</span></div>`);
      rows.push(`<div class="tip__row">Real <span style="color:#ff9a9a">${fmtMin(it.min_consumidos)} · ${fmtMin(it.min_exceso)} de más</span></div>`);
    }
    if (it.tipo === 'real') {
      if (it.progreso_piezas != null) rows.push(`<div class="tip__row">Progreso <span>${it.progreso_piezas}% de las piezas</span></div>`);
      if (it.operarios) rows.push(`<div class="tip__row">Operarios <span>${it.operarios}</span></div>`);
    }
    if (it.tipo === 'trabajado' || it.tipo === 'parcial') {
      if (it.min_real != null) rows.push(`<div class="tip__row">Tiempo real <span>${Math.round(it.min_real)} min</span></div>`);
      if (it.piezas)           rows.push(`<div class="tip__row">Piezas <span>${it.piezas}</span></div>`);
    }
    const fuente = ORIGEN[it.origen_estimado] || it.origen_estimado;
    if (it.sin_tiempo) {
      rows.push(`<div class="tip__row">Estimado <span>sin tiempo teorico ni media</span></div>`);
    } else if (it.min_pieza != null) {
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
      if (it.min_restantes != null) rows.push(`<div class="tip__row">Le queda <span>${fmtMin(it.min_restantes)}</span></div>`);
      // Sin esto la cuenta no cuadra a la vista: 60 piezas a 5,18 min/pieza
      // son 331 minutos y la barra mide 83, porque el tiempo estimado son
      // minutos-HOMBRE y el eje es un reloj.
      if (it.a_la_vez > 1) rows.push(`<div class="tip__row">Cuadrilla <span>${it.a_la_vez} operarios a la vez · ${fmtMin(it.min_hombre)} de trabajo</span></div>`);
      rows.push(`<div class="tip__row">Segun <span>${fuente}</span></div>`);
    }
    rows.push(`<div class="tip__row">Inicio <span>${fmtDt(it.start)}</span></div>`);
    // Sin estimacion fiable o pasado de presupuesto, `end` es donde quedo la
    // barra al no poder proyectar -- casi siempre "ahora"-- y darlo como hora
    // de fin dice justo lo contrario de lo que pasa.
    rows.push(it.fin_indeterminado
      ? `<div class="tip__row">Fin <span class="tip__indet">Indeterminado</span></div>`
      : `<div class="tip__row">Fin <span>${fmtDt(it.end)}${it.estimado ? ' ~' : ''}</span></div>`);
    if (it.prev) rows.push(`<div class="tip__row">Prevista <span>${fmtDate(it.prev)}</span></div>`);
    const MARK = { real: '▶ ', trabajado: '✓ ', parcial: '⏸ ' };
    const badge = `<span style="color:${ST_COLOR[it.estado] || '#79859a'}">●</span> ${ST_LABEL[it.estado] || it.estado_label}`;
    // El codigo va en la cabecera junto a la descripcion: en la barra no cabe
    // -- son 8 digitos fijos y se cortaba a la mitad, que es peor que no
    // ponerlo-- y aqui identifica el articulo sin robarle sitio a nada.
    const art = [it.art_id, it.art].filter(Boolean).map(esc).join(' · ');
    tip.innerHTML = `<b>${MARK[it.tipo] || ''}${esc(it.idorden)}</b>${art ? ' — ' + art : ''}<hr>${rows.join('')}` +
                    `<div class="tip__row" style="margin-top:6px">Estado <span>${badge}</span></div>`;
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
    $('d-body').innerHTML =
      `<div class="notice">${aviso}</div>` +
      `<dl class="dl">
        <dt>Operario</dt><dd>${esc(grp ? grp.nombre : it.recurso_id)}</dd>
        <dt>Bono</dt><dd>${it.idbono || '—'}${it.operacion ? ' · ' + esc(it.operacion) : ''}</dd>
        <dt>Artículo</dt><dd>${esc([it.art_id, it.art].filter(Boolean).join(' · ') || '—')}</dd>
        <dt>Inicio</dt><dd>${fmtDt(it.start)}</dd>
        <dt>Fin</dt><dd>${it.fin_indeterminado ? '<span class="dd-indet">Indeterminado</span>'
          : fmtDt(it.end) + (it.estimado ? ' <span style="color:var(--ink-3)">(est.)</span>' : '')}</dd>
        ${it.progreso != null ? `<dt>Progreso</dt><dd>${it.progreso}%</dd>` : ''}
        ${it.min_real != null ? `<dt>Tiempo real</dt><dd>${Math.round(it.min_real)} min</dd>` : ''}
        ${it.piezas  != null ? `<dt>Piezas</dt><dd>${it.piezas}</dd>` : ''}
        ${it.prev ? `<dt>Prevista</dt><dd>${fmtDate(it.prev)}</dd>` : ''}
      </dl>`;
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
    loadGrupos().then(() => loadItems());
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
          `▲ <b>${sinTiempo}</b> sin tiempo</span>`
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
        await loadGrupos();
        await loadItems();
        toast(auto ? 'Datos actualizados al entrar' : 'Datos actualizados desde el ERP');
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
