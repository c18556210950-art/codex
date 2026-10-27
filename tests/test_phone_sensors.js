const vm=require('vm'),fs=require('fs'),assert=require('assert');
let clock=100000,watchSuccess,watchError,currentSuccess,currentRequests=0,watchRequests=0;
const timers=[],sent=[],handlers={},nodes=new Map();
class TestDate extends Date{static now(){return clock}}
const ctx={Date:TestDate,Math,Number,Promise,WebSocket:{OPEN:1},visionSocket:{readyState:1,send(s){sent.push(JSON.parse(s))}},cameraStream:null,videoSocket:null,
 document:{hidden:false,getElementById(id){if(!nodes.has(id))nodes.set(id,{textContent:''});return nodes.get(id)},addEventListener(){}},
 window:{addEventListener(name,fn){handlers[name]=fn}},navigator:{geolocation:{watchPosition(ok,err){watchSuccess=ok;watchError=err;watchRequests++;return watchRequests},clearWatch(){},getCurrentPosition(ok){currentRequests++;currentSuccess=ok}}},
 setInterval(fn,ms){timers.push({fn,ms})}};
vm.createContext(ctx);const run=s=>vm.runInContext(s,ctx);
run(require('./reference_sections').phoneSensors);
(async()=>{
 await run('requestPhoneSensors()');assert(sent[0].type==='sensor_claim');assert.equal(watchRequests,1);
 run('phoneSensorClaimed=true');
 const fix=timestamp=>({timestamp,coords:{latitude:32,longitude:118,accuracy:5,speed:0,heading:null}});
 watchSuccess(fix(99000));assert(sent.some(s=>s.type==='phone_sensors'&&s.location_at===99000),'uploads must work without camera');
 clock+=5000;timers.find(t=>t.ms===4000).fn();assert.equal(currentRequests,1,'stale watch requires a fresh one-shot request');
 let m=sent.at(-1);assert.equal(m.location_at,99000);assert(m.location_age_ms>=6000,'heartbeat must not fabricate a fresh measurement');
 currentSuccess(fix(clock));clock+=400;run('phoneSend()');m=sent.at(-1);assert.equal(m.gps_fixes,2);
 watchSuccess(fix(99000));clock+=400;run('phoneSend()');assert.equal(sent.at(-1).location_at,105000,'old callback must not replace newer fix');
 let a=run('cameraCompass({alpha:0,beta:60,gamma:0,webkitCompassHeading:90})');assert(a.absolute&&Math.abs(a.heading-90)<1e-8);
 let b=run('cameraCompass({alpha:123,beta:60,gamma:0,webkitCompassHeading:90})');assert(b.absolute&&Math.abs(b.heading-90)<1e-8,'arbitrary Safari alpha must not alter absolute camera heading');
 a=run('cameraCompass({alpha:0,beta:0,gamma:0,webkitCompassHeading:90})');assert.equal(a.heading,null,'downward camera has no horizontal heading');
 watchError({code:1,message:'permission denied'});assert(run('!phoneGeoEnabled'));
 console.log('Phone: camera-free telemetry, watch plus fresh polling, cached timestamps preserved, out-of-order fixes rejected, compass absolute anchoring, permission denial passed');
})().catch(e=>{console.error(e);process.exitCode=1});
