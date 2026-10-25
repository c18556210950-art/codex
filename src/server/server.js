'use strict';
const http=require('http'),fs=require('fs'),path=require('path');
const {WebSocketServer,WebSocket}=require('ws');
const config=JSON.parse(fs.readFileSync(process.env.CAR_CONFIG||path.join(__dirname,'config.json'),'utf8'));
const base='/car/'+config.controlKey+'/';
const devicePath='/car-link/'+config.deviceKey;
const html=Buffer.from(fs.readFileSync(path.join(__dirname,'controller.html'),'utf8').replaceAll('__CONTROL_KEY__',config.controlKey));
let device=null,lastDeviceAt=0,deviceStatus={},commandId=0,lastDriveAt=0,owner='',active=false;
const seqs=new Map();
const now=()=>Date.now();
function online(){return device&&device.readyState===WebSocket.OPEN&&now()-lastDeviceAt<3500}
function snapshot(){const ok=!!online();return {...deviceStatus,cloud_online:ok,connected:ok,
 motor_running:ok&&active&&!!deviceStatus.motor_running,motor_mode:ok?deviceStatus.motor_mode:'idle',
 motor_phase:ok?deviceStatus.motor_phase:'offline',a_percent:ok?deviceStatus.a_percent||0:0,
 b_percent:ok?deviceStatus.b_percent||0:0,cloud_server_time:now()}}
