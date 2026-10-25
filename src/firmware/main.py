"""ESP32-S3 serial, Wi-Fi and HTTP test. Install as main.py.
wifi.json on the board contains {"ssid": "...", "password": "..."}.
Motor outputs start disabled. Hold-to-drive control has a 700 ms command watchdog.
"""
import asyncio
import gc
import json
import network
import select
import socket
import sys
import time

AP_NAME = "ESP32S3-Test"
AP_PASSWORD = "S3test2468"
status = {"chip": "ESP32-S3", "wifi": "starting", "http_requests": 0}
sta = network.WLAN(network.WLAN.IF_STA)
ap = network.WLAN(network.WLAN.IF_AP)


def snapshot():
    result = status.copy()
    result.update(uptime_ms=time.ticks_ms(), free_heap=gc.mem_free(),
                  connected=sta.isconnected(), ap_clients=len(ap.status("stations")))
    return result


def internet_test():
    conn = None
    try:
        addr = socket.getaddrinfo("example.com", 80)[0][-1]
        status["dns"] = "OK"
        conn = socket.socket()
        conn.settimeout(5)
        conn.connect(addr)
        conn.sendall(b"GET / HTTP/1.0\r\nHost: example.com\r\nConnection: close\r\n\r\n")
        data = b""
        while len(data) < 8192:
            block = conn.recv(1024)
            if not block:
                break
            data += block
        status["internet_http"] = data.split(b"\r\n", 1)[0].decode()
        status["internet_content_ok"] = b"Example Domain" in data and b" 200 " in data.split(b"\r\n", 1)[0]
    except Exception as exc:
        status["internet_error"] = str(exc)
    finally:
        if conn:
            conn.close()


async def setup_wifi():
    sta.active(True)
    cfg = json.load(open("wifi.json"))
    profiles = cfg.get("networks",[cfg])
    found = sta.scan()
    status["scan_count"] = len(found)
    status["wifi"] = "connecting"
    success = False
    for profile in profiles:
        requested = profile.get("ssid", "")
        allowed = [requested.lower(),requested.replace("(-5g)", "").lower(),requested.replace("(-5g)","-5g").lower()]
        choices = [row for row in found if row[0].decode("utf-8","replace").lower() in allowed]
        choices.sort(key=lambda row:row[3],reverse=True)
        if not choices:
            continue
        chosen = choices[0][0].decode()
        if sta.isconnected() and status.get("ssid") == chosen:
            success = True
            break
        sta.disconnect()
        await asyncio.sleep_ms(200)
        sta.config(reconnects=2)
        sta.connect(chosen,profile["password"])
        for _ in range(60):
            if sta.isconnected():
                break
            await asyncio.sleep_ms(250)
        if sta.isconnected():
            status.update(ssid=chosen,scan_rssi=choices[0][3])
            success=True
            break
    if success:
        status.update(wifi="connected",ip=sta.ifconfig()[0],rssi=sta.status("rssi"))
    else:
        status.update(wifi="ssid_not_found_or_connect_failed",ip="")
    ap.active(True)
    ap.config(ssid=AP_NAME,security=network.AUTH_WPA2_PSK,key=AP_PASSWORD)
    status.update(ap_ssid=AP_NAME,ap_ip=ap.ifconfig()[0])
    print("WIFI_READY",json.dumps({"connected":sta.isconnected(),"ssid":status.get("ssid"),"ip":status.get("ip")}))


from machine import Pin, PWM

motor_stby = Pin(7, Pin.OUT, value=0)
motor_a1 = Pin(5, Pin.OUT, value=0)
motor_a2 = Pin(6, Pin.OUT, value=0)
motor_b1 = Pin(15, Pin.OUT, value=0)
motor_b2 = Pin(16, Pin.OUT, value=0)
motor_pa = PWM(Pin(4), freq=20000, duty_u16=0)
motor_pb = PWM(Pin(17), freq=20000, duty_u16=0)
motor_job = None
drive_active = False
drive_last_ms = 0
drive_owner = ""
drive_sequences = {}
status.update(motor_running=False, motor_phase="stopped", motor_test="",
              motor_error="", motor_mode="idle", a_percent=0, b_percent=0)


def disable_motors():
    motor_stby.value(0)
    motor_pa.duty_u16(0)
    motor_pb.duty_u16(0)
    for pin in (motor_a1, motor_a2, motor_b1, motor_b2):
        pin.value(0)
    status.update(a_percent=0, b_percent=0)


def apply_motors(a, b):
    # Remove drive before changing direction.
    disable_motors()
    for power, pwm, p1, p2 in ((a, motor_pa, motor_a1, motor_a2),
                              (b, motor_pb, motor_b1, motor_b2)):
        p1.value(1 if power > 0 else 0)
        p2.value(1 if power < 0 else 0)
        pwm.duty_u16(int(abs(power) * 65535 / 100))
    motor_stby.value(1 if a or b else 0)
    status.update(a_percent=a, b_percent=b, motor_running=bool(a or b))


