"""炉子阵列模板。

一排炉子，北面两条独立输入带（进料 / 进燃料），南面一条出板带；臂全部算好朝向。
布局（格坐标，y 向下）：

    y = -3      燃料带  ────────────────────────→   （只有烧固体燃料的炉子才有）
    y = -2      进料带  ────────────────────────→
    y = -1      [短臂取料][长臂跨过进料带取燃料]  ×N
    y = 0..fh-1 炉子 ×N（紧挨着排）
    y = fh      [短臂从炉子取成品]
    y = fh+1    出板带  ────────────────────────→

⚠ **炉子的配方由进料决定，不是由 recipe 字段决定。**
27 张真实语料里 375 个原版炉子（stone/steel/electric）**带 recipe 的 0 个**，
223 个组装机带 recipe 的 223 个。真机也验过：给电炉写 recipe=steel-plate、喂铁矿石，
出来的是铁板。所以这个模板的语义是「喂什么出什么」：
参数是 input_item / output_item，炉子实体上**不写** recipe。
（`foundry` 那种 type=assembling-machine 的机器才写 recipe，它们认这个字段。）

两条输入带必须分开：烧固体燃料的炉子，料和煤不能串在一条带上。
燃料那一路用长手臂跨过进料带取料——长手臂取货距离 2 格，正好越过一格带。
"""

from .. import geometry, primitives, protos

# belt 默认用快速带，是量出来的不是拍的：
#   24 台石炉从西端一个进料口喂：快带能过 300 的验收线，黄带跑不到。
#   差的那部分全是坡道时间——料要沿 48 格带子走到东头，带子越快坡道越短。
#   玩家在端口接普通黄带照样能进出（黄带喂快带是通的）。
DEFAULT_OPTIONS = {
    "count": 24,
    "furnace": "stone-furnace",
    "input_item": "iron-ore",
    "belt": "fast-transport-belt",
    "inserter": "inserter",
    "long_inserter": "long-handed-inserter",
    "pole": "medium-electric-pole",
    "label": None,
}


def resolve_items(input_item=None, output_item=None):
    """把「喂什么 / 出什么」补全，并**交叉校验**。

    两个都给的时候必须在 smelting 配方里真的对得上——这一步是纯静态可查的，
    不该留到真机跑完才发现产出是 0。
    """
    smelting = [(name, protos.recipe(name)) for name in protos.recipes_in_category("smelting")]
    if input_item and output_item:
        producers = [
            recipe
            for _, recipe in smelting
            if any(name == output_item for name, _ in (recipe.get("out") or []))
        ]
        if not producers:
            raise ValueError(
                "炉子烧不出 %r：smelting 类配方里没有它的产物。可选的产物有：%s"
                % (
                    output_item,
                    "、".join(
                        sorted({name for _, r in smelting for name, _ in (r.get("out") or [])})
                    ),
                )
            )
        needs = {name for recipe in producers for name, _ in (recipe.get("in") or [])}
        if input_item not in needs:
            raise ValueError(
                "喂 %r 做不出 %r：炉子的 smelting 配方里，做 %r 需要 %s。"
                "要么把进料改成 %s，要么把产物换成喂 %r 能出的东西"
                % (
                    input_item,
                    output_item,
                    output_item,
                    "、".join(sorted(needs)),
                    " 或 ".join(sorted(needs)),
                    input_item,
                )
            )
        return input_item, output_item
    if output_item and not input_item:
        for _, recipe in smelting:
            if any(name == output_item for name, _ in (recipe.get("out") or [])):
                return (recipe.get("in") or [[None]])[0][0], output_item
        raise ValueError(
            "没有哪个 smelting 配方产出 %r；换一个炉子能烧的产物（铁板/铜板/钢板/石头砖…）"
            % output_item
        )
    if input_item and not output_item:
        for _, recipe in smelting:
            if (recipe.get("in") or [[None]])[0][0] == input_item:
                return input_item, (recipe.get("out") or [[None]])[0][0]
        raise ValueError(
            "没有哪个 smelting 配方吃 %r；炉子只跑 smelting（矿→板、铁板→钢板）" % input_item
        )
    raise ValueError("至少要给 input_item 或 output_item 一个")


