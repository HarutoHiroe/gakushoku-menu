#!/usr/bin/env python3
"""学食メニュー有志サイト ビルダー（自己完結版）
本家を「トップGET(セッション確立) → shop_id POST → current_dayを変えてGET」で
スクレイプし、3キャンパス×(今日/明日)のメニュー画像URLを埋め込んだ
自己完結 index.html を生成する。

依存は httpx と beautifulsoup4 のみ（menu CLI には依存しない＝GitHub Actionsでも動く）。
ローカル実行: ~/.local/menu-venv/bin/python3 build.py
"""
import json
import ssl
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
BASE = ("https://signage.univcoop-tokai.net/smt_menu_ants2/view_list.php"
        "?uv=13&current_day=0&current_page=no_page")

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
    with httpx.Client(verify=_ctx, timeout=30, follow_redirects=True,
                      headers={"User-Agent": UA, "Accept-Language": "ja-JP"}) as c:
        c.get(BASE)  # ① セッション確立(Set-Cookie)
        url_tomorrow = BASE.replace("current_day=0", "current_day=1")
        for shop in SHOPS:
            rp = c.post(BASE, data={  # ② 店舗をPOST → 今日のメニュー
                "shop_id": shop["id"], "client_id": "13", "shop_name": shop["name"],
            })
            shop["today"] = parse_image_urls(rp.text)
            r1 = c.get(url_tomorrow)  # ③ 同セッションで明日をGET
            shop["tomorrow"] = parse_image_urls(r1.text)
            print(f"  {shop['emoji']} {shop['name']}: "
                  f"今日{len(shop['today'])}枚 / 明日{len(shop['tomorrow'])}枚", file=sys.stderr)
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
  .daytabs { display: flex; gap: 6px; justify-content: center; margin-bottom: 16px; }
  .daytab {
    border: 1px solid rgba(255,255,255,.18); cursor: pointer; font-size: .85rem; font-weight: 600;
    padding: 7px 18px; border-radius: 999px; color: #d9c9ff; background: transparent; transition: .15s;
  }
  .daytab:hover { background: rgba(255,255,255,.1); }
  .daytab.active { background: rgba(255,255,255,.92); color: #3a1d5e; border-color: transparent; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }
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
    return '<div class="empty">🈳 まだメニュー画像がないみたい<br>（未掲載 / 準備中かも。少し待ってね）</div>';
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
  panel.innerHTML =
    '<h2>' + s.emoji + ' ' + s.name + '</h2>' +
    '<div class="daytabs">' +
      '<button class="daytab active" data-day="today">📅 今日</button>' +
      '<button class="daytab" data-day="tomorrow">➡️ 明日</button>' +
    '</div>' +
    '<div class="dayview" data-day="today">' + cardsHtml(s.today) + '</div>' +
    '<div class="dayview" data-day="tomorrow" hidden>' + cardsHtml(s.tomorrow) + '</div>';
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
                   "today": s["today"], "tomorrow": s["tomorrow"]} for s in shops],
    }
    out = Path(__file__).resolve().parent / "index.html"
    out.write_text(TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False)),
                   encoding="utf-8")
    print(f"✅ 生成完了: {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
