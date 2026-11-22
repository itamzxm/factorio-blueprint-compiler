"""静态 lint：毫秒级、离线、结构化缺陷列表。

每条缺陷都带 code / severity / cells / entities / message / fix。
severity 三档：error（游戏里一定错）、warning（几乎肯定是 bug）、note（信息，可能是设计意图）。

direction 相关的一条硬规矩：**绝不因为 direction 的值判实体非法**。真实蓝图里
assembling-machine-3 就在用 direction=12，而真机探针把它的可旋转档数读成 0。
按 dirs 判非法会误杀真蓝图，误报会让人不再信任这份报错。
"""

import collections

from . import geometry, protos

ERROR = "error"
WARNING = "warning"
NOTE = "note"

_SEVERITY_ORDER = {ERROR: 0, WARNING: 1, NOTE: 2}

# 能装物品的实体类型。插入/取出判定用；不在表里的类型只发 warning，不硬判。
_ITEM_HOLDERS = {
    "container",
    "logistic-container",
    "furnace",
    "assembling-machine",
    "mining-drill",
    "lab",
    "boiler",
    "reactor",
    "rocket-silo",
    "ammo-turret",
    "artillery-turret",
    "car",
    "cargo-wagon",
    "locomotive",
    "spider-vehicle",
    "infinity-container",
    "linked-container",
}

_BELT_TYPES = {"transport-belt", "underground-belt", "splitter", "loader", "linked-belt"}

_RAIL_TYPES = {
    "rail-signal",
    "rail-chain-signal",
    "rail-support",
    "straight-rail",
    "curved-rail-a",
    "curved-rail-b",
    "half-diagonal-rail",
    "elevated-straight-rail",
    "elevated-curved-rail-a",
    "elevated-curved-rail-b",
    "elevated-half-diagonal-rail",
    "legacy-straight-rail",
    "legacy-curved-rail",
    "rail-ramp",
    "train-stop",
    "locomotive",
    "cargo-wagon",
    "fluid-wagon",
    "artillery-wagon",
}

_NO_ITEM_TRANSFER = {"pipe", "pipe-to-ground", "electric-pole", "wall", "gate", "lamp", "radar"}


def _defect(code, severity, message, fix, cells=(), entities=(), **extra):
    payload = {
        "code": code,
        "severity": severity,
        "message": message,
        "fix": fix,
        "cells": [[cell[0], cell[1]] for cell in cells],
        "entities": [number for number in entities if number is not None],
    }
    payload.update(extra)
    return payload


def _describe(entity):
    position = entity["position"]
    return "%s#%s@%s" % (
        entity["name"],
        entity.get("entity_number"),
        geometry.format_position((position["x"], position["y"])),
    )


# ---------------------------------------------------------------- 单条检查


def check_entity_names(blueprint):
    defects = []
    for entity in blueprint.entities:
        if protos.find(entity["name"]) is None:
            defects.append(
                _defect(
                    "unknown-entity",
                    ERROR,
                    "实体名 %r 不在原版原型表里（蓝图里的一格成不了实体）" % entity["name"],
                    "换成原版名字；mod 实体不在支持范围（只收 base/space-age/quality/elevated-rails/recycler）",
                    entities=[entity.get("entity_number")],
                )
            )
    return defects


def check_overlap(blueprint):
    """两实体共格。

    轨道类整个跳过：它们的碰撞盒跟曲线不是一回事（直轨/弯轨/高架轨/轨撑可以合法重叠，
    见 doc/蓝图格式与坑.md 第六节），拿矩形算必然误报。
    """
    defects = []
    pairs = {}
    for cell, bucket in blueprint._cells.items():
        if len(bucket) < 2:
            continue
        bucket = [
            e for e in bucket if (protos.find(e["name"]) or {}).get("type") not in _RAIL_TYPES
        ]
        if len(bucket) < 2:
            continue
        for index, first in enumerate(bucket):
            for second in bucket[index + 1 :]:
                key = tuple(
                    sorted((first.get("entity_number", 0), second.get("entity_number", 0)))
                )
                entry = pairs.setdefault(key, {"first": first, "second": second, "cells": []})
                entry["cells"].append(cell)
    for (_, _), entry in sorted(pairs.items()):
        first, second = entry["first"], entry["second"]
        cells = sorted(entry["cells"])
        defects.append(
            _defect(
                "overlap",
                ERROR,
                "%s 和 %s 都占了 %s（共 %d 格）"
                % (
                    _describe(first),
                    _describe(second),
                    geometry.format_cell(cells[0]),
                    len(cells),
                ),
                "两个实体不能共格。挪走其中一个，或用地下带/地下管从下面穿过去",
                cells=cells,
                entities=[first.get("entity_number"), second.get("entity_number")],
            )
        )
    return defects