function send(command,data={},ttl=700){
 if(!online())return false;
 device.send(JSON.stringify({type:'command',id:++commandId,command,data,sent_at:now(),ttl_ms:ttl}));return true;
}
function stop(reason='stopped'){active=false;owner='';lastDriveAt=0;send('stop',{},700);deviceStatus={...deviceStatus,motor_running:false,motor_phase:reason,motor_mode:'idle',a_percent:0,b_percent:0}}
function sequence(d){
 if(typeof d.client!=='string'||!d.client.length||d.client.length>32||!Number.isInteger(d.seq)||d.seq<=0||d.seq>=1e9)throw Error('Invalid controller sequence');
 if(d.seq<=(seqs.get(d.client)||0))throw Error('Stale command');
 if(seqs.size>=32&&!seqs.has(d.client))seqs.clear();seqs.set(d.client,d.seq);
}
function json(res,code,data){const body=Buffer.from(JSON.stringify(data));res.writeHead(code,{'Content-Type':'application/json; charset=utf-8','Content-Length':body.length,'Cache-Control':'no-store','Referrer-Policy':'no-referrer'});res.end(body)}
async function readBody(req){let n=0,blocks=[];for await(const b of req){n+=b.length;if(n>1024)throw Error('Request too large');blocks.push(b)}return n?JSON.parse(Buffer.concat(blocks).toString()):{}}
const server=http.createServer(async(req,res)=>{
 try{
  const u=new URL(req.url,'http://local');
  if(!u.pathname.startsWith(base))return json(res,404,{error:'Not found'});
  const p=u.pathname.slice(base.length);
  if(req.method==='GET'&&p===''){
   res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Content-Length':html.length,'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'});return res.end(html);
  }
  if(req.method==='GET'&&p==='api/navigation/provider')return json(res,200,{amap_enabled:!!config.amapKey});
  if(req.method==='GET'&&p==='api/status')return json(res,200,snapshot());
  if(req.method==='GET'&&p.startsWith('api/ping'))return json(res,200,{reply:'PONG',token:u.searchParams.get('token')});
  if(req.method==='GET'&&['assets/leaflet.js','assets/leaflet.css'].includes(p)){const file=path.join(__dirname,p);if(!fs.existsSync(file))return json(res,404,{error:'Missing map asset'});const data=fs.readFileSync(file);res.writeHead(200,{'Content-Type':p.endsWith('.js')?'application/javascript':'text/css','Cache-Control':'public, max-age=86400'});return res.end(data)}
  if(!p.startsWith('api/'))return json(res,404,{error:'Not found'});
  if(req.method!=='POST')return json(res,405,{error:'Use POST'});
  const origin=req.headers.origin;
  if(origin&&origin!==config.publicOrigin)return json(res,403,{error:'Origin mismatch'});
  const d=await readBody(req);
  if(!d||typeof d!=='object'||Array.isArray(d))return json(res,400,{error:'Expected an object'});
  if(p==='api/navigation/plan')return json(res,200,await amapPlan(d));
  if(p==='api/motor/stop'){
   autoDisarm('手动停止');
   if(d.client)sequence(d);stop();return json(res,200,snapshot());
  }
  if(!online())return json(res,503,{error:'车辆离线，请检查热点和供电'});
  if(p==='api/drive'){
   if(!Number.isFinite(d.x)||!Number.isFinite(d.y)||Math.abs(d.x)>1||Math.abs(d.y)>1||!Number.isInteger(d.speed)||d.speed<0||d.speed>100)throw Error('Invalid drive input');
   if((d.invert_a!==undefined&&typeof d.invert_a!=='boolean')||(d.invert_b!==undefined&&typeof d.invert_b!=='boolean'))throw Error('Invalid motor settings');
   sequence(d);autoDisarm('手动驾驶已接管');if(active&&owner&&owner!==d.client)return json(res,409,{error:'另一个控制页正在操作'});
   active=!!(d.x||d.y);owner=active?d.client:'';lastDriveAt=now();send('drive',d,700);
   return json(res,200,snapshot());
  }
  const mode=p.slice('api/motor/'.length);
  if(p.startsWith('api/motor/')&&['a','b','both','stagger'].includes(mode)){
   autoDisarm('电机测试已接管');if(active)return json(res,409,{error:'电机正在运行，请先停止'});
   active=true;owner='';lastDriveAt=now();send('test',{mode},700);return json(res,200,snapshot());
  }
  return json(res,404,{error:'Not found'});
 }catch(e){return json(res,400,{error:e.message||'Bad request'})}
});
server.requestTimeout=3000;server.headersTimeout=5000;
const wss=new WebSocketServer({noServer:true,perMessageDeflate:false,maxPayload:8192});
// In-memory JPEG live relay, sharing the existing HTTPS listener.
const cameraWss=new WebSocketServer({noServer:true,perMessageDeflate:false,maxPayload:256*1024});
let cameraPublisher=null,lastCameraFrame=null,lastCameraAt=0;
const cameraViewers=new Set();
function cameraInfo(){return {server_time:now(),type:'camera',online:!!cameraPublisher&&now()-lastCameraAt<3500,viewers:cameraViewers.size}}
function cameraBroadcastInfo(){const message=JSON.stringify(cameraInfo());for(const ws of cameraWss.clients)if(ws.readyState===WebSocket.OPEN)ws.send(message)}
cameraWss.on('connection',ws=>{
 cameraViewers.add(ws);ws.send(JSON.stringify(cameraInfo()));
 if(lastCameraFrame&&now()-lastCameraAt<2500)ws.send(lastCameraFrame,{binary:true});
 cameraBroadcastInfo();
 ws.on('message',(raw,binary)=>{
  if(binary){
   if(ws!==cameraPublisher||raw.length<4||raw[0]!==255||raw[1]!==216||raw[raw.length-2]!==255||raw[raw.length-1]!==217){ws.close(1008,'Invalid frame');return}
   visionFrame(raw);
   const wasOffline=now()-lastCameraAt>=3500;lastCameraFrame=Buffer.from(raw);lastCameraAt=now();
   if(wasOffline)cameraBroadcastInfo();
   for(const viewer of cameraViewers)if(viewer.readyState===WebSocket.OPEN&&viewer.bufferedAmount<128*1024)viewer.send(lastCameraFrame,{binary:true});
   return;
  }
  try{
   const m=JSON.parse(raw.toString());
   if(m.type==='frame_meta'&&ws===cameraPublisher){frameMeta={at:m.at};return}
   if(m.type==='sensors'&&ws===cameraPublisher){phoneSensors=navSensorMessage(m);return}
   if(m.type==='publish'){autoDisarm('相机来源已切换');visionFrames.clear();visionResult=null;frameMeta=null;phoneSensors=null;navigation={...navigation,revision:navigation.revision+1,calibration:null};navigationStatus=null;
    if(cameraPublisher&&cameraPublisher!==ws){const old=cameraPublisher;cameraPublisher=null;old.close(1000,'New camera selected')}
    cameraViewers.delete(ws);cameraPublisher=ws;lastCameraFrame=null;lastCameraAt=0;cameraBroadcastInfo();
   }else if(m.type==='unpublish'&&ws===cameraPublisher){cameraPublisher=null;lastCameraFrame=null;lastCameraAt=0;frameMeta=null;visionResult=null;autoDisarm('手机相机已停止');cameraViewers.add(ws);cameraBroadcastInfo()}
  }catch(e){ws.close(1008,'Invalid message')}
 });
 ws.on('error',()=>{});
 ws.on('close',()=>{cameraViewers.delete(ws);if(ws===cameraPublisher){cameraPublisher=null;lastCameraFrame=null;lastCameraAt=0;frameMeta=null;visionResult=null;autoDisarm('手机相机已停止')}cameraBroadcastInfo()});
});
setInterval(()=>{if(lastCameraFrame&&now()-lastCameraAt>3500){lastCameraFrame=null;cameraBroadcastInfo()}},1000);
setInterval(()=>{for(const ws of cameraWss.clients)if(ws.readyState===WebSocket.OPEN)ws.ping()},20000);

