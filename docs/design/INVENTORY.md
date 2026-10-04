# 画面の色・文字・余白の棚卸し(現状。見た目は変えていない。D-239)

`web/index.html` の `<style>` から機械的に抜き出した一覧(F3-2a の時点)。新しく作る部分は「まとめた値」(CSS の変数)で指定し、既存の画面の置き換えはデザインの見直しで行う(REQUESTS)。

## まとめた値(CSS の変数。`:root`)

| 変数 | 明るい | 暗い | 意味 |
|---|---|---|---|
| `--bg` | `#ffffff` | `#0d1117` | 背景 |
| `--fg` | `#1f2328` | `#e6edf3` | 文字 |
| `--muted` | `#57606a` | `#9198a1` | 薄い文字 |
| `--line` | `#d0d7de` | `#3d444d` | 線 |
| `--accent` | `#0969da` | `#4493f8` | 強調(ボタン・リンク) |
| `--accent-fg` | `#ffffff` | `#0d1117` | 強調の上の文字 |
| `--ok` | `#1a7f37` | `#3fb950` | よい(勝ち) |
| `--ng` | `#cf222e` | `#ff7b72` | 悪い(負け・エラー) |
| `--box` | `#f6f8fa` | `#161b22` | 箱の背景 |
| `--mine` | `#fff8c5` | `#3b2e00` | 自球団の背景 |
| `--mine-line` | `#d4a72c` | `#bb8009` | 自球団の線 |
| `--warn-bg` | `#fff1e5` | `#2d1a0a` | 注意の背景 |
| `--warn-line` | `#e16f24` | `#db6d28` | 注意の線・文字 |

色の変数は最初からある。余白・文字の大きさ・角丸の変数(`--space-*`・`--font-*`・`--radius`)は F3-2a で足し、契約の表示など新しく作る部分で使う。

## 同じ意味なのに値が違う箇所(デザインの見直しで整理する候補)

| 意味 | 今の値 | 場所 |
|---|---|---|
| 小さい文字 | 0.75rem / 0.8rem / 0.85rem / 0.88rem / 0.9rem / 0.92rem / 0.95rem | `.sub`・`.dirty`(0.75)、表の見出し `th`(0.8)、`.small`(0.85)、指標の解説 `.guide-item .formula`・`ul`(0.88)、`.muted`・`.error`・`.info`(0.9)、`table`・`.notice`(0.92)、`select`・`.check`・`.team .pick`(0.95) |
| ボタンの最小の高さ | 32px / 40px / 44px / 56px | 表の見出しの並べ替え `th button.sort`(32)、`.back`・`select`・`.check`・小さい切り替え `.seg.small-seg button`(40)、ふつうのボタン `button`・`summary`(44)、下のタブ `.tabs button`(56) |
| 角丸 | 6px / 8px / 10px / 12px | `.info`・`.desc`・`.formula`(6)、`select`・入力欄(8)、`.table-wrap`・`.seg`・`.card`(10)、`fieldset`(12) |
| 箱の内側の余白 | 8px 10px / 10px 12px / 4px 12px 12px / 12px 16px | `.info`(8 10)、`.game-card`・`.card`(10 12)、`fieldset`(4 12 12)、画面 `.screen`(12 16) |
| 縦の外側の余白 | 4px / 6px / 8px / 12px / 16px | 見出し・段落・箱・表のまわりで混在(`h1` 4、`ul.games li`・`.game-card` 6、`.seg`・`.filters`・`.info` 8、`fieldset`・`dl.terms`・`details` 12、`.boot` 16) |
| 薄い文字の色 | `var(--muted)` / `#57606a` 直書きなし | 直書きの色はない(すべて変数)。濃淡の段階は 1 つだけ |
| 線 | `1px solid var(--line)` / `box-shadow: 2px 0 0 var(--line)` / `inset 4px 0 0 var(--mine-line)` | 表の固定列の境目だけ影で線を描いている |
| 最大の幅 | 640px / 1180px / 9em / 9.5em / 10em | 画面(640。広い画面の手続き・振り返りは 1180)と、名前の列(9em・10em)・補足 `.sub`(9.5em) |
| 文字の色の強弱 | `font-weight: bold` を 名前・自球団・並び順の見出し・箱の見出し で使用 | 太字の基準は決めていない |

