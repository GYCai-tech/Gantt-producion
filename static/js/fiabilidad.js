/* Fiabilidad de las estimaciones — lee /api/fiabilidad y lo pinta.
   Sin dependencias: la app no tiene build y no se va a añadir uno por esto. */
const Fiab = (() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const NOMBRE = {
    teorico:        'Escandallo del ERP',
    media_articulo: 'Media del artículo',
    media_trabajo:  'Media del trabajo',
    media_maquina:  'Media de la máquina',
    sin_estimacion: 'Sin estimación',
    media_montaje:  'Media de montaje',
  };
  const DETALLE = {
    teorico:        'Trabajos_ManoObra, el tiempo que alguien metió a mano',
    media_articulo: '18 meses de bonos cerrados del mismo artículo',
    media_trabajo:  'Duplicado: 4.830 de 4.836 trabajos fabrican un solo artículo',
    media_maquina:  'Último recurso. La misma máquina hace piezas muy distintas',
    sin_estimacion: 'Ni escandallo ni histórico suficiente',
    media_montaje:  'Lo que suele tardarse en preparar esa máquina',
  };

  // El semáforo de la propia página. Los cortes son los mismos que usa el
  // Gantt para decidir si una barra merece el aviso de "sin datos fiables".
  const nivel = mdape => mdape == null ? 'nulo'
    : mdape <= 25 ? 'bien' : mdape <= 45 ? 'regular' : 'mal';
  const VEREDICTO = {
    bien:    'Sirve para planificar',
    regular: 'Orientativo',
    mal:     'No sirve como estimación',
    nulo:    'Sin datos',
  };

  const pct = v => (v == null ? '—' : (v < 1 ? v.toFixed(1).replace('.', ',') : Math.round(v)) + '%');
  const num = v => (v == null ? '—' : v.toLocaleString('es-ES'));
  // Los ratios se pintan como "1,22x": el resto de la pagina ya va en es-ES y
  // un punto decimal suelto se lee como un separador de miles.
  const rat = v => (v == null ? '—' : v.toLocaleString('es-ES',
    { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + 'x');

  let datos = null;

  function titular(d) {
    const g = d.global;
    const n = nivel(g && g.mdape);
    // Se nombra el escalón que más bonos cubre: es el que decide la sensación
    // general, y en este taller no es el escandallo sino el histórico.
    const dominante = [...d.origenes].filter(o => o.bonos)
      .sort((a, b) => b.bonos - a.bonos)[0];
    $('titular').innerHTML =
      `<div class="fiab__titular-card is-${n}">
         <div class="fiab__titular-num">${pct(g.mdape)}</div>
         <div class="fiab__titular-txt">
           <b>Error típico de una estimación.</b>
           ${g.dentro_25}% de los bonos caen dentro de ±25% y ${g.dentro_50}% dentro de ±50%.
           El sesgo global es ${rat(g.sesgo)}, así que
           ${g.sesgo > 1.05 ? 'de media nos quedamos cortos'
                            : g.sesgo < 0.95 ? 'de media nos pasamos'
                                             : 'no hay una desviación sistemática'}.
         </div>
       </div>
       <div class="fiab__titular-pie">
         Medido sobre <b>${num(d.bonos)}</b> bonos cerrados de los últimos ${d.meses} meses.
         El escalón que más carga lleva es <b>${esc(NOMBRE[dominante.origen])}</b>
         (${pct(dominante.pct)} de los bonos).
       </div>`;
  }

  function tablaOrigenes(d) {
    const filas = d.origenes.map(o => {
      if (!o.bonos) {
        return `<tr class="is-vacio">
          <td><b>${esc(NOMBRE[o.origen])}</b><span>${esc(DETALLE[o.origen])}</span></td>
          <td colspan="5">No llegó a usarse en ningún bono</td></tr>`;
      }
      const n = nivel(o.mdape);
      return `<tr>
        <td><b>${esc(NOMBRE[o.origen])}</b><span>${esc(DETALLE[o.origen])}</span></td>
        <td class="num">${num(o.bonos)}<span>${pct(o.pct)}</span></td>
        <td class="num"><b>${pct(o.mdape)}</b></td>
        <td class="num">${pct(o.dentro_25)}</td>
        <td class="num">${rat(o.sesgo)}</td>
        <td><span class="fiab__chip is-${n}">${VEREDICTO[n]}</span></td>
      </tr>`;
    }).join('');
    $('tabla-origenes').innerHTML =
      `<thead><tr>
         <th>Fuente</th><th class="num">Bonos</th><th class="num">MdAPE</th>
         <th class="num">Dentro ±25%</th><th class="num">Sesgo</th><th>Veredicto</th>
       </tr></thead><tbody>${filas}</tbody>`;
  }

  function histogramas(d) {
    $('histogramas').innerHTML = d.origenes.filter(o => o.histograma).map(o => {
      const max = Math.max(...o.histograma.map(t => t.pct), 1);
      const barras = o.histograma.map(t => {
        // "Casi" es el centro: los dos tramos que rodean el 1x.
        const centro = t.sentido === 'Casi' ? ' is-centro' : '';
        return `<div class="fiab__hbar${centro}" title="${esc(t.sentido)}: ${num(t.bonos)} bonos">
                  <div class="fiab__hbar-col"><i style="height:${100 * t.pct / max}%"></i></div>
                  <div class="fiab__hbar-pct">${pct(t.pct)}</div>
                  <div class="fiab__hbar-lbl">${esc(t.tramo)}</div>
                </div>`;
      }).join('');
      return `<figure class="fiab__hist">
                <figcaption>${esc(NOMBRE[o.origen])}
                  <span>${num(o.bonos)} bonos</span></figcaption>
                <div class="fiab__hbars">${barras}</div>
              </figure>`;
    }).join('');
  }

  function cobertura(d) {
    const total = d.cobertura_hoy.reduce((a, c) => a + c.barras, 0);
    $('cobertura').innerHTML = d.cobertura_hoy.map(c => {
      const o = d.origenes.find(x => x.origen === c.origen);
      const n = nivel(o && o.bonos ? o.mdape : null);
      return `<div class="fiab__cob is-${n}">
                <div class="fiab__cob-num">${num(c.barras)}</div>
                <div class="fiab__cob-lbl">${esc(NOMBRE[c.origen] || c.origen)}</div>
                <div class="fiab__cob-pct">${pct(c.pct)} de las barras</div>
              </div>`;
    }).join('') + `<p class="fiab__nota">${num(total)} barras con estimación en el tablero de hoy.</p>`;
  }

  function tablaPeores(d) {
    if (!d.peores.length) {
      $('tabla-peores').innerHTML =
        '<tbody><tr><td>Ningún artículo supera el umbral. Nada que arreglar.</td></tr></tbody>';
      return;
    }
    const filas = d.peores.map(p => `<tr>
      <td><b>${esc(p.articulo)}</b><span>${esc(p.descrip)}</span></td>
      <td class="num">${num(p.bonos)}</td>
      <td class="num"><b>${num(p.error_horas)} h</b></td>
      <td class="num">${pct(p.mdape)}</td>
      <td class="num">${rat(p.sesgo)}<span>${p.sesgo > 1 ? 'tarda más' : 'tarda menos'}</span></td>
    </tr>`).join('');
    $('tabla-peores').innerHTML =
      `<thead><tr>
         <th>Artículo</th><th class="num">Bonos</th><th class="num">Horas perdidas</th>
         <th class="num">MdAPE</th><th class="num">Sesgo</th>
       </tr></thead><tbody>${filas}</tbody>`;
  }

  function pintar(d) {
    datos = d;
    titular(d); tablaOrigenes(d); histogramas(d); cobertura(d); tablaPeores(d);
    const g = new Date(d.generado);
    $('generado').textContent = 'Calculado a las ' +
      g.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
    $('fiab-cargando').hidden = true;
    $('fiab-cuerpo').hidden = false;
  }

  async function cargar(refrescar) {
    $('fiab-error').hidden = true;
    if (!datos) $('fiab-cargando').hidden = false;
    try {
      const r = await fetch('/api/fiabilidad' + (refrescar ? '?refrescar=true' : ''));
      if (!r.ok) throw new Error('El ERP respondió ' + r.status);
      pintar(await r.json());
    } catch (e) {
      $('fiab-cargando').hidden = true;
      $('fiab-error').hidden = false;
      $('fiab-error').textContent = 'No se pudo analizar: ' + e.message;
    }
  }

  function refrescar() {
    const b = document.getElementById('refresh-label');
    b.textContent = 'Calculando…';
    cargar(true).finally(() => { b.textContent = 'Recalcular'; });
  }

  document.addEventListener('DOMContentLoaded', () => cargar(false));
  return { refrescar };
})();
