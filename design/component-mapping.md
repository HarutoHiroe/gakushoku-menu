# Component Mapping

デザイン名 ↔ 実装の対応を、人の記憶に頼らず固定する表。
デザインツール・コード・レビューが「同じ名前」で会話するための約束事。

| デザイン名 | 実装ファイル / export | 使うトークン | variant / prop |
| --- | --- | --- | --- |
| Primary Button | `src/components/Button.tsx` → `Button` | `color.accent.*`, `radius.md`, `space.3` | `variant="primary" \| "ghost" \| "danger"` |
| Card | `src/components/Card.tsx` → `Card` | `color.bg.elevated`, `color.border.base`, `radius.lg` | `padding="md" \| "lg"` |
| Text Field | `src/components/TextField.tsx` → `TextField` | `color.bg.subtle`, `color.border.base`, `space.2` | `state="default" \| "error"` |

## ルール

- 行を増やすときは **4 列すべて** を埋める（デザイン名・実装・トークン・variant）。
- 実装ファイル名や export 名を変えたら、**同じ PR でこの表も更新する**。
- ここに無い見た目を実装したくなったら、まず既存行への割り当てを検討する。
