"""Cinematic renderer: HTML/CSS -> PNG with headless Chromium (Playwright).
Far richer than Pillow: gradients, glow, film grain, blur, real typography, SVG charts."""
import pathlib, html as _h

FONTS = pathlib.Path(__file__).parent / "fonts"


def _face(family, file, weight, style="normal"):
    return (f"@font-face{{font-family:'{family}';src:url('file://{FONTS / file}');"
            f"font-weight:{weight};font-style:{style};}}")


FACES = "".join([
    _face("Hanken", "HankenGrotesk-Light.otf", 300), _face("Hanken", "HankenGrotesk-Regular.otf", 400),
    _face("Hanken", "HankenGrotesk-Medium.otf", 500), _face("Hanken", "HankenGrotesk-SemiBold.otf", 600),
    _face("Hanken", "HankenGrotesk-Bold.otf", 700),
    _face("Serif", "SourceSerifPro-Light.ttf", 300), _face("Serif", "SourceSerifPro-Regular.ttf", 400),
    _face("Serif", "SourceSerifPro-Semibold.ttf", 600), _face("Serif", "SourceSerifPro-It.ttf", 400, "italic"),
])

GRAIN = ("<svg width='0' height='0'><filter id='g'><feTurbulence type='fractalNoise' baseFrequency='.9' "
         "numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 .55 0'/>"
         "</filter></svg>")


def to_png(html_doc, out, w=1080, h=1350):
    import os
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers") if os.path.exists("/opt/pw-browsers") else None
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--allow-file-access-from-files"])
        pg = b.new_page(viewport={"width": w, "height": h})
        import tempfile
        tmp = pathlib.Path(tempfile.gettempdir()) / "card.html"
        tmp.write_text(html_doc, encoding="utf-8")
        pg.goto(f"file://{tmp}", wait_until="load")
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(out))
        b.close()
    return str(out)


def rgb(c):
    return f"{c[0]},{c[1]},{c[2]}"


def poster_card(d, pal, out, brand):
    """Typographic 'contrast' poster. d: symbol, name, chain, label_big, big, label_small, small, line, date"""
    a1, a2 = pal["a1"], pal["a2"]
    e = _h.escape
    doc = f"""<html><head><meta charset='utf-8'><style>{FACES}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1080px;height:1350px;overflow:hidden;background:#0b0a0c;color:#f1ece4;font-family:'Hanken';position:relative}}
.glow1{{position:absolute;width:1100px;height:1100px;right:-420px;top:-380px;border-radius:50%;
  background:radial-gradient(circle,rgba({rgb(a1)},.55) 0%,rgba({rgb(a1)},.18) 40%,transparent 70%);filter:blur(30px)}}
.glow2{{position:absolute;width:900px;height:900px;left:-420px;bottom:-420px;border-radius:50%;
  background:radial-gradient(circle,rgba({rgb(a2)},.32) 0%,transparent 70%);filter:blur(40px)}}
.vig{{position:absolute;inset:0;background:radial-gradient(ellipse at center,transparent 45%,rgba(0,0,0,.65) 100%)}}
.grain{{position:absolute;inset:0;opacity:.16;filter:url(#g);mix-blend-mode:overlay}}
.wrap{{position:absolute;inset:0;padding:72px 78px}}
.top{{display:flex;justify-content:space-between;align-items:center;font-size:21px;letter-spacing:.28em;font-weight:500;color:rgba(241,236,228,.62)}}
.brand{{color:#f1ece4;font-weight:700;letter-spacing:.34em}}
.rule{{height:1px;background:linear-gradient(90deg,rgba(241,236,228,.5),rgba(241,236,228,0));margin:26px 0 44px}}
.id{{font-size:30px;font-weight:500;letter-spacing:.04em}}
.id span{{color:rgba(241,236,228,.55);font-weight:400}}
.lab{{font-size:20px;letter-spacing:.3em;font-weight:500;color:rgba(241,236,228,.55);margin-top:78px}}
.big{{font-family:'Serif';font-weight:300;font-size:345px;line-height:1;white-space:nowrap;letter-spacing:-.02em;margin-top:6px;
  background:linear-gradient(180deg,#fff 10%,rgba(241,236,228,.28) 100%);-webkit-background-clip:text;color:transparent}}
.small{{font-family:'Serif';font-weight:600;font-size:205px;line-height:1.05;white-space:nowrap;margin-top:8px;letter-spacing:-.01em;
  color:rgb({rgb(a1)});text-shadow:0 0 60px rgba({rgb(a1)},.55)}}
.line{{font-family:'Serif';font-style:italic;font-size:50px;line-height:1.3;color:rgba(241,236,228,.9);margin-top:70px;max-width:880px}}
.foot{{position:absolute;left:78px;right:78px;bottom:64px;display:flex;justify-content:space-between;font-size:19px;color:rgba(241,236,228,.5);letter-spacing:.06em}}
</style></head><body>{GRAIN}
<div class='glow1'></div><div class='glow2'></div><div class='vig'></div>
<div class='wrap'>
 <div class='top'><div class='brand'>{e(brand.upper())}</div><div>RISK CHECK &nbsp;/&nbsp; {e(d['date'])}</div></div>
 <div class='rule'></div>
 <div class='id'>${e(d['symbol'])} <span>&nbsp;{e(d['name'])} &nbsp;/&nbsp; {e(d['chain'])}</span></div>
 <div class='lab'>{e(d['label_big'])}</div>
 <div class='big'>{e(d['big'])}</div>
 <div class='lab' style='margin-top:34px'>{e(d['label_small'])}</div>
 <div class='small'>{e(d['small'])}</div>
 <div class='line'>{e(d['line'])}</div>
</div>
<div class='foot'><div>{e(d['sources'])}</div><div>Not financial advice. DYOR.</div></div>
</body></html>"""
    return to_png(doc, out)


