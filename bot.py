"""Memecoin Risk Radar bot. Jobs: brief | alert | scoreboard | education | ig_prepare | ig_publish
Run: python bot.py <job>   (DRY_RUN=true prints instead of publishing)
"""
import os, sys, json, re, time, pathlib, datetime as dt
import requests

ROOT = pathlib.Path(__file__).parent
STATE, PUBLIC, HELD = ROOT / "state", ROOT / "public", ROOT / "held"
for d in (STATE, PUBLIC, HELD):
    d.mkdir(exist_ok=True)

BRAND = os.getenv("BRAND") or "Is It Cooked"
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
DISCLOSURE = os.getenv("DISCLOSURE", "")
DS = "https://api.dexscreener.com"
FOOTER = "Not financial advice. DYOR."
BANNED = re.compile(r"\b(buy|sell|100x|1000x|gem|guaranteed|moon)\b", re.I)
TOPICS = [
    "How to read a token's holder list and spot concentration",
    "What liquidity is and why low liquidity makes exits impossible",
    "Mint and freeze authority: what they are and why they matter",
    "Bundled launches: how insiders can control supply at launch",
    "Volume vs. liquidity: spotting wash trading",
    "What an LP lock does and what it does not protect against",
    "Why token age matters when judging risk",
]


# ---------- helpers ----------
def load(name, default):
    p = STATE / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else default


def save(name, obj):
    (STATE / f"{name}.json").write_text(json.dumps(obj, indent=1))


def http_get(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def fmt_usd(x):
    x = float(x or 0)
    for d, s in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= d:
            return f"${x / d:.1f}{s}"
    return f"${x:.2f}"


def fmt_price(x):
    x = float(x)
    return f"${x:.8f}".rstrip("0") if x < 0.001 else f"${x:.6g}"


# ---------- data ----------
def best_pair(chain, addr):
    pairs = http_get(f"{DS}/latest/dex/tokens/{addr}").get("pairs") or []
    pairs = [p for p in pairs if p.get("chainId") == chain]
    return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0), default=None)


def rugcheck(addr):
    try:
        j = http_get(f"https://api.rugcheck.xyz/v1/tokens/{addr}/report/summary")
        risks = [f"{r.get('name')} ({r.get('level')})" for r in j.get("risks", [])][:6]
        return risks
    except Exception as e:
        print("rugcheck unavailable:", e)
        return None


def build_data(pair, risks):
    b, liq = pair["baseToken"], (pair.get("liquidity") or {}).get("usd", 0)
    vol = (pair.get("volume") or {}).get("h24", 0)
    tx = (pair.get("txns") or {}).get("h24", {})
    age_h = (time.time() * 1000 - (pair.get("pairCreatedAt") or 0)) / 3.6e6
    age = f"{age_h:.0f} hours" if age_h < 72 else f"{age_h / 24:.0f} days"
    facts = [
        f"Token: {b['symbol']} on {pair['chainId']}",
        f"Liquidity: {fmt_usd(liq)}",
        f"24h volume: {fmt_usd(vol)}",
        f"24h price change: {(pair.get('priceChange') or {}).get('h24', 0):+.1f}%",
        f"Pair age: {age}",
        f"FDV: {fmt_usd(pair.get('fdv'))}",
        f"24h transactions: {tx.get('buys', 0)} buys / {tx.get('sells', 0)} sells",
    ]
    if liq:
        facts.append(f"Volume to liquidity ratio: {vol / liq:.1f}x")
    facts.append("Risk flags (RugCheck): " + ("; ".join(risks) if risks else "none reported or unavailable"))
    chg = (pair.get("priceChange") or {}).get("h24", 0)
    metrics = [("LIQUIDITY", fmt_usd(liq), None), ("24H VOLUME", fmt_usd(vol), None), ("PAIR AGE", age, None),
               ("24H CHANGE", f"{chg:+.1f}%", "red" if chg < 0 else "green")]
    return {
        "metrics": metrics, "risks_known": risks is not None,
        "logo": (pair.get("info") or {}).get("imageUrl"), "pool": pair.get("pairAddress"),
        "token": {"symbol": b["symbol"], "name": b["name"], "chain": pair["chainId"],
                  "address": b["address"], "url": pair.get("url")},
        "price": pair.get("priceUsd"), "liquidity": liq, "risk_count": len(risks or []),
        "facts": facts,
    }


def candidates(limit=15):
    out = http_get(f"{DS}/token-boosts/latest/v1")
    return [c for c in out if c.get("chainId") == "solana"][:limit]


def pick_alert_data():
    seen = {h["address"] for h in load("history", [])}
    for c in candidates():
        if c["tokenAddress"] in seen:
            continue
        pair = best_pair(c["chainId"], c["tokenAddress"])
        if not pair or (pair.get("liquidity") or {}).get("usd", 0) < 5000:
            continue
        return build_data(pair, rugcheck(c["tokenAddress"]))
    return None


