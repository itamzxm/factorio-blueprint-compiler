"""语义原语：调用方只说"要什么"，朝向和坐标由这里算。

所有原语都用**格坐标**（整数），不用 0.5 的实体中心坐标——少一层换算就少一类错。
一条硬约束：原语不会静默产出坏几何。放不下就抛错并说清是哪一格被谁占了。
"""

import collections

from . import blueprint as bpm
from . import geometry, protos


def _free(blueprint, cell, allow=()):
    for entity in blueprint.at(cell):
        if entity["name"] in allow:
            continue
        return False
    return True


def _unit_cell(entity, blueprint):
    cells = sorted(blueprint.cells_of(entity))
    return cells[0] if cells else geometry.cell_of(entity["position"]["x"], entity["position"]["y"])


def underground_variant(belt_name):
    """按带的运行速度找同档地下带——不写死名字表，换档位时自动对上。"""
    speed = protos.belt_speed(belt_name)
    if speed is None:
        raise bpm.BlueprintError("%s 不是传送带（没有 belt.speed）" % belt_name)
    for name, proto in protos._data()["entities"].items():
        if proto.get("type") != "underground-belt":
            continue
        if abs((proto.get("belt") or {}).get("speed", -1) - speed) < 1e-9:
            return name
    raise bpm.BlueprintError("找不到速度 %g 的地下带档位" % speed)


def belt_run(blueprint, start_cell, direction, length, tier="transport-belt", underground=()):
    """铺一条带。

    underground=[(相对下标, 跨度)] —— 在下标处放地下带入口，在 下标+跨度 处放出口，
    中间那几格空出来给别的东西穿过。跨度不能超过该档地下带的上限。
    """
    direction = geometry.canonical(direction)
    specs = sorted(underground)
    covered = set()
    spans = []
    for index, span in specs:
        limit = protos.underground_span(underground_variant(tier))
        if span < 1 or span > limit:
            raise bpm.BlueprintError(
                "地下段跨度 %d 不合法：%s 只能跨 1..%d 格" % (span, underground_variant(tier), limit)
            )
        if index < 0 or index + span > length - 1:
            raise bpm.BlueprintError(
                "地下段 [%d, %d] 超出带长 %d（下标 0..%d）" % (index, index + span, length, length - 1)
            )
        for offset in range(index, index + span + 1):
            if offset in covered:
                raise bpm.BlueprintError("第 %d 格被两段地下带同时占用" % offset)
            covered.add(offset)
        spans.append((index, span))

    placed = []
    for index in range(length):
        cell = geometry.offset(start_cell, direction, index)
        if index in covered:
            continue
        placed.append(
            blueprint.add(tier, geometry.cell_center(cell), direction if direction else None)
        )
    for index, span in spans:
        entry = geometry.offset(start_cell, direction, index)
        exit_cell = geometry.offset(start_cell, direction, index + span)
        variant = underground_variant(tier)
        placed.append(
            blueprint.add(
                variant,
                geometry.cell_center(entry),
                direction if direction else None,
                type="input",
            )
        )
        placed.append(
            blueprint.add(
                variant,
                geometry.cell_center(exit_cell),
                direction if direction else None,
                type="output",
            )
        )
    return placed


def belt_line_cells(start_cell, direction, length):
    return [geometry.offset(start_cell, direction, index) for index in range(length)]


def inserter(blueprint, cell, direction, tier="inserter", **fields):
    """放一个物流臂。direction 指向**取货侧**，不是放货侧。"""
    return blueprint.add(tier, geometry.cell_center(cell), geometry.canonical(direction), **fields)


def _aim_cell(aim, blueprint):
    if isinstance(aim, tuple) and len(aim) == 2 and all(isinstance(v, int) for v in aim):
        return aim
    entity = aim
    return _unit_cell(entity, blueprint)


