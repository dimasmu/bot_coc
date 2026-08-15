"""Deteksi bar X/centang pada mode deploy bangunan baru.

Bar ini DINAMIS — melayang tepat di atas building ghost dan mengikuti
posisinya (bukan posisi tetap di layar). Struktur bar (~70x60, gelap
transparan): tombol centang di sisi KIRI (hijau = tempat valid, abu =
terhalang), tombol X merah di sisi KANAN.

Kalibrasi dari capture live 1280x720 (Air Defense):
  bar            = (507,267,69,61)
  centang hijau  = cluster H 50-70, S>150, V>60 — 346 px (valid), ~4 px (rumput)
  X merah        = cluster H<12|>168, S>120, V>120 — centroid rel (59%, 41%)
"""

import cv2
import numpy as np

# Game area tempat ghost bisa berada — mengecualikan HUD atas/bawah
_GAME_AREA = {"y1": 150, "y2": 580, "x1": 200, "x2": 1050}

# Centang hijau: hue 50-70 membedakannya dari rumput (H~30-45) dan
# dari state abu (sat rendah).
_CK_GREEN_MIN_PX = 50


def find_deploy_bar(img: np.ndarray) -> tuple[int, int, int, int] | None:
    """Temukan bar X/centang di mode deploy. Returns (x, y, w, h) atau None.

    Tombol X (merah) dan centang sejajar kiri-kanan di dalam bar.
    Anchor = kontur merah kompak (100-900 px) di area game.

    PRIORITAS 1 — state valid: ada cluster hijau centang (H 50-70,
    S>150, V>60, >= 50 px) di BARIS yang sama dengan X (±25 y, ±80 x).
    Footprint hijau di tanah (indikator posisi valid, area jauh lebih
    besar) tidak boleh dikira centang — proximity ke X adalah kuncinya.

    PRIORITAS 2 — state terhalang (centang abu): X berada di dalam bbox
    blob gelap kompak (V<90, area 300-1500).
    """
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    green_mask = ((hsv[:, :, 0] >= 50) & (hsv[:, :, 0] <= 70)
                  & (hsv[:, :, 1] > 150) & (hsv[:, :, 2] > 60))

    red = (((hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 168))
           & (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 120)).astype(np.uint8)
    red_contours, _ = cv2.findContours(red, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
    x_candidates = []
    for c in red_contours:
        area = cv2.contourArea(c)
        if not (100 <= area <= 900):
            continue
        bx, by, bw, bh = cv2.boundingRect(c)
        cx, cy = bx + bw // 2, by + bh // 2
        if not (_GAME_AREA["y1"] <= cy <= _GAME_AREA["y2"]
                and _GAME_AREA["x1"] <= cx <= _GAME_AREA["x2"]):
            continue
        x_candidates.append((area, cx, cy))

    if not x_candidates:
        return None

    # ── Prioritas 1: centang hijau sebaris dengan X ──
    for _, cx, cy in sorted(x_candidates, reverse=True):
        x1, y1 = max(0, cx - 80), max(0, cy - 25)
        x2, y2 = min(w, cx + 80), min(h, cy + 25)
        window = green_mask[y1:y2, x1:x2]
        if int(window.sum()) >= 50:
            ys, xs = np.where(window)
            gcx, gcy = x1 + int(xs.mean()), y1 + int(ys.mean())
            # kotak bar memuat X dan centang
            bx = min(cx, gcx) - 20
            by = cy - 32
            bw = abs(cx - gcx) + 50
            bh = 70
            return (max(0, bx), max(0, by), bw, bh)

    # ── Prioritas 2: state terhalang — X di dalam blob gelap kompak ──
    dark = (hsv[:, :, 2] < 90).astype(np.uint8)
    dark_contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_SIMPLE)

    for _, cx, cy in sorted(x_candidates, reverse=True):
        for dc in dark_contours:
            da = cv2.contourArea(dc)
            if not (300 <= da <= 1500):
                continue
            bx, by, bw, bh = cv2.boundingRect(dc)
            if bw < 40 or bh < 25 or bw / bh < 0.8 or bw / bh > 2.5:
                continue
            if bx <= cx <= bx + bw and by <= cy <= by + bh:
                return (bx, by, bw, bh)

    return None


def deploy_checkmark_state(img: np.ndarray,
                           bar: tuple[int, int, int, int]) -> str:
    """Klasifikasi centang: 'green' (tempat valid) atau 'gray' (terhalang).

    Centang hijau CoC = cluster hue 50-70 jenuh di dalam bar; state abu
    kehilangan cluster itu. Rumput yang tembus bar transparan punya hue
    30-45 sehingga tidak ikut terhitung.
    """
    bx, by, bw, bh = bar
    sub = cv2.cvtColor(img[by:by + bh, bx:bx + bw], cv2.COLOR_BGR2HSV)
    green = ((sub[:, :, 0] >= 50) & (sub[:, :, 0] <= 70)
             & (sub[:, :, 1] > 150) & (sub[:, :, 2] > 60)).sum()
    return "green" if green >= _CK_GREEN_MIN_PX else "gray"


def deploy_button_centers(img: np.ndarray, bar: tuple[int, int, int, int]) -> tuple[
        tuple[int, int], tuple[int, int]]:
    """Returns ((ck_x, ck_y), (x_x, x_y)).

    X = centroid merah di dalam bar. Centang = centroid hijau pada
    BAND BARIS yang sama dengan X (±25 y) — footprint hijau di tanah
    (di bawah bar) sengaja dikecualikan supaya tap tidak meleset ke
    indikator posisi yang bukan tombol.
    """
    bx, by, bw, bh = bar
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    sub_bar = hsv[by:by + bh, bx:bx + bw]
    rmask = (((sub_bar[:, :, 0] < 12) | (sub_bar[:, :, 0] > 168))
             & (sub_bar[:, :, 1] > 120) & (sub_bar[:, :, 2] > 120))
    ys, xs = np.where(rmask)
    if len(xs) >= 30:
        xb = (bx + int(xs.mean()), by + int(ys.mean()))
    else:
        xb = (bx + int(bw * 0.60), by + int(bh * 0.45))

    band = hsv[max(0, xb[1] - 25):xb[1] + 25, bx:bx + bw]
    gmask = ((band[:, :, 0] >= 50) & (band[:, :, 0] <= 70)
             & (band[:, :, 1] > 150) & (band[:, :, 2] > 60))
    ys, xs = np.where(gmask)
    if len(xs) >= _CK_GREEN_MIN_PX:
        ck = (bx + int(xs.mean()), max(0, xb[1] - 25) + int(ys.mean()))
    else:
        ck = (bx + int(bw * 0.2), xb[1])

    return ck, xb
