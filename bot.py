"""Automated Instagram-only memecoin radar for IS IT COOKED?."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import pathlib
import re
import sys
import time
from urllib.parse import quote, urlsplit

import requests
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import design
import render_html

ROOT = pathlib.Path(__file__).parent
STATE, PUBLIC = ROOT / "state", ROOT / "public"
STATE.mkdir(exist_ok=True); PUBLIC.mkdir(exist_ok=True)
BRAND = os.getenv("BRAND") or "Is It Cooked"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
CHAINS = [x.strip() for x in os.getenv("CHAINS", "solana,base").split(",") if x.strip()]
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "20000"))
MIN_VOLUME_1H = float(os.getenv("MIN_VOLUME_1H", "25000"))
MIN_TXNS_1H = int(os.getenv("MIN_TXNS_1H", "35"))
MIN_SCORE = int(os.getenv("MIN_SCORE", "70"))
MAX_AGE_HOURS = float(os.getenv("MAX_AGE_HOURS", "72"))
REQUIRE_OFFICIAL_LINK = os.getenv("REQUIRE_OFFICIAL_LINK", "true").lower() == "true"
MEDIA_BRANCH = os.getenv("MEDIA_BRANCH", "media")
DS = "https://api.dexscreener.com"
CHAIN_NAMES = {"solana": "Solana", "base": "Base", "ethereum": "Ethereum", "bsc": "BNB Chain"}
CHAIN_IDS = {"ethereum": 1, "bsc": 56, "base": 8453, "arbitrum": 42161, "polygon": 137,
             "avalanche": 43114, "optimism": 10}
EXPLORERS = {"solana": "Solscan", "base": "BaseScan", "ethereum": "Etherscan", "bsc": "BscScan"}
HASHTAGS = {"solana": "#solana", "base": "#basechain", "ethereum": "#ethereum", "bsc": "#bnbchain"}
LOGO_HOSTS = {"cdn.dexscreener.com", "dd.dexscreener.com"}

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "IsItCookedInstagramRadar/3.0"})
HTTP.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, connect=3, read=3, backoff_factor=.8,
    status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))))


def load(name, default):
    path = STATE / f"{name}.json"
    if not path.exists(): return default
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Invalid state/{name}.json ({exc}); using safe default")
        return default


def save(name, value):
    """Atomic JSON write so interrupted jobs cannot corrupt state."""
    path = STATE / f"{name}.json"; tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def get_json(url, timeout=25):
    response = HTTP.get(url, timeout=timeout); response.raise_for_status()
    try: return response.json()
    except requests.JSONDecodeError as exc: raise RuntimeError(f"Invalid JSON from {urlsplit(url).netloc}") from exc


def num(value, default=0.0):
    try:
        value = float(value); return value if math.isfinite(value) else default
    except (TypeError, ValueError): return default


def fmt_usd(value):
    value = num(value)
    for divisor, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= divisor: return f"${value / divisor:.1f}{suffix}"
    return f"${value:,.0f}"


def short_ca(value): return value if len(value) <= 20 else f"{value[:8]}...{value[-8:]}"
def token_key(chain, address): return f"{chain}:{address if chain == 'solana' else address.lower()}"


def official_presence(pair):
    info = pair.get("info") or {}
    websites = [x.get("url") for x in info.get("websites") or [] if isinstance(x, dict) and x.get("url")]
    socials = [x.get("url") for x in info.get("socials") or [] if isinstance(x, dict) and x.get("url")]
    return websites[:2], socials[:3]


def discovery_pairs():
    grouped, discovered = {chain: [] for chain in CHAINS}, set()
    for endpoint in ("token-boosts/latest/v1", "token-profiles/latest/v1", "token-boosts/top/v1"):
        try: rows = get_json(f"{DS}/{endpoint}")
        except Exception as exc:
            print(f"DexScreener {endpoint} unavailable: {exc}"); continue
        for row in rows if isinstance(rows, list) else []:
            chain, address = row.get("chainId"), row.get("tokenAddress")
            if chain not in grouped or not isinstance(address, str): continue
            key = token_key(chain, address)
            if key not in discovered and len(grouped[chain]) < 30:
                grouped[chain].append(address); discovered.add(key)
    best = {}
    for chain, addresses in grouped.items():
        if not addresses: continue
        try: pairs = get_json(f"{DS}/tokens/v1/{chain}/{','.join(addresses)}")
        except Exception as exc:
            print(f"Pair lookup for {chain} failed: {exc}"); continue
        for pair in pairs if isinstance(pairs, list) else []:
            address = (pair.get("baseToken") or {}).get("address")
            if not isinstance(address, str): continue
            key, liquidity = token_key(chain, address), num((pair.get("liquidity") or {}).get("usd"))
            if key not in best or liquidity > num((best[key].get("liquidity") or {}).get("usd")): best[key] = pair
    return list(best.values())


def snapshot(pair):
    volume, txns, change = pair.get("volume") or {}, pair.get("txns") or {}, pair.get("priceChange") or {}
    h1 = txns.get("h1") or {}; created = num(pair.get("pairCreatedAt"))
    websites, socials = official_presence(pair)
    return {"age": (time.time()*1000-created)/3_600_000 if created else 999999,
            "liq": num((pair.get("liquidity") or {}).get("usd")), "v1": num(volume.get("h1")),
            "v5": num(volume.get("m5")), "v24": num(volume.get("h24")),
            "buys": int(num(h1.get("buys"))), "sells": int(num(h1.get("sells"))),
            "c1": num(change.get("h1")), "c5": num(change.get("m5")),
            "websites": websites, "socials": socials}


def qualifies(s):
    return (5/60 <= s["age"] <= MAX_AGE_HOURS and s["liq"] >= MIN_LIQUIDITY and
            s["v1"] >= MIN_VOLUME_1H and s["buys"] + s["sells"] >= MIN_TXNS_1H and
            s["buys"] > s["sells"] and 0 < s["c1"] <= 120 and s["c5"] > -8 and
            (bool(s["websites"] or s["socials"]) or not REQUIRE_OFFICIAL_LINK))


def market_score(s):
    liq = min(20, 8 + 8*math.log10(max(1, s["liq"]/MIN_LIQUIDITY)))
    vol = min(25, 10 + 10*math.log10(max(1, s["v1"]/MIN_VOLUME_1H)))
    ratio = s["buys"]/max(1, s["buys"]+s["sells"])
    pressure = min(22, max(0, (ratio-.5)*100))
    velocity = min(13, 78*s["v5"]/max(1, s["v1"]))
    momentum = 10 if 5 <= s["c1"] <= 60 else 6
    presence = 5 if s["websites"] and s["socials"] else 3
    age = 5 if .25 <= s["age"] <= 24 else 3
    return round(min(100, liq+vol+pressure+velocity+momentum+presence+age))


def solana_security(address):
    risks = get_json(f"https://api.rugcheck.xyz/v1/tokens/{address}/report/summary").get("risks")
    if not isinstance(risks, list): raise RuntimeError("RugCheck returned no risk list")
    labels, critical = [], False
    for risk in risks:
        level = str(risk.get("level") or "").lower(); name = str(risk.get("name") or "Unknown flag")[:80]
        labels.append(f"{name} ({level or 'flag'})"); critical |= level in {"danger", "critical", "error"}
    return critical, labels[:6]


def evm_security(chain, address):
    chain_id = CHAIN_IDS.get(chain)
    if not chain_id: raise RuntimeError(f"No security provider for {chain}")
    result = get_json(f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={address}")
    token = (result.get("result") or {}).get(address.lower())
    if not isinstance(token, dict): raise RuntimeError("GoPlus returned no token report")
    critical_map = {"is_honeypot":"Honeypot", "hidden_owner":"Hidden owner", "cannot_sell_all":"Cannot sell all",
                    "is_blacklisted":"Blacklist function", "selfdestruct":"Self-destruct function"}
    warning_map = {"is_mintable":"Mintable supply", "can_take_back_ownership":"Ownership reclaimable",
                   "owner_change_balance":"Owner can change balances", "slippage_modifiable":"Modifiable tax"}
    critical = [label for flag, label in critical_map.items() if token.get(flag) == "1"]
    warnings = [label for flag, label in warning_map.items() if token.get(flag) == "1"]
    if token.get("is_open_source") != "1": critical.append("Contract source not verified")
    if num(token.get("buy_tax")) > .10 or num(token.get("sell_tax")) > .10: critical.append("Tax above 10%")
    return bool(critical), (critical+warnings)[:6]


def security_scan(chain, address): return solana_security(address) if chain == "solana" else evm_security(chain, address)


def build_data(pair, s, warnings, score):
    base, chain = pair.get("baseToken") or {}, pair.get("chainId")
    address = str(base.get("address") or ""); age = f"{s['age']:.0f} hours" if s["age"] < 48 else f"{s['age']/24:.1f} days"
    metrics = [("LIQUIDITY", fmt_usd(s["liq"]), None), ("1H VOLUME", fmt_usd(s["v1"]), None),
               ("PAIR AGE", age, None), ("1H CHANGE", f"{s['c1']:+.1f}%", "green")]
    return {"metrics":metrics, "risks_known":True, "risk_list":warnings, "risk_count":len(warnings),
            "radar_score":score, "age_h":s["age"], "volume":s["v1"], "liquidity":s["liq"],
            "buys":s["buys"], "sells":s["sells"], "change_h1":s["c1"],
            "logo":(pair.get("info") or {}).get("imageUrl"), "pool":pair.get("pairAddress"),
            "token":{"symbol":str(base.get("symbol") or "?")[:20], "name":str(base.get("name") or "Token")[:80],
                     "chain":chain, "chain_name":CHAIN_NAMES.get(chain, str(chain).title()), "address":address,
                     "url":pair.get("url")}, "price":pair.get("priceUsd"),
            "websites":s["websites"], "socials":s["socials"]}


def pick_promising(exclude_seen=True):
    seen = set(load("seen", [])); ranked = []
    for pair in discovery_pairs():
        chain, address = pair.get("chainId"), str((pair.get("baseToken") or {}).get("address") or "")
        if not chain or not address or (exclude_seen and (token_key(chain,address) in seen or address in seen)): continue
        s = snapshot(pair)
        if qualifies(s): ranked.append((market_score(s), pair, s))
    ranked.sort(key=lambda row: row[0], reverse=True); print("market-qualified candidates:", len(ranked))
    for score, pair, s in ranked[:12]:
        chain, address = pair["chainId"], pair["baseToken"]["address"]
        try: critical, warnings = security_scan(chain, address)
        except Exception as exc:
            print(f"security unavailable for {chain}:{address}: {exc}"); continue
        if critical:
            print(f"blocked critical risk for {chain}:{address}: {warnings}"); continue
        final = max(0, score-min(12, 3*len(warnings)))
        if final >= MIN_SCORE:
            print(f"selected {pair['baseToken'].get('symbol')} score={final}")
            return build_data(pair, s, warnings, final)
    print("no token met the complete quality and security threshold"); return None


def safe_logo_url(value):
    try:
        parsed = urlsplit(value or "")
        return value if parsed.scheme == "https" and (parsed.hostname or "").lower() in LOGO_HOSTS else ""
    except ValueError: return ""


def get_logo(data, name):
    path, url = STATE/f"logo_{name}.png", safe_logo_url(data.get("logo"))
    try:
        if not url: raise ValueError("no approved logo URL")
        response = HTTP.get(url, timeout=20, stream=True); response.raise_for_status(); raw = response.raw.read(3_000_001)
        if len(raw) > 3_000_000: raise ValueError("logo exceeds 3 MB")
        path.write_bytes(raw)
        with Image.open(path) as source:
            if source.width*source.height > 12_000_000: raise ValueError("logo dimensions too large")
            source.convert("RGBA").save(path, "PNG")
        return str(path)
    except (OSError, ValueError, requests.RequestException, UnidentifiedImageError) as exc:
        print("using branded fallback logo:", exc)
    image = Image.new("RGBA", (512,512), (0,0,0,0)); draw = ImageDraw.Draw(image)
    draw.ellipse((0,0,511,511), fill=(35,18,67)); draw.ellipse((38,38,473,473), fill=(255,156,0))
    font = ImageFont.truetype(str(ROOT/"fonts/Poppins-Bold.ttf"), 245)
    draw.text((256,270), data["token"]["symbol"][:1].upper(), font=font, fill="white", anchor="mm",
              stroke_width=10, stroke_fill=(10,10,20)); image.save(path); return str(path)


def verdict(data): return ("PROMISING SIGNAL", "24,224,138") if not data["risk_list"] else ("WATCHLIST", "255,176,32")


def render_alert_card(data, prefix):
    logo = get_logo(data, prefix); palette = design.logo_palette(Image.open(logo)); m = {k:v for k,v,_ in data["metrics"]}
    card = {"symbol":data["token"]["symbol"].upper()[:12],
            "stats":[("Liquidity",m["LIQUIDITY"]),("1h volume",m["1H VOLUME"]),("Radar score",f"{data['radar_score']}/100")],
            "caption":"Momentum worth watching", "sub":"Filtered by market strength and security",
            "chain":data["token"]["chain_name"], "chain_key":data["token"]["chain"], "verdict":verdict(data),
            "ca":short_ca(data["token"]["address"]), "copies":0, "sources":"Data: DexScreener, RugCheck / GoPlus"}
    return pathlib.Path(render_html.coin_card(card, logo, palette, PUBLIC/f"{prefix}_card.png", BRAND.upper()))


def render_carousel(data, prefix):
    token = data["token"]; logo = get_logo(data,prefix); palette = design.logo_palette(Image.open(logo))
    slide = {"symbol":token["symbol"].upper()[:10], "chain":token["chain_name"], "chain_key":token["chain"],
             "explorer":EXPLORERS.get(token["chain"],"the chain explorer"), "address":token["address"],
             "metrics":data["metrics"], "risk_list":data["risk_list"], "risks_known":True, "copies":0}
    pngs = [render_alert_card(data,prefix+"_s1")]
    for index, kind in enumerate(("numbers","flags","verify"), start=2):
        path = PUBLIC/f"{prefix}_s{index}.png"; render_html.token_slide(kind,slide,palette,index-1,4,path,BRAND.upper()); pngs.append(path)
    files=[]
    for png in pngs:
        jpg=png.with_suffix(".jpg")
        with Image.open(png) as image: image.convert("RGB").save(jpg,"JPEG",quality=92,optimize=True)
        png.unlink(missing_ok=True); files.append(jpg)
    return files


def caption(data):
    token=data["token"]; ratio=100*data["buys"]/max(1,data["buys"]+data["sells"])
    security = "No critical scanner flags detected." if not data["risk_list"] else "No critical flags; review the warnings shown in the carousel."
    tags=" ".join(("#memecoins","#cryptoresearch","#onchain",HASHTAGS.get(token["chain"],"#crypto"),"#dexscreener"))
    return "\n\n".join((f"${token['symbol'].upper()} just entered the IS IT COOKED? watchlist.",
        f"Radar score: {data['radar_score']}/100\nLiquidity: {fmt_usd(data['liquidity'])}\n1h volume: {fmt_usd(data['volume'])}\nBuy pressure: {ratio:.0f}%\n1h change: {data['change_h1']:+.1f}%",
        security, f"{token['chain_name']} contract:\n{token['address']}",
        "Save this research card, verify the contract and follow for the next signal.",
        "Promising momentum is not a guarantee. Not financial advice. Always do your own research.", tags))


def cleanup_media(days=3):
    cutoff=time.time()-days*86400
    for path in list(PUBLIC.glob("*.png"))+list(PUBLIC.glob("*.jpg")):
        if path.stat().st_mtime < cutoff: path.unlink(missing_ok=True)


def job_instagram_prepare():
    cleanup_media()
    if load("ig_next",None): return print("a post is already prepared; publish it before replacing it")
    data=pick_promising(True)
    if not data: return
    symbol=re.sub(r"[^A-Za-z0-9_-]","",data["token"]["symbol"][:10]) or "token"
    prefix=f"ig_{dt.datetime.now(dt.timezone.utc):%Y%m%d_%H%M}_{symbol}"; files=render_carousel(data,prefix)
    save("ig_next",{"files":[p.name for p in files],"caption":caption(data),
         "token_key":token_key(data["token"]["chain"],data["token"]["address"]),"score":data["radar_score"],
         "prepared_at":dt.datetime.now(dt.timezone.utc).isoformat()})
    print("prepared Instagram carousel:",[p.name for p in files])


def ig_credentials():
    return (os.getenv("IG_HOST","https://graph.facebook.com").strip().rstrip("/"),
            os.environ["IG_USER_ID"].strip(), os.environ["IG_ACCESS_TOKEN"].strip().strip("\"'"))


def ig_post(host,path,data):
    response=requests.post(f"{host}/{path}",data=data,timeout=45)
    if not response.ok: raise RuntimeError(f"Instagram API {response.status_code}: {response.text[:500].replace(data.get('access_token',''),'***')}")
    return response.json()


def ig_wait(host,container,token,tries=30):
    for attempt in range(tries):
        response=HTTP.get(f"{host}/{container}",params={"fields":"status_code","access_token":token},timeout=30)
        status=(response.json() if response.ok else {}).get("status_code")
        if status=="FINISHED": return
        if status in {"ERROR","EXPIRED"}: raise RuntimeError(f"Instagram media container {status}")
        time.sleep(min(10,3+attempt//5))
    raise TimeoutError(f"Instagram container {container} did not finish in time")


def publish_carousel(urls,text):
    host,user,token=ig_credentials(); children=[]
    for url in urls:
        child=ig_post(host,f"{user}/media",{"image_url":url,"is_carousel_item":"true","access_token":token})["id"]
        ig_wait(host,child,token); children.append(child)
    parent=ig_post(host,f"{user}/media",{"media_type":"CAROUSEL","children":",".join(children),"caption":text,"access_token":token})["id"]
    ig_wait(host,parent,token); return ig_post(host,f"{user}/media_publish",{"creation_id":parent,"access_token":token})


def publish_story(url):
    host,user,token=ig_credentials(); cid=ig_post(host,f"{user}/media",{"image_url":url,"media_type":"STORIES","access_token":token})["id"]
    ig_wait(host,cid,token); return ig_post(host,f"{user}/media_publish",{"creation_id":cid,"access_token":token})


def media_url(filename):
    repo=os.environ.get("GITHUB_REPOSITORY","isitcooked/isitcooked-bot"); version=quote(os.getenv("MEDIA_VERSION","latest"),safe="")
    return f"https://raw.githubusercontent.com/{repo}/{MEDIA_BRANCH}/{quote(filename)}?v={version}"


def wait_public(urls,tries=18):
    for attempt in range(tries):
        try:
            if all((lambda r:r.ok and r.headers.get("content-type","").startswith("image/") and len(r.content)>1000)(HTTP.get(u,timeout=20)) for u in urls): return
        except requests.RequestException: pass
        time.sleep(min(15,3+attempt))
    raise TimeoutError("media images did not become publicly available")


def update_stats(post=False):
    stats=load("stats",{"posts":0,"stories":0}); key="posts" if post else "stories"; stats[key]=stats.get(key,0)+1
    stats["last_publish"]=dt.datetime.now(dt.timezone.utc).isoformat(); save("stats",stats)


def job_instagram_publish():
    pending=load("ig_next",None)
    if not pending or not pending.get("files"): return print("nothing prepared")
    urls=[media_url(f) for f in pending["files"]]
    if DRY_RUN: return print("[DRY RUN INSTAGRAM]",pending["caption"],urls)
    wait_public(urls); result=publish_carousel(urls,pending["caption"])
    if not result.get("id"): raise RuntimeError("Instagram did not confirm publication")
    seen=load("seen",[]); seen.append(pending["token_key"]); save("seen",list(dict.fromkeys(seen))[-1000:])
    update_stats(True); (STATE/"ig_next.json").unlink(missing_ok=True); print("carousel published:",result["id"])


TIPS=[("Liquidity is your exit door","If a pool is tiny, selling moves the price against you. Check liquidity first."),
      ("Verify the contract","Names can be copied. Match the contract with official links before interacting."),
      ("Volume can be faked","Compare volume, liquidity and real transactions. One number never tells the whole story."),
      ("Buy pressure needs context","More buys can signal momentum, but liquidity and holder distribution still matter."),
      ("Fresh means fragile","New pairs have little history. Never confuse momentum with safety."),
      ("Scanner flags need review","A scanner is a first filter, not a guarantee. Confirm findings on the explorer."),
      ("Protect your main wallet","Use a separate low-balance wallet and revoke old approvals regularly.")]


def story_jpg(png):
    png=pathlib.Path(png); jpg=png.with_suffix(".jpg")
    with Image.open(png) as image: image.convert("RGB").save(jpg,"JPEG",quality=92,optimize=True)
    png.unlink(missing_ok=True); return jpg


def job_story_tip_prepare():
    cleanup_media(); index=dt.date.today().toordinal()%len(TIPS); title,body=TIPS[index]
    png=PUBLIC/f"story_tip_{dt.date.today():%Y%m%d}.png"; render_html.story_tip_card({"n":index+1,"title":title,"body":body},png,BRAND.upper())
    jpg=story_jpg(png); save("ig_story_next",{"file":jpg.name,"kind":"tip"}); print("prepared tip story:",title)


def job_story_radar_prepare():
    cleanup_media(); data=pick_promising(False)
    if not data:return
    token=data["token"]; symbol=re.sub(r"[^A-Za-z0-9_-]","",token["symbol"][:10]) or "token"
    prefix=f"story_radar_{dt.datetime.now(dt.timezone.utc):%Y%m%d_%H%M}_{symbol}"; logo=get_logo(data,prefix); palette=design.logo_palette(Image.open(logo)); m={k:v for k,v,_ in data["metrics"]}
    payload={"symbol":token["symbol"].upper()[:12],"chain":token["chain_name"],"chain_key":token["chain"],"verdict":verdict(data),
             "stats":[("1h volume",m["1H VOLUME"]),("Liquidity",m["LIQUIDITY"]),("Radar score",f"{data['radar_score']}/100")],
             "ca":short_ca(token["address"]),"updated":dt.datetime.now(dt.timezone.utc).strftime("%H:%M UTC")}
    png=PUBLIC/f"{prefix}.png"; render_html.story_radar_card(payload,logo,palette,png,BRAND.upper()); jpg=story_jpg(png)
    save("ig_story_next",{"file":jpg.name,"kind":"radar"}); print("prepared radar story:",token["symbol"])


def job_story_publish():
    pending=load("ig_story_next",None)
    if not pending or not pending.get("file"):return print("no story prepared")
    url=media_url(pending["file"])
    if DRY_RUN:return print("[DRY RUN STORY]",url)
    wait_public([url]); result=publish_story(url)
    if not result.get("id"):raise RuntimeError("Instagram did not confirm story publication")
    update_stats(False); (STATE/"ig_story_next.json").unlink(missing_ok=True); print("story published:",result["id"])


def job_diagnose():
    host,user,token=ig_credentials(); response=HTTP.get(f"{host}/{user}",params={"fields":"username,name","access_token":token},timeout=30)
    print("Instagram account:",response.status_code,response.text.replace(token,"***")[:700])
    print("DRY_RUN:",DRY_RUN,"chains:",CHAINS,"minimum score:",MIN_SCORE)


JOBS={"instagram_prepare":job_instagram_prepare,"instagram_publish":job_instagram_publish,
      "story_tip_prepare":job_story_tip_prepare,"story_radar_prepare":job_story_radar_prepare,
      "story_publish":job_story_publish,"diagnose":job_diagnose}

if __name__=="__main__":
    if len(sys.argv)!=2 or sys.argv[1] not in JOBS:raise SystemExit("Usage: python bot.py "+"|".join(JOBS))
    JOBS[sys.argv[1]]()