def furnace_array(
    blueprint,
    count=24,
    furnace="stone-furnace",
    input_item=None,
    output_item=None,
    recipe=None,
    belt="fast-transport-belt",
    inserter="inserter",
    long_inserter="long-handed-inserter",
    pole="medium-electric-pole",
    label=None,
):
    if count < 1:
        raise ValueError("count 至少 1")
    proto = protos.entity(furnace)
    width, height = proto["w"], proto["h"]
    if width != height or width not in (2, 3):
        raise ValueError("%s 是 %dx%d，这个模板只排 2x2 或 3x3 的炉子" % (furnace, width, height))
    notes = []
    # 兼容旧参数：recipe 当 output_item 用（语义一样：这个阵列出什么）
    if recipe is not None and output_item is None:
        output_item = recipe
        notes.append(
            "你给的是 recipe=%r，但**炉子不认蓝图里的 recipe**（原版炉子导出的蓝图里没有这个字段，"
            "产物由进料决定）。已按 output_item=%r 处理。" % (recipe, recipe)
        )
    elif recipe is not None and output_item is not None and recipe != output_item:
        notes.append(
            "你同时给了 recipe=%r 和 output_item=%r；recipe 对炉子无效，已忽略，只按 output_item 处理。"
            % (recipe, output_item)
        )
    if input_item is None and output_item is None:
        input_item = DEFAULT_OPTIONS["input_item"]
    derived_input = input_item is None
    derived_output = output_item is None
    feed, product = resolve_items(input_item, output_item)
    if derived_input:
        notes.append(
            "进料按 smelting 配方反推为 %r（喂它才出 %r）。要是想喂别的，显式传 input_item。"
            % (feed, product)
        )
    if derived_output:
        notes.append("产物按进料反推为 %r。" % product)
    notes.append(
        "炉子实体上没有写 recipe——产物由进料决定：喂 %s 出 %s。" % (feed, product)
    )
    burner = protos.energy_type(furnace) == "burner"
    # 原版炉子不认 recipe；组配机类型的机器（foundry）才写
    recipe_field = product if proto["type"] != "furnace" else None

    fuel_row = -3 if burner else None
    feed_row = -2
    in_row = -1
    furnace_top = 0
    out_row = height
    plate_row = height + 1
    last_left = (count - 1) * width
    # 带要正好停在最后一个消费格上，不许尾巴多出一格——多出来的那格会变成断头带。
    feed_length = last_left + 2
    fuel_length = last_left + width + 1
    plate_length = last_left + 2

    furnaces = primitives.machine_grid(
        blueprint, furnace, cols=count, rows=1, origin_cell=(0, furnace_top), recipe=recipe_field
    )
    primitives.belt_run(blueprint, (-1, feed_row), 4, feed_length, belt)
    if burner:
        primitives.belt_run(blueprint, (-1, fuel_row), 4, fuel_length, belt)
    primitives.belt_run(blueprint, (-1, plate_row), 4, plate_length, belt)

    for index in range(len(furnaces)):
        left = index * width
        primitives.inserter(blueprint, (left, in_row), 0, inserter)
        if burner:
            primitives.inserter(blueprint, (left + width - 1, in_row), 0, long_inserter)
        primitives.inserter(blueprint, (left, out_row), 0, inserter)

    primitives.ensure_power(blueprint, tier=pole, area=(-1, in_row, last_left + width, plate_row))

    blueprint.add_port(
        (-1, feed_row), "输入", "在这里接 %s 的进料带（向东流）" % feed, 4, feed
    )
    if burner:
        blueprint.add_port((-1, fuel_row), "输入", "在这里接燃料带（向东流）", 4, "coal")
    blueprint.add_port(
        (plate_length - 1, plate_row), "输出", "成品从这里出来，接你的出板带", 4, product
    )

    widest = max(
        feed_length - 1,
        (fuel_length - 1) if burner else 0,
        plate_length - 1,
        last_left + width - 1,
    )
    blueprint.set_build_area((-1, fuel_row if burner else feed_row), (widest, plate_row))
    if label:
        blueprint.data["label"] = label
    _annotate(blueprint, count, furnace, feed, product, burner)
    ports = {
        "feed_input_cell": [-1, feed_row],
        "fuel_input_cell": [-1, fuel_row] if burner else None,
        "product_output_cell": [plate_length - 1, plate_row],
        # 旧名字保留，免得外面的脚本一次性断掉
        "ore_input_cell": [-1, feed_row],
        "coal_input_cell": [-1, fuel_row] if burner else None,
        "plate_output_cell": [plate_length - 1, plate_row],
    }
    return {
        "entities": len(blueprint.entities),
        "furnaces": count,
        "input_item": feed,
        "output_item": product,
        "recipe_written_to_machine": recipe_field,
        "notes": notes,
        "size_cells": [widest + 1, plate_row - (fuel_row if burner else feed_row) + 1],
        "ports": ports,
    }


def _annotate(blueprint, count, furnace, feed, product, burner):
    if not blueprint.data.get("label"):
        blueprint.data["label"] = "%d 台 %s：%s → %s" % (count, furnace, feed, product)
    blueprint.data["description"] = (
        "%d 台 %s，喂 %s 出 %s。北面 y=-2 是进料带%s；南面出成品。"
        "炉子的配方由进料决定，机器上没有 recipe 字段。"
        "所有臂朝向由生成器算好（direction 指向取货侧）。"
        % (
            count,
            furnace,
            feed,
            product,
            "，y=-3 是燃料带（料和煤必须是两条带，不能串）" if burner else "（这台不用燃料带）",
        )
    )
