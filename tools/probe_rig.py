"""真机校验的共用底座：临时 mod + 隔离 write-data + --test-scenario 启动 + 结果回收。

所有真机校验工具都从这里走，免得每加一件事就把启动流程抄一遍。
"""

import json
import os
import random
import subprocess
import time

_FACTORIO_CANDIDATES = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Factorio\bin\x64\factorio.exe",
    r"C:\Program Files\Steam\steamapps\common\Factorio\bin\x64\factorio.exe",
    os.path.expanduser("~/.local/share/Steam/steamapps/common/Factorio/bin/x64/factorio"),
    os.path.expanduser(
        "~/Library/Application Support/Steam/steamapps/common/Factorio/bin/x64/factorio"
    ),
]


def _default_factorio():
    """游戏可执行文件：先认环境变量 FACTORIO_EXE，再试各平台的常见安装路径。"""
    override = os.environ.get("FACTORIO_EXE")
    if override:
        return override
    for candidate in _FACTORIO_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return _FACTORIO_CANDIDATES[0]


FACTORIO_DEFAULT = _default_factorio()
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
MOD_NAME = "bp-probe"
SCENARIO_NAME = "probe"
RESULT_REL = ("script-output", "bp-probe", "result.json")


def write_run_dir(run_dir, scenario_lua, factorio_version="2.1"):
    """铺临时 mod：control.lua 空壳 + scenarios/gt/control.lua 装真实逻辑。"""
    mod_dir = os.path.join(run_dir, "mods", MOD_NAME)
    scenario_dir = os.path.join(mod_dir, "scenarios", SCENARIO_NAME)
    os.makedirs(scenario_dir, exist_ok=True)
    with open(os.path.join(mod_dir, "info.json"), "w", encoding="utf-8") as handle:
        json.dump({"name": MOD_NAME, "version": "0.0.1", "title": "Blueprint Build Probe",
                   "author": "factorio蓝图编译器", "description": "真机校验夹具",
                   "factorio_version": factorio_version,
                   "dependencies": ["base >= %s" % factorio_version]}, handle, indent=2)
    with open(os.path.join(mod_dir, "control.lua"), "w", encoding="utf-8") as handle:
        handle.write("-- 数据阶段空实现，全部逻辑在 scenario 里\n")
    with open(os.path.join(scenario_dir, "description.json"), "w", encoding="utf-8") as handle:
        json.dump({"multiplayer-compatible": True, "order": "z"}, handle, indent=2)
    with open(os.path.join(scenario_dir, "control.lua"), "w", encoding="utf-8") as handle:
        handle.write(scenario_lua)
    return scenario_dir


def write_config(run_dir):
    """把 write-data 钉在本次临时目录里，用户 %APPDATA%\\Factorio 一个字节都不进。"""
    write_data = os.path.join(run_dir, "write")
    config_dir = os.path.join(run_dir, "cfgd")
    os.makedirs(write_data, exist_ok=True)
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "config.ini")
    with open(config_path, "w", encoding="utf-8") as handle:
        handle.write("; version=13\n[path]\nread-data=__PATH__system-read-data__\n")
        handle.write("write-data=%s\n[other]\nautosave-interval=0\n" % write_data.replace("\\", "/"))
    return config_path, write_data


def launch(factorio, run_dir, config_path, test_ticks, timeout):
    args = [factorio, "--test-scenario", "%s/%s" % (MOD_NAME, SCENARIO_NAME),
            "--mod-directory", os.path.join(run_dir, "mods"),
            "--config", config_path,
            "--test-ticks", str(test_ticks),
            "--port", str(random.randint(20000, 40000)),
            "--disable-audio", "--no-log-rotation"]
    started = time.time()
    proc = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout)
    return proc, time.time() - started, args


def collect(proc, elapsed, write_data):
    result_path = os.path.join(write_data, *RESULT_REL)
    payload = None
    if os.path.isfile(result_path):
        with open(result_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    log_path = os.path.join(write_data, "factorio-current.log")
    return {
        "exit_code": proc.returncode,
        "elapsed_s": round(elapsed, 2),
        "result_path": result_path if payload is not None else None,
        "log_path": log_path if os.path.isfile(log_path) else None,
        "timings": parse_timings(proc.stdout),
        "raw": payload,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-25:]),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-15:]),
    }


def parse_timings(stdout):
    marks = {}
    for line in stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) < 2 or not fields[0].replace(".", "", 1).isdigit():
            continue
        stamp = float(fields[0])
        if "Prototype list checksum" in line:
            marks["prototypes_loaded"] = stamp
        elif "Loading level.dat" in line:
            marks["scenario_loaded"] = stamp
        elif "Test passed" in line or "Test failed" in line:
            marks["test_finished"] = stamp
    return marks


def run_once(scenario_lua, work_dir, factorio=FACTORIO_DEFAULT, test_ticks=1200, timeout=300.0, keep=False):
    """铺目录 → 启动 → 回收。keep=False 时调用方负责清理 work_dir。"""
    config_path, write_data = write_config(work_dir)
    write_run_dir(work_dir, scenario_lua)
    proc, elapsed, args = launch(factorio, work_dir, config_path, test_ticks, timeout)
    outcome = collect(proc, elapsed, write_data)
    outcome["command"] = " ".join(args)
    outcome["work_dir"] = work_dir
    outcome["kept"] = keep
    return outcome
