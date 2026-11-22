# 选型：为什么产品路径不用 factorio-draftsman 当编码层

> 结论：编码层自己写（zlib+base64，8 行），draftsman 只留在测试里当**独立解码器**互证。
> 下面是现场实测，不是口味问题。

## 1. 它读不了 2.1 的蓝图

拿 `data/corpus/` 里那 27 张真实 2.1 蓝图喂
`draftsman.blueprintable.get_blueprintable_from_string`：

| 结果 | 张数 | 例子 |
|---|---|---|
| 正确解出实体 | **21** | — |
| 解出 0 个实体（不报错） | 1 | `-P5NR-xIfQars69PtKup` |
| 抛 `DataFormatError` | 1 | `-P5tPpSBcgLd0LAToQnV` |
| 抛 `IndexError` | 4 | `-P6fxvwb5tPSLLD3e1Kf`（**典型改造任务的输入**）、`-P6g1HfhVvFYPfMw3Vfo`、`-P6Y88Iitz3kCnw9doXE`、其余 |

**22% 的真实 2.1 蓝图它读不对，其中一张正是改造类任务最需要的那种输入。** 这不是"够不够用"的问题，
是产品路径上会静默丢数据。

## 2. 它会在解码时改数据

`label` 超过 200 字节会被**静默截断**（每次都抛 `DraftsmanWarning`，但结果已经变了）。
改造类需求（原样读进来、只改该改的地方）要"原样读进来、只改该改的地方"，静默改写会直接违背需求。

## 3. 它的 API 会给你假绿灯

`Blueprint("0eNq...")` 不会解码——它把整串塞进了 `label`，`entities` 是空的。
**第一版测试就被它骗过一次**：断言写的是 `len(bp2.entities) == n`，两边都是 0，于是"27/27 通过"。
正确用法是 `get_blueprintable_from_string`。测试里那条 `test_cross_decoder_with_draftsman` 现在就是这么写的，
注释里也写了这个坑。

## 4. 它自带一套原型数据，会和我们的真值表打架

`data/prototypes.json` 是从 2.1.24 的 `data-raw-dump.json` 生成、并拿 27 张样本做了 76/76 奇偶校验的。
draftsman 自带一份快照。两套真值源同时存在，迟早出现"我们表里对、它表里错"的分歧，
而分歧点通常正好是最难查的那类（尺寸、朝向）。

## 5. 但它的输出是对的 —— 所以要留着当互证

我们编码器写出来的串，draftsman 能正确读回（`test_cross_decoder_with_draftsman` 断言实体数一致）。
这说明**我们的编码是标准形状**，不是自说自话。这是 draftsman 现在唯一的用途。

## 我们自己的编码层

`bpcompile/blueprint.py` 里 `encode` / `decode` 共 8 行。三条它必须守住的行为，都有测试钉着：

- 顶层必须是 `{"blueprint": {...}}`。少这层包装，真机工具直接报"认不出这是蓝图"（**踩过**）。
- 解→写→再解，实体逐字段一致（`test_codec_roundtrip`，27 张样本）。
- `version` 用 `562954249961473`（= 2.1.24.1，按 `(major<<48)|(minor<<32)|(patch<<16)|dev` 打包）。

## 翻案信号

draftsman 能在 27/27 上正确解出实体、且不再改写 `label`——那时可以重新考虑把编码层换回去。
换之前必须先让 `test_corpus_no_error_false_positives` 之类的样本闸继续绿。