def accept_sequence(data):
    client = data.get("client")
    seq = data.get("seq")
    if not isinstance(client, str) or not 1 <= len(client) <= 32:
        raise ValueError("Invalid client")
    if type(seq) is not int or not 0 < seq < 1000000000:
        raise ValueError("Invalid sequence")
    if seq <= drive_sequences.get(client, 0):
        raise ValueError("Stale command")
    if client not in drive_sequences and len(drive_sequences) >= 8:
        drive_sequences.clear()
    drive_sequences[client] = seq
    return client


def stop_motor_test(reason="stopped"):
    global drive_active, drive_owner
    drive_active = False
    drive_owner = ""
    disable_motors()
    if motor_job is not None:
        motor_job.cancel()
    status.update(motor_running=False, motor_phase=reason, motor_mode="idle")


def drive_command(data):
    global drive_active, drive_last_ms, drive_owner
    try:
        x, y, speed = data.get("x"), data.get("y"), data.get("speed")
        if type(x) not in (int, float) or type(y) not in (int, float) or not (-1 <= x <= 1 and -1 <= y <= 1):
            raise ValueError("Invalid direction")
        if type(speed) is not int or not 0 <= speed <= 100:
            raise ValueError("Invalid speed")
        if type(data.get("invert_a", False)) is not bool or type(data.get("invert_b", False)) is not bool:
            raise ValueError("Invalid motor setting")
        client = accept_sequence(data)
    except (ValueError, TypeError, AttributeError) as exc:
        return "400 Bad Request", {"error": str(exc)}
    if motor_job is not None:
        return "409 Conflict", {"error": "Test running; stop first"}
    if drive_active and drive_owner != client:
        return "409 Conflict", {"error": "Another controller is active"}
    a, b = y + x, y - x
    scale = max(abs(a), abs(b), 1)
    a, b = int(a * speed / scale), int(b * speed / scale)
    if data.get("invert_a", False):
        a = -a
    if data.get("invert_b", False):
        b = -b
    apply_motors(a, b)
    drive_active = bool(x or y)
    drive_owner = client if drive_active else ""
    drive_last_ms = time.ticks_ms()
    status.update(motor_phase="drive" if a or b else "stopped",
                  motor_mode="drive" if drive_active else "idle", motor_error="")
    return "200 OK", snapshot()


async def drive_watchdog():
    while True:
        if drive_active and time.ticks_diff(time.ticks_ms(), drive_last_ms) >= 700:
            stop_motor_test("timeout")
        await asyncio.sleep_ms(40)


async def run_motor_test(mode):
    global motor_job
    try:
        if mode == "stagger":
            status["motor_phase"] = "a"
            apply_motors(100, 0)
            await asyncio.sleep_ms(1000)
            status["motor_phase"] = "both"
            apply_motors(100, 100)
            await asyncio.sleep_ms(1000)
        else:
            status["motor_phase"] = mode
            apply_motors(100 if mode in ("a", "both") else 0,
                         100 if mode in ("b", "both") else 0)
            await asyncio.sleep_ms(1000)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        status["motor_error"] = str(exc)
    finally:
        disable_motors()
        status.update(motor_running=False, motor_phase="stopped", motor_mode="idle")
        motor_job = None


def start_motor_test(mode):
    global motor_job
    if mode not in ("a", "b", "both", "stagger"):
        return "400 Bad Request", {"error": "Unknown test"}
    if motor_job is not None or drive_active:
        return "409 Conflict", {"error": "Motors busy; stop first"}
    status.update(motor_running=True, motor_test=mode, motor_error="",
                  motor_phase="starting", motor_mode="test")
    try:
        motor_job = asyncio.create_task(run_motor_test(mode))
    except Exception:
        stop_motor_test()
        raise
    return "200 OK", snapshot()


