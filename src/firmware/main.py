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
AP_PASSWORD = "REPLACE_AP_PASSWORD"
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


HTML = b'<!doctype html><meta charset="utf-8"><title>Car reference</title><p>Read-only firmware reference. Use the current controller application.</p>'

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