## 性質ごとの、使っている値と場所(機械的な抜き出し)

### 文字の色(`color`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `var(--muted)` | 土台(変数・本文・ボタン):`.muted`、下のタブ:`.tabs button`、順位表:`th`、順位表:`.rank`、順位表:`dl.terms dd` ほか 8 件 |
| `var(--accent)` | 土台(変数・本文・ボタン):`button.secondary, .button.secondary`、下のタブ:`.tabs button[aria-selected=true]`、順位表:`.seg button`、新規開始:`summary`、成績の画面(②):`.link` ほか 2 件 |
| `var(--fg)` | 土台(変数・本文・ボタン):`body`、土台(変数・本文・ボタン):`input[type=text], input[type=number]`、成績の画面(②):`select`、成績の画面(②):`.game-card`、成績の画面(②):`.kv th` |
| `var(--ng)` | 土台(変数・本文・ボタン):`.error`、土台(変数・本文・ボタン):`.message.ng`、進行:`.mark.loss`、成績の画面(②):`.log .runs` |
| `var(--warn-line)` | 上の帯:保存・読み込みと、未保存の印:`.dirty`、成績の画面(②):`.warn`、成績の画面(②):`.answer-badge` |
| `var(--accent-fg)` | 土台(変数・本文・ボタン):`button, .button`、順位表:`.seg button[aria-pressed=true]` |
| `var(--ok)` | 土台(変数・本文・ボタン):`.message.ok`、進行:`.mark.win` |
| `#fff` | 土台(変数・本文・ボタン):`button.danger` |
| `var(--mine-line)` | 順位表:`.you` |
| `inherit` | 成績の画面(②):`th button.sort` |

### 背景(`background`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `var(--bg)` | 土台(変数・本文・ボタン):`body`、土台(変数・本文・ボタン):`input[type=text], input[type=number]`、上の帯:保存・読み込みと、未保存の印:`.topbar`、下のタブ:`.tabs`、順位表:`th, td` ほか 1 件 |
| `var(--box)` | 土台(変数・本文・ボタン):`.card`、成績の画面(②):`.info`、成績の画面(②):`.guide-item .formula`、成績の画面(②):`.game-card`、成績の画面(②):`.steps li.done` ほか 1 件 |
| `transparent` | 土台(変数・本文・ボタン):`button.secondary, .button.secondary`、下のタブ:`.tabs button`、順位表:`.seg button` |
| `var(--mine)` | 順位表:`tr.mine td`、成績の画面(②):`.game-card.mine`、成績の画面(②):`tr.selected td` |
| `var(--accent)` | 土台(変数・本文・ボタン):`button, .button`、順位表:`.seg button[aria-pressed=true]` |
| `none` | 成績の画面(②):`.link`、成績の画面(②):`th button.sort` |
| `var(--ng)` | 土台(変数・本文・ボタン):`button.danger` |
| `var(--warn-bg)` | 土台(変数・本文・ボタン):`.notice` |

### 線(`border`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `1px solid var(--line)` | 土台(変数・本文・ボタン):`input[type=text], input[type=number]`、土台(変数・本文・ボタン):`.card`、順位表:`.table-wrap`、新規開始:`fieldset`、成績の画面(②):`select` ほか 2 件 |
| `none` | 下のタブ:`.tabs button`、順位表:`.seg button`、成績の画面(②):`.link`、成績の画面(②):`th button.sort` |
| `1px solid var(--accent)` | 土台(変数・本文・ボタン):`button, .button`、順位表:`.seg` |
| `1px solid var(--warn-line)` | 土台(変数・本文・ボタン):`.notice` |