// The PC supplies perception; this relay alone grants a short driving lease.
const visionWss=new WebSocketServer({noServer:true,perMessageDeflate:false,maxPayload:400*1024});
let visionWorker=null,visionResult=null,visionAt=0,visionReady=false;
let autoEnabled=false,autoClient='',autoLease=0,autoSpeed=30,autoInvert={invert_a:false,invert_b:false};
let autoReason='未启动',frameCounter=0,frameMeta=null,phoneSensors=null;
const visionFrames=new Map();
function autoDisarm(reason){const was=autoEnabled;autoEnabled=false;autoClient='';autoLease=0;autoReason=reason;if(was)stop(reason)}
function visionSnapshot(){return {type:'vision_state',pc_online:!!visionWorker&&now()-visionAt<3500,model_ready:visionReady,
 enabled:autoEnabled,controller:autoClient,reason:autoReason,camera_online:!!cameraPublisher&&now()-lastCameraAt<1500,
 result_age_ms:visionResult?now()-visionResult.frame_at:null,result:visionResult&&now()-visionResult.frame_at<1800?visionResult:null,
 navigation:{...navigation,active:autoEnabled&&autoMode==='navigate',status:navigationStatus&&now()-navigationAt<2000?navigationStatus:null},
 sensors:phoneSensors&&now()-phoneSensors.received_at<10000?phoneSensors:null,server_time:now()}}
function visionBroadcast(){const msg=JSON.stringify(visionSnapshot());for(const ws of visionWss.clients)if(ws!==visionWorker&&ws.readyState===WebSocket.OPEN&&ws.bufferedAmount<400*1024)ws.send(msg)}
function visionFrame(raw){
 const meta=frameMeta;frameMeta=null;
 if(!meta||!Number.isFinite(meta.at)||now()-meta.at>800||meta.at>now()+200)return;
 const id=++frameCounter;visionFrames.set(id,meta.at);while(visionFrames.size>20)visionFrames.delete(visionFrames.keys().next().value);
 if(visionWorker&&visionWorker.readyState===WebSocket.OPEN&&visionWorker.bufferedAmount<300*1024)
  visionWorker.send(JSON.stringify({type:'frame',id,at:meta.at,jpeg:raw.toString('base64')}));
}
function validResult(m){return Number.isInteger(m.frame_id)&&visionFrames.has(m.frame_id)&&
 Number.isFinite(m.steering)&&Math.abs(m.steering)<=1&&Number.isFinite(m.throttle)&&m.throttle>=0&&m.throttle<=1&&
 typeof m.clear==='boolean'&&Array.isArray(m.scores)&&m.scores.length===3&&m.scores.every(x=>Number.isFinite(x)&&x>=0&&x<=1)&&
 typeof m.overlay==='string'&&m.overlay.length<220000&&typeof m.reason==='string'&&m.reason.length<160}
