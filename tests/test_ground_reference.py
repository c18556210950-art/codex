import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'/'pc'))
import numpy as np
from vision_planner import plan_ground

def test_planner():
    rng=np.random.default_rng(1);im=rng.integers(40,220,(240,320,3),dtype=np.uint8)
    p=np.zeros((240,320),np.float32);p[120:]=.99
    r,_=plan_ground(p,im);assert r['clear'] and r['steering']==0
    p[170:230,110:210]=0
    r,_=plan_ground(p,im);assert not r['clear'] and r['throttle']==0
    r,_=plan_ground(np.full_like(p,.99),np.zeros_like(im));assert not r['clear']
    dim=rng.integers(0,22,(240,320,3),dtype=np.uint8)
    r,_=plan_ground(np.full_like(p,.99),dim);assert r['clear'], 'Dim light alone must not override confirmed ground'
    p[:]=0;p[:,156:164]=.99
    r,_=plan_ground(p,im);assert not r['clear']
    # A far obstacle does not yet affect near scores, but should prompt an
    # early turn into the already-confirmed clear side.
    p[:]=.99;p[75:132,55:188]=0
    r,_=plan_ground(p,im);assert r['clear'] and r['turn_early'] and r['steering']>0
    p[:]=.99;p[75:132,132:265]=0
    r,_=plan_ground(p,im);assert r['clear'] and r['turn_early'] and r['steering']<0
    p[180:235,105:215]=0
    r,_=plan_ground(p,im);assert not r['clear'], 'Far side cannot override near blockage'
    print('planner: open floor / central obstacle / darkness / narrow path passed')

if __name__=='__main__':test_planner()