def check_bounds(blueprint):
    if not blueprint.build_area:
        return []
    min_i, min_j, max_i, max_j = blueprint.build_area
    area = "(%d,%d)-(%d,%d)" % (min_i, min_j, max_i, max_j)
    defects = []
    for entity in blueprint.entities:
        outside = [
            cell
            for cell in blueprint.cells_of(entity)
            if not (min_i <= cell[0] <= max_i and min_j <= cell[1] <= max_j)
        ]
        if outside:
            defects.append(
                _defect(
                    "out-of-bounds",
                    ERROR,
                    "%s 有 %d 格落在声明占地 %s 之外，第一格是 %s"
                    % (_describe(entity), len(outside), area, geometry.format_cell(sorted(outside)[0])),
                    "把它挪回 %s 以内，或放宽声明占地" % area,
                    cells=sorted(outside)[:6],
                    entities=[entity.get("entity_number")],
                )
            )
    return defects


def check_parity(blueprint):
    """1 格实体必须落 .5，2 格实体必须落整数；轨道类不守这条规矩，跳过。"""
    defects = []
    for entity in blueprint.entities:
        proto = protos.find(entity["name"])
        if proto is None or proto["type"] in _RAIL_TYPES:
            continue
        position = entity["position"]
        direction = entity.get("direction", 0)
        if not geometry.is_snapped(position["x"], position["y"], proto["w"], proto["h"], direction):
            wanted = geometry.snapped_center(
                position["x"], position["y"], proto["w"], proto["h"], direction
            )
            defects.append(
                _defect(
                    "bad-parity",
                    WARNING,
                    "%s 是 %dx%d，中心本应落在 %s，现在在 %s，实际占地会多算一格"
                    % (
                        _describe(entity),
                        proto["w"],
                        proto["h"],
                        geometry.format_position(wanted),
                        geometry.format_position((position["x"], position["y"])),
                    ),
                    "把 position 改成 %s（或调 blueprint.normalize() 自动修）"
                    % geometry.format_position(wanted),
                    entities=[entity.get("entity_number")],
                )
            )
    return defects


def check_directions(blueprint):
    defects = []
    for entity in blueprint.entities:
        if "direction" not in entity:
            continue
        proto = protos.find(entity["name"])
        if proto is None or proto["type"] in _RAIL_TYPES:
            continue
        direction = entity["direction"]
        accept = protos.accept_dirs(entity["name"])
        if direction in accept:
            continue
        if direction in range(16) and direction % 4:
            defects.append(
                _defect(
                    "direction-not-quarter",
                    WARNING,
                    "%s 的 direction=%d 不是 4 的倍数；普通实体只用 0/4/8/12"
                    % (_describe(entity), direction),
                    "改成 0/4/8/12 里最近的一档",
                    entities=[entity.get("entity_number")],
                )
            )
        else:
            defects.append(
                _defect(
                    "direction-out-of-range",
                    WARNING,
                    "%s 的 direction=%r 超出 0-15" % (_describe(entity), direction),
                    "改成 0/4/8/12",
                    entities=[entity.get("entity_number")],
                )
            )
    return defects