# ---------- colorful meme-native style (neo-brutalist stickers) ----------
def meme_card(d, out, brand, pal=None):
    """d: symbol, name, chain, label_a, value_a, label_b, value_b, bubble, sources"""
    pal = pal or {"bg": "#FFD23F", "blue": "#2D5BFF", "pink": "#FF4D8D"}
    e = _h.escape
    doc = f"""<html><head><meta charset='utf-8'><style>{FACES}{_face('Fredoka','FredokaOne-Regular.ttf',400)}{_face('Pop','Poppins-Bold.ttf',700)}{_face('PopM','Poppins-Medium.ttf',500)}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1080px;height:1350px;overflow:hidden;background:{pal['bg']};font-family:'Pop';color:#111;position:relative;
 background-image:radial-gradient(rgba(0,0,0,.16) 3px,transparent 3.5px);background-size:26px 26px}}
.wrap{{position:absolute;inset:0;padding:64px 70px}}
.sticker{{display:inline-block;border:6px solid #111;border-radius:18px;box-shadow:10px 10px 0 #111;padding:12px 26px;font-family:'Fredoka';font-size:40px}}
.top{{display:flex;justify-content:space-between;align-items:center}}
.brand{{background:#111;color:{pal['bg']};transform:rotate(-3deg);box-shadow:8px 8px 0 {pal['pink']}}}
.tag{{background:{pal['pink']};color:#fff;font-size:30px;transform:rotate(3deg)}}
.tick{{font-family:'Fredoka';font-size:178px;line-height:1;margin-top:34px;color:#fff;-webkit-text-stroke:12px #111;paint-order:stroke fill;text-shadow:12px 12px 0 #111;letter-spacing:2px}}
.sub{{margin-top:22px;background:#fff;font-family:'PopM';font-size:30px;padding:10px 24px;box-shadow:8px 8px 0 #111}}
.card{{border:6px solid #111;border-radius:30px;box-shadow:14px 14px 0 #111;padding:20px 40px 14px;margin-top:38px}}
.lab{{font-family:'Pop';font-size:26px;letter-spacing:.06em}}
.val{{font-family:'Fredoka';font-size:122px;line-height:1.05;margin-top:0}}
.a{{background:#fff;transform:rotate(-1.6deg)}} .a .val{{color:{pal['blue']}}}
.b{{background:{pal['pink']};transform:rotate(1.6deg);margin-top:34px}} .b .val{{color:#fff;-webkit-text-stroke:7px #111;paint-order:stroke fill;text-shadow:8px 8px 0 #111}}
.vs{{position:absolute;left:830px;top:672px;width:118px;height:118px;border-radius:50%;background:#111;color:{pal['bg']};
 font-family:'Fredoka';font-size:46px;display:flex;align-items:center;justify-content:center;transform:rotate(-8deg);z-index:5}}
.bubble{{position:absolute;left:70px;right:70px;bottom:112px;background:#fff;border:6px solid #111;border-radius:34px;box-shadow:10px 10px 0 #111;
 padding:22px 36px;font-size:36px;line-height:1.25}}
.bubble:after{{content:'';position:absolute;right:120px;top:-44px;width:50px;height:50px;background:#fff;border-left:6px solid #111;border-top:6px solid #111;transform:rotate(45deg)}}
.foot{{position:absolute;left:70px;right:70px;bottom:44px;display:flex;justify-content:space-between;font-family:'PopM';font-size:21px}}
</style></head><body><div class='wrap'>
 <div class='top'><div class='sticker brand'>{e(brand)}</div><div class='sticker tag'>RISK CHECK</div></div>
 <div class='tick'>${e(d['symbol'])}</div>
 <div class='sticker sub'>{e(d['name'])} &nbsp;/&nbsp; {e(d['chain'])}</div>
 <div class='card a'><div class='lab'>{e(d['label_a'])}</div><div class='val'>{e(d['value_a'])}</div></div>
 <div class='card b'><div class='lab'>{e(d['label_b'])}</div><div class='val'>{e(d['value_b'])}</div></div>
</div><div class='vs'>VS</div>
<div class='bubble'>{e(d['bubble'])}</div>
<div class='foot'><div>{e(d['sources'])}</div><div>Not financial advice. DYOR.</div></div>
</body></html>"""
    return to_png(doc, out)


# ---------- memecoin-native style: real token logo, candle backdrop, sunburst, meme-caption type ----------
def _candles_svg(w=1080, h=1350, n=26, seed=7):
    import random
    r = random.Random(seed); y = h * 0.55; out = []
    step = w / n
    for i in range(n):
        o = y; y = min(h * 0.85, max(h * 0.25, y + r.uniform(-90, 80))); c = y
        hi, lo = min(o, c) - r.uniform(10, 60), max(o, c) + r.uniform(10, 60)
        col = "#18e08a" if c < o else "#ff4d5e"
        x = i * step + step / 2
        out.append(f"<line x1='{x}' x2='{x}' y1='{hi}' y2='{lo}' stroke='{col}' stroke-width='4'/>"
                   f"<rect x='{x - step * .3}' y='{min(o, c)}' width='{step * .6}' height='{max(abs(o - c), 6)}' fill='{col}'/>")
    return f"<svg width='{w}' height='{h}' style='position:absolute;inset:0;opacity:.20'>{''.join(out)}</svg>"