### 下の線(`border-bottom`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `1px solid var(--line)` | 上の帯:保存・読み込みと、未保存の印:`.topbar`、進行:`ul.games li`、順位表:`th, td`、新規開始:`.team`、成績の画面(②):`.guide-item` ほか 1 件 |
| `none` | 進行:`ul.games li:last-child`、順位表:`tr:last-child td`、新規開始:`.team:last-child` |

### 左の線(`border-left`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `4px solid var(--accent)` | 成績の画面(②):`.info` |

### 上の線(`border-top`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `1px solid var(--line)` | 下のタブ:`.tabs` |
| `2px solid var(--line)` | 成績の画面(②):`tr.career td, tr.career th` |

### 影(線の代わり)(`box-shadow`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `inset 4px 0 0 var(--mine-line)` | 順位表:`tr.mine td.sticky`、成績の画面(②):`.wide .table-wrap.fluid tr.mine td.sticky` |
| `inset 0 3px 0 var(--accent)` | 下のタブ:`.tabs button[aria-selected=true]` |
| `2px 0 0 var(--line)` | 成績の画面(②):`th.sticky2, td.sticky2` |
| `none` | 成績の画面(②):`.wide .table-wrap.fluid th.sticky, .wide .table-wrap.fluid td.sticky, .wide .table-wrap.fluid th.sticky2, .wide .table-wrap.fluid td.sticky2` |

### 文字の大きさ(`font-size`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `0.9rem` | 土台(変数・本文・ボタン):`.muted`、土台(変数・本文・ボタン):`.error`、上の帯:保存・読み込みと、未保存の印:`.topbar-title`、上の帯:保存・読み込みと、未保存の印:`.topbar button, .topbar .button`、順位表:`dl.terms` ほか 4 件 |
| `0.95rem` | 新規開始:`.team .pick`、成績の画面(②):`select`、成績の画面(②):`.check`、成績の画面(②):`.kv th`、成績の画面(②):`label.radio` |
| `0.85rem` | 土台(変数・本文・ボタン):`.small`、成績の画面(②):`.steps li`、成績の画面(②):`tr.detail td`、成績の画面(②):`.pick-btn` |
| `1rem` | 土台(変数・本文・ボタン):`button, .button`、土台(変数・本文・ボタン):`input[type=text], input[type=number]`、下のタブ:`.tabs button`、成績の画面(②):`.log h3` |
| `0.75rem` | 上の帯:保存・読み込みと、未保存の印:`.dirty`、成績の画面(②):`.sub`、成績の画面(②):`.answer-badge` |
| `1.05rem` | 進行:`.mine-line`、成績の画面(②):`.guide-item h3`、成績の画面(②):`.game-card .score` |
| `0.8rem` | 順位表:`th`、成績の画面(②):`.game-card .tags`、成績の画面(②):`.desc` |
| `0.88rem` | 成績の画面(②):`.guide-item .formula`、成績の画面(②):`.guide-item ul`、成績の画面(②):`.log li.change` |
| `0.92rem` | 土台(変数・本文・ボタン):`.notice`、順位表:`table` |
| `16px` | 土台(変数・本文・ボタン):`body` |
| `1.35rem` | 土台(変数・本文・ボタン):`h1` |
| `1.1rem` | 土台(変数・本文・ボタン):`h2` |
| `1.6rem` | 進行:`.day` |
| `1.2rem` | 成績の画面(②):`.page-head h1` |
| `0.93rem` | 成績の画面(②):`.log li` |