def check_underground_belts(blueprint):
    """入口找出口：同一条线上、朝向相同、跨度不超过 max_distance。"""
    defects = []
    entries = [e for e in blueprint.entities if e["name"] and protos.is_underground(e["name"])]
    entries = [e for e in entries if (protos.find(e["name"]) or {}).get("type") == "underground-belt"]
    by_cell = {}
    for entity in entries:
        proto = protos.find(entity["name"]) or {}
        if proto.get("type") != "underground-belt":
            continue
        for cell in blueprint.cells_of(entity):
            by_cell[cell] = entity

    paired = set()
    for entity in entries:
        role = entity.get("type")
        if role not in ("input", "output"):
            defects.append(
                _defect(
                    "underground-belt-missing-role",
                    ERROR,
                    "%s 没有 type=input/output 字段，游戏会把它当成坏掉的地下带" % _describe(entity),
                    '入口补 "type": "input"，出口补 "type": "output"',
                    entities=[entity.get("entity_number")],
                )
            )
            continue
        if role != "input":
            continue
        cells = sorted(blueprint.cells_of(entity))
        if not cells:
            continue
        head = cells[0]
        direction = entity.get("direction", 0)
        span = protos.underground_span(entity["name"]) or 0
        # 真实蓝图里存在"输入和输出不同档位"的相邻地下带，游戏那边能不能认这一对没实测过，
        # 所以不同档位只发 note，不当错误——误报比漏报毒。
        candidates = []
        for distance in range(1, span + 1):
            probe = geometry.offset(head, direction, distance)
            for other in blueprint.at(probe):
                proto_other = protos.find(other["name"]) or {}
                if proto_other.get("type") != "underground-belt" or other is entity:
                    continue
                if other.get("type") != "output":
                    continue
                candidates.append((other, distance))
        same_name = [c for c in candidates if c[0]["name"] == entity["name"]]
        matching = [c for c in same_name if c[0].get("direction", 0) == direction]
        found = matching[0] if matching else (same_name[0] if same_name else None)
        if found is None and candidates:
            other, distance = candidates[0]
            defects.append(
                _defect(
                    "underground-belt-tier-mix",
                    NOTE,
                    "%s 的东家出口是另一档位的 %s（相距 %d 格），两档能不能连上没实测过"
                    % (_describe(entity), _describe(other), distance),
                    "同档位配对更稳；确认过能连就忽略这条",
                    cells=[head],
                    entities=[entity.get("entity_number"), other.get("entity_number")],
                )
            )
            paired.add(id(entity))
            paired.add(id(other))
            continue
        if found is None:
            # 跨度超限是硬错：出口确实存在，只是太远，游戏连不上。
            far = None
            for distance in range(span + 1, span * 2 + 2):
                for other in blueprint.at(geometry.offset(head, direction, distance)):
                    if (
                        other["name"] == entity["name"]
                        and other.get("type") == "output"
                        and other.get("direction", 0) == direction
                    ):
                        far = (other, distance)
                        break
                if far:
                    break
            if far:
                defects.append(
                    _defect(
                        "underground-belt-span",
                        ERROR,
                        "%s 到出口 %s 相距 %d 格，超过 %s 的最大跨度 %d 格，中间这段是断的"
                        % (
                            _describe(entity),
                            _describe(far[0]),
                            far[1],
                            entity["name"],
                            span,
                        ),
                        "把出口挪到 %d 格以内，或中间插一段明带再接下一个地下段" % span,
                        cells=[head, geometry.offset(head, direction, far[1])],
                        entities=[entity.get("entity_number"), far[0].get("entity_number")],
                    )
                )
                paired.add(id(entity))
                paired.add(id(far[0]))
                continue
            defects.append(
                _defect(
                    "underground-belt-unpaired",
                    WARNING,
                    "%s 朝 %s 找 %d 格内没有同型号的地下带出口，地下段是个死胡同"
                    % (_describe(entity), geometry.DIRECTION_NAMES[direction], span),
                    "在 %s 方向上 %d 格内补一个 type=output 的同型号地下带，朝向相同；"
                    "如果这是可平铺模块留的接缝，可以忽略"
                    % (geometry.DIRECTION_NAMES[direction], span),
                    cells=[head],
                    entities=[entity.get("entity_number")],
                )
            )
            continue
        other, distance = found
        paired.add(id(entity))
        paired.add(id(other))
        if other.get("direction", 0) != direction:
            defects.append(
                _defect(
                    "underground-belt-direction",
                    ERROR,
                    "%s 和出口 %s 朝向不一致（%s vs %s），这一对连不上"
                    % (
                        _describe(entity),
                        _describe(other),
                        geometry.DIRECTION_NAMES[direction],
                        geometry.DIRECTION_NAMES[other.get("direction", 0)],
                    ),
                    "出口的 direction 改成 %d，跟入口一致" % direction,
                    cells=[head, geometry.offset(head, direction, distance)],
                    entities=[entity.get("entity_number"), other.get("entity_number")],
                )
            )
    for entity in entries:
        if entity.get("type") == "output" and id(entity) not in paired:
            cells = sorted(blueprint.cells_of(entity))
            defects.append(
                _defect(
                    "underground-belt-unpaired",
                    WARNING,
                    "%s 没有配对的入口（朝 %s 反方向 %d 格内找不到 type=input）"
                    % (
                        _describe(entity),
                        geometry.DIRECTION_NAMES[entity.get("direction", 0)],
                        protos.underground_span(entity["name"]) or 0,
                    ),
                    "在它 %s 反方向补一个 type=input 的同型号地下带；"
                    "如果这是可平铺模块留的接缝，可以忽略"
                    % geometry.DIRECTION_NAMES[entity.get("direction", 0)],
                    cells=cells[:1],
                    entities=[entity.get("entity_number")],
                )
            )
    return defects