def inserter_between(blueprint, cell, take_from, put_into, tier="inserter", **fields):
    """放一个臂在 cell 上，从 take_from 取货、放进 put_into。

    take_from / put_into 可以是格坐标，也可以是蓝图里的实体。
    """
    source = _aim_cell(take_from, blueprint)
    target = _aim_cell(put_into, blueprint)
    dx_in = (source[0] - cell[0], source[1] - cell[1])
    dx_out = (target[0] - cell[0], target[1] - cell[1])
    pickup_dir = geometry.direction_from_vector(*dx_in)
    drop_dir = geometry.direction_from_vector(*dx_out)
    if pickup_dir is None or drop_dir is None:
        raise bpm.BlueprintError(
            "臂在 %s，取货侧 %s / 放货侧 %s 必须正交落在相邻格（臂只能隔一格取、隔一格放）"
            % (geometry.format_cell(cell), geometry.format_cell(source), geometry.format_cell(target))
        )
    if drop_dir != geometry.opposite(pickup_dir):
        raise bpm.BlueprintError(
            "臂在 %s，取货侧在 %s、放货侧在 %s，两者不相对；一个臂只能从一侧取、往对面放"
            % (
                geometry.format_cell(cell),
                geometry.DIRECTION_NAMES[pickup_dir],
                geometry.DIRECTION_NAMES[drop_dir],
            )
        )
    return inserter(blueprint, cell, pickup_dir, tier, **fields)


def _adjacent_free(blueprint, entity, aim, occupied):
    cells = blueprint.cells_of(entity)
    candidates = set()
    for cell in cells:
        for direction in geometry.DIRECTIONS:
            neighbour = geometry.offset(cell, direction, 1)
            if neighbour in cells or neighbour in occupied:
                continue
            candidates.add(neighbour)
    if not candidates:
        raise bpm.BlueprintError("%s 四周没有空格放臂" % entity["name"])
    free = [cell for cell in candidates if _free(blueprint, cell)]
    pool = free or sorted(candidates)
    return min(pool, key=lambda cell: (abs(cell[0] - aim[0]) + abs(cell[1] - aim[1]), cell))


def inserter_pair(blueprint, machine, load_from, unload_to, tier="inserter", unload_tier=None):
    """给一台机器配一对臂：一台从 load_from 进货，一台往 unload_to 出货。

    自动挑机器四周的空格、自动算朝向（direction 指取货侧）。
    """
    entity = machine
    source = _aim_cell(load_from, blueprint)
    sink = _aim_cell(unload_to, blueprint)
    center = _unit_cell(entity, blueprint)
    in_cell = _adjacent_free(blueprint, entity, source, set())
    in_helper = inserter_between(blueprint, in_cell, source, center, tier)
    out_cell = _adjacent_free(blueprint, entity, sink, {in_cell})
    out_helper = inserter_between(
        blueprint, out_cell, center, sink, unload_tier or tier
    )
    return in_helper, out_helper


def machine_grid(
    blueprint,
    machine,
    cols,
    rows,
    origin_cell,
    direction=0,
    gap=(0, 0),
    recipe=None,
    **fields
):
    """摆一片机器。origin_cell 是第一台的左上角格，按 cols×rows 平铺。"""
    proto = protos.entity(machine)
    width, height = geometry.footprint(proto["w"], proto["h"], direction or 0)
    step_x = width + gap[0]
    step_y = height + gap[1]
    if cols < 1 or rows < 1:
        raise bpm.BlueprintError("机器网格至少 1x1")
    placed = []
    for row in range(rows):
        for col in range(cols):
            top_left = (origin_cell[0] + col * step_x, origin_cell[1] + row * step_y)
            center = (top_left[0] + width / 2.0, top_left[1] + height / 2.0)
            placed.append(
                blueprint.add(machine, center, direction if direction else None, recipe=recipe, **fields)
            )
    return placed


COPPER_WIRE = 5


def _pole_cells(blueprint):
    out = []
    for entity in blueprint.entities:
        if (protos.find(entity["name"]) or {}).get("type") != "electric-pole":
            continue
        cells = sorted(blueprint.cells_of(entity))
        if cells:
            out.append((entity, cells[0]))
    return out


