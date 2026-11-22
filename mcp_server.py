"""MCP server：把语义原语、lint、模板、渲染、真机验证暴露给另一个 AI。

设计约定（写进工具描述里，AI 才不会踩坑）：
- 坐标一律用**格坐标**（整数）：格 (i,j) 覆盖 x∈[i,i+1)。不用实体中心的 0.5 坐标。
- 物流臂的 direction 指向**取货侧**，不是放货侧。
- 带配方槽的机器写非 0 方向时可能被游戏忽略（见 direction_requires_recipe）。
- 一次会话维护一张"当前蓝图"；改完自己调 lint 看缺陷，再调 verify 上真机。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer

from bpcompile import protos
from bpcompile.service import Service

server = MCPServer(
    name="factorio-blueprint",
    instructions=(
        "给 AI 用的 Factorio 2.1 蓝图编译器。不要自己写坐标或蓝图字符串："
        "调语义工具（模板/带/臂/网格/供电），几何由代码展开；每次改完调 bp_lint 看结构化缺陷，"
        "再调 bp_verify 上真机落地。坐标一律是格坐标（整数），臂的 direction 指向取货侧。"
    ),
)

SERVICE = Service()


def _json(payload):
    return json.dumps(payload, ensure_ascii=False)


@server.tool(
    name="bp_new",
    description="清空并开一张新蓝图。返回当前状态。",
)
def bp_new(label: str | None = None) -> str:
    return _json(SERVICE.new(label))


@server.tool(
    name="bp_load",
    description="载入蓝图：可以传蓝图字符串、解码后的蓝图 JSON，或本机文件路径。",
)
def bp_load(source: str) -> str:
    try:
        return _json(SERVICE.load(source))
    except Exception as failure:
        return _json({"ok": False, "error": str(failure)})


@server.tool(
    name="bp_status",
    description="看当前蓝图：实体数、各类实体数量、占地格数、对外接线口（ports）、声明占地。",
)
def bp_status() -> str:
    return _json(SERVICE.status())


@server.tool(
    name="bp_string",
    description="导出当前蓝图的游戏内字符串（直接粘进 Factorio 的那种）。同时给出统计。",
)
def bp_string() -> str:
    payload = SERVICE.status()
    payload["blueprint_string"] = SERVICE.to_string()
    return _json(payload)


@server.tool(
    name="bp_save",
    description="把当前蓝图写到文件。as_json=true 写解码后的 JSON，否则写游戏字符串。",
)
def bp_save(path: str, as_json: bool = False) -> str:
    try:
        return _json(SERVICE.save(path, as_json))
    except Exception as failure:
        return _json({"ok": False, "error": str(failure)})


@server.tool(
    name="bp_entity",
    description=(
        "查实体真值表：尺寸、支持的朝向、是否要电、配方槽、带速/地下跨度、臂的取货放货距离、"
        "流体端口。别凭记忆写数字，先查这张表。"
    ),
)
def bp_entity(name: str) -> str:
    return _json(SERVICE.entity_info(name))


@server.tool(
    name="bp_recipe",
    description="查配方：耗时、进出料、类别、哪些机器能做。",
)
def bp_recipe(name: str) -> str:
    return _json(SERVICE.recipe_info(name))


@server.tool(
    name="bp_find_recipe",
    description="反查某个物品是哪些配方产出的（做产线先确定走哪条配方）。",
)
def bp_find_recipe(product: str) -> str:
    return _json(SERVICE.find_recipes(product))


@server.tool(
    name="bp_furnace_array",
    description=(
        "模板：一排炉子。count 台，北面两条独立输入带（进料 / 进燃料，料和煤不能串一条），"
        "南面一条出板带；臂朝向、供电全部算好。返回占地、输入/输出物品与三个接线口格坐标。"
        "⚠ **炉子的配方由进料决定，机器上不写 recipe**（写 steel-plate 也照样出铁板）。"
        "所以参数是 input_item / output_item：炼铁给 iron-ore，炼钢给 iron-plate（5 铁板→1 钢板），"
        "炼铜给 copper-ore。furnace 用 stone-furnace/steel-furnace（烧燃料）或 electric-furnace（要电）。"
    ),
)
def bp_furnace_array(
    count: int = 24,
    furnace: str = "stone-furnace",
    input_item: str | None = None,
    output_item: str | None = None,
    label: str | None = None,
) -> str:
    return _json(
        SERVICE.furnace_array(
            count=count,
            furnace=furnace,
            input_item=input_item,
            output_item=output_item,
            label=label,
        )
    )


@server.tool(
    name="bp_green_circuit",
    description=(
        "模板：绿板（electronic-circuit）产线。target_rate 是**目标绿板/秒**，台数由真值表算出来"
        "（机器数 = 目标速率 × 配方耗时 ÷ 机器速度 ÷ 每次产出）。内部结构是两站上下叠："
        "上面一排铜线机、下面一排绿板机，铜线由上一站的成品带直接当下一站的第二条输入带。"
        "返回台数、每秒产能、三个端口格坐标。⚠ 铜线带是往西流的，改方向会让绿板机永远缺料。"
    ),
)
def bp_green_circuit(
    target_rate: float = 2.0,
    machine: str = "assembling-machine-1",
    label: str | None = None,
) -> str:
    return _json(SERVICE.green_circuit(target_rate, machine, label))


@server.tool(
    name="bp_plastic_line",
    description=(
        "⚠ **未验证**：结构成立、lint 干净、真机可建（44/44），但**气路未通**——"
        "60 秒 `by_product` 是 null，两台化工厂一台缺气一台缺煤，别当它能用。"
        "模板：塑料产线。化工厂一排，石油气从北侧角格经地下管跨过煤带进入，煤从北侧煤带送进，"
        "塑料从南侧出。台数由真值表算（塑料 1 秒 × 每次 2 个）。"
        "自测版会给气口接 infinity-pipe。"
    ),
)
def bp_plastic_line(
    target_rate: float = 2.0,
    machine: str = "chemical-plant",
    label: str | None = None,
) -> str:
    return _json(SERVICE.plastic_line(target_rate, machine, label))


@server.tool(
    name="bp_add_entity",
    description=(
        "放一个实体。position 是实体中心（1 格实体落 .5，2 格实体落整数，会自动吸附到合法奇偶）。"
        "撞到别的实体会返回 conflict 而不是静默覆盖。"
        "⚠ 带 direction_requires_recipe 标记的机器（foundry / cryogenic-plant）写非 0 方向时必须同时给 recipe，"
        "否则游戏会把方向读回 0。"
    ),
)
def bp_add_entity(
    name: str,
    x: float,
    y: float,
    direction: int | None = None,
    recipe: str | None = None,
    allow_bare_direction: bool = False,
    force: bool = False,
) -> str:
    return _json(
        SERVICE.add_entity(name, (x, y), direction, recipe, allow_bare_direction, force)
    )


@server.tool(
    name="bp_remove_at",
    description="删掉占着某一格的所有实体。",
)
def bp_remove_at(cell_x: int, cell_y: int) -> str:
    return _json(SERVICE.remove_at((cell_x, cell_y)))


@server.tool(
    name="bp_belt",
    description=(
        "铺一条传送带。start_cell 是首格的格坐标，direction 是**流动方向**（east/west/north/south 或 0/4/8/12），"
        "length 是格数。underground 传 [[下标, 跨度], ...] 就能在地下跨过别的东西："
        "下标处放地下带入口，下标+跨度 处放出口，中间空出来（跨度上限查 bp_entity 的 belt.max_distance）。"
    ),
)
def bp_belt(
    start_cell_x: int,
    start_cell_y: int,
    direction: str = "east",
    length: int = 1,
    tier: str = "transport-belt",
    underground: list[list[int]] | None = None,
) -> str:
    return _json(
        SERVICE.belt(
            (start_cell_x, start_cell_y), direction, length, tier, underground or ()
        )
    )


@server.tool(
    name="bp_inserter",
    description=(
        "放一个物流臂。⚠ direction 指向**取货侧**，不是放货侧：朝北的臂从北边取、往南边放。"
        "拿不准就用 bp_inserter_between。"
    ),
)
def bp_inserter(
    cell_x: int, cell_y: int, direction: str = "north", tier: str = "inserter"
) -> str:
    return _json(SERVICE.inserter((cell_x, cell_y), direction, tier))


@server.tool(
    name="bp_inserter_between",
    description=(
        "放一个物流臂并自动算朝向：臂放在 cell，从 take_from 格取货、放进 put_into 格。"
        "两个目标必须相对（一左一右或一上一下），朝向由代码算，调用方不用管。"
    ),
)
def bp_inserter_between(
    cell_x: int,
    cell_y: int,
    take_from_x: int,
    take_from_y: int,
    put_into_x: int,
    put_into_y: int,
    tier: str = "inserter",
) -> str:
    return _json(
        SERVICE.inserter_between(
            (cell_x, cell_y), (take_from_x, take_from_y), (put_into_x, put_into_y), tier
        )
    )


@server.tool(
    name="bp_inserter_pair",
    description=(
        "给一台机器配一对臂（一进一出），机器四周挑空格、朝向全自动。"
        "machine_cell 是机器占的任意一格；load_from 是进货来源格，unload_to 是出货目标格。"
    ),
)
def bp_inserter_pair(
    machine_cell_x: int,
    machine_cell_y: int,
    load_from_x: int,
    load_from_y: int,
    unload_to_x: int,
    unload_to_y: int,
    tier: str = "inserter",
) -> str:
    return _json(
        SERVICE.inserter_pair(
            (machine_cell_x, machine_cell_y),
            (load_from_x, load_from_y),
            (unload_to_x, unload_to_y),
            tier,
        )
    )


@server.tool(
    name="bp_machine_grid",
    description=(
        "摆一片同型号机器。origin_cell 是第一台的**左上角格**，按 cols×rows 平铺，gap=[x,y] 是机器之间的空格数。"
        "recipe 会写到每台上（带方向时可能必须给，见 bp_add_entity）。"
    ),
)
def bp_machine_grid(
    machine: str,
    cols: int,
    rows: int,
    origin_cell_x: int,
    origin_cell_y: int,
    direction: int = 0,
    gap_x: int = 0,
    gap_y: int = 0,
    recipe: str | None = None,
) -> str:
    return _json(
        SERVICE.machine_grid(
            machine, cols, rows, (origin_cell_x, origin_cell_y), direction, (gap_x, gap_y), recipe
        )
    )


@server.tool(
    name="bp_power",
    description=(
        "自动补电：把当前蓝图里所有还没被供电的用电实体覆盖上，自己挑空格摆杆、保证杆子互相连得上。"
        "area 可选 [x0,y0,x1,y1] 限制杆子只能摆在这个格范围内。"
    ),
)
def bp_power(
    pole: str = "medium-electric-pole",
    area: list[int] | None = None,
) -> str:
    return _json(SERVICE.power(pole, area))


@server.tool(
    name="bp_lint",
    description=(
        "静态检查（毫秒级）：实体重叠、越出声明占地、地下带/地下管配对与跨度、断头带、臂的取放两侧、"
        "电线杆供电覆盖、配方与机器类别。每条缺陷带格坐标、人话原因、修复建议。"
        "min_severity 取 error/warning/note，用来过滤。"
    ),
)
def bp_lint(min_severity: str = "note") -> str:
    return _json(SERVICE.lint(min_severity))


@server.tool(
    name="bp_verify",
    description=(
        "三档验证。modes 可组合：'lint' / 'lint,rig' / 'lint,run'。"
        "rig = 送进本机 Factorio 真建一遍（约 2 秒），只证明'游戏认这张蓝图、实体都落地了'，"
        "**不证明它能跑**——臂对着空气照样落地通过。"
        "run = 加上真跑 run_seconds 秒并统计产出（produced / theoretical / achievement），"
        "要配合 bp_testbench 的自测版才有意义（夹具自带料和电）。"
        "powered_single=true 只给第一根电线杆接电，专门用来抓'电塔没写 wires'。"
    ),
)
def bp_verify(
    modes: str = "lint,rig",
    run_seconds: int | None = None,
    powered_single: bool = False,
) -> str:
    return _json(SERVICE.verify(modes, run_seconds, powered_single))


@server.tool(
    name="bp_render",
    description=(
        "把当前蓝图渲染成 PNG（多模态看图比读坐标快）。会自动标出接线口。返回图片绝对路径。"
    ),
)
def bp_render(path: str | None = None, cell_px: int = 18) -> str:
    try:
        return _json(SERVICE.render(path, cell_px))
    except Exception as failure:
        return _json({"ok": False, "error": str(failure)})


@server.tool(
    name="bp_declare_port",
    description=(
        "登记一个对外接线口（'这里接你的矿带/出板带'）。lint 不会把这些格当断头带。"
        "端口信息存在会话里、不进蓝图串，所以 bp_load 之后要重新声明。"
    ),
)
def bp_declare_port(
    cell_x: int,
    cell_y: int,
    role: str = "输出",
    note: str = "",
    direction: int | None = None,
    item: str | None = None,
) -> str:
    return _json(SERVICE.declare_port((cell_x, cell_y), role, note, direction, item))


@server.tool(
    name="bp_testbench",
    description=(
        "把当前蓝图包成**自测版**：输入口自动接 infinity-chest、输出口接钢箱、电网接 "
        "electric-energy-interface。夹具是隐藏原型（正常游戏造不出来），"
        "所以自测版单独出文件、不要发给玩家。要求蓝图先声明过端口（含 direction 与 item）。"
        "返回蓝图串、验收清单、lint 结果。"
    ),
)
def bp_testbench(
    seconds: int = 60,
    path: str | None = None,
    product: str | None = None,
    arms: int | None = None,
) -> str:
    return _json(SERVICE.testbench(seconds, path, product, arms))


@server.tool(
    name="bp_normalize",
    description="把坐标吸到合法奇偶、补图标、重排 entity_number。返回改了哪些。",
)
def bp_normalize() -> str:
    return _json(SERVICE.normalize())


def main():
    server.run("stdio")


if __name__ == "__main__":
    main()
