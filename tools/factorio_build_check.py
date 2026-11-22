"""真机蓝图校验：把蓝图送进 Factorio 本体跑一遍，回报能不能建、建出几个实体、报什么错。

用法：
    python tools/factorio_build_check.py a蓝图.txt b蓝图.txt
    python tools/factorio_build_check.py --expr "0eJw..."          # 直接给字符串
    python tools/factorio_build_check.py 蓝图.json --json out.json --keep out/runs
    python tools/factorio_build_check.py x.txt --text              # 人读摘要而不是 JSON

机制见 doc/真机校验.md：临时 mod（独立 --mod-directory）+ 隔离 write-data（--config）
+ --test-scenario 无头启动，场景里 import_stack → build_blueprint → 采样 → finish_test。
"""

import argparse
import base64
import hashlib
import math
import json
import os
import shutil
import sys
import tempfile
import zlib

import probe_rig

TOOL_DIR = probe_rig.TOOL_DIR
SCENARIO_TEMPLATE = os.path.join(TOOL_DIR, "probe_scenario.lua")
BP_KINDS = ("blueprint", "blueprint-book", "deconstruction-planner", "upgrade-planner")
BP_KIND_ALIASES = {"blueprint_book": "blueprint-book", "deconstruction_planner": "deconstruction-planner",
                   "upgrade_planner": "upgrade-planner"}

ISSUE_TEXT = {
    "missing_name": "缺少实体名",
    "unknown_entity": "游戏里没有这个实体",
    "unknown_recipe": "游戏里没有这个配方",
    "unknown_item": "游戏里没有这个物品",
    "unknown_quality": "游戏里没有这个品质等级",
}


class DecodeError(Exception):
    pass


def decode_blueprint(text):
    """返回 (kind, payload, encoded_string)。encoded_string 是交给游戏 import_stack 的原串。"""
    body = text.strip()
    if not body:
        raise DecodeError("输入为空")
    if body.startswith("{"):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise DecodeError("不是合法 JSON：%s" % exc)
        encoded = None
    else:
        payload_text = _inflate(body[1:] if body.startswith("0") else body)
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise DecodeError("解压后不是 JSON：%s" % exc)
        encoded = body
    raw_kind = next((key for key in BP_KINDS if key in payload), None)
    if raw_kind is None:
        raw_kind = next((key for key in BP_KIND_ALIASES if key in payload), None)
    if raw_kind is None:
        raise DecodeError("认不出这是蓝图/蓝图册/升级规划/拆除规划（顶层键：%s）" % list(payload))
    kind = BP_KIND_ALIASES.get(raw_kind, raw_kind)
    if encoded is None:
        compact = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        encoded = "0" + base64.b64encode(zlib.compress(compact, 9)).decode("ascii")
    return kind, payload[raw_kind], encoded


def _inflate(b64_body):
    compact = "".join(b64_body.split())
    if any(ord(ch) > 127 for ch in compact):
        raise DecodeError("不是合法 base64：混进了非 ASCII 字符（多半是复制时夹了中文或空格以外的符号）")
    padded = compact + "=" * (-len(compact) % 4)
    try:
        raw = base64.b64decode(padded, validate=False)
    except Exception as exc:
        raise DecodeError("不是合法 base64：%s" % exc)
    try:
        return zlib.decompress(raw).decode("utf-8")
    except zlib.error as exc:
        raise DecodeError("zlib 解不开（多半是长度截断或混进了非蓝图文本）：%s" % exc)


def lua_literal(value, indent=0):
    pad = "  " * indent
    inner = "  " * (indent + 1)
    if value is None:
        return "nil"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _lua_string(value)
    if isinstance(value, dict):
        parts = ["%s[%s] = %s" % (inner, _lua_string(str(key)), lua_literal(item, indent + 1))
                 for key, item in value.items()]
        return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
    if isinstance(value, (list, tuple)):
        parts = [inner + lua_literal(item, indent + 1) for item in value]
        return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
    raise TypeError("无法转成 Lua 的值：%r" % (value,))


def _lua_string(text):
    out = ['"']
    for ch in text:
        code = ord(ch)
        if ch in ('\\', '"'):
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif code < 32 or code == 127:
            out.append("\\%03d" % code)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def build_scenario(jobs, tick_limit, terrain="flat", run_ticks=0, power_probe=False, power_single=False,
                   feed_item="", fluid_probe=False, fluid_name="", unlock_recipes=True, ports="",
                   fluid_port="", supply_cells=""):
    with open(SCENARIO_TEMPLATE, encoding="utf-8") as handle:
        template = handle.read()
    payload = [{"id": job["id"], "blob": job["blob"], "json": job["payload"]} for job in jobs]
    return (template.replace("__JOBS__", lua_literal(payload), 1)
                    .replace("__TICK_LIMIT__", str(tick_limit), 1)
                    .replace("__TERRAIN__", terrain, 1)
                    .replace("__RUN_TICKS__", str(run_ticks), 1)
                    .replace("__POWER_PROBE__", "true" if power_probe else "false", 1)
                    .replace("__POWER_SINGLE__", "true" if power_single else "false", 1)
                    .replace("__FEED_ITEM__", feed_item or "", 1)
                    .replace("__FLUID_PROBE__", "true" if fluid_probe else "false", 1)
                    .replace("__FLUID_NAME__", fluid_name or "water", 1)
                    .replace("__UNLOCK_RECIPES__", "true" if unlock_recipes else "false", 1)
                    .replace("__PORT_CELLS__", ports or "", 1)
                    .replace("__FLUID_PORT__", fluid_port or "", 1)
                    .replace("__SUPPLY_CELLS__", supply_cells or "", 1))



DIRECTION_NAMES = {0: "北", 4: "东", 8: "南", 12: "西"}


def _round_pos(value, digits=1):
    return round(float(value), digits)


def _xy(value):
    """位置在 JSON 里可能是 [x, y] 也可能是 {x=…, y=…}，两种都收。"""
    if isinstance(value, dict):
        return _round_pos(value.get("x", 0)), _round_pos(value.get("y", 0))
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _round_pos(value[0]), _round_pos(value[1])
    return None


def _blueprint_offset(built_entities, blueprint_entities):
    """落在世界里的实体是蓝图坐标整体平移的结果：取出现次数最多的位移当偏移。"""
    from collections import Counter
    deltas = Counter()
    by_name = {}
    for entity in blueprint_entities:
        name = entity.get("name")
        position = _xy(entity.get("position"))
        if name and position:
            by_name.setdefault(name, []).append(position)
    for built in built_entities:
        candidates = by_name.get(built.get("name")) or []
        built_position = _xy(built.get("pos"))
        if built_position is None:
            continue
        for bx, by in candidates:
            deltas[(_round_pos(built_position[0] - bx), _round_pos(built_position[1] - by))] += 1
    if not deltas:
        return (0.0, 0.0)
    return deltas.most_common(1)[0][0]


def _blueprint_index(blueprint_entities, offset):
    index = {}
    for entity in blueprint_entities:
        name = entity.get("name")
        position = _xy(entity.get("position"))
        if not name or not position:
            continue
        key = (name, _round_pos(position[0] + offset[0]), _round_pos(position[1] + offset[1]))
        index.setdefault(key, entity.get("entity_number"))
    return index


