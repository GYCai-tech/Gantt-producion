/* Órdenes no asignadas — lee /api/ordenes-no-asignadas y lo pinta agrupado. */
const Ord = (() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = v => Number(v || 0).toLocaleString('es-ES', { maximumFractionDigits: 0 });

  let datos = null, area = 'todos', busqueda = '';

  // Un bono pasa el filtro si coincide el área Y el texto. El texto se busca
  // en todo lo que se ve, que es lo que uno espera de una caja de búsqueda.
  const casa = (o, b) => (area === 'todos' || b.area === area) && (!busqueda || [
    o.idorden, o.articulo, o.descrip, b.idbono, b.descrip,
    b.matricula, b.maquina, b.art_salida, b.descrip_salida, b.modelo,
  ].join(' ').toLowerCase().includes(busqueda));

  function visibles() {
    return datos.ordenes
      .map(o => ({ ...o, bonos: o.bonos.filter(b => casa(o, b)) }))
      .filter(o => o.bonos.length);
  }

  function areas() {
    const todas = ['todos', ...datos.areas];
    $('ord-areas').innerHTML = todas.map(a =>
      `<button class="area-pill ${a === area ? 'is-active' : ''}"
               onclick="Ord.setArea('${a.replace(/'/g, "\'")}')">${
        a === 'todos' ? 'Todas las áreas' : esc(a)}</button>`).join('');
  }

  function tabla() {
    const lista = visibles();
    const bonos = lista.reduce((n, o) => n + o.bonos.length, 0);
    $('ord-resumen').textContent =
      `${num(bonos)} bonos en ${num(lista.length)} órdenes` +
      (bonos < datos.total_bonos ? ` · de ${num(datos.total_bonos)} en total` : '');
    $('ord-sin-resultados').hidden = lista.length > 0;

    const filas = lista.map(o => {
      // Cabecera de la orden: se repite el artículo que se fabrica, que es lo
      // que identifica el trabajo mejor que el número.
      const cab = `<tr class="ord__orden"><td colspan="5">
          <b>${esc(o.idorden)}</b>
          <span>${esc(o.articulo)} · ${esc(o.descrip)}</span>
          <em>${o.bonos.length} bono${o.bonos.length > 1 ? 's' : ''}</em>
        </td></tr>`;
      return cab + o.bonos.map(b => `<tr>
        <td class="num">${esc(b.idbono)}</td>
        <td><b>${esc(b.descrip) || '—'}</b>
            ${b.asignados ? `<span class="ord__chip">${b.asignados} asignado${b.asignados > 1 ? 's' : ''}</span>` : ''}</td>
        <td>${b.maquina ? esc(b.maquina) : '<i class="ord__fuera">Sin máquina · operación externa</i>'}
            <span>${esc(b.area) || 'Sin área'}</span></td>
        <td><b>${esc(b.art_salida)}</b><span>${esc(b.descrip_salida)}</span></td>
        <td class="num">${num(b.cantidad)}</td>
      </tr>`).join('');
    }).join('');

    $('ord-tabla').innerHTML =
      `<thead><tr>
         <th class="num">Bono</th><th>Operación</th><th>Máquina y área</th>
         <th>Artículo que sale</th><th class="num">Cantidad</th>
       </tr></thead><tbody>${filas}</tbody>`;
  }

  function pintar(d) {
    datos = d;
    areas(); tabla();
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

  function setArea(a) { area = a; areas(); tabla(); }
  function setBusqueda(t) { busqueda = t.trim().toLowerCase(); tabla(); }

  document.addEventListener('DOMContentLoaded', cargar);
  return { cargar, setArea, setBusqueda };
})();
