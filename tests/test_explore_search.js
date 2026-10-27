const fs=require('fs'),vm=require('vm'),assert=require('assert');
let clock=1000,commands=[],tick;
class Wss{constructor(){this.clients=new Set()}on(){}}
const ctx={require,console,WebSocketServer:Wss,WebSocket:{OPEN:1},now:()=>clock,
 setInterval(fn,ms){if(ms===150)tick=fn},online:()=>true,
 cameraPublisher:{},lastCameraAt:1000,autoMode:'explore',active:false,owner:'',lastDriveAt:0,commandId:0,
 send(command,data){commands.push({command,data})},stop(){commands.push({command:'stop'})}};
vm.createContext(ctx);vm.runInContext(require('./reference_sections').autonomy,ctx);
const run=s=>vm.runInContext(s,ctx);
function fresh(clear=false){ctx.lastCameraAt=clock;run(`visionWorker={};visionReady=true;visionAt=${clock};autoLease=${clock};visionResult={frame_at:${clock},frame_id:${clock},clear:${clear},throttle:${clear?1:0},steering:0,scores:[.2,.1,.8],reason:'blocked'};`)}
run("autoEnabled=true;autoClient='test'");fresh();tick();assert.equal(commands.at(-1).data.y,0);assert.equal(commands.at(-1).data.x,-1);
clock+=650;fresh();tick();assert.equal(commands.at(-1).command,'stop');
clock+=300;fresh();tick();assert.equal(commands.at(-1).data.y,0);
clock=7000;fresh();tick();assert.equal(commands.at(-1).command,'stop');assert(run('autoPaused&&autoEnabled'));
clock+=150;fresh();tick();assert.equal(commands.at(-1).command,'stop');assert(!run('exploreSearching'),'exhausted search must not repeat forever');
fresh(true);tick();assert.equal(commands.at(-1).data.y,-1);assert.equal(run('exploreSearchAt'),null);
clock+=150;fresh();tick();assert.equal(commands.at(-1).data.y,0);
clock+=1000;run(`autoLease=${clock};visionAt=${clock}`);tick();assert.equal(commands.at(-1).command,'stop');
fresh();tick();run("autoDisarm('manual stop')");tick();assert.equal(commands.at(-1).command,'stop');assert(!run('autoEnabled||exploreSearching'));
run("autoEnabled=true;autoMode='navigate';visionResult={scores:[.8,.2,.1]}");assert.equal(run('exploreSearch()'),false);
run("autoMode='explore';autoSpeed=100");fresh(true);run('visionResult.steering=.85;visionResult.throttle=.2');tick();
assert.equal(commands.at(-1).data.y,-1);assert.equal(commands.at(-1).data.x,-.85*.55);assert.equal(commands.at(-1).data.speed,100);
console.log('Explore search: in-place turn, observation pauses, 6s limit, clear-frame resume, stale-frame stop, manual stop and navigation isolation passed');