def check_underground_pipes(blueprint):
    """地下管配对。

    口径来自 data-raw-dump：pipe-to-ground 的 fluid_box.pipe_connections 是
    [{direction:0}] + [{connection_type:"underground", direction:8, max:10}]，
    套上实体旋转后，**管口朝向 direction，地下段朝 direction+8**。
    所以一对连通的地下管是「A 朝 D，B 在 A 的 D+8 方向上、B 朝 D+8」，两口对外。
    这条只在真实蓝图上验过、没在游戏里单独验过，因此判 warning 不判 error。
    """
    pipes = [
        e
        for e in blueprint.entities
        if (protos.find(e["name"]) or {}).get("type") == "pipe-to-ground"
    ]
    if not pipes:
        return []
    by_cell = {}
    for entity in pipes:
        for cell in blueprint.cells_of(entity):
            by_cell[cell] = entity
    matched = set()
    defects = []
    for entity in pipes:
        cells = sorted(blueprint.cells_of(entity))
        if not cells or id(entity) in matched:
            continue
        head = cells[0]
        direction = entity.get("direction", 0)
        tunnel = geometry.opposite(direction)
        span = protos.underground_span(entity["name"]) or 0
        partner = None
        for distance in range(1, span + 1):
            other = by_cell.get(geometry.offset(head, tunnel, distance))
            if other is None or other is entity:
                continue
            if other.get("direction", 0) == tunnel:
                partner = other
                break
        if partner is None:
            defects.append(
                _defect(
                    "underground-pipe-unpaired",
                    WARNING,
                    "%s 朝 %s 的地下管没有配对：%d 格内该方向上找不到朝 %s 的 pipe-to-ground"
                    % (
                        _describe(entity),
                        geometry.DIRECTION_NAMES[direction],
                        span,
                        geometry.DIRECTION_NAMES[tunnel],
                    ),
                    "在 %s 方向 %d 格内放一个 direction=%d 的 pipe-to-ground；或确认这里就是终点"
                    % (geometry.DIRECTION_NAMES[tunnel], span, tunnel),
                    cells=[head],
                    entities=[entity.get("entity_number")],
                )
            )
        else:
            matched.add(id(entity))
            matched.add(id(partner))
    return defects


def _inserter_take_cells(blueprint):
    """所有臂的取货格——带子停在这些格上是正常终点，不是断头。"""
    out = set()
    for entity in blueprint.entities:
        proto = protos.find(entity["name"])
        if proto is None or proto["type"] != "inserter":
            continue
        position = entity["position"]
        reach = proto.get("inserter") or {}
        pickup = reach.get("pickup") or [0, -1]
        offset = geometry.rotate_point(pickup[0], pickup[1], entity.get("direction", 0))
        out.add(geometry.cell_of(position["x"] + offset[0], position["y"] + offset[1]))
    return out


def _inserter_takes_from(entity, cell):
    """这只臂的取货格是不是 cell。带子怼到一只正在取它的臂上不算断头。"""
    proto = protos.find(entity["name"])
    if proto is None or proto["type"] != "inserter":
        return False
    position = entity["position"]
    reach = proto.get("inserter") or {}
    pickup = reach.get("pickup") or [0, -1]
    offset = geometry.rotate_point(pickup[0], pickup[1], entity.get("direction", 0))
    return geometry.cell_of(position["x"] + offset[0], position["y"] + offset[1]) == cell


def check_belts(blueprint):
    """断头带 / 对撞带 / 带直接怼非带实体。"""
    defects = []
    belts = [e for e in blueprint.entities if (protos.find(e["name"]) or {}).get("type") == "transport-belt"]
    belt_cells = {}
    for entity in belts:
        for cell in blueprint.cells_of(entity):
            belt_cells[cell] = entity
    taken = _inserter_take_cells(blueprint)
    ports = set(getattr(blueprint, "ports", {}) or {})
    for entity in belts:
        cells = sorted(blueprint.cells_of(entity))
        if not cells:
            continue
        head = cells[0]
        direction = entity.get("direction", 0)
        ahead = geometry.offset(head, direction, 1)
        neighbours = blueprint.at(ahead)
        if not neighbours:
            if head in taken or ahead in ports or head in ports:
                continue
            defects.append(
                _defect(
                    "belt-tail",
                    NOTE,
                    "%s 的下游 %s 是空的，物品会在这格堆住"
                    % (_describe(entity), geometry.format_cell(ahead)),
                    "如果是取料终点就不用管；如果该继续走，在这里补带或机器",
                    cells=[ahead],
                    entities=[entity.get("entity_number")],
                )
            )
            continue
        takers = [n for n in neighbours if _inserter_takes_from(n, head)]
        if takers:
            continue
        kinds = [(protos.find(n["name"]) or {}).get("type") for n in neighbours]
        if any(kind in _BELT_TYPES for kind in kinds):
            facing = [
                n
                for n, kind in zip(neighbours, kinds)
                if kind in _BELT_TYPES and n.get("direction", 0) == geometry.opposite(direction)
            ]
            if facing:
                defects.append(
                    _defect(
                        "belt-head-on",
                        WARNING,
                        "%s 正对着 %s 送料，两条带方向相反，接缝处会互相顶死"
                        % (_describe(entity), _describe(facing[0])),
                        "把下游那条带的方向转过来，或中间改成侧向汇入",
                        cells=[head, ahead],
                        entities=[entity.get("entity_number"), facing[0].get("entity_number")],
                    )
                )
            continue
        defects.append(
            _defect(
                "belt-into-nonbelt",
                WARNING,
                "%s 的下游 %s 是 %s；带不会把物品交进机器，需要物流臂去取"
                % (
                    _describe(entity),
                    geometry.format_cell(ahead),
                    _describe(neighbours[0]),
                ),
                "在这格放物流臂朝 %s（取货侧朝带），或者让带继续往前铺"
                % geometry.DIRECTION_NAMES[geometry.opposite(direction)],
                cells=[ahead],
                entities=[entity.get("entity_number")],
            )
        )
    return defects