def coin_card(d, logo_path, pal, out, brand):
    """d: symbol, stats[(label,value)x3], caption, sub, sources. pal: a1,a2 (logo colors)"""
    import base64
    e = _h.escape
    b64 = base64.b64encode(open(logo_path, "rb").read()).decode()
    a2c = pal["a2"] if max(pal["a2"]) >= 200 else tuple(min(255, int(v * 0.35 + 255 * 0.65)) for v in pal["a1"])
    a1, a2 = rgb(pal["a1"]), rgb(a2c)
    import os as _os
    cp = pathlib.Path(__file__).parent / "chains" / f"{d.get('chain_key', '')}.png"
    cimg = (f"<img src='data:image/png;base64,{base64.b64encode(cp.read_bytes()).decode()}' "
            f"style='width:54px;height:54px;border-radius:50%;vertical-align:middle;margin-right:12px;border:3px solid #111'>") if cp.exists() else ""
    chain_html = f"<div class='sticker chain'>{cimg}{e(d['chain'].upper())}</div>" if d.get("chain") else ""
    vlabel, vcol = d.get("verdict", ("RISK CHECK", "255,255,255"))
    vtext = "#fff" if vlabel == "HIGH RISK" else "#111"
    copies_html = (f"<div class='sticker copies'>{d['copies']} TOKENS<br>SHARE THIS TICKER</div>" if d.get("copies", 0) > 1 else "")
    tsize = 196 if len(d["symbol"]) <= 5 else max(96, int(196 * 5 / len(d["symbol"])))
    stats = "".join(
        f"<div class='st s{i}'><div class='sl'>{e(k)}</div><div class='sv'>{e(v)}</div></div>" for i, (k, v) in enumerate(d["stats"]))
    doc = f"""<html><head><meta charset='utf-8'><style>{FACES}{_face('Fredoka','FredokaOne-Regular.ttf',400)}{_face('Pop','Poppins-Bold.ttf',700)}{_face('PopM','Poppins-Medium.ttf',500)}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1080px;height:1350px;overflow:hidden;background:#0a0a13;font-family:'Pop';color:#fff;position:relative}}
.burst{{position:absolute;left:-300px;top:-330px;width:1680px;height:1680px;border-radius:50%;
 background:repeating-conic-gradient(from 0deg at 50% 50%,rgba({a1},.30) 0deg 7deg,transparent 7deg 15deg);
 -webkit-mask-image:radial-gradient(circle at 50% 50%,#000 0%,transparent 52%);mask-image:radial-gradient(circle at 50% 50%,#000 0%,transparent 52%);top:-410px}}
.glow{{position:absolute;left:240px;top:80px;width:600px;height:600px;border-radius:50%;background:radial-gradient(circle,rgba({a1},.55),transparent 68%);filter:blur(10px)}}
.wrap{{position:absolute;inset:0;padding:60px 64px}}
.sticker{{display:inline-block;border:6px solid #111;border-radius:16px;box-shadow:9px 9px 0 #000;padding:10px 24px;font-family:'Fredoka';font-size:38px;color:#111}}
.top{{display:flex;justify-content:space-between;align-items:center}}
.brand{{background:rgb({a2});transform:rotate(-3deg)}}
.tag{{background:#fff;transform:rotate(3deg);font-size:28px}}
.logo{{position:absolute;left:340px;top:190px;width:400px;height:400px;border-radius:50%;border:12px solid #fff;box-shadow:16px 16px 0 rgb({a1}),0 0 90px rgba({a1},.7);
 background:url(data:image/png;base64,{b64}) center/cover;transform:rotate(-5deg)}}
.tick{{position:absolute;left:0;right:0;top:612px;text-align:center;font-family:'Pop';font-weight:700;font-size:{tsize}px;line-height:1.05;
 -webkit-text-stroke:16px #000;paint-order:stroke fill;text-shadow:12px 12px 0 #000;letter-spacing:2px}}
.stats{{position:absolute;left:64px;right:64px;top:872px;display:flex;gap:26px}}
.st{{flex:1;border:6px solid #111;border-radius:22px;box-shadow:10px 10px 0 #000;padding:16px 22px 14px;color:#111}}
.s0{{background:rgb({a2});transform:rotate(-2deg)}} .s1{{background:#fff;transform:rotate(1.5deg)}} .s2{{background:rgb({a1});transform:rotate(-1deg)}}
.sl{{font-family:'PopM';font-size:21px;letter-spacing:.05em;text-transform:uppercase}}
.sv{{font-family:'Fredoka';font-size:54px;white-space:nowrap;line-height:1.1;margin-top:4px}}
.cap{{position:absolute;left:40px;right:40px;top:1068px;text-align:center;font-family:'Pop';font-weight:700;font-size:74px;line-height:1.05;text-transform:uppercase;
 -webkit-text-stroke:11px #000;paint-order:stroke fill;text-shadow:7px 7px 0 #000}}
.sub{{position:absolute;left:64px;right:64px;top:1200px;text-align:center;font-family:'PopM';font-size:28px;color:#fff}}
.chain{{position:absolute;left:600px;top:488px;background:#fff;transform:rotate(5deg);font-size:34px;z-index:3;padding:8px 22px 8px 12px}}
.copies{{position:absolute;left:60px;top:470px;background:#ff4d5e;color:#fff;transform:rotate(-6deg);font-size:28px;z-index:3;text-align:center;line-height:1.1}}
.ca{{position:absolute;left:0;right:0;top:1250px;text-align:center}}
.ca span{{display:inline-block;background:#000;border:3px solid rgba(255,255,255,.55);border-radius:30px;padding:8px 26px;font-family:'PopM';font-size:25px;letter-spacing:.03em}}
.foot{{position:absolute;left:64px;right:64px;bottom:16px;display:flex;justify-content:space-between;font-family:'PopM';font-size:17px;color:rgba(255,255,255,.62)}}
</style></head><body>
{_candles_svg()}<div class='burst'></div><div class='glow'></div>
<div class='wrap'><div class='top'><div class='sticker brand'>{e(brand)}</div><div class='sticker tag' style='background:rgb({vcol});color:{vtext}'>{e(vlabel)}</div></div></div>
<div class='logo'></div>
<div class='tick'>${e(d['symbol'])}</div>
{chain_html}{copies_html}
<div class='stats'>{stats}</div>
<div class='cap'>{e(d['caption'])}</div>
<div class='sub'>{e(d['sub'])}</div>
<div class='ca'><span>{e(d.get('chain','')).upper()} &nbsp;|&nbsp; CA: {e(d.get('ca',''))}</span></div>
<div class='foot'><div>{e(d['sources'])}</div><div>Not financial advice. DYOR.</div></div>
</body></html>"""
    return to_png(doc, out)


