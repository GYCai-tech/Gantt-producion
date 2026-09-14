/* ============================================================
   GYC · Bonos duplicados
   Articulos que mas de un bono vivo esta fabricando a la vez.
   Cada grupo es un articulo; dentro van sus bonos, el mas viejo
   primero, que es como se decide cual sobra.
   ============================================================ */
const Dup = (() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = v => Number(v || 0).toLocaleString('es-ES', { maximumFractionDigits: 0 });
  const fecha = s => s ? new Date(s).toLocaleDateString('es-ES',
    { day: '2-digit', month: '2-digit', year: '2-digit' }) : '—';

  // Los tres estados vivos del ERP. El bloqueado es el que mas aparece aqui:
  // una orden vieja se queda bloqueada y se lanza otra para lo mismo.
  const ESTADO = { 0: 'espera', 1: 'activado', 3: 'bloqueado' };

  const ICO = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 5h18l-7 8.2V21l-4-2.4v-5.4z"/></svg>';

  const FILTROS = {
    todos:      { tit: 'Todos',        ok: () => true },
    // Nadie ha fichado ninguno de los bonos del grupo: se lanzo dos veces y
    // aun se esta a tiempo de anular uno sin tirar trabajo.
    sin_tocar:  { tit: 'Sin empezar',  ok: g => g.sin_tocar },
    // Alguno ya lleva trabajo hecho: ahi la decision no es gratis.
    empezados:  { tit: 'Con trabajo',  ok: g => !g.sin_tocar },
  };

  // Las columnas con filtro propio: de donde sale el valor de un bono y como
  // se llama la columna. Mismo gesto que en ordenes no asignadas: el titulo
  // abre la lista de valores que quedan, con su cuenta.
  const COLS = {
    operacion: { tit: 'Operación', val: b => b.descrip || '—' },
    maquina:   { tit: 'Máquina',   val: b => b.maquina || 'Sin máquina' },
    area:      { tit: 'Área',      val: b => b.area || 'Sin área' },
    estado:    { tit: 'Estado',    val: b => b.estado_label },
  };

  // Las columnas de la tabla en orden. `f` es el filtro que abre su titulo;
  // sin `f`, el titulo no abre nada.
  const CAB = [
    { f: null,        tit: 'Orden · bono', cls: 'num' },
    { f: 'operacion', tit: 'Operación' },
    { f: 'maquina',   tit: 'Máquina' },
    { f: 'area',      tit: 'Área' },
    { f: 'estado',    tit: 'Estado' },
    { f: null,        tit: 'Lanzada' },
    { f: null,        tit: 'Cantidad', cls: 'num' },
  ];

  let datos = null, filtro = 'todos', busqueda = '';
  const sel = { operacion: new Set(), maquina: new Set(), area: new Set(), estado: new Set() };
  let abierto = null, opsVista = [];

  const marcados = c => sel[c];

  // Un grupo pasa el filtro si lo pasa ALGUNO de sus bonos, y entonces se
  // enseña entero. Filtrar bono a bono dejaria grupos de uno, que es justo lo
  // contrario de lo que se viene a ver: al duplicado hay que verle la pareja.
  function pasa(g) {
    if (!FILTROS[filtro].ok(g)) return false;
    if (busqueda && !g.texto.includes(busqueda)) return false;
    return Object.keys(COLS).every(c =>
      !sel[c].size || g.bonos.some(b => sel[c].has(COLS[c].val(b))));
  }

  function visibles() {
    return datos ? datos.grupos.filter(pasa) : [];
  }

  // Los valores que quedan para esa columna, contados sobre lo que se ve con
  // los DEMAS filtros puestos: si no, se ofrecen valores que dan cero.
  function opciones(c) {
    const otros = datos.grupos.filter(g => {
      if (!FILTROS[filtro].ok(g)) return false;
      if (busqueda && !g.texto.includes(busqueda)) return false;
      return Object.keys(COLS).every(x =>
        x === c || !sel[x].size || g.bonos.some(b => sel[x].has(COLS[x].val(b))));
    });
    const cuenta = new Map();
    for (const g of otros) {
      for (const v of new Set(g.bonos.map(b => COLS[c].val(b)))) {
        cuenta.set(v, (cuenta.get(v) || 0) + 1);
      }
    }
    return [...cuenta.entries()].sort((a, b) => a[0].localeCompare(b[0], 'es'));
  }

  function stats(vis) {
    const bonos = vis.reduce((n, g) => n + g.bonos.length, 0);
    const ordenes = new Set(vis.flatMap(g => g.bonos.map(b => b.idorden))).size;
    // Sin recuento de piezas: sumar las de los dos duplicados da un total que
    // no se va a fabricar, porque la gracia es que uno de los dos sobra.
    const sinEmpezar = vis.filter(g => g.sin_tocar).length;
    $('dup-stats').innerHTML = [
      ['Artículos repetidos', vis.length, datos.total_grupos, 'ord__stat--ojo'],
      ['Bonos implicados', bonos, datos.total_bonos, ''],
      ['Órdenes implicadas', ordenes, datos.total_ordenes, ''],
      ['Sin empezar', sinEmpezar, datos.total_grupos, ''],
    ].map(([t, n, tot, cls]) => `<div class="ord__stat ${cls}">
        <span>${t}</span><b>${num(n)}</b>${
          n !== tot ? `<em>de ${num(tot)}</em>` : ''}</div>`).join('');
  }

  function tabla(vis) {
    const cab = CAB.map(({ f, tit, cls }) => {
      if (!f) return `<th class="${cls || ''}"><span class="dup__th">${tit}</span></th>`;
      const n = marcados(f).size;
      return `<th class="${cls || ''}">
        <button class="ord__th ${n ? 'is-on' : ''}" data-col="${f}">
          <span>${tit}</span>${ICO}${n > 1 ? `<i>${n}</i>` : ''}
        </button></th>`;
    }).join('');

    const cuerpo = vis.map(g => {
      const cabecera = `<tr class="ord__orden"><td colspan="7">
          <b>${esc(g.articulo)}</b>
          <span class="art"><u>${esc(g.descrip)}</u></span>
          <em>${g.bonos.length} bonos · ${g.ordenes} órdenes · ${num(g.piezas)} piezas${
            g.sin_tocar ? ' · sin empezar' : ''}</em>
        </td></tr>`;
      return cabecera + g.bonos.map(b => `<tr class="ord__bono dup__bono">
        <td class="num ord__cod"><b>${esc(b.idorden)}</b>·${esc(b.idbono)}</td>
        <td><b>${esc(b.descrip) || '—'}</b>${b.asignados
            ? `<span class="ord__chip">${b.asignados} asignado${b.asignados > 1 ? 's' : ''}</span>`
            : ''}${b.fichajes
            ? `<span class="ord__chip dup__chip-hecho">${b.fichajes} fichaje${b.fichajes > 1 ? 's' : ''}</span>`
            : ''}</td>
        <td>${b.maquina
            ? `${esc(b.maquina)}<span class="sub ord__cod">${esc(b.matricula)}</span>`
            : '<i class="ord__fuera">Sin máquina · operación externa</i>'}</td>
        <td>${b.area ? `<span class="ord__area">${esc(b.area)}</span>` : ''}</td>
        <td><span class="dup__estado is-${ESTADO[b.estado] || 'espera'}">${esc(b.estado_label)}</span></td>
        <td>${fecha(b.fecha)}${b.lote ? `<span class="sub ord__cod">${esc(b.lote)}</span>` : ''}</td>
        <td class="num"><b>${num(b.objetivo)}</b>${b.hechas
            ? `<span class="sub">${num(b.hechas)} hechas</span>` : ''}</td>
      </tr>`).join('');
    }).join('');

    $('dup-tabla').innerHTML = `<thead><tr>${cab}</tr></thead><tbody>${cuerpo}</tbody>`;
    $('dup-sin-resultados').hidden = vis.length > 0;
  }

  /* ---------- el menu de una columna ---------- */

  function menu(c, ancla) {
    const p = $('dup-pop');
    // `nuevo` solo cuando el menu no es ya el de esta columna: al refrescar,
    // se repinta la lista y NO la caja de busqueda, que perderia lo tecleado.
    const nuevo = p.hidden || p.dataset.col !== c;
    abierto = c;
    p.dataset.col = c;
    if (nuevo) {
      p.innerHTML = `<header><b>${esc(COLS[c].tit)}</b><button data-lim="${c}">Quitar</button></header>
        <input class="fpop__buscar" type="search" placeholder="Buscar valor…">
        <div class="fpop__list"></div>`;
    }
    const q = (p.querySelector('.fpop__buscar')?.value || '').trim().toLowerCase();
    const ops = opciones(c).filter(([v]) => !q || v.toLowerCase().includes(q));
    opsVista = ops.map(o => o[0]);
    p.querySelector('.fpop__list').innerHTML = ops.length
      ? ops.map(([v, n], i) => `<button class="fpop__op ${marcados(c).has(v) ? 'is-on' : ''}" data-i="${i}">
            <span class="fpop__box"></span><span class="fpop__v">${esc(v)}</span><em>${num(n)}</em>
          </button>`).join('')
      : '<p class="fpop__nada">Ningún valor</p>';

    p.hidden = false;
    if (ancla) {   // debajo de su cabecera y encajado en la ventana
      const r = ancla.getBoundingClientRect();
      p.style.top = `${Math.min(r.bottom + 6, innerHeight - p.offsetHeight - 8)}px`;
      p.style.left = `${Math.min(Math.max(8, r.left), innerWidth - p.offsetWidth - 8)}px`;
      p.querySelector('.fpop__buscar')?.focus();
    }
  }

  function cerrar() { abierto = null; $('dup-pop').hidden = true; }

  function quita(c) {
    if (c === '*') Object.keys(sel).forEach(k => sel[k].clear());
    else marcados(c).clear();
    refrescar();
  }

  function chips() {
    const puestos = Object.keys(COLS)
      .filter(c => sel[c].size)
      .map(c => `<button class="ord__chip-f" data-quita="${c}">
          <b>${COLS[c].tit}:</b> <i>${esc([...sel[c]].join(', '))}</i><u>&times;</u>
        </button>`);
    if (puestos.length > 1) puestos.push('<button class="ord__limpiar" data-quita="*">Limpiar todo</button>');
    $('dup-chips').innerHTML = puestos.join('');
  }

  function refrescar() {
    const vis = visibles();
    $('dup-filtros').innerHTML = Object.entries(FILTROS).map(([k, f]) =>
      `<button class="${k === filtro ? 'is-active' : ''}" data-f="${k}">${f.tit}</button>`).join('');
    stats(vis);
    chips();
    const bonos = vis.reduce((n, g) => n + g.bonos.length, 0);
    $('dup-resumen').textContent = vis.length === datos.total_grupos
      ? `${num(vis.length)} artículos · ${num(bonos)} bonos`
      : `${num(vis.length)} de ${num(datos.total_grupos)} artículos`;
    tabla(vis);
    if (abierto) menu(abierto);   // las cuentas del menu abierto han cambiado
  }

  function pintar(d) {
    datos = d;
    // El texto de la busqueda se precalcula sobre todo lo que se ve en la
    // fila, que es lo que uno espera al escribir en la caja de arriba.
    d.grupos.forEach(g => {
      g.texto = [g.articulo, g.descrip, ...g.bonos.flatMap(b => [
        b.idorden, b.idbono, b.descrip, b.maquina, b.matricula, b.area, b.lote,
      ])].join(' ').toLowerCase();
    });
    cerrar();
    refrescar();
    $('generado').textContent = 'Leído a las ' + new Date(d.generado)
      .toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
    $('dup-cargando').hidden = true;
    $('dup-cuerpo').hidden = false;
  }

  async function cargar() {
    $('dup-error').hidden = true;
    if (!datos) $('dup-cargando').hidden = false;
    try {
      const r = await fetch('/api/bonos-duplicados');
      if (!r.ok) throw new Error('El ERP respondió ' + r.status);
      pintar(await r.json());
    } catch (e) {
      $('dup-cargando').hidden = true;
      $('dup-error').hidden = false;
      $('dup-error').textContent = 'No se pudo leer: ' + e.message;
    }
  }

  document.addEventListener('click', e => {
    const th = e.target.closest('.ord__th');
    if (th) {
      const c = th.dataset.col;
      if (abierto === c) cerrar(); else { cerrar(); menu(c, th); }
      return;
    }
    const pop = e.target.closest('.fpop');
    if (!pop) {
      const chip = e.target.closest('[data-quita]');
      if (chip) { cerrar(); quita(chip.dataset.quita); return; }
      const b = e.target.closest('[data-f]');
      if (b) { cerrar(); filtro = b.dataset.f; refrescar(); return; }
      if (abierto) cerrar();
      return;
    }
    const lim = e.target.closest('[data-lim]');
    if (lim) { quita(lim.dataset.lim); cerrar(); return; }
    const op = e.target.closest('.fpop__op');
    if (op) {
      const v = opsVista[+op.dataset.i];
      const s = marcados(abierto);
      s.has(v) ? s.delete(v) : s.add(v);
      refrescar();
    }
  });

  document.addEventListener('input', e => {
    if (e.target.classList.contains('fpop__buscar')) menu(abierto);
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') cerrar(); });
  addEventListener('resize', cerrar);
  // Al scrollar se cierra, porque va anclado a una cabecera que se mueve. Se
  // excluye el scroll DE DENTRO del menu: su lista de valores tiene el suyo.
  addEventListener('scroll', e => {
    if (abierto && !e.target.closest?.('.fpop')) cerrar();
  }, true);

  function setBusqueda(t) { busqueda = t.trim().toLowerCase(); refrescar(); }

  document.addEventListener('DOMContentLoaded', cargar);
  return { cargar, setBusqueda };
})();
