"""Visual engine, editorial 'noir' style. Real data only: token logo + real candles from GeckoTerminal."""
import io, re, pathlib, requests
from PIL import Image, ImageDraw, ImageFont

FONTS = pathlib.Path(__file__).parent / "fonts"
BG, INK, MUTED, LINE, RED = (15, 14, 13), (236, 230, 220), (135, 128, 118), (60, 56, 52), (196, 48, 43)
W, H = 1080, 1350


def F(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def spaced(d, xy, text, font, fill, sp=5):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + sp


def wrap(d, text, font, maxw):
    lines, line = [], ""
    for w in text.split():
        if d.textlength((line + " " + w).strip(), font=font) > maxw:
            lines.append(line); line = w
        else:
            line = (line + " " + w).strip()
    return lines + [line]


def fetch_candles(network, pool, limit=48):
    try:
        r = requests.get(f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool}/ohlcv/hour",
                         params={"aggregate": 1, "limit": limit}, timeout=20)
        r.raise_for_status()
        rows = r.json()["data"]["attributes"]["ohlcv_list"]
        return sorted(rows, key=lambda x: x[0])  # [ts, o, h, l, c, v]
    except Exception as e:
        print("candles unavailable:", e)
        return None


def fetch_logo(url, size):
    try:
        im = Image.open(io.BytesIO(requests.get(url, timeout=15).content)).convert("RGBA").resize((size, size))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
        im.putalpha(mask)
        return im
    except Exception as e:
        print("logo unavailable:", e)
        return None


def header(d, brand, right):
    d.text((70, 60), brand, font=F("LiberationSerif-BoldItalic.ttf", 44), fill=INK)
    spaced(d, (70, 124), right, F("Poppins-Medium.ttf", 22), MUTED)
    d.line([70, 186, 1010, 186], fill=MUTED, width=1)