BRAND_PAL = {"a1": (255, 138, 0), "a2": (255, 214, 10)}


def slide_card(text, i, total, out, brand, pal=None):
    """Instagram carousel slide in the same memecoin-native style."""
    import re as _re
    pal = pal or BRAND_PAL
    e = _h.escape
    a1, a2 = rgb(pal["a1"]), rgb(pal["a2"])
    m = _re.search(r"[-+]?\$?\d[\d,]*\.?\d*[KMB%x]?", text)
    stat = m.group(0) if m and _re.search(r"[$%KMBx]", m.group(0)) else None
    last = i == total - 1
    if i == 0:
        body = f"<div class='big' style='top:330px;font-size:{108 if len(text) < 40 else 88}px'>{e(text.upper())}</div><div class='swipe'>SWIPE &gt;&gt;</div>"
    elif last:
        body = (f"<div class='big' style='top:360px;font-size:78px'>{e(text.upper())}</div>"
                f"<div class='sticker cta'>FOLLOW {e(brand)}</div>")
    else:
        st = f"<div class='statbox'>{e(stat)}</div>" if stat else ""
        body = f"{st}<div class='big' style='top:{690 if stat else 420}px;font-size:{66 if stat else 84}px'>{e(text.upper())}</div>"
    doc = f"""<html><head><meta charset='utf-8'><style>{FACES}{_face('Fredoka','FredokaOne-Regular.ttf',400)}{_face('Pop','Poppins-Bold.ttf',700)}{_face('PopM','Poppins-Medium.ttf',500)}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1080px;height:1350px;overflow:hidden;background:#0a0a13;font-family:'Pop';color:#fff;position:relative}}
.burst{{position:absolute;left:-300px;top:-300px;width:1680px;height:1680px;border-radius:50%;
 background:repeating-conic-gradient(from 0deg at 50% 50%,rgba({a1},.26) 0deg 7deg,transparent 7deg 15deg);
 -webkit-mask-image:radial-gradient(circle at 50% 50%,#000 0%,transparent 50%)}}
.sticker{{display:inline-block;border:6px solid #111;border-radius:16px;box-shadow:9px 9px 0 #000;padding:10px 24px;font-family:'Fredoka';font-size:38px;color:#111}}
.top{{position:absolute;left:64px;right:64px;top:60px;display:flex;justify-content:space-between;align-items:center}}
.brand{{background:rgb({a2});transform:rotate(-3deg)}} .pg{{background:#fff;transform:rotate(3deg);font-size:28px}}
.big{{position:absolute;left:56px;right:56px;text-align:center;font-weight:700;line-height:1.08;
 -webkit-text-stroke:12px #000;paint-order:stroke fill;text-shadow:8px 8px 0 #000}}
.statbox{{position:absolute;left:90px;right:90px;top:250px;text-align:center;background:rgb({a1 if not (stat or '').startswith('-') else '255,77,94'});border:8px solid #111;border-radius:34px;
 box-shadow:14px 14px 0 #000;font-family:'Fredoka';font-size:200px;line-height:1.15;color:#111;transform:rotate(-2deg);padding:10px 0}}
.swipe{{position:absolute;left:0;right:0;top:1180px;text-align:center;font-family:'Fredoka';font-size:44px;color:rgb({a2})}}
.cta{{position:absolute;left:150px;right:150px;top:900px;text-align:center;background:rgb({a2});font-size:54px;transform:rotate(-2deg)}}
.foot{{position:absolute;left:64px;right:64px;bottom:34px;display:flex;justify-content:space-between;font-family:'PopM';font-size:19px;color:rgba(255,255,255,.62)}}
</style></head><body>{_candles_svg(seed=11+i)}<div class='burst'></div>
<div class='top'><div class='sticker brand'>{e(brand)}</div><div class='sticker pg'>{i + 1}/{total}</div></div>
{body}
<div class='foot'><div>Data: DexScreener, RugCheck</div><div>Not financial advice. DYOR.</div></div></body></html>"""
    return to_png(doc, out)


# ---------- Instagram carousel slides 2-4 for a token (slide 1 is the main token card) ----------
def _chain_img(key, size=54):
    cp = pathlib.Path(__file__).parent / "chains" / f"{key}.png"
    if not cp.exists():
        return ""
    import base64
    return (f"<img src='data:image/png;base64,{base64.b64encode(cp.read_bytes()).decode()}' "
            f"style='width:{size}px;height:{size}px;border-radius:50%;vertical-align:middle;margin-right:12px;border:3px solid #111'>")


