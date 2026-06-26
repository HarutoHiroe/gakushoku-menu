# DESIGN.md

UI を実装するときに **最初に読む入口ファイル**。
AI と人間が同じ判断基準で実装・レビューするための「参照順」を固定する。
ここに詳細は書かない。詳細は `design/` 以下へ委譲する。

## Read first（この順で読む）

1. `design/design-tokens.json` — 色・余白・タイポなどの正本（SSoT）
2. `design/component-mapping.md` — デザイン名と実装の対応表
3. `design/operation.md` — 日常運用と検証手順

## Source of truth（正本はどこか）

- **Tokens SSoT**: `design/design-tokens.json`
  （Markdown の説明はあくまで要約。正本は常に JSON 側）
- **Component mapping**: `design/component-mapping.md`

## Rules（必ず守る）

- 外部 UI 語彙（`Drawer` / `Tabs` / `App Bar` など）をそのまま実装名に持ち込まない。
  まず内部の責務・パターンへ正規化してから実装する。
- 新しい見た目をいきなり増やさない。
  既存トークン・既存コンポーネントへの割り当てを先に確認する。
- デザイン関連の変更後は検証を実行する（`design/operation.md` 参照）。

## For AI（AI が最初に読む方針）

- 実装前に必ず上記 1〜3 を読む。
- 「雰囲気でこう」ではなく、トークン名・コンポーネント名で会話する。
- 迷ったら新規追加より、既存への割り当てを優先する。
