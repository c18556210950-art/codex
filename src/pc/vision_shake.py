"""Image-motion jitter estimate for feedback only; never changes motor control."""
import math
import cv2
import numpy as np

class VideoShake:
    def __init__(self):
        self.gray=None;self.at=None;self.velocity=None;self.samples=0;self.strength=0.

    def update(self,rgb,at_ms):
        gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
        if gray.shape[1]>320:gray=cv2.resize(gray,(320,round(gray.shape[0]*320/gray.shape[1])))
        previous=self.gray;old_at=self.at;self.gray=gray;self.at=at_ms
        def unavailable():
            self.velocity=None;self.samples=0;self.strength=0.
            return dict(valid=False,intensity=0.,confidence=0.)
        if previous is None or previous.shape!=gray.shape or old_at is None:return unavailable()
        dt=(at_ms-old_at)/1000
        if not .04<=dt<=.7:return unavailable()
        points=cv2.goodFeaturesToTrack(previous,maxCorners=160,qualityLevel=.015,minDistance=6,blockSize=7)
        if points is None or len(points)<12:return unavailable()
        moved,status,_=cv2.calcOpticalFlowPyrLK(previous,gray,points,None,winSize=(21,21),maxLevel=3)
        if moved is None or status is None:return unavailable()
        good=status.ravel()==1;a=points[good];b=moved[good]
        if len(a)<12:return unavailable()
        transform,inliers=cv2.estimateAffinePartial2D(a,b,method=cv2.RANSAC,ransacReprojThreshold=2.5)
        if transform is None or inliers is None:return unavailable()
        confidence=float(inliers.mean())
        if confidence<.5:return unavailable()
        h,w=gray.shape;cx=w/2;cy=h/2
        delta=transform@np.array([cx,cy,1.])-np.array([cx,cy])
        angle=math.atan2(transform[1,0],transform[0,0]);scale=math.hypot(transform[0,0],transform[1,0])
        if np.linalg.norm(delta/np.array([w,h]))>.15 or abs(angle)>.18 or abs(scale-1)>.12:return unavailable()
        velocity=np.array([delta[0]/w,delta[1]/h,angle*.3])/dt
        if self.velocity is None:self.velocity=velocity.copy()
        residual=float(np.linalg.norm(velocity-self.velocity))*.125
        self.velocity+=(1-math.exp(-dt/.35))*(velocity-self.velocity)
        self.samples+=1
        level=max(0.,min(1.,(residual-.0015)/.025))
        self.strength+=(.65 if level>self.strength else .5)*(level-self.strength)
        ready=self.samples>=4
        return dict(valid=ready,intensity=round(self.strength,3) if ready else 0.,confidence=round(confidence,3))