HTML = '<!doctype html>\n<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<title>双电机遥控手柄</title>\n<style>\n*{box-sizing:border-box}body{font:16px/1.5 system-ui,sans-serif;background:#f2f5fa;color:#182940;margin:0;padding:22px;max-width:740px;margin-inline:auto}h1{font-size:27px;margin:8px 0}p{margin:8px 0}.sub{color:#54657d;font-size:14px}.panel{background:white;border-radius:18px;padding:20px;margin:18px 0}.top{display:flex;gap:12px;justify-content:space-between;align-items:center;flex-wrap:wrap}#phase{font-size:21px;font-weight:700}#connection{font-size:13px;color:#54657d}button{font:inherit;border:0;border-radius:12px;min-height:52px;padding:10px 15px;background:#e8eef7;color:#173b6b;font-weight:650;cursor:pointer}button:disabled{opacity:.4;cursor:default}button:focus-visible{outline:3px solid #ffb52d;outline-offset:2px}.pad{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;max-width:390px;margin:18px auto;touch-action:none;user-select:none;-webkit-user-select:none}.direction{min-height:78px;touch-action:none;-webkit-touch-callout:none}.direction span{display:block;font-size:25px;line-height:1.1}.direction small{font-size:12px}.direction.active{background:#1763ce;color:white}.stop{background:#b91c1c;color:white}#emergency{width:100%;min-height:58px;font-size:20px}.speed{display:flex;gap:14px;align-items:center;flex-wrap:wrap}.speed input{flex:1;min-width:150px;accent-color:#1763ce;height:32px}.speed output{font-weight:700;min-width:44px}.settings{display:flex;gap:22px;flex-wrap:wrap;margin:16px 0 0}.settings label{display:flex;align-items:center;gap:7px;min-height:34px}.settings input{width:19px;height:19px}.tests{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:16px}.message{min-height:26px;font-size:14px;overflow-wrap:anywhere}summary{cursor:pointer;font-weight:650}kbd{font:13px system-ui;background:#e8eef7;padding:2px 6px;border-radius:4px}@media(max-width:380px){body{padding:12px}.panel{padding:14px}.direction{min-height:68px}.pad{gap:7px}}\n</style>\n<div class="top"><h1>双电机遥控手柄</h1><span id="connection">正在连接</span></div>\n<p class="sub">按住行驶，松手停止。首次使用请将轮子或转轴悬空。</p>\n<section class="panel">\n<div class="top"><div id="phase" role="status" aria-live="polite">正在读取状态…</div><div id="power" class="sub">A 0% · B 0%</div></div>\n<div class="pad" aria-label="方向手柄">\n<button class="direction" data-x="-1" data-y="1" aria-label="向左前方行驶"><span>↖</span><small>左前</small></button>\n<button class="direction" data-x="0" data-y="1" aria-label="前进"><span>↑</span><small>前进</small></button>\n<button class="direction" data-x="1" data-y="1" aria-label="向右前方行驶"><span>↗</span><small>右前</small></button>\n<button class="direction" data-x="-1" data-y="0" aria-label="原地左转"><span>↶</span><small>左转</small></button>\n<button class="stop" id="centerStop" aria-label="停止">停止</button>\n<button class="direction" data-x="1" data-y="0" aria-label="原地右转"><span>↷</span><small>右转</small></button>\n<button class="direction" data-x="-1" data-y="-1" aria-label="向左后方行驶"><span>↙</span><small>左后</small></button>\n<button class="direction" data-x="0" data-y="-1" aria-label="后退"><span>↓</span><small>后退</small></button>\n<button class="direction" data-x="1" data-y="-1" aria-label="向右后方行驶"><span>↘</span><small>右后</small></button>\n</div>\n<div class="speed"><label for="speed">最高速度</label><input id="speed" type="range" min="50" max="100" step="5" value="80"><output id="speedValue" for="speed">80%</output></div>\n<p class="sub">键盘：<kbd>W A S D</kbd> 或方向键；<kbd>空格</kbd> 急停。低速无法起转时，可适当提高速度。</p>\n<button id="emergency" class="stop">急停 / 停止电机</button>\n<p id="message" class="message" role="status" aria-live="polite"></p>\n</section>\n<section class="panel">\n<div class="top"><strong>实体手柄</strong><label style="display:flex;gap:8px;align-items:center"><input id="controllerEnable" type="checkbox" checked style="width:19px;height:19px">启用实体手柄控制</label></div>\n<p id="controllerState" class="sub" role="status">正在检测手柄…</p>\n<select id="controllerSelect" aria-label="选择实体手柄" style="max-width:100%;width:100%;padding:9px;font:inherit;border:1px solid #ccd5e3;border-radius:8px"></select>\n<p id="controllerLive" class="sub"></p>\n<p id="controllerHint" class="sub">默认启用：<strong>RT 前进加速，LT 后退，左摇杆左右转向</strong>。松开油门停止，B 键急停；RT、LT 同时按下时停止。</p>\n<p id="localController" class="sub">电脑手柄读取受限时，打开 <a href="http://127.0.0.1:8765/">本机实体手柄控制页</a>（在连接手柄的电脑上使用）。</p>\n</section>\n\n<section class="panel">\n<label style="display:flex;align-items:center;gap:9px;font-weight:650"><input id="bankKeepalive" type="checkbox" style="width:19px;height:19px">充电宝保活微动</label>\n<p class="sub">静止约 3 秒后，以 40% 强度前后各微动 0.2 秒。会实际给电机通电，可能产生轻微位移；不保证每款充电宝都有效。</p>\n<p id="bankState" class="sub" role="status">保活已关闭</p>\n<p class="sub">操作手柄时暂停；急停、切出页面或断连时停止保活。满转向时内侧轮保留外侧约 60% 的指令速度。</p>\n</section>\n<details class="panel"><summary>电机方向设置与单项测试</summary>\n<div class="settings"><label><input id="invertA" type="checkbox">A 电机反向</label><label><input id="invertB" type="checkbox">B 电机反向</label></div>\n<p class="sub">前进时两侧方向不对，可调整对应电机反向；左右转向相反时检查 A、B 对应的左右轮。设置仅影响手柄。</p>\n<div class="tests"><button class="test" data-mode="a">测试 A · 1 秒</button><button class="test" data-mode="b">测试 B · 1 秒</button><button class="test" data-mode="both">两只同时转 · 1 秒</button><button class="test" data-mode="stagger">错开启动 · 共 2 秒</button></div>\n<p class="sub">松手、切换页面会发送停止指令；手柄指令中断约 0.7 秒，板端自动停止。电源灯明显变暗时停止测试。</p>\n</details>\n<script>\nconst client=Date.now().toString(36)+Math.random().toString(36).slice(2,8);\nlet keepaliveBusy=false,keepaliveEpoch=0,keepaliveAbort=null,idleSince=Date.now(),controllerFreshAt=0;\nlet seq=0,online=false,holding=false,vector={x:0,y:0},pointer=null,keys=new Set(),driveBusy=false,driveAbort=null,polling=false,testBusy=false,testSending=false;\nconst phase=document.getElementById(\'phase\'),message=document.getElementById(\'message\'),speed=document.getElementById(\'speed\');\nconst dirs=[...document.querySelectorAll(\'.direction\')],tests=[...document.querySelectorAll(\'.test\')];\nconst mapping={ArrowUp:\'up\',w:\'up\',W:\'up\',ArrowDown:\'down\',s:\'down\',S:\'down\',ArrowLeft:\'left\',a:\'left\',A:\'left\',ArrowRight:\'right\',d:\'right\',D:\'right\'};\nfunction updateButtons(){dirs.forEach(b=>b.disabled=!online||testBusy||testSending);tests.forEach(b=>b.disabled=!online||testBusy||holding||testSending||keepaliveBusy)}\nfunction show(d){online=true;testBusy=d.motor_mode===\'test\'&&d.motor_running;document.getElementById(\'connection\').textContent=\'已连接 · \'+(d.ip||d.ap_ip||\'\');document.getElementById(\'power\').textContent=\'A \'+(d.a_percent||0)+\'% · B \'+(d.b_percent||0)+\'%\';\nconst p={stopped:\'已停止\',timeout:\'指令中断，已自动停止\',starting:\'准备测试\',a:\'电机 A 测试中\',b:\'电机 B 测试中\',both:\'两只电机测试中\'};\nif(d.motor_mode===\'drive\'&&d.motor_running){phase.textContent=holding?\'手柄控制中\':\'电机运行中\'}else{phase.textContent=p[d.motor_phase]||\'已停止\'}if(d.motor_error)message.textContent=\'电机异常：\'+d.motor_error;updateButtons()}\nasync function request(path,data,timeout=1200,controller){const c=controller||new AbortController();const timer=setTimeout(()=>c.abort(),timeout);try{const r=await fetch(path,{method:data===undefined?\'GET\':\'POST\',headers:data===undefined?{}:{\'Content-Type\':\'application/json\'},body:data===undefined?undefined:JSON.stringify(data),cache:\'no-store\',signal:c.signal});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);return d}finally{clearTimeout(timer)}}\nfunction payload(){return {client,seq:++seq,x:vector.y?-vector.x*.25:-vector.x,y:vector.y,speed:Number(speed.value),invert_a:document.getElementById(\'invertA\').checked,invert_b:document.getElementById(\'invertB\').checked}}\nfunction clearInput(){holding=false;vector={x:0,y:0};pointer=null;keys.clear();dirs.forEach(b=>b.classList.remove(\'active\'));if(driveAbort)driveAbort.abort();updateButtons()}\nasync function stop(disarm=true){keepaliveEpoch++;idleSince=Date.now();if(keepaliveAbort)keepaliveAbort.abort();if(disarm){document.getElementById(\'bankKeepalive\').checked=false;document.getElementById(\'bankState\').textContent=\'保活已关闭\';controllerSwitch.checked=false;gamepadReady=false;}clearInput();gamepadOwns=false;const data={client,seq:++seq};try{show(await request(\'/api/motor/stop\',data,1000));message.textContent=\'已停止。\'}catch(e){online=false;updateButtons();message.textContent=\'停止指令未确认，请直接断开电机电源。\'}}\nasync function sendDrive(){if(!holding||driveBusy)return;driveBusy=true;const data=payload(),c=new AbortController();driveAbort=c;try{const d=await request(\'/api/drive\',data,500,c);if(holding&&data.seq===seq)show(d)}catch(e){if(holding){clearInput();online=false;message.textContent=\'控制连接中断，请确认电机停止。\';stop()}}finally{driveBusy=false;if(driveAbort===c)driveAbort=null}}\nfunction engage(x,y){if(!online||testBusy||testSending)return;vector={x,y};holding=!!(x||y);dirs.forEach(b=>b.classList.toggle(\'active\',Number(b.dataset.x)===x&&Number(b.dataset.y)===y));updateButtons();if(holding){message.textContent=\'松手即停\';sendDrive()}else stop()}\ndirs.forEach(b=>{b.addEventListener(\'pointerdown\',e=>{if(pointer!==null||holding)return;e.preventDefault();pointer=e.pointerId;b.setPointerCapture(e.pointerId);engage(Number(b.dataset.x),Number(b.dataset.y))});b.addEventListener(\'pointerup\',e=>{if(pointer===e.pointerId)stop()});b.addEventListener(\'pointercancel\',e=>{if(pointer===e.pointerId)stop()});b.addEventListener(\'lostpointercapture\',e=>{if(pointer===e.pointerId)stop()});b.addEventListener(\'contextmenu\',e=>e.preventDefault())});\nwindow.addEventListener(\'pointerup\',e=>{if(pointer===e.pointerId)stop()});\nfunction keyboardVector(){engage(Number(keys.has(\'right\'))-Number(keys.has(\'left\')),Number(keys.has(\'up\'))-Number(keys.has(\'down\')))}\nwindow.addEventListener(\'keydown\',e=>{if(e.code===\'Space\'){e.preventDefault();stop();return}if([\'INPUT\',\'TEXTAREA\',\'SELECT\'].includes(e.target.tagName))return;const key=mapping[e.key];if(!key||pointer!==null)return;e.preventDefault();if(!keys.has(key)){keys.add(key);keyboardVector()}});\nwindow.addEventListener(\'keyup\',e=>{const key=mapping[e.key];if(!key||!keys.has(key))return;e.preventDefault();keys.delete(key);keyboardVector()});\ndocument.getElementById(\'centerStop\').onclick=()=>stop();document.getElementById(\'emergency\').onclick=()=>stop();\nspeed.addEventListener(\'input\',()=>{document.getElementById(\'speedValue\').textContent=speed.value+\'%\'});\n[\'invertA\',\'invertB\'].forEach(id=>document.getElementById(id).addEventListener(\'change\',stop));\ntests.forEach(b=>b.onclick=async()=>{if(testSending||testBusy||holding||keepaliveBusy||!online)return;testSending=true;updateButtons();try{show(await request(\'/api/motor/\'+b.dataset.mode,{}));message.textContent=\'测试结束后自动停止。\'}catch(e){message.textContent=\'测试指令未确认：\'+e.message}finally{testSending=false;updateButtons();refresh()}});\nasync function refresh(){if(polling||driveBusy)return;polling=true;try{show(await request(\'/api/status\'))}catch(e){online=false;updateButtons();document.getElementById(\'connection\').textContent=\'连接中断\';if(holding)stop()}finally{polling=false}}\nfunction leave(){document.getElementById(\'bankKeepalive\').checked=false;keepaliveEpoch++;if(keepaliveAbort)keepaliveAbort.abort();controllerSwitch.checked=false;gamepadReady=false;gamepadOwns=false;clearInput();navigator.sendBeacon(\'/api/motor/stop\',JSON.stringify({client,seq:++seq}))}\nwindow.addEventListener(\'blur\',()=>{if(holding||testBusy||keepaliveBusy)stop();else{document.getElementById(\'bankKeepalive\').checked=false;keepaliveEpoch++}});document.addEventListener(\'visibilitychange\',()=>{if(document.hidden){leave()}else refresh()});window.addEventListener(\'pagehide\',leave);\nlet gamepadOwns=false,gamepadReady=false,padPolling=false,lastPadB=false;\nconst nativeController=[\'127.0.0.1\',\'localhost\'].includes(location.hostname);\nconst controllerSwitch=document.getElementById(\'controllerEnable\');\nconst padSelect=document.getElementById(\'controllerSelect\');\nfunction stickDeadzone(v){const d=.18;return Math.abs(v)<=d?0:Math.sign(v)*(Math.abs(v)-d)/(1-d)}\nfunction triggerValue(v){const d=.07;return v<=d?0:Math.min(1,(v-d)/(1-d))}\nfunction triggerDrive(p){const rt=triggerValue(Number(p.rt)||0),lt=triggerValue(Number(p.lt)||0);const y=rt&&lt?0:rt-lt;const steering=stickDeadzone(p.x);return {x:Math.round(steering*y*1000)/1000,y:Math.round(y*1000)/1000,neutral:!rt&&!lt}}\nfunction browserPads(){\n if(typeof navigator.getGamepads!==\'function\')throw Error(\'此页面无法读取手柄，请使用电脑本机控制页\');\n return [...navigator.getGamepads()].filter(Boolean).map(p=>({index:p.index,id:p.id,mapping:p.mapping,\n x:p.axes[0]||0,y:-(p.axes[1]||0),lt:p.buttons[6]?.value||0,rt:p.buttons[7]?.value||0,lb:!!p.buttons[4]?.pressed,b:!!p.buttons[1]?.pressed,\n up:!!p.buttons[12]?.pressed,down:!!p.buttons[13]?.pressed,left:!!p.buttons[14]?.pressed,right:!!p.buttons[15]?.pressed}));\n}\nfunction processPad(pads){\n const signature=pads.map(p=>p.index+\':\'+p.id).join(\'|\');\n if(padSelect.dataset.signature!==signature){const previous=padSelect.value;padSelect.replaceChildren();pads.forEach(p=>{const o=document.createElement(\'option\');o.value=String(p.index);o.textContent=p.id;padSelect.append(o)});if(pads.some(p=>String(p.index)===previous))padSelect.value=previous;padSelect.dataset.signature=signature;gamepadReady=false}\n const p=pads.find(p=>String(p.index)===padSelect.value);\n if(!p){controllerFreshAt=0;document.getElementById(\'controllerState\').textContent=\'未检测到手柄；请连接后按一下 A 键。\';document.getElementById(\'controllerLive\').textContent=\'\';if(gamepadOwns)stop();gamepadReady=false;return}\n controllerFreshAt=Date.now();\n document.getElementById(\'controllerState\').textContent=\'已读取：\'+p.id;\n document.getElementById(\'controllerLive\').textContent=\'方向 \'+p.x.toFixed(2)+\' · RT \'+Math.round((p.rt||0)*100)+\'% · LT \'+Math.round((p.lt||0)*100)+\'% · B \'+(p.b?\'按下\':\'松开\');\n if(p.b&&!lastPadB){stop();message.textContent=\'手柄 B 键急停，实体手柄控制已关闭。\'}lastPadB=p.b;\n if(!controllerSwitch.checked||document.hidden){if(gamepadOwns)stop();gamepadReady=false;return}\n const command=triggerDrive(p);\n if(!gamepadReady){if(command.neutral){gamepadReady=true;document.getElementById(\'controllerHint\').textContent=\'已就绪：RT 前进加速，LT 后退，左摇杆左右转向。\'}else{document.getElementById(\'controllerHint\').textContent=\'请先松开 RT 和 LT，再开始驾驶。\'}return}\n if(!gamepadReady||!online||testBusy||testSending||pointer!==null||keys.size)return;\n const x=command.x,y=command.y;\n if(!y){if(gamepadOwns)stop(false);return}\n if(!gamepadOwns||Math.abs(vector.x-x)>.025||Math.abs(vector.y-y)>.025){engage(x,y);gamepadOwns=true;message.textContent=y>0?\'RT 前进中：松开油门即停，B 键急停。\':\'LT 后退中：松开油门即停，B 键急停。\'}\n}\nasync function pollController(){\n if(padPolling||document.hidden)return;padPolling=true;\n try{let pads;if(nativeController){const d=await request(\'/api/gamepad\',undefined,500);pads=d.gamepads}else{pads=browserPads()}processPad(pads)}\n catch(e){controllerFreshAt=0;document.getElementById(\'controllerState\').textContent=e.message||\'手柄读取失败\';if(gamepadOwns||keepaliveBusy)stop();gamepadReady=false}\n finally{padPolling=false}\n}\ncontrollerSwitch.addEventListener(\'change\',()=>{if(controllerSwitch.checked)claimControl();gamepadReady=false;stop(false);message.textContent=controllerSwitch.checked?\'请先松开 RT 和 LT，再操作油门和方向。\':\'实体手柄控制已关闭。\'});\npadSelect.addEventListener(\'change\',()=>{gamepadReady=false;stop(false)});\ndocument.getElementById(\'localController\').hidden=nativeController;\npollController();setInterval(pollController,100);\n\n\nfunction keepaliveEligible(){return document.getElementById(\'bankKeepalive\').checked&&controllerSwitch.checked&&gamepadReady&&online&&!holding&&!testBusy&&!testSending&&!document.hidden&&Date.now()-controllerFreshAt<500}\nasync function keepalivePulse(y){const c=new AbortController();keepaliveAbort=c;try{const d=await request(\'/api/drive\',{client,seq:++seq,x:0,y,speed:100,invert_a:document.getElementById(\'invertA\').checked,invert_b:document.getElementById(\'invertB\').checked},450,c);if(!holding)show(d)}finally{if(keepaliveAbort===c)keepaliveAbort=null}}\nasync function keepaliveTick(){\n if(keepaliveBusy||Date.now()-idleSince<3000||!keepaliveEligible())return;\n keepaliveBusy=true;updateButtons();const epoch=++keepaliveEpoch;\n try{\n  document.getElementById(\'bankState\').textContent=\'正在进行保活微动\';\n  await keepalivePulse(.4);await new Promise(r=>setTimeout(r,200));\n  if(epoch!==keepaliveEpoch||!keepaliveEligible())return;\n  await keepalivePulse(-.4);await new Promise(r=>setTimeout(r,200));\n }catch(e){if(epoch===keepaliveEpoch&&!holding){await stop();message.textContent=\'保活指令未确认，已关闭保活并发送停止。\'}}\n finally{\n  if(epoch===keepaliveEpoch&&!holding)await stop(false);\n  keepaliveBusy=false;idleSince=Date.now();updateButtons();\n  document.getElementById(\'bankState\').textContent=document.getElementById(\'bankKeepalive\').checked?\'等待静止 3 秒后再次微动\':\'保活已关闭\';\n }\n}\ndocument.getElementById(\'bankKeepalive\').addEventListener(\'change\',()=>{if(document.getElementById(\'bankKeepalive\').checked){controllerSwitch.checked=true;gamepadReady=false;claimControl()}idleSince=Date.now();if(!document.getElementById(\'bankKeepalive\').checked)stop(false);document.getElementById(\'bankState\').textContent=document.getElementById(\'bankKeepalive\').checked?\'静止时定期微动；可能发生轻微位移\':\'保活已关闭\'});\nsetInterval(keepaliveTick,500);\n\nconst controlChannel=typeof BroadcastChannel===\'function\'?new BroadcastChannel(\'esp32-motor-controller\'):null;\nfunction claimControl(){if(controlChannel)controlChannel.postMessage({type:\'claim\',client})}\nif(controlChannel)controlChannel.onmessage=e=>{if(e.data?.type===\'claim\'&&e.data.client!==client){stop();document.getElementById(\'bankState\').textContent=\'另一个控制页已接管，本页控制与保活已关闭。\';message.textContent=\'要使用本页，请重新启用实体手柄控制。\'}};\nif(!document.hidden){claimControl()}else{controllerSwitch.checked=false;document.getElementById(\'bankKeepalive\').checked=false;gamepadReady=false}\n\nupdateButtons();refresh();setInterval(sendDrive,200);setInterval(refresh,900);\n</script></html>\n\n'

