"use strict";
(async()=>{
  const status=document.getElementById('map-status');
  const detail=document.getElementById('aircraft-detail');
  const list=document.getElementById('aircraft-list');
  const projections=document.getElementById('map-projections');
  let map,layer,observer,selected=null,current=new Map(),expiryTimers=[];
  const add=(parent,tag,text)=>{const node=document.createElement(tag);node.textContent=text;parent.appendChild(node);return node;};
  function show(row){
    selected=row.icao24;detail.replaceChildren();
    for(const button of list.querySelectorAll('button'))button.setAttribute('aria-pressed',String(button.dataset.icao24===selected));
    const summary=row.aircraft_summary;
    add(detail,'p',summary.airline).className='aircraft-airline';
    add(detail,'h3',summary.type_name).className='aircraft-type';
    route(detail,summary);
    add(detail,'p',summary.identity+(row.type_code?' · ICAO type '+row.type_code:'')).className='muted';
    const meta=row.enrichment;
    add(detail,'p',(row.altitude_m/0.3048).toLocaleString(undefined,{maximumFractionDigits:0})+' ft · '+row.altitude_kind);
    const g=row.geometry;
    add(detail,'p',g.range_km.toFixed(1)+' km / '+(g.range_km*0.621371).toFixed(1)+' mi slant range · '+g.altitude_deg.toFixed(1)+'° elevation');
    add(detail,'p',g.from_direction+(g.toward_direction?' → '+g.toward_direction:'; motion unknown'));
    add(detail,'p','Observed position '+row.position_age_seconds+' seconds old; '+row.position_kind+'. Ground track '+(row.track_deg===null?'unknown':row.track_deg+'°')+'.');
    add(detail,'p',summary.type_method+(summary.type_source_name?' · '+summary.type_source_name:'')).className='muted';
    if(meta)add(detail,'p','Metadata: '+meta.source_name+' · '+meta.age_seconds+' seconds old. '+(meta.updated_at?localTime(meta.updated_at):'Unknown'));
    if(summary.airline_source_name)add(detail,'p','Airline source: '+summary.airline_source_name+'.').className='muted';
    if(summary.route_source_name)add(detail,'p','Route source: '+summary.route_source_name+'. '+(summary.route_note?'Catalog endpoints; ':'Reported endpoints; ')+'the dashed line is not this route.');
    add(detail,'p','Dashed projection is a short estimate; turns/climbs and visual detection are unresolved.');
  }
  function route(parent,summary){
    if(summary.route_note)add(parent,'span',summary.route_note).className='muted';
    const endpoints=add(parent,'span','');endpoints.className='aircraft-route';
    for(const [label,value] of [['Origin',summary.origin],['Destination',summary.destination]]){
      const endpoint=add(endpoints,'span','');
      add(endpoint,'small',label);add(endpoint,'strong',value);
    }
  }
  function clear(){
    for(const timer of expiryTimers)clearTimeout(timer);expiryTimers=[];
    if(layer)layer.clearLayers();list.replaceChildren();current.clear();
    if(selected){detail.textContent='Selected aircraft no longer has a fresh position.';selected=null;}
  }
  try{
    const config=await (await fetch('/api/map-config',{signal:AbortSignal.timeout(5000)})).json();
    map=L.map('aircraft-map',{zoomControl:true,attributionControl:true});
    if(config.tiles_enabled){
      const credit=document.createElement('span');credit.textContent='© '+config.tile_attribution;
      const attribution=config.tile_url==='https://tile.openstreetmap.org/{z}/{x}/{y}.png'
        ? '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>' : credit.innerHTML;
      L.tileLayer(config.tile_url,{maxZoom:18,attribution}).addTo(map);
    }
    layer=L.layerGroup().addTo(map);
    document.getElementById('map-center').addEventListener('click',()=>{if(observer)map.setView(observer,10);});
    projections.addEventListener('change',()=>refresh());
  }catch(_){status.textContent='Map library or settings unavailable.';return;}
  let busy=false,timer;
  async function refresh(){
    if(busy)return;busy=true;clearTimeout(timer);
    try{
      const response=await fetch('/api/aircraft',{cache:'no-store',signal:AbortSignal.timeout(5000)});
      if(!response.ok)throw Error();const data=await response.json();
      const wasSelected=selected;clear();selected=wasSelected;
      if(!observer){observer=[data.observer.latitude,data.observer.longitude];map.setView(observer,10);
        L.circleMarker(observer,{radius:7,color:'#f08000',fillOpacity:1}).addTo(map).bindTooltip('Observer');}
      L.circle(observer,{radius:data.radius_km*1000,color:'#f08000',fill:false,weight:1}).addTo(layer);
      for(const row of data.aircraft){
        current.set(row.icao24,row);const items=[];
        const summary=row.aircraft_summary;
        const label=document.createElement('span');label.className='aircraft-tooltip';
        add(label,'strong',summary.airline);add(label,'strong',summary.type_name);add(label,'span',(summary.route_note?'Catalog (unconfirmed): ':'')+summary.origin+' → '+summary.destination);add(label,'small',summary.identity);
        const marker=L.circleMarker([row.latitude,row.longitude],{radius:9,color:'#4bcaff',fillOpacity:0.8,weight:2}).addTo(layer).bindTooltip(label);
        marker.on('click',()=>show(row));items.push(marker);
        const solid=L.polyline(row.history.map(p=>[p.latitude,p.longitude]),{color:'#4bcaff',weight:3}).addTo(layer);items.push(solid);
        // Heading tick uses only numeric geometry, never provider HTML.
        if(row.track_deg!==null){const r=row.track_deg*Math.PI/180;items.push(L.polyline([[row.latitude,row.longitude],
          [row.latitude+.015*Math.cos(r),row.longitude+.015*Math.sin(r)/Math.max(.1,Math.cos(row.latitude*Math.PI/180))]],{color:'#fff',weight:2}).addTo(layer));}
        if(projections.checked&&row.predicted.length)items.push(L.polyline(row.predicted,{color:'#ffc58a',weight:2,dashArray:'6 8'}).addTo(layer));
        const button=add(list,'button','');button.className='aircraft-choice';
        button.dataset.icao24=row.icao24;
        button.setAttribute('aria-pressed',String(selected===row.icao24));
        add(button,'span',summary.airline).className='aircraft-airline';
        add(button,'strong',summary.type_name).className='aircraft-type';
        route(button,summary);
        add(button,'span',summary.identity+' · '+(row.altitude_m/.3048).toLocaleString(undefined,{maximumFractionDigits:0})+' ft').className='muted';
        button.type='button';button.addEventListener('click',()=>{show(row);map.panTo([row.latitude,row.longitude]);});
        expiryTimers.push(setTimeout(()=>{for(const item of items)layer.removeLayer(item);button.remove();current.delete(row.icao24);
          if(selected===row.icao24){detail.textContent='Position expired; awaiting fresh data.';selected=null;}
        },Math.max(0,Date.parse(row.valid_until)-Date.parse(data.checked_at))));
      }
      if(selected&&current.has(selected))show(current.get(selected));
      else if(selected){detail.textContent='Selected aircraft no longer has a fresh position.';selected=null;}
      status.textContent=data.aircraft.length+' fresh aircraft · updates every 15 seconds. '+data.note;
      if(!data.aircraft.length)add(list,'p','No fresh positions in the current source area.');
    }catch(_){clear();status.textContent='Live map data unavailable; current aircraft cleared.';}
    finally{busy=false;timer=setTimeout(refresh,15000);}
  }
  refresh();
})();
