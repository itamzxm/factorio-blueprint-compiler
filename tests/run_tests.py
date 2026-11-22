#!/usr/bin/env python3
"""核心库自检。不依赖 pytest：python tests/run_tests.py

每条测试注明它在保护什么。防的是**已经发生过的**故障，不是想象出来的。
"""

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bpcompile import blueprint as bpm
from bpcompile import geometry, lint as lintmod, primitives, protos, verify
from bpcompile.templates import furnace_array

CORPUS = os.path.join(ROOT, "data", "corpus")
# 仓库带的是 .blueprint.txt 原串；.json 是解码产物（没进版本库），缺了就用原串现场解。
# 两个都没有时，引用语料的用例**跳过**而不是假过——跳过数会写进总结。
HAS_CORPUS = os.path.isdir(CORPUS) and bool(
    glob.glob(os.path.join(CORPUS, "*.json"))
    or glob.glob(os.path.join(CORPUS, "*.blueprint.txt"))
)
CORPUS_MISSING = (
    "蓝图语料不在（%s 不存在或是空的）：放进 .blueprint.txt 就能跑，见 data/corpus/README.md"
    % CORPUS
)

_FAILED = []
_SKIPPED = []
_PASSED = 0


class SkipTest(Exception):
    """这条用例在当前环境下跑不了（缺样本、缺可选依赖）：跳过，不算通过。"""


def check(name, fn):
    global _PASSED
    try:
        fn()
        _PASSED += 1
        print("  ok   %s" % name)
    except SkipTest as reason:
        _SKIPPED.append((name, str(reason)))
        print("  skip %s\n       %s" % (name, reason))
    except AssertionError as failure:
        _FAILED.append((name, str(failure)))
        print("  FAIL %s\n       %s" % (name, failure))
    except Exception as failure:  # noqa: BLE001
        _FAILED.append((name, "%s: %s" % (type(failure).__name__, failure)))
        print("  FAIL %s\n       %s: %s" % (name, type(failure).__name__, failure))


def corpus_blueprints():
    """语料：有解码后的 .json 就用它，否则把 .blueprint.txt 现场解出来。

    仓库只带原串（168 KB）。想要 .json（2.8 MB）跑 tools/decode_corpus.py。
    """
    paths = {}
    for path in glob.glob(os.path.join(CORPUS, "*.json")):
        paths[os.path.splitext(os.path.basename(path))[0]] = path
    for path in glob.glob(os.path.join(CORPUS, "*.blueprint.txt")):
        stem = os.path.basename(path)[: -len(".blueprint.txt")]
        paths.setdefault(stem, path)
    for stem in sorted(paths):
        path = paths[stem]
        if path.endswith(".json"):
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            if "blueprint" not in payload:
                continue
            blueprint = bpm.Blueprint.from_dict(payload)
        else:
            with open(path, encoding="utf-8") as handle:
                payload = bpm.decode(handle.read())
            if "blueprint" not in payload:
                continue
            blueprint = bpm.Blueprint.from_dict(payload)
        yield stem + ".json", blueprint


# ---- 编码层 -------------------------------------------------------------


def test_codec_roundtrip():
    """编码必须是可逆的：解出来再写回去，实体逐字段一致。"""
    if not HAS_CORPUS:
        raise SkipTest(CORPUS_MISSING)
    for name, original in corpus_blueprints():
        text = original.to_string()
        again = bpm.Blueprint.from_string(text)
        assert len(again.entities) == len(original.entities), name
        for before, after in zip(original.entities, again.entities):
            assert before["name"] == after["name"], name
            assert before["position"] == after["position"], name
            assert before.get("direction") == after.get("direction"), name


def test_envelope_shape():
    """顶层必须是 {"blueprint": ...}。少了这层，游戏和真机工具都认不出（踩过）。"""
    blueprint = bpm.Blueprint()
    blueprint.add("transport-belt", (0.5, 0.5), 4)
    payload = json.loads(json.dumps(blueprint.to_dict()))
    assert "blueprint" in payload, "顶层缺 blueprint 包装"
    decoded = bpm.decode(blueprint.to_string())
    assert "blueprint" in decoded