def _item_capability(name):
    """True=能装物品 / False=明显不能 / None=认不出。"""
    proto = protos.find(name)
    if proto is None:
        return None
    if proto["type"] in _BELT_TYPES:
        return True
    if proto["type"] in _ITEM_HOLDERS:
        return True
    if proto["type"] in _NO_ITEM_TRANSFER:
        return False
    return None


def check_inserters(blueprint):
    defects = []
    bounds = blueprint.bounds()
    for entity in blueprint.entities:
        proto = protos.find(entity["name"])
        if proto is None or proto["type"] != "inserter":
            continue
        position = entity["position"]
        direction = entity.get("direction", 0)
        reach = proto.get("inserter") or {}
        pickup_vector = reach.get("pickup") or [0, -1]
        insert_vector = reach.get("insert") or [0, 1.2]
        pickup_point = geometry.rotate_point(pickup_vector[0], pickup_vector[1], direction)
        insert_point = geometry.rotate_point(insert_vector[0], insert_vector[1], direction)
        pickup_cell = geometry.cell_of(position["x"] + pickup_point[0], position["y"] + pickup_point[1])
        insert_cell = geometry.cell_of(position["x"] + insert_point[0], position["y"] + insert_point[1])
        for role, cell, word in (
            ("pickup", pickup_cell, "取货"),
            ("insert", insert_cell, "放货"),
        ):
            occupants = blueprint.at(cell)
            # 大蓝图在包围盒边上伸出去的臂，多半是截边截掉的（真实蓝图里确实有），降成 warning；
            # 小蓝图没有这个豁免——一张三实体的图里臂对着空气就是错。
            outside = (
                bounds is not None
                and len(blueprint.entities) >= 20
                and not (bounds[0] <= cell[0] <= bounds[2] and bounds[1] <= cell[1] <= bounds[3])
            )
            if not occupants:
                defects.append(
                    _defect(
                        "inserter-no-%s" % role,
                        WARNING if outside else ERROR,
                        "%s 的%s侧 %s 是空的（direction=%d 指向取货侧，不是放货侧）%s"
                        % (
                            _describe(entity),
                            word,
                            geometry.format_cell(cell),
                            direction,
                            "；这格已经出了蓝图包围盒，多半是截边截掉的" if outside else "",
                        ),
                        "把臂放到货和机器中间，或让 direction 指向真正有东西的那一侧",
                        cells=[cell],
                        entities=[entity.get("entity_number")],
                    )
                )
                continue
            if any(_item_capability(o["name"]) for o in occupants):
                continue
            unknown = [o for o in occupants if _item_capability(o["name"]) is None]
            if unknown:
                defects.append(
                    _defect(
                        "inserter-%s-unverified" % role,
                        WARNING,
                        "%s 的%s侧 %s 上是 %s，认不出它能不能接/给物品"
                        % (
                            _describe(entity),
                            word,
                            geometry.format_cell(cell),
                            _describe(unknown[0]),
                        ),
                        "人工确认一下这个实体有没有物品栏",
                        cells=[cell],
                        entities=[entity.get("entity_number")],
                    )
                )
            else:
                defects.append(
                    _defect(
                        "inserter-no-%s" % role,
                        ERROR,
                        "%s 的%s侧 %s 上是 %s，它不装物品，臂会空转"
                        % (
                            _describe(entity),
                            word,
                            geometry.format_cell(cell),
                            _describe(occupants[0]),
                        ),
                        "把臂挪到能取到货的一侧，或换成有物品栏的目标",
                        cells=[cell],
                        entities=[entity.get("entity_number")],
                    )
                )
    return defects


