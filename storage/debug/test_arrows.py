import cv2, numpy as np, glob

screen = cv2.imread('storage/debug/deploy_full.png')
screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
h, w = screen.shape[:2]

arrows = sorted(glob.glob('storage/templates/arrow_*-remove-bg-io.png'))
print('Arrow matches on deploy screen:')
positions = []
for path in arrows:
    name = path.rsplit('\\', 1)[-1].replace('-remove-bg-io.png', '')
    tpl = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    th, tw = tpl.shape[:2]
    mask = tpl[:,:,3]
    tpl_gray = cv2.cvtColor(tpl[:,:,:3], cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(screen_gray, tpl_gray, cv2.TM_CCOEFF_NORMED, mask=mask)
    _, mv, _, ml = cv2.minMaxLoc(res)
    cx, cy = ml[0]+tw//2, ml[1]+th//2
    positions.append((cx, cy, mv, name))
    b,g,r = screen[cy,cx].astype(int)
    print(f'  {name:15s}: ({cx:4d},{cy:3d}) val={mv:.3f}')

if len(positions) >= 3:
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    x_med = sorted(xs)[len(xs)//2]
    y_med = sorted(ys)[len(ys)//2]
    print(f'\nMedian arrow: ({x_med},{y_med})')

    # Search area below arrows for checkmark (buttons ~500px down)
    est_y = y_med + 500
    est_x = x_med
    sx1, sx2 = max(0, est_x-200), min(w, est_x+200)
    sy1, sy2 = max(0, est_y-100), min(h, est_y+100)

    # Check template match AND green color in this zone
    tpl_ck = cv2.imread('storage/templates/btn_deploy_checkmark.png', cv2.IMREAD_UNCHANGED)
    ck_mask = tpl_ck[:,:,3]
    ck_gray = cv2.cvtColor(tpl_ck[:,:,:3], cv2.COLOR_BGR2GRAY)
    region = screen_gray[sy1:sy2, sx1:sx2]
    res_ck = cv2.matchTemplate(region, ck_gray, cv2.TM_CCOEFF_NORMED, mask=ck_mask)
    _, ck_mv, _, ck_ml = cv2.minMaxLoc(res_ck)
    abs_ck = (ck_ml[0]+tpl_ck.shape[1]//2+sx1, ck_ml[1]+tpl_ck.shape[0]//2+sy1)
    print(f'Checkmark in search zone: max={ck_mv:.4f} at {abs_ck}')

    # Also brute-force: scan the zone for any horizontal band of pixels that could be buttons
    print(f'\nScanning button zone ({sx1},{sy1})-({sx2},{sy2}):')
    zone = screen[sy1:sy2, sx1:sx2]
    # Find dark UI panel (should be a semi-transparent dark area)
    dark = (zone[:,:,0] < 80) & (zone[:,:,1] < 80) & (zone[:,:,2] < 80)
    dark_ratio = np.sum(dark) / zone.size
    print(f'  Dark pixels: {dark_ratio:.1%} (deploy panel should be dark)')
