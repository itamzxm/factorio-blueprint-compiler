"""蓝图数据模型：加载、保存、增删实体、坐标规范化、编码解码。

编码层自己实现（8 行 zlib+base64），不用 factorio-draftsman。理由见 doc/选型-为什么不用draftsman.md：
draftsman 在解码时会把超过 200 字节的 label 静默截断，改造类任务（原样读入、只改该改的地方）会丢数据。
"""

import base64
import collections
import json
import zlib

from . import geometry, protos

# 2.1.24.1 —— version == (major<<48)|(minor<<32)|(patch<<16)|dev，见 doc/蓝图格式与坑.md 第二节。
DEFAULT_VERSION = 562954249961473


class BlueprintError(Exception):
    pass


class UnknownEntity(BlueprintError):
    pass


class GeometryConflict(BlueprintError):
    def __init__(self, message, conflict=None):
        super().__init__(message)
        self.conflict = conflict or {}


def encode(obj, version_char="0"):
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    return version_char + base64.b64encode(zlib.compress(text.encode("utf-8"), 9)).decode("ascii")


def decode(text):
    body = text.strip()
    if not body:
        raise BlueprintError("蓝图字符串为空")
    payload = body[1:] if body[0] in "0123456789" else body
    try:
        raw = zlib.decompress(base64.b64decode(payload, validate=False))
    except Exception as exc:
        raise BlueprintError(
            "蓝图字符串解不开（%s）。常见原因：复制时被截断、夹了中文、或不是蓝图串" % exc
        ) from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise BlueprintError("解出来的不是合法 JSON：%s" % exc) from exc


def _clean(value):
    value = round(float(value) * 2.0) / 2.0
    return int(value) if value == int(value) else value