async def serve(reader, writer):
    try:
        req = await asyncio.wait_for(reader.readline(), 3)
        parts = req.split()
        path = parts[1].decode() if len(parts) > 1 else "/"
        header_bytes = len(req)
        content_length = 0
        while True:
            header = await asyncio.wait_for(reader.readline(), 3)
            header_bytes += len(header)
            if header_bytes > 8192:
                raise ValueError("HTTP headers too large")
            if header.lower().startswith(b"content-length:"):
                content_length = int(header.split(b":", 1)[1].strip())
            if header in (b"\r\n", b"\n", b""):
                break
        if not 0 <= content_length <= 512:
            raise ValueError("Request body too large")
        request_data = await asyncio.wait_for(reader.readexactly(content_length), 2) if content_length else b""
        status["http_requests"] += 1
        code = "200 OK"
        mime = "application/json"
        if path == "/api/drive" or path.startswith("/api/motor/"):
            if parts[0] != b"POST":
                code = "405 Method Not Allowed"
                body = b'{"error":"Use POST"}'
            else:
                try:
                    data = json.loads(request_data) if request_data else {}
                    if not isinstance(data, dict):
                        raise ValueError("Expected an object")
                    if path == "/api/drive":
                        code, result = drive_command(data)
                    else:
                        mode = path.rsplit("/", 1)[-1]
                        if mode == "stop":
                            if "client" in data:
                                accept_sequence(data)
                            stop_motor_test()
                            result = snapshot()
                        else:
                            code, result = start_motor_test(mode)
                    body = json.dumps(result).encode()
                except (ValueError, TypeError) as exc:
                    code = "400 Bad Request"
                    body = json.dumps({"error": str(exc)}).encode()
        elif path.startswith("/api/ping?token="):
            body = json.dumps({"reply": "PONG", "token": path.split("=", 1)[1][:80]}).encode()
        elif path == "/api/status":
            body = json.dumps(snapshot()).encode()
        elif path == "/":
            body = HTML.encode()
            mime = "text/html; charset=utf-8"
        else:
            code = "404 Not Found"
            body = b'{"error":"not found"}'
        header = "HTTP/1.0 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n" % (code, mime, len(body))
        writer.write(header.encode() + body)
        await writer.drain()
    except Exception as exc:
        print("HTTP_ERROR", str(exc))
    finally:
        writer.close()
        await writer.wait_closed()


