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

    # 配比 3:2（人类蓝图 -P5_wlYMsi2D_Cj8rL0a 就是这个比例）：
    # 铜线机 2 个/秒 × 3 = 6/秒；绿板机满负荷 3 铜线/秒 × 2 = 6/秒 —— 正好平。
    # 一对一是 2 : 3，上限只有 67%，这就是之前注释里写的「跑 67%」。
    n_circuit = primitives.machine_count(target_rate, circuit, machine)
    n_cable = n_circuit + (n_circuit + 1) // 2  # ceil(1.5 × n)

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

    # 排布：铜线-绿板-铜线-绿板-铜线 …… 每台绿板机**东西两侧各有一台铜线机**，
    # 这才是 3:2 能成立的原因（每台绿板机能同时从两侧吃铜线）。
    slots = ["cable"]
    for index in range(n_circuit):
        slots.append("circuit")
        slots.append("cable" if index < n_cable - 1 else "cable")
    slots = slots[: n_cable + n_circuit]
    if slots.count("cable") != n_cable or slots.count("circuit") != n_circuit:
        slots = []
        for index in range(n_cable + n_circuit):
            slots.append("cable" if index % 2 == 0 else "circuit")
        slots = slots[: n_cable + n_circuit]
    step = mw + 1  # 机器占 mw 格 + 中间 1 格臂列
    total = len(slots) * step - 1

    # 两条进料带隔了 1 行。两条都从西头进会抢夹具格子（实测：铁板夹具被挤走 → 绿板 0 个），
    # 而且实测两台绿板机 17 : 9 ——**下游那台只拿到上游一半的铁板**，是沿带被吃掉。
    # 改法：**铁板带反向，从东头进、往西流**。两个端口一个在西、一个在东，天然不抢，
    # 而且铁板先遇到的是下游那台，两台吃到的量就平了。
    # 试过「铁板从东头进、往西流」：几何能建，但自测版夹具建不起来——
    # 铁板口(x+total,1) 和绿板出口(x+total,7) 都在东头，两套夹具抢同一片地。
    # 所以退回西头 + 5 格错开，把「两台吃得不均」留作下一步要解的问题。
    stagger = 5
    primitives.belt_run(blueprint, (x0 - 1, copper_row), 4, total + 1, belt)
    primitives.belt_run(blueprint, (x0 - 1 - stagger, iron_row), 4, total + 1 + stagger, belt)
    primitives.belt_run(blueprint, (x0 - 1, out_row), 4, total + 1, belt)

    def spread(count):
        if count <= 1:
            return [0]
        return [int(round(i * (mw - 1) / (count - 1))) for i in range(count)]

    placed = []
    for index, kind in enumerate(slots):
        left = x0 + index * step
        recipe = cable if kind == "cable" else circuit
        machine_entity = blueprint.add_spanning(machine, (left, top_row), None, recipe=recipe)
        placed.append({"kind": kind, "left": left, "entity": machine_entity})
        if kind == "cable":
            # 铜线机上方：长臂跨过铁板带取铜板（1 个/秒，2 根长臂 1.66/秒够）
            for offset in spread(copper_arms):
                primitives.inserter(
                    blueprint, (left + offset, arm_row), 0, "long-handed-inserter"
                )
        else:
            # 绿板机上方：快臂取铁板
            for offset in spread(iron_arms):
                primitives.inserter(blueprint, (left + offset, arm_row), 0, "fast-inserter")
            # 出绿板
            for offset in spread(iron_arms):
                primitives.inserter(
                    blueprint, (left + offset, out_arm_row), 0, "fast-inserter"
                )

    # 直插臂：每对相邻机器之间一列；铜线在左 → direction=12（取西放东），反之 4
    for index in range(len(placed) - 1):
        a, b = placed[index], placed[index + 1]
        if a["kind"] == b["kind"]:
            continue
        direction = 12 if a["kind"] == "cable" else 4
        for offset in range(direct_arms):
            primitives.inserter(
                blueprint, (b["left"] - 1, top_row + offset), direction, "fast-inserter"
            )

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
