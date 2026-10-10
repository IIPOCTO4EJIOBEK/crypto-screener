<script>
document.querySelectorAll('button[data-action]').forEach(function(button){button.onclick=async function(){
 var reason=prompt('Причина изменения / выхода (сохранится в журнале):');if(!reason||reason.trim().length<3)return;
 var command={op:button.dataset.action,key:button.dataset.key,reason:reason.trim()};
 if(command.op==='edit'){
  var stop=prompt('Новый стоп:',button.dataset.stop);if(stop===null)return;
  var target=prompt('Новый тейк:',button.dataset.target);if(target===null)return;
  command.stop=Number(stop);command.target=Number(target);
 }
 button.disabled=true;
 try{var response=await fetch('/api/position?bot='+encodeURIComponent(button.dataset.bot),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(command)});var data=await response.json();if(!response.ok)throw Error(data.error||'Ошибка');alert(data.note+'; смотрите журнал команд и время обновления.');}
 catch(error){alert(error.message);}finally{button.disabled=false;}
};});
</script>