async def serial_task():
    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)
    line = ""
    last = time.ticks_ms()
    while True:
        if poller.poll(0):
            c = sys.stdin.read(1)
            if c in ("\r", "\n"):
                if line == "PING":
                    print("PONG", time.ticks_ms())
                elif line == "STOP":
                    stop_motor_test()
                    print("MOTOR_STOPPED")
                elif line == "STATUS":
                    print("STATUS", json.dumps(snapshot()))
                line = ""
            elif len(line) < 128:
                line += c
            else:
                line = ""
        if time.ticks_diff(time.ticks_ms(), last) >= 5000:
            print("ALIVE", time.ticks_ms(), "WIFI", sta.isconnected())
            last = time.ticks_ms()
        await asyncio.sleep_ms(10)


import os
import ssl
import hashlib
import ubinascii

cloud_config = json.load(open("cloud.json"))
cloud_write_lock = asyncio.Lock()
cloud_server_anchor = None
cloud_ticks_anchor = 0
status.update(cloud_connected=False, cloud_error="")


def cloud_now_ms():
    if cloud_server_anchor is not None:
        return cloud_server_anchor + time.ticks_diff(time.ticks_ms(),cloud_ticks_anchor)
    shift = 946684800 if time.gmtime(0)[0] == 2000 else 0
    if hasattr(time, "time_ns"):
        return time.time_ns() // 1000000 + shift * 1000
    return int(time.time() * 1000) + shift * 1000