def connect_poles(blueprint, reach_tolerance=0.0):
    """把线距范围内**所有**电塔两两用铜线连上（幂等，可以反复调）。

    不写这段，游戏建出来的每根塔就是一张独立电网——接一根线只有一根塔有电，
    其余塔下的机器全是 no_power。这不是几何问题，是字段问题。
    """
    poles = _pole_cells(blueprint)
    added = 0
    for index, (first, first_cell) in enumerate(poles):
        reach_first = protos.pole_wire(first["name"]) or 0
        for second, second_cell in poles[index + 1 :]:
            reach = min(reach_first, protos.pole_wire(second["name"]) or 0) + reach_tolerance
            if _euclid(first_cell, second_cell) > reach:
                continue
            before = len(blueprint.wires)
            blueprint.add_wire(
                first["entity_number"], COPPER_WIRE, second["entity_number"], COPPER_WIRE
            )
            if len(blueprint.wires) > before:
                added += 1
    return added


def _euclid(a, b):
    dx = (a[0] + 0.5) - (b[0] + 0.5)
    dy = (a[1] + 0.5) - (b[1] + 0.5)
    return (dx * dx + dy * dy) ** 0.5


def pole_run(blueprint, start_cell, direction, count, spacing, tier="medium-electric-pole"):
    """一排等距电线杆（自动连铜线）。"""
    direction = geometry.canonical(direction)
    placed = []
    for index in range(count):
        cell = geometry.offset(start_cell, direction, index * spacing)
        placed.append(blueprint.add(tier, geometry.cell_center(cell)))
    connect_poles(blueprint)
    return placed


def _covered_cells(pole_cell, supply):
    out = set()
    radius = int(supply)
    for dx in range(-radius - 1, radius + 2):
        for dy in range(-radius - 1, radius + 2):
            cell = (pole_cell[0] + dx, pole_cell[1] + dy)
            cx, cy = geometry.cell_center(cell)
            if abs(cx - (pole_cell[0] + 0.5)) <= supply and abs(cy - (pole_cell[1] + 0.5)) <= supply:
                out.add(cell)
    return out