visionWss.on('connection',(ws,req)=>{
 const worker=req.headers['x-vision-key']===config.computeKey&&!!config.computeKey;
 if(worker){if(visionWorker){ws.close(1008,'PC already connected');return}visionWorker=ws;visionReady=false;visionAt=now();autoDisarm('PC 已重新连接，请重新启动');ws.send(JSON.stringify({type:'hello',server_time:now()}))}
 else ws.send(JSON.stringify(visionSnapshot()));
 ws.on('message',raw=>{try{
  const m=JSON.parse(raw.toString());
  if(worker){if(ws!==visionWorker)return;visionAt=now();navWorkerMessage(m);
   if(m.type==='ready'){visionReady=m.ready===true;if(!visionReady)autoDisarm('PC 模型未就绪')}
   if(m.type==='result'&&validResult(m)){
    const at=visionFrames.get(m.frame_id);if(now()-at>1200||at<(visionResult?.frame_at||0))return;
    visionResult={...m,frame_at:at};visionBroadcast();
   }
  }else if(navClientMessage(m)){
   ws.send(JSON.stringify({type:'nav_saved',action:m.type}));visionBroadcast();return;
  }else if(m.type==='arm'){
   if(typeof m.client!=='string'||m.client.length>32||!m.client.length)throw Error('Invalid client');
   if(autoEnabled&&autoClient!==m.client)throw Error('另一个页面正在自动驾驶');
   if(active&&!autoEnabled)throw Error('请先停止手动驾驶或电机测试');
   if(!online()||!visionReady||!visionWorker||now()-visionAt>1500||!cameraPublisher||!visionResult||now()-visionResult.frame_at>900||!visionResult.clear||visionResult.throttle<=0)throw Error('需要车辆、PC 和新鲜的可通行画面全部就绪');
   if(!Number.isInteger(m.speed)||m.speed<15||m.speed>45||typeof m.invert_a!=='boolean'||typeof m.invert_b!=='boolean')throw Error('Invalid settings');
   const mode=m.mode==='navigate'?'navigate':'explore';
   if(mode==='navigate'&&navigation.blocked)throw Error('路线包含不适合小车的路段，请重新选点');
   if(mode==='navigate'&&(!navigationStatus?.ready||now()-navigationAt>1500||navigationStatus.revision!==navigation.revision||!visionResult.nav_drive?.clear||visionResult.nav_drive?.revision!==navigation.revision))throw Error(navigationStatus?.reason||'导航尚未就绪');
   autoMode=mode;autoEnabled=true;autoClient=m.client;ws.autoClient=m.client;autoLease=now();autoSpeed=m.speed;autoInvert={invert_a:m.invert_a,invert_b:m.invert_b};autoReason=mode==='navigate'?'沿航点导航中':'低速视觉行驶中';navSendContext();
  }else if(m.type==='lease'&&autoEnabled&&m.client===autoClient&&ws.autoClient===autoClient)autoLease=now();
  else if(m.type==='stop')autoDisarm('已停止自动驾驶');
  visionBroadcast();
 }catch(e){ws.send(JSON.stringify({type:'vision_error',error:e.message}))}});
 ws.on('error',()=>{});
 ws.on('close',()=>{if(ws===visionWorker){visionWorker=null;visionReady=false;autoDisarm('PC 计算连接中断')}if(ws.autoClient&&ws.autoClient===autoClient)autoDisarm('驾驶页面已断开');visionBroadcast()});
});
setInterval(()=>{
 if(!autoEnabled)return;
 if(!online())return autoDisarm('车辆连接中断');
 if(now()-autoLease>1100)return autoDisarm('驾驶页面心跳中断');
 if(!visionWorker||now()-visionAt>1500||!visionReady)return autoDisarm('PC 计算中断');
 if(!cameraPublisher||now()-lastCameraAt>1000||!visionResult||now()-visionResult.frame_at>1200)return autoDisarm('视觉画面过期');
 let drive=visionResult;
 if(autoMode==='navigate'){
  if(!navigationStatus||now()-navigationAt>1500||navigationStatus.revision!==navigation.revision)return autoDisarm('导航计算已过期');
  if(['arrival_check','waypoint'].includes(navigationStatus.stage)){active=false;owner='';send('stop',{},500);return}
  if(!navigationStatus.ready)return autoDisarm(navigationStatus.reason);
  drive=visionResult.nav_drive;
  if(!drive||drive.revision!==navigation.revision||!Number.isFinite(drive.steering)||Math.abs(drive.steering)>1||!Number.isFinite(drive.throttle)||drive.throttle<0||drive.throttle>1)return autoDisarm('等待新路线的视觉计算');
  if(drive.index!==navigationStatus.index){active=false;owner='';send('stop',{},500);return}
 }
 if(!visionResult.clear||!drive.clear||drive.throttle<=0)return autoDisarm(drive.reason||'前方不确定或无通路');
 const y=Math.min(.8,drive.throttle),x=-drive.steering*y*.25;
 active=true;owner='vision';lastDriveAt=now();send('drive',{client:'vision',seq:++commandId,x,y,speed:autoSpeed,...autoInvert},500);
},150);
setInterval(()=>{if(visionWorker&&now()-visionAt>4000)visionWorker.terminate();visionBroadcast()},500);

