"""Conservative image-space ground corridor selection. No metric depth claim."""
import cv2
import numpy as np


def plan_ground(prob, image):
    h, w = prob.shape
    # Erode uncertain boundaries so a single narrow strip cannot become a path.
    mask = (prob >= .62).astype(np.uint8)
    mask = cv2.erode(mask, np.ones((5, 5), np.uint8))
    n, labels = cv2.connectedComponents(mask)
    seed = labels[int(h * .78):int(h * .95), int(w * .36):int(w * .64)]
    counts = np.bincount(seed.ravel(), minlength=n)
    counts[0] = 0
    ground = (labels == counts.argmax()) & (mask > 0) if counts.max() > seed.size * .12 else np.zeros_like(mask, bool)
    scores = []
    for cx in (.28, .5, .72):
        polygon = np.array([[int(w*(.5-.16)),int(h*.94)], [int(w*(.5+.16)),int(h*.94)],
                            [int(w*(cx+.10)),int(h*.55)], [int(w*(cx-.10)),int(h*.55)]])
        roi = np.zeros_like(mask); cv2.fillPoly(roi, [polygon], 1)
        scores.append(float(ground[roi > 0].mean()))
    near = float(ground[int(h*.76):int(h*.94),int(w*.34):int(w*.66)].mean())
    sharpness = float(cv2.Laplacian(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), cv2.CV_64F).var())
    brightness = float(image.mean())
    clear = near >= .86 and max(scores) >= .92 and sharpness >= 18 and brightness >= 24
    idx = 1 if scores[1] >= .94 else int(np.argmax(scores))
    steering = [-.85, 0., .85][idx] if clear else 0.
    if brightness < 24:
        reason = '画面过暗，停车；请增加照明'
    elif sharpness < 18:
        reason = '画面模糊或纹理不足，停车'
    elif near < .86:
        reason = '车前近处未确认连续地面，停车'
    elif max(scores) < .92:
        reason = '前方障碍或通路太窄，停车'
    else:
        reason = '前方疑似可走地面'
    overlay = image.copy()
    overlay[ground] = (overlay[ground] * .55 + np.array([20,220,100]) * .45).astype(np.uint8)
    cv2.arrowedLine(overlay, (w//2,int(h*.92)), (int(w*(.5+steering*.23)),int(h*.58)), (40,210,255) if clear else (255,60,60), max(2,w//150), tipLength=.2)
    return dict(clear=bool(clear), steering=steering, throttle=.65 if clear else 0.,
                scores=[round(s,3) for s in scores], reason=reason,
                near_ratio=round(near,3), sharpness=round(sharpness,1), brightness=round(brightness,1)), overlay
