#!/usr/bin/env python3
"""学食メニュー有志サイト ビルダー（1週間表示 + 栄養解析・コンボ最適化版）

本家を「トップGET → shop_id POST → current_day切替GET」でスクレイプし、
3キャンパス×7日分のメニュー画像URLを取得。各メニュー画像を Claude Sonnet 4.6 で
解析して栄養・価格を抽出し、自己完結 index.html を生成する。

解析結果は cache/<画像ID>.json に保存（画像ID単位＝同じ画像は二度と再解析しない）。
このキャッシュをリポジトリにコミットすることで、GitHub Actions 間でも永続化し
二重課金を防ぐ。

依存: httpx, beautifulsoup4, anthropic, python-dotenv（GitHub Actions でも動く）
ローカル実行: ~/.local/menu-venv/bin/python3 build.py
APIキー: ローカルは ~/.local/menu-venv/.env、Actions は Secrets の ANTHROPIC_API_KEY
"""
import base64
import json
import os
import re
import ssl
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
import anthropic

# ローカル実行用に .env からキーを読む（Actions ではキーが環境変数で渡るので無害にスキップ）
try:
    from dotenv import load_dotenv
    load_dotenv(Path.home() / ".local" / "menu-venv" / ".env")
except Exception:
    pass

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
BASE = ("https://signage.univcoop-tokai.net/smt_menu_ants2/view_list.php"
        "?uv=13&current_day=0&current_page=no_page")
DAYS = 7
WD = "月火水木金土日"
MODEL = "claude-sonnet-4-6"  # 栄養の小さい数字も読むため Sonnet（Haikuは読み飛ばす）

SHOPS = [
    {"key": "pacchia",  "id": "29",  "name": "半田キャンパス パッキア", "emoji": "🏫"},
    {"key": "shokusai", "id": "74",  "name": "美浜キャンパス 食菜",     "emoji": "🌊"},
    {"key": "lupo",     "id": "130", "name": "東海キャンパス ルポ",     "emoji": "🚗"},
]

CACHE_DIR = Path(__file__).resolve().parent / "cache"

_ctx = ssl.create_default_context()
_ctx.set_ciphers("DEFAULT@SECLEVEL=1")  # 本家の古いTLS対策


# ============================================================
# 画像URL抽出
# ============================================================
def parse_image_urls(html):
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for li in soup.select("li.item"):
        img = li.find("img")
        if not img or not img.get("src"):
            continue
        url = img["src"]
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://signage.univcoop-tokai.net" + url
        if url.endswith("s.png"):
            url = url[:-5] + ".png"
        url = url.replace("http://", "https://")
        urls.append(url)
    return urls


# ============================================================
# Claude 解析（menu CLI のロジックを移植・dict版）
# ============================================================
NAME_FIXES = {
    "スカツカレー": "ロースカツカレー", "スカツ丼": "ロースカツ丼",
    "ースカツカレー": "ロースカツカレー", "ースカツ丼": "ロースカツ丼",
}
NAME_PATTERNS = [
    (re.compile(r"^スカツ"), "ロースカツ"),
    (re.compile(r"^ースカツ"), "ロースカツ"),
]
SIZE_ORDER = {"小": 0, "並": 1, "中": 2, "大": 3}


def fix_dish_name(name):
    if not name:
        return name
    if name in NAME_FIXES:
        return NAME_FIXES[name]
    for pat, repl in NAME_PATTERNS:
        if pat.search(name):
            return pat.sub(repl, name)
    return name