def test_cross_decoder_with_draftsman():
    """用自己的编码器写、用 factorio-draftsman 读——两套独立实现互证。

    注意 draftsman 的 API：Blueprint(str) 会把字符串当 label，实体数是 0，看着像"解出了空蓝图"。
    必须用 get_blueprintable_from_string。（这个坑本身也说明不该把它当产品路径的解码器。）
    """
    try:
        import draftsman.blueprintable as draftsman_blueprintable
    except Exception:
        raise SkipTest("本解释器没装 factorio-draftsman（可选依赖，见 README 安装一节）")
    blueprint = bpm.Blueprint()
    furnace_array(blueprint, count=4)
    blueprint.normalize()
    decoded = draftsman_blueprintable.get_blueprintable_from_string(blueprint.to_string())
    assert len(decoded.entities) == len(blueprint.entities), (
        "draftsman 读回 %d 个实体，我们写了 %d 个" % (len(decoded.entities), len(blueprint.entities))
    )


# ---- 几何 ---------------------------------------------------------------


def test_footprint_swaps_on_east_west():
    """3x3 转 90° 不该变；2x1 转 90° 要换成 1x2。"""
    assert geometry.footprint(3, 3, 4) == (3, 3)
    assert geometry.footprint(2, 1, 4) == (1, 2)
    assert geometry.footprint(2, 1, 12) == (1, 2)


def test_cells_of_two_by_two():
    """2x2 落在整数坐标时占 4 格；落在 .5 上要被识别成没吸附。"""
    cells = geometry.cells_of(4, 0, 2, 2, 0)
    assert cells == frozenset({(3, -1), (4, -1), (3, 0), (4, 0)}), cells
    assert geometry.is_snapped(4, 0, 2, 2, 0)
    assert not geometry.is_snapped(4.5, 0, 2, 2, 0)


def test_canonical_direction_handles_rail_values():
    """轨道信号用 0-15 全量；几何计算必须按象限收敛，不能抛错（踩过）。"""
    for raw in range(16):
        assert geometry.canonical(raw) in (0, 4, 8, 12)


# ---- lint ---------------------------------------------------------------


def test_overlap_is_pinpointed():
    """两座炉子叠在一起：必须报出重叠、给出格坐标和双方实体号。"""
    blueprint = bpm.Blueprint()
    blueprint.set_build_area((0, 0), (10, 10))
    first = blueprint.add("stone-furnace", (1, 1))
    blueprint.add("stone-furnace", (1, 1), on_conflict="report")
    defects = [d for d in lintmod.lint(blueprint) if d["code"] == "overlap"]
    assert len(defects) == 1, "一对重叠实体应该只报一条，实际 %d" % len(defects)
    assert all(d["severity"] == "error" for d in defects)
    assert len(defects[0]["cells"]) == 4, "2x2 对 2x2 应该占 4 个共格，实际 %s" % defects[0]["cells"]
    assert defects[0]["cells"][0] == [0, 0], defects[0]["cells"]
    assert first["entity_number"] in defects[0]["entities"]
    assert "%d" % first["entity_number"] in defects[0]["message"]


def test_out_of_bounds():
    blueprint = bpm.Blueprint()
    blueprint.set_build_area((0, 0), (3, 3))
    blueprint.add("transport-belt", (0.5, 0.5), 4)
    blueprint.add("transport-belt", (9.5, 0.5), 4)
    defects = [d for d in lintmod.lint(blueprint) if d["code"] == "out-of-bounds"]
    assert len(defects) == 1, defects
    assert defects[0]["cells"][0] == [9, 0], defects[0]["cells"]


def test_underground_belt_span_limit():
    """跨度超上限必须报出来，且不能说成"没配对"。"""
    blueprint = bpm.Blueprint()
    blueprint.add("underground-belt", (0.5, 0.5), 4, type="input")
    blueprint.add("underground-belt", (8.5, 0.5), 4, type="output")  # 跨度 8 > 5
    codes = {d["code"] for d in lintmod.lint(blueprint)}
    assert "underground-belt-span" in codes, codes


def test_underground_belt_pair_ok():
    blueprint = bpm.Blueprint()
    blueprint.add("underground-belt", (0.5, 0.5), 4, type="input")
    blueprint.add("underground-belt", (5.5, 0.5), 4, type="output")
    codes = {d["code"] for d in lintmod.lint(blueprint)}
    assert not (codes & {"underground-belt-unpaired", "underground-belt-span"}), codes


def test_underground_pipe_pair_rule():
    """地下管：A 朝 D，配对物在 A 的 D+8 方向且朝 D+8（语料 320 对这种形状，262 对两头都接上）。"""
    blueprint = bpm.Blueprint()
    blueprint.add("pipe-to-ground", (0.5, 0.5), 4)   # 口朝东，地下段往西
    blueprint.add("pipe-to-ground", (-3.5, 0.5), 12)  # 在西边，口朝西
    codes = {d["code"] for d in lintmod.lint(blueprint)}
    assert "underground-pipe-unpaired" not in codes, codes