def ensure_power(
    blueprint, tier="medium-electric-pole", area=None, extra_free=(), extra_targets=()
):
    """把还没被供电的用电实体全都覆盖上：自动挑空格摆杆子，并保证杆子互相连得上。

    返回新摆的电线杆列表；本来就够电就返回空列表。
    """
    supply = protos.pole_supply(tier)
    wire = protos.pole_wire(tier)
    if supply is None:
        raise bpm.BlueprintError("%s 不是电线杆" % tier)
    electric = [e for e in blueprint.entities if protos.needs_power(e["name"])]
    # extra_targets：不需要"用电"但必须并进电网的实体（例如 electric-energy-interface）
    for entity in extra_targets:
        if entity not in electric:
            electric.append(entity)
    if not electric:
        return []
    covered = set()
    for entity in blueprint.entities:
        if (protos.find(entity["name"]) or {}).get("type") == "electric-pole":
            covered |= _covered_cells(_unit_cell(entity, blueprint), protos.pole_supply(entity["name"]) or 0)

    def is_powered(entity):
        return any(cell in covered for cell in blueprint.cells_of(entity))

    pending = [e for e in electric if not is_powered(e)]
    if not pending:
        return []

    bounds = blueprint.bounds()
    if area is None:
        pad = int(supply) + 2
        area = (
            bounds[0] - pad,
            bounds[1] - pad,
            bounds[2] + pad,
            bounds[3] + pad,
        )
    candidates = []
    for i in range(area[0], area[2] + 1):
        for j in range(area[1], area[3] + 1):
            cell = (i, j)
            if _free(blueprint, cell, allow=extra_free):
                candidates.append(cell)
    if not candidates:
        raise bpm.BlueprintError("没有空格可以摆电线杆")

    poles = [
        _unit_cell(e, blueprint)
        for e in blueprint.entities
        if (protos.find(e["name"]) or {}).get("type") == "electric-pole"
        and protos.pole_wire(e["name"]) is not None
    ]
    placed = []
    while True:
        pending = [e for e in electric if not any(c in covered for c in blueprint.cells_of(e))]
        if not pending:
            break
        need = set()
        for entity in pending:
            need |= blueprint.cells_of(entity)
        best_linked = None
        best_linked_score = 0
        best_any = None
        best_any_score = 0
        for cell in candidates:
            reach = _covered_cells(cell, supply)
            score = sum(1 for entity in pending if blueprint.cells_of(entity) & reach)
            if score == 0:
                continue
            # 用欧氏距离判"接得上"——connect_poles 也是欧氏的，两边口径必须一致，
            # 否则会出现"以为接上了、其实 wire 拉不到"的孤岛（踩过：3 根塔自成一张网）。
            linked = any(
                ((cell[0] + 0.5 - px) ** 2 + (cell[1] + 0.5 - py) ** 2) ** 0.5 <= wire
                for px, py in poles
            )
            if linked and score > best_linked_score:
                best_linked_score = score
                best_linked = cell
            elif score > best_any_score:
                best_any_score = score
                best_any = cell
        # 能接上现有电网的位置优先；一个都没有才允许先放孤岛，后面 bridge_networks 兜底
        best = best_linked or best_any
        if best is None:
            raise bpm.BlueprintError(
                "%d 个用电实体里有 %d 个找不到能覆盖的空格——布局太散，人工算一下杆位"
                % (len(electric), len(pending))
            )
        placed.append(blueprint.add(tier, geometry.cell_center(best)))
        covered |= _covered_cells(best, supply)
        poles.append((best[0] + 0.5, best[1] + 0.5))
        candidates.remove(best)
    connect_poles(blueprint)
    bridge_networks(blueprint, tier)
    return placed