def extract_json(text):
    """AI応答からJSONを取り出す（フェンス/前置き/末尾切れに強い）"""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
    start = candidate.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(candidate)):
            c = candidate[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(candidate[start:i + 1])
                    except json.JSONDecodeError:
                        break
    m = re.search(r"\{.*\}", candidate, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def normalize_dish(d):
    """AI出力のdishを整える。サイズ・価格を正規化して描画用dictにする"""
    sizes = {k: int(v) for k, v in (d.get("sizes") or {}).items() if v}
    if not sizes:
        sizes = {"並": int(d.get("price") or 0)}
    if "中" in sizes:
        price = sizes["中"]
    else:
        price = int(d.get("price") or 0)
        if price not in sizes.values():
            price = sorted(sizes.items(), key=lambda kv: SIZE_ORDER.get(kv[0], 9))[0][1]
    return {
        "name": fix_dish_name(d.get("name", "")),
        "price": price,
        "sizes": sizes,
        "energy": d.get("energy"),
        "protein": d.get("protein"),
        "fat": d.get("fat"),
        "carb": d.get("carb"),
        "category": d.get("category", "その他"),
    }


def is_valid_menu(dishes):
    """ちゃんと食事メニューか判定（告知ポスターを弾く）"""
    if len(dishes) < 2:
        return False
    priced = sum(1 for d in dishes if d.get("price"))
    return priced >= max(2, len(dishes) // 2)


ANALYZE_PROMPT = """この画像は日本の大学食堂の今日のメニュー一覧です。
画像に写っている**全ての**メニューの情報を**漏れなく正確に**抽出してください。

# 最重要ルール1: サイズ別価格を必ず読み取る

丼・カレー・麺などの「ご飯もの・麺もの」には、価格バッジの近くに
**複数サイズの価格**が併記されています。
例: 「小 440」「中 528」「大 660」のように 小・中・大 の3段階。
- 大きく目立つ価格は通常「中」サイズです。
- その周囲に小さく「小◯◯」「大◯◯」が書かれています。**これらも必ず読み取ること**。
- 読み取れたサイズだけ入れてOK（例: 中と大しか無ければ {"中":528,"大":660}）。
- サイズ展開が無い単品（主菜・小鉢・サラダ・デザート等）は1価格だけでOK。

# 最重要ルール2: 栄養情報は必ず読み取る（中サイズ基準）

各料理の写真の右側または上部に**栄養成分表**が必ず記載されています：
- エネルギー (kcal)
- タンパク質 (g)
- 脂質 (g)
- 炭水化物 (g)
- 食塩相当量 (g)

これらは通常「**中サイズ基準**」の数値です（小さく「中サイズのものです」と注記あり）。
**全ての料理にこれらの数値が記載されている**ので、絶対に見落とさず読み取ってください。
たとえ小さい文字でも、必ず数値を抽出してください。
nullを返すのは、本当に画像上に数値が見当たらない場合のみです。

特に画面下半分の小鉢・サラダ・デザート類も、栄養情報が必ず書かれています。

# カテゴリ分類のルール
- カレーライス、丼物（〜丼、〜ライス） → "丼"
- ハンバーグ、フライ、塩焼きなどメイン1品 → "主菜"
- ラーメン、うどん、そば、〜麺 → "麺"
- 味噌汁、豚汁、スープ → "汁物"
- サラダ → "サラダ"
- ライス（白米のみ） → "ご飯"
- 煮物、和え物などの副菜 → "小鉢"
- ケーキ、タルト、もちなど → "デザート"

特に「カツカレー」「カレーライス」は必ず**"丼"**として分類すること。

# 料理名の補完
画像のレイアウトの都合で頭文字が見切れている場合は補完してください:
- 「スカツカレー」→「ロースカツカレー」
- 「ースカツ」→「ロースカツ」
英語名や写真も参考にして正確な料理名を判定してください。

# 出力形式
以下のJSON形式で、**JSONのみ**回答してください。

{
  "dishes": [
    {
      "name": "正確な料理名",
      "price": 中サイズまたは単独表示の価格(整数・税込),
      "sizes": {"小": 440, "中": 528, "大": 660},
      "energy": カロリー(整数・kcal),
      "protein": タンパク質(小数・g),
      "fat": 脂質(小数・g),
      "carb": 炭水化物(小数・g),
      "salt": 食塩相当量(小数・g),
      "category": "主菜|丼|麺|汁物|サラダ|ご飯|小鉢|デザート|その他",
      "allergens": ["卵","乳","小麦"などのアレルゲン]
    }
  ]
}

- "sizes" は読み取れたサイズだけ入れる。サイズ展開が無い品は省略するか {"並": 価格} にする。
- "price" は必ず「中」（または単独）の税込価格にすること。
**サイズ価格・栄養数値の見落としは厳禁です。必ず全料理の全項目を埋めること。**
"""


def analyze_image_url(client, url):
    """画像URLを解析して栄養付きdishリストを返す。画像ID単位でキャッシュ（再解析しない）"""
    img_id = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]  # 0000042193
    cf = CACHE_DIR / f"{img_id}.json"
    if cf.exists():
        try:
            cached = json.loads(cf.read_text(encoding="utf-8"))
            return cached["dishes"] if cached.get("valid") else []
        except Exception:
            pass

    # 画像ダウンロード
    try:
        with httpx.Client(verify=_ctx, timeout=30, follow_redirects=True,
                          headers={"User-Agent": UA}) as c:
            r = c.get(url)
            if not r.is_success:
                return []
            img_bytes = r.content
    except Exception as e:
        print(f"  ⚠️ 画像DL失敗 {img_id}: {e}", file=sys.stderr)
        return []

    b64 = base64.standard_b64encode(img_bytes).decode("ascii")
    media = "image/png"
    print(f"  🤖 解析中 {img_id} …", file=sys.stderr)
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=8000,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                {"type": "text", "text": ANALYZE_PROMPT},
            ]}],
        )
    except Exception as e:
        print(f"  ❌ Claude解析失敗 {img_id}: {e}", file=sys.stderr)
        return []

    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    data = extract_json(text)
    raw = (data or {}).get("dishes", []) if data else []
    dishes = [normalize_dish(d) for d in raw if d.get("name")]
    valid = is_valid_menu(dishes)
    CACHE_DIR.mkdir(exist_ok=True)
    cf.write_text(json.dumps({"dishes": dishes, "valid": valid}, ensure_ascii=False, indent=1),
                  encoding="utf-8")
    u = resp.usage
    print(f"     → {len(dishes)}品 / valid={valid} / in {u.input_tokens}tok out {u.output_tokens}tok",
          file=sys.stderr)
    return dishes if valid else []