def check_power(blueprint):
    """电线杆供电覆盖 + 电线连通。没有任何电杆时不报覆盖（蓝图本来可能只画了产线）。"""
    poles = [
        e for e in blueprint.entities if (protos.find(e["name"]) or {}).get("type") == "electric-pole"
    ]
    electric = [e for e in blueprint.entities if protos.needs_power(e["name"])]
    if not electric:
        return []
    if not poles:
        return [
            _defect(
                "power-missing",
                WARNING,
                "这张蓝图有 %d 个要电的实体，但一根电线杆都没有" % len(electric),
                "加一排 %s，或接现成的电网" % ("medium-electric-pole"),
                entities=[e.get("entity_number") for e in electric[:10]],
            )
        ]
    defects = []
    areas = []
    for pole in poles:
        supply = protos.pole_supply(pole["name"]) or 0
        position = pole["position"]
        areas.append((position["x"], position["y"], supply))
    for entity in electric:
        position = entity["position"]
        cells = sorted(blueprint.cells_of(entity))
        # 游戏判供电看碰撞盒与供电区有没有重叠，所以只要有一格落在供电区里就算通电。
        covered = any(
            abs(cell[0] + 0.5 - px) <= supply and abs(cell[1] + 0.5 - py) <= supply
            for cell in cells
            for px, py, supply in areas
        )
        if covered:
            continue
        defects.append(
            _defect(
                "power-uncovered",
                ERROR,
                "%s 整个都落在电线杆的供电范围外（%d 根杆，最大供电 %.1f 格）"
                % (
                    _describe(entity),
                    len(poles),
                    max((supply for _, _, supply in areas), default=0),
                ),
                "在它旁边加一根电线杆，或换供电范围更大的型号",
                cells=cells[:1],
                entities=[entity.get("entity_number")],
            )
        )
    return defects