### 内側の余白(`padding`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `0` | 進行:`ul.games`、成績の画面(②):`.link`、成績の画面(②):`.steps`、成績の画面(②):`.log ul` |
| `8px 10px` | 土台(変数・本文・ボタン):`input[type=text], input[type=number]`、成績の画面(②):`.info` |
| `10px 12px` | 土台(変数・本文・ボタン):`.notice`、成績の画面(②):`.game-card` |
| `6px 10px` | 上の帯:保存・読み込みと、未保存の印:`.topbar button, .topbar .button`、成績の画面(②):`.seg.small-seg button` |
| `10px 0` | 新規開始:`.team`、成績の画面(②):`.guide-item` |
| `12px 16px 96px` | 土台(変数・本文・ボタン):`.screen` |
| `8px 16px` | 土台(変数・本文・ボタン):`button, .button` |
| `12px` | 土台(変数・本文・ボタン):`.card` |
| `8px 12px` | 上の帯:保存・読み込みと、未保存の印:`.topbar-inner` |
| `6px 0` | 進行:`ul.games li` |
| `8px 5px` | 順位表:`th, td` |
| `4px 12px 12px` | 新規開始:`fieldset` |
| `0 4px` | 新規開始:`legend` |
| `4px 0` | 成績の画面(②):`th button.sort` |
| `8px` | 成績の画面(②):`select` |
| `6px 8px` | 成績の画面(②):`.guide-item .formula` |
| `6px 12px` | 成績の画面(②):`.back` |
| `6px 4px` | 成績の画面(②):`.steps li` |
| `2px 10px` | 成績の画面(②):`.pick-btn` |
| `3px 0` | 成績の画面(②):`.log li` |
| `8px 0` | 成績の画面(②):`label.radio` |

### 外側の余白(`margin`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `8px 0` | 土台(変数・本文・ボタン):`h1`、土台(変数・本文・ボタン):`p`、土台(変数・本文・ボタン):`.stack > *`、土台(変数・本文・ボタン):`.message`、順位表:`.seg` ほか 5 件 |
| `0` | 土台(変数・本文・ボタン):`body`、進行:`ul.games`、順位表:`dl.terms dd`、成績の画面(②):`.seg.small-seg`、成績の画面(②):`.page-head h1` ほか 1 件 |
| `12px 0` | 土台(変数・本文・ボタン):`.card`、土台(変数・本文・ボタン):`.notice`、進行:`.advance`、順位表:`dl.terms`、新規開始:`fieldset` ほか 1 件 |
| `4px 0` | 進行:`.day`、成績の画面(②):`.guide-item .formula`、成績の画面(②):`.guide-item ul` |
| `4px 0 8px` | 進行:`.stop`、成績の画面(②):`.page-head`、成績の画面(②):`.run-box` |
| `0 auto` | 土台(変数・本文・ボタン):`.screen`、上の帯:保存・読み込みと、未保存の印:`.topbar-inner` |
| `20px 0 8px` | 土台(変数・本文・ボタン):`h2` |
| `4px 0 0` | 土台(変数・本文・ボタン):`.error` |
| `16px 0` | 新規開始:`.boot` |
| `0 0 4px` | 成績の画面(②):`.guide-item h3` |
| `6px 0` | 成績の画面(②):`.game-card` |
| `16px 0 4px` | 成績の画面(②):`.log h3` |

### 間隔(`gap`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `8px` | 土台(変数・本文・ボタン):`.row`、進行:`.advance`、進行:`ul.games li`、成績の画面(②):`.filters`、成績の画面(②):`.page-head` ほか 2 件 |
| `6px` | 上の帯:保存・読み込みと、未保存の印:`.topbar-inner`、新規開始:`.team .pick`、成績の画面(②):`.check` |
| `4px` | 成績の画面(②):`.steps` |

### 列の間隔(`column-gap`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `8px` | 成績の画面(②):`.game-card` |