def _fmt_pos(value, offset=None):
    position = _xy(value)
    if not position:
        return "?"
    if offset:
        position = (_round_pos(position[0] - offset[0]), _round_pos(position[1] - offset[1]))
    return "(%s,%s)" % (position[0], position[1])


def _describe(entity, at=None):
    direction = entity.get("direction")
    label = DIRECTION_NAMES.get(direction if direction is not None else 0, str(direction))
    position = at or _xy(entity.get("pos")) or (0, 0)
    return "%s@(%s,%s)朝%s" % (entity.get("name"), position[0], position[1], label)



TERRAIN_MODES = ("flat", "ore", "water")




def power_summary(job, raw_job):
    """供电覆盖：先给电网塞发电机，再逐实体读状态。no_power 就是游戏判的"没覆盖到"。"""
    power = (raw_job or {}).get("power") or {}
    entities = power.get("entities") or []
    if not entities:
        return None
    poles = power.get("poles") or []
    setup = (raw_job or {}).get("power_setup") or {}

    def distance(a, b):
        # 供电范围是正方形（supply_area_distance 是切比雪夫半径），不是圆，所以用 max(|dx|,|dy|)
        return round(max(abs(float(a["x"]) - float(b["x"])), abs(float(a["y"]) - float(b["y"]))), 2)

    unpowered = []
    powered = 0
    for entity in entities:
        if entity.get("status") == "no_power":
            nearest = None
            if poles:
                nearest = min((distance(entity["pos"], pole["pos"]) for pole in poles), default=None)
            unpowered.append({"unit": entity.get("unit"), "name": entity.get("name"),
                              "at": [entity["pos"]["x"], entity["pos"]["y"]],
                              "nearest_pole_distance": nearest})
        elif entity.get("network") is not None:
            powered += 1

    blueprint_wires = len(((job.get("payload") or {}).get("wires")) or [])
    networks = {}
    for pole in poles:
        key = str(pole.get("network"))
        networks.setdefault(key, []).append(pole)
    network_list = [{"network": key, "poles": len(group), "positions": [[p["pos"]["x"], p["pos"]["y"]] for p in group]}
                    for key, group in networks.items()]
    single_network = len(networks) <= 1
    arms = [entity for entity in entities if entity.get("type") == "inserter"]
    arm_distances = [min(distance(arm["pos"], pole["pos"]) for pole in poles) for arm in arms] if poles else []
    consumers = [entity for entity in entities
                 if entity.get("type") != "electric-pole"
                 and (entity.get("network") is not None or entity.get("status") == "no_power")]
    consumer_distances = []
    for entity in consumers:
        if poles:
            consumer_distances.append(min(distance(entity["pos"], pole["pos"]) for pole in poles))
    return {
        "poles": len(poles),
        "blueprint_wires": blueprint_wires,
        "max_consumer_pole_distance": round(max(consumer_distances), 2) if consumer_distances else None,
        "network_split_warning": (len(networks) > 1),
        "arms": len(arms),
        "arms_unpowered": sum(1 for arm in arms if arm.get("status") == "no_power"),
        "arms_max_pole_distance": round(max(arm_distances), 2) if arm_distances else None,
        "generators": setup.get("generators"),
        "generator_kind": setup.get("generator_kind"),
        "networks": network_list,
        "single_network": single_network,
        "powered_entities": powered,
        "unpowered_count": len(unpowered),
        "unpowered": unpowered,
        "verdict": ("所有用电实体都在电网覆盖内（未覆盖 0 个）" if not unpowered
                    else "%d 个实体没被电塔覆盖到" % len(unpowered)),
    }




def fluids_summary(job, raw_job):
    """流体第四条：灌进去之后，目标里到底有没有。这一块不进 usable（它说的是"跑起来有没有"，不是结构缺陷）。"""
    fluids = (raw_job or {}).get("fluids")
    if not fluids:
        return None
    source = fluids.get("source") or {}
    entities = fluids.get("entities") or []
    segments = fluids.get("by_segment") or {}

    targets = []
    total = 0
    for entity in entities:
        contents = entity.get("contents") or {}
        amount = sum(contents.values()) if isinstance(contents, dict) else 0
        if amount:
            total += amount
        targets.append({"name": entity.get("name"), "type": entity.get("type"),
                        "at": [entity["pos"][0], entity["pos"][1]],
                        "segment": entity.get("segment"), "contents": contents, "amount": amount,
                        "recipe": entity.get("recipe")})

    machines = [t for t in targets if t["type"] in ("storage-tank", "furnace", "assembling-machine",
                                                    "chemical-plant", "boiler", "offshore-pump", "pump")]
    filled_machines = [t for t in machines if t["amount"] > 0]
    # 机器读了 0 要分两种：没配方（前提不成立，判据不适用） vs 有配方却干着（真没接上）
    no_recipe = [t for t in machines if t["amount"] == 0 and not t.get("recipe")
                 and t["type"] in ("assembling-machine", "chemical-plant", "furnace", "oil-refinery")]
    dry_with_recipe = [t for t in machines if t["amount"] == 0 and t.get("recipe")]
    problems = []
    for target in dry_with_recipe:
        problems.append({"code": "machine_dry_with_recipe", "severity": "error",
                         "message": "%s@%s 有配方（%s）但 fluidbox 是空的：液体根本没接到它"
                                    % (target["name"], _fmt_pos(target["at"]), target.get("recipe"))})
    if no_recipe:
        problems.append({"code": "machine_no_recipe", "severity": "warning",
                         "message": "%d 台机器没有配方（%s）：不吸液，流体判据对它们不适用"
                                    % (len(no_recipe), "、".join(sorted({t["name"] for t in no_recipe})))})
    if not source.get("placed"):
        problems.append({"code": "no_fluid_source", "severity": "warning",
                         "message": "没找到能接 infinity-pipe 的管道（管网为空或四周被占），这次没灌流体"})
    elif not total:
        problems.append({"code": "fluid_not_arrived", "severity": "warning",
                         "message": "灌了 %s，但管网里一处都没读到（跑的时间太短，或管段根本不连通）" % source.get("fluid")})
    return {
        "source": source,
        "segments": segments,
        "targets": targets,
        "machines": {"count": len(machines), "filled": len(filled_machines),
                     "no_recipe": len(no_recipe), "dry_with_recipe": len(dry_with_recipe)},
        "total_fluid": total,
        "problems": problems,
        "ok": bool(total),
    }


