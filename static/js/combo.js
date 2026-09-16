/* ============================================================
   GYC · Desplegable en el que tambien se puede escribir

   Lo comparten el Consultor de bonos y la Urgencia de bonos para
   elegir maquina. Un <select> nativo obliga a bajar con el raton
   por una lista de decenas de maquinas; aqui se escribe un trozo
   del nombre -"adira", "bolsas", o la matricula- y la lista se
   queda con lo que coincide. Se sigue pudiendo abrir y verlas
   todas, como antes.

   Sin libreria y sin <datalist>: el nativo no deja filtrar como
   queremos -busca por prefijo y con tildes- ni darle el aspecto
   de la casa.

   El detalle que manda en el diseno: las pantallas llaman a
   pintar() en CADA refresco, tambien mientras se escribe. Por eso
   aqui solo se reconstruye la lista, nunca el <input>. Si se
   rehiciera el input entero, el cursor saltaria fuera a media
   palabra.
   ============================================================ */
const Combo = (() => {
  'use strict';

  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  //  Se busca sin tildes y sin mayusculas: las maquinas estan dadas de alta
  //  en el ERP como "PLEGADORA ADIRA" y nadie las escribe asi.
  const norm = s => String(s ?? '').toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '');

  const estados = new Map();   //  id del hueco -> estado de ese combo

  //  El texto que se ve cuando hay una maquina elegida. Si la maquina ya no
  //  esta entre las opciones se devuelve su codigo pelado, que es mejor que
  //  dejar la caja en blanco aparentando que no hay filtro puesto.
  function textoDe(st, valor) {
    if (!valor) return '';
    const o = st.opciones.find(([v]) => v === valor);
    return o ? o[1] : valor;
  }

  //  Lo que se ofrece ahora mismo: todo si no se ha escrito nada, y si se ha
  //  escrito, lo que contenga ese texto en el nombre o en la matricula.
  function candidatas(st) {
    const q = norm(st.query);
    if (!q) return st.opciones;
    return st.opciones.filter(([v, t]) => norm(t).includes(q) || norm(v).includes(q));
  }

  function lista(st) {
    const items = candidatas(st);
    st.vistas = items;
    //  "Todas las maquinas" solo mientras no se filtra: cuando se esta
    //  buscando una en concreto, estorba en la primera fila.
    const todas = st.query
      ? ''
      : `<li data-i="-1" class="${st.marca === -1 ? 'is-marcada' : ''}${st.valor ? '' : ' is-puesta'}">${esc(st.vacio)}</li>`;
    const filas = items.map(([v, t], i) =>
      `<li data-i="${i}" class="${i === st.marca ? 'is-marcada' : ''}${v === st.valor ? ' is-puesta' : ''}">${esc(t)}</li>`).join('');
    st.lista.innerHTML = todas + filas ||
      '<li class="combo__nada">Ninguna máquina coincide</li>';
    const m = st.lista.querySelector('.is-marcada');
    if (m) m.scrollIntoView({ block: 'nearest' });
  }

  function abrir(st) {
    st.abierto = true;
    st.lista.hidden = false;
    lista(st);
  }

  //  Al cerrar se devuelve la caja al texto de la maquina que de verdad esta
  //  puesta: si se quedara a medias ("adi"), se leeria como un filtro activo
  //  que no lo es.
  function cerrar(st) {
    st.abierto = false;
    st.lista.hidden = true;
    st.query = '';
    st.input.value = textoDe(st, st.valor);
  }

  function elegir(st, valor) {
    st.valor = valor;
    cerrar(st);
    st.raiz.classList.toggle('is-puesto', !!valor);
    st.onElegir(valor);
  }

  //  Lo que hay marcado ahora: -1 es "Todas las maquinas", que solo esta en
  //  la lista cuando no se ha escrito nada.
  function elegirMarcada(st) {
    if (st.marca === -1) return elegir(st, '');
    const o = st.vistas[st.marca];
    if (o) elegir(st, o[0]);
  }

  function mover(st, paso) {
    const min = st.query ? 0 : -1;
    const max = st.vistas.length - 1;
    if (max < 0) return;
    st.marca = Math.max(min, Math.min(max, st.marca + paso));
    lista(st);
  }

  function crear(id, raiz) {
    raiz.classList.add('combo');
    raiz.innerHTML =
      '<input class="combo__input" type="text" autocomplete="off" spellcheck="false">' +
      '<ul class="combo__lista" hidden></ul>';

    const st = {
      raiz, id,
      input: raiz.querySelector('.combo__input'),
      lista: raiz.querySelector('.combo__lista'),
      opciones: [], vistas: [], valor: '', vacio: '', query: '', marca: -1,
      abierto: false, onElegir: () => {},
    };

    //  Al entrar se abre con todo y con el texto seleccionado, para que
    //  escribir reemplace en vez de ir anadiendo a lo que ya habia.
    st.input.addEventListener('focus', () => {
      st.query = ''; st.marca = -1;
      st.input.select();
      abrir(st);
    });

    st.input.addEventListener('input', () => {
      st.query = st.input.value;
      st.marca = st.query ? 0 : -1;   //  escribiendo, se apunta al primer resultado
      abrir(st);
    });

    st.input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') { e.preventDefault(); mover(st, 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); mover(st, -1); }
      else if (e.key === 'Enter') { e.preventDefault(); elegirMarcada(st); st.input.blur(); }
      else if (e.key === 'Escape') { cerrar(st); st.input.blur(); }
    });

    //  mousedown y no click: el click llega DESPUES del blur, y para entonces
    //  la lista ya estaria cerrada y la fila pulsada ya no existiria.
    st.lista.addEventListener('mousedown', e => {
      e.preventDefault();
      const li = e.target.closest('li[data-i]');
      if (!li) return;
      const i = Number(li.dataset.i);
      elegir(st, i === -1 ? '' : (st.vistas[i] || [''])[0]);
    });

    st.input.addEventListener('blur', () => cerrar(st));

    estados.set(id, st);
    return st;
  }

  /*  Pinta (o crea la primera vez) el combo que vive en el hueco `id`.
      opciones: [[valor, texto], ...]   vacio: texto de "sin filtro"
      valor: el elegido ahora           onElegir: que hacer al elegir  */
  function pintar(id, cfg) {
    const raiz = document.getElementById(id);
    if (!raiz) return;
    const st = estados.get(id) || crear(id, raiz);

    st.opciones = cfg.opciones || [];
    st.valor = cfg.valor || '';
    st.vacio = cfg.vacio || '';
    st.onElegir = cfg.onElegir || (() => {});
    st.input.placeholder = st.vacio;
    st.raiz.classList.toggle('is-puesto', !!st.valor);

    //  Si el usuario esta escribiendo, no se le toca el texto: solo se
    //  reordena la lista con las opciones nuevas.
    if (document.activeElement === st.input) { if (st.abierto) lista(st); }
    else st.input.value = textoDe(st, st.valor);
  }

  return { pintar };
})();