def draw_candles(d, box, candles):
    x0, y0, x1, y1 = box
    for i in range(1, 5):
        yy = y0 + (y1 - y0) * i / 5
        d.line([x0, yy, x1, yy], fill=(34, 32, 30), width=1)
    if not candles or len(candles) < 5:
        d.text((x0 + 20, (y0 + y1) // 2 - 14), "Chart unavailable", font=F("Poppins-Light.ttf", 28), fill=MUTED)
        return
    hi, lo = max(c[2] for c in candles), min(c[3] for c in candles)
    span = (hi - lo) or 1
    pad, n = 14, len(candles)
    step = (x1 - x0 - 2 * pad) / n
    py = lambda v: y1 - pad - (v - lo) / span * (y1 - y0 - 2 * pad)
    for i, (_, o, h, l, c, _v) in enumerate(candles):
        cx = x0 + pad + step * (i + 0.5)
        col = INK if c >= o else RED
        d.line([cx, py(h), cx, py(l)], fill=col, width=2)
        top, bot = sorted((py(o), py(c)))
        d.rectangle([cx - step * 0.32, top, cx + step * 0.32, max(bot, top + 2)], fill=col)


def alert_card(data, candles, out_path, brand):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    t = data["token"]
    header(d, brand, f"RISK BRIEF  /  {t['chain'].upper()}")
    logo = fetch_logo(data.get("logo"), 130) if data.get("logo") else None
    x = 70
    if logo:
        img.paste(logo, (70, 225), logo)
        x = 230
    d.text((x, 205), "$" + t["symbol"].upper()[:10], font=F("LiberationSerif-Bold.ttf", 140 if len(t["symbol"]) < 7 else 100), fill=INK)
    n = data["risk_count"]
    head = f"{n} of 6 risk flags raised." if data["risks_known"] else "Risk data unavailable for this token."
    d.text((70, 400), head, font=F("LiberationSerif-Italic.ttf", 52), fill=INK)
    d.rectangle([70, 500, 1010, 900], outline=LINE, width=1)
    draw_candles(d, (70, 500, 1010, 900), candles)
    spaced(d, (70, 912), "LAST 48 HOURS, HOURLY", F("Poppins-Medium.ttf", 18), MUTED, 4)
    m = {k: (v, tone) for k, v, tone in data["metrics"]}
    cols = [("LIQUIDITY", 70), ("24H VOLUME", 400), ("PAIR AGE", 730)]
    for k, xx in cols:
        spaced(d, (xx, 990), k, F("Poppins-Medium.ttf", 20), MUTED, 4)
        d.text((xx, 1030), m[k][0], font=F("LiberationSerif-Bold.ttf", 54), fill=INK)
    d.line([70, 1130, 1010, 1130], fill=LINE, width=1)
    spaced(d, (70, 1160), "24H CHANGE", F("Poppins-Medium.ttf", 20), MUTED, 4)
    ch, tone = m["24H CHANGE"]
    d.text((70, 1195), ch, font=F("LiberationSerif-Bold.ttf", 54), fill=RED if tone == "red" else INK)
    d.text((70, 1290), "Data: DexScreener, GeckoTerminal, RugCheck. Not financial advice.", font=F("Poppins-Light.ttf", 20), fill=MUTED)
    img.save(out_path)
    return out_path


def carousel(slides, prefix, outdir, brand):
    files, total = [], len(slides)
    for i, text in enumerate(slides):
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        header(d, brand, "RISK BRIEF")
        if i == 0:
            y = 330
            for l in wrap(d, text, F("LiberationSerif-Bold.ttf", 96), 940):
                d.text((70, y), l, font=F("LiberationSerif-Bold.ttf", 96), fill=INK); y += 118
            spaced(d, (70, 1230), "SWIPE", F("Poppins-Medium.ttf", 24), MUTED, 6)
        else:
            m = re.search(r"[-+]?\$?\d[\d,]*\.?\d*[KMB%x]?", text)
            stat = m.group(0) if m and re.search(r"[$%KMBx]", m.group(0)) else None
            y = 340
            if stat and i < total - 1:
                d.text((70, y), stat, font=F("LiberationSerif-Bold.ttf", 190), fill=RED if stat.startswith("-") else INK)
                d.line([70, y + 235, 1010, y + 235], fill=LINE, width=1)
                y += 275
            f = F("LiberationSerif-Italic.ttf", 58)
            for l in wrap(d, text, f, 940):
                d.text((70, y), l, font=f, fill=INK); y += 78
        spaced(d, (70, 1290), f"{i + 1} / {total}", F("Poppins-Medium.ttf", 20), MUTED, 4)
        d.text((560, 1288), "Not financial advice. DYOR.", font=F("Poppins-Light.ttf", 20), fill=MUTED)
        name = f"{prefix}_{i + 1}.png"
        img.save(pathlib.Path(outdir) / name)
        files.append(name)
    return files


# ---------- friendly style (colors taken from the token logo) ----------
def logo_palette(logo_img):
    """Return dict(bg, ink, a1, a2) derived from the logo's dominant colors."""
    import colorsys
    im = logo_img.convert("RGBA")
    px = [p[:3] for p in im.resize((64, 64)).getdata() if p[3] > 200]
    q = Image.new("RGB", (len(px), 1)); q.putdata(px)
    pal = q.quantize(6).convert("RGB").getcolors(len(px)) or [(1, (120, 80, 200))]
    cols = [c for _, c in sorted(pal, reverse=True)]
    sat = lambda c: colorsys.rgb_to_hsv(*[v / 255 for v in c])[1]
    val = lambda c: colorsys.rgb_to_hsv(*[v / 255 for v in c])[2]
    vivid = sorted(cols, key=lambda c: sat(c) * val(c), reverse=True)
    hue = lambda c: colorsys.rgb_to_hsv(*[v / 255 for v in c])[0]
    a1 = vivid[0]
    far = [c for c in vivid[1:] if min(abs(hue(c) - hue(a1)), 1 - abs(hue(c) - hue(a1))) > 0.12]
    a2 = far[0] if far else tuple(int(v * 0.6) for v in a1)
    tint = lambda c, k: tuple(int(255 - (255 - v) * k) for v in c)
    ink = tuple(int(v * 0.28) for v in a1)
    return {"bg": tint(a1, 0.10), "ink": ink, "a1": a1, "a2": a2}


def friendly_card(d_in, pal, out_path, brand, logo=None):
    img = Image.new("RGB", (W, H), pal["bg"])
    d = ImageDraw.Draw(img)
    ink, a1, a2 = pal["ink"], pal["a1"], pal["a2"]
    soft = tuple(int(255 - (255 - v) * 0.22) for v in a1)
    fr = lambda s: F("FredokaOne-Regular.ttf", s)
    d.ellipse([70, 66, 94, 90], fill=a1)
    d.text((108, 58), brand, font=fr(40), fill=ink)
    tag = "RISK CHECK"
    tw = d.textlength(tag, font=fr(24)) + 44
    d.rounded_rectangle([1010 - tw, 56, 1010, 104], 24, fill=soft)
    d.text((1010 - tw + 22, 66), tag, font=fr(24), fill=a2)
    x = 70
    if logo:
        img.paste(logo, (70, 170), logo); x = 220
    d.text((x, 150), "$" + d_in["symbol"], font=fr(150), fill=ink)
    d.text((x + 4, 335), d_in["name"], font=F("Poppins-Medium.ttf", 36), fill=a2)
    y = 440
    for l in wrap(d, d_in["headline"], fr(64), 940):
        d.text((70, y), l, font=fr(64), fill=ink); y += 80
    # comparison bars (real figures, drawn to scale: the small one is a sliver on purpose)
    y += 50
    for label, value, frac, col in d_in["bars"]:
        d.text((70, y), label, font=F("Poppins-Medium.ttf", 28), fill=ink)
        d.text((1010 - d.textlength(value, font=fr(40)), y - 8), value, font=fr(40), fill=col)
        y += 56
        d.rounded_rectangle([70, y, 1010, y + 54], 27, fill=soft)
        d.rounded_rectangle([70, y, 70 + max(14, int(940 * frac)), y + 54], 27, fill=col)
        y += 120
    # stat chips
    cx = 70
    for k, v in d_in["stats"]:
        w = 290
        d.rounded_rectangle([cx, y, cx + w, y + 130], 28, fill=soft)
        d.text((cx + 26, y + 20), k, font=F("Poppins-Medium.ttf", 21), fill=a2)
        d.text((cx + 26, y + 58), v, font=fr(44), fill=ink)
        cx += w + 35
    d.text((70, 1262), d_in["footer"], font=F("Poppins-Medium.ttf", 21), fill=a2)
    d.text((70, 1296), "Not financial advice. DYOR.", font=F("Poppins-Light.ttf", 21), fill=ink)
    img.save(out_path)
    return out_path
