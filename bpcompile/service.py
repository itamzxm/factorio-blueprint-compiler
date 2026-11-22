"""MCP 工具的实现体。MCP 那一层只负责转参数，所有动作都在这里，便于不经过 MCP 直接测。

会话里维护一张"当前蓝图"：调用方不必每次把蓝图串传来传去。
"""

import functools
import json
import os

from . import blueprint as bpm
from . import geometry, lint as lintmod, primitives, protos, render, testbench as testbench_module, verify
from .templates import furnace_array, green_circuit, plastic_line

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")


def _ensure_out():
    os.makedirs(OUT_DIR, exist_ok=True)
    return OUT_DIR


def _reports_lint(method):
    """所有会改实体的写操作，返回里当场带 lint 结果。

    判据直接复用 lint 模块里已有的检查，**不另写一份**——两份判定早晚会漂移。
    `new_defects` 只列这次操作**新引入**的缺陷（对比操作前后的计数），
    所以一次正常的写操作不会因为图纸本来就有毛病而背一堆噪音。
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        before = lintmod.report(self.current)["by_code"]
        result = method(self, *args, **kwargs)
        if not isinstance(result, dict):
            return result
        payload = lintmod.report(self.current)
        result.setdefault("lint", payload["counts"])
        result.setdefault("lint_by_code", payload["by_code"])
        introduced = {
            code: count
            for code, count in payload["by_code"].items()
            if count > before.get(code, 0)
        }
        if introduced:
            result["new_defects"] = introduced
            result["new_defect_details"] = [
                defect for defect in payload["defects"] if defect["code"] in introduced
            ][:5]
        return result

    return wrapper


def code_version():
    """当前这套代码的指纹（包内最新 .py 的绝对路径 + mtime）。

    长驻进程里 import 过旧版时，调用方看不出差别——文档说做了、代码没做的错觉多半起源于此。
    把指纹打出来，就能一眼分辨"代码没做"和"我跑的是旧代码"。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    newest = (0.0, "")
    for base, _, files in os.walk(os.path.join(root, "bpcompile")):
        if "__pycache__" in base:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            stamp = os.path.getmtime(path)
            if stamp > newest[0]:
                newest = (stamp, path)
    for extra in ("mcp_server.py",):
        path = os.path.join(root, extra)
        if os.path.exists(path) and os.path.getmtime(path) > newest[0]:
            newest = (os.path.getmtime(path), path)
    import datetime

    return {
        "newest_file": os.path.relpath(newest[1], root) if newest[1] else None,
        "mtime": datetime.datetime.fromtimestamp(newest[0]).isoformat(timespec="seconds")
        if newest[0]
        else None,
        "loaded_from": os.path.dirname(os.path.abspath(__file__)),
    }