let navigation={revision:0,points:[],radius:10,calibration:null},navigationStatus=null,navigationAt=0,autoMode='explore';
function navSensorMessage(m){
 const s={received_at:now()};for(const k of ['latitude','longitude','accuracy','heading','speed','camera_heading','heading_accuracy'])if(Number.isFinite(m[k]))s[k]=m[k];
 if(Number.isFinite(m.location_age_ms)&&m.location_age_ms>=0)s.location_at=now()-m.location_age_ms;
 if(Number.isFinite(m.location_at))s.location_id=m.location_at;
 if(Number.isFinite(m.heading_age_ms)&&m.heading_age_ms>=0)s.heading_at=now()-m.heading_age_ms;
 if(m.motion&&typeof m.motion==='object')s.motion=m.motion;
 return s;
}
function navContext(){return {type:'navigation',server_time:now(),navigation:{...navigation,active:autoEnabled&&autoMode==='navigate'},sensors:phoneSensors}}
function navSendContext(){if(visionWorker&&visionWorker.readyState===WebSocket.OPEN&&visionWorker.bufferedAmount<300*1024)visionWorker.send(JSON.stringify(navContext()))}
function navDistanceBearing(a,b){const rad=Math.PI/180,p=a.lat*rad,q=b.lat*rad,d=(b.lng-a.lng)*rad,v=Math.sin((q-p)/2)**2+Math.cos(p)*Math.cos(q)*Math.sin(d/2)**2;return {distance:6371008.8*2*Math.atan2(Math.sqrt(v),Math.sqrt(Math.max(0,1-v))),bearing:(Math.atan2(Math.sin(d)*Math.cos(q),Math.cos(p)*Math.sin(q)-Math.sin(p)*Math.cos(q)*Math.cos(d))/rad+360)%360}}
function navPoint(p){return p&&Number.isFinite(p.lat)&&Math.abs(p.lat)<=85&&Number.isFinite(p.lng)&&Math.abs(p.lng)<=180}
function navClientMessage(m){
 if(m.type==='nav_import'){amapImport(m);return true}
 if(m.type==='nav_set'){
  if(!Array.isArray(m.points)||m.points.length>20||!m.points.every(navPoint)||!Number.isInteger(m.radius)||m.radius<5||m.radius>30)throw Error('航点或到达半径无效');
  let total=0;for(let i=1;i<m.points.length;i++){const d=navDistanceBearing(m.points[i-1],m.points[i]).distance;if(d>1000)throw Error('单段路线不能超过1公里');if(d<m.radius*2)throw Error('相邻航点间距需大于到达半径的2倍');total+=d}if(total>2000)throw Error('测试版总路线不能超过2公里');
  autoDisarm('导航路线已更新，请重新启动');navigation={...navigation,revision:navigation.revision+1,points:m.points.map(p=>({lat:p.lat,lng:p.lng})),radius:m.radius,path:null,provider:'manual',meta:null,blocked:false,calibration_point:null};navigationStatus=null;navSendContext();return true;
 }
 if(m.type==='nav_calibrate'){
  if(!navigation.points.length)throw Error('请先保存航点');
  const s=phoneSensors;if(!s||!navPoint({lat:s.latitude,lng:s.longitude})||!Number.isFinite(s.location_at)||now()-s.location_at>4000||!Number.isFinite(s.accuracy)||s.accuracy>10)throw Error('需要新鲜且误差不超过10米的手机定位');
  if(!Number.isFinite(s.camera_heading)||!Number.isFinite(s.heading_at)||now()-s.heading_at>1500||!Number.isFinite(s.heading_accuracy)||s.heading_accuracy<0||s.heading_accuracy>25)throw Error('需要手机新鲜、精度良好的朝向数据；请刷新手机网页并授权');
  const direction=navDistanceBearing({lat:s.latitude,lng:s.longitude},navigation.calibration_point||navigation.points[0]);if(direction.distance<Math.max(20,s.accuracy*3))throw Error('校准参考点过近，请选至少20米且大于定位误差3倍的点');
  autoDisarm('车头方向已校准，请重新启动');navigation={...navigation,revision:navigation.revision+1,calibration:{offset:direction.bearing-s.camera_heading,at:now()}};navigationStatus=null;navSendContext();return true;
 }
 return false;
}
function navWorkerMessage(m){
 if(m.type!=='nav_status')return;
 const s=m.status;
 if(!s||s.revision!==navigation.revision||typeof s.ready!=='boolean'||typeof s.reason!=='string'||s.reason.length>180||!Number.isInteger(s.index)||s.index<0||s.index>navigation.points.length)return;
 navigationStatus=s;navigationAt=now();
 if(autoEnabled&&autoMode==='navigate'&&!s.ready&&!['arrival_check','waypoint'].includes(s.stage))autoDisarm(s.reason);
}
setInterval(navSendContext,400);