### 角丸(`border-radius`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `10px` | 土台(変数・本文・ボタン):`button, .button`、順位表:`.seg`、順位表:`.table-wrap` |
| `8px` | 土台(変数・本文・ボタン):`input[type=text], input[type=number]`、成績の画面(②):`select`、成績の画面(②):`.steps li` |
| `12px` | 土台(変数・本文・ボタン):`.card`、土台(変数・本文・ボタン):`.notice`、新規開始:`fieldset` |
| `0` | 下のタブ:`.tabs button`、順位表:`.seg button` |
| `6px` | 成績の画面(②):`.info`、成績の画面(②):`.guide-item .formula` |

### 最小の高さ(`min-height`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `40px` | 上の帯:保存・読み込みと、未保存の印:`.topbar button, .topbar .button`、成績の画面(②):`.seg.small-seg button`、成績の画面(②):`select`、成績の画面(②):`.check`、成績の画面(②):`.back` |
| `44px` | 土台(変数・本文・ボタン):`button, .button`、新規開始:`summary` |
| `32px` | 成績の画面(②):`th button.sort`、成績の画面(②):`.pick-btn` |
| `56px` | 下のタブ:`.tabs button` |
| `0` | 成績の画面(②):`.link` |

### 行の高さ(`line-height`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `1.3` | 上の帯:保存・読み込みと、未保存の印:`.topbar-title`、成績の画面(②):`td.name-cell` |
| `1.6` | 土台(変数・本文・ボタン):`body` |
| `1.2` | 順位表:`th` |

### 幅(`width`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `100%` | 土台(変数・本文・ボタン):`.stack > *`、土台(変数・本文・ボタン):`input[type=text], input[type=number]`、土台(変数・本文・ボタン):`progress`、進行:`.stop`、順位表:`table` ほか 3 件 |
| `20px` | 新規開始:`.team .pick input`、成績の画面(②):`.check input`、成績の画面(②):`label.radio input` |
| `1px` | 土台(変数・本文・ボタン):`input[type=file]` |
| `9em` | 成績の画面(②):`/* 並び順の指標が表にないとき、名前の隣に固定の列を出す(D-131)。名前の列の幅を決めて、その右に固定する */ th.name-fixed, td.name-fixed` |
| `auto` | 成績の画面(②):`.wide .table-wrap.fluid th.name-fixed, .wide .table-wrap.fluid td.name-fixed` |

### 最大の幅(`max-width`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `640px` | 土台(変数・本文・ボタン):`.screen`、上の帯:保存・読み込みと、未保存の印:`.topbar-inner` |
| `none` | 成績の画面(②):`.wide .table-wrap.fluid th.name-fixed, .wide .table-wrap.fluid td.name-fixed`、成績の画面(②):`.wide .table-wrap.fluid td.name-cell` |
| `9.5em` | 成績の画面(②):`.sub` |
| `10em` | 成績の画面(②):`td.name-cell` |
| `9em` | 成績の画面(②):`/* 並び順の指標が表にないとき、名前の隣に固定の列を出す(D-131)。名前の列の幅を決めて、その右に固定する */ th.name-fixed, td.name-fixed` |
| `1180px` | 成績の画面(②):`/* 広い画面(幅が app.js の WIDE_MIN_WIDTH 以上。<html class="wide">)では、fluid の印の表を横スクロールなしで全列出す(D-223)。既存の表は変えない */ .wide #screen-procedure, .wide #screen-review` |
| `100%` | 成績の画面(②):`select` |

### 最小の幅(`min-width`)

| 値 | 使っている場所(画面:セレクタ) |
|---|---|
| `9em` | 成績の画面(②):`/* 並び順の指標が表にないとき、名前の隣に固定の列を出す(D-131)。名前の列の幅を決めて、その右に固定する */ th.name-fixed, td.name-fixed`、成績の画面(②):`.wide .table-wrap.fluid th.name-fixed, .wide .table-wrap.fluid td.name-fixed` |
| `0` | 上の帯:保存・読み込みと、未保存の印:`.topbar-title` |
| `1.5em` | 進行:`.mark` |
| `1.6em` | 順位表:`.rank` |
| `7.5em` | 成績の画面(②):`td.name-cell` |
