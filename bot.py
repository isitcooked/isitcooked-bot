"""Memecoin Risk Radar bot. Jobs: brief | alert | scoreboard | education | ig_prepare | ig_publish
Run: python bot.py <job>   (DRY_RUN=true prints instead of publishing)
"""
import os, sys, json, re, time, random, pathlib, datetime as dt
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
CHAINS = [c.strip() for c in (os.getenv("CHAINS") or "solana,bsc,ethereum,base").split(",") if c.strip()]
CHAIN_NAMES = {"solana": "Solana", "bsc": "BNB Chain", "ethereum": "Ethereum", "base": "Base", "arbitrum": "Arbitrum",
               "polygon": "Polygon", "avalanche": "Avalanche", "optimism": "Optimism"}
EVM_IDS = {"ethereum": 1, "bsc": 56, "base": 8453, "arbitrum": 42161, "polygon": 137, "avalanche": 43114, "optimism": 10}
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


def goplus_risks(chain, addr):
    """EVM contract checks via GoPlus (free). Returns list of flag names, or None if unavailable."""
    cid = EVM_IDS.get(chain)
    if not cid:
        return None
    try:
        j = http_get(f"https://api.gopluslabs.io/api/v1/token_security/{cid}?contract_addresses={addr}")
        t = (j.get("result") or {}).get(addr.lower())
        if not t:
            return None
        risks = []
        if t.get("is_honeypot") == "1": risks.append("Honeypot (danger)")
        if t.get("is_open_source") == "0": risks.append("Contract not verified (warn)")
        if t.get("hidden_owner") == "1": risks.append("Hidden owner (danger)")
        if t.get("is_mintable") == "1": risks.append("Mintable supply (warn)")
        if t.get("can_take_back_ownership") == "1": risks.append("Ownership can be reclaimed (warn)")
        if float(t.get("sell_tax") or 0) > 0.10: risks.append("High sell tax (warn)")
        if float(t.get("buy_tax") or 0) > 0.10: risks.append("High buy tax (warn)")
        return risks
    except Exception as e:
        print("goplus unavailable:", e)
        return None


def count_copies(sym):
    """How many different tokens share this ticker (DexScreener search, first results)."""
    try:
        pairs = http_get(f"{DS}/latest/dex/search?q={sym}").get("pairs") or []
        return len({(p["chainId"], p["baseToken"]["address"]) for p in pairs
                    if p["baseToken"]["symbol"].upper() == sym.upper()})
    except Exception as e:
        print("copies lookup failed:", e)
        return 0


def short_ca(a):
    return a if len(a) <= 16 else f"{a[:8]}...{a[-8:]}"


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
    facts.append(f"Chain: {CHAIN_NAMES.get(pair['chainId'], pair['chainId'])}")
    if risks is None:
        facts.append("Security scan: unavailable for this token (not the same as no flags)")
    elif not risks:
        facts.append("Security scan: no flags reported by the scanner")
    else:
        facts.append("Scanner flags: " + "; ".join(risks))
    chg = (pair.get("priceChange") or {}).get("h24", 0)
    metrics = [("LIQUIDITY", fmt_usd(liq), None), ("24H VOLUME", fmt_usd(vol), None), ("PAIR AGE", age, None),
               ("24H CHANGE", f"{chg:+.1f}%", "red" if chg < 0 else "green")]
    return {
        "metrics": metrics, "risks_known": risks is not None, "volume": vol, "age_h": age_h, "risk_list": list(risks or []),
        "logo": (pair.get("info") or {}).get("imageUrl"), "pool": pair.get("pairAddress"),
        "token": {"symbol": b["symbol"], "name": b["name"], "chain": pair["chainId"],
                  "chain_name": CHAIN_NAMES.get(pair["chainId"], pair["chainId"].title()),
                  "address": b["address"], "url": pair.get("url")},
        "price": pair.get("priceUsd"), "liquidity": liq, "risk_count": len(risks or []),
        "facts": facts,
    }


def candidates(limit=60):
    """Unique tokens from several DexScreener lists (latest boosts, latest profiles, top boosts)."""
    seen_a, out = set(), []
    for ep in ("token-boosts/latest/v1", "token-profiles/latest/v1", "token-boosts/top/v1"):
        try:
            items = http_get(f"{DS}/{ep}")
        except Exception as e:
            print(f"list {ep} unavailable:", e)
            continue
        for c in items:
            if c.get("chainId") in CHAINS and c.get("tokenAddress") and c["tokenAddress"] not in seen_a:
                seen_a.add(c["tokenAddress"])
                out.append(c)
    return out[:limit]


