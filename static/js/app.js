/* ============================================================
   GYC · Planificador de Producción — motor del Gantt
   Eje en horas de trabajo (7–16, descanso 11:00–11:15).
   Unidad = día (con zoom). Bonos solapados se apilan en carriles.
   ============================================================ */
const App = (() => {
  'use strict';

  // ── Configuración ──────────────────────────────────────────────────
  const RAIL = 232;
  const BAR_H = 26, LANE_GAP = 5, ROW_PAD = 7;     // geometría de carriles
  const WORK_INI = 7, WORK_FIN = 16;               // jornada
  const VIS_MIN = (WORK_FIN - WORK_INI) * 60;      // 540 min visibles/día
  const BREAK = { ini: 11 * 60, fin: 11 * 60 + 15 };
  const SNAP_MIN = 15;

  // Zoom = nº de días que llenan el ancho de página (las horas se estiran hasta el final).
  const ZOOM = [
    { key: 'Día',     days: 1, tick: 1 },
    { key: '3 días',  days: 3, tick: 2 },
    { key: 'Semana',  days: 5, tick: 2 },
  ];
  const MIN_PPH = 8;        // ancho mínimo de hora (si no cabe, hay scroll horizontal)
  let _pph = 70;            // ancho de hora calculado para llenar el ancho disponible

  const ST_LABEL = {
    retrasada:'Retrasada', riesgo:'En riesgo', plazo:'En plazo', 'sin-estimar':'Sin estimar',
    vencida:'Vencida', urgente:'Urgente', normal:'En plazo', 'sin-fecha':'Sin fecha',
    parada:'Parada', pausada:'Pausada',
  };
  const ST_COLOR = {
    retrasada:'#d83b46', vencida:'#d83b46', riesgo:'#c4710c', urgente:'#c4710c',
    plazo:'#1f9254', normal:'#1f9254', 'sin-estimar':'#79859a', 'sin-fecha':'#79859a',
    parada:'#9a4b52', pausada:'#5b6b8a',
  };
  const SITU_KEY = { PARADA:'parada', PAUSADA:'pausada' };

  // ── Estado ─────────────────────────────────────────────────────────
  let vista = 'empleado';
  let zi = 0;
  let winStart, winEnd, days = [];
  let allGrupos = [], grupos = [], items = [];
  const itemMap = new Map();
  let backlog = [], areaActive = 'todos', cargaFilter = 'todos', selectedId = null;
  let colaSearch = '', colaTipo = 'todo', colaOrden = 'urgencia';
  let _recursosCache = { empleado: null, maquina: null };

  // ── Utilidades de fecha ────────────────────────────────────────────
  const HOUR = 3600000, DAY = 86400000;
  const pad = n => String(n).padStart(2, '0');
  const startOfDay = d => { const r = new Date(d); r.setHours(0,0,0,0); return r; };
  const addDays = (d, n) => new Date(+d + n * DAY);
  const isWeekend = d => d.getDay() === 0 || d.getDay() === 6;
  const toInput = d => `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const fmtDt = s => s ? new Date(s).toLocaleString('es-ES',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
  const fmtDate = s => s ? new Date(s).toLocaleDateString('es-ES',{day:'2-digit',month:'2-digit',year:'numeric'}) : '—';
  const clamp = (v, a, b) => Math.min(Math.max(v, a), b);

  const cfg = () => ZOOM[zi];
  const pph = () => _pph;
  const dayWidth = () => (VIS_MIN / 60) * pph();
  const timelineW = () => days.length * dayWidth();

  // Ancho de hora para que los días del zoom llenen el ancho disponible del Gantt.
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

  // Tiempo (wall-clock) → x en píxeles sobre el eje comprimido (solo jornada)
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
      mins = clamp((+d - (+d0 + WORK_INI * HOUR)) / 60000, 0, VIS_MIN);
    }
    return idx * dayWidth() + (mins / 60) * pph();
  }

  // x en píxeles → tiempo wall-clock
  function xToTime(px) {
    const idx = clamp(Math.floor(px / dayWidth()), 0, days.length - 1);
    const mins = clamp((px - idx * dayWidth()) / pph() * 60, 0, VIS_MIN);
    const t = new Date(days[idx]); t.setHours(WORK_INI, 0, 0, 0);
    return new Date(+t + mins * 60000);
  }

  function snap(d) {
    const r = new Date(d);
    let m = Math.round((r.getHours() * 60 + r.getMinutes()) / SNAP_MIN) * SNAP_MIN;
    m = clamp(m, WORK_INI * 60, WORK_FIN * 60);
    r.setHours(0, m, 0, 0);
    return r;
  }

  // ── Arranque ───────────────────────────────────────────────────────
  function init() {
    winStart = startOfDay(new Date());
    buildDays();
    renderZoom();
    tickClock(); setInterval(tickClock, 30000);
    loadGrupos()
      .then(() => Promise.all([loadItems(), loadBacklog()]))
      .then(() => { setTimeout(scrollToNow, 100); maybeAutoRefresh(); });
    setInterval(() => { loadItems(); loadBacklog(); }, 300000);
  }

  // Al entrar a la app: traer datos frescos del ERP (salvo refresco reciente).
  const REFRESH_COOLDOWN_MIN = 5;
  function maybeAutoRefresh() {
    const last = +(localStorage.getItem('gyc_last_refresh') || 0);
    if (Date.now() - last < REFRESH_COOLDOWN_MIN * 60000) return;  // datos ya recientes
    refrescar(true);   // modo automático (silencioso ante errores)
  }

  function tickClock() {
    $('clock').textContent = new Date().toLocaleString('es-ES',
      { weekday:'long', day:'2-digit', month:'long', hour:'2-digit', minute:'2-digit' });
  }

  // ── Carga de datos ─────────────────────────────────────────────────
  function setVista(v) {
    if (v === vista) return;
    vista = v;
    $('tab-empleado').classList.toggle('is-active', v === 'empleado');
    $('tab-maquina').classList.toggle('is-active', v === 'maquina');
    $('gantt-corner').textContent = v === 'empleado' ? 'Operarios' : 'Máquinas';
    areaActive = 'todos';
    _recursosCache.empleado = _recursosCache.maquina = null;
    loadGrupos().then(loadItems);
  }

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

  async function loadBacklog() {
    backlog = await (await fetch('/api/backlog')).json();
    renderCola();
  }

  // ── Áreas (filtros) ────────────────────────────────────────────────
  function renderAreas() {
    const areas = ['todos', ...new Set(allGrupos.map(g => g.area || 'Sin área'))];
    $('areas').innerHTML = areas.map(a =>
      `<button class="area-pill ${a === areaActive ? 'is-active' : ''}" onclick="App.setArea('${a.replace(/'/g,"\\'")}')">${a === 'todos' ? 'Todas las áreas' : a}</button>`
    ).join('');
  }
  function setArea(a) { areaActive = a; renderAreas(); applyArea(); render(); }
  function applyArea() {
    grupos = areaActive === 'todos' ? allGrupos : allGrupos.filter(g => (g.area || 'Sin área') === areaActive);
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
        for (let h = WORK_INI; h < WORK_FIN; h += tick) {   // sin la última hora (borde del día)
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
      // descanso 11:00–11:15
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

    // línea de "ahora"
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
    if (!grupos.length) {
      cont.innerHTML = `<div class="gantt__empty">No hay ${vista === 'empleado' ? 'operarios' : 'máquinas'} para esta área.</div>`;
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
    if (!lista.length) {
      cont.innerHTML = `<div class="gantt__empty">${
        cargaFilter === 'con' ? 'Ningún recurso con carga en esta vista.' :
        cargaFilter === 'sin' ? 'Todos los recursos tienen carga.' :
        `No hay ${vista === 'empleado' ? 'operarios' : 'máquinas'} para esta área.`}</div>`;
      return;
    }

    lista.forEach(grp => {
      const its = (byRes.get(String(grp.id)) || []).slice()
        .sort((a, b) => new Date(a.start) - new Date(b.start));

      // Asignación de carriles: solapados → carriles distintos
      const laneEnd = [];
      its.forEach(it => {
        const s = +new Date(it.start), e = +new Date(it.end);
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
                        `<div class="row__sub">${esc(grp.sub || '')}${lanes > 1 ? ` · ${lanes} a la vez` : ''}</div>`;

      const track = document.createElement('div');
      track.className = 'row__track'; track.style.width = W + 'px';
      track.dataset.rid = grp.id;
      attachDrop(track);

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
    bar.innerHTML = `<span class="bar__id">${esc(it.idorden)}</span>` +
                    (w > 60 ? `<span class="bar__sub">${esc(String(sub).slice(0, 30))}</span>` : '');
    if (it.tipo === 'real' && it.progreso != null) {
      const p = document.createElement('div');
      p.className = 'bar__prog'; p.style.width = it.progreso + '%';
      bar.appendChild(p);
    }

    bar.addEventListener('mouseenter', e => showTip(e, it));
    bar.addEventListener('mousemove', moveTip);
    bar.addEventListener('mouseleave', hideTip);
    bar.addEventListener('click', () => { if (!bar.dataset.moved) openDetalle(it.id); });
    if (it.tipo === 'planificado') attachBarDrag(bar, it);
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
    rows.push(`<div class="tip__row">Inicio <span>${fmtDt(it.start)}</span></div>`);
    rows.push(`<div class="tip__row">Fin <span>${fmtDt(it.end)}${it.estimado ? ' ~' : ''}</span></div>`);
    if (it.prev) rows.push(`<div class="tip__row">Prevista <span>${fmtDate(it.prev)}</span></div>`);
    if (it.notas) rows.push(`<div class="tip__row">Notas <span>${esc(it.notas)}</span></div>`);
    const badge = `<span style="color:${ST_COLOR[it.estado]}">●</span> ${ST_LABEL[it.estado] || it.estado_label}`;
    tip.innerHTML = `<b>${it.tipo === 'real' ? '▶ ' : ''}${esc(it.idorden)}</b> — ${esc(it.art || '')}<hr>${rows.join('')}` +
                    `<div class="tip__row" style="margin-top:6px">Estado <span>${badge}</span></div>`;
    tip.classList.add('is-visible');
    moveTip(e);
  }
  function moveTip(e) {
    const tip = $('tip');
    let x = e.clientX + 14, y = e.clientY + 14;
    const r = tip.getBoundingClientRect();
    if (x + r.width > innerWidth - 10) x = e.clientX - r.width - 14;
    if (y + r.height > innerHeight - 10) y = e.clientY - r.height - 14;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  function hideTip() { $('tip').classList.remove('is-visible'); }

  // ── Arrastre de barras planificadas ────────────────────────────────
  function attachBarDrag(bar, it) {
    bar.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      e.preventDefault();
      hideTip();
      const startX = e.clientX, startY = e.clientY;
      const origLeft = parseFloat(bar.style.left);
      const w = parseFloat(bar.style.width);
      let targetTrack = bar.parentElement, moved = false;

      const onMove = ev => {
        const dx = ev.clientX - startX;
        if (Math.abs(dx) > 3 || Math.abs(ev.clientY - startY) > 3) { moved = true; bar.classList.add('is-dragging'); bar.dataset.moved = '1'; }
        if (!moved) return;
        let nl = clamp(origLeft + dx, 0, timelineW() - w);
        bar.style.left = nl + 'px';
        const el = document.elementFromPoint(ev.clientX, ev.clientY);
        const tr = el && el.closest ? el.closest('.row__track') : null;
        if (tr && tr !== targetTrack) {
          document.querySelectorAll('.row__track.is-drop-target').forEach(t => t.classList.remove('is-drop-target'));
          if (tr !== bar.parentElement) tr.classList.add('is-drop-target');
          targetTrack = tr;
        }
      };

      const onUp = async () => {
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        document.querySelectorAll('.row__track.is-drop-target').forEach(t => t.classList.remove('is-drop-target'));
        bar.classList.remove('is-dragging');
        if (!moved) { delete bar.dataset.moved; return; }
        setTimeout(() => delete bar.dataset.moved, 50);

        const nl = parseFloat(bar.style.left);
        const dur = new Date(it.end) - new Date(it.start);
        const start = snap(xToTime(nl));
        const end = new Date(+start + dur);
        const rid = (targetTrack && targetTrack.dataset.rid) || it.recurso_id;
        try {
          const r = await fetch(`/api/programar/${it.id}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ start: start.toISOString(), end: end.toISOString(), recurso_id: rid }),
          });
          if (!r.ok) throw 0;
          toast('Asignación movida');
        } catch { toast('No se pudo mover', true); }
        await loadItems();
      };

      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onUp);
    });
  }

  // ── Drop desde el backlog ──────────────────────────────────────────
  function attachDrop(track) {
    track.addEventListener('dragover', e => { e.preventDefault(); track.classList.add('is-drop-target'); });
    track.addEventListener('dragleave', () => track.classList.remove('is-drop-target'));
    track.addEventListener('drop', e => {
      e.preventDefault();
      track.classList.remove('is-drop-target');
      const idorden = e.dataTransfer.getData('idorden');
      if (!idorden) return;
      const rect = track.getBoundingClientRect();
      const t = snap(xToTime(e.clientX - rect.left));
      openAsignar(idorden, e.dataTransfer.getData('art'), e.dataTransfer.getData('cant'),
                  parseFloat(e.dataTransfer.getData('horas')) || 8, t, track.dataset.rid,
                  parseInt(e.dataTransfer.getData('idbono')) || 0, e.dataTransfer.getData('op'));
    });
  }

  // ── Cola de trabajo ────────────────────────────────────────────────
  const _diasRetraso = prev => {
    if (!prev) return 0;
    return Math.floor((+startOfDay(new Date()) - +startOfDay(new Date(prev))) / DAY);
  };
  function _colaSort() {
    const prevT = o => o.fecha_prevista_fin ? +new Date(o.fecha_prevista_fin) : Infinity;
    const urg = o => o.nivel === 'bono'
      ? (o.situacion === 'PARADA' ? 0 : 1)
      : ({ VENCIDA:0, URGENTE:1, NORMAL:2 }[o.prioridad] ?? 3);
    return {
      urgencia: (a, b) => urg(a) - urg(b) || prevT(a) - prevT(b),
      fecha:    (a, b) => prevT(a) - prevT(b),
      tiempo:   (a, b) => (b.horas_estimadas || 0) - (a.horas_estimadas || 0),
    }[colaOrden];
  }

  function renderCola() {
    const q = colaSearch.toLowerCase().trim();
    const data = backlog.filter(o => !q
      || o.idorden.toLowerCase().includes(q)
      || (o.articulo || '').toLowerCase().includes(q)
      || (o.operacion || '').toLowerCase().includes(q));

    $('backlog-count').textContent = data.length;
    const sortFn = _colaSort();
    const bonos   = colaTipo === 'orden' ? [] : data.filter(o => o.nivel === 'bono').sort(sortFn);
    const ordenes = colaTipo === 'bono'  ? [] : data.filter(o => o.nivel === 'orden').sort(sortFn);

    const el = $('backlog-list');
    if (!bonos.length && !ordenes.length) {
      el.innerHTML = `<div class="backlog__empty">${q || colaTipo !== 'todo' ? 'Sin resultados' : 'Nada pendiente ✓'}</div>`;
      return;
    }
    const sumH = list => { const s = list.reduce((a, o) => a + (o.horas_estimadas || 0), 0); return s ? `~${Math.round(s)} h` : ''; };
    const section = (titulo, list) => !list.length ? '' :
      `<div class="backlog__group">${titulo} · ${list.length}<span class="grp-sum">${sumH(list)}</span></div>` + list.map(card).join('');
    el.innerHTML = section('Bonos a replanificar', bonos) + section('Órdenes programadas', ordenes);
  }

  function card(o) {
    const esBono = o.nivel === 'bono';
    // color del borde = situación (bonos) o urgencia (órdenes)
    const state = esBono
      ? (SITU_KEY[o.situacion] || 'sin-estimar')
      : (o.prioridad || 'sin-fecha').toLowerCase().replace(/ /g, '-');
    // etiqueta: bonos muestran su situación; órdenes muestran "PROGRAMADA" (estado ERP)
    const tagCls = esBono ? state : 'programada';
    const lbl = esBono ? (o.situacion || '—') : 'PROGRAMADA';
    const horas = o.horas_estimadas || (esBono ? 1 : 8);
    const title = esBono ? esc(o.operacion || '—') : `Orden ${o.idorden}`;
    const subline = esBono ? `Orden ${o.idorden} · ${esc(o.articulo || '—')}` : esc(o.articulo || '—');
    const dias = _diasRetraso(o.fecha_prevista_fin);
    const late = dias > 0 ? `<span class="chip-late">▲ ${dias} d</span>` : '';
    return `<div class="order" draggable="true" style="--accent-state:${ST_COLOR[state] || '#79859a'}"
      data-idorden="${o.idorden}" data-idbono="${o.idbono || 0}" data-op="${esc(o.operacion || '')}"
      data-art="${esc(o.articulo || '')}" data-cant="${o.cantidad_pedida}" data-horas="${horas}"
      ondragstart="App.dragStart(event,this)" ondragend="App.dragEnd(this)"
      onclick="App.openAsignar('${o.idorden}','${esc(o.articulo || '').replace(/'/g,"\\'")}',${o.cantidad_pedida},${horas},null,null,${o.idbono || 0},'${esc(o.operacion || '').replace(/'/g,"\\'")}')">
      <div class="order__top">
        <span class="order__id">${title}</span>
        <span class="tag tag--${tagCls}">${lbl}</span>
      </div>
      <div class="order__art">${subline}</div>
      <div class="order__meta">
        ${o.horas_estimadas ? `<span>~${o.horas_estimadas} h</span>` : ''}
        ${esBono && o.num_operarios ? `<span>${o.num_operarios} op.</span>` : ''}
        ${!esBono && o.cantidad_pedida != null ? `<span>${o.cantidad_pedida} uds</span>` : ''}
        ${o.fecha_prevista_fin ? `<span>Prev ${fmtDate(o.fecha_prevista_fin)}</span>` : ''}
        ${late}
      </div>
    </div>`;
  }

  function filterBacklog(q) { colaSearch = q; renderCola(); }
  function setColaTipo(t) {
    colaTipo = t;
    [...$('cola-tipo').children].forEach(b => b.classList.toggle('is-active', b.textContent.trim().toLowerCase() === (t === 'todo' ? 'todo' : t === 'bono' ? 'bonos' : 'órdenes')));
    renderCola();
  }
  function setColaOrden(v) { colaOrden = v; renderCola(); }

  function dragStart(e, el) {
    el.classList.add('is-dragging');
    ['idorden', 'idbono', 'op', 'art', 'cant', 'horas'].forEach(k => e.dataTransfer.setData(k, el.dataset[k]));
    e.dataTransfer.effectAllowed = 'copy';
  }
  function dragEnd(el) { el.classList.remove('is-dragging'); document.querySelectorAll('.row__track.is-drop-target').forEach(t => t.classList.remove('is-drop-target')); }

  // ── Modal asignar ──────────────────────────────────────────────────
  let _aData = { idbono: 0 };
  async function openAsignar(idorden, art, cant, horas, start, rid, idbono, op) {
    _aData = { idbono: idbono || 0 };
    $('a-orden').textContent = idorden;
    $('a-art').textContent = art || '—';
    $('a-cant').textContent = cant;
    $('a-horas').textContent = horas || '—';
    $('a-op').textContent = op || '';
    $('a-op-wrap').style.display = op ? '' : 'none';
    $('a-reclabel').textContent = vista === 'empleado' ? 'Operario' : 'Máquina';
    $('a-notas').value = '';
    $('a-start').dataset.horas = horas || 8;

    const s = start instanceof Date ? start : (() => { const d = startOfDay(new Date()); d.setHours(WORK_INI, 0, 0, 0); return d; })();
    $('a-start').value = toInput(s);
    $('a-end').value = toInput(new Date(+s + (horas || 8) * HOUR));

    if (!_recursosCache[vista]) _recursosCache[vista] = await (await fetch(`/api/recursos?tipo=${vista}`)).json();
    const sel = $('a-recurso');
    sel.innerHTML = _recursosCache[vista].map(r =>
      `<option value="${r.id}">${r.grupo ? '[' + r.grupo + '] ' : ''}${esc(r.nombre)}</option>`).join('');
    if (rid) sel.value = String(rid);

    openModal('ov-asignar');
  }

  function recalcFin() {
    const v = $('a-start').value; if (!v) return;
    const h = parseFloat($('a-start').dataset.horas) || 8;
    $('a-end').value = toInput(new Date(+new Date(v) + h * HOUR));
  }

  async function submitAsignar(e) {
    e.preventDefault();
    try {
      const r = await fetch('/api/programar', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          idorden: $('a-orden').textContent,
          idbono: _aData.idbono,
          recurso_tipo: vista,
          recurso_id: $('a-recurso').value,
          start: new Date($('a-start').value).toISOString(),
          end: new Date($('a-end').value).toISOString(),
          notas: $('a-notas').value || null,
        }),
      });
      if (!r.ok) { const err = await r.json().catch(() => ({})); throw new Error(err.detail || 'No se pudo asignar'); }
      closeModal('ov-asignar');
      toast('Orden asignada');
      await loadGrupos();
      await Promise.all([loadItems(), loadBacklog()]);
    } catch (err) { toast(err.message || 'Error al asignar', true); }
  }

  // ── Modal detalle ──────────────────────────────────────────────────
  function openDetalle(id) {
    const it = itemMap.get(String(id)); if (!it) return;
    selectedId = id;
    document.querySelectorAll('.bar.is-selected').forEach(b => b.classList.remove('is-selected'));
    const bar = document.querySelector(`.bar[data-id="${CSS.escape(String(id))}"]`);
    if (bar) bar.classList.add('is-selected');

    const real = it.tipo === 'real';
    const grp = grupos.find(g => String(g.id) === String(it.recurso_id));
    $('d-orden').textContent = it.idorden;
    const aviso = real
      ? `<div class="notice">▶ ${esc(it.situacion || 'EN CURSO')} (ERP) · solo lectura${it.estimado ? ' · fin estimado' : ''}</div>`
      : '';
    $('d-body').innerHTML = aviso +
      `<dl class="dl">
        <dt>${vista === 'empleado' ? 'Operario' : 'Máquina'}</dt><dd>${esc(grp ? grp.nombre : it.recurso_id)}</dd>
        <dt>Bono</dt><dd>${it.idbono || '—'}${it.operacion ? ' · ' + esc(it.operacion) : ''}</dd>
        <dt>Artículo</dt><dd>${esc(it.art || '—')}</dd>
        <dt>Inicio</dt><dd>${fmtDt(it.start)}</dd>
        <dt>Fin</dt><dd>${fmtDt(it.end)}${it.estimado ? ' <span style="color:var(--ink-3)">(est.)</span>' : ''}</dd>
        ${it.progreso != null ? `<dt>Progreso</dt><dd>${it.progreso}%</dd>` : ''}
        ${it.prev ? `<dt>Prevista</dt><dd>${fmtDate(it.prev)}</dd>` : ''}
        ${it.notas ? `<dt>Notas</dt><dd>${esc(it.notas)}</dd>` : ''}
      </dl>`;
    $('d-quitar').style.display = real ? 'none' : '';
    openModal('ov-detalle');
  }

  async function desprogramar() {
    if (!selectedId) return;
    try {
      const r = await fetch(`/api/programar/${selectedId}`, { method: 'DELETE' });
      if (!r.ok) throw 0;
      closeModal('ov-detalle'); selectedId = null;
      toast('Asignación eliminada');
      await loadGrupos();
      await Promise.all([loadItems(), loadBacklog()]);
    } catch { toast('No se pudo eliminar', true); }
  }

  // ── Navegación / zoom ──────────────────────────────────────────────
  function nav(dir) {
    winStart = addWorkingDays(days[0], dir * cfg().days);
    buildDays();
    loadItems();
  }
  function today() {
    winStart = startOfDay(new Date());
    buildDays();
    loadItems();
    setTimeout(scrollToNow, 120);
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
    const plan = items.filter(i => i.tipo === 'planificado').length;
    const real = items.filter(i => i.tipo === 'real').length;
    $('summary').innerHTML =
      `<span><span class="dot" style="background:var(--accent)"></span><b>${plan}</b> asignadas</span>` +
      `<span><span class="dot" style="background:var(--verde)"></span><b>${real}</b> en curso</span>`;
  }

  function setCarga(v) {
    cargaFilter = v;
    [...$('carga').children].forEach(b => b.classList.toggle('is-active', b.textContent.trim().toLowerCase().startsWith(
      v === 'todos' ? 'todos' : v === 'con' ? 'con' : 'libres')));
    render();
  }

  function toggleBacklog() { $('backlog').classList.toggle('is-collapsed'); setTimeout(render, 60); }

  // ── Refresco bajo demanda del ETL (Prefect) ────────────────────────
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
      if (r.status === 503 && auto) return;   // sin configurar: en auto no molestamos
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || 'No se pudo lanzar el flujo'); }
      const { flow_run_id, estado: est0 } = await r.json();
      if (!flow_run_id) throw new Error('Prefect no devolvió un id de ejecución');

      const FIN = ['COMPLETED', 'FAILED', 'CRASHED', 'CANCELLED'];
      const deadline = Date.now() + 180000;   // 3 min máx
      let estado = est0 || 'SCHEDULED';
      lbl.textContent = _ESTADO_LBL[estado] || 'Actualizando…';
      while (Date.now() < deadline) {
        await new Promise(res => setTimeout(res, 1500));   // sondeo ágil
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
        await Promise.all([loadItems(), loadBacklog()]);
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

  // ── Modales / toast / util ─────────────────────────────────────────
  function openModal(id) { $(id).classList.add('is-open'); }
  function closeModal(id) { $(id).classList.remove('is-open'); }
  let _toastT;
  function toast(msg, err) {
    const t = $('toast'); t.textContent = msg;
    t.className = 'toast is-visible' + (err ? ' is-error' : '');
    clearTimeout(_toastT); _toastT = setTimeout(() => t.classList.remove('is-visible'), 2600);
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c])); }

  document.addEventListener('click', e => { if (e.target.classList.contains('overlay')) e.target.classList.remove('is-open'); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') document.querySelectorAll('.overlay.is-open').forEach(o => o.classList.remove('is-open')); });
  let _rsT;
  window.addEventListener('resize', () => { clearTimeout(_rsT); _rsT = setTimeout(() => { if (grupos.length || items.length) render(); }, 150); });

  return {
    setVista, setArea, setCarga, nav, today, setZoom, toggleBacklog, refrescar,
    filterBacklog, setColaTipo, setColaOrden, dragStart, dragEnd, openAsignar, recalcFin, submitAsignar,
    openModal, closeModal, desprogramar, init,
  };
})();

document.addEventListener('DOMContentLoaded', App.init);
