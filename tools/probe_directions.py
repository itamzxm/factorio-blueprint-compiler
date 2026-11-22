r"""方向档数实测：每种实体原型支持几档朝向，只能靠真机问。

用法：python tools/probe_directions.py --out data/direction-probe.json

三条线同时测，判定按可信度取：
  ① blueprint_read（判据）：direction 写进单块蓝图 → 建出来 → 读回。游戏会规整掉不支持的朝向，
     这正是编译器产出蓝图后游戏会做的事，所以它是"游戏到底认不认"的硬证据。
  ② create_read（对照）：create_entity 时给 direction 再读回 —— 有些类型会无视这个参数。
  ③ assign_read（对照）：建好后写 entity.direction 再读回。
三条线不一致的原型全部列出来，别让读数被误读。
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

import probe_rig

SCENARIO = os.path.join(probe_rig.TOOL_DIR, "probe_directions_scenario.lua")

RAIL_TYPES = {"rail", "rail-signal", "rail-chain-signal", "train-stop", "locomotive", "cargo-wagon",
              "fluid-wagon", "artillery-wagon", "infinity-cargo-wagon", "rolling-stock", "rail-ramp",
              "rail-support", "elevated-rail", "curved-rail-a", "curved-rail-b", "half-diagonal-rail",
              "straight-rail", "legacy-straight-rail", "legacy-curved-rail"}

LINES = ("blueprint_line", "create_line", "assign_line")


def distinct(values):
    return sorted({int(value) for value in (values or [])})


def classify_gating(bare, best):
    """配方门控分类：判据是"裸建能不能读到 >1 档"，不是"有没有 crafting_categories"。"""
    if not best:
        return "unmeasurable"
    if len(best) > 1:
        return "bare_ok" if len(bare) > 1 else "requires_recipe"
    if not bare:
        return "bare_build_failed"
    return "never_rotates"


def build_row(raw):
    lines = {key: raw.get(key) or {} for key in LINES}
    readings = {key: distinct(line.get("seen")) for key, line in lines.items()}
    row = {
        "name": raw["name"],
        "type": raw.get("type"),
        "placeable": raw.get("placeable"),
        "placeable_items": raw.get("placeable_items") or [],
        "blueprint_seen": readings["blueprint_line"],
        "create_seen": readings["create_line"],
        "assign_seen": readings["assign_line"],
        "blueprint_counts": lines["blueprint_line"].get("counts") or {},
        "probe_recipes": raw.get("probe_recipes") or [],
        "probe_recipe": raw.get("probe_recipe"),
        "bare_seen": distinct((raw.get("bare_line") or {}).get("seen")),
    }
    if not row["placeable"]:
        row.update({"dirs": None, "dirs_probe": "not_placeable",
                    "note": "没有可用物品放置的原型（corpse/remnant/projectile 之类），不进方向表"})
        return row

    for key in LINES:
        if readings[key]:
            row["dirs"] = len(readings[key])
            row["dirs_probe"] = key.replace("_line", "_read")
            row["directions_seen"] = readings[key]
            break
    else:
        row["dirs"] = None
        row["dirs_probe"] = "unmeasurable"
        row["directions_seen"] = []

    row["direction_gating"] = classify_gating(row.get("bare_seen") or [], row.get("blueprint_seen") or [])
    notes = []
    if row["direction_gating"] == "requires_recipe":
        notes.append("配方门控：裸建只读回 %s，写了 recipe 才读到 %s"
                     % (",".join(str(v) for v in row["bare_seen"]) or "无",
                        ",".join(str(v) for v in row["blueprint_seen"])))
    if row["dirs"] == 1:
        notes.append("不可旋转：蓝图里写 direction=0..15，建出来读回恒为 %d（游戏自己把它规整掉了）"
                     % (row["directions_seen"] or [0])[0])
    if row["probe_recipe"]:
        notes.append("探针用的配方：%s（带配方的机器才认 direction，裸建的会被规整）" % row["probe_recipe"])
    elif row["dirs"]:
        notes.append("实测 %d 档：%s" % (row["dirs"], ",".join(str(value) for value in row["directions_seen"])))
    if readings["blueprint_line"] and readings["create_line"] and \
            len(readings["blueprint_line"]) != len(readings["create_line"]):
        notes.append("注意：建时给 direction 只读回 %s，走蓝图才读回 %s —— create_entity 对这类原型会无视 direction 参数"
                     % (",".join(str(v) for v in readings["create_line"]),
                        ",".join(str(v) for v in readings["blueprint_line"])))
    if not readings["blueprint_line"]:
        errors = lines["blueprint_line"].get("errors") or []
        detail = (errors[0].get("error") or "") if errors else ""
        if detail == "built_nothing":
            notes.append("蓝图上建不出来（这实体在平地上建不了，如 offshore-pump/thruster），方向取自对照线")
        elif detail:
            notes.append("蓝图线失败：%s" % str(detail).splitlines()[0])
        else:
            notes.append("蓝图线没取到读数，方向取自对照线")
    if raw.get("type") in RAIL_TYPES:
        notes.append("轨道类：direction 编码的是坡度/连接形态，不是简单朝向")
    row["note"] = "；".join(notes)
    return row


def _count_by(rows, key):
    counts = {}
    for row in rows:
        counts[row.get(key)] = counts.get(row.get(key), 0) + 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="实测每种实体原型支持几个朝向")
    parser.add_argument("--out", default=os.path.join(probe_rig.TOOL_DIR, "..", "data",
                                             "direction-probe.json"))
    parser.add_argument("--keep", metavar="DIR", help="保留临时目录便于取证")
    parser.add_argument("--factorio", default=probe_rig.FACTORIO_DEFAULT)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    with open(SCENARIO, encoding="utf-8") as handle:
        scenario = handle.read()
    if args.keep:
        os.makedirs(args.keep, exist_ok=True)
    work_dir = tempfile.mkdtemp(prefix="bp-dir-", dir=args.keep) if args.keep else tempfile.mkdtemp(prefix="bp-dir-")
    try:
        outcome = probe_rig.run_once(scenario, work_dir, factorio=args.factorio,
                                  test_ticks=600, timeout=args.timeout, keep=bool(args.keep))
        if outcome["raw"] is None:
            print("没有结果文件；exit=%s\n%s" % (outcome["exit_code"], outcome["stdout_tail"][-2000:]))
            return 1
        rows = [build_row(raw) for raw in outcome["raw"]["rows"]]
        rows.sort(key=lambda item: (item["type"] or "", item["name"]))
        disagree = [row["name"] for row in rows
                    if row["blueprint_seen"] and row["create_seen"]
                    and len(row["blueprint_seen"]) != len(row["create_seen"])]
        summary = {
            "generated_by": "tools/probe_directions.py",
            "method": "判据是 blueprint_read：把 direction=0..15 依次写进单块蓝图，建出来 revive 后读回 entity.direction，"
                      "去重个数即档数。create_read / assign_read 两列是对照，不一致的列在 line_disagree。",
            "notes": [
                "dirs=1 = 不可旋转：蓝图里怎么写 direction，游戏建出来都读回同一个数。",
                "注意：真实蓝图里常见给 assembling-machine / furnace / foundry 写 direction，游戏仍按不可旋转处理；"
                "写这个字段不会报错，只是不生效。判定要给几何时按 dirs 为准。",
                "轨道类：direction 不是朝向而是坡度/连接形态。rail-signal 16 档；straight-rail 落到偶数格、只按 4 个主方向；"
                "curved-rail-a 8 档；rail-support 8 档。",
                "全表含不可放置原型（corpse/remnant/projectile 等），placeable=false、dirs=null，只为补齐原型全集。",
            ],
            "game_env": outcome["raw"].get("env"),
            "elapsed_s": outcome["elapsed_s"],
            "line_disagree": disagree,
            "recipe_gated": [row["name"] for row in rows if row.get("direction_gating") == "requires_recipe"],
            "gating_counts": _count_by(rows, "direction_gating"),
            "counts": {
                "total": len(rows),
                "placeable": sum(1 for row in rows if row["placeable"]),
                "unmeasurable": sum(1 for row in rows if row["dirs_probe"] == "unmeasurable"),
                "line_disagree": len(disagree),
            },
            "rows": rows,
        }
        out_path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=1)
        print("写出 %s：%d 行（可放置 %d，测不出 %d，三线不一致 %d），耗时 %.1fs" % (
            out_path, summary["counts"]["total"], summary["counts"]["placeable"],
            summary["counts"]["unmeasurable"], summary["counts"]["line_disagree"], outcome["elapsed_s"]))
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