# ============================================================
# 取得＋解析
# ============================================================
def fetch_all():
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).date()
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境から
    with httpx.Client(verify=_ctx, timeout=30, follow_redirects=True,
                      headers={"User-Agent": UA, "Accept-Language": "ja-JP"}) as c:
        c.get(BASE)
        for shop in SHOPS:
            print(f"{shop['emoji']} {shop['name']}", file=sys.stderr)
            rp = c.post(BASE, data={"shop_id": shop["id"], "client_id": "13",
                                    "shop_name": shop["name"]})
            raw = {0: parse_image_urls(rp.text)}
            for d in range(1, DAYS):
                u = BASE.replace("current_day=0", f"current_day={d}")
                raw[d] = parse_image_urls(c.get(u).text)
                time.sleep(0.2)

            # ユニークな画像だけ1回解析（キャッシュで二重課金防止）
            analyzed = {}
            for d in range(DAYS):
                for url in raw[d]:
                    if url not in analyzed:
                        analyzed[url] = analyze_image_url(client, url)

            shop["days"] = []
            for d in range(DAYS):
                dt = today + timedelta(days=d)
                dishes, seen = [], set()
                for url in raw[d]:
                    for dish in analyzed.get(url, []):
                        k = (dish["name"], dish["price"])
                        if k not in seen:
                            seen.add(k)
                            dishes.append(dish)
                shop["days"].append({
                    "date": f"{dt.month}/{dt.day}", "wday": WD[dt.weekday()],
                    "weekend": dt.weekday() >= 5, "images": raw[d], "dishes": dishes,
                })
    return SHOPS


