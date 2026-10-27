"""WGS84 waypoint guidance, with uncertainty, freshness and visual gating."""
import math

def distance_bearing(a, b):
    p,q=map(math.radians,[a[0],b[0]]);dl=math.radians(b[1]-a[1]);dp=q-p
    v=math.sin(dp/2)**2+math.cos(p)*math.cos(q)*math.sin(dl/2)**2
    d=6371008.8*2*math.atan2(math.sqrt(v),math.sqrt(max(0,1-v)))
    bearing=math.degrees(math.atan2(math.sin(dl)*math.cos(q),math.cos(p)*math.sin(q)-math.sin(p)*math.cos(q)*math.cos(dl)))%360
    return d,bearing

def wrap(x):return (x+180)%360-180
def number(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)

def follow_path(path, current, accuracy, previous):
    """Project onto a bounded part of the route, then select a forward lookahead."""
    lat0=path[0]['lat'];lon0=path[0]['lng'];scale=111195.08;cosine=math.cos(math.radians(lat0))
    xy=[((p['lng']-lon0)*scale*cosine,(p['lat']-lat0)*scale) for p in path]
    position=((current[1]-lon0)*scale*cosine,(current[0]-lat0)*scale)
    cumulative=[0.]
    for a,b in zip(xy,xy[1:]):cumulative.append(cumulative[-1]+math.hypot(b[0]-a[0],b[1]-a[1]))
    lower=max(0,(previous or 0)-5);upper=(previous or 0)+max(40,accuracy*2+15)
    candidates=[]
    for i,(a,b) in enumerate(zip(xy,xy[1:])):
        length=cumulative[i+1]-cumulative[i]
        if length<.01 or cumulative[i+1]<lower or cumulative[i]>upper:continue
        t=((position[0]-a[0])*(b[0]-a[0])+(position[1]-a[1])*(b[1]-a[1]))/length**2
        t=max(max(0,(lower-cumulative[i])/length),min(min(1,(upper-cumulative[i])/length),t))
        x=a[0]+t*(b[0]-a[0]);y=a[1]+t*(b[1]-a[1])
        candidates.append((math.hypot(position[0]-x,position[1]-y),cumulative[i]+t*length))
    if not candidates:return None
    cross,along=min(candidates);progress=max(previous or 0,along);ahead=min(cumulative[-1],progress+max(5,min(10,accuracy*1.5)))
    target=path[-1]
    for i in range(len(path)-1):
        if cumulative[i+1]>=ahead:
            t=(ahead-cumulative[i])/max(.01,cumulative[i+1]-cumulative[i]);target={'lat':path[i]['lat']+(path[i+1]['lat']-path[i]['lat'])*t,'lng':path[i]['lng']+(path[i+1]['lng']-path[i]['lng'])*t};break
    _,bearing=distance_bearing(current,(target['lat'],target['lng']))
    return dict(progress=progress,remaining=max(0,cumulative[-1]-progress),cross_track=cross,bearing=bearing,total=cumulative[-1])

