"""Conservative image-space ground corridor selection. No metric depth claim."""
import cv2
import numpy as np


def plan_ground(prob, image):
    h, w = prob.shape
    # Erode uncertain boundaries so a single narrow strip cannot become a path.
    mask = (prob >= .62).astype(np.uint8)
    mask = cv2.erode(mask, np.ones((3, 3), np.uint8))
    n, labels = cv2.connectedComponents(mask)
    seed = labels[int(h * .85):int(h * .98), int(w * .36):int(w * .64)]
    counts = np.bincount(seed.ravel(), minlength=n)
    counts[0] = 0
    ground = (labels == counts.argmax()) & (mask > 0) if counts.max() > seed.size * .12 else np.zeros_like(mask, bool)
    scores = []
    for cx in (.28, .5, .72):
        polygon = np.array([[int(w*(.5-.16)),int(h*.98)], [int(w*(.5+.16)),int(h*.98)],
                            [int(w*(cx+.10)),int(h*.62)], [int(w*(cx-.10)),int(h*.62)]])
        roi = np.zeros_like(mask); cv2.fillPoly(roi, [polygon], 1)
        scores.append(float(ground[roi > 0].mean()))
    # A separate higher image band sees approaching bends before they enter
    # the near driving corridors. It can select among already-safe corridors,
    # but cannot make an unsafe near corridor drivable.
    far_scores = []
    for cx in (.28, .5, .72):
        polygon = np.array([[int(w*(cx-.11)),int(h*.58)], [int(w*(cx+.11)),int(h*.58)],
                            [int(w*(cx+.09)),int(h*.35)], [int(w*(cx-.09)),int(h*.35)]])
        roi = np.zeros_like(mask); cv2.fillPoly(roi, [polygon], 1)
        far_scores.append(float(ground[roi > 0].mean()))
    near = float(ground[int(h*.85):int(h*.98),int(w*.34):int(w*.66)].mean())
    sharpness = float(cv2.Laplacian(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), cv2.CV_64F).var())
    brightness = float(image.mean())
    near_threshold=.82;corridor_threshold=.88
    clear = near >= near_threshold and max(scores) >= corridor_threshold and sharpness >= 18
    idx = 1 if scores[1] >= corridor_threshold and max(scores)-scores[1]<.03 else int(np.argmax(scores))
    turn_early = False
    if clear and far_scores[1] < .92:
        alternatives = [i for i in (0, 2) if scores[i] >= corridor_threshold and far_scores[i] >= .88 and far_scores[i] - far_scores[1] >= .08]
        if alternatives:
            idx = max(alternatives, key=lambda i: far_scores[i])
            turn_early = True
    steering = [-.85, 0., .85][idx] if clear else 0.
    if sharpness < 18:
        reason = '画面模糊或纹理不足，停车'
    elif near < near_threshold:
        reason = '车前近处未确认连续地面，停车'
    elif max(scores) < corridor_threshold:
        reason = '前方障碍或通路太窄，停车'
    else:
        reason = ('较远处直行通路收窄，提前向'+('左' if idx == 0 else '右')+'转向') if turn_early else '前方疑似可走地面'
        if brightness < 24:reason += '（偏暗，但地面识别通过）'
    overlay = image.copy()
    overlay[ground] = (overlay[ground] * .55 + np.array([20,220,100]) * .45).astype(np.uint8)
    cv2.rectangle(overlay,(int(w*.34),int(h*.85)),(int(w*.66),int(h*.98)),(255,190,40),max(1,w//320))
    cv2.arrowedLine(overlay, (w//2,int(h*.92)), (int(w*(.5+steering*.23)),int(h*.58)), (40,210,255) if clear else (255,60,60), max(2,w//150), tipLength=.2)
    return dict(clear=bool(clear), steering=steering, throttle=.65 if clear else 0.,
                scores=[round(s,3) for s in scores], reason=reason,
                far_scores=[round(s,3) for s in far_scores], turn_early=turn_early,
                near_ratio=round(near,3),near_threshold=near_threshold,corridor_threshold=corridor_threshold,
                sharpness=round(sharpness,1), brightness=round(brightness,1)), overlay
