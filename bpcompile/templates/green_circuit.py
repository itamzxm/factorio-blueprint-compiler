"""绿板产线（横向交替 + 直插）。

面预算（这是设计的核心约束）：

    铜线机：北（进铜板）+ 东（直插出铜线）            = 2 张面
    绿板机：北（进铁板）+ 西（直插接铜线）+ 南（出绿板） = 3 张面

两台机器的**进料都在北侧**，直插走东西向，输出走南侧——所以每一层都有面，
不用再往上叠带子，也不用把链条再往上抬一层。

    y-2  铜板带  ────────────────────────────────
    y-1  铁板带  ────────────────────────────────
    y0   臂：铜线机上方是长臂（跨过铁板带取铜板），绿板机上方是快臂（取铁板）
    y1..y3  [铜线机] [臂列] [绿板机]   [铜线机] [臂列] [绿板机]
    y4   取绿板的臂
    y5   绿板带  ────────────────────────────────
"""

from .. import primitives, protos


def _recipe_input(recipe_name, item):
    for name, amount in protos.recipe(recipe_name).get("in") or []:
        if name == item:
            return amount
    raise ValueError("配方 %s 不吃 %s" % (recipe_name, item))


def green_circuit(
    blueprint,
    target_rate=2.0,
    machine="assembling-machine-1",
    belt="fast-transport-belt",
    origin_cell=(0, 0),
    label=None,
    copper_arms=2,
    iron_arms=2,
    direct_arms=3,
):
    """横向交替的绿板线：铜线机直插绿板机，两个进料都在北侧。"""
    cable = "copper-cable"
    circuit = "electronic-circuit"
    mw = protos.entity(machine)["w"]
    mh = protos.entity(machine)["h"]

    n_circuit = primitives.machine_count(target_rate, circuit, machine)
    n_cable = n_circuit  # 一对一直插；铜线机 2 个/秒，绿板机满负荷要 3 个/秒 → 跑 67%

    # 直插吞吐当场核算：3 根快臂 6.93/秒 ≥ 绿板机满负荷 3 铜线/秒
    demand = _recipe_input(circuit, cable) * (protos.crafting_speed(machine) or 1) / (
        protos.recipe(circuit).get("time") or 0.5
    )
    supply = primitives.inserter_throughput("fast-inserter", direct_arms)
    if supply < demand:
        raise ValueError(
            "直插只有 %.2f 铜线/秒，一台绿板机满负荷要 %.2f/秒——搬不动" % (supply, demand)
        )

    x0, y0 = origin_cell
    copper_row = y0 + 0
    iron_row = y0 + 1
    arm_row = y0 + 2
    top_row = y0 + 3
    out_arm_row = top_row + mh
    out_row = out_arm_row + 1

    pair_width = mw + 1 + mw
    total = n_circuit * pair_width + 1

    # 两条进料带隔了 1 行，如果两个端口都在同一处，夹具会抢同一批格子
    # （实测：铁板夹具被挤走 → 铁板进不来 → 绿板机全饿着 → 铜线机 full_output）。
    # 所以让铁板带往西多伸 5 格，两个端口在横向上分开。
    stagger = 5
    primitives.belt_run(blueprint, (x0 - 1, copper_row), 4, total + 1, belt)
    primitives.belt_run(blueprint, (x0 - 1 - stagger, iron_row), 4, total + 1 + stagger, belt)
    primitives.belt_run(blueprint, (x0 - 1, out_row), 4, total + 1, belt)

    def spread(count):
        if count <= 1:
            return [0]
        return [int(round(i * (mw - 1) / (count - 1))) for i in range(count)]

    pairs = []
    for index in range(n_circuit):
        left = x0 + index * pair_width
        cable_machine = blueprint.add_spanning(machine, (left, top_row), None, recipe=cable)
        circuit_machine = blueprint.add_spanning(
            machine, (left + mw + 1, top_row), None, recipe=circuit
        )
        # 铜线机上方：长臂跨过铁板带取铜板（1 个/秒，2 根长臂 1.66/秒够）
        for offset in spread(copper_arms):
            primitives.inserter(blueprint, (left + offset, arm_row), 0, "long-handed-inserter")
        # 绿板机上方：快臂取铁板
        for offset in spread(iron_arms):
            primitives.inserter(
                blueprint, (left + mw + 1 + offset, arm_row), 0, "fast-inserter"
            )
        # 直插：铜线机 → 绿板机，臂列在两机之间
        for offset in range(direct_arms):
            primitives.inserter(
                blueprint, (left + mw, top_row + offset), 12, "fast-inserter"
            )
        # 出绿板
        for offset in spread(iron_arms):
            primitives.inserter(
                blueprint, (left + mw + 1 + offset, out_arm_row), 0, "fast-inserter"
            )
        pairs.append({"cable": cable_machine, "circuit": circuit_machine})

    primitives.ensure_power(
        blueprint, "medium-electric-pole", (x0 - 1 - stagger, arm_row, x0 + total, out_row)
    )
    primitives.connect_poles(blueprint)

    copper_port = (x0 - 1, copper_row)
    iron_port = (x0 - 1 - stagger, iron_row)
    out_port = (x0 + total, out_row)
    blueprint.add_port(copper_port, "输入", "铜板从这里进（向东流）", 4, "copper-plate")
    blueprint.add_port(iron_port, "输入", "铁板从这里进（向东流）", 4, "iron-plate")
    blueprint.add_port(out_port, "输出", "绿板从这里出来", 4, circuit)
    blueprint.set_build_area((x0 - 1 - stagger, copper_row), (x0 + total, out_row))

    per_circuit = min(supply, demand) / demand if demand else 1.0
    blueprint.data["label"] = label or "绿板 %d 对直插" % n_circuit
    blueprint.data["description"] = (
        "%d 台铜线机直插 %d 台绿板机（横向交替）。上行铜板带、下行铁板带，绿板从南侧出。"
        "臂位按面预算排：铜线机用北+东两张面，绿板机用北+西+南三张面。"
        % (n_cable, n_circuit)
    )
    return {
        "entities": len(blueprint.entities),
        "stations": {"cable": n_cable, "circuit": n_circuit},
        "machine": machine,
        "capacity_per_second": {
            "copper-cable": round(n_cable * (protos.crafting_speed(machine) or 1) / 0.5 * 2, 2),
            "electronic-circuit": round(
                n_circuit * (protos.crafting_speed(machine) or 1) / 0.5 * per_circuit, 2
            ),
        },
        "throughput_check": {
            "direct_insert_supply": supply,
            "demand": round(demand, 2),
            "ratio": round(per_circuit, 3),
        },
        "ports": {
            "copper_input_cell": list(copper_port),
            "iron_input_cell": list(iron_port),
            "circuit_output_cell": list(out_port),
        },
        "notes": [
            "一对一直插：铜线机出 2 个/秒，绿板机满负荷要 3 个/秒，所以每台绿板机跑 %.0f%%"
            % (per_circuit * 100),
            "直插 %d 根快臂 = %.2f/秒，够搬运（需要 %.2f/秒），瓶颈在铜线产量不在臂上"
            % (direct_arms, supply, demand),
        ],
    }