def brief_data(n=5):
    rows = []
    for c in candidates(25):
        pair = best_pair(c["chainId"], c["tokenAddress"])
        if pair and (pair.get("liquidity") or {}).get("usd", 0) >= 5000:
            rows.append(build_data(pair, None))
        if len(rows) == n:
            break
    facts = [f"{r['token']['symbol']}: " + " | ".join(r["facts"][1:6]) for r in rows]
    return {"date": dt.date.today().isoformat(), "facts": facts} if rows else None


# ---------- generation + validation ----------
def generate(job, data, fmt, platform):
    from anthropic import Anthropic
    user = (f"JOB: {job}\nPLATFORM: {platform}\nFORMAT: {fmt}\nDISCLOSURE: {DISCLOSURE}\n"
            f"RULES: quote numbers exactly as written in DATA.facts and add no other numbers. "
            f"If a token is named, end with '{FOOTER}'. For alerts also return image_caption (max 5 words, meme-caption style, no numbers) "
            f"and image_sub (max 10 words, no numbers).\nDATA:\n{json.dumps(data, ensure_ascii=False)}\n"
            f"Return only the JSON object.")
    msg = Anthropic().messages.create(model=MODEL, max_tokens=1800, system=(ROOT / "master_prompt.md").read_text().replace("{BRAND}", BRAND),
                                      messages=[{"role": "user", "content": user}])
    m = re.search(r"\{.*\}", msg.content[0].text, re.S)
    return json.loads(m.group(0))


def all_text(out):
    t = out.get("text", "")
    parts = t if isinstance(t, list) else [t]
    return parts + out.get("slides", []) + [out.get("caption", ""), out.get("image_caption", ""), out.get("image_sub", "")]


def validate(out, data):
    problems, parts = [], all_text(out)
    joined = " ".join(parts)
    if BANNED.search(joined):
        problems.append("banned word: " + BANNED.search(joined).group(0))
    facts = " ".join(data.get("facts", [])).replace(",", "")
    for num in re.findall(r"\d+(?:\.\d+)?", joined.replace(",", "")):
        if num not in facts and not (num.isdigit() and int(num) <= 10):
            problems.append(f"number not in data: {num}")
    if out.get("platform") == "x" and any(len(p) > 280 for p in (out["text"] if isinstance(out["text"], list) else [out["text"]])):
        problems.append("post over 280 chars")
    if len(out.get("image_caption", "")) > 40 or len(out.get("image_sub", "")) > 80:
        problems.append("image text too long")
    if data.get("token") and FOOTER not in joined:
        problems.append("missing footer")
    if DISCLOSURE and DISCLOSURE not in joined:
        problems.append("missing disclosure")
    return problems


def hold(job, out, problems):
    name = f"{dt.datetime.utcnow():%Y%m%d_%H%M}_{job}.json"
    (HELD / name).write_text(json.dumps({"problems": problems, "output": out}, indent=1))
    print("HELD:", problems)


# ---------- publishing ----------
def publish_x(parts, image=None):
    import tweepy
    c = tweepy.Client(consumer_key=os.environ["X_API_KEY"], consumer_secret=os.environ["X_API_SECRET"],
                      access_token=os.environ["X_ACCESS_TOKEN"], access_token_secret=os.environ["X_ACCESS_SECRET"])
    media_ids = None
    if image:
        try:
            auth = tweepy.OAuth1UserHandler(os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
                                            os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_SECRET"])
            media_ids = [tweepy.API(auth).media_upload(image).media_id]
        except Exception as e:
            print("image upload failed, posting text only:", e)
    prev = None
    for i, p in enumerate(parts):
        r = c.create_tweet(text=p, in_reply_to_tweet_id=prev, media_ids=media_ids if i == 0 else None)
        prev = r.data["id"]
        time.sleep(2)


def deliver_x(job, out, data, image_fn=None):
    problems = validate(out, data)
    if problems:
        hold(job, out, problems)
        return False
    parts = out["text"] if isinstance(out["text"], list) else [out["text"]]
    image = image_fn(out) if image_fn else None
    if DRY_RUN:
        print("[DRY RUN X]\n" + "\n---\n".join(parts) + f"\n[image: {image}]")
    else:
        publish_x(parts, image)
    return True


import design, render_html
from PIL import Image, ImageDraw, ImageFont


def get_logo(data, name):
    """Download the token logo (DexScreener imageUrl); fall back to a letter coin."""
    path = STATE / f"logo_{name}.png"
    url = data.get("logo")
    try:
        if url and os.path.exists(url):
            path.write_bytes(open(url, "rb").read())
        elif url:
            r = requests.get(url, timeout=15); r.raise_for_status(); path.write_bytes(r.content)
        else:
            raise ValueError("no logo")
        Image.open(path).convert("RGBA").save(path, "PNG")
        return str(path)
    except Exception as e:
        print("logo unavailable, using letter coin:", e)
    im = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, 0, 511, 511], fill=(255, 138, 0)); d.ellipse([40, 40, 471, 471], fill=(255, 196, 0))
    f = ImageFont.truetype(str(ROOT / "fonts" / "Poppins-Bold.ttf"), 260)
    ch = data["token"]["symbol"][:1].upper()
    d.text((256, 270), ch, font=f, fill=(255, 255, 255), anchor="mm", stroke_width=10, stroke_fill=(20, 20, 20))
    im.save(path)
    return str(path)