def token_slide(kind, d, pal, i, total, out, brand):
    """kind: numbers | flags | verify. d: symbol, chain, chain_key, explorer, address, metrics, risk_list, copies."""
    import re as _re
    e = _h.escape
    a2c = pal["a2"] if max(pal["a2"]) >= 200 else tuple(min(255, int(v * 0.35 + 255 * 0.65)) for v in pal["a1"])
    a1, a2 = rgb(pal["a1"]), rgb(a2c)
    head = (f"<div class='tk'>${e(d['symbol'])}</div>"
            f"<div class='sticker chainb'>{_chain_img(d.get('chain_key', ''))}{e(d['chain'].upper())}</div>")
    if kind == "numbers":
        tiles = ""
        for k, (label, value, tone) in enumerate(d["metrics"]):
            bg = [f"rgb({a2})", "#fff", f"rgb({a1})", "#fff"][k % 4]
            col = "#ff3d52" if tone == "red" else "#111"
            tiles += (f"<div class='tile' style='background:{bg};transform:rotate({[-1.6, 1.4, 1.2, -1.2][k % 4]}deg)'>"
                      f"<div class='tl'>{e(label)}</div><div class='tv' style='color:{col}'>{e(value)}</div></div>")
        body = f"<div class='sticker ttl'>THE NUMBERS</div><div class='grid'>{tiles}</div>"
    elif kind == "flags":
        rows = ""
        items = list(d["risk_list"])[:5]
        if d.get("copies", 0) > 1:
            items = [f"{d['copies']} tokens share this ticker (danger)"] + items[:4]
        if not items:
            items = [("No flags found by the scanner (ok)" if d.get("risks_known") else "Security scan unavailable (unknown)")]
        for it in items:
            m = _re.match(r"^(.*?)\s*\((\w+)\)$", it)
            name, lvl = (m.group(1), m.group(2).lower()) if m else (it, "warn")
            col, sym = {"danger": ("#ff3d52", "!"), "warn": ("#ffb020", "!"), "ok": ("#18e08a", "OK")}.get(lvl, ("#9aa0aa", "?"))
            rows += (f"<div class='row'><div class='bd' style='background:{col}'>{sym}</div><div class='rt'>{e(name)}</div></div>")
        body = f"<div class='sticker ttl'>FLAGS FOUND</div><div class='rows'>{rows}</div>"
    else:
        steps = [f"Get the contract from the project's official link, never from a reply or a DM.",
                 f"Open it on {d['explorer']} and check it matches the address below.",
                 "Compare liquidity and holders before you trust the ticker."]
        cards = "".join(f"<div class='step'><div class='sn'>{n}</div><div class='st2'>{e(t)}</div></div>" for n, t in enumerate(steps, 1))
        body = (f"<div class='sticker ttl'>VERIFY FIRST</div><div class='steps'>{cards}</div>"
                f"<div class='cab'><div class='cal'>CONTRACT ON {e(d['chain'].upper())}</div><div class='cav'>{e(d['address'])}</div></div>"
                f"<div class='sticker cta'>FOLLOW {e(brand)}</div>")
    doc = f"""<html><head><meta charset='utf-8'><style>{FACES}{_face('Fredoka','FredokaOne-Regular.ttf',400)}{_face('Pop','Poppins-Bold.ttf',700)}{_face('PopM','Poppins-Medium.ttf',500)}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1080px;height:1350px;overflow:hidden;background:#0a0a13;font-family:'Pop';color:#fff;position:relative}}
.burst{{position:absolute;left:-300px;top:-300px;width:1680px;height:1680px;border-radius:50%;
 background:repeating-conic-gradient(from 0deg at 50% 50%,rgba({a1},.22) 0deg 7deg,transparent 7deg 15deg);
 -webkit-mask-image:radial-gradient(circle at 50% 50%,#000 0%,transparent 50%)}}
.sticker{{display:inline-block;border:6px solid #111;border-radius:16px;box-shadow:9px 9px 0 #000;padding:10px 24px;font-family:'Fredoka';font-size:38px;color:#111}}
.top{{position:absolute;left:64px;right:64px;top:56px;display:flex;justify-content:space-between;align-items:center}}
.brand{{background:rgb({a2});transform:rotate(-3deg)}} .pg{{background:#fff;transform:rotate(3deg);font-size:28px}}
.tk{{position:absolute;left:64px;top:170px;font-family:'Pop';font-weight:700;font-size:118px;line-height:1.1;-webkit-text-stroke:12px #000;paint-order:stroke fill;text-shadow:8px 8px 0 #000}}
.chainb{{position:absolute;right:64px;top:196px;background:#fff;transform:rotate(4deg);font-size:32px;padding:8px 22px 8px 12px}}
.ttl{{position:absolute;left:64px;top:340px;background:rgb({a1});transform:rotate(-2deg);font-size:46px}}
.grid{{position:absolute;left:64px;right:64px;top:470px;display:flex;flex-wrap:wrap;gap:36px}}
.tile{{width:446px;height:300px;border:6px solid #111;border-radius:28px;box-shadow:12px 12px 0 #000;padding:30px 34px;color:#111}}
.tl{{font-family:'PopM';font-size:26px;letter-spacing:.06em}} .tv{{font-family:'Fredoka';font-size:92px;line-height:1.1;margin-top:46px;white-space:nowrap}}
.rows{{position:absolute;left:64px;right:64px;top:470px}}
.row{{display:flex;align-items:center;background:#fff;border:6px solid #111;border-radius:24px;box-shadow:9px 9px 0 #000;margin-bottom:22px;padding:14px 22px;color:#111;height:112px}}
.bd{{width:70px;height:70px;border:5px solid #111;border-radius:16px;display:flex;align-items:center;justify-content:center;font-family:'Fredoka';font-size:38px;margin-right:24px;flex:none}}
.rt{{font-family:'Pop';font-weight:700;font-size:34px;line-height:1.1}}
.steps{{position:absolute;left:64px;right:64px;top:466px}}
.step{{display:flex;align-items:center;background:#fff;border:6px solid #111;border-radius:24px;box-shadow:9px 9px 0 #000;margin-bottom:20px;padding:14px 24px;color:#111;height:128px}}
.sn{{font-family:'Fredoka';font-size:60px;margin-right:24px;color:rgb({a1});-webkit-text-stroke:3px #111;flex:none}}
.st2{{font-family:'PopM';font-size:29px;line-height:1.2}}
.cab{{position:absolute;left:64px;right:64px;top:944px;background:#000;border:4px solid rgba(255,255,255,.6);border-radius:28px;padding:20px 28px}}
.cal{{font-family:'PopM';font-size:21px;letter-spacing:.1em;color:rgb({a2})}} .cav{{font-family:'PopM';font-size:32px;margin-top:8px;word-break:break-all;line-height:1.25}}
.cta{{position:absolute;left:150px;right:150px;top:1150px;text-align:center;background:rgb({a2});font-size:46px;transform:rotate(-2deg)}}
.foot{{position:absolute;left:64px;right:64px;bottom:20px;display:flex;justify-content:space-between;font-family:'PopM';font-size:17px;color:rgba(255,255,255,.62)}}
</style></head><body><div class='burst'></div>
<div class='top'><div class='sticker brand'>{e(brand)}</div><div class='sticker pg'>{i + 1}/{total}</div></div>
{head}{body}
<div class='foot'><div>Data: DexScreener, RugCheck / GoPlus</div><div>Not financial advice. DYOR.</div></div></body></html>"""
    return to_png(doc, out)


