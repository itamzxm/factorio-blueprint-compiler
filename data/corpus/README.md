# 蓝图语料：27 张真实玩家的原版蓝图

> **用途：AI 学习与对标，不限。** 分析人类布局、当基准对照、喂进模型训练或微调，都可以。
>
> **署名归原作者。** 这些设计是 27 位（或更多）玩家做出来的，不是本项目的作品；
> 下面的表格是出处，再分发时请一并保留。

## 里面有什么

| 文件 | 说明 |
|---|---|
| `<id>.blueprint.txt` | 原始蓝图字符串——**就是玩家会粘贴的那一串**，原样保存，未改动一个字节 |
| `index.json` | 元信息：标题、作者、来源链接、抓取时间、实体数、包围盒、用到了哪些实体、以及这张被选中的理由（`why`） |
| `<id>.json` | **不随仓库发布**（2.8 MB，且一条命令就能再生）。要的话跑 `python tools/decode_corpus.py` |

## 出处与署名

全部来自 [factorio.school](https://www.factorio.school)：2026-11-22 前后抓取其最新 150 张，
按"原版 2.0/2.1、无第三方 mod、每类布局都有代表"筛出 27 张。
每张的 `id` / `title` / `author` / `source` 都记在 `index.json` 里，原样保留。

| 设计 | 作者 | 实体数 | id |
|---|---|---|---|
| [City Block Standard 100*100](https://www.factorio.school/view/-P6Y88Iitz3kCnw9doXE) | — | 40 | `-P6Y88Iitz3kCnw9doXE` |
| [Recycler（legendary）](https://www.factorio.school/view/-P6Hm_gReLzD3PRonzqh) | — | 43 | `-P6Hm_gReLzD3PRonzqh` |
| [Show number circuit - expandable](https://www.factorio.school/view/-P5w31JmiY-lbNNwtUxP) | — | 45 | `-P5w31JmiY-lbNNwtUxP` |
| [Memory Selector](https://www.factorio.school/view/-P7-UiQEn_VeNkJAomtj) | — | 69 | `-P7-UiQEn_VeNkJAomtj` |
| [plasticbars](https://www.factorio.school/view/-P625rATcBaaROdqUARf) | — | 70 | `-P625rATcBaaROdqUARf` |
| [T1 Red Science - Early Game](https://www.factorio.school/view/-P61CxHbCfdW2_enDEXK) | — | 87 | `-P61CxHbCfdW2_enDEXK` |
| [Electromagnetic plant（legendary）](https://www.factorio.school/view/-P6Hi36qoaWhXRFg13no) | — | 90 | `-P6Hi36qoaWhXRFg13no` |
| [tileable red circuts](https://www.factorio.school/view/-P6Cf_tbKOQ22EqhuSeu) | — | 108 | `-P6Cf_tbKOQ22EqhuSeu` |
| [Urianium processing](https://www.factorio.school/view/-P6t93zv5wF0heiiAPOK) | — | 119 | `-P6t93zv5wF0heiiAPOK` |
| [Automatic Mall](https://www.factorio.school/view/-P6tK0MII4xY7XU-sk9w) | — | 146 | `-P6tK0MII4xY7XU-sk9w` |
| [steam energy](https://www.factorio.school/view/-P6fvfCoM2DpZCIxXrh4) | — | 160 | `-P6fvfCoM2DpZCIxXrh4` |
| [Quality module 3（legendary）](https://www.factorio.school/view/-P6HdClFqf8O-Qq9eN6u) | — | 165 | `-P6HdClFqf8O-Qq9eN6u` |
| [eary smelting](https://www.factorio.school/view/-P6fxvwb5tPSLLD3e1Kf) | — | 177 | `-P6fxvwb5tPSLLD3e1Kf` |
| [efficient coal liquefaction / oil refining](https://www.factorio.school/view/-P6hMjeHxu8MSUmK7c2q) | — | 210 | `-P6hMjeHxu8MSUmK7c2q` |
| [Compact Diesel（Vulcanus）](https://www.factorio.school/view/-P668Z1cLQKIUaYFj01S) | — | 221 | `-P668Z1cLQKIUaYFj01S` |
| [mid smelting](https://www.factorio.school/view/-P6g-axgCmZtm3fkblb-) | — | 262 | `-P6g-axgCmZtm3fkblb-` |
| [Multiplexed Memory Banks](https://www.factorio.school/view/-P6q7rRil3QbFCDkP2-N) | — | 306 | `-P6q7rRil3QbFCDkP2-N` |
| [late smelting](https://www.factorio.school/view/-P6g1HfhVvFYPfMw3Vfo) | — | 337 | `-P6g1HfhVvFYPfMw3Vfo` |
| [Newt (Inner Planet) - v1](https://www.factorio.school/view/-P6qemJVnePqw2s2jegc) | — | 385 | `-P6qemJVnePqw2s2jegc` |
| [파란물약초당1.2개생산](https://www.factorio.school/view/-P5pFffrPL_taA6a-Olg) | neil | 515 | `-P5pFffrPL_taA6a-Olg` |
| [Voidbreaker Ship](https://www.factorio.school/view/-P5tPpSBcgLd0LAToQnV) | — | 519 | `-P5tPpSBcgLd0LAToQnV` |
| [Jump Start](https://www.factorio.school/view/-P62Hy45IGDpzpNJHdJw) | — | 688 | `-P62Hy45IGDpzpNJHdJw` |
| [1.12 GW Nuclear](https://www.factorio.school/view/-P5rgqU2D9jMhefEg413) | — | 1100 | `-P5rgqU2D9jMhefEg413` |
| [Rocket Rush Starter Base Layout（스피드런）](https://www.factorio.school/view/-P5_wlYMsi2D_Cj8rZ0a) | 배가고파 | 1116 | `-P5_wlYMsi2D_Cj8rZ0a` |
| [4-lane 4-way LHD Elevated Intersection](https://www.factorio.school/view/-P6wH4ZGXY9yKaYddtLr) | — | 1710 | `-P6wH4ZGXY9yKaYddtLr` |
| [Max Eletric Furnace](https://www.factorio.school/view/-P5URapBl8sTwe8zdyLe) | Vinicius | 2095 | `-P5URapBl8sTwe8zdyLe` |
| [Train Network](https://www.factorio.school/view/-P5NR-xIfQars69PtKup) | Sam Rood | 2559 | `-P5NR-xIfQars69PtKup` |

作者一栏写"—"的，是来源站上本来就没标作者（不是我们省略了）。
**如果你要再分发这些设计，请保留上面的作者与链接。**

## 怎么解码

蓝图串是 `<1 字节版本字符> + base64(zlib(UTF-8 JSON))`。三行就能看：

    import base64, json, zlib
    text = open("data/corpus/-P6fxvwb5tPSLLD3e1Kf.blueprint.txt", encoding="utf-8").read().strip()
    print(json.dumps(json.loads(zlib.decompress(base64.b64decode(text[1:]))), ensure_ascii=False, indent=1)[:400])

或者用现成的：

    python tools/decode_corpus.py            # 27 张全解成 .json（自检 + 给测试用）
    python tools/decode_corpus.py --list     # 只看清单和元信息
    python tools/decode_corpus.py --check    # 只解码校验，不写文件

## 拿它干什么（这是它存在的理由）

- **误报闸**：这 27 张都是**合法、真实的蓝图**。任何"重叠 / 越界 / 朝向非法"的判定在它们身上都该报 0 违规。
  误报会立刻暴露——这是 `tests/run_tests.py` 里最重要的一条测试。
- **解码往返**：解码后再编码，实体逐字段一致。
- **实体白名单**：解码出来的每个 `entity.name` 都该能在 `data/prototypes.json` 里查到。
- **尺寸表回归**：实体落点的奇偶性必须和尺寸表对得上（`doc/蓝图格式与坑.md` 第七节）。
- **对标**：自己的产物和人类布局放一起比，看差在哪。

## 别把它当"正确布局"的模板

真实玩家布局里有大量手工凑数和就地妥协。**它证明的是"游戏收得下"，不是"这样摆是对的"。**

## 已知缺口

- 27 张里只有 2 张有铁轨、2 张有核能。**轨道和核电的结论别从这批语料下。**
- 全部是 2.0 之后（`version` 解出来 2.0.15 ~ 2.1.25），不含 1.1 蓝图，不用做向后兼容。
- 没有只能靠 mod 才存在的实体（筛选时就丢掉了带 bob/angel 的那批）。