def test_inserter_direction_is_pickup_side():
    """臂的 direction 指取货侧：朝北的臂从北边取、往南边放。写反要报。"""
    blueprint = bpm.Blueprint()
    blueprint.add("iron-chest", (0.5, -0.5))          # 北边的箱子
    blueprint.add("stone-furnace", (1, 2))            # 南边的炉子
    blueprint.add("inserter", (0.5, 0.5), 0)          # direction=0 朝北取
    codes = {d["code"] for d in lintmod.lint(blueprint)}
    assert not (codes & {"inserter-no-pickup", "inserter-no-insert"}), codes

    broken = bpm.Blueprint()
    broken.add("iron-chest", (0.5, -0.5))
    broken.add("stone-furnace", (1, 2))
    broken.add("inserter", (0.5, 0.5), 12)            # 朝西取货，西边是空气
    codes = {d["code"] for d in lintmod.lint(broken)}
    assert "inserter-no-pickup" in codes, codes


def test_direction_never_judged_illegal_by_dirs():
    """组装机 3 带 direction=12 在真实蓝图里存在；lint 不许因此报 error。"""
    blueprint = bpm.Blueprint()
    blueprint.add("assembling-machine-3", (1, 1), 12, recipe="iron-gear-wheel")
    defects = [d for d in lintmod.lint(blueprint) if d["severity"] == "error"]
    assert not [d for d in defects if "direction" in d["code"]], defects


def test_direction_requires_recipe_only_for_flagged():
    """只有 direction_requires_recipe 的实体报"有方向没配方"；组装机裸建也认方向，报了就是误报。"""
    flagged = bpm.Blueprint()
    flagged.add("foundry", (2, 2), 8, allow_bare_direction=True)
    codes = {d["code"] for d in lintmod.lint(flagged)}
    assert "direction-needs-recipe" in codes, codes

    normal = bpm.Blueprint()
    normal.add("assembling-machine-2", (1, 1), 4, recipe="iron-gear-wheel")
    codes = {d["code"] for d in lintmod.lint(normal)}
    assert "direction-needs-recipe" not in codes, codes


def test_add_refuses_direction_without_recipe():
    """生成器这一层就要拦住：给 foundry 写方向却不给配方，直接抛错。"""
    blueprint = bpm.Blueprint()
    try:
        blueprint.add("foundry", (2, 2), 8)
    except bpm.BlueprintError as failure:
        assert "recipe" in str(failure)
        return
    raise AssertionError("foundry 写方向没给配方，居然放过去了")


def test_rails_do_not_trigger_overlap():
    """轨道互相重叠是合法的（高架/地面两层），不许报重叠。"""
    if not HAS_CORPUS:
        raise SkipTest(CORPUS_MISSING)
    for name, original in corpus_blueprints():
        if name != "-P6wH4ZGXY9yKaYddtLr.json":
            continue
        codes = {d["code"] for d in lintmod.lint(original) if d["severity"] == "error"}
        assert "overlap" not in codes, "轨道图被误报重叠"


# ---- 原语与模板 ---------------------------------------------------------


def test_belt_run_with_underground():
    blueprint = bpm.Blueprint()
    primitives.belt_run(blueprint, (0, 0), 4, 10, underground=[(3, 4)])
    names = [entity["name"] for entity in blueprint.entities]
    assert names.count("underground-belt") == 2, names
    # 长度 10 的带，下标 3..7 被地下段吃掉，剩 5 格明带
    assert names.count("transport-belt") == 5, names
    codes = {d["code"] for d in lintmod.lint(blueprint)}
    assert not (codes & {"underground-belt-unpaired", "underground-belt-span"}), codes


def test_belt_run_rejects_illegal_span():
    blueprint = bpm.Blueprint()
    try:
        primitives.belt_run(blueprint, (0, 0), 4, 20, underground=[(1, 9)])
    except bpm.BlueprintError:
        return
    raise AssertionError("跨度 9 超过 5，居然放过去了")


def test_inserter_between_validates_geometry():
    blueprint = bpm.Blueprint()
    blueprint.add("iron-chest", (0.5, 0.5))
    blueprint.add("iron-chest", (4.5, 0.5))
    blueprint.add("iron-chest", (2.5, 3.5))
    try:
        primitives.inserter_between(blueprint, (2, 0), (0, 0), (2, 3))
    except bpm.BlueprintError as failure:
        assert "相对" in str(failure) or "正交" in str(failure)
        return
    raise AssertionError("取货和放货不在一条直线上，居然放过去了")


