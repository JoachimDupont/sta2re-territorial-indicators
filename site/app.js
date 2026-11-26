const map=L.map('map').setView([50.45,3.9],8);
const osm=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>',maxZoom:19}).addTo(map);
L.control.layers({'OpenStreetMap':osm},null,{collapsed:true}).addTo(map);
const configs={
  icu:{title:'Îlots de chaleur urbains',description:'Population 2026 exposée selon la cartographie ICU AWAC disponible (mise à jour le 14/11/2024).',files:{communes:'icu_exposure_communes_2026.geojson',quartiers:'icu_exposure_quartiers_2026.geojson',sectors:'icu_exposure_sectors_2026.geojson'},defaultIndicator:'icu_population_pct',total:'icu_population_est',pct:'icu_population_pct',area:'icu_area_total_m2',highestLabel:'Exposée à un ICU très intense',areaLabel:'Surface ICU',method:'ICU : raster AWAC ICU_PRESENCE, couche 12, EPSG:31370, mise à jour le 14/11/2024. Classes : pas d’ICU, ICU très intense et ICU intense. La population exposée est estimée au prorata de la surface ICU dans chaque secteur.'},
  fire:{title:'Probabilité d’incendie',description:'Population et surfaces situées dans les zones boisées cartographiées selon quatre niveaux de probabilité d’incendie.',files:{communes:'fire_exposure_communes_2026.geojson',quartiers:'fire_exposure_quartiers_2026.geojson',sectors:'fire_exposure_sectors_2026.geojson'},defaultIndicator:'fire_population_pct',total:'fire_population_est',pct:'fire_population_pct',area:'fire_area_total_m2',highestLabel:'Population dans la classe la plus élevée',areaLabel:'Surface en zone d’aléa',method:'Incendie : couche de probabilité des incendies des zones boisées en Wallonie, produite en 2024 par le LEMA (ULiège) pour l’AWAC, catalogue mis à jour le 24/04/2025. Les quatre classes de la légende officielle sont conservées. L’exposition de population est une estimation surfacique, pas une probabilité individuelle de sinistre.'},
  flood:{title:'Aléa d’inondation',description:'Population et surfaces exposées à l’aléa d’inondation par débordement de cours d’eau et par ruissellement.',files:{communes:'flood_exposure_communes_2026.geojson',quartiers:'flood_exposure_quartiers_2026.geojson',sectors:'flood_exposure_sectors_2026.geojson'},defaultIndicator:'flood_population_pct',total:'flood_population_est',pct:'flood_population_pct',area:'flood_area_total_m2',highestLabel:'Population en aléa élevé',areaLabel:'Surface en zone d’aléa',method:'Inondation : raster officiel SPW « Aléa d’inondation par débordement et ruissellement », résolution 2 m, mise à jour le 18/02/2021. Les niveaux très faible, faible, moyen et élevé sont distingués. Les classes combinées débordement + ruissellement sont comptées une seule fois dans le total.'},
  roadnoise:{title:'Bruit des axes routiers principaux',description:'Exposition aux niveaux sonores cartographiés le long des grands axes routiers wallons.',files:{communes:'noise_exposure_communes_2026.geojson',quartiers:'noise_exposure_quartiers_2026.geojson',sectors:'noise_exposure_sectors_2026.geojson'},defaultIndicator:'road_noise_lden_population_pct',total:'road_noise_lden_population_est',pct:'road_noise_lden_population_pct',area:'road_noise_lden_area_total_m2',highestField:'road_noise_lden_population_ge_55_est',highestLabel:'Population estimée à ≥55 dB Lden',areaLabel:'Surface concernée par le bruit',method:'Bruit routier : cartographie stratégique 2022 du SPW, axes supportant plus de 3 millions de véhicules/an. Données de référence observées en 2021, mise à jour du catalogue le 19/05/2025. Lden et Lnight sont analysés séparément; les seuils de synthèse sont ≥55 dB Lden et ≥50 dB Lnight.'},
  railnoise:{title:'Bruit des axes ferroviaires principaux',description:'Exposition aux niveaux sonores cartographiés le long des grands axes ferroviaires wallons — résultats disponibles pour Tournai uniquement.',files:{communes:'rail_noise_tournai_communes_2026.geojson',quartiers:'rail_noise_tournai_quartiers_2026.geojson',sectors:'rail_noise_tournai_sectors_2026.geojson'},defaultIndicator:'rail_noise_lden_population_pct',total:'rail_noise_lden_population_est',pct:'rail_noise_lden_population_pct',area:'rail_noise_lden_area_total_m2',highestField:'rail_noise_lden_population_ge_55_est',highestLabel:'Population estimée à ≥55 dB Lden',areaLabel:'Surface concernée par le bruit',method:'Bruit ferroviaire : cartographie stratégique 2022 du SPW, axes supportant plus de 30 000 passages de trains/an. Données de référence observées en 2021, mise à jour du catalogue le 19/05/2025. Lden et Lnight sont analysés séparément; les seuils de synthèse sont ≥55 dB Lden et ≥50 dB Lnight.'}
};
let layer=null,allFeatures=[],currentHazard='icu',kpiCache={};
const fmt=n=>Number.isFinite(Number(n))?new Intl.NumberFormat('fr-BE',{maximumFractionDigits:1}).format(Number(n)):'—';
const num=n=>new Intl.NumberFormat('fr-BE',{maximumFractionDigits:0}).format(Number(n)||0);
const palette=['#d9eee9','#a7d8ca','#65b7a2','#e6a45b','#c85c45'];
function color(v){if(v==null||!Number.isFinite(Number(v)))return '#cbd5d9';let n=Number(v);return palette[n<=10?0:n<=25?1:n<=50?2:n<=75?3:4]}
function propsName(p){return p.sector_name||p.tx_sector_descr_fr||p.quartier_name||(p.statistical_quarter_code?'Quartier '+p.statistical_quarter_code:null)||p.municipality_name||p.tx_munty_descr_fr||p.unit_id||'Unité territoriale'}
function fireClasses(p){return [1,2,3,4].map(i=>({i,label:p['fire_class_'+i+'_label']||('Classe '+i)}))}
function highestFireClass(p){const cs=fireClasses(p);return cs.find(c=>/très élevé|very high/i.test(c.label))||cs.find(c=>/(?:^|[\\s:])élevé|high/i.test(c.label))||cs[3]}
function setupIndicators(){
  const cfg=configs[currentHazard],p=allFeatures[0]?.properties||{},select=document.querySelector('#indicator');
  let options=[];
  if(currentHazard==='icu') options=[['icu_population_pct','Population estimée exposée (%)'],['icu_surface_pct','Surface ICU (%)'],['icu_population_very_intense_pct','Population exposée à un ICU très intense (%)']];
  if(currentHazard==='fire'){
    options=[['fire_population_pct','Population estimée exposée (%)'],['fire_area_pct','Surface en zone d’aléa (%)']];
    fireClasses(p).forEach(c=>options.push(['fire_population_class_'+c.i+'_pct','Population — '+c.label+' (%)']));
  }
  if(currentHazard==='flood'){
    options=[['flood_population_pct','Population estimée exposée (%)'],['flood_area_pct','Surface en zone d’aléa (%)'],['flood_population_high_pct','Population — aléa élevé (%)'],['flood_population_medium_pct','Population — aléa moyen (%)'],['flood_population_low_pct','Population — aléa faible (%)'],['flood_population_very_low_pct','Population — aléa très faible (%)']];
  }
  if(currentHazard==='roadnoise'||currentHazard==='railnoise'){
    const prefix=currentHazard==='roadnoise'?'road_noise':'rail_noise';
    options=[[prefix+'_lden_population_pct','Population exposée — Lden (%)'],[prefix+'_lden_area_pct','Surface concernée — Lden (%)'],[prefix+'_lden_population_ge_55_pct','Population exposée à ≥55 dB Lden (%)'],[prefix+'_lnight_population_pct','Population exposée — Lnight (%)'],[prefix+'_lnight_area_pct','Surface concernée — Lnight (%)'],[prefix+'_lnight_population_ge_50_pct','Population exposée à ≥50 dB Lnight (%)']];
  }
  select.innerHTML=options.map(o=>'<option value="'+o[0]+'">'+o[1]+'</option>').join('');
  select.value=cfg.defaultIndicator;
}
function detailRows(f){
  const p=f.properties||{},rows=[['Population totale',num(p.population_total)]];
  if(currentHazard==='icu'){
    rows.push(['Population estimée exposée',num(p.icu_population_est)],['Exposée à un ICU très intense',num(p.icu_population_very_intense_est)],['Part exposée à un ICU très intense',fmt(p.icu_population_very_intense_pct)+' %'],['Surface ICU',fmt(Number(p.icu_area_total_m2||0)/1e6)+' km²'],['Surface ICU très intense',fmt(Number(p.icu_area_very_intense_m2||0)/1e6)+' km²'],['Surface ICU intense',fmt(Number(p.icu_area_intense_m2||0)/1e6)+' km²'],['Couverture raster',fmt(p.raster_coverage_pct)+' %'],['Mise à jour cartographie',p.source_icu_updated||'14/11/2024']);
  } else if(currentHazard==='fire'){
    rows.push(['Population estimée dans les zones cartographiées',num(p.fire_population_est)],['Part de population estimée',fmt(p.fire_population_pct)+' %'],['Surface exposée',fmt(Number(p.fire_area_total_m2||0)/1e6)+' km²']);
    fireClasses(p).forEach(c=>rows.push([c.label+' — population',num(p['fire_population_class_'+c.i+'_est'])],[c.label+' — surface',fmt(Number(p['fire_area_class_'+c.i+'_m2']||0)/1e6)+' km²']));
    rows.push(['Source','LEMA / AWAC · 2024']);
  } else if(currentHazard==='flood'){
    rows.push(['Population estimée exposée',num(p.flood_population_est)],['Part de population estimée',fmt(p.flood_population_pct)+' %'],['Surface exposée',fmt(Number(p.flood_area_total_m2||0)/1e6)+' km²']);
    [['very_low','Très faible'],['low','Faible'],['medium','Moyen'],['high','Élevé']].forEach(([k,label])=>rows.push(['Aléa '+label+' — population',num(p['flood_population_'+k+'_est'])],['Aléa '+label+' — surface',fmt(Number(p['flood_area_'+k+'_m2']||0)/1e6)+' km²']));
    const typeArea=Number(p.flood_overflow_area_high_m2||0)+Number(p.flood_runoff_area_high_m2||0)+Number(p.flood_combined_area_high_m2||0);if(typeArea>0)rows.push(['Débordement — aléa élevé',fmt(Number(p.flood_overflow_area_high_m2||0)/1e6)+' km²'],['Ruissellement — aléa élevé',fmt(Number(p.flood_runoff_area_high_m2||0)/1e6)+' km²'],['Débordement + ruissellement — aléa élevé',fmt(Number(p.flood_combined_area_high_m2||0)/1e6)+' km²']);rows.push(['Couverture raster',fmt(p.flood_raster_coverage_pct)+' %'],['Mise à jour cartographie','18/02/2021']);
  } else {
    const prefix=currentHazard==='roadnoise'?'road_noise':'rail_noise';
    rows.push(['Population estimée — Lden',num(p[prefix+'_lden_population_est'])],['Part de population — Lden',fmt(p[prefix+'_lden_population_pct'])+' %'],['Surface concernée — Lden',fmt(Number(p[prefix+'_lden_area_total_m2']||0)/1e6)+' km²'],['Population à ≥55 dB Lden',num(p[prefix+'_lden_population_ge_55_est'])],['Surface à ≥55 dB Lden',fmt(Number(p[prefix+'_lden_area_ge_55_m2']||0)/1e6)+' km²'],['Population estimée — Lnight',num(p[prefix+'_lnight_population_est'])],['Part de population — Lnight',fmt(p[prefix+'_lnight_population_pct'])+' %'],['Surface concernée — Lnight',fmt(Number(p[prefix+'_lnight_area_total_m2']||0)/1e6)+' km²'],['Population à ≥50 dB Lnight',num(p[prefix+'_lnight_population_ge_50_est'])],['Surface à ≥50 dB Lnight',fmt(Number(p[prefix+'_lnight_area_ge_50_m2']||0)/1e6)+' km²']);
    for(const index of ['lden','lnight']){for(let i=1;i<=6;i++){const label=p[prefix+'_'+index+'_class_'+i+'_label'];if(label)rows.push([label+' — population',num(p[prefix+'_'+index+'_population_class_'+i+'_est'])],[label+' — surface',fmt(Number(p[prefix+'_'+index+'_area_class_'+i+'_m2']||0)/1e6)+' km²']);}}
    rows.push(['Période de référence','2021'],['Mise à jour du catalogue','19/05/2025']);
  }
  return rows;
}
function show(f){
  const p=f.properties||{},name=propsName(p),indicator=document.querySelector('#indicator').value,v=p[indicator];
  document.querySelector('#details').innerHTML='<h2>'+name+'</h2><div class="detail-value">'+fmt(v)+' %</div><p>Indicateur sélectionné</p>'+detailRows(f).map(x=>'<div class="detail-row"><span>'+x[0]+'</span><span>'+x[1]+'</span></div>').join('');
}
function style(f){let p=f.properties||{},v=p[document.querySelector('#indicator').value];return{color:'#fff',weight:1,fillColor:color(v),fillOpacity:.68}}
function draw(){
  if(layer)map.removeLayer(layer);
  let q=document.querySelector('#search').value.toLocaleLowerCase('fr');
  let features=allFeatures.filter(f=>!q||propsName(f.properties||{}).toLocaleLowerCase('fr').includes(q));
  layer=L.geoJSON({type:'FeatureCollection',features},{style,onEachFeature:(f,l)=>l.on('click',()=>show(f))}).addTo(map);
  if(features.length&&document.querySelector('#scale').value==='communes')map.fitBounds(layer.getBounds(),{padding:[15,15]});
}
async function updateKpis(){
  const cfg=configs[currentHazard];
  if(!kpiCache[currentHazard]){
    const r=await fetch('data/'+cfg.files.communes);
    if(!r.ok)throw Error('Données communales non publiées ('+r.status+')');
    kpiCache[currentHazard]=await r.json();
  }
  const features=kpiCache[currentHazard].features||[],p=features.map(f=>f.properties||{});
  document.querySelector('#commune-count').textContent=String(features.length);
  const total=p.reduce((s,x)=>s+Number(x.population_total||0),0);
  const exposed=p.reduce((s,x)=>s+Number(x[cfg.total]||0),0);
  const area=p.reduce((s,x)=>s+Number(x[cfg.area]||0),0);
  let highest=0;
  if(currentHazard==='icu') highest=p.reduce((s,x)=>s+Number(x.icu_population_very_intense_est||0),0);
  else if(currentHazard==='flood') highest=p.reduce((s,x)=>s+Number(x.flood_population_high_est||0),0);
  else if(currentHazard==='fire') highest=p.reduce((s,x)=>s+Number(x['fire_population_class_'+highestFireClass(x).i+'_est']||0),0);
  else highest=p.reduce((s,x)=>s+Number(x[cfg.highestField]||0),0);
  document.querySelector('#exposed').textContent=num(exposed);
  document.querySelector('#exposed-pct').textContent=fmt(total?exposed/total*100:0)+' %';
  document.querySelector('#very-intense').textContent=num(highest);
  document.querySelector('#icu-area').textContent=fmt(area/1e6)+' km²';
  document.querySelector('#exposed-label').textContent='Population estimée exposée';
  document.querySelector('#highest-label').textContent=cfg.highestLabel;
  document.querySelector('#area-label').textContent=cfg.areaLabel;
}
async function load(){
  const scale=document.querySelector('#scale').value,cfg=configs[currentHazard];
  document.querySelector('#data-status').textContent='Chargement…';
  document.querySelector('#page-title').textContent=cfg.title;
  document.querySelector('#page-description').textContent=cfg.description;
  document.querySelector('#method-description').textContent='Population : Statbel, situation au 01/01/2026. '+cfg.method+' Les estimations de population reposent sur une répartition uniforme au sein des secteurs statistiques.';
  try{
    const r=await fetch('data/'+cfg.files[scale]);
    if(!r.ok)throw Error('Données absentes ('+r.status+')');
    const gj=await r.json();allFeatures=gj.features||[];
    setupIndicators();draw();
    document.querySelector('#data-status').textContent=allFeatures.length+' unités · données chargées';
    await updateKpis();
  }catch(e){
    document.querySelector('#data-status').textContent='Données en attente de publication';
    document.querySelector('#details').innerHTML='<h2>Données non encore publiées</h2><p>'+e.message+'. Le traitement des données officielles doit se terminer avant la publication des indicateurs.</p>';
  }
}
document.querySelector('#hazard').addEventListener('change',e=>{currentHazard=e.target.value;kpiCache[currentHazard]=null;load()});
document.querySelector('#scale').addEventListener('change',load);
document.querySelector('#indicator').addEventListener('change',draw);
document.querySelector('#search').addEventListener('input',draw);
load();