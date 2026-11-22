"""自测版（testbench）：把声明了端口的蓝图包上夹具，做成一张可以直接跑、直接数产量的图。

夹具三件套（人类做蓝图测试的标准做法）：
- 输入口 → 一排 infinity-chest ＋ 等量臂，把物品持续送上带
- 输出口 → 一排臂把货搬进钢箱，箱子装得下就跑不堵
- 电网 → electric-energy-interface 就近并网

原型都是 hidden（正常游戏造不出来），所以自测版**单独出**，绝不混进给玩家的正式产物。

两个因为实测才定下来的细节：

1. **一只臂喂不动一条带。** 24 台石炉要吃 7.5 板/秒的矿，而一只快速臂大约 2.3/秒。
   只用一只臂时，真机 40 秒后仍有 56 个臂在 waiting_for_source_items——矿上不来。
   所以每个口配 DEFAULT_ARMS 只臂。
2. **输出箱子用普通钢箱，不用 infinity-chest。** infinity-chest 会把东西吞掉或补满，
   "60 秒累计到多少"就没法读了。钢箱 48 格装得下 60 秒的产量。
"""

from . import blueprint as bpm
from . import geometry, primitives, protos

INFINITY_CHEST = "infinity-chest"
ENERGY_INTERFACE = "electric-energy-interface"
BOX = "steel-chest"
ARM = "fast-inserter"

DEFAULT_ARMS = 4
_SIDES = (4, 12)  # 相对流向的左/右法向：(dy,dx) 由 geometry 旋转得到


def _free(blueprint, cell):
    return not blueprint.at(cell)


def _normals(flow_direction):
    return (geometry.opposite(geometry.canonical(flow_direction) + 4) % 16, (geometry.canonical(flow_direction) + 12) % 16)