def test_furnace_array_is_clean():
    """24 台石炉阵列：lint 必须 0 error / 0 warning / 0 note。"""
    blueprint = bpm.Blueprint()
    info = furnace_array(blueprint, count=24)
    blueprint.normalize()
    payload = lintmod.report(blueprint)
    assert payload["counts"]["error"] == 0, payload["by_code"]
    assert payload["counts"]["warning"] == 0, payload["by_code"]
    assert payload["counts"]["note"] == 0, payload["by_code"]
    assert info["furnaces"] == 24
    assert len(blueprint.entities) == 253, len(blueprint.entities)


# ---- 接口必须开口说话：这四条钉住的是「工具会主动报错」，别再靠人记 ----


def test_furnace_array_rejects_bad_pair():
    """喂料和产物对不上必须当场抛错，不能建出来再等真机发现产出是 0。"""
    service = _service()
    for bad in (("stone", "steel-plate"), ("iron-ore", "steel-plate"), ("copper-ore", "steel-plate")):
        result = service.furnace_array(count=6, input_item=bad[0], output_item=bad[1])
        assert result["ok"] is False, "%r → %r 居然建成功了：%s" % (bad[0], bad[1], result)
        assert bad[1] in result["error"], result["error"]
        assert "iron-plate" in result["error"], "报错里要说清真正需要什么：%s" % result["error"]


def test_furnace_array_accepts_good_pair():
    """合法组合必须照常能建——这是唯一验过的主路径，不许改坏。"""
    for good, furnace in (
        (("iron-ore", "iron-plate"), "stone-furnace"),
        (("iron-plate", "steel-plate"), "electric-furnace"),
    ):
        # 每组用一张新蓝图：同一张 Service 上连建两排会实体重叠（是测试的错，不是库的）
        service = _service()
        result = service.furnace_array(
            count=6, furnace=furnace, input_item=good[0], output_item=good[1]
        )
        assert result["ok"] is True, result.get("error")
        assert result["input_item"] == good[0]
        assert result["output_item"] == good[1]
        assert result["lint"]["error"] == 0, result["lint_by_code"]


def test_furnace_array_explains_implicit_rewrite():
    """recipe= 被改写成 output_item 时，回包必须逐条说明，不能让人猜。"""
    result = _service().furnace_array(count=6, recipe="steel-plate")
    assert result["ok"] is True, result.get("error")
    assert result["output_item"] == "steel-plate"
    assert result["input_item"] == "iron-plate", "进料要按 smelting 配方反推"
    assert result["recipe_written_to_machine"] is None, "炉子上不该写 recipe"
    notes = result.get("notes") or []
    assert len(notes) >= 2, "至少要说明：recipe 无效 + 进料是怎么来的；实际 %r" % (notes,)
    joined = " ".join(notes)
    assert "recipe" in joined and "进料" in joined, joined


def test_entity_query_declares_recipe_acceptance():
    """bp_entity 必须能回答"这台机器认不认 blueprint 里的 recipe"。"""
    service = _service()
    furnace = service.entity_info("electric-furnace")
    assert furnace["accepts_blueprint_recipe"] is False
    assert "进料" in furnace["why"]
    machine = service.entity_info("assembling-machine-2")
    assert machine["accepts_blueprint_recipe"] is True
    foundry = service.entity_info("foundry")
    assert foundry["accepts_blueprint_recipe"] is True, "foundry 是组装机，认 recipe"


def test_recipe_query_says_how_to_use():
    """bp_recipe 对 smelting 配方必须说明"靠进料选中"，否则调用方会去写 recipe。"""
    payload = _service().recipe_info("steel-plate")
    assert "how_to_use_in_blueprint" in payload, payload.keys()
    assert "进料" in payload["how_to_use_in_blueprint"]
    assert "stone-furnace" in payload["machines_that_need_input_not_recipe"]


def test_add_entity_reports_defects_immediately():
    """写操作当场就要说话：给电炉写 recipe，不许等下一次 bp_lint。"""
    result = _service().add_entity("electric-furnace", (10, 10), None, recipe="steel-plate")
    assert result["ok"] is True, result
    introduced = result.get("new_defects") or {}
    assert "furnace-recipe-ignored" in introduced, "写操作没当场报缺陷：%r" % (result.keys(),)
    assert result["lint"]["warning"] >= 1
    details = result.get("new_defect_details") or []
    assert details and details[0]["code"] == "furnace-recipe-ignored"
    assert details[0]["fix"], "缺陷要带修复建议"


