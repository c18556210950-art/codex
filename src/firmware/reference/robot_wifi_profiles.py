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
