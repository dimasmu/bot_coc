import cv2, numpy as np, glob

screen_gray = cv2.imread('storage/debug/deploy_full.png', cv2.IMREAD_GRAYSCALE)
screen_color = cv2.imread('storage/debug/deploy_full.png')

# Match all 4 arrows
arrows = sorted(glob.glob('storage/templates/arrow_*-remove-bg-io.png'))
print(f'Found {len(arrows)} arrow templates\n')

arrow_positions = {}
for path in arrows:
    name = path.split('\\')[-1].replace('-remove-bg-io.png', '').replace('arrow_', '')
    tpl_color = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    h, w = tpl_color.shape[:2]
    print(f'{name}: {w}x{h}')

    mask = None
    if len(tpl_color.shape) == 3 and tpl_color.shape[2] == 4:
        mask = tpl_color[:, :, 3]
        tpl_gray = cv2.cvtColor(tpl_color[:, :, :3], cv2.COLOR_BGR2GRAY)
        result = cv2.matchTemplate(screen_gray, tpl_gray, cv2.TM_CCOEFF_NORMED, mask=mask)
    else:
        result = cv2.matchTemplate(screen_gray, tpl_color, cv2.TM_CCOEFF_NORMED)

    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    cx, cy = max_loc[0] + w // 2, max_loc[1] + h // 2
    print(f'  max_val={max_val:.4f} at ({cx}, {cy})')
    arrow_positions[name] = (cx, cy, max_val)

if len(arrow_positions) >= 2:
    cx_avg = int(np.mean([p[0] for p in arrow_positions.values()]))
    cy_avg = int(np.mean([p[1] for p in arrow_positions.values()]))
    print(f'\nD-pad center: ({cx_avg}, {cy_avg})')

    # Find green circles near D-pad
    hsv = cv2.cvtColor(screen_color, cv2.COLOR_BGR2HSV)
    green_mask = cv2.inRange(hsv, np.array([40, 80, 80]), np.array([85, 255, 255]))
    contours, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    print(f'\nGreen circles near D-pad ({cx_avg}, {cy_avg}):')
    best = None
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 80 or area > 4000:
            continue
        M = cv2.moments(cnt)
        if M['m00'] == 0:
            continue
        gx = int(M['m10'] / M['m00'])
        gy = int(M['m01'] / M['m00'])
        dist = np.sqrt((gx - cx_avg)**2 + (gy - cy_avg)**2)
        if dist < 300:
            ss = 8
            x1, y1 = max(0, gx - ss), max(0, gy - ss)
            x2, y2 = min(screen_color.shape[1], gx + ss), min(screen_color.shape[0], gy + ss)
            roi = screen_color[y1:y2, x1:x2]
            gc = np.sum((roi[:, :, 1] > 150) & (roi[:, :, 1] > roi[:, :, 2] * 1.05) & (roi[:, :, 1] > roi[:, :, 0] * 1.05))
            ratio = gc / (roi.shape[0] * roi.shape[1])
            perimeter = cv2.arcLength(cnt, True)
            circularity = 4 * np.pi * area / (perimeter * perimeter) if perimeter > 0 else 0
            offset_x = gx - cx_avg
            offset_y = gy - cy_avg
            print(f'  ({gx},{gy}) area={area:.0f} green={ratio:.0%} circ={circularity:.2f} offset=({offset_x:+d},{offset_y:+d})')
            if circularity > 0.3 and ratio > 0.05:
                if best is None or area > best[0]:
                    best = (area, gx, gy, offset_x, offset_y, ratio)

    if best:
        print(f'\nBEST CHECKMARK: ({best[1]},{best[2]}) offset=({best[3]:+d},{best[4]:+d}) green={best[5]:.0%}')

    # Draw D-pad center and best checkmark on debug image
    dbg = screen_color.copy()
    cv2.circle(dbg, (cx_avg, cy_avg), 10, (0, 0, 255), 3)
    if best:
        cv2.circle(dbg, (best[1], best[2]), 10, (0, 255, 0), 3)
    cv2.imwrite('storage/debug/dpad_analysis.png', dbg)
    print('Saved dpad_analysis.png')