def test_clean_write_reports_no_new_defects():
    """正常写操作不该背噪音：放一台干净的电炉，没有新缺陷。"""
    result = _service().add_entity("iron-chest", (0.5, 0.5))
    assert result["ok"] is True
    assert not (result.get("new_defects") or {}), result.get("new_defects")
    assert "lint" in result


# ---- 管道原语（验收标准第 5 条的地基）----


def test_pipe_run_with_underground():
    blueprint = bpm.Blueprint()
    primitives.pipe_run(blueprint, (0, 0), 4, 12, underground=[(3, 6)])
    names = [e["name"] for e in blueprint.entities]
    assert names.count("pipe-to-ground") == 2, names
    counts = lintmod.report(blueprint)["counts"]
    assert counts["error"] == 0 and counts["warning"] == 0, lintmod.report(blueprint)["by_code"]


def test_underground_pipe_pair_direction():
    """往东埋的一对：入口 direction=12（朝西）、出口 direction=4（朝东）。写反了两端各成孤岛。"""
    blueprint = bpm.Blueprint()
    entry, exit_entity = primitives.underground_pipe_pair(blueprint, (0, 0), 4, 5)
    assert entry.get("direction") == 12, entry
    assert exit_entity.get("direction") == 4, exit_entity
    assert exit_entity["position"]["x"] == 5.5


def test_underground_pipe_pair_span_limit():
    blueprint = bpm.Blueprint()
    try:
        primitives.underground_pipe_pair(blueprint, (0, 0), 4, 11)
    except bpm.BlueprintError:
        return
    raise AssertionError("跨度 11 超过 10，居然放过去了")


def test_connect_machine_port():
    """机器液口在机器自己的边格上，管子要接在再往外一格。"""
    blueprint = bpm.Blueprint()
    machine = blueprint.add_spanning("chemical-plant", (0, 0))
    ports = primitives.machine_fluid_ports(blueprint, machine)
    assert ports, "化学厂应该有液口"
    pipe = primitives.connect_machine_port(blueprint, machine)
    cells = blueprint.cells_of(pipe)
    assert cells, "管子得占格"
    inside = blueprint.cells_of(machine)
    assert not (cells & inside), "管子接到机器身上了"


# ---- row_station / 配比 ----------------


def test_machine_count_comes_from_truth_table():
    """台数必须由真值表算：绿板 0.5 秒、铜线 0.5 秒、AM1 速度 0.5。"""
    assert primitives.machine_count(2.0, "electronic-circuit", "assembling-machine-1") == 2
    assert primitives.machine_count(6.0, "copper-cable", "assembling-machine-1") == 3
    # 换成 AM2（速度 0.75）台数应该少下来
    assert primitives.machine_count(2.0, "electronic-circuit", "assembling-machine-2") == 2


def test_row_station_two_inputs():
    blueprint = bpm.Blueprint()
    info = primitives.row_station(
        blueprint, "assembling-machine-1", 2, recipe="iron-gear-wheel", inputs=2
    )
    assert info["ports"]["input_far_cell"] is not None
    rows = info["rows"]
    assert rows["far_belt"] < rows["near_belt"] < rows["arms"] < rows["machines"]
    counts = lintmod.report(blueprint)["counts"]
    assert counts["error"] == 0, lintmod.report(blueprint)["by_code"]


def test_by_product_separates_intermediate_from_product():
    """多级产线的产物身份只能按机器配方分组数——produced 会把中间品算进去。

    实测证据：绿板线 `produced 111` 全部是铜线，2 台绿板机出 0 个。
    """

    production = {
        "produced": 70,
        "machines": [
            {"recipe": "copper-cable", "products_finished": 15},
            {"recipe": "copper-cable", "products_finished": 15},
            {"recipe": "electronic-circuit", "products_finished": 5},
            {"recipe": None, "products_finished": 99},
        ],
    }
    totals = verify.by_product(production)
    assert totals == {"copper-cable": 30, "electronic-circuit": 5}, totals
    assert sum(totals.values()) != production["produced"], "produced 不该等于成品数之和"