def pick_alert_data():
    seen = {h["address"] for h in load("history", [])} | set(load("seen", []))
    cands = candidates()
    random.shuffle(cands)
    print(f"candidates: {len(cands)} unique tokens, {len(seen)} already used")
    skipped = {"used": 0, "no pair": 0, "low liquidity": 0, "error": 0}
    for c in cands:
        if c["tokenAddress"] in seen:
            skipped["used"] += 1
            continue
        try:
            pair = best_pair(c["chainId"], c["tokenAddress"])
        except Exception as e:
            skipped["error"] += 1
            continue
        if not pair:
            skipped["no pair"] += 1
            continue
        if (pair.get("liquidity") or {}).get("usd", 0) < 5000:
            skipped["low liquidity"] += 1
            continue
        risks = rugcheck(c["tokenAddress"]) if c["chainId"] == "solana" else goplus_risks(c["chainId"], c["tokenAddress"])
        data = build_data(pair, risks)
        data["copies"] = count_copies(data["token"]["symbol"])
        if data["copies"] > 1:
            data["facts"].append(f"Tokens sharing this ticker (DexScreener search, first results): {data['copies']}")
        print(f"PICKED: {data['token']['symbol']} on {data['token']['chain']} ({data['token']['address']}) | skipped: {skipped}")
        return data
    print("nothing picked | skipped:", skipped)
    return None


def brief_data(n=5):
    rows = []
    for c in candidates(40):
        pair = best_pair(c["chainId"], c["tokenAddress"])
        if pair and (pair.get("liquidity") or {}).get("usd", 0) >= 5000:
            rows.append(build_data(pair, None))
        if len(rows) == n:
            break
    facts = [f"{r['token']['symbol']}: " + " | ".join(r["facts"][1:6]) for r in rows]
    return {"date": dt.date.today().isoformat(), "facts": facts} if rows else None


# ---------- generation + validation ----------
def mock_generate(job, data, fmt, platform):
    """Free test mode (no ANTHROPIC_API_KEY): template text built only from the real data."""
    print("[MOCK AI: no ANTHROPIC_API_KEY set, using template text. Real posts need the key.]")
    f = data.get("facts", [])
    if job == "alert":
        t = data["token"]["symbol"]
        fv = lambda p: next((x for x in f if x.startswith(p)), p)
        txt = [f"${t} on {data['token']['chain_name']}: {fv('Liquidity')}, {fv('24h volume')}.",
               f"Breakdown: {fv('Pair age')}. {fv('24h price change')}. {fv('Risk flags')}."]
        return {"platform": "x", "pillar": "alert", "format": "thread", "text": txt,
                "image_caption": "Check before you trust", "image_sub": "Verify the pool and the contract"}
    if job == "brief" and fmt == "carousel":
        slides = ["Today's memecoin risk board"] + [x[:90] for x in f[:4]] + ["Check liquidity and holders first."]
        return {"platform": "instagram", "pillar": "brief", "format": "carousel", "slides": slides,
                "caption": "Daily memecoin risk board. #memecoins #solana #crypto #dexscreener #risk"}
    if job == "brief":
        return {"platform": "x", "pillar": "brief", "format": "post",
                "text": "Memecoin brief:\n" + "\n".join(x[:110] for x in f[:2])}
    if job == "scoreboard":
        return {"platform": "x", "pillar": "scoreboard", "format": "thread",
                "text": ["Scoreboard Sunday: this week's alerts, misses included."] + [x[:270] for x in f[:5]]}
    return {"platform": "x", "pillar": "education", "format": "post",
            "text": f"Memecoin basics: {data['topic']}.\n\nCheck this before trusting any token."}


ALERT_RULES = ("ALERT FORMAT: put exactly 2 posts in the text array. Post 1 (max 170 chars): start with $TICKER and the chain, then the single "
               "most notable fact from DATA.facts (e.g. age, liquidity, or the number of tokens sharing the ticker). "
               "Post 2 (max 260 chars): start with 'What stands out:' and give 2-3 neutral observations from the data, then 'Scanner:' with the "
               "scanner result exactly as DATA.facts says (flags listed, 'no flags reported', or 'scan unavailable'; never turn 'unavailable' into 'none'), "
               "then one thing to double-check. NEVER call liquidity, volume, FDV or age 'green flags' or 'good', never praise the token, never say why to buy. "
               "The contract, the how-to-verify post and the disclaimer are appended automatically: do not write them. ")


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiQuota(RuntimeError):
    pass


def _gemini_call(model, system, user):
    """One model, retrying temporary errors (503/429/5xx) with waits."""
    last = None
    for wait in (0, 8, 25):
        if wait:
            time.sleep(wait)
        r = requests.post(f"{GEMINI_URL}/models/{model}:generateContent",
                          headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"].strip()},
                          json={"systemInstruction": {"parts": [{"text": system}]},
                                "contents": [{"role": "user", "parts": [{"text": user}]}],
                                "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"}}, timeout=120)
        if r.status_code == 429:
            raise GeminiQuota(f"429 (quota or rate limit) for {model}")
        if r.status_code in (500, 502, 503, 504):
            last = RuntimeError(f"{r.status_code} for {model}")
            print(f"Gemini {model}: busy ({r.status_code}), retrying")
            continue
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    raise last


