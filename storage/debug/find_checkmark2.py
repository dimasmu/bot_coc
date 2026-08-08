import cv2, numpy as np, glob

screen = cv2.imread('storage/debug/deploy_full.png')
screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
h, w = screen.shape[:2]

# Match all 4 arrow templates
arrows = sorted(glob.glob('storage/templates/arrow_*-remove-bg-io.png'))
positions = []
for path in arrows:
    name = path.rsplit('\\', 1)[-1]
    tpl = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    th, tw = tpl.shape[:2]
    mask = tpl[:, :, 3] if len(tpl.shape) == 3 and tpl.shape[2] == 4 else None
    tpl_gray = cv2.cvtColor(tpl[:, :, :3], cv2.COLOR_BGR2GRAY)
    result = cv2.matchTemplate(screen_gray, tpl_gray, cv2.TM_CCOEFF_NORMED, mask=mask)
    _, mv, _, ml = cv2.minMaxLoc(result)
    cx, cy = ml[0] + tw // 2, ml[1] + th // 2
    positions.append((cx, cy, mv, name))
    print(f'{name}: ({cx},{cy}) val={mv:.3f}')

if not positions:
    print('No arrows found')
    exit()

# D-pad center
cx = int(np.mean([p[0] for p in positions]))
cy = int(np.mean([p[1] for p in positions]))
print(f'\nD-pad center: ({cx}, {cy})')

# Search for the 2 buttons (X and checkmark) below the D-pad
# They should be in a row at the bottom of the building card
# Look in the area: same X range as D-pad, Y from cy+200 to bottom
band_y1 = max(0, cy + 200)
band_y2 = h
band_x1 = max(0, cx - 200)
band_x2 = min(w, cx + 200)

band = screen[band_y1:band_y2, band_x1:band_x2]
print(f'\nSearching buttons in band: x={band_x1}-{band_x2}, y={band_y1}-{band_y2}')

# Find green and red rect/circle shapes (buttons)
hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)

# Green button (checkmark)
green_mask = cv2.inRange(hsv, np.array([40, 80, 80]), np.array([85, 255, 255]))
green_contours, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

# Red button (X cancel) - red wraps around in HSV
red_mask1 = cv2.inRange(hsv, np.array([0, 80, 80]), np.array([10, 255, 255]))
red_mask2 = cv2.inRange(hsv, np.array([170, 80, 80]), np.array([180, 255, 255]))
red_mask = cv2.bitwise_or(red_mask1, red_mask2)
red_contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

print('\nGreen button candidates:')
for cnt in green_contours:
    area = cv2.contourArea(cnt)
    if area < 80 or area > 4000: continue
    M = cv2.moments(cnt)
    if M['m00'] == 0: continue
    gx = int(M['m10'] / M['m00']) + band_x1
    gy = int(M['m01'] / M['m00']) + band_y1
    perimeter = cv2.arcLength(cnt, True)
    circ = 4*np.pi*area/(perimeter*perimeter) if perimeter > 0 else 0
    ss = 8
    roi = screen[max(0,gy-ss):min(h,gy+ss), max(0,gx-ss):min(w,gx+ss)]
    gc = np.sum((roi[:,:,1] > 150) & (roi[:,:,1] > roi[:,:,2]*1.05) & (roi[:,:,1] > roi[:,:,0]*1.05))
    ratio = gc/(roi.shape[0]*roi.shape[1])
    off_x, off_y = gx - cx, gy - cy
    print(f'  ({gx},{gy}) area={area:.0f} circ={circ:.2f} green={ratio:.0%} offset=({off_x:+d},{off_y:+d})')

print('\nRed/X button candidates:')
for cnt in red_contours:
    area = cv2.contourArea(cnt)
    if area < 80 or area > 4000: continue
    M = cv2.moments(cnt)
    if M['m00'] == 0: continue
    rx = int(M['m10'] / M['m00']) + band_x1
    ry = int(M['m01'] / M['m00']) + band_y1
    perimeter = cv2.arcLength(cnt, True)
    circ = 4*np.pi*area/(perimeter*perimeter) if perimeter > 0 else 0
    off_x, off_y = rx - cx, ry - cy
    print(f'  ({rx},{ry}) area={area:.0f} circ={circ:.2f} offset=({off_x:+d},{off_y:+d})')
