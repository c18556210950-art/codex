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
