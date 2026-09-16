/* ============================================================
   GYC · Urgencia de bonos
   Bonos vivos de ordenes cuyo articulo no tiene stock libre, en
   el almacen Principal o en Produccion. Una fila por bono, en el
   orden que trae el ERP (por descripcion). El stock es el del
   articulo de la ORDEN, no el de lo que fabrica el bono.

   Los tres filtros (almacen, estado y maquina) se aplican en el
   cliente sobre los mismos datos: el ERP ya manda los dos saldos
   y la matricula, asi que cambiar de filtro no cuesta una peticion.
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

  // En que almacen se mira el negativo. "Total" es estar en negativo en ALGUNO
  // de los dos, no la suma: un agujero en Produccion sigue siendo un agujero
  // aunque en Principal sobre material.
  const ALMACENES = {
    total:      { tit: 'Total',      hay: b => b.libre_principal < 0 || b.libre_produccion < 0 },
    principal:  { tit: 'Principal',  hay: b => b.libre_principal < 0 },
    produccion: { tit: 'Producción', hay: b => b.libre_produccion < 0 },
  };

  let datos = null, filtro = 'todos', almacen = 'total', maquina = '';
  let busqueda = '', busquedaBono = '';

  // "6372/30", "6372·30", "6372-30" o "6372 30" son la misma orden y bono.
  // Se compara con la forma normalizada Y con lo tecleado tal cual, igual que
  // en duplicados, para no perder un codigo de articulo que lleve guion.
  const aBono = t => t.replace(/(\d)\s*[·.\-\/ ]\s*(\d)/g, '$1/$2');
  const coincide = b => !busqueda
    || b.texto.includes(busqueda) || b.texto.includes(busquedaBono);
  const deEstado  = (b, f) => FILTROS[f].e === null || b.estado === FILTROS[f].e;
  const deAlmacen = (b, a) => ALMACENES[a].hay(b);
  const deMaquina = b => !maquina || b.matricula === maquina;

  // Todo menos el filtro que se esta contando: si la cuenta de un boton se
  // calculara sobre lo ya filtrado por el, siempre diria lo mismo que el total.
  const base = b => coincide(b) && deMaquina(b);

  // La cuenta de cada boton sale de lo que dejan los OTROS filtros: si no, un
  // boton promete bonos que al pulsarlo no aparecen.
  function botones() {
    const paraAlmacen = datos.bonos.filter(b => base(b) && deEstado(b, filtro));
    $('bon-almacenes').innerHTML = Object.entries(ALMACENES).map(([k, a]) => {
      const n = paraAlmacen.filter(b => deAlmacen(b, k)).length;
      return `<button class="${k === almacen ? 'is-active' : ''}" data-a="${k}">${a.tit} · ${num(n)}</button>`;
    }).join('');

    const paraEstado = datos.bonos.filter(b => base(b) && deAlmacen(b, almacen));
    $('bon-filtros').innerHTML = Object.entries(FILTROS).map(([k, f]) => {
      const n = paraEstado.filter(b => deEstado(b, k)).length;
      return `<button class="${k === filtro ? 'is-active' : ''}" data-f="${k}">${f.tit} · ${num(n)}</button>`;
    }).join('');
  }

  // El desplegable se arma con las maquinas que de verdad hay en pantalla, no
  // con el censo entero: ofrecer una maquina que al elegirla no devuelve nada
  // es peor que no ofrecerla.
  function selectorMaquinas() {
    const visibles = datos.bonos.filter(b => coincide(b) && deEstado(b, filtro) && deAlmacen(b, almacen));
    const mapa = new Map();
    visibles.forEach(b => {
      if (b.matricula) mapa.set(b.matricula, b.maquina || b.matricula);
    });
    const opciones = [...mapa.entries()]
      .sort((a, b) => a[1].localeCompare(b[1], 'es', { sensitivity: 'base' }));

    // Si la maquina elegida se queda sin bonos al cambiar otro filtro, se
    // mantiene en la lista para poder verla seleccionada y desmarcarla.
    if (maquina && !mapa.has(maquina)) opciones.unshift([maquina, maquina + ' (sin bonos)']);

    $('bon-maquina').innerHTML =
      `<option value="">Todas las máquinas · ${num(mapa.size)}</option>` +
      opciones.map(([m, d]) =>
        `<option value="${esc(m)}" ${m === maquina ? 'selected' : ''}>${esc(d)}</option>`).join('');
  }

  function tabla(vis) {
    // Las columnas de la consulta de Access mas las dos que pedia la pantalla:
    // la maquina del bono y el stock libre separado por almacen (antes solo se
    // veia el del Principal, y los agujeros de Produccion quedaban ocultos).
    const cab = `<tr>
        <th class="num"><span class="dup__th">Orden</span></th>
        <th class="num"><span class="dup__th">Bono</span></th>
        <th><span class="dup__th">Artículo</span></th>
        <th><span class="dup__th">Descripción</span></th>
        <th><span class="dup__th">Máquina</span></th>
        <th class="num"><span class="dup__th">Libre Principal</span></th>
        <th class="num"><span class="dup__th">Libre Producción</span></th>
        <th><span class="dup__th">Artículo de la orden</span></th>
        <th class="num"><span class="dup__th">Libre − mínimo</span></th>
        <th><span class="dup__th">Estado</span></th>
      </tr>`;
    const celdaStock = v => `<td class="num"${v < 0 ? ' style="color:var(--rojo)"' : ''}><b>${num(v)}</b></td>`;
    // La fila entera va tintada segun el estado del bono, igual que en el
    // Consultor: de un vistazo se ve el reparto sin leer la ultima columna.
    // Aqui solo hay tres estados vivos --en espera, activado y bloqueado--, y
    // los tres tienen ya su color en el sistema compartido.
    const cuerpo = vis.map(b => `<tr class="ord__bono es-${ESTADO[b.estado] || 'espera'}">
        <td class="num ord__cod"><b>${esc(b.idorden)}</b></td>
        <td class="num ord__cod">${esc(b.idbono)}</td>
        <td class="ord__cod">${esc(b.idarticulo)}</td>
        <td><b>${esc(b.descrip) || '—'}</b></td>
        <td>${esc(b.maquina || b.matricula) || '—'}</td>
        ${celdaStock(b.libre_principal)}
        ${celdaStock(b.libre_produccion)}
        <td>${esc(b.descrip_orden) || '—'}</td>
        <td class="num"><b>${num(b.sobre_minimo)}</b></td>
        <td><span class="dup__estado is-${ESTADO[b.estado] || 'espera'}">${esc(b.estado_label)}</span></td>
      </tr>`).join('');
    $('bon-tabla').innerHTML = `<thead>${cab}</thead><tbody>${cuerpo}</tbody>`;
    $('bon-sin-resultados').hidden = vis.length > 0;
  }

  function refrescar() {
    const vis = datos.bonos.filter(b =>
      coincide(b) && deEstado(b, filtro) && deAlmacen(b, almacen) && deMaquina(b));
    botones();
    selectorMaquinas();
    $('bon-resumen').textContent = vis.length === datos.total_bonos
      ? `${num(vis.length)} bonos`
      : `${num(vis.length)} de ${num(datos.total_bonos)} bonos`;
    tabla(vis);
  }

  function pintar(d) {
    datos = d;
    d.bonos.forEach(b => {
      b.texto = [b.idorden, b.idbono, `${b.idorden}/${b.idbono}`, b.idarticulo, b.descrip,
                 b.descrip_orden, b.matricula, b.maquina].join(' ').toLowerCase();
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

  function setMaquina(m) {
    maquina = m;
    if (datos) refrescar();
  }

  document.addEventListener('click', e => {
    if (!datos) return;
    const f = e.target.closest('#bon-filtros [data-f]');
    if (f) { filtro = f.dataset.f; refrescar(); return; }
    const a = e.target.closest('#bon-almacenes [data-a]');
    if (a) { almacen = a.dataset.a; refrescar(); }
  });

  document.addEventListener('DOMContentLoaded', cargar);
  return { cargar, setBusqueda, setMaquina };
})();