def test_row_station_arm_count_and_column():
    """臂位与臂数可调：流体机器要让开四角液口，高吞吐机器要在一侧站多根臂。"""
    blueprint = bpm.Blueprint()
    primitives.row_station(
        blueprint,
        "assembling-machine-1",
        2,
        recipe="iron-gear-wheel",
        inputs=2,
        arms=(1, 2),
        arm_column="middle",
    )
    counts = lintmod.report(blueprint)["counts"]
    assert counts["error"] == 0, lintmod.report(blueprint)["by_code"]
    arms = [e for e in blueprint.entities if e["name"] in ("inserter", "long-handed-inserter")]
    # 2 台机器 x (近端 1 + 远端 2 + 出料 1) = 8
    assert len(arms) == 8, len(arms)

    # 一侧只有 3 格：两条输入加起来超过 3 根臂要明确拒绝——"加臂解决吞吐"是个陷阱
    try:
        primitives.row_station(
            bpm.Blueprint(), "assembling-machine-1", 1, recipe="iron-gear-wheel",
            inputs=2, arms=(2, 2),
        )
    except bpm.BlueprintError as failure:
        assert "加臂解决不了吞吐" in str(failure), str(failure)
        return
    raise AssertionError("一侧 3 格塞 4 根臂，居然放过去了")


def test_machine_pair_checks_throughput():
    """直插的搬运速率必须当场判：1 根快臂 2.31/秒，而绿板机要 3 铜线/秒。"""
    blueprint = bpm.Blueprint()
    info = primitives.machine_pair(
        blueprint,
        "assembling-machine-1",
        "copper-cable",
        "assembling-machine-1",
        "electronic-circuit",
        1,
        arms=3,
    )
    threshold = info["threshold"]
    assert threshold["per_second"] == 6.93, threshold
    assert threshold["demand_per_second"] == 3.0, threshold
    arms = [e for e in blueprint.entities if e["name"] == "fast-inserter"]
    assert len(arms) == 3, len(arms)

    try:
        primitives.machine_pair(
            bpm.Blueprint(),
            "assembling-machine-1",
            "copper-cable",
            "assembling-machine-1",
            "electronic-circuit",
            1,
            arms=1,
        )
    except bpm.BlueprintError as failure:
        assert "搬不动" in str(failure), str(failure)
        return
    raise AssertionError("1 根快臂搬不动 3 个/秒，居然放过去了")


def test_green_circuit_is_clean():
    """绿板线：lint 必须 0 error，且铜线带是**往西流**（否则东边的铜线回不到取料格）。"""
    from bpcompile.templates import green_circuit

    blueprint = bpm.Blueprint()
    info = green_circuit(blueprint, target_rate=2.0)
    blueprint.normalize()
    counts = lintmod.report(blueprint)["counts"]
    assert counts["error"] == 0, lintmod.report(blueprint)["by_code"]
    # 配比 3:2（人类蓝图同款）：铜线机 = 绿板机 + ceil(绿板机/2)
    assert info["stations"]["cable"] == 3 and info["stations"]["circuit"] == 2, info["stations"]
    # 直插：铜线机 -> 绿板机，direction=12（取货朝西、放货朝东）
    direct = [
        e
        for e in blueprint.entities
        if e["name"] == "fast-inserter" and e.get("direction") == 12
    ]
    assert direct, "应该有东西向直插的臂（direction=12）"
    assert info["throughput_check"]["direct_insert_supply"] >= info["throughput_check"]["demand"]


def _service():
    from bpcompile.service import Service

    return Service()


def test_furnace_array_has_two_separate_input_belts():
    """矿和燃料必须是两条独立带：同一格不许被两种东西占；两条带的 y 不同。"""
    blueprint = bpm.Blueprint()
    info = furnace_array(blueprint, count=6)
    ore_row = info["ports"]["ore_input_cell"][1]
    fuel_row = info["ports"]["coal_input_cell"][1]
    assert ore_row != fuel_row, "矿和煤被排在同一个 y 上了"
    belts = [
        e for e in blueprint.entities if (protos.find(e["name"]) or {}).get("type") == "transport-belt"
    ]
    rows = {int(e["position"]["y"] - 0.5) for e in belts}
    assert len(rows) == 3, "应该有三条带（矿/燃料/成品），实际行数 %s" % rows


def test_poles_are_wired():
    """电塔必须写成铜线。少这段，游戏会建出 12 张独立电网——接一根线只有一根塔有电。"""
    blueprint = bpm.Blueprint()
    furnace_array(blueprint, count=24)
    blueprint.normalize()
    poles = [e for e in blueprint.entities if e["name"] == "medium-electric-pole"]
    assert len(poles) > 1, len(poles)
    wires = blueprint.copper_wires()
    assert wires, "塔有 %d 根，铜线 0 根" % len(poles)
    for entry in blueprint.data["wires"]:
        assert entry[1] == 5 and entry[3] == 5, "铜线的连接点编号是 5（语料 340/341 都是 5），不是 %r" % entry


