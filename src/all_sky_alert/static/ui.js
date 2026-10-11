"use strict";
for(const table of document.querySelectorAll('table')){
  const headings=[...table.querySelectorAll('thead th')].map(node=>node.textContent);
  for(const row of table.querySelectorAll('tbody tr'))[...row.children].forEach((cell,index)=>{cell.dataset.label=headings[index]||'';});
}
const filters=document.getElementById('event-filters');
if(filters)for(const button of filters.querySelectorAll('[data-selection]'))button.addEventListener('click',()=>{
  for(const box of filters.querySelectorAll('[name=types]'))box.checked=button.dataset.selection==='all';
});
async function refreshHealth(){
  try{
    const response=await fetch('/api/health',{cache:'no-store',signal:AbortSignal.timeout(5000)});
    const h=(await response.json()).overall;
    if(!['green','yellow','red','gray'].includes(h.status))throw Error();
    document.getElementById('overall-health').className='stoplight '+h.status;
    document.getElementById('health-label').textContent='● '+h.status.toUpperCase()+' · '+h.label;
    document.getElementById('health-summary').textContent=h.summary;
    document.getElementById('health-time').textContent='Last evaluation: '+(h.evaluated_at?new Date(h.evaluated_at).toLocaleString():'waiting');
  }catch(_){
    document.getElementById('overall-health').className='stoplight red';
    document.getElementById('health-label').textContent='● RED · Health update unavailable';
    document.getElementById('health-summary').textContent='Check the service connection and detailed health.';
  }finally{setTimeout(refreshHealth,15000);}
}
setTimeout(refreshHealth,15000);
