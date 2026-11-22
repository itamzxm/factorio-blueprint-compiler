# examples/

这几个文件**不是手写的**：跑 `python tools/make_examples.py` 用库现算出来的，
所以它们和当前代码永远一致。`--check` 只比对不写，能告诉你仓库里的样例过期没有。

| 文件 | 是什么 | 怎么用 |
|---|---|---|
| `furnace24.blueprint.txt` | 24 台石炉，铁矿石 → 铁板，253 个实体 | 游戏里导入蓝图（Ctrl+V 粘字符串），或 `bp_load("examples/furnace24.blueprint.txt")` |
| `furnace24.png` | 同一张图的预览 | 直接看；青色/橙色标的是接线口 |
| `smelt-steel24.blueprint.txt` | 24 台电炉，铁板 → 钢板 | 同上，注意它要接电 |
| `smelt-steel24.png` | 预览 | — |
| `furnace24.testbench.blueprint.txt` | 自测版：输入口接 `infinity-chest`、输出口接钢箱、电网自带电源 | **只给测试用**，见下 |

## 自测版别发给玩家

`furnace24.testbench.blueprint.txt` 里的 `infinity-chest` / `electric-energy-interface` 是隐藏原型，
正常游戏里造不出来（导入会显示成问号实体）。它存在的意义只有一个：
让"这张图 60 秒能出多少板"变成一条可重复的命令。

    python tools/factorio_build_check.py examples/furnace24.testbench.blueprint.txt --run-seconds 60

读 `jobs[0].production`：`produced`（总量）、`drained`（箱子里**实际**收到了什么）。
细节见 `doc/自测版夹具.md` 与 `doc/真机校验.md`。

## 想改数量/炉种

样例只是 `furnace_array` 的默认参数跑出来的，改参数自己生成就行：

    from bpcompile import blueprint as bpm
    from bpcompile.templates import furnace_array

    bp = bpm.Blueprint()
    furnace_array(bp, count=48, furnace="steel-furnace",
                  input_item="iron-ore", output_item="iron-plate")
    bp.normalize()
    open("my48.blueprint.txt", "w", encoding="utf-8").write(bp.to_string())