def test_pole_network_is_single_component():
    """连通图，不是链：所有塔必须在同一张网里，孤岛数 == 1。"""
    blueprint = bpm.Blueprint()
    furnace_array(blueprint, count=24)
    blueprint.normalize()
    components = primitives.pole_components(blueprint)
    assert len(components) == 1, "电网被拆成 %d 张：%s" % (
        len(components),
        [[p["entity_number"] for p in group] for group in components],
    )


def test_poles_without_wires_is_error():
    """poles > 1 且 wires 为空，必须是 error，不能是 warning。"""
    blueprint = bpm.Blueprint()
    blueprint.add("medium-electric-pole", (0.5, 0.5))
    blueprint.add("medium-electric-pole", (8.5, 0.5))
    defects = [d for d in lintmod.lint(blueprint) if d["code"] == "power-network-split"]
    assert len(defects) == 1, [d["code"] for d in lintmod.lint(blueprint)]
    assert defects[0]["severity"] == "error"
    assert "一条铜线都没写" in defects[0]["message"]


def test_split_pole_network_is_warning():
    """四根塔只连了两根：报 warning 并列出分量。

    有铜线却分成几张网，在真实大基地里可能是**故意**的独立电网，所以不到 error。
    一条铜线都没有才是 error（那种图每根塔各成一张网）。
    """
    blueprint = bpm.Blueprint()
    left = blueprint.add("medium-electric-pole", (0.5, 0.5))
    left2 = blueprint.add("medium-electric-pole", (4.5, 0.5))
    right = blueprint.add("medium-electric-pole", (30.5, 0.5))
    right2 = blueprint.add("medium-electric-pole", (34.5, 0.5))
    blueprint.add_wire(left["entity_number"], 5, left2["entity_number"], 5)
    blueprint.add_wire(right["entity_number"], 5, right2["entity_number"], 5)
    defects = [d for d in lintmod.lint(blueprint) if d["code"] == "power-network-split"]
    assert len(defects) == 1, [d["code"] for d in lintmod.lint(blueprint)]
    assert defects[0]["severity"] == "warning", defects[0]["severity"]
    assert "2 张" in defects[0]["message"], defects[0]["message"]


def test_testbench_fixtures():
    """自测版：输入口要有 infinity-chest，输出口要有钢箱，电网要自己带电源。"""
    from bpcompile import testbench as testbench_module

    blueprint = bpm.Blueprint()
    furnace_array(blueprint, count=8)
    clone, manifest = testbench_module.build_testbench(blueprint, seconds=60)
    clone.normalize()
    names = [e["name"] for e in clone.entities]
    assert names.count("infinity-chest") >= 2, names
    assert "electric-energy-interface" in names
    assert "steel-chest" in names
    assert manifest["product"] == "iron-plate"
    counts = lintmod.report(clone)["counts"]
    assert counts["error"] == 0, lintmod.report(clone)["by_code"]


def test_furnace_array_every_furnace_has_fuel_and_ore():
    """每台炉子都要有进货臂和进燃料臂——缺燃料会空转 10 秒以上，产量验收直接挂。"""
    blueprint = bpm.Blueprint()
    info = furnace_array(blueprint, count=8)
    targets = {}
    for entity in blueprint.entities:
        proto = protos.find(entity["name"]) or {}
        if proto.get("type") != "inserter":
            continue
        position = entity["position"]
        direction = entity.get("direction", 0)
        insert = proto["inserter"]["insert"]
        offset = geometry.rotate_point(insert[0], insert[1], direction)
        cell = geometry.cell_of(position["x"] + offset[0], position["y"] + offset[1])
        targets.setdefault(cell, []).append(entity["name"])
    furnaces = [e for e in blueprint.entities if e["name"] == "stone-furnace"]
    assert len(furnaces) == 8
    for furnace in furnaces:
        cells = blueprint.cells_of(furnace)
        count = sum(len(targets.get(cell, ())) for cell in cells)
        assert count == 2, "炉子 %s 只收到 %d 个进料臂" % (furnace["position"], count)


# ---- 语料误报闸 ---------------------------------------------------------