def pipes_summary(raw_job):
    """管线判据：管段连通 / 地下管配对 / 机器液口接入。全部读游戏字段，不靠画图。"""
    entities = ((raw_job or {}).get("adjacency") or {}).get("entities") or []
    pipes = [e for e in entities if e.get("type") in ("pipe", "pipe-to-ground")]
    if not pipes:
        return None

    segments = {}
    for pipe in pipes:
        for box in pipe.get("fluid_boxes") or []:
            segment = box.get("segment")
            if segment is not None:
                segments.setdefault(str(segment), []).append(pipe)
    unpaired = []
    for pipe in pipes:
        if pipe.get("type") != "pipe-to-ground":
            continue
        shared = False
        for box in pipe.get("fluid_boxes") or []:
            segment = box.get("segment")
            if segment is not None and len(segments.get(str(segment), [])) > 1:
                shared = True
        if not shared:
            unpaired.append({"entity": pipe.get("unit"), "at": pipe.get("pos"),
                             "direction": pipe.get("direction")})

    # 机器液口：有配方、有液口，却一个管道连接都没有
    unconnected_machines = []
    for entity in entities:
        boxes = entity.get("fluid_boxes") or []
        if not boxes or entity.get("type") in ("pipe", "pipe-to-ground"):
            continue
        expected = sum(box.get("expected_connections") or 0 for box in boxes)
        connected = sum(box.get("neighbours") or 0 for box in boxes)
        if expected > 0 and connected == 0:
            unconnected_machines.append({"entity": entity.get("unit"), "name": entity.get("name"),
                                         "at": entity.get("pos"), "has_recipe": entity.get("recipe") is not None})

    problems = []
    for item in unpaired:
        problems.append({"code": "underground_pipe_unpaired", "severity": "error",
                         "message": "地下管道没有配对（%s@%s 朝 %s）：它自己一段，液体到此为止"
                                    % ("pipe-to-ground", _fmt_pos(item["at"]), item["direction"])})
    for segment_id, members in segments.items():
        if len(members) == 1 and members[0].get("type") != "pipe-to-ground":
            problems.append({"code": "pipe_isolated", "severity": "warning",
                             "message": "有一段只有 1 个管道实体（%s），等于没连上任何东西" % _fmt_pos(members[0].get("pos"))})
    for item in unconnected_machines:
        if item["has_recipe"]:
            problems.append({"code": "machine_fluid_port_open", "severity": "error",
                             "message": "%s@%s 有配方但液口一个管道都没接" % (item["name"], _fmt_pos(item["at"]))})

    return {
        "pipes": len(pipes),
        "segments": [{"segment": key, "members": len(members)} for key, members in sorted(segments.items())],
        "segment_count": len(segments),
        "unpaired_underground": unpaired,
        "unconnected_machines": unconnected_machines,
        "problems": problems,
        "counts": {"errors": sum(1 for p in problems if p["severity"] == "error"),
                   "warnings": sum(1 for p in problems if p["severity"] == "warning")},
    }