class PointNavigator:
    def __init__(self):
        self.revision=None;self.index=0;self.arrival_fixes=0;self.last_fix=None;self.done=False;self.previous_position=None;self.route_progress=None

    def update(self, ctx, now_ms):
        cfg=ctx.get('navigation') or {};s=ctx.get('sensors') or {};points=cfg.get('points') or []
        rev=cfg.get('revision',0)
        if self.revision!=rev:
            self.__init__();self.revision=rev
        out=dict(revision=rev,ready=False,stage='waiting',index=self.index,total=len(points),reason='请先设置目标航点',distance_m=None,bearing=None,heading_error=None,arrived=self.done)
        if not points:return out
        handheld=cfg.get('handheld',False);gps_test=cfg.get('gps_test') is True
        out['gps_test']=gps_test
        if cfg.get('blocked') and not handheld:return dict(out,reason='路线含需人工处理的人行横道、特殊路段或偏离道路的起终点，请检查路线提示')
        if self.done:return dict(out,stage='arrived',reason='已到达最后航点的定位容差范围',arrived=True)
        if self.index>=len(points):return out
        lat,lon,acc=s.get('latitude'),s.get('longitude'),s.get('accuracy')
        if not all(number(x) for x in [lat,lon,acc]) or abs(lat)>90 or abs(lon)>180 or acc<=0:
            return dict(out,reason='等待手机定位')
        current=(lat,lon);target=(points[self.index]['lat'],points[self.index]['lng'])
        distance,bearing=distance_bearing(current,target);out.update(distance_m=round(distance,1),bearing=round(bearing,1),accuracy_m=round(acc,1))
        if distance>(2000 if cfg.get('path') else 1000):return dict(out,reason='当前航点过远，请缩短测试路线')
        fix=s.get('location_at');radius=cfg.get('radius',10)
        location_timeout_ms=30000 if gps_test else 15000 if handheld else 4000
        out['location_timeout_ms']=location_timeout_ms
        out['location_age_ms']=max(0,now_ms-fix) if number(fix) else None
        if not number(fix) or now_ms-fix>location_timeout_ms or fix>now_ms+300:return dict(out,reason='手机定位已过期，等待新位置')
        if not gps_test and acc>(30 if handheld else min(10,radius/2)):return dict(out,reason='定位误差过大，等待更精确的位置')
        fix_id=s.get('location_id',fix)
        new_fix=fix_id!=self.last_fix
        if new_fix:
            if self.previous_position:
                old,oldfix,oldacc=self.previous_position;step,_=distance_bearing(old,current);dt=(fix-oldfix)/1000
                if dt<=0 or step>max(8,oldacc+acc+max(0,dt)*3):return dict(out,reason='定位发生异常跳变，等待稳定')
            self.previous_position=(current,fix,acc);self.last_fix=fix_id
        arrival=(distance if gps_test else distance+acc)<=radius
        if cfg.get('path'):
            route=follow_path(cfg['path'],current,min(acc,10) if gps_test else acc,self.route_progress)
            if not route:return dict(out,reason='无法匹配路线，请重新规划')
            out.update(remaining_m=round(route['remaining'],1),progress_m=round(route['progress'],1),route_total_m=round(route['total'],1),cross_track_m=round(route['cross_track'],1))
            if route['cross_track']>max(8,(min(acc,10) if gps_test else acc)*2):return dict(out,reason='偏离规划路线，请手动调整或重新规划')
            self.route_progress=route['progress'];bearing=route['bearing'];out['bearing']=round(bearing,1)
            commands=cfg.get('commands') or []
            current_command=next((c for c in commands if c['end_m']>route['progress']+1),commands[-1] if commands else None)
            if current_command:out['route_command']=dict(current_command,remaining_m=round(max(0,current_command['end_m']-route['progress']),1))
            arrival=arrival and route['remaining']<=radius*1.5
        if new_fix:
            if cfg.get('active'):
                self.arrival_fixes=self.arrival_fixes+1 if arrival else 0
                if self.arrival_fixes>=3:
                    self.index+=1;self.arrival_fixes=0
                    if self.index==len(points):
                        self.done=True;return dict(out,stage='arrived',ready=False,reason='已到达最后航点的定位容差范围',arrived=True,index=self.index)
                    return dict(out,stage='waypoint',reason='已通过航点，正在切换下一点',index=self.index)
        if arrival:
            return dict(out,stage='arrival_check',reason='处于到达范围，等待连续3次新定位确认' if cfg.get('active') else '目标位于当前到达范围，请选择更远航点')
        cal=cfg.get('calibration')
        absolute=s.get('camera_heading_absolute') is True
        if not absolute and (not cal or now_ms-cal.get('at',0)>30*60*1000):return dict(out,reason='等待手机绝对朝向，请允许定位与运动权限并将镜头朝行进方向')
        heading,heading_at=s.get('camera_heading'),s.get('heading_at')
        if not number(heading) or not number(heading_at) or now_ms-heading_at>1500 or heading_at>now_ms+300:return dict(out,reason='等待新鲜的手机朝向数据')
        heading_accuracy=s.get('heading_accuracy')
        out['heading_accuracy_deg']=heading_accuracy if number(heading_accuracy) and heading_accuracy>=0 else None
        out['heading_accuracy_ok']=number(heading_accuracy) and 0<=heading_accuracy<=25
        if not gps_test and not out['heading_accuracy_ok']:return dict(out,reason='手机罗盘精度不足，等待校准稳定')
        offset=0 if absolute else cal['offset']
        error=wrap(bearing-(heading+offset)%360)
        out.update(heading_error=round(error,1),heading=round((heading+offset)%360,1),heading_source='phone_absolute' if absolute else 'manual_calibration')
        if abs(error)>65:
            if not absolute:return dict(out,stage='align',reason='目标偏离车头超过65°，请手动调整车头后重新启动')
            return dict(out,ready=True,stage='turning',reason=('请向左转' if error<0 else '请向右转') if handheld else '自动对齐路线方向中',steering=-.85 if error<0 else .85,rotate=True,command='turn_left' if error<0 else 'turn_right')
        steering=max(-.85,min(.85,error/55))
        return dict(out,ready=True,stage='navigating' if cfg.get('active') else 'ready',reason='手持导航：沿路线继续前进' if handheld else '路线指令就绪，持续根据定位与朝向修正',steering=steering,rotate=False,command='forward' if abs(error)<12 else 'left' if error<0 else 'right')

def guide_visual(result, nav):
    """Never let a route or compass bypass a visual stop."""
    result=dict(result);result['navigation']=nav
    if not result['clear'] or not nav.get('ready'):
        result.update(clear=False,throttle=0)
        if not nav.get('ready'):result['reason']=nav['reason']
        return result
    desired=nav['steering'];candidates=[(-.85,result['scores'][0]),(0,result['scores'][1]),(.85,result['scores'][2])]
    threshold=result.get('corridor_threshold',.88)
    if nav.get('rotate'):
        idx=0 if desired<0 else 2
        if result['scores'][idx]<threshold:return dict(result,clear=False,throttle=0,reason='目标转向侧暂无视觉通路')
        return dict(result,steering=desired,rotate=True,throttle=.65,reason='按路线指令原地调整方向')
    safe=[(abs(s-desired),s) for s,score in candidates if score>=threshold and abs(s-desired)<=.85]
    if not safe:
        result.update(clear=False,throttle=0,reason='目标方向暂无视觉通路，请调整航点或手动绕行');return result
    _,steering=min(safe)
    result.update(steering=steering,throttle=min(result['throttle'],.65),reason='沿航点行驶，视觉通路已确认')
    return result