# ============================================================
# HTML
# ============================================================
TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🍱 日福 学食メニュー</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: system-ui, -apple-system, "Hiragino Sans", sans-serif;
    background: linear-gradient(160deg, #1a1030 0%, #2d1b4e 50%, #4a1d5e 100%);
    color: #f3eaff; min-height: 100vh; padding: 16px;
  }
  header { text-align: center; padding: 12px 0 14px; }
  header h1 { font-size: 1.6rem; letter-spacing: .02em; }
  header .updated { font-size: .78rem; opacity: .6; margin-top: 6px; }
  .budget-bar { text-align: center; margin: 10px 0 18px; font-size: .9rem; }
  .budget-bar input {
    width: 92px; font-size: 1rem; font-weight: 700; text-align: right;
    padding: 6px 10px; border-radius: 10px; border: 1px solid rgba(255,255,255,.25);
    background: rgba(255,255,255,.1); color: #fff;
  }
  .budget-bar .preset { cursor: pointer; text-decoration: underline; opacity: .75; margin-left: 8px; }
  .half-toggle { display: inline-block; margin-left: 14px; cursor: pointer; user-select: none; padding: 5px 12px; border-radius: 999px; background: rgba(255,80,80,.18); border: 1px solid rgba(255,120,120,.45); font-weight: 600; }
  .half-toggle input { vertical-align: middle; margin-right: 3px; }
  .half-on { text-align: center; font-weight: 800; color: #ffd0d0; background: linear-gradient(135deg, rgba(255,70,70,.35), rgba(255,120,60,.3)); border-radius: 12px; padding: 8px; margin: 16px 0 4px; }
  .tabs { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; margin-bottom: 16px; }
  .tab {
    border: none; cursor: pointer; font-size: .95rem; font-weight: 600;
    padding: 10px 16px; border-radius: 999px; color: #e7d8ff;
    background: rgba(255,255,255,.08); transition: .15s; backdrop-filter: blur(4px);
  }
  .tab:hover { background: rgba(255,255,255,.16); }
  .tab.active { background: linear-gradient(135deg,#ff6ec4,#7873f5); color: #fff; box-shadow: 0 4px 16px rgba(255,110,196,.4); }
  .panel { display: none; max-width: 880px; margin: 0 auto; }
  .panel.active { display: block; animation: fade .25s ease; }
  @keyframes fade { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
  .panel h2 { text-align: center; font-size: 1.15rem; margin-bottom: 12px; opacity: .92; }
  .daytabs {
    display: flex; gap: 6px; margin-bottom: 16px; overflow-x: auto;
    padding-bottom: 6px; -webkit-overflow-scrolling: touch; scrollbar-width: thin;
  }
  .daytab {
    flex: 0 0 auto; border: 1px solid rgba(255,255,255,.18); cursor: pointer;
    font-size: .82rem; font-weight: 600; padding: 7px 13px; border-radius: 14px;
    color: #d9c9ff; background: transparent; transition: .15s; line-height: 1.25; text-align: center;
  }
  .daytab small { display: block; opacity: .7; font-size: .72em; font-weight: 500; }
  .daytab:hover { background: rgba(255,255,255,.1); }
  .daytab.weekend { color: #ff9ed8; }
  .daytab.active { background: rgba(255,255,255,.92); color: #3a1d5e; border-color: transparent; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 320px)); gap: 14px; justify-content: center; }
  .card { background: rgba(255,255,255,.06); border-radius: 16px; overflow: hidden; box-shadow: 0 8px 24px rgba(0,0,0,.3); }
  .card img { width: 100%; display: block; background: #fff; }
  .empty { text-align: center; padding: 40px 16px; opacity: .7; line-height: 1.8; background: rgba(255,255,255,.05); border-radius: 18px; }
  .sec-h { font-size: .82rem; opacity: .7; margin: 20px 0 8px; font-weight: 700; }
  .nut-wrap { overflow-x: auto; border-radius: 12px; -webkit-overflow-scrolling: touch; }
  table.nut { width: 100%; min-width: 480px; border-collapse: collapse; font-size: .8rem; background: rgba(255,255,255,.04); }
  table.nut th { background: rgba(255,255,255,.1); padding: 7px 8px; text-align: right; font-weight: 600; white-space: nowrap; }
  table.nut th:first-child, table.nut td:first-child { text-align: left; }
  table.nut th:nth-child(2), table.nut td:nth-child(2) { text-align: center; }
  table.nut td { padding: 6px 8px; text-align: right; border-top: 1px solid rgba(255,255,255,.06); white-space: nowrap; }
  table.nut td:first-child { white-space: normal; min-width: 120px; }
  .size-sel { display: inline-block; margin-left: 12px; }
  .size-sel select { background: rgba(255,255,255,.12); color: #fff; border: 1px solid rgba(255,255,255,.28); border-radius: 8px; padding: 4px 7px; font-size: .85rem; }
  .dsel-bar { margin: 16px 0 0; text-align: center; font-size: .85rem; }
  .dsel-bar select { background: rgba(255,180,80,.16); color: #fff; border: 1px solid rgba(255,200,100,.45); border-radius: 8px; padding: 5px 8px; font-size: .85rem; margin-left: 4px; }
  table.nut tr:hover td { background: rgba(255,255,255,.05); }
  .nut-cat { color: #c9b6ff; }
  .total { text-align: right; font-size: .76rem; opacity: .65; margin-top: 6px; }
  .combo-card {
    background: rgba(255,255,255,.06); border: 1px solid rgba(120,115,245,.35);
    border-radius: 14px; padding: 11px 13px; margin-top: 10px; position: relative;
  }
  .combo-rank { position: absolute; top: -9px; left: 12px; background: linear-gradient(135deg,#ffb347,#ffcc33); color: #4a1d5e; font-weight: 800; font-size: .72rem; padding: 2px 9px; border-radius: 999px; }
  .combo-names { font-weight: 700; margin-bottom: 5px; padding-top: 2px; }
  .combo-stats { font-size: .8rem; opacity: .92; }
  .combo-stats .diff { color: #7fe6a0; }
  .combo-badge { font-size: .76rem; opacity: .8; margin-top: 3px; }
  .combo-empty { opacity: .6; padding: 12px; text-align: center; }
  footer { text-align: center; font-size: .72rem; opacity: .45; margin-top: 28px; line-height: 1.7; }
  a { color: #ff9ed8; }
  code { background: rgba(255,255,255,.1); padding: 1px 5px; border-radius: 5px; }
</style>
</head>
<body>
<header>
  <h1>🍱 日福 学食メニュー</h1>
  <div class="updated"></div>
</header>
<div class="budget-bar">
  🎫 予算 ¥<input type="number" id="budget" value="740" min="100" step="10">
  <span class="preset" data-v="740">学食パス740</span>
  <span class="preset" data-v="980">🌟高級980</span>
  <span class="preset" data-v="600">節約600</span>
  <label class="half-toggle"><input type="checkbox" id="half">🉐半額week</label>
  <span class="size-sel">🍚<select id="rsize">
    <option value="">サイズおまかせ</option>
    <option value="小">小で固定</option>
    <option value="中">中で固定</option>
    <option value="大">大で固定</option>
  </select></span>
</div>
<div class="tabs" id="tabs"></div>
<div id="panels"></div>
<footer>
  非公式・有志ページ / 画像は日本福祉大学生協 signage より / 栄養はAI解析（誤りがある場合あり・中サイズ基準）<br>
  キャンパスは上のタブ、または URL末尾 <code>#pacchia</code> で直接開けます
</footer>
<script>
const DATA = __DATA__;
const tabs = document.getElementById('tabs');
const panels = document.getElementById('panels');
document.querySelector('.updated').textContent = '取得: ' + DATA.updated;

const SIZE_ORDER = {"小":0,"並":1,"中":2,"大":3};
const SIZE_RICE_DELTA = {"小":[-100,-24],"並":[0,0],"中":[0,0],"大":[150,36]};
const MAIN = ["主菜","丼","麺"], CARB = ["丼","麺","ご飯"], SOLO_NG = ["汁物","小鉢","サラダ","ご飯","デザート"];

function num(x){ return (x==null)?null:Number(x); }

function cardsHtml(images) {
  if (!images.length) return '<div class="empty">🈚 この日はメニューがないみたい<br>（土日・休業日 / まだ未掲載かも）</div>';
  return '<div class="grid">' + images.map((u) =>
    '<a class="card" href="' + u + '" target="_blank" rel="noopener"><img loading="lazy" src="' + u + '" alt="menu"></a>'
  ).join('') + '</div>';
}

function fmtPrice(d){
  const ks = Object.keys(d.sizes||{});
  if (ks.length > 1) {
    return Object.entries(d.sizes).sort((a,b)=>(SIZE_ORDER[a[0]]??9)-(SIZE_ORDER[b[0]]??9)).map(e=>e[1]).join('/');
  }
  return '¥' + d.price;
}
function nutritionTable(dishes){
  if (!dishes.length) return '';
  const rows = dishes.map((d)=>{
    const p=num(d.protein), f=num(d.fat), c=num(d.carb), e=num(d.energy);
    return '<tr><td>'+d.name+'</td><td class="nut-cat">'+d.category+'</td><td>'+fmtPrice(d)+'</td>'+
      '<td>'+(e!=null?e:'-')+'</td><td>'+(p!=null?p.toFixed(1):'-')+'</td>'+
      '<td>'+(f!=null?f.toFixed(1):'-')+'</td><td>'+(c!=null?c.toFixed(1):'-')+'</td></tr>';
  }).join('');
  const tp = dishes.reduce((s,d)=>s+d.price,0);
  const tk = dishes.reduce((s,d)=>s+(num(d.energy)||0),0);
  return '<div class="sec-h">📋 栄養一覧</div><div class="nut-wrap"><table class="nut"><thead><tr>'+
    '<th>料理</th><th>区分</th><th>価格</th><th>kcal</th><th>P</th><th>F</th><th>C</th></tr></thead>'+
    '<tbody>'+rows+'</tbody></table></div>'+
    '<div class="total">全'+dishes.length+'品 / 全部頼むと ¥'+tp+'（'+tk+'kcal）</div>'+
    '<div class="total" style="opacity:.5">価格が複数値の料理は 小/中/大（kcal等は中基準）</div>';
}

function expandSizes(dishes, onlySize){
  const out=[];
  for(const d of dishes){
    const sizes = (d.sizes && Object.keys(d.sizes).length) ? d.sizes : {"並": d.price};
    const multi = Object.keys(sizes).length>1;
    for(const [sz,price] of Object.entries(sizes).sort((a,b)=>(SIZE_ORDER[a[0]]??9)-(SIZE_ORDER[b[0]]??9))){
      if(onlySize && multi && sz!==onlySize) continue;  // サイズ固定（指定サイズ以外は除外）
      const [dk,dc] = SIZE_RICE_DELTA[sz]||[0,0];
      let e=num(d.energy), c=num(d.carb);
      if(multi && e!=null) e=Math.max(0,e+dk);
      if(multi && c!=null) c=Math.max(0,c+dc);
      out.push({name: multi?(d.name+'('+sz+')'):d.name, price:Math.round(price),
                energy:e, protein:num(d.protein), fat:num(d.fat), carb:c,
                category:d.category, base:d.name});
    }
  }
  return out;
}
function* combinations(arr,r){
  const n=arr.length; if(r>n) return;
  const idx=[...Array(r).keys()];
  while(true){
    yield idx.map(i=>arr[i]);
    let i=r-1; while(i>=0 && idx[i]===i+n-r) i--;
    if(i<0) break;
    idx[i]++; for(let j=i+1;j<r;j++) idx[j]=idx[j-1]+1;
  }
}
function suggestCombos(dishes, budget, onlySize, topN=3, maxItems=4){
  const v = expandSizes(dishes, onlySize), all=[];
  for(let r=1;r<=Math.min(v.length,maxItems);r++){
    for(const combo of combinations(v,r)){
      const bases=combo.map(d=>d.base);
      if(new Set(bases).size!==bases.length) continue;
      const price=combo.reduce((s,d)=>s+d.price,0);
      if(price>budget) continue;
      if(combo.filter((d)=>CARB.includes(d.category)).length > 1) continue;  // ご飯もの(丼/麺/ご飯)は1つまで＝丼+ライス等の重複を防ぐ
      if(r===1 && SOLO_NG.includes(combo[0].category)) continue;
      const energy=combo.reduce((s,d)=>s+(d.energy||0),0);
      const protein=combo.reduce((s,d)=>s+(d.protein||0),0);
      const fat=combo.reduce((s,d)=>s+(d.fat||0),0);
      const carb=combo.reduce((s,d)=>s+(d.carb||0),0);
      const balanced=combo.some(d=>MAIN.includes(d.category));
      const hasCarb=combo.some(d=>CARB.includes(d.category));
      all.push({combo,price,diff:budget-price,energy,protein,fat,carb,balanced,hasCarb});
    }
  }
  all.sort((a,b)=> a.diff-b.diff || (b.balanced-a.balanced) || (b.energy-a.energy));
  return all.slice(0,topN);
}
function comboHtml(dishes, budget, onlySize, chosen){
  if(!dishes.length) return '';
  let cs;
  if(chosen){
    // 選んだデザートを先打ち確定 → 残予算で食事ベスト3を最適化（リア友案）
    const meals = dishes.filter((d) => d.category !== 'デザート');
    const rem = budget - chosen.price;
    cs = (rem >= 0 ? suggestCombos(meals, rem, onlySize, 3) : []).map((best) => {
      const dv = {name:chosen.name, price:chosen.price, energy:num(chosen.energy), protein:num(chosen.protein),
                  fat:num(chosen.fat), carb:num(chosen.carb), category:chosen.category, base:chosen.name};
      const combo = [...best.combo, dv];
      const price = best.price + dv.price;
      return {combo, price, diff:budget-price,
              energy:best.energy+(dv.energy||0), protein:best.protein+(dv.protein||0),
              fat:best.fat+(dv.fat||0), carb:best.carb+(dv.carb||0),
              balanced:combo.some((d)=>MAIN.includes(d.category)),
              hasCarb:combo.some((d)=>CARB.includes(d.category))};
    });
  } else {
    cs = suggestCombos(dishes, budget, onlySize, 3);
  }
  let body;
  if(!cs.length){
    body='<div class="combo-empty">¥'+budget+'以内の組み合わせが見つからなかった〜！予算を上げてみて</div>';
  } else {
    body=cs.map((c,i)=>{
      const names=c.combo.map(d=>d.name+'('+d.category+')').join(' + ');
      const b=(c.balanced?'⭐主菜あり ':'')+(c.hasCarb?'🍚炭水化物あり':'')||'🍃軽め';
      return '<div class="combo-card"><div class="combo-rank">#'+(i+1)+'</div>'+
        '<div class="combo-names">'+names+'</div>'+
        '<div class="combo-stats"><b>¥'+c.price+'</b> <span class="diff">(残¥'+c.diff+')</span> / '+
        c.energy+'kcal / P'+c.protein.toFixed(1)+' F'+c.fat.toFixed(1)+' C'+c.carb.toFixed(1)+'</div>'+
        '<div class="combo-badge">'+b+'</div></div>';
    }).join('');
  }
  const sztag = onlySize ? '（🍚'+onlySize+'固定）' : '';
  const dtag = chosen ? '（🍰'+chosen.name+'込み）' : '';
  return '<div class="sec-h">🎫 予算 ¥<span class="bv">'+budget+'</span> スレスレ最適化 TOP3'+sztag+dtag+'</div>'+body;
}

DATA.shops.forEach((s) => {
  const btn = document.createElement('button');
  btn.className = 'tab'; btn.dataset.key = s.key;
  btn.textContent = s.emoji + ' ' + s.name.replace('キャンパス ', ' / ');
  btn.onclick = () => { location.hash = s.key; };
  tabs.appendChild(btn);

  const panel = document.createElement('div');
  panel.className = 'panel'; panel.id = 'panel-' + s.key;
  const dayTabs = s.days.map((dy, i) =>
    '<button class="daytab' + (i === 0 ? ' active' : '') + (dy.weekend ? ' weekend' : '') +
    '" data-day="' + i + '">' + dy.date + '<small>' + dy.wday + '</small></button>'
  ).join('');
  const dayViews = s.days.map((dy, i) =>
    '<div class="dayview" data-day="' + i + '"' + (i === 0 ? '' : ' hidden') + '>' +
      cardsHtml(dy.images) +
      (dy.dishes.length ? '<div class="dyn" id="dyn-' + s.key + '-' + i + '"></div>' : '') +
    '</div>'
  ).join('');
  panel.innerHTML = '<h2>' + s.emoji + ' ' + s.name + '</h2>' +
    '<div class="daytabs">' + dayTabs + '</div>' + dayViews;
  panel.querySelectorAll('.daytab').forEach((b) => {
    b.onclick = () => {
      panel.querySelectorAll('.daytab').forEach((x) => x.classList.toggle('active', x === b));
      panel.querySelectorAll('.dayview').forEach((v) => { v.hidden = v.dataset.day !== b.dataset.day; });
    };
  });
  panels.appendChild(panel);
});

function applyHalf(dishes){
  // 🉐半額week: 全品50%OFF（menu CLI の apply_discount 相当）
  return dishes.map((d) => ({
    ...d,
    price: Math.max(0, Math.round(d.price / 2)),
    sizes: Object.fromEntries(Object.entries(d.sizes || {}).map(([k, v]) => [k, Math.max(0, Math.round(v / 2))])),
  }));
}
const dessertSel = {};  // "shopkey-dayidx" → 選択中のデザート名（各日ごとに保持）
function renderAll(){
  const budget = parseInt(document.getElementById('budget').value) || 740;
  const half = document.getElementById('half').checked;
  const onlySize = document.getElementById('rsize').value || null;
  DATA.shops.forEach((s) => s.days.forEach((dy, i) => {
    const el = document.getElementById('dyn-' + s.key + '-' + i);
    if (!el) return;
    const dishes = half ? applyHalf(dy.dishes) : dy.dishes;
    const key = s.key + '-' + i;
    const dayDesserts = dishes.filter((d) => d.category === 'デザート');
    const chosenName = dessertSel[key] || '';
    let dselHtml = '';
    if (dayDesserts.length) {
      dselHtml = '<div class="dsel-bar">🍰 デザート: <select class="dsel" data-key="' + key + '">' +
        '<option value="">なし</option>' +
        dayDesserts.map((d) => '<option value="' + d.name + '"' + (d.name === chosenName ? ' selected' : '') + '>' + d.name + ' ¥' + d.price + '</option>').join('') +
        '</select></div>';
    }
    const chosen = chosenName ? dayDesserts.find((d) => d.name === chosenName) : null;
    el.innerHTML = (half ? '<div class="half-on">🉐 半額week適用中！ 全品50%OFFで計算中</div>' : '') +
      nutritionTable(dishes) + dselHtml + comboHtml(dishes, budget, onlySize, chosen);
  }));
}
document.getElementById('budget').addEventListener('input', renderAll);
document.getElementById('half').addEventListener('change', renderAll);
document.getElementById('rsize').addEventListener('change', renderAll);
document.addEventListener('change', (e) => {
  if (e.target.classList && e.target.classList.contains('dsel')) {
    dessertSel[e.target.dataset.key] = e.target.value;
    renderAll();
  }
});
document.querySelectorAll('.budget-bar .preset').forEach((p) => {
  p.onclick = () => { document.getElementById('budget').value = p.dataset.v; renderAll(); };
});
renderAll();

function show(key) {
  if (!DATA.shops.some((s) => s.key === key)) key = DATA.shops[0].key;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.key === key));
  document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.id === 'panel-' + key));
}
show(location.hash.slice(1));
window.addEventListener('hashchange', () => show(location.hash.slice(1)));
</script>
</body>
</html>
"""


def main():
    shops = fetch_all()
    jst = timezone(timedelta(hours=9))
    payload = {
        "updated": datetime.now(jst).strftime("%Y-%m-%d %H:%M JST"),
        "shops": [{"key": s["key"], "name": s["name"], "emoji": s["emoji"],
                   "days": s["days"]} for s in shops],
    }
    out = Path(__file__).resolve().parent / "index.html"
    out.write_text(TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False)),
                   encoding="utf-8")
    print(f"✅ 生成完了: {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
