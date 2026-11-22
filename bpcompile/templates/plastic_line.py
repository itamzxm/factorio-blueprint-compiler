"""塑料产线（验收标准第 5 条）：化工厂 + 石油气 + 煤。

面预算（化学厂 3x3，液口在北侧两个角格、南侧两个角格）：

    北面：气进（角格上的管口）+ 煤进（中间格的臂）  —— 气走地下管跨越煤带，两条互不抢格
    南面：塑料出（中间格的臂）

    行结构
      py-4  气主管 ────────────────  端口在 (-1, py-4)
      py-3  地下管入口（气从主管往下穿）
      py-2  煤带 ──────────────────  端口在 (-1, py-2)
      py-1  气出管 + 煤臂
      py..py+2  化工厂
      py+3  取塑料的臂
      py+4  塑料带 ────────────────  端口在东端

**关键**：石油气从主管下来时会被煤带那一行挡住，所以用**地下管**跨过煤带
（`underground_pipe_pair`，跨度 2）。管口只和"朝向那一侧"的管子连通，
所以主管在上、入口朝北、出口朝南，三段才能接成一条。
"""

from .. import primitives, protos


def plastic_line(
    blueprint,
    target_rate=2.0,
    machine="chemical-plant",
    belt="fast-transport-belt",
    origin_cell=(0, 0),
    label=None,
    gap=2,
):
    """造一条塑料线。石油气从端口进，煤从带子进，塑料从南侧出。"""
    recipe = "plastic-bar"
    count = primitives.machine_count(target_rate, recipe, machine)
    mw = protos.entity(machine)["w"]
    mh = protos.entity(machine)["h"]

    gas_row = origin_cell[1]
    drop_row = gas_row + 1
    coal_row = gas_row + 2
    arm_row = gas_row + 3
    top_row = gas_row + 4
    out_arm_row = top_row + mh
    out_row = out_arm_row + 1

    step = mw + gap
    last_left = origin_cell[0] + (count - 1) * step
    span = last_left + mw + gap + 1

    # 气主管
    primitives.pipe_run(blueprint, (origin_cell[0] - 1, gas_row), 4, span)
    # 煤带
    primitives.belt_run(blueprint, (origin_cell[0] - 1, coal_row), 4, span, belt)
    # 塑料带
    primitives.belt_run(blueprint, (origin_cell[0] - 1, out_row), 4, span, belt)

    plants = []
    for index in range(count):
        left = origin_cell[0] + index * step
        plant = blueprint.add_spanning(machine, (left, top_row), None, recipe=recipe)
        plants.append(plant)
        # **落点一律由 machine_fluid_ports 给，不手摆坐标。**
        # 同一条判据不能有两个实现路径——"液口要接在再往外一格"已经在
        # connect_machine_port 里算好了，手摆一遍就等于绕过它。
        #
        # ⚠ 这里踩过一个坑：`outside` 是**终点**不是起点。地下管的跨度从起点往
        # flow 方向量，把终点当起点用，隧道就会一路钻到机器底下去
        # （真机读数：化工厂 fluid_box.segment = nil，机器压根没接进管网）。
        for port in primitives.machine_fluid_ports(blueprint, plant):
            if port["io"] not in ("input", "input-output"):
                continue
            outside = primitives.geometry.offset(port["cell"], port["direction"], 1)
            flow = primitives.geometry.opposite(port["direction"])
            # 地面口在 outside、朝着机器；隧道朝反方向退 span 格，从机器外侧绕
            start = primitives.geometry.offset(outside, flow, -2)
            primitives.underground_pipe_pair(blueprint, start, flow, 2)
        # 煤：从煤带取，中间格往下塞
        primitives.inserter(blueprint, (left + mw // 2, arm_row), 0, "fast-inserter")
        # 塑料：南侧中间格取出
        primitives.inserter(blueprint, (left + mw // 2, out_arm_row), 0, "fast-inserter")

    primitives.ensure_power(
        blueprint,
        "medium-electric-pole",
        (origin_cell[0] - 1, gas_row, origin_cell[0] + span, out_row),
    )
    primitives.connect_poles(blueprint)

    gas_port = (origin_cell[0] - 1, gas_row)
    coal_port = (origin_cell[0] - 1, coal_row)
    # 带子从 x0-1 起铺 span 格 → 最后一格是 x0+span-2，端口要紧贴它的**下一格**
    out_port = (origin_cell[0] + span - 1, out_row)
    blueprint.add_port(gas_port, "输入", "石油气从这里进（走地下管穿煤带）", 4, "petroleum-gas")
    blueprint.add_port(coal_port, "输入", "煤从这里进（向东流）", 4, "coal")
    blueprint.add_port(out_port, "输出", "塑料从这里出来", 4, "plastic-bar")
    blueprint.set_build_area((origin_cell[0] - 1, gas_row), (origin_cell[0] + span, out_row))

    speed = protos.crafting_speed(machine) or 1
    time = protos.recipe(recipe).get("time") or 1
    per_craft = (protos.recipe(recipe).get("out") or [["", 2]])[0][1]
    blueprint.data["label"] = label or "塑料 %d 台" % count
    blueprint.data["description"] = (
        "%d 台化工厂做塑料（%g 秒、每次 %d 个）。气从北侧角格经地下管跨过煤带进入，"
        "煤从北侧煤带由中间格的臂送进，塑料从南侧出。台上数由真值表算，没写死。"
        % (count, time, per_craft)
    )
    return {
        "entities": len(blueprint.entities),
        "stations": {"chemical-plant": count},
        "machine": machine,
        "capacity_per_second": {recipe: round(count * speed / time * per_craft, 2)},
        "ports": {
            "gas_input_cell": list(gas_port),
            "coal_input_cell": list(coal_port),
            "plastic_output_cell": list(out_port),
        },
        "notes": [
            "气走地下管跨越煤带（跨度 2）：主管在上、入口朝北、出口朝南，三段才连得起来",
            "台数：%d 台 = %.1f/秒 × %g 秒 ÷ %g 速度 ÷ %d 个/次"
            % (count, target_rate, time, speed, per_craft),
        ],
    }