// Credentials stay in config.json; this service returns only route data.
const amapCoord=require('coordtransform'),amapPlans=new Map();
let amapPlanBusy=false,amapLastPlan=0;
function amapInverse(p){
 let guess=[p.lng,p.lat];
 for(let i=0;i<6;i++){const q=amapCoord.wgs84togcj02(...guess);guess=[guess[0]+p.lng-q[0],guess[1]+p.lat-q[1]]}
 return {lat:guess[1],lng:guess[0]};
}
async function amapGet(endpoint,params,signal){
 const u=new URL('https://restapi.amap.com'+endpoint);u.search=new URLSearchParams({key:config.amapKey,...params}).toString();
 let res;
 try{res=await fetch(u,{signal})}catch(e){throw Error('高德服务连接超时或不可用，请稍后重试')}
 if(!res.ok)throw Error('高德服务响应异常');
 const body=await res.text();if(body.length>1500000)throw Error('路线数据过大');
 let d;try{d=JSON.parse(body)}catch(e){throw Error('高德响应格式异常')}
 if(d.status!=='1'){
  const known={INVALID_USER_KEY:'Key无效',USERKEY_PLAT_NOMATCH:'Key类型不匹配，需要Web服务类型',INVALID_USER_SCODE:'授权校验失败',INVALID_USER_DOMAIN:'域名限制不匹配',INVALID_USER_IP:'服务器IP不在白名单',DAILY_QUERY_OVER_LIMIT:'当日接口额度已用完',USER_DAILY_QUERY_OVER_LIMIT:'当日接口额度已用完',INSUFFICIENT_PRIVILEGES:'接口权限不足'};
  throw Error('高德：'+(known[d.info]||'接口返回错误')+'（'+String(d.infocode||'unknown').replace(/[^0-9a-z]/gi,'').slice(0,16)+'）');
 }
 return d;
}
function amapBuildRoute(data,origin,destination,radius){
 const best=data.route?.paths?.[0];if(!best||!Array.isArray(best.steps))throw Error('未找到步行路线');
 const distance=Number(best.distance);if(!Number.isFinite(distance)||distance<=0||distance>2000)throw Error('测试版仅支持2公里以内的步行路线');
 let path=[],instructions=[],warnings=[];const blockedTypes=new Set([3,4,5,8,9,10,11,14,15,16,20,30]);let blocked=false;
 for(const s of best.steps){
  const t=Number(s.navi?.walk_type);const text=String(s.instruction||'').slice(0,180);
  if(blockedTypes.has(t)||/阶梯|楼梯|扶梯|电梯|轮渡|天桥/.test(text)){blocked=true;warnings.push('路线包含台阶、通道、天桥或特殊交通设施，小车不可直接执行')}
  if(t===1)warnings.push('路线包含人行横道，请人工确认通行环境');
  instructions.push({text,distance:Number(s.step_distance)||0,walk_type:Number.isFinite(t)?t:null});
  if(typeof s.polyline!=='string')throw Error('高德未返回路线形状');
  for(const pair of s.polyline.split(';')){
   const [lng,lat]=pair.split(',').map(Number);if(!navPoint({lat,lng}))throw Error('高德路线坐标无效');
   const point=amapInverse({lat,lng});if(!path.length||navDistanceBearing(path.at(-1),point).distance>.15)path.push(point);
  }
 }
 if(path.length<2||path.length>2000)throw Error('路线点数量异常或超过测试版限制');
 const startGap=navDistanceBearing(origin,path[0]).distance,endGap=navDistanceBearing(destination,path.at(-1)).distance;
 if(startGap>20){blocked=true;warnings.push('起点离高德可通行道路超过20米，请先手动驶至路线起点')}
 if(endGap>20){blocked=true;warnings.push('终点离高德可通行道路超过20米，请重新选择可到达的位置')}
 // Retain snapped endpoints; never add an invented off-road straight connector.
 let length=0;for(let i=1;i<path.length;i++)length+=navDistanceBearing(path[i-1],path[i]).distance;
 if(length>2300||length<1)throw Error('路线长度校验失败');
 let reference=path.at(-1),walked=0;
 for(let i=1;i<path.length;i++){walked+=navDistanceBearing(path[i-1],path[i]).distance;if(walked>=35&&navDistanceBearing(origin,path[i]).distance>=30){reference=path[i];break}}
 return {points:[path.at(-1)],path,radius,calibration_point:reference,provider:'amap',blocked,
  meta:{distance_m:Math.round(distance),geometry_m:Math.round(length),duration_s:Number(best.cost?.duration)||null,start_gap_m:Math.round(startGap),end_gap_m:Math.round(endGap),instructions,warnings:[...new Set(warnings)],source:'高德步行路线',coordinate_system:'WGS84（已从GCJ-02转换）'}};
}
async function amapPlan(d){
 if(!config.amapKey)throw Error('尚未配置高德Web服务Key');
 if(amapPlanBusy||now()-amapLastPlan<3000)throw Error('正在规划或请求过于频繁，请稍后重试');
 if(!navPoint(d.destination)||!Number.isInteger(d.radius)||d.radius<5||d.radius>30)throw Error('终点或到达半径无效');
 let origin=d.origin;
 if(!origin){const s=phoneSensors;if(!s||now()-s.received_at>10000||!Number.isFinite(s.location_at)||now()-s.location_at>15000)throw Error('请在车上手机刷新网页，开启相机并允许定位');origin={lat:s.latitude,lng:s.longitude}}
 if(!navPoint(origin)||navDistanceBearing(origin,d.destination).distance>2000)throw Error('起终点无效或相距超过2公里');
 amapPlanBusy=true;amapLastPlan=now();const signal=AbortSignal.timeout(8000);
 try{
  const text=p=>p.lng.toFixed(6)+','+p.lat.toFixed(6);
  const converted=await amapGet('/v3/assistant/coordinate/convert',{locations:text(origin)+'|'+text(d.destination),coordsys:'gps'},signal);
  const coords=String(converted.locations||'').split(';');if(coords.length!==2)throw Error('高德坐标转换失败');
  const data=await amapGet('/v5/direction/walking',{origin:coords[0],destination:coords[1],show_fields:'polyline,navi,cost'},signal);
  const route=amapBuildRoute(data,origin,d.destination,d.radius);
  const id=require('crypto').randomBytes(12).toString('hex');amapPlans.set(id,{route,at:now()});while(amapPlans.size>4)amapPlans.delete(amapPlans.keys().next().value);
  return {plan_id:id,route,origin,destination:d.destination};
 }finally{amapPlanBusy=false}
}
function amapImport(m){
 const entry=amapPlans.get(m.plan_id);if(!entry||now()-entry.at>10*60*1000)throw Error('路线预览已过期，请重新规划');
 autoDisarm('已载入高德路线，请校准并重新启动');navigation={...navigation,...entry.route,revision:navigation.revision+1,calibration:null};navigationStatus=null;navSendContext();
}