def check_power_network(blueprint):
    """电网连通性——**只看蓝图里真的写下来的铜线**，不许用距离推。

    三次同类失败的第三次：① direction 只有写了 recipe 才生效 ② 地下管配对方向是反的
    ③ 电塔不写 wires 就是各自为政。共同点都是"几何算得出、字段里没写"。
    距离近不等于连着；游戏只认 blueprint.wires 里 [A, 5, B, 5] 这种条目。
    """
    poles = [
        e for e in blueprint.entities if (protos.find(e["name"]) or {}).get("type") == "electric-pole"
    ]
    if len(poles) < 2:
        return []
    by_number = {e.get("entity_number"): e for e in poles}
    links = [(a, b) for a, b in blueprint.copper_wires() if a in by_number and b in by_number]
    if not links:
        cells = []
        for pole in poles[:6]:
            found = sorted(blueprint.cells_of(pole))
            if found:
                cells.append(found[0])
        return [
            _defect(
                "power-network-split",
                ERROR,
                "有 %d 根电线杆但蓝图里一条铜线都没写。游戏会建出 %d 张互不相连的电网——"
                "接一根线只有一根塔有电，其余塔下的机器全是 no_power"
                % (len(poles), len(poles)),
                "生成时调 primitives.connect_poles()；或手工给每对线距内的塔写 [A,5,B,5] 到 blueprint.wires",
                cells=cells,
                entities=[p.get("entity_number") for p in poles[:6]],
            )
        ]
    index_of = {pole["entity_number"]: i for i, pole in enumerate(poles)}
    parent = list(range(len(poles)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in links:
        ra, rb = find(index_of[a]), find(index_of[b])
        if ra != rb:
            parent[ra] = rb
    groups = collections.defaultdict(list)
    for i, pole in enumerate(poles):
        groups[find(i)].append(pole)
    if len(groups) == 1:
        return []
    smallest = sorted(groups.values(), key=len)[0]
    summary = []
    for members in sorted(groups.values(), key=len, reverse=True):
        first = sorted(blueprint.cells_of(members[0]))
        summary.append(
            "%d 根（塔 #%s @%s）"
            % (
                len(members),
                members[0].get("entity_number"),
                geometry.format_cell(first[0]) if first else "?",
            )
        )
    cells = []
    for pole in smallest[:6]:
        found = sorted(blueprint.cells_of(pole))
        if found:
            cells.append(found[0])
    return [
        _defect(
            "power-network-split",
            # 有铜线但分成几张，可能是**故意**的独立电网（大基地常见），所以只说 warning；
            # 一条铜线都没写则一定是错——那种图每根塔各成一张网。
            WARNING,
            "电线杆按 wires 分成 %d 张互不相连的电网：%s。只有接到电源的那一张会通电"
            % (len(groups), "；".join(summary[:6])),
            "在断开的两张网之间补铜线 [A,5,B,5]（或调 primitives.connect_poles() 一次补齐）；"
            "本来就想分几张网就忽略",
            cells=cells,
            entities=[p.get("entity_number") for p in smallest[:6]],
        )
    ]


def check_pipes(blueprint):
    """孤立管：四面都接不上东西的管道是白摆的。"""
    pipes = [
        e
        for e in blueprint.entities
        if (protos.find(e["name"]) or {}).get("type") in ("pipe", "pipe-to-ground")
    ]
    if not pipes:
        return []
    defects = []
    collections_names = collections.Counter(e["name"] for e in blueprint.entities)
    for entity in pipes:
        proto = protos.entity(entity["name"])
        cells = sorted(blueprint.cells_of(entity))
        if not cells:
            continue
        head = cells[0]
        connected = False
        if proto["type"] == "pipe":
            for direction in geometry.DIRECTIONS:
                neighbours = blueprint.at(geometry.offset(head, direction, 1))
                for neighbour in neighbours:
                    neighbour_proto = protos.find(neighbour["name"])
                    if neighbour_proto is None:
                        continue
                    if neighbour_proto["type"] in ("pipe", "pipe-to-ground", "pump"):
                        connected = True
                        break
                    if protos.fluid_connections(neighbour["name"]):
                        position = neighbour["position"]
                        for connection in protos.fluid_connections(neighbour["name"]):
                            offset = geometry.rotate_point(
                                connection["position"][0],
                                connection["position"][1],
                                neighbour.get("direction", 0),
                            )
                            if geometry.cell_of(
                                position["x"] + offset[0], position["y"] + offset[1]
                            ) == head:
                                connected = True
                                break
                    if connected:
                        break
                if connected:
                    break
        else:
            connected = True  # 地下管配对另有专门检查
        if not connected:
            defects.append(
                _defect(
                    "pipe-dangling",
                    NOTE,
                    "%s 四面都接不上管道或机器液口，摆着没用"
                    % _describe(entity),
                    "接上管道，或删掉",
                    cells=[head],
                    entities=[entity.get("entity_number")],
                )
            )
    return defects


def check_machines(blueprint):
    defects = []
    for entity in blueprint.entities:
        proto = protos.find(entity["name"])
        if proto is None or proto["type"] not in ("assembling-machine",):
            continue
        if not entity.get("recipe"):
            continue
        try:
            recipe = protos.recipe(entity["recipe"])
        except protos.UnknownName as missing:
            defects.append(
                _defect(
                    "unknown-recipe",
                    ERROR,
                    "%s 的配方 %r 不在原版配方表里" % (_describe(entity), entity["recipe"]),
                    "换成原版配方名",
                    entities=[entity.get("entity_number")],
                )
            )
            continue
        categories = recipe.get("categories") or ["crafting"]
        machine_categories = protos.crafting_categories(entity["name"])
        if machine_categories and not set(categories) & set(machine_categories):
            defects.append(
                _defect(
                    "machine-cannot-craft",
                    ERROR,
                    "%s 做不了 %s：配方类别 %s，机器只支持 %s"
                    % (
                        _describe(entity),
                        entity["recipe"],
                        "/".join(categories),
                        "/".join(machine_categories),
                    ),
                    "换一台支持这些类别的机器（查 python tools/query.py who-makes <物品>）",
                    entities=[entity.get("entity_number")],
                )
            )
    return defects


def check_furnace_recipe_ignored(blueprint):
    """炉子类实体上写了 recipe —— 游戏不认这个字段，配方由**进料**决定。

    证据：27 张真实语料里 375 个原版炉子（stone/steel/electric）带 recipe 的 **0 个**，
    223 个组装机带 recipe 的 223 个。真机也验过：给电炉写 recipe=steel-plate、喂铁矿石，
    出来的还是铁板。所以写着 steel 实际出铁，是静默出错。
    """
    defects = []
    for entity in blueprint.entities:
        proto = protos.find(entity["name"])
        if proto is None or proto["type"] != "furnace" or not entity.get("recipe"):
            continue
        position = entity["position"]
        defects.append(
            _defect(
                "furnace-recipe-ignored",
                WARNING,
                "%s 上写了 recipe=%r，但原版炉子不认这个字段——配方由进料决定，"
                "写着 %s 也可能实际出别的东西"
                % (
                    geometry.format_position((position["x"], position["y"])),
                    entity["recipe"],
                    entity["recipe"],
                ),
                "把 recipe 从炉子上删掉，改由进料带决定（喂铁矿石出铁板、喂铁板出钢板）；"
                "只有 foundry 这类组装机才认 recipe",
                entities=[entity.get("entity_number")],
            )
        )
    return defects


def check_recipes_on_furnaces(blueprint):
    defects = []
    for entity in blueprint.entities:
        proto = protos.find(entity["name"])
        if proto is None or proto["type"] != "furnace" or not entity.get("recipe"):
            continue
        categories = (protos.recipe(entity["recipe"]).get("categories")) or ["crafting"]
        machine_categories = protos.crafting_categories(entity["name"])
        if machine_categories and not set(categories) & set(machine_categories):
            defects.append(
                _defect(
                    "machine-cannot-craft",
                    ERROR,
                    "%s 做不了 %s：配方类别 %s，炉子只支持 %s"
                    % (
                        _describe(entity),
                        entity["recipe"],
                        "/".join(categories),
                        "/".join(machine_categories),
                    ),
                    "炉子只能跑 smelting 类配方",
                    entities=[entity.get("entity_number")],
                )
            )
    return defects


def check_direction_recipe(blueprint):
    """direction_requires_recipe 的实体：写了方向就得连 recipe 一起写。

    真机实测：同一个 foundry，direction=8 不写 recipe → 游戏读回 0；写上 casting-iron → 读回 8。
    这是本项目最危险的一类错——方向静默消失、游戏不报错，装上去只是"通了电不动"。

    只管带这个字段的实体（目前 foundry / cryogenic-plant）。组装机/炉子/化工厂裸建也认方向，
    对它们报这条就是误报。
    """
    defects = []
    for entity in blueprint.entities:
        direction = entity.get("direction", 0)
        if not direction or entity.get("recipe"):
            continue
        if not protos.direction_requires_recipe(entity["name"]):
            continue
        position = entity["position"]
        severity = ERROR
        defects.append(
            _defect(
                "direction-needs-recipe",
                severity,
                "%s 设了 direction=%d 但没写 recipe，游戏会忽略这个方向（读回 0），"
                "液口/进料口可能全朝反了，而且游戏不报错"
                % (
                    geometry.format_position((position["x"], position["y"])),
                    direction,
                ),
                "补上一个这台机器能做的 recipe；这台确实只当摆设就把它转回 0",
                entities=[entity.get("entity_number")],
            )
        )
    return defects


CHECKS = (
    check_entity_names,
    check_overlap,
    check_bounds,
    check_parity,
    check_directions,
    check_direction_recipe,
    check_underground_belts,
    check_underground_pipes,
    check_belts,
    check_inserters,
    check_power,
    check_power_network,
    check_pipes,
    check_machines,
    check_furnace_recipe_ignored,
    check_recipes_on_furnaces,
)


def lint(blueprint, checks=None, min_severity=NOTE):
    """跑全部静态检查，返回结构化缺陷列表（按 severity 与位置排序）。"""
    selected = checks or CHECKS
    defects = []
    for check in selected:
        defects.extend(check(blueprint))
    limit = _SEVERITY_ORDER[min_severity]
    defects = [d for d in defects if _SEVERITY_ORDER[d["severity"]] <= limit]
    defects.sort(
        key=lambda d: (
            _SEVERITY_ORDER[d["severity"]],
            d["cells"][0][1] if d["cells"] else 0,
            d["cells"][0][0] if d["cells"] else 0,
            d["code"],
        )
    )
    return defects


def report(blueprint, min_severity=NOTE):
    defects = lint(blueprint, min_severity=min_severity)
    counts = collections.Counter(d["severity"] for d in defects)
    by_code = collections.Counter(d["code"] for d in defects)
    return {
        "ok": counts.get(ERROR, 0) == 0,
        "counts": {
            "error": counts.get(ERROR, 0),
            "warning": counts.get(WARNING, 0),
            "note": counts.get(NOTE, 0),
            "total": len(defects),
        },
        "by_code": dict(by_code.most_common()),
        "defects": defects,
    }


def format_report(report_payload, limit=40):
    counts = report_payload["counts"]
    lines = [
        "lint: %d error / %d warning / %d note"
        % (counts["error"], counts["warning"], counts["note"])
    ]
    for defect in report_payload["defects"][:limit]:
        where = ""
        if defect["cells"]:
            where = " @%s" % ",".join("(%d,%d)" % (c[0], c[1]) for c in defect["cells"][:3])
        lines.append(
            "  [%s] %s%s  %s"
            % (defect["severity"], defect["code"], where, defect["message"])
        )
        if defect.get("fix"):
            lines.append("       -> %s" % defect["fix"])
    if len(report_payload["defects"]) > limit:
        lines.append("  ... 还有 %d 条" % (len(report_payload["defects"]) - limit))
    return "\n".join(lines)
