#!/usr/bin/env python3
"""学食メニュー有志サイト ビルダー
本家(signage.univcoop-tokai.net)を2連リクエスト(GET→POST)でスクレイプし、
3キャンパス分の今日のメニュー画像URLを埋め込んだ自己完結 index.html を生成する。
実行: ~/.local/menu-venv/bin/python3 ~/menu-site/build.py
"""
import json
import ssl
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".local" / "menu-venv"))
import httpx
from menu import parse_image_urls, upgrade_image_url

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


def fetch_all():
    with httpx.Client(verify=_ctx, timeout=30, follow_redirects=True,
                      headers={"User-Agent": UA, "Accept-Language": "ja-JP"}) as c:
        c.get(BASE)  # ① セッション確立(Set-Cookie)
        for shop in SHOPS:
            r = c.post(BASE, data={  # ② 店舗をPOST
                "shop_id": shop["id"], "client_id": "13", "shop_name": shop["name"],
            })
            shop["images"] = [upgrade_image_url(im["url"]).replace("http://", "https://")
                              for im in parse_image_urls(r.text)]
            print(f"  {shop['emoji']} {shop['name']}: {len(shop['images'])}枚", file=sys.stderr)
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
  .tabs { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; margin-bottom: 18px; }
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
  .panel h2 { text-align: center; font-size: 1.15rem; margin-bottom: 14px; opacity: .92; }
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

DATA.shops.forEach((s) => {
  const btn = document.createElement('button');
  btn.className = 'tab'; btn.dataset.key = s.key;
  btn.textContent = s.emoji + ' ' + s.name.replace('キャンパス ', ' / ');
  btn.onclick = () => { location.hash = s.key; };
  tabs.appendChild(btn);

  const panel = document.createElement('div');
  panel.className = 'panel'; panel.id = 'panel-' + s.key;
  let inner = '<h2>' + s.emoji + ' ' + s.name + '</h2>';
  if (s.images.length) {
    inner += '<div class="grid">' + s.images.map((u) =>
      '<a class="card" href="' + u + '" target="_blank" rel="noopener"><img loading="lazy" src="' + u + '" alt="menu"></a>'
    ).join('') + '</div>';
  } else {
    inner += '<div class="empty">🈳 今は表示できるメニュー画像がないみたい<br>' +
             '（営業時間外 / 準備中かも。昼ごろにまた見てね）</div>';
  }
  panel.innerHTML = inner;
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
    payload = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "shops": [{"key": s["key"], "name": s["name"], "emoji": s["emoji"],
                   "images": s["images"]} for s in shops],
    }
    out = Path(__file__).resolve().parent / "index.html"
    out.write_text(TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False)),
                   encoding="utf-8")
    print(f"✅ 生成完了: {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
