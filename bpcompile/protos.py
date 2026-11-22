"""原型真值表查询入口。唯一数据源：data/prototypes.json。"""

import difflib
import json
import os

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "prototypes.json")

# 地下管的跨度不在 prototypes.json 里（它由 pipe_connections 的 max_underground_distance 给出，
# 而真值表只抓了流体端口没抓这个字段）。数值取自 data-raw-dump.json：
#   pipe-to-ground.fluid_box.pipe_connections[1].max_underground_distance == 10
# 换游戏版本后要回来核这一条。
PIPE_TO_GROUND_SPAN = 10

_DIRECTION_FALLBACK = [0, 4, 8, 12]

_CACHE = {}


def _data():
    if "all" not in _CACHE:
        with open(DATA_PATH, encoding="utf-8") as handle:
            _CACHE["all"] = json.load(handle)
    return _CACHE["all"]


def reload():
    _CACHE.clear()


class UnknownName(KeyError):
    pass


def _suggest(name, table, count=3):
    return difflib.get_close_matches(str(name), list(table), n=count, cutoff=0.5)


def entity(name):
    """取实体原型。名字不在原版表里就抛 UnknownName，并附最接近的候选。"""
    table = _data()["entities"]
    if name in table:
        return table[name]
    raise UnknownName(
        "原型表里没有实体 %r（只收 base/core/space-age/quality/elevated-rails/recycler，不含 mod）%s"
        % (name, _hint(name, table))
    )


def _hint(name, table):
    near = _suggest(name, table)
    return ("；最接近的是 " + " / ".join(near)) if near else ""


def find(name):
    return _data()["entities"].get(name)


def exists(name):
    return name in _data()["entities"]


def is_fluid(name):
    """是不是流体。

    **必须查 fluids 表**：`petroleum-gas` / `water` 这些只存在于 fluids，
    不在 entities 里；而 `crude-oil` 两边都有（entities 里是它的桶/矿）。
    用 `find()` 判流体会漏掉前者、错判后者。
    """
    return name in (_data().get("fluids") or {})


def size(name):
    proto = entity(name)
    return (proto["w"], proto["h"])


def entity_type(name):
    return entity(name)["type"]


def accept_dirs(name):
    """校验器必须放行的 direction 集合。

    真实蓝图里就存在 assembling-machine-3 带 direction=12 这类摆法，而真机探针把一堆机器的
    可旋转档数读成 0。按 dirs 判非法会误杀真蓝图，所以放行集合统一取 direction_accept，
    查不到就是 0/4/8/12。
    """
    proto = entity(name)
    accept = proto.get("direction_accept")
    if accept:
        return list(accept)
    return list(_DIRECTION_FALLBACK)


def generation_dirs(name):
    """生成时可用哪些方向：优先 observed_dirs（真实蓝图写过哪些），否则退回全量。"""
    proto = entity(name)
    observed = proto.get("observed_dirs")
    if observed:
        return list(observed)
    return list(_DIRECTION_FALLBACK)


def belt_speed(name):
    proto = find(name)
    if not proto:
        return None
    belt = proto.get("belt") or {}
    return belt.get("speed")


def underground_span(name):
    """地下件能跨多少格（输入格到输出格的轴向距离上限）。非地下件返回 None。"""
    proto = find(name)
    if not proto:
        return None
    if proto["type"] == "underground-belt":
        return (proto.get("belt") or {}).get("max_distance")
    if proto["type"] == "pipe-to-ground":
        return PIPE_TO_GROUND_SPAN
    return None


def accepts_blueprint_recipe(name):
    """这台机器认不认蓝图里的 recipe 字段。返回 (bool, 人话原因)。

    原版炉子（stone/steel/electric）**不认**：游戏自己导出的炉子蓝图里只有 name 和 position，
    产物由进料决定。组配机类（assembling-machine / foundry）认。
    """
    proto = find(name)
    if proto is None:
        return (None, "原型表里没有 %r" % name)
    kind = proto["type"]
    if kind == "furnace":
        return (
            False,
            "原版炉子（stone/steel/electric）在蓝图里不带 recipe，"
            "产物由**进料**决定：喂铁矿石出铁板、喂铁板出钢板",
        )
    if kind == "assembling-machine":
        return (True, "组配机类机器（含 foundry）认 blueprint 里的 recipe")
    if kind == "rocket-silo":
        return (True, "火箭发射井认 recipe")
    return (False, "%s 类实体没有配方槽" % kind)