def _station_row(blueprint, anchor, flow, count, sign):
    """在 anchor 的上游(sign=-1)/下游(sign=+1)找一排能放下 count 个侧边工位的位置。

    返回 (stub_cells, normal, arm_cells, chest_cells)，放不下返回 None。
    """
    unit = geometry.vector(flow)
    for offset in range(0, count * 8, max(1, count // 2)):
        stations = [
            (anchor[0] + sign * unit[0] * (offset + index + 1),
             anchor[1] + sign * unit[1] * (offset + index + 1))
            for index in range(count)
        ]
        # 带子必须从端口一路连到最远那格工位，中间不能留缺口——否则上游的货根本流不过来。
        run = [
            (anchor[0] + sign * unit[0] * (index + 1),
             anchor[1] + sign * unit[1] * (index + 1))
            for index in range(offset + count)
        ]
        if not all(_free(blueprint, cell) for cell in run):
            continue
        for normal in _normals(flow):
            step = geometry.vector(normal)
            arms = [(cell[0] + step[0], cell[1] + step[1]) for cell in stations]
            chests = [(cell[0] + 2 * step[0], cell[1] + 2 * step[1]) for cell in stations]
            if all(_free(blueprint, cell) for cell in arms + chests):
                return run, stations, normal, arms, chests
    return None


def _infinity_chest(blueprint, cell, item, count=200):
    return blueprint.add(
        INFINITY_CHEST,
        geometry.cell_center(cell),
        infinity_settings={
            "remove_unfiltered_items": True,
            "filters": [{"name": item, "count": count, "mode": "at-least", "index": 1}],
        },
    )


def _is_fluid(item):
    # 走 protos.is_fluid（查 fluids 表），不要用 find()——find 只查 entities，
    # petroleum-gas 这类纯流体查不到，会被当成固体塞进 infinity-chest（塑料那条踩过）。
    return protos.is_fluid(item)


def _attach_fluid(blueprint, cell, item):
    """流体口**只声明、不自建供给源**。

    **流体一律由真机侧的 --fluids 供**，自测版不造 infinity-pipe。
    理由和 5.7 同源：同一个输入不能有两个供给者——两套供气抢同一格，
    结果是**两边都供不上**（塑料那条实测：一台缺气变两台缺气）。

    这里返回一个声明，让 rig 去接；格子保持空着。
    """
    return {"cell": list(cell), "item": item}


def _attach_input(blueprint, cell, flow, item, arms=DEFAULT_ARMS):
    if _is_fluid(item):
        return [cell], _attach_fluid(blueprint, cell, item)
    found = _station_row(blueprint, cell, flow, arms, -1)
    if found is None:
        raise bpm.BlueprintError(
            "输入口 %s 上游找不到能放 %d 个工位的空位（需要 %d 格带 + 一排臂 + 一排箱子）"
            % (geometry.format_cell(cell), arms, arms)
        )
    run, stations, normal, arm_cells, chest_cells = found
    for cell_in_run in run:
        blueprint.add("transport-belt", geometry.cell_center(cell_in_run), flow)
    for arm_cell, chest_cell in zip(arm_cells, chest_cells):
        _infinity_chest(blueprint, chest_cell, item)
        primitives.inserter(blueprint, arm_cell, normal, ARM)
    return run


def _attach_output(blueprint, cell, flow, item, arms=DEFAULT_ARMS):
    if _free(blueprint, cell):
        blueprint.add("transport-belt", geometry.cell_center(cell), flow)
    found = _station_row(blueprint, cell, flow, arms, 1)
    if found is None:
        raise bpm.BlueprintError(
            "输出口 %s 下游找不到能放 %d 个工位的空位" % (geometry.format_cell(cell), arms)
        )
    run, stations, normal, arm_cells, chest_cells = found
    for cell_in_run in run:
        blueprint.add("transport-belt", geometry.cell_center(cell_in_run), flow)
    blueprint.add_port(run[-1], "输出", "夹具尾部，货物由这里的臂取走", flow, item)
    boxes = []
    for arm_cell, chest_cell in zip(arm_cells, chest_cells):
        primitives.inserter(blueprint, arm_cell, geometry.opposite(normal), ARM)
        boxes.append(blueprint.add(BOX, geometry.cell_center(chest_cell)))
    return boxes


def _place_power_interface(blueprint):
    bounds = blueprint.bounds()
    for row in range(bounds[3] + 2, bounds[3] + 12):
        for col in range(bounds[0] - 2, bounds[2] + 2):
            if all(_free(blueprint, (col + dx, row + dy)) for dx in (0, 1) for dy in (0, 1)):
                return blueprint.add(ENERGY_INTERFACE, (col + 1, row + 1))
    raise bpm.BlueprintError("蓝图下方找不到 2x2 空地放 electric-energy-interface")


def build_testbench(blueprint, seconds=60, product=None, arms=DEFAULT_ARMS):
    """产出自测版。返回 (新蓝图, 验收清单)。"""
    if not blueprint.ports:
        raise bpm.BlueprintError(
            "这张蓝图没有声明端口，接不上夹具。先 add_port(cell, '输入'/'输出', direction=..., item=...)"
        )
    clone = blueprint.copy()
    clone.build_area = None
    clone.ports = {}
    manifest = {"inputs": [], "outputs": [], "sinks": [], "seconds": seconds, "arms_per_port": arms}
    for cell, detail in sorted(blueprint.ports.items(), key=lambda item: (item[0][1], item[0][0])):
        role = detail.get("role") or ""
        flow = detail.get("direction")
        item = detail.get("item")
        if flow is None:
            raise bpm.BlueprintError(
                "端口 %s 没写 direction，自测版不知道该往哪接" % geometry.format_cell(cell)
            )
        is_input = "出" not in role
        if is_input:
            if not item:
                raise bpm.BlueprintError(
                    "输入口 %s 没写 item，不知道灌什么" % geometry.format_cell(cell)
                )
            attached = _attach_input(clone, cell, flow, item, arms)
            is_fluid = protos.is_fluid(item)
            manifest["inputs"].append(
                {
                    "cell": list(cell),
                    "item": item,
                    "direction": flow,
                    "kind": "fluid" if is_fluid else "item",
                    "supplied_by": "rig" if is_fluid else "testbench fixture",
                }
            )
            # **声明驱动**：每一个输入点都在 external_supply 里报一次（格 + 物品 + 类别），
            # 谁供由约定决定，不靠猜。
            #   流体 → 只由真机的 --fluids 供，自测版不建源（两套供气抢同一格会互相挤掉）；
            #   固体 → 由自测版自己建的带子/箱子供。
            # 真机侧改成"只在声明格供给，没声明一格都不灌"之后，这条清单就是唯一依据。
            manifest.setdefault("external_supply", []).append(
                {
                    "cell": list(cell),
                    "item": item,
                    "kind": "fluid" if is_fluid else "item",
                    "note": (
                        "自测版不建供给源，由 rig 的 --fluids 接"
                        if is_fluid
                        else "自测版已经建好带子+臂+无限箱，rig 不要在这里再灌"
                    ),
                }
            )
        else:
            boxes = _attach_output(clone, cell, flow, item or product or "iron-plate", arms)
            for box in boxes:
                manifest["sinks"].append(
                    {
                        "cell": list(sorted(clone.cells_of(box))[0]),
                        "item": box.get("item") or item or product,
                    }
                )
            manifest["outputs"].append(
                {"cell": list(cell), "item": item or product, "direction": flow}
            )
    interface = _place_power_interface(clone)
    primitives.ensure_power(clone, extra_targets=[interface])
    primitives.connect_poles(clone)
    bounds = clone.bounds()
    clone.set_build_area((bounds[0], bounds[1]), (bounds[2], bounds[3]))
    manifest["power"] = {
        "cell": list(sorted(clone.cells_of(interface))[0]),
        "note": "electric-energy-interface，已并进电网；它自己是无限电源",
    }
    manifest["fixtures"] = sum(1 for e in clone.entities if e["name"] == INFINITY_CHEST)
    manifest["product"] = product or (manifest["outputs"][0]["item"] if manifest["outputs"] else None)
    manifest["acceptance"] = {
        "product": manifest["product"],
        "seconds": seconds,
        "sinks_cell": [s["cell"] for s in manifest["sinks"]],
        "criterion": "跑 %d 秒后，把所有输出箱里的 %s 加起来，和验收下限比"
        % (seconds, manifest["product"]),
    }
    clone.data["label"] = "%s [testbench]" % (blueprint.data.get("label") or "blueprint")
    clone.data["description"] = (
        "自测版：输入口接 infinity-chest，输出口接钢箱，电网由 electric-energy-interface 供电。"
        "夹具是 hidden 原型，正常游戏造不出来——不要发给玩家。"
    )
    return clone, manifest
