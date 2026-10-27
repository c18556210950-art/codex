// Exercise search -> selection -> plan -> map path without any motor connection.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.style={};this.dataset={};this.value='';this.disabled=false;this.hidden=false;this.attributes={};this.textContent=''}
 append(...nodes){this.children.push(...nodes)}
 replaceChildren(...nodes){this.children=[...nodes]}
 setAttribute(k,v){this.attributes[k]=v}
}
const elements=new Map(),lines=[],calls=[];let bounds=null;
const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
element('navClickMode').value='destination';element('navRadius').value='10';element('placeKeyword').value='公园';element('placeCity').value='测试市';
const shape=()=>({addTo(){return this},bindTooltip(){return this},remove(){},setLatLngs(){return this},getBounds(){return this.path},on(){return this},setLatLng(){return this},setRadius(){return this}});
const map={setView(){return this},on(){},panTo(){},fitBounds(b){bounds=b}};
const place={id:'test-poi',name:'公园东门',city:'测试市',district:'测试区',address:'林荫路1号',point:{lat:32,lng:118},uses_entrance:true,distance_m:120};
const routePath=[{lat:32.001,lng:118},{lat:32.001,lng:118.001},{lat:32,lng:118.001},{lat:32,lng:118}];
const ctx={console,Math,Number,JSON,Date,L:{map:()=>map,layerGroup:()=>({...shape(),clearLayers(){}}),polyline:(p)=>{const s=shape();s.path=p;lines.push(s);return s},circleMarker:shape,circle:shape,tileLayer:shape,control:{scale:shape}},document:{getElementById:element,createElement:tag=>new Element(tag),querySelectorAll:()=>element('placeResults').children},CLOUD_BASE:'/test/',visionSocket:null,WebSocket:{OPEN:1},request:async(path,payload)=>{calls.push({path,payload});if(path.endsWith('/search'))return {items:[place],city:'测试市'};if(path.endsWith('/plan'))return {plan_id:'preview',route:{path:routePath,blocked:false,meta:{distance_m:300,start_gap_m:1,end_gap_m:1,instructions:[{text:'先向东，再向南'}]}}};throw Error('Unexpected call')},online:false};
ctx.setInterval=()=>{};ctx.client='test-browser';
vm.createContext(ctx);vm.runInContext(require('./reference_sections').navigationBrowser,ctx);
(async()=>{
 await element('placeSearchForm').onsubmit({preventDefault(){}});
 assert(element('placeResults').children.length===1);
 const choice=element('placeResults').children[0];assert(choice.children[1].textContent.includes('林荫路1号'));choice.onclick();
 assert(element('placeSelected').textContent.includes('公园东门')&&choice.attributes['aria-pressed']==='true');
 await element('amapPlan').onclick();
 assert(calls[1].payload.destination.lat===32&&calls[1].payload.destination.lng===118);
 assert(JSON.stringify(bounds)===JSON.stringify(routePath.map(p=>[p.lat,p.lng])));
 assert(element('amapImport').disabled===false&&element('amapState').textContent.includes('300 米'));
 assert(!calls.some(c=>c.path.includes('/drive')));
 ctx.sampleState={navigation:{revision:1,points:[],radius:20,gps_test:true},sensors:{latitude:32,longitude:118,heading_accuracy:60},server_time:100000};
 vm.runInContext('navigationRender(sampleState)',ctx);
 assert(element('navHeadingStats').textContent.includes('±60.0°')&&element('navHeadingStats').textContent.includes('精度不足仍可导航'));
 ctx.sampleState.sensors.heading_accuracy=-1;vm.runInContext('navigationRender(sampleState)',ctx);
 assert(element('navHeadingStats').textContent.includes('未提供有效精度'));
 ctx.sampleState.navigation.gps_test=false;vm.runInContext('navigationRender(sampleState)',ctx);
 assert(element('navHeadingStats').textContent.includes('严格模式'));
 console.log('Browser: place names and addresses, selection, correct destination, route geometry and map fit passed; no motor calls');
})().catch(e=>{console.error(e);process.exitCode=1});