def production_summary(job, raw_job, run_seconds):
    """L3：跑完 N 秒之后，数"真的产出了多少"，并解释为什么没产。"""
    if not run_seconds:
        return None
    production = (raw_job or {}).get("production") or {}
    fixtures = (raw_job or {}).get("fixtures") or {}
    machines = production.get("machines") or []
    by_product = {}
    for machine in machines:
        item = machine.get("recipe")
        if item:
            by_product[item] = by_product.get(item, 0) + (machine.get("products_finished") or 0)
    produced = production.get("total_products") or 0
    # 分母只允许来自"实体自己读得到的配方 + 速度"。读不到就是读不到，绝不兜底一个速度/配方：
    # 一个错的分母比没有分母更糟（会把好图判坏、坏图判好，而且两头都不报错）。
    theoretical = 0.0
    counted, skipped, mismatched = 0, 0, 0
    for machine in machines:
        speed = machine.get("crafting_speed")
        energy = machine.get("recipe_energy")
        if machine.get("recipe_source") == "mismatch":
            # 蓝图声明 steel-plate、游戏里却在炼 iron-plate：产量和分母不是同一件东西，
            # 这种读数不能算达成率（宁可说"算不出"，也不给一个假的比率）。
            mismatched += 1
        if speed and energy and speed > 0 and energy > 0:
            theoretical += run_seconds * speed / energy
            counted += 1
        else:
            skipped += 1
    # 配方对不上时：理论值仍按"蓝图声明的配方"给出（那是蓝图想要的产量），
    # 但达成率一律不给——实际产出的是另一件物品，比率没有意义。
    theoretical = round(theoretical, 1) if counted else None
    achievement = round(produced / theoretical, 3) if (theoretical and not mismatched) else None
    basis = {"machines_counted": counted, "machines_skipped": skipped, "recipe_mismatch": mismatched,
             "seconds_per_item": sorted({round(m.get("seconds_per_item"), 3)
                                         for m in machines if m.get("seconds_per_item")})}

    status_counts = {}
    for machine in machines:
        status = machine.get("status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
    entity_status = ((raw_job or {}).get("after_ticks") or {}).get("entities") or []
    entity_status_counts = {}
    for entity in entity_status:
        status = entity.get("status") or "unknown"
        entity_status_counts[status] = entity_status_counts.get(status, 0) + 1

    problems = []
    if mismatched:
        sample = next((m for m in machines if m.get("recipe_source") == "mismatch"), {})
        problems.append({"code": "recipe_mismatch", "severity": "error",
                         "message": "%d 台机器的配方和蓝图声明的不一致（蓝图 %s / 游戏实际 %s）——"
                                    "产量和理论值不是同一件物品，达成率不给"
                                    % (mismatched, sample.get("blueprint_recipe"), sample.get("game_recipe"))})
    if theoretical is None:
        problems.append({"code": "theoretical_unknown", "severity": "warning",
                         "message": "算不出理论产量（有机器读不到配方或速度），本次只报实际产出 %d 个" % produced})
    if theoretical and produced == 0:
        problems.append({"code": "no_output", "severity": "error",
                         "message": "跑满 %s 秒、理论应产 %s 个，实际一个都没产出" % (run_seconds, theoretical)})
    elif theoretical and achievement is not None and achievement < 0.5:
        problems.append({"code": "low_output", "severity": "warning",
                         "message": "跑满 %s 秒只产出 %d 个（理论 %s，达成率 %.0f%%）"
                                    % (run_seconds, produced, theoretical, achievement * 100)})
    if fixtures.get("main_item_source") == "inferred-furnace":
        problems.append({"code": "feed_item_inferred", "severity": "warning",
                         "message": "蓝图没声明配方、机器是石炉/钢炉，按类型推断喂 iron-ore（要精确请用 --feed-item）"})
    if fixtures.get("no_feed_item"):
        problems.append({"code": "no_feed_item", "severity": "warning",
                         "message": "推不出该喂什么物品（蓝图没声明配方），这次没有喂料；要跑请显式给 --feed-item"})
    if not fixtures.get("feeds") and not fixtures.get("feeds_count"):
        problems.append({"code": "no_feed_fixture", "severity": "warning",
                         "message": "没找到可以接喂料的带子断头（夹具没接上：可能是闭环带、或带子端头被挡）"})
    no_power = sum(count for status, count in entity_status_counts.items() if status in ("no_power", "low_power"))
    if no_power:
        problems.append({"code": "no_power", "severity": "error",
                         "message": "%d 个实体没电（夹具的太阳能场没接进电网）" % no_power})
    # 这些状态在两次送货之间是正常的（一炉 50 煤能烧 40 分钟），只当提示，不当错误：
    # 真正的判据是 produced vs theoretical。用户看到的"等待燃料"不该让整张图判红。
    for status, label in (("no_fuel", "没燃料"), ("no_ingredients", "没原料"),
                          ("waiting_for_source_items", "等不到来料"), ("full_output", "输出堵了"),
                          ("not_enough_space_in_output", "输出堵了"), ("item_ingredient_shortage", "原料不足")):
        count = entity_status_counts.get(status, 0)
        if count:
            problems.append({"code": "machine_" + status, "severity": "warning",
                             "message": "%d 个实体采样时是%s（瞬时状态，不据此判失败）" % (count, label)})

    return {
        "run_seconds": run_seconds,
        "produced": produced,
        "theoretical": theoretical,
        "achievement": achievement,
        "theoretical_basis": basis,
        "machine_status": status_counts,
        "entity_status": entity_status_counts,
        "machines": machines,
        "fixtures": fixtures,
        "by_product": by_product,
        "machine_outputs": [{"name": m.get("name"), "recipe": m.get("recipe"),
                             "input": m.get("input"), "input_max_total": m.get("input_max_total"),
                             "output": m.get("output"), "products": m.get("products_finished")} for m in machines],
        "output_chests": production.get("chests"),
        "fixture_diagnostics": production.get("fixture_diagnostics"),
        "belt_items": production.get("belt_items"),
        "belt_tiles": production.get("belt_tiles"),
        "belt_item_names": production.get("belt_item_names"),
        "drained": production.get("drained"),
        "drained_scope": production.get("drained_scope"),
        "belts": production.get("belt_contents"),
        "belt_samples": production.get("belt_samples"),
        "chests": production.get("chests"),
        "drained_caveat": ("出口带清出来的物品统计：用来防止产物堵死，也可能包含夹具灌进去的料"
                           "（输入线与产物线汇合时会串），所以产量以 produced（机器的 products_finished）为准"),
        "injected_total": production.get("injected_total"),
        "furnace_inputs": production.get("furnace_inputs"),
        "problems": problems,
        "ok": not any(problem["severity"] == "error" for problem in problems),
    }


def terrain_report(job, raw_job, terrain_mode):
    """地形模式的判据：钻头有没有挖到矿、水泵吸不吸得到水。都读游戏自己的字段。"""
    adjacency = (raw_job or {}).get("adjacency") or {}
    entities = adjacency.get("entities") or []
    setup = (raw_job or {}).get("terrain") or {"mode": terrain_mode}
    drills, pumps, problems = [], [], []
    for record in entities:
        if record.get("mining_area") is not None:
            target = record.get("mining_target")
            drill = {
                "entity": record.get("unit"), "name": record.get("name"),
                "at": record.get("pos"), "mining_area": record.get("mining_area"),
                "resources_in_area": record.get("resources_in_area"),
                "resource_names": record.get("resource_names"),
                "mining_target": (target or {}).get("name") if target else None,
                "ok": target is not None,
            }
            drills.append(drill)
            if target is None:
                if terrain_mode == "ore":
                    problems.append({"code": "drill_no_resource", "severity": "error",
                                     "message": "%s@%s：铺了矿还是挖不到（mining_area 里没有匹配的矿）"
                                                % (record.get("name"), _fmt_pos(record.get("pos")))})
                else:
                    problems.append({"code": "drill_no_resource_flat", "severity": "warning",
                                     "message": "%s@%s：这块地上没有矿（当前场地=平地，属于场地不匹配）"
                                                % (record.get("name"), _fmt_pos(record.get("pos")))})
        if record.get("source_tile") is not None or record.get("source_fluid") is not None:
            fluid = record.get("source_fluid")
            pump = {
                "entity": record.get("unit"), "name": record.get("name"),
                "at": record.get("pos"), "source_tile": record.get("source_tile"),
                "source_fluid": fluid,
                "fluid_contents": record.get("fluid_contents"),
                "nearby_pipe_fluids": record.get("nearby_pipe_fluids"),
                "ok": fluid is not None,
            }
            pumps.append(pump)
            if fluid is None and terrain_mode == "water":
                problems.append({"code": "pump_no_water", "severity": "error",
                                 "message": "%s@%s：水位那股水没接上（get_fluid_source_fluid 为空）"
                                            % (record.get("name"), _fmt_pos(record.get("pos")))})
            elif fluid is None:
                problems.append({"code": "pump_no_water_flat", "severity": "warning",
                                 "message": "%s@%s：这里没有可抽的水（当前场地=平地，属于场地不匹配）"
                                            % (record.get("name"), _fmt_pos(record.get("pos")))})

    return {
        "mode": terrain_mode,
        "setup": {key: value for key, value in setup.items() if key != "mode"},
        "checks": {"drills": drills, "pumps": pumps},
        "counts": {"drills": len(drills), "drills_ok": sum(1 for d in drills if d["ok"]),
                   "pumps": len(pumps), "pumps_ok": sum(1 for p in pumps if p["ok"]),
                   "problems": len(problems)},
        "problems": problems,
    }


def adjacency_problems(job, raw_job):
    """把游戏给的邻接读数翻译成人话：取/放位落空、带子断裂、管口没接上。"""
    adjacency = (raw_job or {}).get("adjacency") or {}
    entities = adjacency.get("entities") or []
    if not entities:
        return None
    payload = job.get("payload") or {}
    blueprint_entities = payload.get("entities") or []
    offset = _blueprint_offset(entities, blueprint_entities)
    blueprint_index = _blueprint_index(blueprint_entities, offset)

    blueprint_positions = {entity.get("entity_number"): _xy(entity.get("position"))
                           for entity in blueprint_entities}

    def entity_number_of(record):
        position = _xy(record.get("pos"))
        if position is None:
            return None
        return blueprint_index.get((record.get("name"), position[0], position[1]))

    problems = []
    # 先建两张索引：谁的取货位/放货位落在哪条带子上（用来判断带子两端是不是真空着）
    picked_units = {(record.get("pickup_target") or {}).get("unit") for record in entities
                    if (record.get("pickup_target") or {}).get("unit") is not None}
    dropped_units = {(record.get("drop_target") or {}).get("unit") for record in entities
                     if (record.get("drop_target") or {}).get("unit") is not None}

    segment_usage = {}
    for record in entities:
        for box in record.get("fluid_boxes") or []:
            if box.get("segment") is not None:
                key = (box.get("segment"), box.get("index"))
                segment_usage[key] = segment_usage.get(key, 0) + 1

    for record in entities:
        entity_type = record.get("type")
        number = entity_number_of(record)
        blueprint_position = blueprint_positions.get(number)
        tag = {"entity": number, "name": record.get("name"),
               "at": list(blueprint_position) if blueprint_position else None,
               "world": _xy(record.get("pos")), "dir": record.get("direction")}
        if entity_type == "inserter":
            if record.get("pickup") and not record.get("pickup_target"):
                problems.append(dict(tag, code="inserter_pickup_empty", severity="error",
                                     message="第 %s 个实体 %s：取货位 %s 上没有可取货的实体（对着空气）"
                                             % (number, _describe(record, blueprint_position), _fmt_pos(record.get("pickup"), offset))))
            if record.get("drop") and not record.get("drop_target"):
                problems.append(dict(tag, code="inserter_drop_empty", severity="error",
                                     message="第 %s 个实体 %s：放货位 %s 上没有可放货的实体（对着空气）"
                                             % (number, _describe(record, blueprint_position), _fmt_pos(record.get("drop"), offset))))
            continue

        if entity_type in ("underground-belt",) and record.get("underground_pair") is False:
            problems.append(dict(tag, code="underground_unpaired", severity="error",
                                 message="第 %s 个实体 %s：地下带没有配对进出口（另一端没对上或方向反了）"
                                         % (number, _describe(record, blueprint_position))))
        if entity_type in ("transport-belt", "splitter", "loader", "underground-belt", "linked-belt"):
            inputs = record.get("belt_inputs") or 0
            outputs = record.get("belt_outputs") or 0
            fed_by_inserter = record.get("unit") in dropped_units
            taken_by_inserter = record.get("unit") in picked_units
            is_underground_input = entity_type == "underground-belt" and str(record.get("belt_to_ground_type")) == "input"
            is_underground_output = entity_type == "underground-belt" and str(record.get("belt_to_ground_type")) == "output"
            if inputs == 0 and outputs == 0 and not fed_by_inserter and not taken_by_inserter \
                    and not record.get("underground_pair"):
                problems.append(dict(tag, code="belt_isolated", severity="error",
                                     message="第 %s 个实体 %s：这条带子前后都不连任何带子（孤立）" % (number, _describe(record, blueprint_position))))
            elif outputs == 0 and not is_underground_input and not taken_by_inserter:
                problems.append(dict(tag, code="belt_dead_end", severity="warning",
                                     message="第 %s 个实体 %s：带子末端断头（下游没有带子，也没人从这里取货）"
                                             % (number, _describe(record, blueprint_position))))
            elif inputs == 0 and not is_underground_output and not fed_by_inserter:
                problems.append(dict(tag, code="belt_no_source", severity="warning",
                                     message="第 %s 个实体 %s：带子入口没接上游（没有带子往这里送，也没有插入机放货）"
                                             % (number, _describe(record, blueprint_position))))

        boxes = record.get("fluid_boxes") or []
        if boxes:
            expected = sum(box.get("expected_connections") or 0 for box in boxes)
            connected = sum(box.get("neighbours") or 0 for box in boxes)
            if record.get("name") in ("pipe", "pipe-to-ground"):
                segment_isolated = all(
                    segment_usage.get((box.get("segment"), box.get("index")), 0) <= 1
                    for box in boxes if box.get("segment") is not None) if any(
                    box.get("segment") is not None for box in boxes) else (connected == 0)
                if segment_isolated and connected == 0:
                    problems.append(dict(tag, code="pipe_isolated", severity="error",
                                         message="第 %s 个实体 %s：管道没连上任何东西（管段是孤立的）" % (number, _describe(record, blueprint_position))))
            elif record.get("type") not in ("assembling-machine", "furnace") and not record.get("recipe"):
                # 泵、锅炉这类没有配方的设备，液口空着不算错（进水/出水口接不接是设计选择）
                pass
            elif expected > 0 and connected == 0 and not record.get("recipe"):
                problems.append(dict(tag, code="machine_no_recipe", severity="warning",
                                     message="第 %s 个实体 %s：没有设配方，%d 个液口也都没接管道（机器本身还是空壳）"
                                             % (number, _describe(record, blueprint_position), len(boxes))))
            elif expected > 0 and connected == 0:
                problems.append(dict(tag, code="fluid_port_unconnected", severity="error",
                                     message="第 %s 个实体 %s（配方 %s）：%d 个液口一个都没接上管道"
                                             % (number, _describe(record, blueprint_position), record.get("recipe"), len(boxes))))


    return {
        "caveat": "邻接判据只覆盖蓝图自身的实体：靠贴着别人工厂/轨道/火车才能工作的蓝图，取货位为空可能是设计如此，不是错。",
        "checked": {"entities": len(entities), "inserters": len(adjacency.get("inserters") or [])},
        "counts": {
            "problems": len(problems),
            "errors": sum(1 for problem in problems if problem["severity"] == "error"),
            "warnings": sum(1 for problem in problems if problem["severity"] == "warning"),
        },
        "problems": problems,
        "raw": entities,
    }


def _tiles_block(raw_job):
    tiles = raw_job.get("tiles") or {}
    if not tiles.get("expected"):
        return None
    left = len((raw_job.get("after_build") or {}).get("tile_ghosts") or [])
    return {"expected": tiles.get("expected"), "revived": tiles.get("revived"), "left_as_ghost": left}


def _status_histogram(entities):
    counts = {}
    for entity in entities:
        status = entity.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def job_report(job, raw_job, terrain_mode="flat", run_seconds=0.0):
    report = {"id": job["id"], "source": job["source"], "kind": job["kind"],
              "verdict": "fail", "errors": [], "summary": ""}
    if raw_job is None:
        report["errors"].append({"code": "no_result", "message": "游戏没有回这个蓝图的结果（场景在跑到它之前就结束了）"})
        return report
    if raw_job.get("stage") == "job_threw":
        report["errors"].append({"code": "rig_error", "message": "校验夹具内部报错：%s" % raw_job.get("error")})
        return report
    if raw_job.get("stage") == "surface_failed":
        report["errors"].append({"code": "rig_error", "message": "建不出测试场地（create_surface 失败）"})
        return report

    expected = raw_job.get("expected_entities") or 0
    parsed = raw_job.get("game_parsed_entities")
    after_build = raw_job.get("after_build") or {"entities": [], "ghosts": []}
    after_ticks = raw_job.get("after_ticks") or after_build
    built_entities = after_build.get("entities") or []
    ghosts = after_build.get("ghosts") or []
    issues = raw_job.get("issues") or []

    report.update({
        "expected_entities": expected,
        "game_parsed_entities": parsed,
        "import_code": raw_job.get("import_code"),
        "build_ok": raw_job.get("build_ok"),
        "built_entities": len(built_entities),
        "ghosts": len(ghosts),
        "entities_after_ticks": len(after_ticks.get("entities") or []),
        "issues": issues,
        "built": sorted((entity.get("name") or "?") for entity in built_entities),
        "ghost_names": sorted(ghost.get("name") or "?" for ghost in ghosts),
        "ghost_detail": raw_job.get("ghost_detail"),
        "statuses": _status_histogram(after_ticks.get("entities") or []),
        "arena": raw_job.get("arena"),
        "terrain_mode": raw_job.get("terrain_mode"),
        "recipes_unlocked": raw_job.get("recipes_unlocked"),
        "recipes": raw_job.get("recipes"),
        "tiles": _tiles_block(raw_job),
    })

    if not raw_job.get("build_ok", False) and raw_job.get("build_error"):
        report["errors"].append({"code": "build_threw",
                                 "message": "游戏在放置这一步抛错：%s" % raw_job.get("build_error")})

    for issue in issues:
        code = issue.get("kind")
        value = issue.get("value")
        report["errors"].append({
            "code": code,
            "message": "第 %s 个实体%s %s%s" % (
                issue.get("index"), (" " + (issue.get("name") or "")) if issue.get("name") else "",
                ISSUE_TEXT.get(code, code), ("（%s）" % value) if value else ""),
            "entity_index": issue.get("index"), "name": issue.get("name"), "value": value,
        })

    import_code = raw_job.get("import_code")
    if import_code == 1:
        report["errors"].append({"code": "import_rejected", "message": "游戏拒绝了这个蓝图字符串（import_stack=1）"})
    elif import_code == -1:
        report["errors"].append({"code": "import_partial",
                                 "message": "游戏只部分接受：%s 个实体 → 解析出 %s 个（其余被丢弃）" % (expected, parsed or 0)})

    # 实体已经被前面的 issue 判过（名字/配方/物品不认），别再补一条“没落地”，否则读的人分不清主因。
    flagged_names = {issue.get("name") for issue in issues}
    for missing in raw_job.get("missing") or []:
        if missing.get("name") in flagged_names:
            continue
        solo = missing.get("solo_ghosts")
        if solo == 0:
            reason = ("单独拿它在平地上也建不出鬼影 —— 它的放置依赖地形或相邻支撑"
                      "（如 offshore-pump 要岸边、mining-drill 要矿脉），是场地不匹配而不是蓝图写错")
        elif solo and solo > 0:
            reason = "它单独在平地上能建，说明是蓝图坐标处被挤掉或与其他实体冲突"
        else:
            reason = "游戏没有回报这个实体的放置结果（夹具探测失败）"
        report["errors"].append({
            "code": "missing_entity",
            "message": "%d 个 %s 既没建成也没留鬼影。%s" % (missing.get("count") or 0, missing.get("name"), reason),
            "name": missing.get("name"), "count": missing.get("count"),
        })

    if ghosts:
        blocked = [ghost for ghost in (raw_job.get("ghost_detail") or []) if not ghost.get("can_place")]
        hint = "；其中 %d 个在原位就放不下（地形或相邻冲突）" % len(blocked) if blocked else ""
        report["errors"].append({
            "code": "ghost_left",
            "message": "%d 个实体没建起来、只留了鬼影：%s%s" % (
                len(ghosts), ", ".join(report["ghost_names"]), hint),
        })

    if len(built_entities) != expected:
        report["errors"].append({
            "code": "count_mismatch",
            "message": "蓝图里有 %d 个实体，实际建出 %d 个" % (expected, len(built_entities)),
        })

    adjacency = adjacency_problems(job, raw_job)
    report["adjacency"] = adjacency
    production = production_summary(job, raw_job, run_seconds)
    report["production"] = production
    power = power_summary(job, raw_job)
    report["power"] = power
    pipes = pipes_summary(raw_job)
    report["pipes"] = pipes
    report["fluids"] = fluids_summary(job, raw_job)
    terrain = terrain_report(job, raw_job, terrain_mode)
    report["terrain"] = terrain if (terrain["checks"]["drills"] or terrain["checks"]["pumps"] or terrain_mode != "flat") else None
    production_errors = [problem for problem in (production or {}).get("problems") or [] if problem["severity"] == "error"]
    production_warnings = [problem for problem in (production or {}).get("problems") or [] if problem["severity"] == "warning"]
    terrain_errors = [problem for problem in (terrain or {}).get("problems") or [] if problem["severity"] == "error"]
    terrain_warnings = [problem for problem in (terrain or {}).get("problems") or [] if problem["severity"] == "warning"]
    # usable 只回答"结构性上能不能用"：取货位落空、带子断裂、地下件没配对、液口该接没接、
    # 地形不匹配。运行期的事（瞬时的没燃料、跑不出产量）一律归 production，别混进来——
    # 一炉 50 煤能烧 40 分钟，"等燃料"是正常状态，判红会让所有烧煤的图假失败。
    pipe_errors = [problem for problem in (pipes or {}).get("problems") or [] if problem["severity"] == "error"]
    pipe_warnings = [problem for problem in (pipes or {}).get("problems") or [] if problem["severity"] == "warning"]
    total_errors = (adjacency["counts"]["errors"] if adjacency else 0) + len(terrain_errors) + len(pipe_errors)
    total_warnings = (adjacency["counts"]["warnings"] if adjacency else 0) + len(terrain_warnings) + len(pipe_warnings)
    if adjacency or terrain_errors or terrain_warnings:
        first_message = None
        if terrain_errors:
            first_message = terrain_errors[0]["message"]
        elif pipe_errors:
            first_message = pipe_errors[0]["message"]
        elif adjacency and adjacency["problems"]:
            first_message = next((problem["message"] for problem in adjacency["problems"]
                                  if problem["severity"] == "error"), adjacency["problems"][0]["message"])
        elif terrain_warnings:
            first_message = terrain_warnings[0]["message"]
        report["usable"] = {"ok": total_errors == 0, "scope": "structural",
                            "errors": total_errors, "warnings": total_warnings,
                            "first": first_message}

    blocking = [error for error in report["errors"] if error["code"] != "ghost_left"]
    report["verdict"] = "pass" if (not blocking and len(built_entities) == expected and built_entities) else "fail"
    report["summary"] = _summary_text(report)
    return report


def _summary_text(report):
    if report["verdict"] == "pass":
        return "能建：%d/%d 个实体在真游戏里落地，0 鬼影" % (report["built_entities"], report["expected_entities"])
    if report["errors"]:
        return "不能建：%s（落地 %d/%d，鬼影 %d）" % (
            report["errors"][0]["message"], report["built_entities"], report["expected_entities"], report["ghosts"])
    return "建出 %d/%d 个实体" % (report["built_entities"], report["expected_entities"])


def load_specs(paths, expr):
    specs = []
    if expr is not None:
        specs.append({"id": "expr", "source": "--expr", "text": expr})
    for path in paths:
        specs.append({"id": os.path.basename(path), "source": path, "text": read_input(path)})
    return specs


def read_input(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def rig_version():
    """场景模板 + 运行器的源码哈希：调用方据此区分"工具正在被改"和"蓝图坏了"。"""
    digest = hashlib.sha1()
    for path in (SCENARIO_TEMPLATE, os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_rig.py"),
                 os.path.abspath(__file__)):
        try:
            with open(path, "rb") as handle:
                digest.update(handle.read())
        except OSError:
            digest.update(b"missing")
    return digest.hexdigest()[:12]


def run(specs, factorio, tick_limit, test_ticks, timeout, keep_dir, terrain="flat", run_seconds=0.0,
        powered=False, powered_single=False, feed_item="", fluids=False, fluid_name="",
        keep_locked=False, ports="", fluid_port="", supply_cells=""):
    jobs, failures = [], []
    for spec in specs:
        entry = {"id": spec["id"], "source": spec["source"]}
        try:
            kind, payload, encoded = decode_blueprint(spec["text"])
        except DecodeError as exc:
            failures.append({"id": spec["id"], "source": spec["source"], "code": "decode_error", "message": str(exc)})
            continue
        if kind != "blueprint":
            failures.append({"id": spec["id"], "source": spec["source"], "code": "unsupported_kind",
                             "message": "这是 %s，本工具只验单张 blueprint" % kind})
            continue
        entry.update({"kind": kind, "payload": payload, "blob": encoded})
        jobs.append(entry)

    base = {"decode_failures": failures, "game_env": None}
    if not jobs:
        return dict(base, ok=False, verdict="invalid", jobs=[], rig_error=None,
                    message="; ".join(failure["message"] for failure in failures) or "没有可送进游戏的蓝图")

    if keep_dir:
        os.makedirs(keep_dir, exist_ok=True)
    work_dir = tempfile.mkdtemp(prefix="bp-probe-", dir=keep_dir) if keep_dir else tempfile.mkdtemp(prefix="bp-probe-")
    try:
        run_ticks = int(round(run_seconds * 60)) if run_seconds else 0
        effective_tick_limit = run_ticks if run_ticks > tick_limit else tick_limit
        if run_ticks:
            test_ticks = max(test_ticks, run_ticks + 600)
        if powered or powered_single:
            powered = True
            effective_tick_limit = max(effective_tick_limit, 180)
            test_ticks = max(test_ticks, effective_tick_limit + 600)
        if fluids:
            effective_tick_limit = max(effective_tick_limit, 300)
            test_ticks = max(test_ticks, effective_tick_limit + 600)
        outcome = probe_rig.run_once(build_scenario(jobs, effective_tick_limit, terrain, run_ticks, powered, powered_single,
                                                 feed_item, fluids, fluid_name, not keep_locked, ports, fluid_port,
                                                 supply_cells),
                                  work_dir,
                                  factorio=factorio, test_ticks=test_ticks, timeout=timeout,
                                  keep=bool(keep_dir))
        raw_jobs = (outcome["raw"] or {}).get("jobs") or []
        env = (outcome["raw"] or {}).get("env") or {}
        reports = [job_report(job, raw_jobs[index] if index < len(raw_jobs) else None, terrain, run_seconds)
                   for index, job in enumerate(jobs)]
        for failure in failures:
            reports.append({"id": failure["id"], "source": failure["source"], "verdict": "invalid",
                            "errors": [{"code": failure["code"], "message": failure["message"]}],
                            "summary": failure["message"]})
        rig_error = None
        if outcome["raw"] is None:
            rig_error = "游戏没有产出结果文件（exit=%s）。stdout 尾：%s" % (
                outcome["exit_code"], outcome["stdout_tail"][-600:])
        verdicts = [report["verdict"] for report in reports]
        if verdicts and all(verdict == "pass" for verdict in verdicts):
            overall = "pass"
        elif verdicts and all(verdict == "invalid" for verdict in verdicts):
            overall = "invalid"
        else:
            overall = "fail"
        return {
            "ok": rig_error is None and overall != "invalid",
            "verdict": overall,
            "rig_error": rig_error,
            "elapsed_s": outcome["elapsed_s"],
            "timings": outcome["timings"],
            "command": outcome["command"],
            "game_env": {
                "active_mods": env.get("active_mods"),
                "feature_flags": env.get("feature_flags"),
                "dlc_prototypes": env.get("prototypes"),
                "players": env.get("players"),
            },
            "terrain": terrain,
            "recipes_unlocked": not keep_locked,
            "rig_version": rig_version(),
            "jobs": reports,
            "decode_failures": failures,
            "log_path": outcome["log_path"],
            "result_path": outcome["result_path"],
            "run_dir": work_dir,
            "message": "; ".join(report["summary"] for report in reports) if reports else "无结果",
        }
    finally:
        if not keep_dir:
            shutil.rmtree(work_dir, ignore_errors=True)



def check(blueprints, factorio=None, ticks=60, test_ticks=1200, timeout=180.0, keep_dir=None, terrain="flat",
          run_seconds=0.0, powered=False, powered_single=False, feed_item="", fluids=False, fluid_name="",
          keep_locked=False, ports="", fluid_port="", supply_cells=""):
    """库/MCP 入口：吃蓝图字符串或文件路径（单个或列表），直接回结果 dict，不走 stdout。

    blueprints 可以是：
      - 蓝图字符串（"0eJw…" 或未压缩 JSON 文本）
      - 文件路径（.txt / .json）
      - 上面两者的列表
      - 带 id 的 dict：{"id": "page1", "text": "0eJw…"} 或 {"id": "page1", "path": "…"}
    返回的就是 --json 那套字段，verdict / jobs[].adjacency / jobs[].usable 都在里面。
    """
    specs = normalize_specs(blueprints)
    return run(specs, factorio or probe_rig.FACTORIO_DEFAULT, ticks, test_ticks, timeout, keep_dir, terrain,
               run_seconds, powered, powered_single, feed_item, fluids, fluid_name, keep_locked, ports, fluid_port,
               supply_cells)


def normalize_specs(blueprints):
    if isinstance(blueprints, (str, dict)):
        blueprints = [blueprints]
    specs = []
    for index, item in enumerate(blueprints):
        if isinstance(item, dict):
            identifier = item.get("id") or ("item%d" % index)
            if item.get("path"):
                specs.append({"id": identifier, "source": item["path"], "text": read_input(item["path"])})
            else:
                specs.append({"id": identifier, "source": "<memory>", "text": item.get("text", "")})
            continue
        text = item.strip()
        if "\n" not in text and len(text) < 400 and os.path.isfile(text):
            specs.append({"id": os.path.basename(text), "source": text, "text": read_input(text)})
        else:
            specs.append({"id": "blueprint%d" % index, "source": "<memory>", "text": item})
    return specs


def main(argv=None):
    parser = argparse.ArgumentParser(description="把蓝图送进真 Factorio 验证能不能建")
    parser.add_argument("blueprints", nargs="*", help="蓝图文件（压缩字符串或未压缩 JSON）；- 读 stdin")
    parser.add_argument("--expr", help="直接给蓝图字符串")
    parser.add_argument("--factorio", default=probe_rig.FACTORIO_DEFAULT, help="factorio.exe 路径")
    parser.add_argument("--json", help="把完整结果写到这个文件")
    parser.add_argument("--keep", metavar="DIR", help="保留临时目录（建在 DIR 下），便于取证")
    parser.add_argument("--ticks", type=int, default=60, help="建完后跑多少 tick 再采样（默认 60）")
    parser.add_argument("--test-ticks", type=int, default=1200, help="兜底退出 tick（默认 1200）")
    parser.add_argument("--timeout", type=float, default=180.0, help="进程超时秒数")
    parser.add_argument("--terrain", choices=TERRAIN_MODES, default="flat",
                        help="测试场地：flat 平地（默认）/ ore 铺矿 / water 水塘")
    parser.add_argument("--powered-single", action="store_true",
                        help="只给第一根电塔接一台发电机，模拟玩家只拉了一条电线")
    parser.add_argument("--powered", action="store_true",
                        help="给电网接上发电机后逐实体读状态，回答供电覆盖全不全")
    parser.add_argument("--ports", default="",
                        help="模板声明的输出口（蓝图坐标，形如 \"10,14;12,14\"）：只清这些格子上的带子")
    parser.add_argument("--external-supply", default="",
                        help="模板声明的外部供给 JSON（{\"external_supply\":[{cell,item,kind}]}）：流体名和口位都从它读")
    parser.add_argument("--fluid-port", default="",
                        help="模板声明的流体口（蓝图坐标，形如 \"1,0\"）：--fluids 的源优先接到它最近的管网上")
    parser.add_argument("--keep-locked", action="store_true",
                        help="不解除配方锁定（默认会解锁全部非隐藏配方，让组装机/化工线能跑）")
    parser.add_argument("--fluids", action="store_true",
                        help="往管网里放 infinity-pipe 灌流体，跑几秒后读目标里有没有（默认关）")
    parser.add_argument("--fluid", default="",
                        help="--fluids 灌什么流体；不给就按机器配方的液体原料自动判断")
    parser.add_argument("--feed-item", default="",
                        help="夹具往输入带灌什么物品（不传则按蓝图声明的配方推原料；推不出就不喂）")
    parser.add_argument("--run-seconds", type=float, default=0.0,
                        help="L3：建完接上夹具跑多少秒，数真实产量（默认 0 = 不跑）")
    parser.add_argument("--text", action="store_true", help="打印人读摘要而不是 JSON")
    args = parser.parse_args(argv)

    if getattr(args, "external_supply", ""):
        try:
            with open(args.external_supply, encoding="utf-8") as handle:
                declared = json.load(handle)
        except (OSError, ValueError) as failure:
            print("读不了 --external-supply：%s" % failure, file=sys.stderr)
            return 1
        entries = declared.get("external_supply", []) if isinstance(declared, dict) else declared
        fluids = [e for e in entries if isinstance(e, dict) and e.get("kind") == "fluid"]
        if not fluids:
            print("--external-supply 里没有 kind=fluid 的条目", file=sys.stderr)
            return 1
        if not args.fluid:
            args.fluid = fluids[0].get("item") or ""
        supplies = [e for e in entries if isinstance(e, dict) and e.get("kind") == "item" and e.get("cell")]
        if supplies:
            args.supply_cells = ";".join("%s,%s:%s" % (e["cell"][0], e["cell"][1], e.get("item") or "") for e in supplies)
        if not args.fluid_port and fluids[0].get("cell"):
            args.fluid_port = "%s,%s" % (fluids[0]["cell"][0], fluids[0]["cell"][1])
    if args.fluids and not args.fluid:
        args.fluids = False
        print("警告：--fluids 没给流体名，也没给 --external-supply，这次不灌流体（避免把管网灌成水）", file=sys.stderr)

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    specs = load_specs(args.blueprints, args.expr)
    if not specs:
        parser.error("至少给一个蓝图文件或 --expr")

    result = run(specs, args.factorio, args.ticks, args.test_ticks, args.timeout, args.keep, args.terrain,
                 args.run_seconds, args.powered, args.powered_single, args.feed_item, args.fluids, args.fluid,
                 args.keep_locked, args.ports, args.fluid_port, getattr(args, "supply_cells", ""))
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            handle.write(payload)
    if args.text:
        print(result["message"])
        for job in result["jobs"]:
            print("--- %s [%s]" % (job["id"], job.get("verdict")))
            for error in job.get("errors") or []:
                print("    - " + error["message"])
            fluids = job.get("fluids") or {}
            if fluids:
                source = fluids.get("source") or {}
                print("    流体：源=%s @%s｜灌 %s｜目标里读到 %s 个单位｜机器 %d/%d 有液体"
                      % ("infinity-pipe" if source.get("placed") else "未放置",
                         _fmt_pos(source.get("at")), source.get("fluid"),
                         fluids["total_fluid"], fluids["machines"]["filled"], fluids["machines"]["count"]))
                for target in fluids["targets"][:6]:
                    if target["amount"]:
                        print("      %s@%s → %s" % (target["name"], _fmt_pos(target["at"]), target["contents"]))
                for problem in fluids["problems"]:
                    print("      [%s] %s" % (problem["severity"], problem["message"]))
            pipes = job.get("pipes") or {}
            if pipes:
                print("    管线：%d 个管道实体 / %d 个管段；地下管没配对 %d；液口没接 %d"
                      % (pipes["pipes"], pipes["segment_count"], len(pipes["unpaired_underground"]),
                         len(pipes["unconnected_machines"])))
                for problem in pipes["problems"][:10]:
                    print("      [%s] %s" % (problem["severity"], problem["message"]))
            power = job.get("power") or {}
            if power:
                print("    供电覆盖：%d 根电塔 / %d 张电网 / 发电机 %d 台 / 未覆盖 %d 个%s"
                      % (power["poles"], len(power["networks"]), power["generators"] or 0,
                         power["unpowered_count"], "" if power["single_network"] else "（电塔分成多张网！）"))
                for item in power["unpowered"][:20]:
                    print("      [error] %s@%s 没通电（最近电塔 %s 格）"
                          % (item["name"], _fmt_pos(item["at"]), item["nearest_pole_distance"]))
            production = job.get("production") or {}
            if production:
                print("    产量（跑 %ss）：产出 %s 个，理论 %s 个，达成率 %s；夹具 feeds=%s sinks=%s 太阳能板=%s 已并网=%s"
                      % (production["run_seconds"], production["produced"], production["theoretical"],
                         ("%.0f%%" % (production["achievement"] * 100)) if production["achievement"] is not None else "n/a",
                         production["fixtures"].get("feeds_count", len(production["fixtures"].get("feeds") or [])),
                         production["fixtures"].get("sinks_count", len(production["fixtures"].get("sinks") or [])),
                         production["fixtures"].get("generators"), production["fixtures"].get("generator_kind")))
                print("    运行结论：%s" % ("跑通" if production.get("ok") else "没跑通"))
                scope = production.get("drained_scope")
                if scope:
                    print("      drained 覆盖范围：%s，%d 条带子 %s"
                          % (scope.get("mode"), len(scope.get("belts") or []),
                             [_fmt_pos(b) for b in (scope.get("belts") or [])[:6]]))
                for problem in production["problems"]:
                    print("    [%s] %s" % (problem["severity"], problem["message"]))
            terrain = job.get("terrain") or {}
            if terrain.get("counts") and (terrain["checks"]["drills"] or terrain["checks"]["pumps"]):
                print("    地形检查（%s）：矿钻 %d/%d 挖到矿，水泵 %d/%d 吸到水"
                      % (terrain.get("mode"), terrain["counts"]["drills_ok"], terrain["counts"]["drills"],
                         terrain["counts"]["pumps_ok"], terrain["counts"]["pumps"]))
                for drill in terrain["checks"]["drills"]:
                    print("      - %s@%s → mining_target=%s（mining_area 内矿 %s）"
                          % (drill["name"], _fmt_pos(drill["at"]), drill["mining_target"], drill["resource_names"]))
                for pump in terrain["checks"]["pumps"]:
                    print("      - %s@%s → source_tile=%s，source_fluid=%s"
                          % (pump["name"], _fmt_pos(pump["at"]), _fmt_pos(pump["source_tile"]), pump["source_fluid"]))
            adjacency = job.get("adjacency") or {}
            if adjacency.get("counts"):
                print("    邻接检查：%d 个问题（%d 错误 / %d 警告），共看 %d 个实体"
                      % (adjacency["counts"]["problems"], adjacency["counts"]["errors"],
                         adjacency["counts"]["warnings"], adjacency["checked"]["entities"]))
            for problem in adjacency.get("problems") or []:
                print("    [%s] %s" % (problem["severity"], problem["message"]))
        if result.get("rig_error"):
            print("夹具错误：" + result["rig_error"])
    else:
        print(payload)
    return 0 if result["verdict"] == "pass" else 2 if result["verdict"] == "fail" else 1


if __name__ == "__main__":
    sys.exit(main())