server.on('upgrade',(req,socket,head)=>{
 if(req.url===base+'vision'){if(req.headers.origin&&req.headers.origin!==config.publicOrigin){socket.destroy();return}visionWss.handleUpgrade(req,socket,head,ws=>visionWss.emit('connection',ws,req));return}
 if(req.url===base+'camera'){if(req.headers.origin&&req.headers.origin!==config.publicOrigin){socket.destroy();return}cameraWss.handleUpgrade(req,socket,head,ws=>cameraWss.emit('connection',ws,req));return}
 if(req.url!==devicePath){socket.destroy();return}
 wss.handleUpgrade(req,socket,head,ws=>wss.emit('connection',ws,req));
});
wss.on('connection',ws=>{
 if(device){const previous=device;device=null;previous.close(1000,'Replaced')}
 device=ws;lastDeviceAt=now();active=false;owner='';deviceStatus={};
 ws.send(JSON.stringify({type:'hello',server_time:now()}));
 send('stop');
 ws.on('message',raw=>{try{
  const m=JSON.parse(raw.toString());lastDeviceAt=now();
  if(m.type==='status'&&m.status&&typeof m.status==='object'){
   deviceStatus=m.status;
   if(now()-lastDriveAt>300&&!deviceStatus.motor_running&&deviceStatus.motor_mode!=='drive'&&deviceStatus.motor_mode!=='test'){active=false;owner=''}
  }
  if(m.type==='ping')ws.send(JSON.stringify({type:'pong',server_time:now()}));
 }catch(e){ws.close(1008,'Invalid message')}});
 ws.on('error',()=>{});
 ws.on('close',()=>{if(device===ws){device=null;active=false;owner='';deviceStatus={motor_running:false,motor_phase:'offline'}}});
});
setInterval(()=>{
 if(device&&now()-lastDeviceAt>4000){const old=device;device=null;active=false;owner='';old.terminate()}
 if(active&&owner&&now()-lastDriveAt>800)stop('timeout');
 // Bounded tests also get a cloud-side upper limit even if the browser vanishes.
 if(active&&!owner&&now()-lastDriveAt>2500)stop('timeout');
},50);
server.listen(Number(process.env.CAR_PORT||18765),'127.0.0.1',()=>console.log('Car remote service listening on loopback'));
process.on('SIGTERM',()=>{stop();if(device)device.close();server.close(()=>process.exit(0));setTimeout(()=>process.exit(0),800)});
