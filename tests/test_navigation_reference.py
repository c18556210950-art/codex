import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'/'pc'))
import copy,json,math,time
from point_navigation import PointNavigator, distance_bearing, guide_visual

def scenario():
    # Synthetic locations only. 0,0 -> a point 100 m north.
    return dict(server_time=100000,navigation=dict(revision=1,points=[{'lat':.00089932,'lng':0}],radius=10,active=False,calibration={'offset':0,'at':100000}),sensors=dict(latitude=0,longitude=0,accuracy=2,location_at=100000,camera_heading=0,heading_at=100000,heading_accuracy=10))

def run():
    checks=[]
    def check(name,fn):fn();checks.append(name)
    d,b=distance_bearing((0,0),(.00089932,0));assert abs(d-100)<.1 and b==0
    ctx=scenario();nav=PointNavigator();s=nav.update(ctx,100000);assert s['ready'] and abs(s['heading_error'])<.1
    checks.append('north bearing and 100m distance')
    for field,value in [('accuracy',11),('location_at',90000),('heading_at',97000),('heading_accuracy',-1),('heading_accuracy',40),('camera_heading',None)]:
        c=copy.deepcopy(ctx);c['sensors'][field]=value;assert not PointNavigator().update(c,100000)['ready'],field
    checks.append('reject poor/stale GPS, stale heading and inaccurate compass')
    c=copy.deepcopy(ctx);c['navigation']['gps_test']=True;c['sensors'].update(accuracy=80,location_at=70000)
    s=PointNavigator().update(c,100000);assert s['ready'] and s['location_timeout_ms']==30000 and s['location_age_ms']==30000
    c['sensors']['location_at']=69999;assert not PointNavigator().update(c,100000)['ready']
    c['sensors']['location_at']=100000;c['navigation']['gps_test']=False;assert not PointNavigator().update(c,100000)['ready']
    c['navigation']['gps_test']=True;c['sensors']['latitude']=None;assert not PointNavigator().update(c,100000)['ready']
    checks.append('GPS test ignores accuracy-only stop, accepts 30s fix, rejects expired/missing fixes; strict mode still checks accuracy')
    for accuracy in [60,-1,None]:
        c=copy.deepcopy(ctx);c['navigation']['gps_test']=True;c['sensors']['heading_accuracy']=accuracy
        s=PointNavigator().update(c,100000);assert s['ready'] and not s['heading_accuracy_ok']
        assert s['heading_accuracy_deg']==(60 if accuracy==60 else None)
        c['navigation']['gps_test']=False;assert not PointNavigator().update(c,100000)['ready']
    c['navigation']['gps_test']=True;c['sensors']['heading_at']=97000;assert not PointNavigator().update(c,100000)['ready']
    c['sensors'].update(heading_at=100000,camera_heading=None);assert not PointNavigator().update(c,100000)['ready']
    checks.append('test mode tolerates poor or unknown compass accuracy and reports it, while missing/stale heading and strict mode remain gated')
    c=copy.deepcopy(ctx);c['sensors']['camera_heading']=350;assert PointNavigator().update(c,100000)['heading_error']==10
    c['sensors']['camera_heading']=180;assert not PointNavigator().update(c,100000)['ready']
    checks.append('heading wrap and large turn stop')
    c=copy.deepcopy(ctx);c['navigation']['calibration']=None;assert not PointNavigator().update(c,100000)['ready']
    c['navigation']['calibration']={'offset':0,'at':1};assert not PointNavigator().update(c,1900000)['ready']
    checks.append('calibration required')
    c=copy.deepcopy(ctx);c['navigation']['calibration']=None;c['sensors']['camera_heading_absolute']=True
    s=PointNavigator().update(c,100000);assert s['ready'] and s['heading_source']=='phone_absolute'
    c['sensors']['camera_heading']=180;s=PointNavigator().update(c,100000);assert s['ready'] and s['rotate'] and s['command']=='turn_left'
    checks.append('absolute phone heading starts without calibration and requests automatic alignment')
    c['navigation'].update(handheld=True,blocked=True);c['sensors']['accuracy']=20
    assert PointNavigator().update(c,100000)['ready']
    c['navigation']['handheld']=False;assert not PointNavigator().update(c,100000)['ready']
    checks.append('handheld can preview coarse or blocked walking routes without relaxing physical driving')
    c=copy.deepcopy(ctx);c['navigation']['points']=[{'lat':0,'lng':0}];c['navigation']['active']=True;n=PointNavigator()
    for i in range(3):
        c['sensors']['location_at']=100000+i*1000;c['sensors']['heading_at']=100000+i*1000;s=n.update(c,100000+i*1000)
        if i<2:assert not s['arrived'] and not s['ready']
    assert s['arrived'] and not s['ready']
    checks.append('arrival requires three distinct fixes; holds stopped')
    n=PointNavigator();c['sensors']['location_at']=100000
    for i in range(10):s=n.update(c,100000)
    assert not s['arrived'];checks.append('repeated old fix cannot count as arrival')
    n=PointNavigator();c['sensors']['location_id']=12345
    for i in range(4):c['sensors']['location_at']=100000+i;s=n.update(c,100000+i)
    assert not s['arrived'];checks.append('network timestamp jitter cannot turn one GPS fix into three')
    c=copy.deepcopy(ctx);n=PointNavigator();n.update(c,100000);c['sensors'].update(latitude=.0005,location_at=101000,heading_at=101000);assert not n.update(c,101000)['ready']
    checks.append('GPS jump rejected')
    base=dict(clear=True,throttle=.65,steering=0,scores=[.99,.99,.99],reason='test')
    directive=dict(ready=True,steering=.85,reason='ok')
    assert guide_visual(base,directive)['steering']==.85
    base['clear']=False;assert guide_visual(base,directive)['throttle']==0
    base['clear']=True;base['scores']=[.99,.2,.2];assert guide_visual(base,directive)['throttle']==0
    directive['ready']=False;assert guide_visual(base,directive)['throttle']==0
    checks.append('route chooses visual corridor and never overrides visual/navigation stop')
    c=copy.deepcopy(ctx);c['navigation']['points']=[{'lat':0,'lng':0},{'lat':.00089932,'lng':0}];c['navigation']['active']=True;n=PointNavigator()
    for i in range(3):c['sensors'].update(location_at=100000+i*1000,heading_at=100000+i*1000);s=n.update(c,100000+i*1000)
    assert n.index==1 and not n.done
    c['navigation']['revision']=2;n.update(c,102000);assert n.index==0
    checks.append('multi-waypoint advancement and route revision reset')
    c=copy.deepcopy(ctx);c['navigation']['path']=[{'lat':0,'lng':0},{'lat':0,'lng':.00089932},{'lat':.00089932,'lng':.00089932}];c['navigation']['points']=[c['navigation']['path'][-1]];c['sensors']['camera_heading']=90
    n=PointNavigator();s=n.update(c,100000);assert s['ready'] and abs(s['bearing']-90)<1 and abs(s['heading_error'])<1
    checks.append('automatic route follows first road segment rather than straight line to goal')
    c['sensors'].update(latitude=.0003,longitude=0,location_at=101000,heading_at=101000);s=PointNavigator().update(c,101000);assert not s['ready'] and '偏离' in s['reason']
    checks.append('off-route position stops navigation')
    c=copy.deepcopy(ctx);c['navigation']['path']=[{'lat':0,'lng':0},{'lat':0,'lng':.00089932},{'lat':.00005,'lng':.00089932},{'lat':.00005,'lng':0}];c['navigation']['points']=[c['navigation']['path'][-1]];c['navigation']['active']=True;c['sensors']['camera_heading']=90;n=PointNavigator()
    for i in range(3):c['sensors'].update(location_at=100000+i*1000,heading_at=100000+i*1000);s=n.update(c,100000+i*1000)
    assert not s['arrived'] and s['ready'] and s['remaining_m']>150
    checks.append('nearby finish of U-shaped road does not shortcut route or trigger arrival')
    c['navigation']['blocked']=True;assert not PointNavigator().update(c,102000)['ready'];checks.append('known unsuitable route cannot drive')
    report={'checks':checks,'passed':len(checks),'physical_drive_tested':False,'uses_synthetic_positions':True}
    from pathlib import Path
    print(json.dumps(report))

if __name__=='__main__':run()
