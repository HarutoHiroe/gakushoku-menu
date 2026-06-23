#!/usr/bin/env python3
"""学食メニュー有志サイト ビルダー（1週間表示・自己完結版）
本家を「トップGET(セッション確立) → shop_id POST → current_dayを変えてGET」で
スクレイプし、3キャンパス×7日分(今日〜6日後)のメニュー画像URLを埋め込んだ
自己完結 index.html を生成する。

複数日に共通で出る画像(週案内など)は「お知らせ」として分離し、
各日には日替わりメニューだけを表示する。

依存は httpx と beautifulsoup4 のみ（menu CLI には依存しない＝GitHub Actionsでも動く）。
ローカル実行: ~/.local/menu-venv/bin/python3 build.py
"""
import json
import ssl
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
BASE = ("https://signage.univcoop-tokai.net/smt_menu_ants2/view_list.php"
        "?uv=13&current_day=0&current_page=no_page")
DAYS = 7  # 今日を含む表示日数
WD = "月火水木金土日"

SHOPS = [
    {"key": "pacchia",  "id": "29",  "name": "半田キャンパス パッキア", "emoji": "🏫"},
    {"key": "shokusai", "id": "74",  "name": "美浜キャンパス 食菜",     "emoji": "🌊"},
    {"key": "lupo",     "id": "130", "name": "東海キャンパス ルポ",     "emoji": "🚗"},
]

# 本家は古めのTLS設定。SECLEVEL=1 にしないと Python から握手できない
_ctx = ssl.create_default_context()
_ctx.set_ciphers("DEFAULT@SECLEVEL=1")


def parse_image_urls(html):
    """メニューリストHTMLから画像URLを抽出し、大版(.png)・httpsに正規化して返す"""
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
        if url.endswith("s.png"):       # 末尾 s.png(小) → .png(大) に格上げ
            url = url[:-5] + ".png"
        url = url.replace("http://", "https://")  # mixed content 回避
        urls.append(url)
    return urls


def fetch_all():
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).date()
    with httpx.Client(verify=_ctx, timeout=30, follow_redirects=True,
                      headers={"User-Agent": UA, "Accept-Language": "ja-JP"}) as c:
        c.get(BASE)  # ① セッション確立(Set-Cookie)
        for shop in SHOPS:
            rp = c.post(BASE, data={  # ② 店舗をPOST → 今日(day0)
                "shop_id": shop["id"], "client_id": "13", "shop_name": shop["name"],
            })
            raw = {0: parse_image_urls(rp.text)}
            for d in range(1, DAYS):  # ③ 同セッションで各日をGET
                u = BASE.replace("current_day=0", f"current_day={d}")
                raw[d] = parse_image_urls(c.get(u).text)
                time.sleep(0.2)  # 本家への負荷配慮

            # 各日、本家が掲示している画像をそのまま表示（誤判定で消さない）
            shop["days"] = []
            menu_count = 0
            for d in range(DAYS):
                dt = today + timedelta(days=d)
                imgs = raw[d]
                menu_count += len(imgs)
                shop["days"].append({
                    "date": f"{dt.month}/{dt.day}", "wday": WD[dt.weekday()],
                    "weekend": dt.weekday() >= 5, "images": imgs,
                })
            print(f"  {shop['emoji']} {shop['name']}: 画像 計{menu_count}枚", file=sys.stderr)
    return SHOPS


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
  header { text-align: center; padding: 12px 0 18px; }
  header h1 { font-size: 1.6rem; letter-spacing: .02em; }
  header .updated { font-size: .78rem; opacity: .6; margin-top: 6px; }
  .tabs { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; margin-bottom: 16px; }
  .tab {
    border: none; cursor: pointer; font-size: .95rem; font-weight: 600;
    padding: 10px 16px; border-radius: 999px; color: #e7d8ff;
    background: rgba(255,255,255,.08); transition: .15s; backdrop-filter: blur(4px);
  }
  .tab:hover { background: rgba(255,255,255,.16); }
  .tab.active { background: linear-gradient(135deg,#ff6ec4,#7873f5); color: #fff; box-shadow: 0 4px 16px rgba(255,110,196,.4); }
  .panel { display: none; max-width: 860px; margin: 0 auto; }
  .panel.active { display: block; animation: fade .25s ease; }
  @keyframes fade { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
  .panel h2 { text-align: center; font-size: 1.15rem; margin-bottom: 12px; opacity: .92; }
  .notice { background: rgba(255,255,255,.05); border-radius: 14px; padding: 12px; margin-bottom: 16px; }
  .notice-h { font-size: .8rem; opacity: .65; margin-bottom: 8px; text-align: center; }
  .notice img { max-width: 100%; border-radius: 10px; display: block; margin: 0 auto; }
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
  .daytab.active small { opacity: .85; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 360px)); gap: 16px; justify-content: center; }
  .card {
    background: rgba(255,255,255,.06); border-radius: 18px; overflow: hidden;
    box-shadow: 0 8px 24px rgba(0,0,0,.3); transition: transform .15s;
  }
  .card:hover { transform: translateY(-3px); }
  .card img { width: 100%; display: block; background: #fff; }
  .empty {
    text-align: center; padding: 48px 16px; opacity: .7; line-height: 1.8;
    background: rgba(255,255,255,.05); border-radius: 18px;
  }
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
<div class="tabs" id="tabs"></div>
<div id="panels"></div>
<footer>
  非公式・有志ページ / 画像は日本福祉大学生協 signage より<br>
  キャンパスは上のタブ、または URL末尾 <code>#pacchia</code> で直接開けます
</footer>
<script>
const DATA = __DATA__;
const tabs = document.getElementById('tabs');
const panels = document.getElementById('panels');
document.querySelector('.updated').textContent = '取得: ' + DATA.updated;

function cardsHtml(images) {
  if (!images.length) {
    return '<div class="empty">🈚 この日はメニューがないみたい<br>（土日・休業日 / まだ未掲載かも）</div>';
  }
  return '<div class="grid">' + images.map((u) =>
    '<a class="card" href="' + u + '" target="_blank" rel="noopener"><img loading="lazy" src="' + u + '" alt="menu"></a>'
  ).join('') + '</div>';
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
    '<div class="dayview" data-day="' + i + '"' + (i === 0 ? '' : ' hidden') + '>' + cardsHtml(dy.images) + '</div>'
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
