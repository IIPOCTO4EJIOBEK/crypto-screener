/* Layout helpers: no wheel interception, no orders, no quote modifications. */
(function(){
  'use strict';
  var board=document.getElementById('v-coins');
  function read(){try{return JSON.parse(localStorage.getItem('workspace-ui')||'{}');}catch(e){return {};}}
  var state=read();
  function save(){try{localStorage.setItem('workspace-ui',JSON.stringify(state));}catch(e){}}
  if(board){
    document.body.classList.add('board-workspace');
    var tools=document.createElement('div');tools.className='workspace-tools';
    tools.innerHTML='<strong id="workspace-title">Обзор рынка</strong><label>Колонки <select aria-label="Набор колонок"><option value="overview">Обзор</option><option value="flow">Потоки и деривативы</option><option value="all">Все показатели</option></select></label><button type="button" class="density">Компактно</button><button type="button" class="filters">Скрыть фильтры</button>';
    document.querySelector('header').after(tools);
    var select=tools.querySelector('select'),density=tools.querySelector('.density'),filters=tools.querySelector('.filters');
    var sets={overview:[1,2,3,4,5,6,7,17,18,19,20,21],flow:[1,2,5,7,9,11,12,13,14,15,16,17,19],all:null};
    var style=document.createElement('style');document.head.appendChild(style);
    function apply(){
      if(!Object.prototype.hasOwnProperty.call(sets,state.columns))state.columns='overview';
      select.value=state.columns;
      var visible=sets[state.columns],css='';
      for(var i=1;i<=21;i++)if(visible&&visible.indexOf(i)<0)css+='#v-coins th:nth-child('+i+'),#v-coins td:nth-child('+i+'){display:none}';
      style.textContent=css+'#v-coins table{min-width:'+(state.columns==='all'?1700:state.columns==='flow'?1250:1150)+'px}';
      document.body.classList.toggle('compact',!!state.compact);density.setAttribute('aria-pressed',String(!!state.compact));
      document.body.classList.toggle('filters-collapsed',!!state.filters);filters.setAttribute('aria-pressed',String(!!state.filters));filters.textContent=state.filters?'Показать фильтры':'Скрыть фильтры';
      save();window.dispatchEvent(new Event('resize'));
    }
    select.onchange=function(){state.columns=select.value;apply();};density.onclick=function(){state.compact=!state.compact;apply();};filters.onclick=function(){state.filters=!state.filters;apply();};
    function title(){var active=document.querySelector('.view.on');var names={'v-coins':'Обзор рынка','v-trade':'Торговые сетапы','v-forms':'Формации и структура','v-dens':'Ликвидность и плотности'};document.getElementById('workspace-title').textContent=names[active&&active.id]||'Обзор рынка';select.parentElement.hidden=!!active&&active.id!=='v-coins';}
    window.addEventListener('hashchange',function(){requestAnimationFrame(title);});title();apply();
    document.querySelectorAll('.view .wrap').forEach(function(wrap){
      wrap.tabIndex=0;wrap.setAttribute('aria-label','Таблица: прокрутка по вертикали и горизонтали');
      var caption=document.createElement('p');caption.className='scroll-caption';caption.textContent='Монета закреплена слева · прокрутка таблицы ↔ · нажмите строку, чтобы открыть график';
      var rail=document.createElement('div'),inner=document.createElement('div');rail.className='scroll-rail';rail.tabIndex=0;rail.setAttribute('aria-label','Горизонтальная прокрутка таблицы');rail.appendChild(inner);wrap.before(caption,rail);
      var busy=false;function sync(from,to){if(busy)return;busy=true;to.scrollLeft=from.scrollLeft;busy=false;}
      rail.addEventListener('scroll',function(){sync(rail,wrap);},{passive:true});wrap.addEventListener('scroll',function(){sync(wrap,rail);},{passive:true});
      function size(){inner.style.width=wrap.scrollWidth+'px';rail.hidden=wrap.scrollWidth<=wrap.clientWidth;caption.hidden=rail.hidden;rail.scrollLeft=wrap.scrollLeft;}
      if(window.ResizeObserver){var ro=new ResizeObserver(size);ro.observe(wrap);ro.observe(wrap.querySelector('table'));}window.addEventListener('resize',size);size();
    });
  }
  // A bot tab grows to its content: one page scrollbar instead of an 85vh nested iframe.
  document.querySelectorAll('iframe.bot:not([data-autosize])').forEach(function(frame){
    var observer;
    function attach(){try{
      if(observer)observer.disconnect();var doc=frame.contentDocument;if(!doc||!doc.body)return;
      if(!doc.getElementById('workspace-theme')){var link=doc.createElement('link');link.id='workspace-theme';link.rel='stylesheet';link.href='/workspace.css?v=20261006-1';doc.head.appendChild(link);}
      doc.documentElement.style.scrollbarGutter='auto';doc.body.style.margin='0';
      function size(){if(!frame.offsetWidth)return;var h=Math.ceil(Math.max(doc.body.scrollHeight,doc.body.getBoundingClientRect().height));if(Math.abs(frame.getBoundingClientRect().height-h)>2)frame.style.height=h+'px';}
      if(window.ResizeObserver){observer=new ResizeObserver(size);observer.observe(doc.body);}size();
    }catch(e){/* Cross-origin frames keep their native scroll. */}}
    frame.addEventListener('load',attach);attach();
    new MutationObserver(function(){requestAnimationFrame(attach);}).observe(frame.parentElement,{attributes:true,attributeFilter:['hidden']});
  });
})();
