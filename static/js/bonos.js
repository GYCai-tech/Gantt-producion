/* ============================================================
   GYC · Bonos
   Bonos vivos cuyo articulo de orden tiene el stock libre en
   negativo en el almacen Principal. Una fila por bono, en el
   orden que trae el ERP (por descripcion).
   ============================================================ */
const Bonos = (() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = v => Number(v || 0).toLocaleString('es-ES', { maximumFractionDigits: 2 });

  // Los tres estados vivos del ERP y la clase de su etiqueta (la de duplicados).
  const ESTADO = { 0: 'espera', 1: 'activado', 3: 'bloqueado' };

  // `e` es el IdEstado que deja pasar cada boton; null, todos.
  const FILTROS = {
    todos:     { tit: 'Todos',     e: null },
    espera:    { tit: 'En espera', e: 0 },
    activado:  { tit: 'Activado',  e: 1 },
    bloqueado: { tit: 'Bloqueado', e: 3 },
  };

  let datos = null, filtro = 'todos', busqueda = '', busquedaBono = '';

  // "6372/30", "6372·30", "6372-30" o "6372 30" son la misma orden y bono.
  // Se compara con la forma normalizada Y con lo tecleado tal cual, igual que
  // en duplicados, para no perder un codigo de articulo que lleve guion.
  const aBono = t => t.replace(/(\d)\s*[·.\-\/ ]\s*(\d)/g, '$1/$2');
  const coincide = b => !busqueda
    || b.texto.includes(busqueda) || b.texto.includes(busquedaBono);
  const deEstado = (b, f) => FILTROS[f].e === null || b.estado === FILTROS[f].e;

  function stats(vis) {
    const ordenes = new Set(vis.map(b => b.idorden)).size;
    const articulos = new Set(vis.map(b => b.idarticulo)).size;
    $('bon-stats').innerHTML = [
      ['Bonos', vis.length, datos.total_bonos, 'ord__stat--ojo'],
      ['Órdenes', ordenes, datos.total_ordenes, ''],
      ['Artículos', articulos, datos.total_articulos, ''],
    ].map(([t, n, tot, cls]) => `<div class="ord__stat ${cls}">
        <span>${t}</span><b>${num(n)}</b>${
          n !== tot ? `<em>de ${num(tot)}</em>` : ''}</div>`).join('');
  }

  // La cuenta de cada boton sale de lo que deja la busqueda: si no, un boton
  // promete bonos que al pulsarlo no aparecen.
  function botones() {
    const buscados = datos.bonos.filter(coincide);
    $('bon-filtros').innerHTML = Object.entries(FILTROS).map(([k, f]) => {
      const n = buscados.filter(b => deEstado(b, k)).length;
      return `<button class="${k === filtro ? 'is-active' : ''}" data-f="${k}">${f.tit} · ${num(n)}</button>`;
    }).join('');
  }

  function tabla(vis) {
    const cab = `<tr>
        <th class="num"><span class="dup__th">Orden · bono</span></th>
        <th><span class="dup__th">Estado</span></th>
        <th><span class="dup__th">Artículo</span></th>
        <th><span class="dup__th">Descripción</span></th>
        <th class="num"><span class="dup__th">Stock libre</span></th>
      </tr>`;
    const cuerpo = vis.map(b => `<tr class="ord__bono">
        <td class="num ord__cod"><b>${esc(b.idorden)}</b>·${esc(b.idbono)}</td>
        <td><span class="dup__estado is-${ESTADO[b.estado] || 'espera'}">${esc(b.estado_label)}</span></td>
        <td class="ord__cod">${esc(b.idarticulo)}</td>
        <td><b>${esc(b.descrip) || '—'}</b></td>
        <td class="num"><b>${num(b.stock)}</b></td>
      </tr>`).join('');
    $('bon-tabla').innerHTML = `<thead>${cab}</thead><tbody>${cuerpo}</tbody>`;
    $('bon-sin-resultados').hidden = vis.length > 0;
  }

  function refrescar() {
    const vis = datos.bonos.filter(b => coincide(b) && deEstado(b, filtro));
    botones();
    stats(vis);
    $('bon-resumen').textContent = vis.length === datos.total_bonos
      ? `${num(vis.length)} bonos`
      : `${num(vis.length)} de ${num(datos.total_bonos)} bonos`;
    tabla(vis);
  }

  function pintar(d) {
    datos = d;
    d.bonos.forEach(b => {
      b.texto = [b.idorden, b.idbono, `${b.idorden}/${b.idbono}`, b.idarticulo, b.descrip]
        .join(' ').toLowerCase();
    });
    refrescar();
    $('generado').textContent = 'Leído a las ' + new Date(d.generado)
      .toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
    $('bon-cargando').hidden = true;
    $('bon-cuerpo').hidden = false;
  }

  async function cargar() {
    $('bon-error').hidden = true;
    if (!datos) $('bon-cargando').hidden = false;
    try {
      const r = await fetch('/api/bonos-stock');
      if (!r.ok) throw new Error('El ERP respondió ' + r.status);
      pintar(await r.json());
    } catch (e) {
      $('bon-cargando').hidden = true;
      $('bon-error').hidden = false;
      $('bon-error').textContent = 'No se pudo leer: ' + e.message;
    }
  }

  function setBusqueda(t) {
    busqueda = t.trim().toLowerCase();
    busquedaBono = aBono(busqueda);
    if (datos) refrescar();
  }

  document.addEventListener('click', e => {
    const b = e.target.closest('#bon-filtros [data-f]');
    if (b && datos) { filtro = b.dataset.f; refrescar(); }
  });

  document.addEventListener('DOMContentLoaded', cargar);
  return { cargar, setBusqueda };
})();