def avatar_card(out, brand_pal=None):
    """1080x1080 profile picture: flame + question mark on the brand burst. Safe inside a circular crop."""
    pal = brand_pal or BRAND_PAL
    a1, a2 = rgb(pal["a1"]), rgb(pal["a2"])
    doc = f"""<html><head><meta charset='utf-8'><style>{_face('Fredoka','FredokaOne-Regular.ttf',400)}
*{{margin:0;padding:0}}
body{{width:1080px;height:1080px;overflow:hidden;background:#0a0a13;position:relative}}
.burst{{position:absolute;left:-300px;top:-300px;width:1680px;height:1680px;border-radius:50%;
 background:repeating-conic-gradient(from 0deg at 50% 50%,rgba({a1},.55) 0deg 8deg,transparent 8deg 18deg);
 -webkit-mask-image:radial-gradient(circle at 50% 50%,#000 0%,transparent 60%)}}
.glow{{position:absolute;left:190px;top:190px;width:700px;height:700px;border-radius:50%;background:radial-gradient(circle,rgba({a1},.7),transparent 68%)}}
svg{{position:absolute;left:250px;top:90px;width:580px;height:754px;filter:drop-shadow(14px 14px 0 #000)}}
.q{{position:absolute;left:0;right:0;top:230px;text-align:center;font-family:'Fredoka';font-size:600px;line-height:1;color:#fff;
 -webkit-text-stroke:34px #000;paint-order:stroke fill;text-shadow:18px 18px 0 #000;transform:rotate(-6deg)}}
</style></head><body><div class='burst'></div><div class='glow'></div>
<svg viewBox='0 0 200 260'>
<path d='M100 8 C112 52 168 82 168 150 C168 208 136 250 100 250 C64 250 32 208 32 150 C32 118 48 98 62 80 C64 104 78 112 88 108 C80 76 92 40 100 8 Z' fill='rgb({a1})' stroke='#000' stroke-width='9' stroke-linejoin='round'/>
<path d='M100 110 C108 138 140 152 140 188 C140 222 122 242 100 242 C78 242 60 222 60 188 C60 160 80 150 90 132 C92 148 98 150 104 148 C98 134 98 122 100 110 Z' fill='rgb({a2})'/>
</svg>
<div class='q'>?</div></body></html>"""
    return to_png(doc, out, 1080, 1080)