class Service:
    def __init__(self):
        self.current = bpm.Blueprint()

    # ---- 蓝图进出 ----

    def new(self, label=None):
        self.current = bpm.Blueprint(label=label)
        return self.status()

    def load(self, source):
        body = source.strip()
        if os.path.exists(body):
            with open(body, encoding="utf-8") as handle:
                body = handle.read().strip()
        self.current = bpm.load(body)
        return self.status()

    def status(self):
        summary = self.current.summary()
        bounds = summary["bounds_cells"]
        return {
            "entities": summary["entities"],
            "kinds": summary["by_name"],
            "bounds_cells": bounds,
            "size_cells": summary["size_cells"],
            "ports": [
                {"cell": list(cell), **detail} for cell, detail in (self.current.ports or {}).items()
            ],
            "build_area": list(self.current.build_area) if self.current.build_area else None,
            "code_version": code_version(),
        }

    def to_string(self):
        return self.current.to_string()

    def save(self, path=None, as_json=False):
        target = path or os.path.join(_ensure_out(), "blueprint.txt")
        directory = os.path.dirname(os.path.abspath(target))
        os.makedirs(directory, exist_ok=True)
        bpm.save(self.current, target, as_json=as_json)
        return {"path": os.path.abspath(target)}

    # ---- 查原型 ----

    def entity_info(self, name):
        proto = protos.find(name)
        if proto is None:
            from .protos import _suggest

            return {
                "ok": False,
                "error": "原型表里没有 %s" % name,
                "did_you_mean": _suggest(name, protos._data()["entities"]),
            }
        accepts, why = protos.accepts_blueprint_recipe(name)
        payload = {
            "ok": True,
            "name": name,
            "type": proto["type"],
            "size": [proto["w"], proto["h"]],
            "summary": protos.summary(name),
            "direction_accept": protos.accept_dirs(name),
            "observed_dirs_in_real_blueprints": proto.get("observed_dirs"),
            "needs_power": protos.needs_power(name),
            "energy": proto.get("energy"),
            "crafting": proto.get("crafting"),
            "direction_requires_recipe": bool(proto.get("direction_requires_recipe")),
            # 这一条是给"我要给它配个配方"的调用方看的：炉子这边压根不是这么用的。
            "accepts_blueprint_recipe": accepts,
            "why": why,
        }
        if proto.get("belt"):
            payload["belt"] = proto["belt"]
            payload["underground_variant"] = None
            if proto["type"] == "transport-belt":
                try:
                    payload["underground_variant"] = primitives.underground_variant(name)
                except Exception:
                    pass
        if proto.get("inserter"):
            payload["inserter"] = proto["inserter"]
        if proto.get("pole"):
            payload["pole"] = proto["pole"]
        if proto.get("fluids"):
            payload["fluid_ports"] = protos.fluid_connections(name)
        return payload

    def recipe_info(self, name):
        try:
            recipe = protos.recipe(name)
        except protos.UnknownName as missing:
            return {"ok": False, "error": str(missing)}
        categories = recipe.get("categories") or ["crafting"]
        machines = []
        for category in categories:
            for machine in protos.machines_for_category(category):
                if machine not in machines:
                    machines.append(machine)
        payload = {
            "ok": True,
            "name": name,
            "time": recipe.get("time"),
            "time_is_default": bool(recipe.get("time_default")),
            "in": recipe.get("in"),
            "out": recipe.get("out"),
            "categories": categories,
            "machines": machines[:12],
            "allow_productivity": recipe.get("allow_productivity"),
        }
        if "smelting" in categories:
            feed = (recipe.get("in") or [["?"]])[0][0]
            payload["how_to_use_in_blueprint"] = (
                "这个配方只能靠**进料**选中：把 %r 送到炉子的进料带上，别去给炉子写 recipe "
                "（原版炉子不认那个字段，写了也是喂什么出什么）" % feed
            )
            payload["machines_that_need_input_not_recipe"] = [
                m for m in machines if not protos.accepts_blueprint_recipe(m)[0]
            ]
        return payload

    def find_recipes(self, product, include_recycling=False):
        names = protos.recipes_by_product(product)
        rows = []
        for name in names:
            categories = protos.recipe(name).get("categories") or ["crafting"]
            if not include_recycling and "recycling" in categories:
                continue
            rows.append({"name": name, "categories": categories})
        return {"ok": bool(rows), "product": product, "recipes": rows, "hidden_recycling": len(names) - len(rows)}

    # ---- 原语 ----

    @_reports_lint
    def add_entity(
        self,
        name,
        position,
        direction=None,
        recipe=None,
        allow_bare_direction=False,
        force=False,
        **fields
    ):
        try:
            entity = self.current.add(
                name,
                position,
                direction,
                on_conflict="report" if force else "raise",
                recipe=recipe,
                allow_bare_direction=allow_bare_direction,
                **fields
            )
        except bpm.GeometryConflict as conflict:
            return {"ok": False, "error": str(conflict), "conflict": conflict.conflict}
        except bpm.BlueprintError as failure:
            return {"ok": False, "error": str(failure)}
        payload = {
            "ok": True,
            "entity_number": entity["entity_number"],
            "position": [entity["position"]["x"], entity["position"]["y"]],
            "cells": [list(cell) for cell in sorted(self.current.cells_of(entity))],
        }
        if entity.get("_conflicts"):
            payload["ok"] = False
            payload["overlaps"] = [
                {"cell": list(item["cell"]), "name": item["name"], "entity_number": item["entity_number"]}
                for item in entity["_conflicts"]
            ]
            payload["error"] = "强制放进去了，但它压到了别的实体；跑 bp_lint 看完整缺陷"
        return payload

    @_reports_lint
    def remove_at(self, cell):
        removed = self.current.remove_at(tuple(cell))
        return {"ok": True, "removed": removed}

    @_reports_lint
    def belt(self, start_cell, direction="east", length=1, tier="transport-belt", underground=()):
        try:
            entities = primitives.belt_run(
                self.current,
                tuple(start_cell),
                direction,
                length,
                tier,
                underground=tuple(tuple(spec) for spec in underground),
            )
        except bpm.BlueprintError as failure:
            return {"ok": False, "error": str(failure)}
        return {
            "ok": True,
            "placed": len(entities),
            "cells": [list(cell) for cell in primitives.belt_line_cells(tuple(start_cell), direction, length)],
        }

    @_reports_lint
    def inserter(self, cell, direction="north", tier="inserter"):
        try:
            entity = primitives.inserter(self.current, tuple(cell), direction, tier)
        except bpm.BlueprintError as failure:
            return {"ok": False, "error": str(failure)}
        return {"ok": True, "entity_number": entity["entity_number"], "direction": entity.get("direction", 0)}

    @_reports_lint
    def inserter_between(self, cell, take_from, put_into, tier="inserter"):
        try:
            entity = primitives.inserter_between(
                self.current, tuple(cell), tuple(take_from), tuple(put_into), tier
            )
        except bpm.BlueprintError as failure:
            return {"ok": False, "error": str(failure)}
        return {"ok": True, "entity_number": entity["entity_number"], "direction": entity.get("direction", 0)}

    @_reports_lint
    def inserter_pair(self, machine_cell, load_from, unload_to, tier="inserter"):
        try:
            machine = primitives.find_entity(self.current, tuple(machine_cell))
            pair = primitives.inserter_pair(
                self.current, machine, tuple(load_from), tuple(unload_to), tier
            )
        except bpm.BlueprintError as failure:
            return {"ok": False, "error": str(failure)}
        return {
            "ok": True,
            "inserters": [
                {
                    "entity_number": entity["entity_number"],
                    "direction": entity.get("direction", 0),
                    "cells": [list(cell) for cell in sorted(self.current.cells_of(entity))],
                }
                for entity in pair
            ],
        }

    @_reports_lint
    def machine_grid(self, machine, cols, rows, origin_cell, direction="north", gap=(0, 0), recipe=None):
        try:
            placed = primitives.machine_grid(
                self.current,
                machine,
                cols,
                rows,
                tuple(origin_cell),
                direction,
                tuple(gap),
                recipe,
            )
        except bpm.BlueprintError as failure:
            return {"ok": False, "error": str(failure)}
        return {"ok": True, "placed": len(placed), "first": placed[0]["entity_number"]}

    @_reports_lint
    def power(self, pole="medium-electric-pole", area=None):
        try:
            placed = primitives.ensure_power(
                self.current, pole, tuple(area) if area else None
            )
        except bpm.BlueprintError as failure:
            return {"ok": False, "error": str(failure)}
        return {
            "ok": True,
            "placed": len(placed),
            "cells": [list(cell) for cell in (sorted(self.current.cells_of(e))[0] for e in placed)],
        }

    @_reports_lint
    def furnace_array(
        self,
        count=24,
        furnace="stone-furnace",
        input_item=None,
        output_item=None,
        recipe=None,
        label=None,
        **options
    ):
        try:
            info = furnace_array(
                self.current,
                count=count,
                furnace=furnace,
                input_item=input_item,
                output_item=output_item,
                recipe=recipe,
                label=label,
                **options
            )
        except (bpm.BlueprintError, ValueError) as failure:
            return {
                "ok": False,
                "error": str(failure),
                "hint": "查一下 bp_recipe <产物> 的 in 字段：炉子的产物由进料决定，两个参数必须对得上",
            }
        self.current.normalize()
        # lint 由 _reports_lint 统一补，别在这儿再来一份（两份判定早晚漂移）
        return {"ok": True, **info}

    # ---- 检查 ----

    def lint(self, min_severity="note"):
        payload = lintmod.report(self.current, min_severity=min_severity)
        return payload

    def verify(self, modes=("lint", "rig"), run_seconds=None, powered_single=False):
        if isinstance(modes, str):
            modes = tuple(part.strip() for part in modes.split(",") if part.strip())
        else:
            modes = tuple(modes)
        return verify.verify(
            self.current, modes, run_seconds=run_seconds, powered_single=powered_single
        )

    def render(self, path=None, cell_px=18, notes=()):
        if not path:
            path = os.path.join(_ensure_out(), "preview.png")
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        payload_notes = []
        for cell, text in notes:
            payload_notes.append((tuple(cell), text))
        for cell, detail in (self.current.ports or {}).items():
            payload_notes.append((cell, detail.get("note") or detail.get("role", "")))
        render.render(self.current, path, cell_px=cell_px, notes=payload_notes)
        return {"ok": True, "path": os.path.abspath(path)}

    @_reports_lint
    def green_circuit(self, target_rate=2.0, machine="assembling-machine-1", label=None):
        """一条绿板产线。台数由 target_rate 从真值表算出来，不写死。"""
        try:
            info = green_circuit(
                self.current, target_rate=target_rate, machine=machine, label=label
            )
        except (bpm.BlueprintError, ValueError) as failure:
            return {"ok": False, "error": str(failure)}
        self.current.normalize()
        return {"ok": True, **info}

    @_reports_lint
    def plastic_line(self, target_rate=2.0, machine="chemical-plant", label=None):
        """一条塑料线。气从北侧角格经地下管跨过煤带进来。"""
        try:
            info = plastic_line(
                self.current, target_rate=target_rate, machine=machine, label=label
            )
        except (bpm.BlueprintError, ValueError) as failure:
            return {"ok": False, "error": str(failure)}
        self.current.normalize()
        return {"ok": True, **info}

    @_reports_lint
    def testbench(self, seconds=60, path=None, product=None, arms=None):
        """把当前蓝图包成自测版（夹具接好），单独出文件，不覆盖正式产物。

        arms 是每个口配几只臂：一只快速臂约 2.3 物品/秒，进料需求高的产线要加。
        """
        try:
            options = {"arms": arms} if arms else {}
            clone, manifest = testbench_module.build_testbench(
                self.current, seconds=seconds, product=product, **options
            )
        except bpm.BlueprintError as failure:
            return {"ok": False, "error": str(failure)}
        clone.normalize()
        target = path or os.path.join(_ensure_out(), "testbench.blueprint.txt")
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        bpm.save(clone, target)
        lint_payload = lintmod.report(clone)
        return {
            "ok": lint_payload["counts"]["error"] == 0,
            "path": os.path.abspath(target),
            "blueprint_string": clone.to_string(),
            "manifest": manifest,
            "lint": lint_payload["counts"],
            "by_code": lint_payload["by_code"],
        }

    def declare_port(self, cell, role="输出", note="", direction=None, item=None):
        """登记对外接线口。蓝图串里存不下这个信息，所以载入后要重新声明一次。

        direction 与 item 让端口可执行：自测版靠它们自动接夹具。
        """
        detail = self.current.add_port(tuple(cell), role, note, direction, item)
        return {"ok": True, "cell": list(cell), **detail}

    @_reports_lint
    def normalize(self):
        report = self.current.normalize()
        return {"ok": True, **report}
