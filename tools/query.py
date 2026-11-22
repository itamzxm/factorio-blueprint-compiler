#!/usr/bin/env python3
r"""查 data/prototypes.json 的小命令行。给人和给 AI 都能用。

    python tools/query.py entity <名字>            实体一行摘要 + 完整 JSON
    python tools/query.py find-entity <子串>       按名字或 type 找实体
    python tools/query.py recipe <名字>            配方耗时/进出料
    python tools/query.py who-makes <物品>         什么机器能做它、用什么配方
    python tools/query.py port <名字> [方向]       各个朝向的进出位置
    python tools/query.py table <type>             某 type 下所有实体及尺寸

方向出参用 Factorio 的 0/4/8/12（北/东/南/西）；端口坐标是朝北坐标系，
按「每档顺时针 90°」换算，与游戏一致（见 doc/蓝图格式与坑.md）。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "prototypes.json")

_cache = {}


def load():
    if "db" not in _cache:
        with open(DATA, encoding="utf-8") as f:
            _cache["db"] = json.load(f)
    return _cache["db"]


DIR_NAME = {0: "北", 4: "东", 8: "南", 12: "西"}


def rotate(x, y, direction):
    """把朝北坐标按 direction 旋过去。方向值来自游戏实测：0=北 4=东 8=南 12=西。"""
    d = direction % 16
    if d == 0:
        return x, y
    if d == 4:
        return -y, x
    if d == 8:
        return -x, -y
    if d == 12:
        return y, -x
    raise ValueError("不支持的朝向 %r（本工具只处理 0/4/8/12）" % direction)


def fmt_entity(n, e):
    head = "%-28s type=%-22s %sx%s" % (n, e["type"], e["w"], e["h"])
    bits = []
    if e.get("dirs") is not None:
        bits.append("游戏认=%s档" % e["dirs"])
    if e.get("observed_dirs"):
        bits.append("蓝图写过=%s" % e["observed_dirs"])
    if e.get("direction_accept"):
        bits.append("校验放行=%s" % e["direction_accept"])
    if e.get("crafting"):
        c = e["crafting"]
        bits.append("速度=%s 类别=%s" % (c.get("speed"), ",".join(c.get("categories") or [])))
    if e.get("module_slots"):
        bits.append("模块槽=%s" % e["module_slots"])
    if e.get("inserter"):
        i = e["inserter"]
        bits.append("取=%s 放=%s" % (i.get("pickup"), i.get("insert")))
    if e.get("fluids"):
        bits.append("液口=%d 组" % len(e["fluids"]))
    print(head)
    if bits:
        print("  " + " | ".join(bits))


def cmd_entity(args):
    db = load()
    n = args[0]
    e = db["entities"].get(n)
    if not e:
        near = [k for k in db["entities"] if args[0].lower() in k.lower()][:10]
        print("没有这个实体:", n, "| 相近:", near)
        return 1
    fmt_entity(n, e)
    print(json.dumps(e, ensure_ascii=False, indent=1))
    return 0


def cmd_find(args):
    db = load()
    q = args[0].lower()
    hits = [(n, e) for n, e in db["entities"].items()
            if q in n.lower() or q in e["type"].lower()]
    hits.sort()
    for n, e in hits:
        fmt_entity(n, e)
    print("--", len(hits), "个")
    return 0


def cmd_recipe(args):
    db = load()
    n = args[0]
    r = db["recipes"].get(n)
    if not r:
        near = [k for k in db["recipes"] if args[0].lower() in k.lower()][:10]
        print("没有这个配方:", n, "| 相近:", near)
        return 1
    t = r["time"]
    print("%-30s %ss%s  类别=%s" % (n, t, "（缺省值，原型没写 energy_required）" if r.get("time_default") else "",
                                  ",".join(r["categories"])))
    print("  进料:", r["in"])
    print("  出料:", r["out"])
    for c in r["categories"]:
        ms = db["machines_by_category"].get(c) or []
        print("  能做它的机器(%s): %s" % (c, ", ".join(ms)))
    if r.get("allow_productivity"):
        print("  允许产能模块")
    return 0


def cmd_who_makes(args):
    db = load()
    item = args[0]
    rs = db["recipes_by_product"].get(item)
    if not rs:
        print("没有配方产出:", item)
        return 1
    for rn in rs:
        r = db["recipes"][rn]
        print("%-28s %ss  进料=%s  产出=%s" % (rn, r["time"], r["in"], r["out"]))
        for c in r["categories"]:
            print("    %-22s -> %s" % (c, ", ".join(db["machines_by_category"].get(c) or [])))
    return 0


def cmd_port(args):
    db = load()
    n = args[0]
    e = db["entities"].get(n)
    if not e:
        print("没有这个实体:", n)
        return 1
    dirs = [args[1]] if len(args) > 1 else ["0", "4", "8", "12"]
    for d in dirs:
        d = int(d)
        print("== %s 朝向 %s(%s)" % (n, d, DIR_NAME.get(d, "?")))
        if e.get("inserter"):
            px, py = rotate(*e["inserter"]["pickup"], d)
            ix, iy = rotate(*e["inserter"]["insert"], d)
            print("   取 (%.2f, %.2f)  放 (%.2f, %.2f)" % (px, py, ix, iy))
        for box in e.get("fluids") or []:
            for c in box["c"]:
                x, y = rotate(*c["p"], d)
                print("   %-13s 管口 (%.2f, %.2f) 面朝 %s%s" % (
                    box["io"], x, y, DIR_NAME.get((c["d"] + d) % 16, "?"),
                    " [地下]" if c.get("underground") else ""))
        if e.get("drill"):
            x, y = rotate(*e["drill"]["output"], d)
            print("   矿物输出 (%.2f, %.2f)  搜索半径 %s" % (x, y, e["drill"]["radius"]))
    return 0


def cmd_table(args):
    db = load()
    t = args[0]
    rows = [(n, e) for n, e in db["entities"].items() if t.lower() in e["type"].lower()]
    for n, e in sorted(rows):
        print("%-32s %-22s %sx%s dirs=%s" % (n, e["type"], e["w"], e["h"], e.get("dirs")))
    print("--", len(rows), "个")
    return 0


CMDS = {
    "entity": cmd_entity,
    "find-entity": cmd_find,
    "recipe": cmd_recipe,
    "who-makes": cmd_who_makes,
    "port": cmd_port,
    "table": cmd_table,
}


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in CMDS:
        print(__doc__)
        return 2
    return CMDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
