/* Planificación de la producción — rejilla personas × días.
   Lee /api/plan; sin dependencias, igual que el resto de la app. */
const Plan = (() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtH = m => m >= 60 ? `${Math.floor(m / 60)}h${m % 60 ? String(m % 60).padStart(2, '0') : ''}`
                            : `${m}m`;

  // La carga como tramos, no como degradado: lo que se busca de un vistazo es
  // "este esta libre / este va justo / este no tiene hueco".
  // No hay tramo por encima del 100%: la carga de un dia es la UNION de lo
  // ocupado dentro de ese dia, asi que esta acotada por el propio dia. Lo que
  // no cabe hoy no desborda, se coloca manana, y eso se lee en la fila.
  const nivel = pct => pct === 0 ? 'libre' : pct <= 60 ? 'holgado' : 'lleno';
  const ST_LABEL = {
    plazo: 'En curso', disponible: 'En espera', parada: 'Bloqueada',
    'sin-estimar': 'Sin datos fiables', riesgo: 'En riesgo',
    'pendiente-cierre': 'Pendiente de cerrar',
    continuacion: 'Fabricación pendiente',
  };

  let dias = 5, vista = 'empleado', datos = null;

  function leyenda(d) {
    $('leyenda').innerHTML =
      `<span class="plan__lg"><i class="plan__sw is-libre"></i>Libre</span>` +
      `<span class="plan__lg"><i class="plan__sw is-holgado"></i>Con hueco</span>` +
      `<span class="plan__lg"><i class="plan__sw is-lleno"></i>Jornada completa</span>` +
      `<span class="plan__lg"><i class="plan__sw is-simultaneo"></i>Dos cosas a la vez</span>` +
      `<span class="plan__lg plan__lg--fin">${d.plantilla}
        ${vista === 'maquina' ? 'máquinas' : 'operarios en plantilla'} ·
        jornada de ${fmtH(d.jornada_min)}</span>`;
  }

  function celda(c, p) {
    const n = nivel(c.pct);
    if (!c.min) return `<td class="plan__c is-libre"><span class="plan__vacio">—</span></td>`;
    // Un bono bloqueado o sin estimacion fiable dentro de la celda se marca:
    // la carga es la misma pero lo que se puede prometer no.
    const aviso = c.bonos.some(b => b.estado === 'parada') ? ' tiene-parada'
                : c.bonos.some(b => b.estado === 'sin-estimar') ? ' tiene-dudoso' : '';
    // Dos fichajes en marcha a la vez: puede ser una maquina automatica
    // atendida mientras se hace otra cosa, o un fichaje que nadie cerro.
    const simult = c.simultaneo ? ' es-simultaneo' : '';
    return `<td class="plan__c is-${n}${aviso}${simult}" data-p="${esc(p.id)}" data-f="${c.fecha}">
      <div class="plan__barra"><i style="width:${Math.min(100, c.pct)}%"></i></div>
      <div class="plan__cifra">${c.pct}%<span>${c.bonos.length} bono${c.bonos.length > 1 ? 's' : ''}</span></div>
    </td>`;
  }

  function pintar(d) {
    datos = d;
    leyenda(d);
    const cab = d.dias.map(x =>
      `<th class="${x.hoy ? 'is-hoy' : ''}">${esc(x.etiqueta)}
         <span>${x.personas} ${vista === 'maquina' ? 'máq' : 'pers'} · ${fmtH(x.min)}
           ${x.hoy && x.disponible < d.jornada_min ? `· quedan ${fmtH(x.disponible)}` : ''}</span></th>`).join('');
    const filas = d.personas.map(p => {
      const vacio = p.dias_con_trabajo === 0 ? ' is-vacia' : '';
      return `<tr class="${vacio}">
        <th class="plan__pers"><b>${esc(p.nombre)}</b>
          <span>${esc(p.areas.join(', ') || 'Sin área')}</span></th>
        ${p.dias.map(c => celda(c, p)).join('')}
      </tr>`;
    }).join('');
    $('tabla').innerHTML =
      `<thead><tr><th class="plan__pers">${vista === 'maquina' ? 'Máquina' : 'Operario'}</th>`
      + `${cab}</tr></thead><tbody>${filas}</tbody>`;
    const g = new Date(d.generado);
    $('generado').textContent = 'Proyectado a las ' +
      g.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
    $('plan-cargando').hidden = true;
    $('plan-cuerpo').hidden = false;
  }

  // ── Detalle de una celda ──
  function bonosDe(pid, fecha) {
    const p = datos.personas.find(x => x.id === pid);
    const c = p && p.dias.find(x => x.fecha === fecha);
    return c ? c.bonos : [];
  }
  function mostrarTip(e) {
    const td = e.target.closest('.plan__c[data-p]');
    if (!td) return ocultarTip();
    const bonos = bonosDe(td.dataset.p, td.dataset.f);
    if (!bonos.length) return ocultarTip();
    const c = datos.personas.find(x => x.id === td.dataset.p)
                   .dias.find(x => x.fecha === td.dataset.f);
    $('tip').innerHTML = (c.simultaneo
      ? `<div class="tip__sub"><b>${bonos.length} a la vez</b> · ${fmtH(c.min_bonos)} de trabajo
           en ${fmtH(c.min)} de reloj</div>` : '') + bonos.map(b =>
      `<div class="tip__row"><b>${esc(b.idorden)}·${esc(b.idbono)}</b>
         <span>${fmtH(b.min)}</span></div>
       <div class="tip__sub">${esc(b.art_id || '')} ${esc((b.art || '').slice(0, 34))}<br>
         ${esc(b.maquina || '')} · ${esc(ST_LABEL[b.estado] || b.estado)}</div>`).join('');
    $('tip').classList.add('is-visible');
    moverTip(e);
  }
  function moverTip(e) {
    const t = $('tip'), m = 14;
    let x = e.clientX + m, y = e.clientY + m;
    if (x + t.offsetWidth > innerWidth - 8) x = e.clientX - t.offsetWidth - m;
    if (y + t.offsetHeight > innerHeight - 8) y = e.clientY - t.offsetHeight - m;
    t.style.left = x + 'px'; t.style.top = y + 'px';
  }
  const ocultarTip = () => $('tip').classList.remove('is-visible');

  async function cargar() {
    $('plan-error').hidden = true;
    if (!datos) $('plan-cargando').hidden = false;
    try {
      const r = await fetch(`/api/plan?dias=${dias}&vista=${vista}`);
      if (!r.ok) throw new Error('El ERP respondió ' + r.status);
      pintar(await r.json());
    } catch (e) {
      $('plan-cargando').hidden = true;
      $('plan-error').hidden = false;
      $('plan-error').textContent = 'No se pudo proyectar: ' + e.message;
    }
  }

  function setVista(v) {
    vista = v;
    [...$('plan-vista').children].forEach(b => b.classList.toggle('is-active', b.dataset.v === v));
    datos = null;                 // el censo cambia entero: se recarga de cero
    cargar();
  }

  function setDias(n) {
    dias = n;
    [...$('plan-dias').children].forEach(b =>
      b.classList.toggle('is-active', b.textContent.trim() === n + ' días'));
    cargar();
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('mousemove', e => {
      if (e.target.closest('.plan__c[data-p]')) mostrarTip(e); else ocultarTip();
    });
    cargar();
  });
  return { cargar, setDias, setVista };
})();
