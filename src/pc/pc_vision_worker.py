"""Latest-frame-only PC inference worker. Motor authority remains at the relay."""
import os
import base64
import io
import json
import pathlib
import threading
import time
import sys
import cv2
import numpy as np
import torch
import websocket
from PIL import Image
from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor
from vision_planner import plan_ground
from point_navigation import PointNavigator, guide_visual

ROOT = pathlib.Path(__file__).resolve().parent
MODEL = 'nvidia/segformer-b0-finetuned-ade-512-512'
cfg = json.loads(pathlib.Path(os.environ.get('CAR_CONFIG', str(ROOT.parents[1] / 'config.private.json'))).read_text())
torch.set_num_threads(4)
device = 'cuda' if torch.cuda.is_available() else 'cpu'
print('Loading SegFormer on', device, flush=True)
processor = SegformerImageProcessor.from_pretrained(MODEL)
model = SegformerForSemanticSegmentation.from_pretrained(MODEL, use_safetensors=True).to(device).eval()
ground_ids = [i for i, name in model.config.id2label.items() if name in ('floor', 'road', 'sidewalk', 'path')]
assert ground_ids
# Complete the first GPU warm-up before advertising model readiness.
with torch.inference_mode():
    model(**processor(images=Image.new('RGB',(512,512)), return_tensors='pt').to(device))
print('Model ready; ground classes:', ground_ids, flush=True)
latest = None
lock = threading.Lock()
signal = threading.Event()
nav_context = None


def main():
    global latest, nav_context
    while True:
        ws = None
        closed = threading.Event()
        try:
            url = cfg['publicOrigin'].replace('https://','wss://') + '/car/' + cfg['controlKey'] + '/vision'
            ws = websocket.create_connection(url, header={'X-Vision-Key':cfg['computeKey']}, origin=cfg['publicOrigin'], timeout=5, enable_multithread=True)
            ws.send(json.dumps({'type':'ready','ready':True,'model':MODEL,'device':device}))
            latest = None
            nav_context = None
            navigator=PointNavigator();nav_beat=0;nav_status={}
            def receive(sock=ws):
                global latest, nav_context
                try:
                    while not closed.is_set():
                        try:
                            raw=sock.recv()
                        except websocket.WebSocketTimeoutException:
                            continue
                        m=json.loads(raw)
                        if m.get('type')=='frame':
                            m['_received']=time.monotonic()
                            with lock: latest=m
                            signal.set()
                        elif m.get('type')=='navigation':
                            m['_received']=time.monotonic()
                            with lock: nav_context=m
                            signal.set()
                except Exception: closed.set(); signal.set()
            threading.Thread(target=receive, daemon=True).start()
            beat = 0
            while not closed.is_set():
                if time.monotonic()-beat>1:
                    ws.send(json.dumps({'type':'heartbeat'}));beat=time.monotonic()
                signal.wait(.15);signal.clear()
                with lock: context=nav_context
                if context and time.monotonic()-context['_received']<2:
                    nav_status=navigator.update(context,context['server_time']+(time.monotonic()-context['_received'])*1000)
                    if time.monotonic()-nav_beat>.3:
                        ws.send(json.dumps({'type':'nav_status','status':nav_status}));nav_beat=time.monotonic()
                with lock: frame=latest;latest=None
                if frame is None: continue
                if time.monotonic()-frame['_received']>.5: continue
                started=time.perf_counter()
                image=Image.open(io.BytesIO(base64.b64decode(frame['jpeg']))).convert('RGB')
                if image.width>1280 or image.height>1280: continue
                # Aspect-ratio-preserving map for the corridor geometry.
                image.thumbnail((640,480));rgb=np.asarray(image)
                with torch.inference_mode():
                    inputs=processor(images=image,return_tensors='pt').to(device)
                    logits=model(**inputs).logits
                    probs=torch.softmax(logits,dim=1)[:,ground_ids].sum(dim=1,keepdim=True)
                    prob=torch.nn.functional.interpolate(probs,size=rgb.shape[:2],mode='bilinear',align_corners=False)[0,0].cpu().numpy()
                result, overlay=plan_ground(prob,rgb)
                if context and nav_status:
                    directed=guide_visual(result,nav_status)
                    result['nav_drive']={k:directed[k] for k in ['clear','steering','throttle','reason']}
                    result['nav_drive']['revision']=nav_status['revision']
                    result['nav_drive']['index']=nav_status['index']
                ok,jpg=cv2.imencode('.jpg',cv2.cvtColor(overlay,cv2.COLOR_RGB2BGR),[cv2.IMWRITE_JPEG_QUALITY,65])
                if not ok: continue
                result.update(type='result',frame_id=frame['id'],inference_ms=round((time.perf_counter()-started)*1000),model=MODEL,
                              overlay=base64.b64encode(jpg).decode())
                if time.monotonic()-frame['_received']<1:
                    ws.send(json.dumps(result))
        except KeyboardInterrupt: return
        except Exception as exc: print('Reconnecting:',type(exc).__name__,str(exc)[:120],flush=True)
        finally:
            closed.set()
            if ws: ws.close()
        time.sleep(2)


if __name__=='__main__': main()
