const fs=require('fs'),vm=require('vm'),assert=require('assert');
let clock=1000,stops=0;
class FakeWss{constructor(){this.clients=new Set()}on(){}}
const ctx={require,WebSocketServer:FakeWss,WebSocket:{OPEN:1},setInterval(){},now:()=>clock,
 stop(){stops++},autoMode:'explore',console};
vm.createContext(ctx);vm.runInContext(require('./reference_sections').autonomy,ctx);
const run=code=>vm.runInContext(code,ctx);
run("autoEnabled=true;autoClient='operator';autoPause('blocked')");
assert(run('autoPaused&&autoEnabled'));assert.equal(stops,1);
// More than 30 seconds of uncertainty stays armed and stopped, so later
// clear imagery can resume without another click while the UI lease is live.
clock+=60000;run("autoPause('still blocked')");
assert(run('autoPaused&&autoEnabled'));assert.equal(stops,1);
run('visionResult={frame_id:1};autoRecover({})');
assert(run('!autoPaused&&autoEnabled'));
// Each later pause can also recover on its first valid observation.
run("autoPause('blocked again');visionResult={frame_id:5};autoRecover({})");
clock+=850;run("autoPause('wobble');visionResult={frame_id:6};autoRecover({})");
assert(run('!autoPaused&&autoEnabled'));
run("autoDisarm('manual stop')");assert(run('!autoEnabled&&!autoPaused'));
console.log('Recovery: long pause remains armed, one valid frame immediately resumes, manual stop disarms passed');