def _gemini_discover():
    """Ask Google which models this key can use (model names change over time)."""
    try:
        r = requests.get(f"{GEMINI_URL}/models", params={"pageSize": 100},
                         headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"].strip()}, timeout=30)
        names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
    except Exception as e:
        print("model discovery failed:", e)
        return []
    ok = [n for n in names if n.startswith("gemini") and not any(x in n for x in
          ("embedding", "image", "tts", "live", "audio", "robotics", "computer", "vision"))]
    return sorted([n for n in ok if "flash" in n], reverse=True) + sorted([n for n in ok if "flash" not in n], reverse=True)


def gemini_text(system, user):
    """Free writer. Tries GEMINI_MODEL / gemini-4-hydrogen, then whatever models Google lists for this key."""
    tried, last, quota_hits = [], None, 0
    for model in [m for m in [os.getenv("GEMINI_MODEL"), "gemini-4-hydrogen"] if m] + [None]:
        models = [model] if model else [m for m in _gemini_discover() if m not in tried][:2]
        for m in models:
            tried.append(m)
            try:
                out = _gemini_call(m, system, user)
                print("Gemini model used:", m)
                return out
            except GeminiQuota as e:
                quota_hits += 1
                last = e
                print(f"Gemini model {m}: {e}")
                if quota_hits >= 2:
                    raise GeminiQuota("free quota looks exhausted for today") from e
            except Exception as e:
                print(f"Gemini model {m} failed: {e}")
                last = e
    raise last


def generate(job, data, fmt, platform):
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        return mock_generate(job, data, fmt, platform)
    system = (ROOT / "master_prompt.md").read_text().replace("{BRAND}", BRAND)
    user = (f"JOB: {job}\nPLATFORM: {platform}\nFORMAT: {fmt}\nDISCLOSURE: {DISCLOSURE}\n"
            f"RULES: quote numbers exactly as written in DATA.facts and add no other numbers. "
            f"If a token is named, end with '{FOOTER}'. For alerts also return image_caption (max 5 words, meme-caption style, no numbers) "
            f"and image_sub (max 10 words, no numbers). Keep text under 170 characters. Do NOT write the contract address; it is appended automatically.\nDATA:\n{json.dumps(data, ensure_ascii=False)}\n"
            f"{ALERT_RULES if job == 'alert' else ''}Return only the JSON object.")
    try:
        if os.getenv("ANTHROPIC_API_KEY"):
            from anthropic import Anthropic
            msg = Anthropic().messages.create(model=MODEL, max_tokens=1800, system=system,
                                              messages=[{"role": "user", "content": user}])
            raw = msg.content[0].text
        else:
            raw = gemini_text(system, user)
    except Exception as e:
        print(f"AI unavailable ({e}); using template text so today's post is not lost")
        return mock_generate(job, data, fmt, platform)
    return json.loads(re.search(r"\{.*\}", raw, re.S).group(0))


def all_text(out):
    t = out.get("text", "")
    parts = t if isinstance(t, list) else [t]
    return parts + out.get("slides", []) + [out.get("caption", ""), out.get("image_caption", ""), out.get("image_sub", "")]


def validate(out, data):
    problems, parts = [], all_text(out)
    joined = " ".join(parts)
    if data.get("token") and data["token"].get("address"):
        addr = data["token"]["address"]
        if addr not in joined:
            problems.append("missing contract address")
        joined = joined.replace(addr, "")
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


def save_outbox(job, parts, image):
    """Free workflow: ready-to-post text + image path saved in the repo (public/outbox)."""
    box = PUBLIC / "outbox"
    box.mkdir(exist_ok=True)
    for f in box.glob("*.md"):
        if time.time() - f.stat().st_mtime > 30 * 86400:
            f.unlink()
    name = f"{dt.datetime.now(dt.timezone.utc):%Y%m%d_%H%M}_{job}.md"
    imgs = image if isinstance(image, list) else ([image] if image else [])
    img = "".join(f"\n\n---\n\nIMAGE: public/{pathlib.Path(i).name}" for i in imgs)
    (box / name).write_text("\n\n---\n\n".join(parts) + img, encoding="utf-8")


EXPLORERS = {"solana": "Solscan", "bsc": "BscScan", "ethereum": "Etherscan", "base": "BaseScan",
             "arbitrum": "Arbiscan", "polygon": "Polygonscan", "avalanche": "Snowtrace", "optimism": "Optimistic Etherscan"}


def verdict(data):
    """Rule-based label shown on the image (no AI): HIGH RISK / CAUTION / NO FLAGS FOUND."""
    n, liq = data["risk_count"], data.get("liquidity", 0)
    ratio = (data.get("volume", 0) / liq) if liq else 99
    if n >= 3 or liq < 10000:
        return ("HIGH RISK", "255,77,94")
    if n >= 1 or data.get("age_h", 999) < 24 or ratio > 5 or not data["risks_known"]:
        return ("CAUTION", "255,176,32")
    return ("NO FLAGS FOUND", "24,224,138")


def with_contract(out, data):
    """Thread: [hook + contract, breakdown, how-to-verify + disclaimer]. Contract and verify post are added by code."""
    t = data["token"]
    texts = list(out["text"]) if isinstance(out["text"], list) else [out["text"]]
    line = f"{t['chain_name']} CA: {t['address']}"
    if len(texts[0]) + 2 + len(line) <= 280:
        texts[0] += "\n\n" + line
    else:
        texts.insert(1, line)
    texts.append(f"How to verify: open this contract on {EXPLORERS.get(t['chain'], 'the chain explorer')}, check that it matches "
                 f"the project's official link, and compare liquidity before trusting a ticker.\n\n"
                 f"Which red flag would make you skip this one?\n\n{FOOTER}")
    out["text"] = texts
    return out


def hold(job, out, problems):
    name = f"{dt.datetime.now(dt.timezone.utc):%Y%m%d_%H%M}_{job}.json"
    (HELD / name).write_text(json.dumps({"problems": problems, "output": out}, indent=1))
    print("HELD:", problems)


# ---------- publishing ----------
def upload_media_x(path):
    """Returns a media id, or None. Tries the legacy v1.1 upload, then the v2 one-shot upload (images)."""
    import tweepy
    from requests_oauthlib import OAuth1
    try:
        auth = tweepy.OAuth1UserHandler(os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
                                        os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_SECRET"])
        return tweepy.API(auth).media_upload(path).media_id
    except Exception as e:
        print("v1.1 media upload failed, trying v2:", e)
    try:
        oa = OAuth1(os.environ["X_API_KEY"], os.environ["X_API_SECRET"], os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_SECRET"])
        with open(path, "rb") as f:
            r = requests.post("https://api.x.com/2/media/upload", auth=oa, files={"media": f},
                              data={"media_category": "tweet_image", "media_type": "image/png"}, timeout=60)
        r.raise_for_status()
        j = r.json()
        return (j.get("data") or {}).get("id") or j.get("media_id_string")
    except Exception as e:
        print("v2 media upload failed too, posting text only:", e)
        return None


def publish_x(parts, image=None):
    import tweepy
    c = tweepy.Client(consumer_key=os.environ["X_API_KEY"], consumer_secret=os.environ["X_API_SECRET"],
                      access_token=os.environ["X_ACCESS_TOKEN"], access_token_secret=os.environ["X_ACCESS_SECRET"])
    media_id = upload_media_x(image) if image else None
    prev = None
    for i, p in enumerate(parts):
        r = c.create_tweet(text=p, in_reply_to_tweet_id=prev, media_ids=[media_id] if (i == 0 and media_id) else None)
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
        save_outbox(job, parts, image)
    elif not os.getenv("X_API_KEY"):
        print("No X keys: saved to outbox instead of publishing.")
        save_outbox(job, parts, image)
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
    third = ("Risk flags", f"{data['risk_count']} flags") if data["risks_known"] else ("Pair age", m["PAIR AGE"])
    d = {"symbol": data["token"]["symbol"].upper()[:12],
         "stats": [("Liquidity", m["LIQUIDITY"]), ("24h volume", m["24H VOLUME"]), third],
         "caption": out.get("image_caption") or "Check before you trust", "sub": out.get("image_sub", ""),
         "chain": data["token"]["chain_name"], "chain_key": data["token"]["chain"], "verdict": verdict(data), "ca": short_ca(data["token"]["address"]), "copies": data.get("copies", 0),
         "sources": "Data: DexScreener, RugCheck / GoPlus"}
    return render_html.coin_card(d, logo, pal, PUBLIC / f"{prefix}_card.png", BRAND.upper())


def render_carousel(slides, prefix):
    files = []
    for i, t in enumerate(slides):
        name = f"{prefix}_{i + 1}.png"
        render_html.slide_card(t, i, len(slides), PUBLIC / name, BRAND.upper())
        files.append(name)
    return files


def _ig_hosts():
    h = (os.getenv("IG_HOST") or "").strip().rstrip("/")
    return [h] if h else ["https://graph.instagram.com", "https://graph.facebook.com"]


def _ig_post(url, data):
    r = requests.post(url, data=data, timeout=40)
    if not r.ok:
        raise RuntimeError(f"Instagram API error {r.status_code}: {r.text[:400]}")
    return r.json()


def _ig_wait(host, cid, tok, tries=20):
    for _ in range(tries):
        r = requests.get(f"{host}/{cid}", params={"fields": "status_code", "access_token": tok}, timeout=30)
        st = r.json().get("status_code") if r.ok else None
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram media container {st}")
        time.sleep(4)


def _ig_publish_on(host, uid, tok, image_urls, caption):
    if len(image_urls) == 1:
        cid = _ig_post(f"{host}/{uid}/media", {"image_url": image_urls[0], "caption": caption, "access_token": tok})["id"]
    else:
        kids = []
        for u in image_urls:
            k = _ig_post(f"{host}/{uid}/media", {"image_url": u, "is_carousel_item": "true", "access_token": tok})["id"]
            _ig_wait(host, k, tok)
            kids.append(k)
        cid = _ig_post(f"{host}/{uid}/media", {"media_type": "CAROUSEL", "children": ",".join(kids),
                                               "caption": caption, "access_token": tok})["id"]
    _ig_wait(host, cid, tok)
    _ig_post(f"{host}/{uid}/media_publish", {"creation_id": cid, "access_token": tok})


def _ig_try_hosts(fn):
    """Run fn(host, uid, tok) on graph.instagram.com, then graph.facebook.com if the token is not accepted."""
    uid = os.environ["IG_USER_ID"].strip()
    tok = os.environ["IG_ACCESS_TOKEN"].strip().strip("\"'")
    last = None
    for host in _ig_hosts():
        try:
            return fn(host, uid, tok)
        except RuntimeError as e:
            last = e
            if "OAuth" in str(e) or "access token" in str(e).lower():
                print(f"{host}: token not accepted ({str(e)[:120]}), trying the next host")
                continue
            raise
    raise last


def ig_publish(image_urls, caption):
    """Single image or carousel (4:5 JPEG)."""
    return _ig_try_hosts(lambda h, u, t: _ig_publish_on(h, u, t, image_urls, caption))


def _ig_story_on(host, uid, tok, image_url):
    cid = _ig_post(f"{host}/{uid}/media", {"image_url": image_url, "media_type": "STORIES", "access_token": tok})["id"]
    _ig_wait(host, cid, tok)
    _ig_post(f"{host}/{uid}/media_publish", {"creation_id": cid, "access_token": tok})


def ig_publish_story(image_url):
    """One 9:16 JPEG story (the API does not support interactive stickers)."""
    return _ig_try_hosts(lambda h, u, t: _ig_story_on(h, u, t, image_url))


# ---------- Telegram (free Bot API: channel posts) ----------
def tg_enabled():
    return bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip())


def tg_send(text, photo_path=None):
    """Post to the Telegram channel. With a photo, the text is the caption (max 1024 chars; extra goes in a second message)."""
    tok, chat = os.environ["TELEGRAM_BOT_TOKEN"].strip(), os.environ["TELEGRAM_CHAT_ID"].strip()
    base = f"https://api.telegram.org/bot{tok}"
    rest = ""
    if photo_path:
        cap, rest = (text[:1024], text[1024:]) if len(text) > 1024 else (text, "")
        with open(photo_path, "rb") as f:
            r = requests.post(f"{base}/sendPhoto", data={"chat_id": chat, "caption": cap}, files={"photo": f}, timeout=60)
    else:
        r = requests.post(f"{base}/sendMessage", data={"chat_id": chat, "text": text[:4096]}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text[:300].replace(tok, '***')}")
    if rest.strip():
        requests.post(f"{base}/sendMessage", data={"chat_id": chat, "text": rest[:4096]}, timeout=30)


def tg_post_safe(text, photo_path=None):
    """Never let a Telegram problem break the X/Instagram jobs."""
    if DRY_RUN or not tg_enabled():
        return
    try:
        tg_send(text, photo_path)
        print("Telegram: posted")
    except Exception as e:
        print("Telegram failed (ignored):", e)


# ---------- jobs ----------
def job_alert():
    data = pick_alert_data()
    if not data:
        return print("no new candidate")
    seen = load("seen", [])
    seen.append(data["token"]["address"])
    save("seen", seen[-500:])
    out = with_contract(generate("alert", data, "thread", "x"), data)
    prefix = re.sub(r"\W", "", f"x_{dt.date.today():%Y%m%d}_{data['token']['symbol'][:8]}")
    card = {}

    def make_card(o):
        card["p"] = render_alert_card(data, o, prefix)
        return card["p"]

    if deliver_x("alert", out, data, make_card):
        tg_post_safe("\n\n".join(out["text"]), card.get("p"))
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


HASHTAGS = {"solana": "#solana", "bsc": "#bnbchain", "ethereum": "#ethereum", "base": "#basechain",
            "arbitrum": "#arbitrum", "polygon": "#polygon", "avalanche": "#avalanche", "optimism": "#optimism"}


def render_ig_slides(data, out, prefix):
    """Slide 1 = the main token card; slides 2-4 = numbers, flags, verify. Returns JPEG paths."""
    t = data["token"]
    logo = get_logo(data, prefix)
    pal = design.logo_palette(Image.open(logo))
    card = pathlib.Path(render_alert_card(data, out, prefix + "_s1"))
    d = {"symbol": t["symbol"].upper()[:10], "chain": t["chain_name"], "chain_key": t["chain"],
         "explorer": EXPLORERS.get(t["chain"], "the chain explorer"), "address": t["address"], "metrics": data["metrics"],
         "risk_list": data.get("risk_list", []), "risks_known": data["risks_known"], "copies": data.get("copies", 0)}
    pngs = [card]
    for k, kind in enumerate(("numbers", "flags", "verify"), start=2):
        p = PUBLIC / f"{prefix}_s{k}.png"
        render_html.token_slide(kind, d, pal, k - 1, 4, p, BRAND.upper())
        pngs.append(p)
    jpgs = []
    for p in pngs:
        j = p.with_suffix(".jpg")  # Instagram's publishing API only accepts JPEG
        Image.open(p).convert("RGB").save(j, "JPEG", quality=92)
        p.unlink()
        jpgs.append(j)
    return jpgs


def build_ig_caption(out, data):
    """Hook first (only ~125 chars show before 'more'), then substance, contract, save/share + question CTAs, keywords, 5 hashtags."""
    t = data["token"]
    ca = f"{t['chain_name']} CA: {t['address']}"
    parts = []
    for p in out["text"]:
        p = p.replace(ca, "").replace("Which red flag would make you skip this one?\n\n", "").strip()
        if p:
            parts.append(p)
    hook, middle, verify = parts[0], parts[1:-1], parts[-1]
    tags = " ".join(["#memecoins", "#cryptoeducation", "#cryptorisk", HASHTAGS.get(t["chain"], "#crypto"), "#dexscreener"])
    return "\n\n".join([hook, *middle, ca,
                         "Save this post to check your next token, and send it to a friend who trades memecoins.",
                         "Which red flag would make you skip this one? Tell us below.",
                         f"Memecoin risk check on {t['chain_name']}: liquidity, contract and scanner flags.",
                         verify, tags])


def job_ig_prepare():
    for f in list(PUBLIC.glob("*.png")) + list(PUBLIC.glob("*.jpg")):
        if time.time() - f.stat().st_mtime > 14 * 86400:
            f.unlink()
    data = pick_alert_data()
    if not data:
        return print("no new candidate")
    seen = load("seen", [])
    seen.append(data["token"]["address"])
    save("seen", seen[-500:])
    out = with_contract(generate("alert", data, "thread", "instagram"), data)
    problems = validate(out, data)
    if problems:
        return hold("ig", out, problems)
    prefix = re.sub(r"\W", "", f"ig_{dt.date.today():%Y%m%d}_{data['token']['symbol'][:8]}")
    files = render_ig_slides(data, out, prefix)
    caption = build_ig_caption(out, data)
    save("ig_next", {"files": [f.name for f in files], "caption": caption, "verdict": verdict(data)[0]})
    print("prepared", [f.name for f in files])


def job_ig_publish():
    nxt = load("ig_next", None)
    if not nxt or "files" not in nxt:
        (STATE / "ig_next.json").unlink(missing_ok=True)
        return print("nothing prepared")
    base = f"https://raw.githubusercontent.com/{os.environ.get('GITHUB_REPOSITORY', 'USER/REPO')}/main/public/"
    urls = [base + f for f in nxt["files"]]
    if DRY_RUN or not os.getenv("IG_ACCESS_TOKEN"):
        print("[DRY RUN IG]", nxt["caption"], urls)
        save_outbox("instagram", [nxt["caption"]], [str(PUBLIC / f) for f in nxt["files"]])
    else:
        ig_publish(urls, nxt["caption"])
        update_stats(nxt.get("verdict"))
    (STATE / "ig_next.json").unlink(missing_ok=True)


def job_diagnose():
    """Read-only Meta check: what this token is, which permissions it has, which pages/accounts it can see."""
    tok = os.environ["IG_ACCESS_TOKEN"].strip().strip("\"'")
    uid = os.environ["IG_USER_ID"].strip()
    print(f"token starts with '{tok[:4]}', length {len(tok)}; account id starts with '{uid[:4]}', length {len(uid)}")
    checks = [
        ("FB host /me", "https://graph.facebook.com/me", {"fields": "id,name"}),
        ("FB host permissions", "https://graph.facebook.com/me/permissions", {}),
        ("FB host pages", "https://graph.facebook.com/me/accounts", {"fields": "name,id,instagram_business_account"}),
        ("FB host IG account", f"https://graph.facebook.com/{uid}", {"fields": "username,name"}),
        ("FB host publish limit", f"https://graph.facebook.com/{uid}/content_publishing_limit", {"fields": "quota_usage,config"}),
        ("IG host /me", "https://graph.instagram.com/me", {"fields": "user_id,username"}),
    ]
    for label, url, params in checks:
        try:
            r = requests.get(url, params=dict(params, access_token=tok), timeout=30)
            print(f"[{label}] {r.status_code} {r.text[:700].replace(tok, '***')}")
        except Exception as e:
            print(f"[{label}] failed: {e}")


TIPS = [
    ("Liquidity is your exit door", "If a pool is tiny, buying is easy but selling moves the price against you. Check liquidity before anything else."),
    ("Same name, different token", "Anyone can launch a token with a famous name. Only the contract address tells them apart. Always verify it."),
    ("Never trust a CA in a reply", "Scammers reply under viral posts with fake contracts. Get the address from the project's official link."),
    ("Check who holds the supply", "If a few wallets hold most of the supply, they can dump on you. Look at the holder list before you trust a chart."),
    ("Fresh token, fresh risk", "Tokens only hours old have almost no history. Treat age as a risk factor, not a detail."),
    ("Volume can be faked", "Wash trading inflates volume to look alive. Compare volume to liquidity: extreme ratios deserve a second look."),
    ("Mint authority matters", "If the creator can still mint new tokens, supply can be inflated at any time. Check that mint authority is revoked."),
    ("Freeze authority", "On Solana, a freeze authority can lock tokens in your wallet. Check whether it is disabled."),
    ("Honeypots exist", "Some contracts let you buy but block selling. Run a honeypot check before you buy on BNB Chain or Ethereum."),
    ("Unverified code is a red flag", "If a contract's code is not verified on the explorer, you cannot read what it does."),
    ("A locked pool is not a guarantee", "An LP lock helps, but it does not protect you from every rug or from a team selling a large share."),
    ("Taxes can trap you", "Some EVM tokens charge huge sell taxes. Check buy and sell tax before you trade."),
    ("Charts hide the contract", "Price action says nothing about the contract, the team or the liquidity. Look underneath the chart."),
    ("A screenshot is not research", "A screenshot of gains proves nothing about the next trade. Verify the token yourself."),
    ("FOMO is the product", "Memecoins sell urgency. If you feel rushed, that is the moment to slow down and check the basics."),
    ("Position size is survival", "Only risk what you can afford to lose completely. Memecoins can go to zero fast."),
    ("Use a separate wallet", "Keep a second wallet with a small balance for experiments. Never connect your main wallet to random sites."),
    ("Approvals are permissions", "Review and revoke old token approvals regularly. A forgotten approval can be abused."),
    ("Beware fake links", "Typo domains and fake airdrop pages drain wallets. Bookmark official sites instead of searching."),
    ("Unsolicited DMs are scams", "Nobody legit DMs you first with a deal. Treat unsolicited messages as scams."),
    ("Check the pair, not just the token", "The same token can trade in several pools. Make sure you look at the main pool with real liquidity."),
    ("Boosted does not mean good", "Paid promotion only means someone paid for attention. It says nothing about safety."),
    ("Mind your slippage", "Very high slippage can make you overpay or get sandwiched. Keep it as low as the trade allows."),
    ("Test with a tiny trade", "A small first trade shows whether you can actually sell before you commit more."),
    ("Learn the explorer", "The chain explorer shows holders, transfers and contract details for free. Learn the basics once."),
    ("Check the creator wallet", "Look at whether the creator's wallet has launched and abandoned tokens before."),
    ("Bundled launches", "When many wallets buy in the very first block, insiders may control the supply. Worth checking."),
    ("Social proof can be bought", "Followers, likes and holder counts can be faked. Verify on-chain, not on socials."),
    ("A flag is not a verdict", "Flags mean check more, not it is a scam. Judge the full picture."),
    ("Write your exit plan first", "Decide your limits before you buy. Decisions made in the heat of a pump are usually worse."),
]


def _cleanup_old_images():
    for f in list(PUBLIC.glob("*.png")) + list(PUBLIC.glob("*.jpg")):
        if time.time() - f.stat().st_mtime > 14 * 86400:
            f.unlink()


def _to_story_jpg(png_path):
    png_path = pathlib.Path(png_path)
    jpg = png_path.with_suffix(".jpg")
    Image.open(png_path).convert("RGB").save(jpg, "JPEG", quality=92)
    png_path.unlink()
    return jpg


def job_story_tip_prepare():
    _cleanup_old_images()
    idx = (dt.date.today().toordinal() * 2) % len(TIPS)
    title, body = TIPS[idx]
    out = PUBLIC / f"story_tip_{dt.date.today():%Y%m%d}.png"
    render_html.story_tip_card({"n": idx + 1, "title": title, "body": body}, out, BRAND.upper())
    jpg = _to_story_jpg(out)
    save("ig_story_next", {"file": jpg.name})
    print("prepared tip story:", title)


def pick_radar_data():
    """Most active token (highest 24h volume, liquidity >= $25K) among today's scanned candidates; skips the last 3 used."""
    recent = load("story_recent", [])
    best = None
    for c in candidates(40):
        if c["tokenAddress"] in recent:
            continue
        try:
            pair = best_pair(c["chainId"], c["tokenAddress"])
        except Exception:
            continue
        if not pair or (pair.get("liquidity") or {}).get("usd", 0) < 25000:
            continue
        vol = (pair.get("volume") or {}).get("h24", 0)
        if best is None or vol > best[0]:
            best = (vol, c, pair)
    if not best:
        return None
    _, c, pair = best
    risks = rugcheck(c["tokenAddress"]) if c["chainId"] == "solana" else goplus_risks(c["chainId"], c["tokenAddress"])
    return build_data(pair, risks)


def job_story_radar_prepare():
    _cleanup_old_images()
    data = pick_radar_data()
    if not data:
        return print("no radar candidate")
    t = data["token"]
    recent = load("story_recent", [])
    recent.append(t["address"])
    save("story_recent", recent[-3:])
    prefix = re.sub(r"\W", "", f"story_radar_{dt.date.today():%Y%m%d}_{t['symbol'][:8]}")
    logo = get_logo(data, prefix)
    pal = design.logo_palette(Image.open(logo))
    m = {k: v for k, v, _ in data["metrics"]}
    d = {"symbol": t["symbol"].upper()[:12], "chain": t["chain_name"], "chain_key": t["chain"], "verdict": verdict(data),
         "stats": [("24h volume", m["24H VOLUME"]), ("Liquidity", m["LIQUIDITY"]), ("24h change", m["24H CHANGE"])],
         "ca": short_ca(t["address"]), "updated": dt.datetime.now(dt.timezone.utc).strftime("%H:%M UTC")}
    out = PUBLIC / f"{prefix}.png"
    render_html.story_radar_card(d, logo, pal, out, BRAND.upper())
    jpg = _to_story_jpg(out)
    save("ig_story_next", {"file": jpg.name})
    print("prepared radar story:", t["symbol"])


def update_stats(verdict_label=None):
    st = load("stats", {"checks": 0, "last_post": ""})
    st["checks"] = st.get("checks", 0) + 1
    st["last_post"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    if verdict_label:
        st[verdict_label] = st.get(verdict_label, 0) + 1
    save("stats", st)


PROMO_ORDER = ["drop", "counter", "whatwedo", "teaser", "quiz"]


def job_story_promo_prepare():
    """Brand/hype story. Never shows the token of the day: that stays exclusive to the carousel."""
    _cleanup_old_images()
    st = load("stats", {"checks": 0, "last_post": ""})
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    ok = {"drop": st.get("last_post") == today, "counter": st.get("checks", 0) >= 3,
          "whatwedo": True, "teaser": True, "quiz": True}
    day = dt.date.today().toordinal()
    kind = PROMO_ORDER[day % len(PROMO_ORDER)]
    if not ok[kind]:  # not eligible yet (e.g. no post today / too few checks): use a static one, rotating
        kind = ["whatwedo", "teaser", "quiz"][day % 3]
    out = PUBLIC / f"story_promo_{dt.date.today():%Y%m%d}.png"
    render_html.story_promo_card(kind, {"scanned": len(load("seen", [])), "checks": st.get("checks", 0)}, out, BRAND.upper())
    jpg = _to_story_jpg(out)
    save("ig_story_next", {"file": jpg.name})
    print("prepared promo story:", kind)


def job_story_publish():
    nxt = load("ig_story_next", None)
    if not nxt or "file" not in nxt:
        (STATE / "ig_story_next.json").unlink(missing_ok=True)
        return print("no story prepared")
    base = f"https://raw.githubusercontent.com/{os.environ.get('GITHUB_REPOSITORY', 'USER/REPO')}/main/public/"
    if DRY_RUN or not os.getenv("IG_ACCESS_TOKEN"):
        print("[DRY RUN STORY]", base + nxt["file"])
        save_outbox("story", ["(story image)"], str(PUBLIC / nxt["file"]))
    else:
        ig_publish_story(base + nxt["file"])
        print("story published")
        tg_post_safe("Daily update from the radar. Not financial advice. DYOR.", str(PUBLIC / nxt["file"]))
    (STATE / "ig_story_next.json").unlink(missing_ok=True)


JOBS = {"diagnose": job_diagnose, "story_tip_prepare": job_story_tip_prepare, "story_radar_prepare": job_story_promo_prepare, "story_token_prepare": job_story_radar_prepare, "story_publish": job_story_publish, "alert": job_alert, "brief": job_brief, "scoreboard": job_scoreboard, "education": job_education,
        "ig_prepare": job_ig_prepare, "ig_publish": job_ig_publish}

X_JOBS = {"alert", "brief", "scoreboard", "education"}

if __name__ == "__main__":
    job = sys.argv[1]
    if job in X_JOBS and not (os.getenv("X_API_KEY") or tg_enabled()):
        print(f"Job '{job}' skipped: X (and Telegram) are not connected yet. This saves AI quota and Actions minutes.")
    else:
        JOBS[job]()
