/* ============================================================
   GYC · Consultor de Bonos
   Que bonos hay por maquina en las ordenes activas, y en que
   estado. Portado de la v1, que vivia en una pagina con estilo
   propio; ahora usa el patron de las demas pantallas.

   Una sola peticion: se piden TODOS los estados de bono y las
   pestanas, la maquina y la busqueda filtran en el cliente. La
   v1 lanzaba cinco peticiones en paralelo -una por pestana- y
   otra mas cada vez que se cambiaba de maquina.
   ============================================================ */
const Consultor = (() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = v => Number(v || 0).toLocaleString('es-ES', { maximumFractionDigits: 2 });

  //  Los cuatro estados de bono del ERP y la clase de su etiqueta.
  const ESTADO = {
    0: { tit: 'En espera',  cls: 'espera' },
    1: { tit: 'Activo',     cls: 'activado' },
    2: { tit: 'Finalizado', cls: 'finalizado' },
    3: { tit: 'Bloqueado',  cls: 'bloqueado' },
  };

  //  `e` es el IdEstado que deja pasar cada boton; null, todos.
  const FILTROS = {
    activos:   { tit: 'Activos',   e: 1 },
    espera:    { tit: 'En espera', e: 0 },
    todos:     { tit: 'Total',     e: null },
    bloqueado: { tit: 'Bloqueados', e: 3 },
  };

  let datos = null, filtro = 'activos', maquina = '', busqueda = '', busquedaBono = '';

  //  "6372/30", "6372·30" o "6372 30" son la misma orden y bono, igual que en
  //  duplicados y en urgencia.
  const aBono = t => t.replace(/(\d)\s*[·.\-\/ ]\s*(\d)/g, '$1/$2');
  const coincide = b => !busqueda
    || b.texto.includes(busqueda) || b.texto.includes(busquedaBono);
  const deEstado  = (b, f) => FILTROS[f].e === null || b.estado_bono === FILTROS[f].e;
  const deMaquina = b => !maquina || b.matricula === maquina;

  //  PARADO: el ERP lo tiene como Activo y nadie esta fichando ahora mismo.
  //  Lo dice el ERP en vivo (lineas con Hfinal NULL), no la replica.
  //
  //  Se llamaba "Sin fichar", heredado de la v1, y el nombre enganaba: se leia
  //  como "nunca se ha fichado" cuando lo que dice es "ahora no hay nadie".
  //  Ojo: tampoco significa terminado. El bono 6717/10 salia asi con sus tres
  //  fichajes cerrados y 110 minutos trabajados esa manana; el ERP seguia sin
  //  darlo por finalizado ni declarar una sola pieza.
  const estaParado = b => b.estado_bono === 1 && !b.tiene_fichaje_activo;

  //  La cuenta de cada boton sale de lo que dejan los OTROS filtros: si no, un
  //  boton promete bonos que al pulsarlo no aparecen.
  function botones() {
    const base = datos.bonos.filter(b => coincide(b) && deMaquina(b));
    $('con-filtros').innerHTML = Object.entries(FILTROS).map(([k, f]) => {
      const n = base.filter(b => deEstado(b, k)).length;
      return `<button class="${k === filtro ? 'is-active' : ''}" data-f="${k}">${f.tit} · ${num(n)}</button>`;
    }).join('');
  }

  //  El desplegable se arma con las maquinas que de verdad hay en pantalla, no
  //  con el censo entero: ofrecer una que al elegirla no devuelve nada es peor
  //  que no ofrecerla.
  function selectorMaquinas() {
    const visibles = datos.bonos.filter(b => coincide(b) && deEstado(b, filtro));
    const mapa = new Map();
    visibles.forEach(b => { if (b.matricula) mapa.set(b.matricula, b.descrip_matricula || b.matricula); });
    const opciones = [...mapa.entries()]
      .sort((a, b) => a[1].localeCompare(b[1], 'es', { sensitivity: 'base' }));

    //  Si la maquina elegida se queda sin bonos al cambiar de pestana, se
    //  mantiene para poder verla seleccionada y quitarla.
    if (maquina && !mapa.has(maquina)) opciones.unshift([maquina, maquina + ' (sin bonos)']);

    //  No es un <select>: tambien se puede escribir el nombre de la maquina
    //  para no bajar a mano por la lista entera (ver combo.js).
    Combo.pintar('con-maquina', {
      opciones,
      valor: maquina,
      vacio: `Todas las máquinas · ${num(mapa.size)}`,
      onElegir: setMaquina,
    });
  }

  function tabla(vis) {
    //  Dos articulos distintos, y conviene no confundirlos: el FINAL es lo que
    //  la orden fabrica; el del BONO es la pieza intermedia que sale de esta
    //  operacion. En 625 de las 783 filas no son el mismo -la orden 6734 hace
    //  una division de nido y sus tres bonos sacan tres chapas distintas-, asi
    //  que la columna que decia "Articulo" a secas enganaba.
    const cab = `<tr>
        <th><span class="dup__th">Estado</span></th>
        <th class="num"><span class="dup__th">Orden</span></th>
        <th class="num"><span class="dup__th">Bono</span></th>
        <th><span class="dup__th">Artículo final</span></th>
        <th><span class="dup__th">Artículo del bono</span></th>
        <th><span class="dup__th">Máquina</span></th>
        <th><span class="dup__th">Área</span></th>
        <th><span class="dup__th">Operario</span></th>
        <th><span class="dup__th">Cliente</span></th>
        <th><span class="dup__th">Usuario</span></th>
        <th><span class="dup__th">Material</span></th>
      </tr>`;
    //  La fila NO se tinta por estado: para eso esta su pastilla, ahora la
    //  primera columna. El unico color de fila que queda es el rojo de "falta
    //  material", y justamente por eso se ve -- cuando todas las filas iban de
    //  algun color, ninguna destacaba.
    const cuerpo = vis.map(b => {
      const parado = estaParado(b);
      const e = parado
        ? { tit: 'Parado', cls: 'parado' }
        : (ESTADO[b.estado_bono] || ESTADO[0]);
      return `<tr class="ord__bono dup__bono${b.sin_material ? ' sin-material' : ''}">
        <td><span class="dup__estado is-${e.cls}">${e.tit}</span></td>
        <td class="num ord__cod"><b>${esc(b.idorden)}</b></td>
        <td class="num ord__cod">${esc(b.idbono)}</td>
        <td><b>${esc(b.descrip_articulo_orden) || '—'}</b>${
          b.idarticulo_orden ? `<span class="sub">${esc(b.idarticulo_orden)}</span>` : ''}</td>
        <td>${esc(b.descrip_articulo) || '—'}</td>
        <td>${esc(b.descrip_matricula || b.matricula) || '—'}${
          b.matricula && b.descrip_matricula ? `<span class="sub">${esc(b.matricula)}</span>` : ''}</td>
        <td>${esc(b.area) || '—'}</td>
        <td>${(b.operarios || []).length
                ? esc(b.operarios.join(', '))
                : '<span class="sub">Sin asignar</span>'}</td>
        <td>${esc(b.idcliente) || '—'}</td>
        <td>${esc(b.usuario) || '—'}</td>
        <td>${b.sin_material
                ? '<span class="dup__material">Faltante</span>'
                : '<span class="dup__material--ok">—</span>'}</td>
      </tr>`;
    }).join('');
    $('con-tabla').innerHTML = `<thead>${cab}</thead><tbody>${cuerpo}</tbody>`;
    $('con-sin-resultados').hidden = vis.length > 0;
  }

  function refrescar() {
    const vis = datos.bonos.filter(b => coincide(b) && deEstado(b, filtro) && deMaquina(b));
    botones();
    selectorMaquinas();
    $('con-resumen').textContent = vis.length === datos.total
      ? `${num(vis.length)} bonos`
      : `${num(vis.length)} de ${num(datos.total)} bonos`;
    tabla(vis);
  }

  function pintar(d) {
    datos = d;
    d.bonos.forEach(b => {
      //  Se busca por el ARTICULO FINAL -codigo y descripcion- ademas de por
      //  el del bono: quien pregunta "que hay de la division de nido" piensa
      //  en lo que sale por la puerta, no en la chapa intermedia. Y por el
      //  OPERARIO, para poder ver de un vistazo que tiene uno encima.
      b.texto = [b.idorden, b.idbono, `${b.idorden}/${b.idbono}`,
                 b.idarticulo_orden, b.descrip_articulo_orden, b.descrip_articulo,
                 b.matricula, b.descrip_matricula, b.area, b.idcliente,
                 b.usuario, (b.operarios || []).join(' '),
                 //  Para poder escribir "faltante" o "sin material" y quedarse
                 //  con esos.
                 b.sin_material ? 'faltante falta material sin material' : ''
                ].join(' ').toLowerCase();
    });
    refrescar();
    $('generado').textContent = 'Leído a las ' + new Date()
      .toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
    $('con-cargando').hidden = true;
    $('con-cuerpo').hidden = false;
  }

  async function cargar() {
    $('con-error').hidden = true;
    if (!datos) $('con-cargando').hidden = false;
    try {
      //  Sin `estado_bono`: vienen los cuatro estados y las pestanas los
      //  reparten en el cliente.
      const r = await fetch('/api/bonos?estado_orden=1');
      if (!r.ok) throw new Error('El ERP respondió ' + r.status);
      pintar(await r.json());
    } catch (e) {
      $('con-cargando').hidden = true;
      $('con-error').hidden = false;
      $('con-error').textContent = 'No se pudo leer: ' + e.message;
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
    const b = e.target.closest('#con-filtros [data-f]');
    if (b && datos) { filtro = b.dataset.f; refrescar(); }
  });

  document.addEventListener('DOMContentLoaded', cargar);
  return { cargar, setBusqueda, setMaquina };
})();
