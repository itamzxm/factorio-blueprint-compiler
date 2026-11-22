#!/usr/bin/env python3
r"""从 Factorio 的 data-raw-dump.json 抽出蓝图编译器要用的实体/配方真值表。

输入  %APPDATA%\Factorio\script-output\data-raw-dump.json（游戏内 /c 导出的全量原型）
输出  data/prototypes.json

本脚本只做「抽取 + 规整」，不做判断性推断以外的事。凡是推断出来的字段都带 *_source，
让后来人能看出哪一格是数据里写死的、哪一格是规则算出来的。
"""
import json
import math
import os
import sys
import datetime

def _default_dump():
    """游戏内 /c 导出的原型转储。环境变量 FACTORIO_DUMP 优先，否则按各平台默认位置找。"""
    override = os.environ.get("FACTORIO_DUMP")
    if override:
        return override
    candidates = [
        os.path.expandvars(r"%APPDATA%\Factorio\script-output\data-raw-dump.json"),
        os.path.expanduser("~/.factorio/script-output/data-raw-dump.json"),
        os.path.expanduser(
            "~/Library/Application Support/factorio/script-output/data-raw-dump.json"
        ),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return candidates[0]



DUMP = _default_dump()
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "prototypes.json")

EPS = 1e-6

# ---------------------------------------------------------------- 尺寸

# 尺寸默认从 collision_box 向上取整到整格。
# 例外有两条，依据都是「真蓝图里这个实体落在整数坐标还是半整数坐标」——
# 偶数格实体只能落在整数坐标，奇数格只能落在 .5，这条判据在 76 个实体名上 75 个吻合，
# 剩下那一个就是下面的 offshore-pump。详见 doc/蓝图格式与坑.md。
SIZE_OVERRIDES = {
    "train-stop": {
        "w": 2, "h": 2,
        "why": "collision_box 1x1 只是中间那根柱子；真蓝图里 train-stop 全部落在整数坐标(2x2)",
    },
    "offshore-pump": {
        "w": 1, "h": 1,
        "why": "collision_box 1.2x1.35 取整会得 2x2，但 27 张真语料里的 9 个 offshore-pump "
               "全部落在半整数坐标，说明是奇数格；draftsman 4.0.1 亦为 1x1",
    },
}


def box_extent(box):
    if not box or not isinstance(box, list) or len(box) != 2:
        return None
    (x1, y1), (x2, y2) = box
    return (abs(x2 - x1), abs(y2 - y1))


def tiles(n):
    if n is None:
        return 0
    return max(1, int(math.ceil(n - EPS)))


# ---------------------------------------------------------------- 旋转

# Factorio 原型数据里没有「能不能旋转」这个字段（EntityPrototype 属性表查过，没有；
# 全量 dump 里也没有）。旋转是 C++ 按 type 决定的，所以这里只能按 type 立表。
# 其中能被 dump 自己证实的两种，优先用 dump 的证据：
#   two_direction_only    → 该原型只认 2 个朝向（蒸汽机、储罐、聚变反应堆等）
#   turret_base_has_direction → 炮塔底座朝向有意义（火焰炮塔、电磁炮塔）
# 其余按 type 归类，标记 dirs_source，未实测的部分一律 dirs_verified=false。
ROTATABLE_TYPES = {
    "transport-belt": 4,
    "underground-belt": 4,
    "splitter": 4,
    "loader": 4,
    "loader-1x1": 4,
    "lane-splitter": 4,
    "linked-belt": 4,
    "inserter": 4,
    "assembling-machine": 4,
    "furnace": 4,
    "mining-drill": 4,
    "offshore-pump": 4,
    "pump": 4,
    "boiler": 4,
    "generator": 4,
    "pipe": 4,
    "pipe-to-ground": 4,
    "gate": 4,
    "arithmetic-combinator": 4,
    "decider-combinator": 4,
    "constant-combinator": 4,
    "selector-combinator": 4,
    "programmable-speaker": 4,
    "display-panel": 4,
    "train-stop": 4,
    "storage-tank": 4,
    "artillery-turret": 4,
    "thruster": 4,
    "asteroid-collector": 4,
    "agricultural-tower": 4,
}

