/* Общее меню скринера: одно на всех страницах (главная, анализ, боты, алерты, методика).
   Подключается строкой <script src="/nav.js"></script>; если на странице есть #gnav —
   меню встаёт туда, иначе сверху страницы появляется своя полоса. */
(function(){
  if(window.__gnav || window.self!==window.top) return;   // во вкладках ботов (встроенные окна) меню не нужно
  window.__gnav=1;
  var theme=document.createElement('link');theme.rel='stylesheet';theme.href='/workspace.css?v=20261006-1';document.head.appendChild(theme);
  function workspace(){var script=document.createElement('script');script.src='/workspace.js?v=20261006-1';document.body.appendChild(script);}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',workspace,{once:true});else workspace();
  var G=[
    ['Торговля',[['Можно торговать','/#trade','trade'],['Монеты','/#coins','coins']]],
    ['Анализ',[['Формации','/#forms','forms'],['Плотности','/#dens','dens'],['Сигналы скринера','/screener.html'],['Структуры и уровни','/structures.html'],['Стаканы подробно','/densities.html']]],
    ['Боты',[['Все боты','/bots.html'],['Можно торговать','/bots.html#screener-can-trade'],['Краткосрок 5/15m','/bots.html#screener-structure-fast'],['Алерты','/bots.html#screener-alerts'],['Спот','/bots.html#paper-spot'],['Фьючерсы','/bots.html#paper-future'],['Лонг/шорт','/bots.html#paper-future-longshort'],
             ['По скринеру','/bots.html#screener-all'],['Скринер + тренд','/bots.html#screener-all-trend-tf'],['Управляемый','/bots.html#screener-managed'],['Статистика исполнения','/execution-stats.html']]],
    ['Алерты',[['Лента алертов','/alerts.html']]],
    ['Методика',[['Расчёт новых правил','/rule-evaluation.html'],['Стопы и все позиции','/position-review.html'],['Разбор и статус работ','/bot-review.html'],['Исследование и Kronos','/research.html'],['Сверка бумажного учёта','/paper-audit.html'],['Как устроен скринер','/index.html']]]
  ];
  var css='.gn{display:flex;align-items:center;gap:2px;flex-wrap:wrap;font:13px/1.35 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}'+
    '.gn-bar{position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:14px;padding:8px 16px;background:#161a21;border-bottom:1px solid #262c37}'+
    '.gn-bar .gn-home{color:#e6e9ef;font-weight:700;font-size:15px;text-decoration:none;margin-right:6px}'+
    '.gn-g{position:relative}'+
    '.gn-g>a,.gn-g>button{display:block;background:none;border:0;color:#8b93a3;font:inherit;padding:6px 10px;border-radius:6px;cursor:pointer;text-decoration:none;white-space:nowrap}'+
    '.gn-g>a:hover,.gn-g>button:hover,.gn-g.open>button{color:#e6e9ef;background:#1b2029}'+
    '.gn-g.on>a,.gn-g.on>button{color:#fff;background:#24304a}'+
    '.gn-dd{display:none;position:absolute;left:0;top:100%;min-width:200px;background:#161a21;border:1px solid #262c37;border-radius:8px;padding:4px;box-shadow:0 8px 24px rgba(0,0,0,.45);z-index:60}'+
    '.gn-g.open .gn-dd{display:block}'+
    '.gn-dd a{display:block;color:#c7cdd8;text-decoration:none;padding:7px 10px;border-radius:6px;white-space:nowrap}'+
    '.gn-dd a:hover{background:#1b2029;color:#fff}.gn-dd a.on{color:#fff;background:#24304a}'+
    '.gn-burger{display:none;background:none;border:1px solid #262c37;color:#e6e9ef;border-radius:6px;padding:4px 9px;font-size:16px;cursor:pointer}'+
    '@media (max-width:800px){.gn-burger{display:block}.gn{display:none;flex-direction:column;align-items:stretch;width:100%}.gn.open{display:flex}'+
    '.gn-dd{position:static;box-shadow:none;border:0;padding-left:12px}.gn-wrap{flex-wrap:wrap}}';
  var st=document.createElement('style'); st.textContent=css; document.head.appendChild(st);

  function here(){
    var p=location.pathname.replace(/\/+$/,'')||'/'; if(p==='/board.html') p='/';
    return {p:p, h:location.hash.slice(1)};
  }
  function isOn(href){
    var u=new URL(href,location.origin), c=here(), up=u.pathname==='/board.html'?'/':u.pathname;
    if(up!==c.p) return false;
    var uh=u.hash.slice(1);
    if(up==='/') return (uh||'coins')===(c.h||'coins');
    return !uh || uh===c.h;
  }
  var nav=document.createElement('div'); nav.className='gn';
  G.forEach(function(g){
    var w=document.createElement('div'); w.className='gn-g';
    var items=g[1];
    if(items.length===1){
      var a=document.createElement('a'); a.href=items[0][1]; a.textContent=g[0]; a.dataset.href=items[0][1]; w.appendChild(a);
    } else {
      var b=document.createElement('button'); b.type='button'; b.textContent=g[0]+' ▾'; w.appendChild(b);
      var dd=document.createElement('div'); dd.className='gn-dd';
      items.forEach(function(it){ var a=document.createElement('a'); a.href=it[1]; a.textContent=it[0]; a.dataset.href=it[1]; if(it[2]) a.dataset.tab=it[2]; dd.appendChild(a); });
      w.appendChild(dd);
      b.onclick=function(e){ e.stopPropagation(); var o=w.classList.contains('open');
        [].forEach.call(nav.querySelectorAll('.gn-g.open'),function(x){x.classList.remove('open');}); if(!o) w.classList.add('open'); };
    }
    nav.appendChild(w);
  });
  document.addEventListener('click',function(){ [].forEach.call(nav.querySelectorAll('.gn-g.open'),function(x){x.classList.remove('open');}); });
  function mark(){
    [].forEach.call(nav.querySelectorAll('.gn-g'),function(w){
      var any=false;
      [].forEach.call(w.querySelectorAll('a[data-href]'),function(a){ var on=isOn(a.dataset.href); a.classList.toggle('on',on); if(on) any=true; });
      w.classList.toggle('on',any);
    });
  }
  // переход по пункту той же страницы (вкладка главной, вкладка ботов) — без перезагрузки
  nav.addEventListener('click',function(e){
    var a=e.target.closest('a[data-href]'); if(!a) return;
    var u=new URL(a.dataset.href,location.origin), c=here(), up=u.pathname==='/board.html'?'/':u.pathname;
    if(up===c.p && u.hash){ e.preventDefault(); location.hash=u.hash; }
    [].forEach.call(nav.querySelectorAll('.gn-g.open'),function(x){x.classList.remove('open');});
    nav.classList.remove('open');
  });
  window.addEventListener('hashchange',mark);

  var burger=document.createElement('button'); burger.className='gn-burger'; burger.type='button'; burger.textContent='☰';
  burger.onclick=function(e){ e.stopPropagation(); nav.classList.toggle('open'); };
  var host=document.getElementById('gnav');
  if(host){ host.appendChild(burger); host.appendChild(nav); }
  else {
    var bar=document.createElement('div'); bar.className='gn-bar gn-wrap';
    var home=document.createElement('a'); home.className='gn-home'; home.href='/'; home.textContent='Крипто-скринер';
    bar.appendChild(home); bar.appendChild(burger); bar.appendChild(nav);
    document.body.insertBefore(bar,document.body.firstChild);
    // у страниц были свои строки ссылок — оставляем одну общую шапку
    [].forEach.call(document.querySelectorAll('header nav, nav.links, .nav, .links, .toplinks'),function(el){ if(!bar.contains(el)) el.style.display='none'; });
  }
  mark();
  var monitorBadge=document.createElement('a');monitorBadge.href='/bot-review.html';monitorBadge.style.cssText='margin-left:12px;font:12px system-ui;color:#94a3b8;text-decoration:none';monitorBadge.textContent='контроль ботов: проверка…';(host||bar).appendChild(monitorBadge);
  function monitorStatus(){fetch('/monitor-status.json',{cache:'no-store'}).then(function(r){if(!r.ok)throw Error();return r.json();}).then(function(x){var age=(Date.now()-x.updated_ms)/1000,busy=(x.busy_profiles||[]).length;monitorBadge.textContent=age>30?'⚠ контроль ботов: нет свежего статуса':busy?'⚠ контроль: '+busy+' профилей занято':'● контроль ботов: '+x.duration_seconds.toFixed(1)+'с / '+x.interval_seconds+'с';monitorBadge.style.color=age>30||busy||!x.ok?'#fbbf24':'#34d399';}).catch(function(){monitorBadge.textContent='контроль ботов: статус недоступен';});}monitorStatus();setInterval(monitorStatus,10000);

  // Монеты в таблицах — ссылки на график в скринере (#chart=SYM&tf=TF), в том числе
  // внутри вкладок ботов (встроенные окна той же страницы). Таблицы пересобираются — проходим раз в 3 с.
  var SYM=/^(?:(ЛОНГ|ШОРТ)\s*)?([A-Z0-9]{2,}USDT)$/;
  function linkify(doc){
    if(!doc || !doc.body) return;
    [].forEach.call(doc.querySelectorAll('td'),function(td){
      if(td.dataset.gnl || td.querySelector('a')) return;
      var m=SYM.exec((td.textContent||'').trim()); if(!m) return;
      var row=td.closest('tr'), tf=/\b(1m|5m|15m|1h|4h)\b/.exec(row?[].map.call(row.cells,function(c){return c.textContent.trim();}).join(' '):'');
      var a=doc.createElement('a'); a.href='/#chart='+m[2]+'&tf='+(tf?tf[1]:'15m'); a.target='_top'; a.textContent=m[2];
      a.style.cssText='color:#4c8dff;text-decoration:none;font-weight:600'; a.title='Открыть график в скринере';
      td.textContent=''; if(m[1]){ td.appendChild(doc.createTextNode(m[1]+' ')); } td.appendChild(a); td.dataset.gnl='1';
    });
    if(doc===document && location.pathname!=='/' && location.pathname!=='/board.html')
      [].forEach.call(doc.querySelectorAll('iframe'),function(f){ try{ linkify(f.contentDocument); }catch(e){} });
  }
  if(location.pathname!=='/' && location.pathname!=='/board.html'){ linkify(document); setInterval(function(){ linkify(document); },3000); }
})();