def test_corpus_no_error_false_positives():
    """27 张真实蓝图不许被大批量报 error——误报比漏报毒。

    这条是 ratchet：允许的上限只许降不许升。现在的 6 条全是真实蓝图里真的悬空的臂
    （取货/放货侧是空格），不是规则错。一旦换个检查把真蓝图判红，这条会跳。
    """
    if not HAS_CORPUS:
        raise SkipTest(CORPUS_MISSING)
    allowed = {"inserter-no-pickup", "inserter-no-insert"}
    # 已知且尚未定论的一条：这张 1116 实体的基地图有 109 根电塔、**一条铜线都没写**。
    # 它要么本来就靠游戏自动连（那我们这条检查就错），要么这张图从新存档建起来就是没电的。
    # 真机交叉验证还没做（rig 那几天在被改）。在定论之前，只给这一张文件开豁免，其余照报。
    documented = {("-P5_wlYMsi2D_Cj8rZ0a.json", "power-network-split")}
    offenders = []
    total = 0
    for name, blueprint in corpus_blueprints():
        for defect in lintmod.lint(blueprint):
            if defect["severity"] != "error":
                continue
            total += 1
            if (name, defect["code"]) in documented:
                continue
            if defect["code"] not in allowed:
                offenders.append((name, defect["code"], defect["message"][:80]))
    assert not offenders, "出现新类别的误报：%s" % offenders[:3]
    assert total <= 6, "语料 error 总数 %d 超过 ratchet 上限 6" % total


def test_corpus_underground_pipe_rule_matches_reality():
    """地下管配对规则要在真实语料上成立：未配对数必须远小于总数。"""
    if not HAS_CORPUS:
        raise SkipTest(CORPUS_MISSING)
    total = 0
    unpaired = 0
    for _, blueprint in corpus_blueprints():
        for entity in blueprint.entities:
            if (protos.find(entity["name"]) or {}).get("type") == "pipe-to-ground":
                total += 1
        unpaired += len(
            [d for d in lintmod.lint(blueprint) if d["code"] == "underground-pipe-unpaired"]
        )
    assert total > 100, total
    assert unpaired / total < 0.12, "%d/%d 条地下管判成没配对，规则大概反了" % (unpaired, total)


def main():
    groups = [
        ("编码层", [test_codec_roundtrip, test_envelope_shape, test_cross_decoder_with_draftsman]),
        ("几何", [test_footprint_swaps_on_east_west, test_cells_of_two_by_two, test_canonical_direction_handles_rail_values]),
        (
            "lint",
            [
                test_overlap_is_pinpointed,
                test_out_of_bounds,
                test_underground_belt_span_limit,
                test_underground_belt_pair_ok,
                test_underground_pipe_pair_rule,
                test_inserter_direction_is_pickup_side,
                test_direction_never_judged_illegal_by_dirs,
                test_direction_requires_recipe_only_for_flagged,
                test_add_refuses_direction_without_recipe,
                test_rails_do_not_trigger_overlap,
            ],
        ),
        (
            "原语与模板",
            [
                test_belt_run_with_underground,
                test_belt_run_rejects_illegal_span,
                test_inserter_between_validates_geometry,
                test_furnace_array_is_clean,
                test_furnace_array_has_two_separate_input_belts,
                test_by_product_separates_intermediate_from_product,
                test_pipe_run_with_underground,
                test_underground_pipe_pair_direction,
                test_underground_pipe_pair_span_limit,
                test_connect_machine_port,
                test_machine_count_comes_from_truth_table,
                test_row_station_two_inputs,
                test_row_station_arm_count_and_column,
                test_machine_pair_checks_throughput,
                test_green_circuit_is_clean,
                test_add_entity_reports_defects_immediately,
                test_clean_write_reports_no_new_defects,
                test_furnace_array_rejects_bad_pair,
                test_furnace_array_accepts_good_pair,
                test_furnace_array_explains_implicit_rewrite,
                test_entity_query_declares_recipe_acceptance,
                test_recipe_query_says_how_to_use,
                test_poles_are_wired,
                test_pole_network_is_single_component,
                test_poles_without_wires_is_error,
                test_split_pole_network_is_warning,
                test_testbench_fixtures,
                test_furnace_array_every_furnace_has_fuel_and_ore,
            ],
        ),
        ("语料误报闸", [test_corpus_no_error_false_positives, test_corpus_underground_pipe_rule_matches_reality]),
    ]
    for title, cases in groups:
        print("\n[%s]" % title)
        for case in cases:
            check(case.__name__, case)
    print("\n%d passed, %d skipped, %d failed" % (_PASSED, len(_SKIPPED), len(_FAILED)))
    if _SKIPPED:
        print("  跳过 %d 条（缺样本或缺可选依赖时属正常）" % len(_SKIPPED))
    if _FAILED:
        for name, reason in _FAILED:
            print("  FAILED %s: %s" % (name, reason))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