async def cloud_send(writer, data, opcode=1):
    if isinstance(data, dict):
        data = json.dumps(data).encode()
    elif isinstance(data, str):
        data = data.encode()
    length = len(data)
    if length > 4096:
        raise ValueError("Cloud frame too large")
    header = bytes((0x80 | opcode, 0x80 | length)) if length < 126 else bytes((0x80 | opcode, 0xFE, length >> 8, length & 255))
    mask = os.urandom(4)
    payload = bytearray(length)
    for i in range(length):
        payload[i] = data[i] ^ mask[i % 4]
    async with cloud_write_lock:
        writer.write(header + mask + payload)
        await asyncio.wait_for(writer.drain(), 2)


async def cloud_frame(reader):
    header = await reader.readexactly(2)
    if header[0] & 0x70 or not header[0] & 0x80:
        raise ValueError("Unsupported WebSocket frame")
    opcode = header[0] & 15
    length = header[1] & 127
    if length == 126:
        n = await reader.readexactly(2)
        length = (n[0] << 8) | n[1]
    elif length == 127:
        raise ValueError("Cloud frame too large")
    if length > 4096:
        raise ValueError("Cloud frame too large")
    mask = await reader.readexactly(4) if header[1] & 128 else None
    data = await reader.readexactly(length)
    if mask:
        data = bytes(v ^ mask[i % 4] for i,v in enumerate(data))
    return opcode, data