def is_underground(name):
    return underground_span(name) is not None


def is_belt_like(name):
    proto = find(name)
    if not proto:
        return False
    return proto["type"] in ("transport-belt", "underground-belt", "splitter", "loader", "linked-belt")


def is_inserter(name):
    proto = find(name)
    return bool(proto) and proto["type"] == "inserter"


def inserter_reach(name):
    """(取货距离, 放货距离)，单位格。"""
    proto = entity(name)
    reach = proto.get("inserter") or {}
    pickup = reach.get("pickup") or [0, -1]
    insert = reach.get("insert") or [0, 1.2]
    return (abs(pickup[1]), abs(insert[1]))


def pole_supply(name):
    proto = find(name)
    if not proto or proto["type"] != "electric-pole":
        return None
    return (proto.get("pole") or {}).get("supply_area")


def pole_wire(name):
    proto = find(name)
    if not proto or proto["type"] != "electric-pole":
        return None
    return (proto.get("pole") or {}).get("wire_distance")


def energy_type(name):
    proto = find(name)
    if not proto:
        return None
    return (proto.get("energy") or {}).get("type")


def needs_power(name):
    """这台机器是否要靠电线杆供电。燃烧类（钻头/炉子）不需要。"""
    if energy_type(name) != "electric":
        return False
    proto = find(name)
    return proto["type"] not in ("electric-pole", "electric-energy-interface")


def crafting_speed(name):
    proto = find(name)
    if not proto:
        return None
    return (proto.get("crafting") or {}).get("speed")


def crafting_categories(name):
    proto = find(name)
    if not proto:
        return []
    return (proto.get("crafting") or {}).get("categories") or []


def fluid_connections(name):
    """机器的流体端口：position 是朝北坐标下的相对格偏移，direction 是接出方向。"""
    proto = find(name)
    if not proto:
        return []
    out = []
    for box in proto.get("fluids") or []:
        for conn in box.get("c") or []:
            out.append(
                {
                    "position": tuple(conn.get("p") or (0, 0)),
                    "direction": conn.get("d", 0),
                    "underground": bool(conn.get("underground")),
                    "flow": conn.get("flow"),
                    "io": box.get("io"),
                }
            )
    return out


def direction_requires_recipe(name):
    """写非 0 方向时是否必须同时写 recipe。

    只有真机实测确认过的实体带 direction_requires_recipe 标记：foundry 与 cryogenic-plant。
    裸建时游戏把 direction 读回 0，写上 recipe 才认。
    **不要用"有没有配方槽""方不方"去推**——化工厂是方的但裸建也认方向，方形机器（EM plant /
    biochamber）根本不能转。只认这个字段。
    """
    proto = find(name)
    if not proto:
        return False
    return bool(proto.get("direction_requires_recipe"))


def storage_slots(name):
    proto = find(name)
    if not proto:
        return None
    return (proto.get("storage") or {}).get("slots")


def recipe(name):
    table = _data()["recipes"]
    if name in table:
        return table[name]
    raise UnknownName("配方表里没有 %r%s" % (name, _hint(name, table)))


def recipes_in_category(category):
    """某类别下所有配方名。炉子只跑 smelting，配比计算靠这个。"""
    out = []
    for name, recipe in _data()["recipes"].items():
        if category in (recipe.get("categories") or []):
            out.append(name)
    return out


def recipes_by_product(item):
    return list((_data().get("recipes_by_product") or {}).get(item) or [])


def machines_for_category(category):
    return list((_data().get("machines_by_category") or {}).get(category) or [])


def recipe_categories():
    return list(_data().get("recipe_categories") or [])


def placeable_names():
    return [name for name, proto in _data()["entities"].items() if proto.get("placeable")]


def summary(name):
    """一句话摘要，给人看/给模型看。"""
    proto = entity(name)
    parts = ["%s（%s）" % (name, proto["type"]), "%dx%d" % (proto["w"], proto["h"])]
    if needs_power(name):
        parts.append("需电 %s" % (proto.get("energy") or {}).get("usage", "?"))
    else:
        kind = energy_type(name)
        if kind:
            parts.append("%s 燃料" % kind)
    speed = crafting_speed(name)
    if speed:
        parts.append("速度 %g" % speed)
    span = underground_span(name)
    if span:
        parts.append("跨 %d 格" % span)
    supply = pole_supply(name)
    if supply:
        parts.append("供电 %.1f 格 / 线 %.1f 格" % (supply, pole_wire(name) or 0))
    return "，".join(parts)
