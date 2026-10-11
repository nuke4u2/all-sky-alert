"use strict";
const form=document.getElementById('settings-form');
const all=document.getElementById('email-all');
const types=[...form.querySelectorAll('[name=email-type]')];
all.addEventListener('change',()=>{if(all.checked)for(const box of types)box.checked=true;});
for(const box of types)box.addEventListener('change',()=>{all.checked=false;});
form.addEventListener('submit',async event=>{
  event.preventDefault();const changes={};
  for(const input of form.querySelectorAll('[data-kind]'))changes[input.name]=input.dataset.kind==='bool'?input.checked:Number(input.value);
  changes['gmail.event_types']=all.checked?['*']:types.filter(box=>box.checked).map(box=>box.value);
  const output=document.getElementById('settings-result');const button=form.querySelector('[type=submit]');button.disabled=true;
  try{
    const response=await fetch('/settings',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':form.dataset.csrf},
      body:JSON.stringify({revision:form.dataset.revision,changes}),signal:AbortSignal.timeout(15000)});
    const data=await response.json();if(!response.ok)throw Error(data.detail||'Save failed');
    output.textContent=data.message+' Reload Settings before another edit.';
  }catch(error){output.textContent=error.message+' Reload Settings to check whether a save completed.';button.disabled=false;}
});
