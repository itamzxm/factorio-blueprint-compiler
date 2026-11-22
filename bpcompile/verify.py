"""验证档位：静态 lint（毫秒） → 真机落地（秒级）。可插拔。

真机这一档调用 tools/factorio_build_check.py。它没装好、或者
游戏不在本机时，这里要能干净地降级到"只有 lint"，而不是假装验过了。

要紧的一条：**tenet —— 落地 ≠ 能用**。真机这一档只证明"游戏认这张蓝图、实体都建出来了"；
一根臂对着空气照样落地通过。逻辑正确性靠 lint。
"""

import json
import os
import subprocess
import sys
import tempfile

from . import blueprint as bpm
from . import lint as lintmod

TOOL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RIG_SCRIPT = os.path.join(TOOL_ROOT, "tools", "factorio_build_check.py")


def _rig_probe():
    if not os.path.exists(RIG_SCRIPT):
        return False, "tools/factorio_build_check.py 不存在"
    try:
        import lupa  # noqa: F401
    except Exception:
        return False, "当前解释器没装 lupa，真机档跑不了（pip install lupa）"
    return True, "ok"


def rig_available():
    ok, reason = _rig_probe()
    return {"available": ok, "reason": reason, "script": RIG_SCRIPT}


_LAST_RIG_VERSION = {"value": None}


def _parse_version(payload):
    return payload.get("rig_version") or payload.get("version") or None