async def cloud_connect():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cadata=open("cloud_ca.der", "rb").read())
    host = cloud_config["host"]
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host,443,ssl=context),12)
    try:
        key = ubinascii.b2a_base64(os.urandom(16)).strip().decode()
        request = "GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (cloud_config["path"],host,key)
        writer.write(request.encode())
        await asyncio.wait_for(writer.drain(),8)
        first = await asyncio.wait_for(reader.readline(),8)
        if b" 101 " not in first:
            raise OSError("Cloud WebSocket upgrade failed")
        headers = {}
        for _ in range(40):
            line = await asyncio.wait_for(reader.readline(),3)
            if line == b"\r\n":
                break
            k,v = line.split(b":",1)
            headers[k.lower()] = v.strip()
        expected = ubinascii.b2a_base64(hashlib.sha1((key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).strip()
        if headers.get(b"sec-websocket-accept") != expected:
            raise ValueError("Cloud handshake invalid")
        return reader, writer
    except Exception:
        writer.close()
        await writer.wait_closed()
        raise


async def cloud_status_sender(writer):
    count = 0
    while True:
        await cloud_send(writer,{"type":"status","status":snapshot()})
        count += 1
        if count % 2 == 0:
            await cloud_send(writer,{"type":"ping"})
        await asyncio.sleep_ms(500)


async def cloud_worker():
    global cloud_server_anchor, cloud_ticks_anchor
    while True:
        writer = None
        sender = None
        try:
            if not sta.isconnected():
                await asyncio.sleep_ms(1000)
                continue
            stop_motor_test()
            if time.gmtime()[0] < 2026:
                import ntptime
                for host in ("ntp.aliyun.com", "pool.ntp.org"):
                    try:
                        ntptime.host = host
                        ntptime.settime()
                        break
                    except Exception:
                        pass
                if time.gmtime()[0] < 2026:
                    raise OSError("Network clock sync required")
            reader, writer = await cloud_connect()
            status.update(cloud_connected=True,cloud_error="")
            print("CLOUD_CONNECTED")
            last_id = 0
            cloud_server_anchor = None
            sender = asyncio.create_task(cloud_status_sender(writer))
            while True:
                opcode, payload = await asyncio.wait_for(cloud_frame(reader),4)
                if opcode == 8:
                    raise OSError("Cloud closed")
                if opcode == 9:
                    await cloud_send(writer,payload,10)
                    continue
                if opcode != 1:
                    continue
                msg = json.loads(payload)
                if msg.get("type") in ("hello","pong") and isinstance(msg.get("server_time"),int):
                    cloud_server_anchor = msg["server_time"]
                    cloud_ticks_anchor = time.ticks_ms()
                    continue
                if msg.get("type") != "command":
                    continue
                ident = msg.get("id",0)
                if ident <= last_id:
                    continue
                last_id = ident
                age = cloud_now_ms() - msg.get("sent_at",0)
                status["cloud_command_age_ms"] = age
                if age > min(msg.get("ttl_ms",700),700) or age < -5000:
                    stop_motor_test("timeout")
                    continue
                command = msg.get("command")
                if command == "stop":
                    stop_motor_test()
                elif command == "drive":
                    code, _ = drive_command(msg.get("data",{}))
                    if code != "200 OK":
                        stop_motor_test()
                elif command == "test":
                    start_motor_test(msg.get("data",{}).get("mode"))
                await cloud_send(writer,{"type":"status","status":snapshot()})
        except Exception as exc:
            status["cloud_error"] = str(exc)
            print("CLOUD_RETRY",str(exc))
        finally:
            status["cloud_connected"] = False
            stop_motor_test()
            if sender:
                sender.cancel()
                try:
                    await sender
                except BaseException:
                    pass
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
        await asyncio.sleep_ms(2500)


async def wifi_reconnect_worker():
    while True:
        if not sta.isconnected():
            stop_motor_test()
            status["cloud_connected"] = False
            try:
                await setup_wifi()
            except Exception as exc:
                print("WIFI_RETRY",str(exc))
        await asyncio.sleep_ms(3000)


async def main():
    print("ESP32S3_TEST_BOOT")
    await setup_wifi()
    watchdog = asyncio.create_task(drive_watchdog())
    server = await asyncio.start_server(serve, "0.0.0.0", 80)
    cloud_task = asyncio.create_task(cloud_worker())
    wifi_task = asyncio.create_task(wifi_reconnect_worker())
    print("HTTP_READY", status.get("ip", status["ap_ip"]))
    await serial_task()


try:
    asyncio.run(main())
finally:
    disable_motors()