class Blueprint:
    """一张蓝图。实体按写入顺序保存，坐标用连续格坐标（中心）。"""

    def __init__(self, label=None, description=None, version=DEFAULT_VERSION):
        self.data = {"item": "blueprint", "version": version, "entities": []}
        if label:
            self.data["label"] = label
        if description:
            self.data["description"] = description
        self.build_area = None
        # 对外接线口：{(cell): {"role": ..., "note": ...}}。lint 不把这些地方当断头带。
        self.ports = {}
        self._cells = collections.defaultdict(list)

    # ---- 构造 ----

    @classmethod
    def from_dict(cls, payload):
        if "blueprint_book" in payload:
            raise BlueprintError("这是蓝图册，不是单张蓝图；先用蓝图册里的一张")
        body = payload.get("blueprint", payload)
        if not isinstance(body, dict) or "entities" not in body:
            raise BlueprintError("解出来的 JSON 里没有 entities，认不出这是蓝图")
        blueprint = cls()
        blueprint.data = body
        blueprint._reindex()
        return blueprint

    @classmethod
    def from_string(cls, text):
        return cls.from_dict(decode(text))

    def copy(self):
        clone = Blueprint()
        clone.data = json.loads(json.dumps(self.data))
        clone.build_area = self.build_area
        clone.ports = dict(self.ports)
        clone._reindex()
        return clone

    # ---- 连线 ----

    @property
    def wires(self):
        return self.data.setdefault("wires", [])

    def add_wire(self, number_a, connector_a, number_b, connector_b):
        """加一根线。连接点编号：5/6 = 铜线（电力），1/2 = 红/绿信号线。

        **线必须写在蓝图里**，游戏不会替你连。少了这段，一门炮塔对一门炮塔就是两座孤岛。
        """
        if connector_a not in (1, 2, 3, 4, 5, 6) or connector_b not in (1, 2, 3, 4, 5, 6):
            raise BlueprintError(
                "连接点编号只能是 1-6（5/6 铜线，1/2 红/绿线），拿到 %r/%r"
                % (connector_a, connector_b)
            )
        entry = [number_a, connector_a, number_b, connector_b]
        mirror = [number_b, connector_b, number_a, connector_a]
        if entry not in self.wires and mirror not in self.wires:
            self.wires.append(entry)
        return entry

    def copper_wires(self):
        """只挑铜线（电力）那部分，返回 [(实体号A, 实体号B)]。"""
        by_number = {e.get("entity_number"): e for e in self.data["entities"]}
        out = []
        for wire in self.data.get("wires") or []:
            if len(wire) != 4:
                continue
            a, conn_a, b, conn_b = wire
            if conn_a not in (5, 6) or conn_b not in (5, 6):
                continue
            if a in by_number and b in by_number:
                out.append((a, b))
        return out

    def add_port(self, cell, role, note="", direction=None, item=None):
        """登记一个对外接线口。role：输入 / 输出。

        direction 是这个口上的物流方向（带往哪边走），item 是走这个口的物品。
        这两个字段让端口**可执行**——自测版靠它们自动接夹具，不用人肉接线。
        """
        detail = {"role": role, "note": note}
        if direction is not None:
            detail["direction"] = geometry.canonical(direction)
        if item:
            detail["item"] = item
        self.ports[tuple(cell)] = detail
        return detail

    # ---- 导出 ----

    def to_dict(self):
        """蓝图串解出来的那一层结构：顶层必须是 {"blueprint": {...}}，游戏只认这个形状。"""
        return {"blueprint": self.data}

    def to_string(self, version_char="0"):
        return encode(self.to_dict(), version_char)

    def to_json(self, indent=None):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    # ---- 索引 ----

    def _reindex(self):
        self._cells = collections.defaultdict(list)
        for entity in self.data["entities"]:
            for cell in self.cells_of(entity):
                self._cells[cell].append(entity)

    @property
    def entities(self):
        return self.data["entities"]

    def __len__(self):
        return len(self.data["entities"])

    def cells_of(self, entity):
        proto = protos.find(entity["name"])
        if proto is None:
            return frozenset()
        position = entity["position"]
        return geometry.cells_of(
            position["x"], position["y"], proto["w"], proto["h"], entity.get("direction", 0)
        )

    def at(self, cell):
        return list(self._cells.get(cell, ()))

    def next_number(self):
        numbers = [e.get("entity_number", 0) for e in self.data["entities"]]
        return (max(numbers) + 1) if numbers else 1

    def bounds(self):
        """返回格坐标包围盒 (min_i, min_j, max_i, max_j)；空蓝图返回 None。"""
        cells = [cell for entity in self.data["entities"] for cell in self.cells_of(entity)]
        if not cells:
            return None
        xs = [cell[0] for cell in cells]
        ys = [cell[1] for cell in cells]
        return (min(xs), min(ys), max(xs), max(ys))

    # ---- 增删 ----

    def add(
        self,
        name,
        position,
        direction=None,
        *,
        on_conflict="raise",
        allow_bare_direction=False,
        **fields
    ):
        """加一个实体。position 是实体中心；会被挪到该尺寸合法的奇偶上。

        on_conflict: raise（默认，撞了就抛） / report（照加，回执里带 conflicts） / ignore。
        """
        proto = protos.find(name)
        if proto is None:
            near = protos._suggest(name, _entity_table())
            raise UnknownEntity(
                "原型表里没有实体 %r（只收官方 base/space-age/quality/elevated-rails/recycler）%s"
                % (name, ("；最接近的是 " + " / ".join(near)) if near else "")
            )
        direction_value = geometry.normalize_direction(direction) if direction is not None else None
        if (
            direction_value
            and protos.direction_requires_recipe(name)
            and not fields.get("recipe")
            and not allow_bare_direction
        ):
            raise BlueprintError(
                "%s 设了 direction=%d 却没给 recipe。这类机器只有在同时写了 recipe 时方向才生效，"
                "不写 recipe 游戏会把 direction 读回 0——液口接反、进料口朝错，游戏不报任何错。"
                "要么补 recipe=...，要么把方向设成 0（确实只当摆设时传 allow_bare_direction=True）"
                % (name, direction_value)
            )
        x, y = geometry.snapped_center(
            float(position[0]),
            float(position[1]),
            proto["w"],
            proto["h"],
            direction_value or 0,
        )
        entity = {"name": name, "position": {"x": _clean(x), "y": _clean(y)}}
        if direction_value:
            entity["direction"] = direction_value
        for key, value in fields.items():
            if value is not None:
                entity[key] = value
        entity["entity_number"] = self.next_number()

        cells = self.cells_of(entity)
        conflicts = [
            {"cell": cell, "entity_number": other["entity_number"], "name": other["name"]}
            for cell in sorted(cells)
            for other in self._cells.get(cell, ())
        ]
        if conflicts and on_conflict == "raise":
            detail = conflicts[0]
            raise GeometryConflict(
                "%s 放在 %s 会压到 %s（#%d），冲突格 %s。换一格或先删掉那个实体"
                % (
                    name,
                    geometry.format_position((entity["position"]["x"], entity["position"]["y"])),
                    detail["name"],
                    detail["entity_number"],
                    geometry.format_cell(detail["cell"]),
                ),
                {"conflicts": conflicts, "cells": sorted(cells)},
            )

        self.data["entities"].append(entity)
        for cell in cells:
            self._cells[cell].append(entity)
        if conflicts and on_conflict == "ignore":
            return entity
        if conflicts:
            entity["_conflicts"] = conflicts
        return entity

    def add_spanning(self, name, top_left_cell, direction=None, **fields):
        """按"左上角格"放置多格实体。"""
        proto = protos.entity(name)
        width, height = geometry.footprint(proto["w"], proto["h"], direction or 0)
        center = (top_left_cell[0] + width / 2.0, top_left_cell[1] + height / 2.0)
        return self.add(name, center, direction, **fields)

    def remove(self, entity_number):
        target = next(
            (e for e in self.data["entities"] if e.get("entity_number") == entity_number), None
        )
        if target is None:
            return False
        for cell in self.cells_of(target):
            bucket = self._cells.get(cell)
            if not bucket:
                continue
            self._cells[cell] = [e for e in bucket if e is not target]
            if not self._cells[cell]:
                del self._cells[cell]
        self.data["entities"].remove(target)
        return True

    def remove_at(self, cell):
        removed = [e["entity_number"] for e in self.at(cell)]
        for number in removed:
            self.remove(number)
        return removed

    # ---- 规范化 ----

    def normalize(self):
        """把坐标/方向/编号规整到游戏接受的形式，返回改了什么。"""
        report = {
            "snapped": [],
            "directions_dropped": [],
            "renumbered": False,
            "icons": False,
            "wires_dropped": 0,
        }
        for entity in self.data["entities"]:
            proto = protos.find(entity["name"])
            if proto is None:
                continue
            position = entity["position"]
            direction = entity.get("direction", 0)
            wanted = geometry.snapped_center(
                position["x"], position["y"], proto["w"], proto["h"], direction
            )
            if (wanted[0], wanted[1]) != (position["x"], position["y"]):
                report["snapped"].append(
                    {
                        "entity_number": entity.get("entity_number"),
                        "name": entity["name"],
                        "from": [position["x"], position["y"]],
                        "to": [_clean(wanted[0]), _clean(wanted[1])],
                    }
                )
                entity["position"] = {"x": _clean(wanted[0]), "y": _clean(wanted[1])}
            if "direction" in entity and entity["direction"] % 16 == 0:
                del entity["direction"]
        numbers = [e.get("entity_number") for e in self.data["entities"]]
        if len(set(numbers)) != len(numbers) or any(not isinstance(n, int) for n in numbers):
            for index, entity in enumerate(self.data["entities"], start=1):
                entity["entity_number"] = index
            report["renumbered"] = True
        if not self.data.get("icons"):
            self.data["icons"] = self._guess_icons()
            report["icons"] = True
        known = {e.get("entity_number") for e in self.data["entities"]}
        if self.data.get("wires"):
            kept = [w for w in self.data["wires"] if len(w) == 4 and w[0] in known and w[2] in known]
            report["wires_dropped"] = len(self.data["wires"]) - len(kept)
            if kept:
                self.data["wires"] = kept
            else:
                del self.data["wires"]
        self._reindex()
        return report

    def _guess_icons(self):
        counter = collections.Counter(e["name"] for e in self.data["entities"])
        top = [name for name, _ in counter.most_common(4)]
        return [{"signal": {"name": name}, "index": index + 1} for index, name in enumerate(top)] or [
            {"signal": {"name": "stone-furnace"}, "index": 1}
        ]

    def set_build_area(self, min_cell, max_cell):
        """声明这块蓝图的占地（格坐标，闭区间）。lint 用它判"越出声明范围"。"""
        self.build_area = (
            min(min_cell[0], max_cell[0]),
            min(min_cell[1], max_cell[1]),
            max(min_cell[0], max_cell[0]),
            max(min_cell[1], max_cell[1]),
        )

    def summary(self):
        counter = collections.Counter(e["name"] for e in self.data["entities"])
        bounds = self.bounds()
        return {
            "entities": len(self.data["entities"]),
            "kinds": len(counter),
            "by_name": dict(counter.most_common()),
            "bounds_cells": bounds,
            "size_cells": [bounds[2] - bounds[0] + 1, bounds[3] - bounds[1] + 1] if bounds else [0, 0],
        }


def _entity_table():
    return protos._data()["entities"]


def load(text):
    """从蓝图字符串或蓝图 JSON（文件内容）加载。"""
    body = text.strip()
    if body.startswith("{"):
        return Blueprint.from_dict(json.loads(body))
    return Blueprint.from_string(body)


def save(blueprint, path, as_json=False):
    with open(path, "w", encoding="utf-8") as handle:
        if as_json:
            handle.write(blueprint.to_json(indent=1))
        else:
            handle.write(blueprint.to_string())
    return path