# 实测出来的「配方门控」名单：这些机器裸建（蓝图里不带 recipe）时 direction 会被规整成 0，
# 写了 recipe 才是 0/4/8/12。判据来自真机裸建矩阵（同一表面、同一台机器、唯一变量是 recipe）：
#   foundry         裸建 0 → recipe=casting-iron 后 8；写非流体配方 transport-belt 又回 0
#   cryogenic-plant 同根因
# 反面样本（裸建也认，别误伤）：recycler / chemical-plant / oil-refinery。
# 另一类别混进来：electromagnetic-plant / biochamber 的探针**用了配方**仍读回 1 档——它们就是不可旋转，
# 不是配方门控。判据是"带配方时能不能读到 >1 档"，不是"有没有 crafting_categories"，也不是方形与否
# （chemical-plant 方、裸建也认；cryogenic-plant 方、裸建就丢）。
DIRECTION_RECIPE_GATED = {"foundry", "cryogenic-plant"}

# 轨道/列车类：direction 语义与普通实体不同（有半斜向、有 rail_direction/rail_layer），
# 不在这里拍数字，单独标 special，留给轨道专责的人。
RAIL_TYPES = {
    "straight-rail", "half-diagonal-rail", "curved-rail-a", "curved-rail-b",
    "legacy-straight-rail", "legacy-curved-rail",
    "elevated-straight-rail", "elevated-half-diagonal-rail",
    "elevated-curved-rail-a", "elevated-curved-rail-b",
    "rail-support", "rail-ramp", "rail-signal", "rail-chain-signal",
    "locomotive", "cargo-wagon", "fluid-wagon", "artillery-wagon",
    "infinity-cargo-wagon",
}


def direction_info(type_name, proto):
    if type_name in RAIL_TYPES:
        return None, "rail", False, "轨道类 direction 语义与普通实体不同，未拍数字"
    # 注意：two_direction_only 是**贴图**只有 2 套的意思，不是只认 2 个朝向。
    # 反例实测：steam-engine 带这个标志，但真蓝图里 direction 出现 0/4/12 三种。
    # 所以它不进 dirs，只单独记一笔给渲染用。
    if proto.get("turret_base_has_direction"):
        return 4, "dump:turret_base_has_direction", False, "原型自带 turret_base_has_direction"
    if type_name in ("ammo-turret", "electric-turret", "fluid-turret"):
        return 0, "type:static-turret", False, "炮塔自动瞄准，落地朝向无意义（除上面的 turret_base_has_direction）"
    if type_name in ROTATABLE_TYPES:
        return ROTATABLE_TYPES[type_name], "type:rotatable", False, "按 type 归类，未实测"
    return 0, "type:static", False, "按 type 归类为不可旋转，未实测"


# ---------------------------------------------------------------- 端口

def connectors(box):
    """把 fluid_box.pipe_connections 摊平成 [{p:[x,y], d:0|4|8|12, ...}]。
    位置是朝北(north)坐标系下的相对坐标，其它朝向由调用方按 4 档旋转推出。"""
    out = []
    for c in (box.get("pipe_connections") or []):
        if not isinstance(c, dict):
            continue
        rec = {"p": c.get("position"), "d": c.get("direction")}
        if c.get("flow_direction"):
            rec["flow"] = c["flow_direction"]
        if c.get("connection_type"):
            rec["underground"] = True
        if c.get("max_underground_pipes") is not None:
            rec["max_underground_pipes"] = c["max_underground_pipes"]
        out.append(rec)
    return out


def fluid_boxes(proto):
    boxes = []
    single = proto.get("fluid_box")
    if isinstance(single, dict):
        boxes.append(single)
    for b in (proto.get("fluid_boxes") or []):
        if isinstance(b, dict):
            boxes.append(b)
    out = []
    for b in boxes:
        cs = connectors(b)
        if not cs:
            continue
        rec = {"io": b.get("production_type") or "input-output", "c": cs}
        if b.get("volume") is not None:
            rec["volume"] = b["volume"]
        out.append(rec)
    # 锅炉/热交换器那种：进水和出水是两个 fluid_box
    ob = proto.get("output_fluid_box")
    if isinstance(ob, dict):
        cs = connectors(ob)
        if cs:
            out.append({"io": "output", "c": cs, "from": "output_fluid_box"})
    return out


# ---------------------------------------------------------------- 主抽取