def render_alert_card(data, out, prefix):
    logo = get_logo(data, prefix)
    pal = design.logo_palette(Image.open(logo))
    m = {k: v for k, v, _ in data["metrics"]}
    third = ("RugCheck flags", f"{data['risk_count']} flagged") if data["risks_known"] else ("Pair age", m["PAIR AGE"])
    d = {"symbol": data["token"]["symbol"].upper()[:12],
         "stats": [("Liquidity", m["LIQUIDITY"]), ("24h volume", m["24H VOLUME"]), third],
         "caption": out.get("image_caption") or "Check before you trust", "sub": out.get("image_sub", ""),
         "sources": "Data: DexScreener, RugCheck"}
    return render_html.coin_card(d, logo, pal, PUBLIC / f"{prefix}_card.png", BRAND.upper())


def render_carousel(slides, prefix):
    files = []
    for i, t in enumerate(slides):
        name = f"{prefix}_{i + 1}.png"
        render_html.slide_card(t, i, len(slides), PUBLIC / name, BRAND.upper())
        files.append(name)
    return files


def ig_publish(urls, caption):
    G, uid, tok = "https://graph.facebook.com/v21.0", os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
    kids = []
    for u in urls:
        r = requests.post(f"{G}/{uid}/media", data={"image_url": u, "is_carousel_item": "true", "access_token": tok}, timeout=30)
        r.raise_for_status(); kids.append(r.json()["id"])
    r = requests.post(f"{G}/{uid}/media", data={"media_type": "CAROUSEL", "children": ",".join(kids),
                                                "caption": caption, "access_token": tok}, timeout=30)
    r.raise_for_status(); cid = r.json()["id"]
    time.sleep(15)
    requests.post(f"{G}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok}, timeout=30).raise_for_status()


# ---------- jobs ----------
def job_alert():
    data = pick_alert_data()
    if not data:
        return print("no new candidate")
    out = generate("alert", data, "post", "x")
    prefix = re.sub(r"\W", "", f"x_{dt.date.today():%Y%m%d}_{data['token']['symbol'][:8]}")
    if deliver_x("alert", out, data, lambda o: render_alert_card(data, o, prefix)) and not DRY_RUN:
        h = load("history", [])
        t = data["token"]
        h.append({"symbol": t["symbol"], "address": t["address"], "chain": t["chain"], "price": data["price"],
                  "risk_count": data["risk_count"], "ts": time.time()})
        save("history", h)


def job_brief():
    data = brief_data()
    if not data:
        return print("no data")
    deliver_x("brief", generate("brief", data, "post", "x"), data)


def job_scoreboard():
    week = [h for h in load("history", []) if time.time() - h["ts"] < 7 * 86400]
    facts = []
    for h in week:
        try:
            pair = best_pair(h["chain"], h["address"])
            now = float(pair["priceUsd"]) if pair else None
        except Exception:
            now = None
        if now and float(h["price"] or 0):
            chg = (now / float(h["price"]) - 1) * 100
            facts.append(f"{h['symbol']}: price at alert {fmt_price(h['price'])}, now {fmt_price(now)} ({chg:+.0f}%), "
                         f"risk flags at alert: {h['risk_count']}")
        else:
            facts.append(f"{h['symbol']}: current data unavailable (liquidity may be gone)")
    if not facts:
        return print("no history yet")
    data = {"week": dt.date.today().isoformat(), "facts": facts}
    deliver_x("scoreboard", generate("scoreboard", data, "thread", "x"), data)


def job_education():
    topic = TOPICS[dt.date.today().toordinal() % len(TOPICS)]
    data = {"topic": topic, "facts": []}
    deliver_x("education", generate("education", data, "post", "x"), data)


def job_ig_prepare():
    for f in PUBLIC.glob("*.png"):
        if time.time() - f.stat().st_mtime > 14 * 86400:
            f.unlink()
    data = brief_data()
    if not data:
        return print("no data")
    out = generate("brief", data, "carousel", "instagram")
    problems = validate(out, data)
    if problems:
        return hold("ig", out, problems)
    files = render_carousel(out["slides"], f"ig_{dt.date.today():%Y%m%d}")
    save("ig_next", {"files": files, "caption": out.get("caption", "")})
    print("prepared", files)


def job_ig_publish():
    nxt = load("ig_next", None)
    if not nxt:
        return print("nothing prepared")
    base = f"https://raw.githubusercontent.com/{os.environ.get('GITHUB_REPOSITORY', 'USER/REPO')}/main/public/"
    if DRY_RUN:
        print("[DRY RUN IG]", nxt["caption"], [base + f for f in nxt["files"]])
    else:
        ig_publish([base + f for f in nxt["files"]], nxt["caption"])
    (STATE / "ig_next.json").unlink()


JOBS = {"alert": job_alert, "brief": job_brief, "scoreboard": job_scoreboard, "education": job_education,
        "ig_prepare": job_ig_prepare, "ig_publish": job_ig_publish}

if __name__ == "__main__":
    JOBS[sys.argv[1]]()
