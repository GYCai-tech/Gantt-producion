/* ============================================================
   GYC · Seguimiento de Producción — motor del Gantt
   Muestra actividad real: bonos en curso y completados.
   Eje en horas de trabajo (7–16, descanso 11:00–11:15).
   ============================================================ */
const App = (() => {
  'use strict';

  // ── Configuración ──────────────────────────────────────────────────
  const RAIL    = 232;
  const BAR_H   = 36, LANE_GAP = 7, ROW_PAD = 10;
  const WORK_INI = 7, WORK_FIN = 16;
  const VIS_MIN  = (WORK_FIN - WORK_INI) * 60;       // 540 min/día
  const BREAK    = { ini: 11 * 60, fin: 11 * 60 + 15 };

  const ZOOM = [
    { key: 'Día',     days: 1, tick: 1 },
    { key: '3 días',  days: 3, tick: 2 },
    { key: 'Semana',  days: 5, tick: 2 },
  ];
  const MIN_PPH = 8;
  let _pph = 70;

  const ST_LABEL = {
    plazo: 'En curso', completado: 'Completado',
    retrasada: 'Retrasada', riesgo: 'En riesgo', 'sin-estimar': 'Sin estimar',
    parada: 'Bloqueada', pausada: 'Pausada', parcial: 'Pausado (bono abierto)',
    programado: 'En espera',
  };
  const ST_COLOR = {
    plazo: '#1f9254', completado: '#6b7689',
    retrasada: '#d83b46', riesgo: '#c4710c', 'sin-estimar': '#79859a',
    parada: '#9a4b52', pausada: '#5b6b8a', parcial: '#c77b1f',
    programado: '#5b63b0',
  };

  // ── Estado ─────────────────────────────────────────────────────────
  let vista = 'empleado';
  let zi = 0;                          // zoom por defecto: Día
  let winStart, winEnd, days = [];
  let allGrupos = [], grupos = [], items = [];
  const itemMap = new Map();
  let areaActive = 'todos', cargaFilter = 'con', selectedId = null, searchTerm = '';

  // ── Utilidades de fecha ────────────────────────────────────────────
  const DAY = 86400000;
  const pad = n => String(n).padStart(2, '0');
  const startOfDay = d => { const r = new Date(d); r.setHours(0,0,0,0); return r; };
  const addDays = (d, n) => new Date(+d + n * DAY);
  const isWeekend = d => d.getDay() === 0 || d.getDay() === 6;
  const fmtDt = s => s ? new Date(s).toLocaleString('es-ES',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
  const fmtDate = s => s ? new Date(s).toLocaleDateString('es-ES',{day:'2-digit',month:'2-digit',year:'numeric'}) : '—';
  const clamp = (v, a, b) => Math.min(Math.max(v, a), b);

  const cfg = () => ZOOM[zi];
  const pph = () => _pph;
  const dayWidth = () => (VIS_MIN / 60) * pph();
  const timelineW = () => days.length * dayWidth();

  function computePph() {
    const avail = $('gantt').clientWidth - RAIL - 1;
    const horas = cfg().days * (WORK_FIN - WORK_INI);
    _pph = Math.max(MIN_PPH, avail / horas);
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
    items = await (await fetch(url)).json();
    itemMap.clear();
    items.forEach(i => itemMap.set(String(i.id), i));
    render();
    updateSummary();
  }

  // ── Áreas ──────────────────────────────────────────────────────────
  // Un grupo puede pertenecer a varias áreas a la vez (ej. operario que
  // atiende máquinas de dos áreas en paralelo); vista=maquina sigue
  // mandando un único `area`, por eso se admiten ambas formas.
  const gruposAreas = g => (g.areas && g.areas.length) ? g.areas : [g.area || 'Sin área'];
  function renderAreas() {
    const areas = ['todos', ...new Set(allGrupos.flatMap(gruposAreas))];
    $('areas').innerHTML = areas.map(a =>
      `<button class="area-pill ${a === areaActive ? 'is-active' : ''}" onclick="App.setArea('${a.replace(/'/g,"\\'")}')">${a === 'todos' ? 'Todas las áreas' : a}</button>`
    ).join('');
  }
  function setArea(a) { areaActive = a; renderAreas(); applyArea(); render(); }
  function applyArea() {
    grupos = areaActive === 'todos' ? allGrupos : allGrupos.filter(g => gruposAreas(g).includes(areaActive));
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

      const tick = cfg().tick;
      if (tick > 0) {
        for (let h = WORK_INI; h < WORK_FIN; h += tick) {
          const t = document.createElement('div');
          t.className = 'axis__tick';
          t.style.left = (left + (h - WORK_INI) * pph()) + 'px';
          t.textContent = pad(h);
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
    const byRes = new Map();
    items.forEach(i => {
      const k = String(i.recurso_id);
      if (!byRes.has(k)) byRes.set(k, []);
      byRes.get(k).push(i);
    });

    let lista = grupos;
    if (cargaFilter !== 'todos') {
      lista = grupos.filter(g => {
        const has = (byRes.get(String(g.id)) || []).length > 0;
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
        cargaFilter === 'con' ? `Ningún${vista === 'maquina' ? 'a máquina' : ' operario'} con actividad en esta vista.` :
        `Tod${vista === 'maquina' ? 'as las máquinas' : 'os los operarios'} tienen actividad.`}</div>`;
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

      // Asignación de carriles (bonos solapados → carriles distintos)
      // e_vis coincide con el ancho visual de buildBar: en_curso se extiende a now+10min
      const laneEnd = [];
      its.forEach(it => {
        let s = +new Date(it.start);
        let e = +new Date(it.end);
        if (it.en_curso) e = Math.max(e, Date.now() + 10 * 60000);
        // Un "programado" es una proyección de cola que se recalcula en cada
        // refresco del servidor; entre refrescos puede quedar con un inicio
        // ya "pasado" frente al reloj del navegador. Sin este suelo, ese
        // desfase lo manda a un carril paralelo al del bono activo de su
        // mismo recurso, dando la falsa impresión de que los dos se trabajan
        // a la vez. Nunca puede aparecer antes de lo que puede aparecer un
        // "real" (mismo suelo de +10min) -- así siempre queda detrás, nunca al lado.
        if (it.tipo === 'programado') s = Math.max(s, Date.now() + 10 * 60000);
        let lane = laneEnd.findIndex(end => end <= s);
        if (lane === -1) { lane = laneEnd.length; laneEnd.push(e); }
        else laneEnd[lane] = e;
        it._lane = lane;
      });
      const lanes = Math.max(1, laneEnd.length);
      const rowH = ROW_PAD * 2 + lanes * BAR_H + (lanes - 1) * LANE_GAP;

      const row = document.createElement('div');
      row.className = 'row'; row.style.height = rowH + 'px';

      const label = document.createElement('div');
      label.className = 'row__label';
      label.innerHTML = `<div class="row__name">${esc(grp.nombre)}</div>` +
                        `<div class="row__sub">${esc(grp.sub || '')}${lanes > 1 ? ` · ${lanes} paralelos` : ''}</div>`;

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
    if (it.en_curso) rx = Math.max(rx, workX(new Date(Date.now() + 10 * 60000)));
    // Mismo suelo que en la asignación de carriles (ver renderRows): evita que
    // el píxel de inicio quede por delante del bono activo de su recurso.
    if (it.tipo === 'programado') lx = Math.max(lx, workX(new Date(Date.now() + 10 * 60000)));
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
                    `<span class="bar__id">${esc(it.idorden)}<span class="bar__bono">${esc(bonoLabel)}</span></span>` +
                    (w > 60 ? `<span class="bar__sub">${esc(String(sub).slice(0, 30))}</span>` : '');
    if (it.tipo === 'real' && it.progreso != null) {
      const p = document.createElement('div');
      p.className = 'bar__prog'; p.style.width = it.progreso + '%';
      bar.appendChild(p);
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
    if (it.tipo === 'real') {
      if (it.progreso != null) rows.push(`<div class="tip__row">Progreso <span>${it.progreso}%</span></div>`);
      if (it.operarios) rows.push(`<div class="tip__row">Operarios <span>${it.operarios}</span></div>`);
    }
    if (it.tipo === 'trabajado' || it.tipo === 'parcial') {
      if (it.min_real != null) rows.push(`<div class="tip__row">Tiempo real <span>${Math.round(it.min_real)} min</span></div>`);
      if (it.piezas)           rows.push(`<div class="tip__row">Piezas <span>${it.piezas}</span></div>`);
    }
    rows.push(`<div class="tip__row">Inicio <span>${fmtDt(it.start)}</span></div>`);
    rows.push(`<div class="tip__row">Fin <span>${fmtDt(it.end)}${it.estimado ? ' ~' : ''}</span></div>`);
    if (it.prev) rows.push(`<div class="tip__row">Prevista <span>${fmtDate(it.prev)}</span></div>`);
    const MARK = { real: '▶ ', trabajado: '✓ ', parcial: '⏸ ' };
    const badge = `<span style="color:${ST_COLOR[it.estado] || '#79859a'}">●</span> ${ST_LABEL[it.estado] || it.estado_label}`;
    tip.innerHTML = `<b>${MARK[it.tipo] || ''}${esc(it.idorden)}</b> — ${esc(it.art || '')}<hr>${rows.join('')}` +
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
    $('d-body').innerHTML =
      `<div class="notice">${AVISO[it.tipo] || ''}</div>` +
      `<dl class="dl">
        <dt>Operario</dt><dd>${esc(grp ? grp.nombre : it.recurso_id)}</dd>
        <dt>Bono</dt><dd>${it.idbono || '—'}${it.operacion ? ' · ' + esc(it.operacion) : ''}</dd>
        <dt>Artículo</dt><dd>${esc(it.art || '—')}</dd>
        <dt>Inicio</dt><dd>${fmtDt(it.start)}</dd>
        <dt>Fin</dt><dd>${fmtDt(it.end)}${it.estimado ? ' <span style="color:var(--ink-3)">(est.)</span>' : ''}</dd>
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
    buildDays();
    renderZoom();
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
    $('summary').innerHTML =
      `<span><span class="dot" style="background:var(--verde)"></span><b>${en_curso}</b> en curso</span>` +
      `<span><span class="dot" style="background:#6b7689"></span><b>${trabajado}</b> completadas</span>` +
      `<span><span class="dot" style="background:#8a93d8"></span><b>${programado}</b> en espera</span>`;
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
  };
})();

document.addEventListener('DOMContentLoaded', App.init);
