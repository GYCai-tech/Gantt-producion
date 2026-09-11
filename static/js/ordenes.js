/* Órdenes no asignadas — lee /api/ordenes-no-asignadas, lo agrupa por orden y
   deja filtrar por los valores de cada columna.

   El filtro va por columna y no como una lista de pills sueltas porque las
   preguntas que se hacen aquí son cruzadas: "qué le queda sin repartir a
   Mecanizado de este modelo". Cada menú enseña los valores QUE QUEDAN con los
   demás filtros puestos, y su cuenta, así que nunca se llega a cero a ciegas. */
const Ord = (() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = v => Number(v || 0).toLocaleString('es-ES', { maximumFractionDigits: 0 });

  const ICO = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 5h18l-7 8.2V21l-4-2.4v-5.4z"/></svg>';

  // Las columnas filtrables: de dónde sale su valor y cómo se llama en el menú.
  // `cantidad` no está: es un número y va por rango, no por lista de valores.
  // La columna de modelo (`ModeloArticulo`) tira de dos sitios. El del artículo
  // que sale está vacío en los 221 bonos bloqueados; el del ERP entero solo
  // está relleno en 54 artículos de 25.815, y son máquinas y repuestos. Así que
  // cuando el artículo no lo trae se enseña el de la máquina del bono, que es
  // el único con dato: 4 de los 221.
  const COLS = {
    orden:      { tit: 'Orden',      val: r => String(r.o.idorden) },
    operacion:  { tit: 'Operación',  val: r => r.b.descrip || '—' },
    maquina:    { tit: 'Máquina',    val: r => r.b.maquina || 'Sin máquina' },
    area:       { tit: 'Área',       val: r => r.b.area || 'Sin área' },
    // Código y descripción juntos: un código a secas no dice qué pieza es, y
    // como cada código trae siempre la misma descripción agrupa igual.
    salida:     { tit: 'Artículo que sale',
                  val: r => (r.b.art_salida ? `${r.b.art_salida} · ${r.b.descrip_salida}` : '—') },
    modelo:     { tit: 'Modelo',     val: r => r.b.modelo || r.b.modelo_maquina || 'Sin modelo' },
    asignacion: { tit: 'Asignación', val: r => (r.b.asignados ? 'Con operarios' : 'Sin asignar') },
  };

  // Las columnas de la tabla, en orden. `f` es el filtro que abre su cabecera.
  const CAB = [
    { f: 'orden',     tit: 'Orden · bono', cls: 'num' },
    { f: 'operacion', tit: 'Operación' },
    { f: 'maquina',   tit: 'Máquina' },
    { f: 'area',      tit: 'Área' },
    { f: 'salida',    tit: 'Artículo que sale' },
    { f: 'modelo',    tit: 'Modelo' },
    { f: 'cantidad',  tit: 'Cantidad', cls: 'num' },
  ];

  let datos = null;
  let busqueda = '';
  let filas = [];               // pares {o, b} planos: filtrar por columna sale
                                // mucho más simple sobre filas que sobre árbol
  const sel = {};               // columna -> Set de valores marcados (vacío = todos)
  let rango = { min: null, max: null };
  let abierto = null;           // columna cuyo menú está abierto
  let opsVista = [];            // valores que se ven ahora en el menú, por índice

  const marcados = c => sel[c] || (sel[c] = new Set());
  const hayRango = () => rango.min != null || rango.max != null;

  /* ---------- filtrado ---------- */

  // `salvo` deja una columna fuera del filtro: es lo que permite que su propio
  // menú siga enseñando todos sus valores, y no solo el que ya está marcado.
  function pasa(r, salvo) {
    if (busqueda && !r.texto.includes(busqueda)) return false;
    if (salvo !== 'cantidad' && hayRango()) {
      if (rango.min != null && r.b.cantidad < rango.min) return false;
      if (rango.max != null && r.b.cantidad > rango.max) return false;
    }
    for (const c in sel) {
      if (c === salvo || !sel[c].size) continue;
      if (!sel[c].has(COLS[c].val(r))) return false;
    }
    return true;
  }

  // Valor -> cuántas filas lo tienen con los DEMÁS filtros puestos. Lo ya
  // marcado se queda aunque salga a cero, para poder desmarcarlo.
  function opciones(c) {
    const cuenta = new Map();
    for (const r of filas) {
      if (!pasa(r, c)) continue;
      const v = COLS[c].val(r);
      cuenta.set(v, (cuenta.get(v) || 0) + 1);
    }
    for (const v of marcados(c)) if (!cuenta.has(v)) cuenta.set(v, 0);
    return [...cuenta].sort((a, b) =>
      b[1] - a[1] || a[0].localeCompare(b[0], 'es', { numeric: true }));
  }

  /* ---------- pintado ---------- */

  function stats(vis) {
    const bonos = vis.length;
    const ordenes = new Set(vis.map(r => r.o.idorden)).size;
    const sinAsig = vis.filter(r => !r.b.asignados).length;
    const piezas = vis.reduce((n, r) => n + r.b.cantidad, 0);
    const de = (n, tot) => (tot != null && n !== tot ? `<em>de ${num(tot)}</em>` : '');
    $('ord-stats').innerHTML = [
      ['Órdenes', ordenes, datos.total_ordenes, ''],
      ['Bonos bloqueados', bonos, datos.total_bonos, ''],
      ['Sin ningún operario', sinAsig, datos.sin_asignar, 'ord__stat--ojo'],
      ['Piezas por hacer', piezas, null, ''],
    ].map(([t, n, tot, cls]) => `<div class="ord__stat ${cls}">
        <span>${t}</span><b>${num(n)} ${de(n, tot)}</b>
      </div>`).join('');
  }

  function barra(vis) {
    const modo = [...marcados('asignacion')][0] || '';
    $('ord-asig').innerHTML = ['', 'Sin asignar', 'Con operarios'].map(v =>
      `<button data-asig="${v}" class="${v === modo ? 'is-active' : ''}">${
        v || 'Todos'}</button>`).join('');

    const chips = Object.keys(COLS)
      .filter(c => marcados(c).size)
      .map(c => `<button class="ord__chip-f" data-quita="${c}">
          <b>${COLS[c].tit}:</b> <i>${esc([...marcados(c)].join(', '))}</i><u>&times;</u>
        </button>`);
    if (hayRango()) {
      const t = rango.min != null && rango.max != null
        ? `${num(rango.min)} – ${num(rango.max)}`
        : (rango.min != null ? `≥ ${num(rango.min)}` : `≤ ${num(rango.max)}`);
      chips.push(`<button class="ord__chip-f" data-quita="cantidad">
          <b>Cantidad:</b> <i>${t}</i><u>&times;</u></button>`);
    }
    if (chips.length > 1) chips.push('<button class="ord__limpiar" data-quita="*">Limpiar todo</button>');
    $('ord-chips').innerHTML = chips.join('');

    $('ord-resumen').textContent = vis.length === datos.total_bonos
      ? `${num(vis.length)} bonos`
      : `${num(vis.length)} de ${num(datos.total_bonos)} bonos`;
  }

  function tabla(vis) {
    const tope = Math.max(1, ...vis.map(r => r.b.cantidad));
    const cab = CAB.map(({ f, tit, cls }) => {
      const n = f === 'cantidad' ? (hayRango() ? 1 : 0) : marcados(f).size;
      return `<th class="${cls || ''}">
        <button class="ord__th ${n ? 'is-on' : ''}" data-col="${f}">
          <span>${tit}</span>${ICO}${n > 1 ? `<i>${n}</i>` : ''}
        </button></th>`;
    }).join('');

    // Agrupadas por orden, en el orden que trae el ERP (IdOrden descendente):
    // lo último lanzado es lo que se está mirando.
    const grupos = new Map();
    for (const r of vis) {
      if (!grupos.has(r.o.idorden)) grupos.set(r.o.idorden, { o: r.o, bonos: [] });
      grupos.get(r.o.idorden).bonos.push(r.b);
    }

    const cuerpo = [...grupos.values()].map(({ o, bonos }) => {
      const piezas = bonos.reduce((n, b) => n + b.cantidad, 0);
      const fila = `<tr class="ord__orden"><td colspan="${CAB.length}">
          <b>${esc(o.idorden)}</b>
          <span class="art">${esc(o.articulo)} <u>${esc(o.descrip)}</u></span>
          <em>${bonos.length} bono${bonos.length > 1 ? 's' : ''} · ${num(piezas)} piezas</em>
        </td></tr>`;
      return fila + bonos.map(b => `<tr class="ord__bono ${b.asignados ? 'tiene-gente' : ''}">
        <td class="num ord__cod">${esc(b.idbono)}</td>
        <td><b>${esc(b.descrip) || '—'}</b>${b.asignados
            ? `<span class="ord__chip">${b.asignados} asignado${b.asignados > 1 ? 's' : ''}</span>` : ''}</td>
        <td>${b.maquina
            ? `${esc(b.maquina)}<span class="sub ord__cod">${esc(b.matricula)}</span>`
            : '<i class="ord__fuera">Sin máquina · operación externa</i>'}</td>
        <td>${b.area ? `<span class="ord__area">${esc(b.area)}</span>` : ''}</td>
        <td><b class="ord__cod">${esc(b.art_salida)}</b><span class="sub">${esc(b.descrip_salida)}</span></td>
        <td>${b.modelo || b.modelo_maquina
            ? esc(b.modelo || b.modelo_maquina) : '<span class="sub">—</span>'}</td>
        <td><div class="ord__q"><b>${num(b.cantidad)}</b>
            <i style="width:${Math.max(6, (b.cantidad / tope) * 100)}%"></i></div></td>
      </tr>`).join('');
    }).join('');

    $('ord-tabla').innerHTML = `<thead><tr>${cab}</tr></thead><tbody>${cuerpo}</tbody>`;
    $('ord-sin-resultados').hidden = vis.length > 0;
  }

  function refrescar() {
    const vis = filas.filter(r => pasa(r));
    stats(vis); barra(vis); tabla(vis);
    if (abierto) menu(abierto);   // las cuentas del menú abierto han cambiado
  }

  /* ---------- el menú de una columna ---------- */

  function menu(c, ancla) {
    const p = $('ord-pop');
    // `nuevo` solo cuando el menú no es ya el de esta columna: al refrescar las
    // cuentas se repinta la lista y NO la caja de búsqueda, que perdería lo
    // teclado y el foco.
    const nuevo = p.hidden || p.dataset.col !== c;
    abierto = c;
    p.dataset.col = c;

    if (c === 'cantidad') {
      if (nuevo) {
        p.innerHTML = `<header><b>Cantidad</b><button data-lim="cantidad">Quitar</button></header>
          <div class="fpop__rango">
            <label>Desde<input type="number" min="0" data-r="min" value="${rango.min ?? ''}"></label>
            <label>Hasta<input type="number" min="0" data-r="max" value="${rango.max ?? ''}"></label>
          </div>`;
      }
    } else {
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
    }

    p.hidden = false;
    if (ancla) {   // debajo de su cabecera, y encajado dentro de la ventana
      const r = ancla.getBoundingClientRect();
      p.style.top = `${Math.min(r.bottom + 6, innerHeight - p.offsetHeight - 8)}px`;
      p.style.left = `${Math.min(Math.max(8, r.left), innerWidth - p.offsetWidth - 8)}px`;
      p.querySelector('.fpop__buscar')?.focus();
    }
  }

  function cerrar() { abierto = null; $('ord-pop').hidden = true; }

  function quita(c) {
    if (c === '*') { Object.keys(sel).forEach(k => sel[k].clear()); rango = { min: null, max: null }; }
    else if (c === 'cantidad') rango = { min: null, max: null };
    else marcados(c).clear();
    refrescar();
  }

  /* ---------- eventos ---------- */

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
      const asig = e.target.closest('[data-asig]');
      if (asig) {
        const s = marcados('asignacion');
        s.clear();
        if (asig.dataset.asig) s.add(asig.dataset.asig);
        cerrar(); refrescar(); return;
      }
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
    if (e.target.classList.contains('fpop__buscar')) { menu(abierto); return; }
    const r = e.target.dataset?.r;
    if (r) {
      rango[r] = e.target.value === '' ? null : Number(e.target.value);
      refrescar();
    }
  });

  document.addEventListener('keydown', e => { if (e.key === 'Escape') cerrar(); });
  addEventListener('resize', cerrar);
  // Al scrollar se cierra, porque va anclado a una cabecera que se mueve. Se
  // excluye el scroll DE DENTRO del menú: su lista de valores tiene el suyo.
  addEventListener('scroll', e => {
    if (abierto && !e.target.closest?.('.fpop')) cerrar();
  }, true);

  /* ---------- carga ---------- */

  function pintar(d) {
    datos = d;
    // El texto de la búsqueda libre se precalcula: se busca en todo lo que se
    // ve, que es lo que uno espera de una caja de búsqueda.
    filas = d.ordenes.flatMap(o => o.bonos.map(b => ({
      o, b,
      texto: [o.idorden, o.articulo, o.descrip, b.idbono, b.descrip, b.matricula,
        b.maquina, b.area, b.art_salida, b.descrip_salida, b.modelo,
        b.modelo_maquina].join(' ').toLowerCase(),
    })));
    cerrar();
    refrescar();
    const g = new Date(d.generado);
    $('generado').textContent = 'Leído a las ' +
      g.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
    $('ord-cargando').hidden = true;
    $('ord-cuerpo').hidden = false;
  }

  async function cargar() {
    $('ord-error').hidden = true;
    if (!datos) $('ord-cargando').hidden = false;
    try {
      const r = await fetch('/api/ordenes-no-asignadas');
      if (!r.ok) throw new Error('El ERP respondió ' + r.status);
      pintar(await r.json());
    } catch (e) {
      $('ord-cargando').hidden = true;
      $('ord-error').hidden = false;
      $('ord-error').textContent = 'No se pudo leer: ' + e.message;
    }
  }

  function setBusqueda(t) { busqueda = t.trim().toLowerCase(); refrescar(); }

  document.addEventListener('DOMContentLoaded', cargar);
  return { cargar, setBusqueda };
})();
