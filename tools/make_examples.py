#!/usr/bin/env python3
r"""重建 examples/：可发布的最小样例。

样例不是手写的，是**用本仓库自己的库现算出来的**——所以样例永远和当前代码一致。
改完库跑一遍这个脚本，diff 会告诉你输出变了没有。

    python tools/make_examples.py            # 写 examples/
    python tools/make_examples.py --check    # 只算不写，对比仓库里的样例是否过期

产物（都在 examples/ 下）：

    furnace24.blueprint.txt            24 台石炉，铁矿石 → 铁板（玩家版）
    furnace24.png                      同一张图的预览
    smelt-steel24.blueprint.txt        24 台电炉，铁板 → 钢板（玩家版）
    smelt-steel24.png                  预览
    furnace24.testbench.blueprint.txt  自测版：自带料、自带电，只给测试用

⚠ 自测版用的是隐藏原型（infinity-chest / electric-energy-interface），
正常游戏里造不出来，**不要发给玩家**。
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bpcompile import lint, render  # noqa: E402
from bpcompile import blueprint as bpm  # noqa: E402
from bpcompile import testbench  # noqa: E402
from bpcompile.templates import furnace_array  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")


def furnace_blueprint(furnace, count, input_item, output_item, label):
    blueprint = bpm.Blueprint()
    info = furnace_array(
        blueprint,
        count=count,
        furnace=furnace,
        input_item=input_item,
        output_item=output_item,
        label=label,
    )
    blueprint.normalize()
    return blueprint, info


def build_all():
    """返回 [(文件名, 字节内容)]。顺序固定，内容由代码决定，可重复。"""
    artifacts = []

    stone, stone_info = furnace_blueprint(
        "stone-furnace", 24, "iron-ore", "iron-plate", "24 台石炉：铁矿石 → 铁板"
    )
    artifacts.append(("furnace24.blueprint.txt", stone.to_string().encode("utf-8")))
    artifacts.append(("furnace24.png", render_png(stone, "24 台石炉阵列（示例）")))

    steel, steel_info = furnace_blueprint(
        "electric-furnace", 24, "iron-plate", "steel-plate", "24 台电炉：铁板 → 钢板"
    )
    artifacts.append(("smelt-steel24.blueprint.txt", steel.to_string().encode("utf-8")))
    artifacts.append(("smelt-steel24.png", render_png(steel, "24 台电炉阵列（示例）")))

    clone, manifest = testbench.build_testbench(stone, seconds=60, product="iron-plate")
    clone.normalize()
    artifacts.append(
        ("furnace24.testbench.blueprint.txt", clone.to_string().encode("utf-8"))
    )

    report = {
        "furnace24": stone_info,
        "smelt-steel24": steel_info,
        "testbench": {
            "product": manifest["product"],
            "seconds": manifest["seconds"],
            "fixtures": manifest["fixtures"],
        },
        "lint": {
            "furnace24": lint.report(stone)["counts"],
            "smelt-steel24": lint.report(steel)["counts"],
            "furnace24.testbench": lint.report(clone)["counts"],
        },
    }
    return artifacts, report


def render_png(blueprint, title):
    path = os.path.join(EXAMPLES, "_tmp.png")
    os.makedirs(EXAMPLES, exist_ok=True)
    render.render(blueprint, path, cell_px=16, title=title)
    with open(path, "rb") as handle:
        data = handle.read()
    os.unlink(path)
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description="重建 examples/")
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = parser.parse_args(argv)

    artifacts, report = build_all()
    print("  实体数/物品：%s" % ", ".join(
        "%s=%d 个实体" % (name, info["entities"]) for name, info in report.items()
        if isinstance(info, dict) and "entities" in info))
    print("  lint：%s" % report["lint"])

    changed = []
    for name, data in artifacts:
        path = os.path.join(EXAMPLES, name)
        old = None
        if os.path.exists(path):
            with open(path, "rb") as handle:
                old = handle.read()
        if old == data:
            continue
        changed.append(name)
        if not args.check:
            with open(path, "wb") as handle:
                handle.write(data)

    if args.check:
        if changed:
            print("样例过期（%s）：跑 python tools/make_examples.py 重建" % ", ".join(changed))
            return 1
        print("样例是最新的（%d 个文件）" % len(artifacts))
        return 0
    print("写好了 %d 个样例，其中 %d 个有变化：%s"
          % (len(artifacts), len(changed), ", ".join(changed) or "无"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
