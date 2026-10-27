'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let clock=100000,stops=0;
const ctx={require,console,config:{amapKey:'TEST_KEY_NEVER_PUBLISH'},now:()=>clock,setInterval:()=>{},autoEnabled:false,phoneSensors:null,visionWorker:null,WebSocket:{OPEN:1},autoDisarm:()=>{stops++},AbortSignal,URL,URLSearchParams};
vm.createContext(ctx);vm.runInContext(require('./reference_sections').navigation,ctx);
vm.runInContext(`
function publicFixture(type='0'){
 const raw=[{lat:32,lng:118},{lat:32,lng:118.0005},{lat:32.0005,lng:118.0005}];
 return {route:{paths:[{distance:'110',cost:{duration:'90'},steps:[{instruction:'沿道路前行后左转',step_distance:'110',navi:{walk_type:type},polyline:raw.map(p=>amapCoord.wgs84togcj02(p.lng,p.lat).join(',')).join(';')}]}]}};
}
function runSynchronous(){
 const origin={lat:32,lng:118},destination={lat:32.0005,lng:118.0005};
 const route=amapBuildRoute(publicFixture(),origin,destination,10);
 if(route.blocked||route.path.length!==3||navDistanceBearing(route.path[0],origin).distance>.02)throw Error('Coordinate or route parsing failed');
 if(route.commands.length!==2||route.commands[0].action!=='forward'||route.commands[1].action!=='left')throw Error('Route forward/left command extraction failed');
 if(!amapBuildRoute(publicFixture('20'),origin,destination,10).blocked)throw Error('Stairs were not blocked');
 if(!amapBuildRoute(publicFixture('1'),origin,destination,10).blocked)throw Error('Unvalidated public crossing was not blocked');
 // Public fixed API conversion sample: approximate inverse must stay within 2m.
 const sample=amapInverse({lng:116.472624240452,lat:39.996516927084});
 if(navDistanceBearing(sample,{lng:116.466485,lat:39.995197}).distance>2)throw Error('Official sample coordinate mismatch');
 return route;
}
runSynchronous();`,ctx);
ctx.fetch=async(url)=>({ok:true,text:async()=>JSON.stringify(url.pathname.includes('convert')?{status:'1',locations:'118.005,32.000;118.0055,32.0005'}:{status:'1',...vm.runInContext('publicFixture()',ctx)})});
(async()=>{
 const plan=await vm.runInContext("amapPlan({origin:{lat:32,lng:118},destination:{lat:32.0005,lng:118.0005},radius:10})",ctx);
 assert(plan.plan_id&&!JSON.stringify(plan).includes('TEST_KEY_NEVER_PUBLISH'));
 ctx.planId=plan.plan_id;vm.runInContext('amapImport({plan_id:planId})',ctx);
 assert(stops===1&&vm.runInContext("navigation.provider==='amap'&&navigation.path.length===3",ctx));
 vm.runInContext("navClientMessage({type:'nav_set',points:[],radius:10})",ctx);assert(vm.runInContext('navigation.path===null&&navigation.meta===null',ctx));
 clock+=610000;assert.throws(()=>vm.runInContext('amapImport({plan_id:planId})',ctx));
 ctx.fetch=async()=>({ok:true,text:async()=>JSON.stringify({status:'0',info:'INVALID_USER_KEY',infocode:'10001'})});
 await assert.rejects(vm.runInContext("amapGet('/v5/direction/walking',{},AbortSignal.timeout(1000))",ctx),e=>!e.message.includes('TEST_KEY_NEVER_PUBLISH')&&e.message.includes('Key'));
 ctx.fetch=async(url)=>({ok:true,text:async()=>JSON.stringify({status:'1',pois:[{id:'p1',name:'公园东门',address:'测试路1号',cityname:'测试市',adname:'测试区',location:'118.005,32.001',navi:{entr_location:'118.0051,32.001'}},{id:'invalid',name:'无坐标地点',location:''}]})});
 const search=await vm.runInContext("amapSearch({keyword:'公园',city:'测试市'})",ctx);
 assert(search.items.length===1&&search.items[0].uses_entrance&&search.items[0].city==='测试市'&&search.items[0].address==='测试路1号');
 assert(!JSON.stringify(search).includes('TEST_KEY_NEVER_PUBLISH'));
 assert.strictEqual(vm.runInContext("amapLocation('')",ctx),null);
 clock+=1500;
 await assert.rejects(vm.runInContext("amapSearch({keyword:'',city:''})",ctx));
 const searchCalls=[];ctx.phoneSensors={latitude:32,longitude:118,received_at:clock,location_at:clock};
 ctx.fetch=async(url)=>{searchCalls.push({path:url.pathname,region:url.searchParams.get('region')});return {ok:true,text:async()=>JSON.stringify(url.pathname.includes('regeo')?{status:'1',regeocode:{addressComponent:{citycode:'025',city:'南京市'}}}:{status:'1',pois:[]})}};
 const nearby=await vm.runInContext("amapSearch({keyword:'公园',city:''})",ctx);
 assert(nearby.city==='南京市'&&searchCalls[1].region==='025');
 clock+=1500;await vm.runInContext("amapSearch({keyword:'商店',city:''})",ctx);assert(searchCalls.length===3);
 console.log('AMap: route geometry, coordinate conversion, stairs block, preview/import, no-key exposure, manual reset, expiry and API errors passed');
})().catch(e=>{console.error(e);process.exitCode=1});