# ---------- Instagram Stories (1080x1920; keep content inside the safe zone y 250-1600) ----------
STORY_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{width:1080px;height:1920px;overflow:hidden;background:#0a0a13;font-family:'Pop';color:#fff;position:relative}
.burst{position:absolute;left:-400px;top:-200px;width:1880px;height:1880px;border-radius:50%;
 background:repeating-conic-gradient(from 0deg at 50% 50%,rgba(A1,.24) 0deg 7deg,transparent 7deg 15deg);
 -webkit-mask-image:radial-gradient(circle at 50% 50%,#000 0%,transparent 50%)}
.sticker{display:inline-block;border:6px solid #111;border-radius:16px;box-shadow:9px 9px 0 #000;padding:10px 24px;font-family:'Fredoka';font-size:38px;color:#111}
.cap{font-family:'Pop';font-weight:700;text-transform:uppercase;-webkit-text-stroke:12px #000;paint-order:stroke fill;text-shadow:8px 8px 0 #000;line-height:1.08}
"""


def _story_head(brand, a2, right_text, right_bg="#fff"):
    return (f"<div style='position:absolute;left:64px;right:64px;top:270px;display:flex;justify-content:space-between;align-items:center'>"
            f"<div class='sticker' style='background:rgb({a2});transform:rotate(-3deg)'>{_h.escape(brand)}</div>"
            f"<div class='sticker' style='background:{right_bg};transform:rotate(3deg);font-size:30px'>{_h.escape(right_text)}</div></div>")


def story_tip_card(d, out, brand, pal=None):
    """d: n, title, body"""
    pal = pal or BRAND_PAL
    a1, a2 = rgb(pal["a1"]), rgb(pal["a2"])
    e = _h.escape
    tsize = 104 if len(d["title"]) < 26 else 86
    doc = f"""<html><head><meta charset='utf-8'><style>{FACES}{_face('Fredoka','FredokaOne-Regular.ttf',400)}{_face('Pop','Poppins-Bold.ttf',700)}{_face('PopM','Poppins-Medium.ttf',500)}
{STORY_CSS.replace('A1', a1)}
.wrap{{position:absolute;left:64px;right:64px;top:520px}}
.title{{font-size:{tsize}px;text-align:center}}
.card{{margin-top:64px;background:#fff;border:6px solid #111;border-radius:30px;box-shadow:14px 14px 0 #000;padding:44px 46px;color:#111;
 font-family:'PopM';font-size:44px;line-height:1.3;transform:rotate(-1.2deg)}}
.cta{{position:absolute;left:110px;right:110px;top:1450px;text-align:center;background:rgb({a2});font-size:40px;transform:rotate(-2deg)}}
.foot{{position:absolute;left:64px;right:64px;top:1560px;text-align:center;font-family:'PopM';font-size:24px;color:rgba(255,255,255,.7)}}
</style></head><body>{_candles_svg(1080, 1920, seed=21 + d['n'])}<div class='burst'></div>
{_story_head(brand, a2, f"TIP #{d['n']}", f"rgb({a1})")}
<div class='wrap'><div class='cap title'>{e(d['title'])}</div><div class='card'>{e(d['body'])}</div></div>
<div class='sticker cta'>FOLLOW FOR VERIFIED SIGNALS</div>
<div class='foot'>Not financial advice. DYOR.</div></body></html>"""
    return to_png(doc, out, 1080, 1920)


def story_radar_card(d, logo_path, pal, out, brand):
    """d: symbol, chain, chain_key, verdict (label, 'r,g,b'), stats [(label,value)x3], ca, updated"""
    import base64
    a1, a2c = pal["a1"], (pal["a2"] if max(pal["a2"]) >= 200 else tuple(min(255, int(v * 0.35 + 255 * 0.65)) for v in pal["a1"]))
    a1s, a2 = rgb(a1), rgb(a2c)
    e = _h.escape
    b64 = base64.b64encode(open(logo_path, "rb").read()).decode()
    vlabel, vcol = d["verdict"]
    vtext = "#fff" if vlabel == "HIGH RISK" else "#111"
    tsize = 150 if len(d["symbol"]) <= 5 else max(80, int(150 * 5 / len(d["symbol"])))
    stats = "".join(f"<div class='st' style='background:{['rgb('+a2+')', '#fff', 'rgb('+a1s+')'][i]}'><div class='sl'>{e(k)}</div><div class='sv'>{e(v)}</div></div>"
                    for i, (k, v) in enumerate(d["stats"]))
    doc = f"""<html><head><meta charset='utf-8'><style>{FACES}{_face('Fredoka','FredokaOne-Regular.ttf',400)}{_face('Pop','Poppins-Bold.ttf',700)}{_face('PopM','Poppins-Medium.ttf',500)}
{STORY_CSS.replace('A1', a1s)}
.glow{{position:absolute;left:240px;top:430px;width:600px;height:600px;border-radius:50%;background:radial-gradient(circle,rgba({a1s},.55),transparent 68%)}}
.logo{{position:absolute;left:350px;top:470px;width:380px;height:380px;border-radius:50%;border:12px solid #fff;box-shadow:16px 16px 0 rgb({a1s});
 background:url(data:image/png;base64,{b64}) center/cover;transform:rotate(-5deg)}}
.chainb{{position:absolute;left:610px;top:770px;background:#fff;transform:rotate(5deg);font-size:32px;padding:8px 22px 8px 12px;z-index:3}}
.tick{{position:absolute;left:0;right:0;top:880px;text-align:center;font-size:{tsize}px}}
.ver{{position:absolute;left:0;right:0;top:1090px;text-align:center}}
.stats{{position:absolute;left:64px;right:64px;top:1190px;display:flex;gap:22px}}
.st{{flex:1;border:6px solid #111;border-radius:22px;box-shadow:9px 9px 0 #000;padding:14px 18px;color:#111}}
.sl{{font-family:'PopM';font-size:20px;letter-spacing:.05em;text-transform:uppercase}}
.sv{{font-family:'Fredoka';font-size:46px;line-height:1.15;white-space:nowrap}}
.sub{{position:absolute;left:64px;right:64px;top:1380px;text-align:center;font-family:'PopM';font-size:32px}}
.ca{{position:absolute;left:0;right:0;top:1450px;text-align:center}}
.ca span{{display:inline-block;background:#000;border:3px solid rgba(255,255,255,.55);border-radius:30px;padding:8px 24px;font-family:'PopM';font-size:25px}}
.foot{{position:absolute;left:64px;right:64px;top:1535px;text-align:center;font-family:'PopM';font-size:22px;color:rgba(255,255,255,.72);line-height:1.35}}
</style></head><body>{_candles_svg(1080, 1920, seed=5)}<div class='burst'></div><div class='glow'></div>
{_story_head(brand, a2, "RADAR UPDATE")}
<div class='logo'></div><div class='sticker chainb'>{_chain_img(d.get('chain_key', ''))}{e(d['chain'].upper())}</div>
<div class='cap tick'>${e(d['symbol'])}</div>
<div class='ver'><div class='sticker' style='background:rgb({vcol});color:{vtext};font-size:40px;transform:rotate(-2deg)'>{e(vlabel)}</div></div>
<div class='stats'>{stats}</div>
<div class='sub'>Promising momentum filtered by market strength + security</div>
<div class='ca'><span>{e(d['chain'].upper())} &nbsp;|&nbsp; CA: {e(d['ca'])}</span></div>
<div class='foot'>Scanned {e(d['updated'])}. This is not a recommendation.<br>Not financial advice. DYOR.</div></body></html>"""
    return to_png(doc, out, 1080, 1920)


def story_promo_card(kind, d, out, brand, pal=None):
    """Brand / hype stories that never reveal the token of the day. kinds: drop | counter | whatwedo | teaser | quiz"""
    pal = pal or BRAND_PAL
    a1, a2 = rgb(pal["a1"]), rgb(pal["a2"])
    e = _h.escape
    card = lambda t, rot=-1.2: (f"<div class='card' style='transform:rotate({rot}deg)'>{e(t)}</div>")
    if kind == "drop":
        title = "TODAY'S SIGNAL IS LIVE"
        body = (f"<div class='sticker big' style='background:rgb({a1});transform:rotate(-2deg)'>GO SEE IT ON THE FEED</div>"
                + card("Full breakdown: momentum, liquidity, scanner flags and the verified contract."))
    elif kind == "counter":
        title = "THE RADAR IN NUMBERS"
        body = (f"<div class='tile' style='background:rgb({a2});transform:rotate(-2deg)'><div class='n'>{d['scanned']}</div><div class='l'>TOKENS SCANNED</div></div>"
                f"<div class='tile' style='background:#fff;transform:rotate(1.6deg)'><div class='n'>{d['checks']}</div><div class='l'>SIGNALS DOCUMENTED</div></div>"
                + card("Every signal keeps its contract and outcome. No deleted losses."))
    elif kind == "whatwedo":
        title = "WHAT WE DO"
        body = "".join(card(t, rot) for t, rot in (("We scan emerging memecoins every day.", -1.4),
                                                   ("We filter momentum, liquidity and security.", 1.2),
                                                   ("Every signal gets a public receipt.", -1.0)))
    elif kind == "teaser":
        title = "NEXT RISK CHECK DROPS TOMORROW"
        body = (f"<div class='tile' style='background:rgb({a1});transform:rotate(-2deg)'><div class='n' style='font-size:130px'>~17:00 UTC</div></div>"
                + card("One token. The full breakdown. Contract included.")
                + card("Follow and turn on post notifications so you do not miss it.", 1.2))
    else:  # quiz
        title = "COOKED OR NOT?"
        chips = "".join(f"<div class='chip'><span>{e(k)}</span> {e(v)}</div>" for k, v in
                        (("LIQUIDITY", "SMALL"), ("VOLUME", "HUGE"), ("AGE", "3 HOURS")))
        body = (f"<div class='sticker' style='background:#fff;font-size:30px;transform:rotate(-1.5deg)'>EXAMPLE SETUP, NOT A REAL TOKEN</div>{chips}"
                f"<div class='btns'><div class='btn' style='background:#ff4d5e'>COOKED</div><div class='btn' style='background:#18e08a'>NOT COOKED</div></div>"
                + card("Reply with your pick. We explain it in the next check.", 1.0))
    tsize = 100 if len(title) < 24 else 82
    doc = f"""<html><head><meta charset='utf-8'><style>{FACES}{_face('Fredoka','FredokaOne-Regular.ttf',400)}{_face('Pop','Poppins-Bold.ttf',700)}{_face('PopM','Poppins-Medium.ttf',500)}
{STORY_CSS.replace('A1', a1)}
.wrap{{position:absolute;left:64px;right:64px;top:430px;display:flex;flex-direction:column;gap:26px}}
.title{{font-size:{tsize}px;text-align:center;margin-bottom:6px}}
.card{{background:#fff;border:6px solid #111;border-radius:28px;box-shadow:12px 12px 0 #000;padding:24px 34px;color:#111;font-family:'PopM';font-size:38px;line-height:1.28}}
.big{{font-size:44px;text-align:center;padding:18px 26px}}
.tile{{border:6px solid #111;border-radius:30px;box-shadow:12px 12px 0 #000;padding:16px 26px;color:#111;text-align:center}}
.n{{font-family:'Fredoka';font-size:118px;line-height:1.05}} .l{{font-family:'Pop';font-weight:700;font-size:30px;margin-top:2px}}
.chip{{background:rgb({a2});border:5px solid #111;border-radius:20px;box-shadow:8px 8px 0 #000;padding:12px 26px;color:#111;font-family:'Pop';font-weight:700;font-size:34px}}
.chip span{{font-family:'PopM';font-weight:500;margin-right:14px}}
.btns{{display:flex;gap:26px}} .btn{{flex:1;text-align:center;border:6px solid #111;border-radius:24px;box-shadow:9px 9px 0 #000;padding:18px 10px;color:#111;font-family:'Fredoka';font-size:44px}}
.cta{{position:absolute;left:110px;right:110px;top:1495px;text-align:center;background:rgb({a2});font-size:36px;transform:rotate(-2deg)}}
.foot{{position:absolute;left:64px;right:64px;top:1590px;text-align:center;font-family:'PopM';font-size:22px;color:rgba(255,255,255,.7)}}
</style></head><body>{_candles_svg(1080, 1920, seed=33)}<div class='burst'></div>
{_story_head(brand, a2, "RADAR", f"rgb({a1})")}
<div class='wrap'><div class='cap title'>{e(title)}</div>{body}</div>
<div class='sticker cta'>LIVE RADAR · LINK IN BIO</div>
<div class='foot'>Not financial advice. DYOR.</div></body></html>"""
    return to_png(doc, out, 1080, 1920)