def pole_components(blueprint):
    """按铜线把电塔分成连通块，返回 [[实体, ...], ...]。"""
    poles = [e for e in blueprint.entities if (protos.find(e["name"]) or {}).get("type") == "electric-pole"]
    index = {p["entity_number"]: i for i, p in enumerate(poles)}
    parent = list(range(len(poles)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in blueprint.copper_wires():
        if a in index and b in index:
            ra, rb = find(index[a]), find(index[b])
            if ra != rb:
                parent[ra] = rb
    groups = {}
    for i, pole in enumerate(poles):
        groups.setdefault(find(i), []).append(pole)
    return list(groups.values())


def bridge_networks(blueprint, tier="medium-electric-pole"):
    """电网被拆成几块时，补塔把它们桥起来。补不了就返回补了几根。"""
    added = 0
    for _ in range(12):
        comps = pole_components(blueprint)
        if len(comps) <= 1:
            return added
        wire = max((protos.pole_wire(p["name"]) or 0) for p in comps[0])
        best = None
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                for first in comps[i]:
                    for second in comps[j]:
                        reach = min(
                            protos.pole_wire(first["name"]) or 0,
                            protos.pole_wire(second["name"]) or 0,
                        )
                        distance = _euclid(
                            geometry.cell_of(first["position"]["x"], first["position"]["y"]),
                            geometry.cell_of(second["position"]["x"], second["position"]["y"]),
                        )
                        if distance <= 2 * reach and (best is None or distance < best[0]):
                            best = (distance, first, second, reach)
        if best is None:
            return added
        _, first, second, reach = best
        mid = (
            (first["position"]["x"] + second["position"]["x"]) / 2.0 - 0.5,
            (first["position"]["y"] + second["position"]["y"]) / 2.0 - 0.5,
        )
        target = (int(round(mid[0])), int(round(mid[1])))
        spot = None
        for radius in range(0, 5):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    cell = (target[0] + dx, target[1] + dy)
                    if not _free(blueprint, cell):
                        continue
                    if any(
                        _euclid(cell, geometry.cell_of(p["position"]["x"], p["position"]["y"])) > reach
                        for p in (first, second)
                    ):
                        continue
                    spot = cell
                    break
                if spot:
                    break
            if spot:
                break
        if spot is None:
            return added
        blueprint.add(tier, geometry.cell_center(spot))
        connect_poles(blueprint)
        added += 1
    return added


def find_entity(blueprint, cell):
    occupants = blueprint.at(cell)
    if not occupants:
        raise bpm.BlueprintError("格 %s 上没有实体" % geometry.format_cell(cell))
    return occupants[0]


# row_station 的参数名会遮住同名的模块函数，这里先取别名
_belt_run = belt_run
_place_inserter = inserter
_ensure_power = ensure_power


def machine_count(target_rate, recipe_name, machine, output_index=0):
    """要多少台机器才能达到 target_rate 个成品/秒。

    机器数 = 目标速率 × 配方耗时 ÷ 机器速度 ÷ 每次产出数量。数字全从真值表算，不写死。
    """
    import math

    recipe = protos.recipe(recipe_name)
    speed = protos.crafting_speed(machine)
    if not speed:
        raise bpm.BlueprintError("%s 没有 crafting.speed，算不出产能" % machine)
    time = recipe.get("time") or 0.5
    results = recipe.get("out") or []
    if not results:
        raise bpm.BlueprintError("配方 %s 没有产出，算不出台数" % recipe_name)
    per_craft = results[min(output_index, len(results) - 1)][1] or 1
    return max(1, int(math.ceil(target_rate * time / speed / per_craft - 1e-9)))


def row_station(
    blueprint,
    machine,
    count,
    recipe=None,
    origin_cell=(0, 0),
    width=None,
    gap=0,
    inputs=1,
    belt="fast-transport-belt",
    inserter="inserter",
    long_inserter="long-handed-inserter",
    pole="medium-electric-pole",
    build_belts=(True, True),
    build_output_belt=True,
    belt_span=None,
    power_area=None,
    output_direction=4,
    arm_column="left",
    arms=(1, 1),
):
    """一排机器的可重复单元：北面 k 条输入带，南面一条输出带，臂朝向全算好。

    行结构（相对 origin_cell，y 向下）：

        y+0  远端输入带（只有 inputs==2 才有；由**长手臂跨过 y+1** 取料）
        y+1  近端输入带（短手臂取料）
        y+2  臂：左端短臂取近端，右端长臂取远端
        y+3 .. y+3+mh-1  机器
        y+3+mh  取成品的臂
        y+4+mh  成品带

    build_belts / build_output_belt 置 False 时那条带不建——用来把上一站的产品带
    直接当下一站的输入带（绿板线就是这么把铜线送进绿板机的）。
    """
    proto = protos.entity(machine)
    mh = proto["h"]
    mw = width or proto["w"]
    step = mw + gap
    x0, y0 = origin_cell[0], origin_cell[1]
    last_left = x0 + (count - 1) * step
    span = belt_span if belt_span is not None else last_left + mw - x0 + 1
    belt_start_x = x0 - 1

    near_row = y0 + 1
    far_row = y0 + 0
    arm_row = y0 + 2
    top_row = y0 + 3
    out_arm_row = top_row + mh
    out_row = out_arm_row + 1

    machines = []
    for index in range(count):
        left = x0 + index * step
        machines.append(blueprint.add_spanning(machine, (left, top_row), None, recipe=recipe))

    # 臂位可调：流体机器的液口在四角边格上，臂压左列会正好盖住西北角液口，
    # 这时候把臂挪到中间列，把两个角让给管子。
    if arm_column == "left":
        near_x, long_x = 0, mw - 1
    elif arm_column == "middle":
        near_x, long_x = mw // 2, mw // 2
    else:
        raise bpm.BlueprintError("arm_column 只能是 'left' 或 'middle'，收到 %r" % (arm_column,))
    # 输入带只要盖到最后一个取料格为止——多出来的尾格是断头带（没人取，货堆死）
    # 远端那排臂可能排在机器最右一列上，输入带必须一直盖到那里
    outermost = (mw - 1) if inputs >= 2 else near_x
    in_span = step * (count - 1) + outermost + 2
    if build_belts[0]:
        _belt_run(blueprint, (belt_start_x, near_row), 4, in_span, belt)
    if inputs >= 2 and build_belts[1]:
        _belt_run(blueprint, (belt_start_x, far_row), 4, in_span, belt)
    if build_output_belt:
        # 成品带可以反向流：把上一站的产品送进**东边**的下一站时，只往东流的话
        # 落在下游的货永远回不到上游的消费格（绿板线踩过这个）。
        # 反向流时整体东移一格：西端别压在进料口的夹具位上（绿板线的铜线带踩过）
        out_start = (
            belt_start_x if output_direction == 4 else belt_start_x + span
        )
        _belt_run(blueprint, (out_start, out_row), output_direction, span, belt)

    # 一台机器一侧能站 mw 根臂。取货速率是会被算漏的瓶颈：长臂 0.83/秒、快臂 2.31/秒，
    # 一台绿板机要 3 铜线/秒，一根臂根本搬不动——上游只会堆满，游戏不会报任何错。
    near_count, far_count = arms
    total_arms = near_count + (far_count if inputs >= 2 else 0)
    if total_arms > mw:
        raise bpm.BlueprintError(
            "机器一侧只有 %d 格，每格只能站一根臂，要了 %d 根（近端 %d + 远端 %d）。"
            "两条输入抢同一侧时，**加臂解决不了吞吐**——要么换快臂，要么把其中一条输入挪到别的侧面，"
            "要么改成机器对机器的直插。"
            % (mw, total_arms, near_count, far_count if inputs >= 2 else 0)
        )
    # 从机器左侧起把手臂依次排开：近端先占，远端接着占
    pool = list(range(mw))
    near_cols = pool[:near_count] if near_count <= 1 else [
        int(round(i * (mw - 1) / max(1, near_count - 1))) for i in range(near_count)
    ]
    remaining = [c for c in pool if c not in near_cols]
    far_cols = remaining[:far_count] if far_count <= 1 else [
        remaining[0] if len(remaining) == 1 else
        int(round(i * (remaining[-1] - remaining[0]) / max(1, far_count - 1))) + remaining[0]
        for i in range(far_count)
    ]
    for index in range(count):
        left = x0 + index * step
        for column in near_cols:
            _place_inserter(blueprint, (left + column, arm_row), 0, inserter)
        if inputs >= 2:
            for column in far_cols:
                _place_inserter(blueprint, (left + column, arm_row), 0, long_inserter)
        _place_inserter(blueprint, (left + near_cols[0], out_arm_row), 0, inserter)

    _ensure_power(
        blueprint, pole, power_area or (belt_start_x, arm_row, x0 + span - 1, out_row)
    )

    ports = {
        "input_near_cell": [belt_start_x, near_row],
        "input_far_cell": [belt_start_x, far_row] if inputs >= 2 else None,
        "output_cell": [belt_start_x + span - 1, out_row],
    }
    return {
        "machines": machines,
        "machine_cells": [[x0 + i * step, top_row] for i in range(count)],
        "ports": ports,
        "rows": {
            "far_belt": far_row,
            "near_belt": near_row,
            "arms": arm_row,
            "machines": top_row,
            "out_arms": out_arm_row,
            "out_belt": out_row,
        },
        "height": out_row - y0 + 1,
        "next_origin": (x0, out_row),
        "span": span,
    }



# ---------------------------------------------------------------- 管道


def underground_pipe_pair(blueprint, start_cell, flow_direction, span, tier="pipe-to-ground"):
    """一对地下管，**埋在 flow_direction 方向跨 span 格**。

    方向口径（从 `lint.check_underground_pipes` 的同一份判据搬来，不另写一套）：
    地下段朝 direction+8，所以让管道往东流时，**入口 direction=12（朝西）、出口 direction=4（朝东）**。
    写反了两端各成孤立管段，游戏不报错。
    """
    limit = protos.underground_span(tier) or 0
    if span < 1 or span > limit:
        raise bpm.BlueprintError("%s 只能跨 1..%d 格，要了 %d" % (tier, limit, span))
    flow = geometry.canonical(flow_direction)
    entry_dir = geometry.opposite(flow)
    exit_cell = geometry.offset(start_cell, flow, span)
    entry = blueprint.add(tier, geometry.cell_center(start_cell), entry_dir)
    exit_entity = blueprint.add(tier, geometry.cell_center(exit_cell), flow)
    return entry, exit_entity


def pipe_run(blueprint, start_cell, direction, length, underground=(), tier="pipe"):
    """铺一条管。underground=[(下标, 跨度)] 表示从该下标起埋到 下标+跨度，
    中间那段不铺明管（给带子/机器让路）。
    """
    direction = geometry.canonical(direction)
    covered = set()
    spans = []
    for index, span in sorted(underground):
        for offset in range(index, index + span + 1):
            if offset in covered:
                raise bpm.BlueprintError("第 %d 格被两段地下管同时占用" % offset)
            covered.add(offset)
        if index + span > length - 1:
            raise bpm.BlueprintError(
                "地下管 [%d, %d] 超出管长 %d" % (index, index + span, length)
            )
        spans.append((index, span))
    placed = []
    for index in range(length):
        if index in covered:
            continue
        placed.append(
            blueprint.add(tier, geometry.cell_center(geometry.offset(start_cell, direction, index)))
        )
    for index, span in spans:
        placed.extend(
            underground_pipe_pair(
                blueprint, geometry.offset(start_cell, direction, index), direction, span
            )
        )
    return placed


def machine_fluid_ports(blueprint, machine):
    """机器液口的实际格坐标：{格, 方向, io, 是否地下口}。方向已经按机器朝向旋好。"""
    position = machine["position"]
    out = []
    for connection in protos.fluid_connections(machine["name"]):
        offset = geometry.rotate_point(
            connection["position"][0], connection["position"][1], machine.get("direction", 0)
        )
        cell = geometry.cell_of(position["x"] + offset[0], position["y"] + offset[1])
        direction = (connection["direction"] + geometry.canonical(machine.get("direction", 0))) % 16
        out.append(
            {
                "cell": cell,
                "direction": geometry.canonical(direction),
                "io": connection["io"],
                "flow": connection.get("flow"),
            }
        )
    return out


def connect_machine_port(blueprint, machine, port_cell=None, tier="pipe"):
    """在机器某个液口外侧接一节管。不给 port_cell 就接第一个进料口。

    返回接上的那节管。接不上（那格被占）抛错并说清是谁占了。
    """
    ports = [p for p in machine_fluid_ports(blueprint, machine) if p["io"] in ("input", "input-output")]
    if not ports:
        raise bpm.BlueprintError("%s 没有进料液口" % machine["name"])
    target = None
    if port_cell is None:
        target = ports[0]
    else:
        for port in ports:
            if port["cell"] == tuple(port_cell):
                target = port
                break
    if target is None:
        raise bpm.BlueprintError(
            "%s 的 %s 不在它的液口列表里：%s"
            % (machine["name"], port_cell, [list(p["cell"]) for p in ports])
        )
    # 液口坐标落在机器自己的边格上（化学厂是四角），管子要接在**再往外一格**
    cell = geometry.offset(target["cell"], target["direction"], 1)
    occupants = blueprint.at(cell)
    if occupants:
        raise bpm.BlueprintError(
            "液口 %s 外侧已经被 %s 占了，管子接不上"
            % (geometry.format_cell(cell), occupants[0]["name"])
        )
    return blueprint.add(tier, geometry.cell_center(cell))



# ---------------------------------------------------------------- 直插


# 各类臂的搬运速率（个/秒，满负荷、同类物品）。数字来自游戏实测的转速/摆臂角度，
# 这里只用来**当场判吞吐够不够**，不让调用方靠记忆。
INSERTER_RATE = {
    "inserter": 0.83,
    "long-handed-inserter": 0.83,
    "fast-inserter": 2.31,
    "bulk-inserter": 5.6,
}


def inserter_throughput(tier, count):
    return INSERTER_RATE.get(tier, 0.83) * count


def machine_pair(
    blueprint,
    upper,
    upper_recipe,
    lower,
    lower_recipe,
    pairs,
    origin_cell=(0, 0),
    gap=1,
    arms=3,
    inserter="fast-inserter",
    check_rate=True,
):
    """上位机器的产物用快臂**直插**下位机器，中间不过带子。

    这是 Factorio 里最基本的单元之一。横向排：上位在左、下位在右，臂站在中间那一列上。

        [上位 3x3][臂列][下位 3x3]

    每对之间站 `arms` 根臂（受机器高度限制）。`check_rate` 开着时，会按下位机器每秒需要的
    原料量和臂的搬运速率比一次，不够就当场抛错——"搬不动的那个东西不会报错，它只会让上游堆满"。
    """
    up_proto = protos.entity(upper)
    low_proto = protos.entity(lower)
    uh, lh = up_proto["h"], low_proto["h"]
    if uh != lh:
        raise bpm.BlueprintError("%s 和 %s 高度不同，直插列对不齐" % (upper, lower))
    if arms > uh:
        raise bpm.BlueprintError(
            "%s 只有 %d 行高，一侧最多站 %d 根臂，要了 %d 根" % (lower, uh, uh, arms)
        )
    tier_rate = INSERTER_RATE.get(inserter, 0.83)
    per_second = tier_rate * arms

    # 功率核算：下位机器每做一次要几个上位的产物
    need = None
    for name, amount in protos.recipe(lower_recipe).get("in") or []:
        if name == upper_recipe or name == protos.recipe(upper_recipe).get("out", [[None]])[0][0]:
            need = amount
            break
    if need is None:
        produced = protos.recipe(upper_recipe).get("out") or []
        product = produced[0][0] if produced else None
        for name, amount in protos.recipe(lower_recipe).get("in") or []:
            if name == product:
                need = amount
                break
    if need is not None and check_rate:
        low_speed = protos.crafting_speed(lower) or 1
        low_time = protos.recipe(lower_recipe).get("time") or 0.5
        demand = need * low_speed / low_time
        if per_second < demand:
            raise bpm.BlueprintError(
                "%d 根%s 只有 %.2f 个/秒，而一台 %s 做 %s 每秒要 %.2f 个——搬不动。"
                "加臂（一侧最多 %d 根，上限 %.2f/秒）或换更快的臂。"
                % (arms, inserter, per_second, lower, lower_recipe, demand, uh, tier_rate * uh)
            )

    step = up_proto["w"] + gap + low_proto["w"]
    row = origin_cell[1] + 1
    made = []
    for index in range(pairs):
        left = origin_cell[0] + index * step
        up = blueprint.add_spanning(upper, (left, row), None, recipe=upper_recipe)
        low = blueprint.add_spanning(lower, (left + up_proto["w"] + gap, row), None, recipe=lower_recipe)
        arm_column = left + up_proto["w"]
        for k in range(arms):
            offset = 0 if arms == 1 else int(round(k * (uh - 1) / (arms - 1)))
            # direction=12：取货朝西（上位机器），放货朝东（下位机器）
            _place_inserter(blueprint, (arm_column, row + offset), 12, inserter)
        made.append({"upper": up, "lower": low, "arm_column": arm_column, "row": row})

    return {
        "pairs": made,
        "step": step,
        "threshold": {
            "inserter": inserter,
            "arms": arms,
            "per_second": per_second,
            "demand_per_second": demand if need is not None else None,
        },
    }