def run_rig(blueprint_or_path, timeout=240, powered_single=False, powered=False, run_seconds=None):
    """把蓝图送进游戏。run_seconds 给了就跑产量（L3），否则只验落地。

    **不要自己传 --test-ticks**：工具在给了 --run-seconds 时会自己把它抬到够用，
    写死一个小的会把自动抬高盖掉，结果就是 no_result。这里刻意不传。
    """
    ok, reason = _rig_probe()
    if not ok:
        return {"backend": "rig", "ok": False, "available": False, "reason": reason}
    if isinstance(blueprint_or_path, bpm.Blueprint):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".blueprint.txt", delete=False, encoding="utf-8"
        )
        handle.write(blueprint_or_path.to_string())
        handle.close()
        path = handle.name
    else:
        path = blueprint_or_path
    out = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    out.close()
    command = [sys.executable, RIG_SCRIPT, path, "--json", out.name]
    if powered_single:
        command.append("--powered-single")
    if powered:
        command.append("--powered")
    if run_seconds:
        command += ["--run-seconds", str(int(run_seconds))]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=TOOL_ROOT,
        )
        try:
            with open(out.name, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {
                "backend": "rig",
                "ok": False,
                "available": True,
                "reason": "真机工具没有产出可解析的 JSON（退出码 %s）" % proc.returncode,
                "stderr": (proc.stderr or "")[-800:],
            }
    except subprocess.TimeoutExpired:
        return {"backend": "rig", "ok": False, "available": True, "reason": "真机验证超时 %ds" % timeout}
    finally:
        for candidate in (out.name,):
            try:
                os.unlink(candidate)
            except OSError:
                pass
        if isinstance(blueprint_or_path, bpm.Blueprint):
            try:
                os.unlink(path)
            except OSError:
                pass
    job = (payload.get("jobs") or [{}])[0]
    usable = job.get("usable") or {}
    version = _parse_version(payload) or _parse_version(job)
    comparable = True
    if _LAST_RIG_VERSION["value"] is None:
        _LAST_RIG_VERSION["value"] = version
    elif version != _LAST_RIG_VERSION["value"]:
        comparable = False
    production = job.get("production") or None
    return {
        "backend": "rig",
        "ok": bool(payload.get("ok")),
        "available": True,
        "verdict": payload.get("verdict"),
        "rig_version": version,
        "comparable": comparable,
        "elapsed_s": payload.get("elapsed_s"),
        "expected_entities": job.get("expected_entities"),
        "built_entities": job.get("built_entities"),
        "ghosts": job.get("ghosts"),
        "statuses": job.get("statuses"),
        "usable": usable,
        "production": (
            {
                "run_seconds": production.get("run_seconds"),
                "produced": production.get("produced"),
                "theoretical": production.get("theoretical"),
                "achievement": production.get("achievement"),
                "machine_status": production.get("machine_status"),
                # drained 是"验货"的关键：它告诉你箱子里进的是铁板还是钢板。
                # 炉子的配方由进料决定，写着 steel 也可能实际出铁——只能靠这个字段抓。
                "drained": production.get("drained"),
                "injected_total": production.get("injected_total"),
                # 判产物身份最可靠的一路：按机器自己的配方分组数成品
                "by_product": by_product(production),
                # 判产物身份最可靠的一路：**按机器自己的配方分组**数成品。
                # produced 是所有机器的成品数之和——多级产线里它把中间品也算进去
                # （绿板线 produced 70 = 60 铜线 + 10 绿板），拿它当产量会高估 6 倍。
                "by_product": by_product(production),
            }
            if production
            else None
        ),
        "rig_error": job.get("rig_error") or payload.get("rig_error"),
        "decode_failures": payload.get("decode_failures") or [],
        "message": payload.get("message"),
    }


def by_product(production):
    """按机器自报的配方把 products_finished 分组。多级产线的产物身份就靠它。

    `produced` 是**所有机器**成品数之和，中间品也计入（绿板线 produced 70 = 60 铜线 + 10 绿板），
    拿它当产量会高估；`drained` 是夹具收进箱子的东西，会被夹具自身行为污染。
    只有这个是"哪台机器按什么配方出了多少个"，不受夹具影响。
    """
    totals = {}
    for machine in (production or {}).get("machines") or []:
        recipe = machine.get("recipe")
        finished = machine.get("products_finished") or 0
        if not recipe or not finished:
            continue
        totals[recipe] = totals.get(recipe, 0) + finished
    return totals or None


def verify(blueprint, modes=("lint", "rig"), run_seconds=None, powered_single=False):
    """按顺序跑各档。lint 不过就不必上真机——省时间，也让报错聚焦。

    modes 里带 "run" 等价于 rig + 跑产量。
    """
    modes = tuple(modes)
    if "run" in modes and not run_seconds:
        run_seconds = 60
    result = {"modes": list(modes), "run_seconds": run_seconds}
    lint_payload = lintmod.report(blueprint)
    result["lint"] = {
        "ok": lint_payload["ok"],
        "counts": lint_payload["counts"],
        "by_code": lint_payload["by_code"],
        "defects": lint_payload["defects"][:30],
    }
    if "rig" not in modes and "run" not in modes:
        result["ok"] = lint_payload["ok"]
        result["verdict"] = "lint-only"
        return result
    if not lint_payload["ok"]:
        result["rig"] = {
            "backend": "rig",
            "skipped": True,
            "reason": "lint 有 %d 个 error，先修完再上真机" % lint_payload["counts"]["error"],
        }
        result["ok"] = False
        result["verdict"] = "lint-failed"
        return result
    result["rig"] = run_rig(
        blueprint, run_seconds=run_seconds, powered_single=powered_single
    )
    result["ok"] = bool(lint_payload["ok"] and result["rig"].get("ok"))
    result["verdict"] = "pass" if result["ok"] else "rig-failed"
    return result


def format_verify(result):
    lines = []
    lint_payload = result.get("lint") or {}
    counts = lint_payload.get("counts") or {}
    lines.append(
        "lint: %d error / %d warning / %d note"
        % (counts.get("error", 0), counts.get("warning", 0), counts.get("note", 0))
    )
    rig = result.get("rig")
    if rig is None:
        return "\n".join(lines)
    if rig.get("skipped"):
        lines.append("真机: 跳过（%s）" % rig.get("reason"))
        return "\n".join(lines)
    if not rig.get("available", True):
        lines.append("真机: 不可用（%s）" % rig.get("reason"))
        return "\n".join(lines)
    lines.append(
        "真机: %s，落地 %s/%s，影子 %s"
        % (
            rig.get("verdict"),
            rig.get("built_entities"),
            rig.get("expected_entities"),
            rig.get("ghosts"),
        )
    )
    usable = rig.get("usable") or {}
    if usable:
        lines.append(
            "  可用性: %d error / %d warning"
            % (usable.get("errors", 0), usable.get("warnings", 0))
        )
        if usable.get("first"):
            lines.append("  第一条: %s" % usable.get("first"))
    statuses = rig.get("statuses") or {}
    if statuses:
        lines.append("  实体状态: %s" % json.dumps(statuses, ensure_ascii=False))
    production = rig.get("production")
    if production:
        lines.append(
            "  产量: %s 秒产出 %s / 理论上限 %s = %.1f%%，机器状态 %s"
            % (
                production.get("run_seconds"),
                production.get("produced"),
                production.get("theoretical"),
                (production.get("achievement") or 0) * 100,
                json.dumps(production.get("machine_status"), ensure_ascii=False),
            )
        )
    if rig.get("rig_version"):
        lines.append(
            "  rig 版本 %s%s"
            % (rig.get("rig_version"), "" if rig.get("comparable", True) else "（变过，读数不可比）")
        )
    return "\n".join(lines)