def energy_of(proto):
    es = proto.get("energy_source")
    e = {}
    if isinstance(es, dict):
        for k in ("type", "usage_priority", "drain", "buffer_capacity", "fuel_categories"):
            if k in es:
                e[k] = es[k]
        if es.get("emissions_per_minute"):
            e["emissions"] = es["emissions_per_minute"]
    for k, tgt in (("energy_usage", "usage"), ("max_energy_usage", "max_usage"),
                   ("energy_consumption", "consumption"), ("max_power_output", "max_output"),
                   ("effectivity", "effectivity"), ("energy_per_movement", "per_movement"),
                   ("energy_per_rotation", "per_rotation")):
        if proto.get(k) is not None:
            e[tgt] = proto[k]
    return e or None


def recipe_refs(dump, name):
    """反向索引：这个实体能做什么配方（按 recipe.categories 交集）。"""
    return None  # 由 build_recipes 统一算


def main():
    if not os.path.exists(DUMP):
        sys.exit("找不到原型转储: " + DUMP)
    with open(DUMP, "r", encoding="utf-8") as f:
        d = json.load(f)

    # --- 判定 dump 是否带 mod：看所有 __namespace__ 引用
    namespaces = set()
    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and o.startswith("__"):
            parts = o.split("__")
            if len(parts) >= 3:
                namespaces.add(parts[1])
    walk(d)
    official = {"core", "base", "space-age", "quality", "elevated-rails", "recycler"}
    foreign = sorted(namespaces - official)

    # --- 认哪些顶层组是实体原型
    def is_entity_group(k, v):
        if not isinstance(v, dict) or not v:
            return False
        sample = next(iter(v.values()))
        if not (isinstance(sample, dict) and sample.get("type") == k):
            return False
        n_box = sum(1 for p in v.values()
                    if isinstance(p, dict) and ("collision_box" in p or "selection_box" in p))
        return n_box >= max(1, len(v) * 0.5)

    groups = [k for k, v in d.items() if is_entity_group(k, v)]

    # --- 物品 → 放置出的实体（用来查「造这台机器要什么」）
    place_item = {}
    for iname, ip in d.get("item", {}).items():
        pr = ip.get("place_result")
        if pr:
            place_item.setdefault(pr, iname)

    entities = {}
    for g in groups:
        for name, proto in d[g].items():
            cb = proto.get("collision_box")
            sb = proto.get("selection_box")
            ce = box_extent(cb)
            se = box_extent(sb)
            rec = {"type": proto.get("type", g)}

            ov = SIZE_OVERRIDES.get(name)
            if ov:
                rec["w"], rec["h"] = ov["w"], ov["h"]
                rec["size_source"] = "override"
                rec["size_note"] = ov["why"]
            elif ce:
                rec["w"], rec["h"] = tiles(ce[0]), tiles(ce[1])
                rec["size_source"] = "collision_box"
            elif se:
                rec["w"], rec["h"] = tiles(se[0]), tiles(se[1])
                rec["size_source"] = "selection_box"
            else:
                rec["w"], rec["h"] = 0, 0
                rec["size_source"] = "missing"

            dirs, dirs_src, verified, dirs_note = direction_info(rec["type"], proto)
            rec["dirs"] = dirs
            rec["dirs_source"] = dirs_src
            rec["dirs_verified"] = verified
            if dirs_note:
                rec["dirs_note"] = dirs_note
            if proto.get("two_direction_only"):
                rec["graphics_two_direction_only"] = True

            if cb:
                rec["collision_box"] = cb
            if sb:
                rec["selection_box"] = sb
            if proto.get("flags"):
                rec["flags"] = proto["flags"]
                rec["placeable"] = any(f in ("placeable-player", "placeable-neutral", "placeable-enemy")
                                       for f in proto["flags"])
            else:
                rec["placeable"] = False
            if proto.get("max_health") is not None:
                rec["hp"] = proto["max_health"]
            if place_item.get(name):
                rec["item"] = place_item[name]

            e = energy_of(proto)
            if e:
                rec["energy"] = e

            cs = proto.get("crafting_speed")
            cats = proto.get("crafting_categories")
            if cs is not None or cats:
                c = {}
                if cs is not None:
                    c["speed"] = cs
                if cats:
                    c["categories"] = cats
                rec["crafting"] = c
            if proto.get("module_slots"):
                rec["module_slots"] = proto["module_slots"]
            if proto.get("allowed_effects"):
                rec["allowed_effects"] = proto["allowed_effects"]
            be = proto.get("effect_receiver", {}).get("base_effect") if isinstance(proto.get("effect_receiver"), dict) else None
            if be:
                rec["effect_receiver"] = be
            if proto.get("beacon_effect_receiver") if "beacon_effect_receiver" in proto else False:
                rec["beacon_effect_receiver"] = proto["beacon_effect_receiver"]

            fb = fluid_boxes(proto)
            if fb:
                rec["fluids"] = fb

            if rec["type"] == "inserter":
                ins = {}
                for k_src, k_dst in (("pickup_position", "pickup"), ("insert_position", "insert"),
                                     ("rotation_speed", "rot_speed"),
                                     ("extension_speed", "ext_speed"),
                                     ("energy_per_movement", "e_move"),
                                     ("energy_per_rotation", "e_rot")):
                    if proto.get(k_src) is not None:
                        ins[k_dst] = proto[k_src]
                if ins:
                    rec["inserter"] = ins

            if rec["type"] in ("transport-belt", "underground-belt", "splitter", "loader"):
                b = {}
                if proto.get("speed") is not None:
                    b["speed"] = proto["speed"]
                if proto.get("max_distance") is not None:
                    b["max_distance"] = proto["max_distance"]
                if proto.get("structure") and isinstance(proto["structure"], dict) and "direction_in" in proto["structure"]:
                    b["has_direction_structure"] = True
                if b:
                    rec["belt"] = b

            if rec["type"] == "mining-drill":
                dr = {}
                for k_src, k_dst in (("mining_speed", "speed"),
                                     ("resource_searching_radius", "radius"),
                                     ("vector_to_place_result", "output"),
                                     ("resource_categories", "categories"),
                                     ("energy_usage", "energy_usage")):
                    if proto.get(k_src) is not None:
                        dr[k_dst] = proto[k_src]
                if dr:
                    rec["drill"] = dr

            if rec["type"] in ("container", "logistic-container"):
                st = {}
                for k_src, k_dst in (("inventory_size", "slots"), ("logistic_mode", "logistic_mode"),
                                     ("logistic_parameters", "logistic"), ("trash_inventory_size", "trash_slots")):
                    if proto.get(k_src) is not None:
                        st[k_dst] = proto[k_src]
                if st:
                    rec["storage"] = st

            if rec["type"] == "electric-pole":
                ep = {}
                for k_src, k_dst in (("supply_area_distance", "supply_area"),
                                     ("maximum_wire_distance", "wire_distance")):
                    if proto.get(k_src) is not None:
                        ep[k_dst] = proto[k_src]
                if ep:
                    rec["pole"] = ep

            if rec["type"] == "roboport":
                rp = {}
                for k_src, k_dst in (("logistics_radius", "logistics_radius"),
                                     ("construction_radius", "construction_radius"),
                                     ("robot_slots_count", "robot_slots"),
                                     ("material_slots_count", "material_slots")):
                    if proto.get(k_src) is not None:
                        rp[k_dst] = proto[k_src]
                if rp:
                    rec["roboport"] = rp

            if rec["type"] == "beacon":
                bc = {}
                for k_src, k_dst in (("supply_area_distance", "supply_area"),
                                     ("distribution_effectivity", "effectivity"),
                                     ("module_slots", "slots"),
                                     ("allowed_effects", "allowed_effects")):
                    if proto.get(k_src) is not None:
                        bc[k_dst] = proto[k_src]
                if bc:
                    rec["beacon"] = bc

            if rec["type"] == "lab":
                lb = {}
                for k_src, k_dst in (("inputs", "inputs"), ("researching_speed", "speed"),
                                     ("energy_usage", "energy_usage")):
                    if proto.get(k_src) is not None:
                        lb[k_dst] = proto[k_src]
                if lb:
                    rec["lab"] = lb

            if rec["type"] == "reactor":
                rn = {}
                for k_src, k_dst in (("neighbour_bonus", "neighbour_bonus"),
                                     ("consumption", "consumption"),
                                     ("burner", "burner")):
                    if proto.get(k_src) is not None:
                        rn[k_dst] = proto[k_src]
                if rn:
                    rec["reactor"] = rn

            if rec["type"] == "boiler":
                bl = {}
                for k_src, k_dst in (("target_temperature", "target_temperature"),
                                     ("mode", "mode"), ("energy_consumption", "energy_consumption")):
                    if proto.get(k_src) is not None:
                        bl[k_dst] = proto[k_src]
                if bl:
                    rec["boiler"] = bl

            if rec["type"] in ("assembling-machine", "furnace"):
                m = {}
                if proto.get("fluid_boxes_off_when_no_fluid_recipe"):
                    m["fluid_boxes_off_when_no_fluid_recipe"] = True
                if m:
                    rec["machine"] = m

            if proto.get("surface_conditions"):
                rec["surface_conditions"] = proto["surface_conditions"]

            entities[name] = rec

    # --- 用真语料给真值表做一次体检：实测方向分布 + 位置奇偶对照
    corpus_dir = os.path.join(os.path.dirname(OUT), "corpus")
    parity = {"checked": 0, "matched": 0, "mismatched": []}
    if os.path.isdir(corpus_dir):
        import collections
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bpstring
        joint = collections.defaultdict(collections.Counter)
        for fn in sorted(os.listdir(corpus_dir)):
            if not fn.endswith(".json") or fn == "index.json":
                continue
            with open(os.path.join(corpus_dir, fn), encoding="utf-8") as f:
                doc = json.load(f)
            for _path, bp in bpstring.iter_blueprints(doc):
                for e in (bp.get("entities") or []):
                    p = e.get("position") or {}
                    if "x" in p and "y" in p:
                        joint[e["name"]][(e.get("direction", 0),
                                          round(abs(p["x"] % 1.0), 1),
                                          round(abs(p["y"] % 1.0), 1))] += 1
        for name, cnt in joint.items():
            rec = entities.get(name)
            if not rec:
                continue
            rec["observed_dirs"] = sorted({_dir for _dir, _x, _y in cnt})
            rec["observed_n"] = sum(cnt.values())
            if rec["type"] in RAIL_TYPES or not rec["w"] or not rec["h"]:
                continue
            total = sum(cnt.values())
            ok = 0
            for (_dir, fx, fy), k in cnt.items():
                ew, eh = (rec["h"], rec["w"]) if _dir in (4, 12) else (rec["w"], rec["h"])
                if fx == (0.5 if ew % 2 else 0.0) and fy == (0.5 if eh % 2 else 0.0):
                    ok += k
            parity["checked"] += 1
            if ok >= total * 0.995:
                parity["matched"] += 1
            else:
                parity["mismatched"].append({"name": name, "w": rec["w"], "h": rec["h"],
                                             "match_ratio": round(ok / total, 4), "n": total})

    # --- 真机方向实测（tools/gt_directions.py 产出，存成 data/direction-probe.json）
    # 判据只能是真机：把 direction 写进单块蓝图 → 建出来 → 读回。语料只作旁证。
    #
    # 两个**不同**的问题，分两个字段，别用一个数糊住：
    #   dirs          —— 游戏认几档朝向（"能不能转"）
    #   observed_dirs —— 真实蓝图里玩家写过哪些方向（"玩家干了什么"）
    # 二者在装配机 / 炉子 / foundry 上会打架，打架时**探针是对的**。语料侧独立旁证：
    #   electric-furnace 177 个全部没有 direction 字段、assembling-machine-1 30 个全部没有，
    #   而 oil-refinery 14 个全部有、chemical-plant 53 个里 37 个有。**能转的才带字段。**
    #
    # ⚠ 最容易踩的一条（真机实测，不是推测）：
    #   **带配方槽的机器，direction 只有在实体写了 recipe 时才生效。** 裸建（无配方）的 foundry /
    #   cryogenic-plant 会把 direction 规整成 0；写了 recipe 才是 0/4/8/12。所以编译器给这类机器
    #   写方向时，必须同时写 recipe，否则方向静默丢失。语料旁证：70 个"方向非零的带配方机器"里
    #   60 个同时带 recipe，其余 10 个是 6 台 recycler（2x4，转向会改占地）和 4 台 AM3 孤例（游戏本就不认）。
    #
    # 校验器口径：非轨道实体在蓝图里出现 4 的倍数一律不许报错——真蓝图里确实有（AM3 有 4 例），
    # 游戏照收不报错，只是不生效。那种情况给 warning，用 direction_ignored_by_game 标出来。
    probe_path = os.path.join(os.path.dirname(OUT), "direction-probe.json")
    probe_rows = {}
    if os.path.exists(probe_path):
        with open(probe_path, encoding="utf-8") as f:
            for row in json.load(f).get("rows") or []:
                if row.get("dirs") is not None:
                    probe_rows[row["name"]] = row

    # 方向这件事有**两个不同的问题**，必须分两个字段，别用一个数糊住：
    #   dirs          —— 游戏认几档朝向（"能不能转"）。判据只能是真机：蓝图往返后读回。
    #   observed_dirs —— 真实蓝图里玩家写过哪些方向。这是"玩家干了什么"，不是"游戏认什么"。
    # 两者在装配机/炉子/foundry 上会打架，且打架时**探针是对的**：
    # 游戏解析蓝图时就把这些原型的 direction 字段丢了（get_blueprint_entities 读回没有该字段），
    # 所以蓝图里写 direction=12 不报错、也不会生效。语料里那 4 个 AM3 带 direction 的是孤例。
    #
    # 但**校验器不能因此判它非法**——真实蓝图里确实有，游戏也确实照收。
    # 所以第三个字段 direction_accept 给出"必须放行"的取值集合。
    dir_conflicts = []
    for name, rec in entities.items():
        observed = rec.get("observed_dirs") or []
        type_dirs = rec.get("dirs") or 0
        row = probe_rows.get(name)
        probe_dirs = None
        if row:
            probe_dirs = 0 if row["dirs"] == 1 else row["dirs"]
            rec["dirs_probe"] = probe_dirs
            rec["dirs_seen"] = row.get("directions_seen")
            if row.get("probe_recipe"):
                rec["probe_recipe"] = row["probe_recipe"]

        if rec["type"] in RAIL_TYPES:
            # 轨道类的 direction 是坡度/连接编码，不是朝向；档数与取值都只信探针
            if probe_dirs is not None:
                rec["dirs"] = probe_dirs
                rec["dirs_source"] = "probe"
                rec["dirs_verified"] = True
                rec["direction_accept"] = row.get("directions_seen") or []
            rec.pop("dirs_note", None)
            continue

        if probe_dirs is not None:
            rec["dirs"] = probe_dirs
            rec["dirs_source"] = "probe"
            rec["dirs_verified"] = True
        else:
            rec["dirs"] = type_dirs
            rec["dirs_source"] = "type"
            rec["dirs_verified"] = False
        rec.pop("dirs_note", None)

        seen = rec.get("dirs_seen") or []
        rec["direction_accept"] = [0, 4, 8, 12] if rec["dirs"] <= 4 else sorted(set(seen) | {0, 4, 8, 12})
        if name in DIRECTION_RECIPE_GATED:
            # 不写 recipe 就丢方向 —— 编译器写这个实体的 direction 时必须同时写 recipe
            rec["direction_requires_recipe"] = True
        if rec["dirs"] <= 1:
            # 游戏不认方向，但真蓝图会写——判错就是误报，只许 warning
            rec["direction_ignored_by_game"] = True
            if len(observed) > 1:
                dir_conflicts.append({"name": name, "type": rec["type"], "game_dirs": rec["dirs"],
                                      "type": type_dirs, "probe": probe_dirs,
                                      "blueprint_wrote": observed})

    # --- 配方
    recipes = {}
    for rname, rp in d.get("recipe", {}).items():
        r = {}
        if rp.get("categories"):
            r["categories"] = rp["categories"]
        else:
            r["categories"] = ["crafting"]
        # energy_required 缺省时游戏用 0.5 秒，这是最容易踩的一格
        r["time"] = rp.get("energy_required", 0.5)
        if "energy_required" not in rp:
            r["time_default"] = True
        ins = []
        for i in (rp.get("ingredients") or []):
            if isinstance(i, dict):
                ins.append([i.get("name"), i.get("amount") if i.get("amount") is not None else i.get("amount_min", 1)])
            elif isinstance(i, list) and len(i) == 2:
                ins.append([i[0], i[1]])
        outs = []
        for o in (rp.get("results") or []):
            if isinstance(o, dict):
                outs.append([o.get("name"), o.get("amount", 1)])
            elif isinstance(o, list) and len(o) == 2:
                outs.append([o[0], o[1]])
        r["in"] = ins
        r["out"] = outs
        if rp.get("main_product"):
            r["main_product"] = rp["main_product"]
        if rp.get("hidden"):
            r["hidden"] = True
        if rp.get("enabled") is False:
            r["enabled"] = False
        if rp.get("allow_productivity"):
            r["allow_productivity"] = True
        if rp.get("allow_quality") is not None:
            r["allow_quality"] = rp["allow_quality"]
        if rp.get("auto_recycle") is not None:
            r["auto_recycle"] = rp["auto_recycle"]
        recipes[rname] = r

    # --- 反向索引：产物 → 配方
    by_product = {}
    for rname, r in recipes.items():
        for item, _amt in r["out"]:
            by_product.setdefault(item, []).append(rname)

    # --- 收集器：能做什么配方（按类别）
    machines_by_category = {}
    for name, e in entities.items():
        cats = (e.get("crafting") or {}).get("categories")
        if cats:
            for c in cats:
                machines_by_category.setdefault(c, []).append(name)

    # --- 物品 / 流体 / 模块 / 品质 / 星球
    items = {}
    for iname, ip in d.get("item", {}).items():
        items[iname] = {
            "type": ip.get("type", "item"),
            "stack": ip.get("stack_size", 1),
            "subgroup": ip.get("subgroup"),
            "place_result": ip.get("place_result"),
            "fuel_value": ip.get("fuel_value"),
        }

    fluids = {}
    for fname, fp in d.get("fluid", {}).items():
        fluids[fname] = {
            "default_temperature": fp.get("default_temperature"),
            "max_temperature": fp.get("max_temperature"),
        }

    modules = {}
    for mname, mp in d.get("module", {}).items():
        modules[mname] = {
            "category": mp.get("category"),
            "effects": mp.get("effect"),
            "tier": mp.get("tier"),
        }

    out = {
        "meta": {
            "source": DUMP,
            "source_bytes": os.path.getsize(DUMP),
            "source_mtime": datetime.datetime.fromtimestamp(os.path.getmtime(DUMP)).isoformat(timespec="seconds"),
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "dump_namespaces": sorted(namespaces),
            "mod_namespaces": foreign,
            "modded": bool(foreign),
            "official_content": sorted(official),
            "note": "modded=false 表示 dump 里只出现官方命名空间的资源引用；"
                    "若将来换用带 mod 的 dump，此字段会变成 true，画布就不该再被信任",
            "counts": {
                "entity_groups": len(groups),
                "entities": len(entities),
                "recipes": len(recipes),
                "items": len(items),
                "fluids": len(fluids),
            },
        },
        "recipe_categories": sorted(d.get("recipe-category", {}).keys()),
        "qualities": list(d.get("quality", {}).keys()),
        "planets": list(d.get("planet", {}).keys()),
        "modules": modules,
        "fluids": fluids,
        "items": items,
        "entities": entities,
        "recipes": recipes,
        "recipes_by_product": by_product,
        "machines_by_category": machines_by_category,
        "special": {
            "rail_types": sorted(RAIL_TYPES),
            "size_overrides": SIZE_OVERRIDES,
            "corpus_parity": parity,
            "dir_probe_conflicts": dir_conflicts,
        },
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("wrote", OUT, os.path.getsize(OUT), "bytes")
    print("entities", len(entities), "recipes", len(recipes), "modded", bool(foreign), "mod_ns", foreign)
    print("语料奇偶校验: %d/%d 吻合" % (parity["matched"], parity["checked"]))
    print("方向：真机探针 %d 条；游戏不认但蓝图里写过的 %d 条（校验器要放行，别判错）" % (
        len(probe_rows), len(dir_conflicts)))
    for c in sorted(dir_conflicts, key=lambda x: x["name"])[:24]:
        print("  孤例:", "%-26s type=%-22s 游戏认=%s 蓝图写过=%s" % (
            c["name"], c["type"], c["game_dirs"], c["blueprint_wrote"]))
    for m in parity["mismatched"]:
        print("  不吻合:", m)


if __name__ == "__main__":
    main()
