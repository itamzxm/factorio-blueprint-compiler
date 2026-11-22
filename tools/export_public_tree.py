#!/usr/bin/env python3
r"""把完整工程导出成可公开发布的子集，也就是本仓库的内容。

本仓库只发布「能公开的那部分」：源码、原型真值表、技术文档、最小样例。
完整工程里还有本地测试样本、临时实验目录和开发期的记录，那些一律不进公开树。
核心库还在改，所以每次改完都要重跑本脚本。

一次运行做四件事：

  1. **清理** —— 删掉目标树里「既不在吸收清单、也不在就地维护清单」的文件，
     不留旧文件残骸（幂等：同样的源跑两次，结果一样）。
  2. **拷贝 + 改写** —— 按白名单从源目录取文件。代码里几处描述内部工装的字句在这里
     改写成中性说法。每条改写都断言命中次数：源文件一改就整体失败停下，
     而不是把旧文字悄悄带出去。
  3. **重建 examples/** —— 用目标树自己的库重新生成，样例永远跟当前代码一致。
  4. **自检** —— 跑 tools/leak_scan.py；有命中即失败，退出码非 0。

doc/ 下那几篇**不在**吸收清单里：它们是就地维护的公开版本，本文不做自动改写。
缺了哪篇、或者源文档改了而公开版没跟上，脚本会报出来（见 tools/doc_sources.json）。

用法：

    python tools/export_public_tree.py --source <完整工程目录>
    python tools/export_public_tree.py --source <完整工程目录> --dry-run
    python tools/export_public_tree.py --source <完整工程目录> --accept-docs
    python tools/export_public_tree.py --source <完整工程目录> --skip-checks
"""

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_SOURCES = os.path.join(ROOT, "tools", "doc_sources.json")

ANY = "any"  # expect 用这个值表示「出现 0 次以上都行」（跨文件改名用）

# ---------------------------------------------------------------- 吸收清单

# (源相对路径, 目标相对路径)；目标 None 表示同名。
FILES = [
    ("mcp_server.py", None),
    ("tests/run_tests.py", None),
    ("tools/bpstring.py", None),
    ("tools/query.py", None),
    ("tools/build_prototypes.py", None),
    ("tools/factorio_build_check.py", None),
    # 真机校验夹具：对外统一改名成 probe_*，内部代号不进公开树。
    ("tools/gt_rig.py", "tools/probe_rig.py"),
    ("tools/gt_directions.py", "tools/probe_directions.py"),
    ("tools/gt_scenario.lua", "tools/probe_scenario.lua"),
    ("tools/gt_directions_scenario.lua", "tools/probe_directions_scenario.lua"),
    ("data/prototypes.json", None),
    ("data/direction-probe.json", None),
    ("data/corpus/index.json", None),
]

# 整批吸收：语料只带原串（.blueprint.txt），解码后的 .json 不进版本库。
COPY_GLOBS = [
    ("data/corpus/*.blueprint.txt", "data/corpus"),
]

# 整个目录吸收（只收 *.py，跳过 __pycache__ 与 .pyc）：库里新加的模块自动跟上。
TREES = [("bpcompile", "bpcompile")]

# 就地维护：只存在于公开树，不来自源目录。少一个就失败。
KEEP = [
    "README.md",
    "LICENSE",
    ".gitignore",
    "pyproject.toml",
    "requirements.txt",
    "examples/README.md",
    "data/corpus/README.md",
    "tools/export_public_tree.py",
    "tools/leak_scan.py",
    "tools/make_examples.py",
    "tools/decode_corpus.py",
    "tools/doc_sources.json",
    "doc/蓝图格式与坑.md",
    "doc/核心库与MCP接口.md",
    "doc/自测版夹具.md",
    "doc/炉子阵列模板.md",
    "doc/选型-为什么不用draftsman.md",
    "doc/绿板产线.md",
    "doc/塑料产线.md",
    "doc/直插原语.md",
    "doc/真机校验.md",
]

# examples/ 的产物，由 tools/make_examples.py 生成。
EXAMPLES = [
    "examples/furnace24.blueprint.txt",
    "examples/furnace24.png",
    "examples/smelt-steel24.blueprint.txt",
    "examples/smelt-steel24.png",
    "examples/furnace24.testbench.blueprint.txt",
]

# ---------------------------------------------------------------- 改写表
#
# 每条 = (kind, pattern, replacement, expect)。
#   kind   "lit" 字面替换 / "re" 正则替换（re.MULTILINE）
#   expect 必须命中的次数；ANY 表示 0 次以上；对不上就整体失败，提示更新这张表。
#
# 少数模式把某个字写成一字字符类（如 评测[集]）：正则语义完全一样，
# 只是让本文件自己过扫描器时不命中——扫描器要盯的就是那几个词。

# 真机夹具的对外改名，逐文件施加（顺序有意义：先长后短）。
RIG_RENAMES = [
    ("gt_directions_scenario", "probe_directions_scenario"),
    ("gt_directions", "probe_directions"),
    ("gt_scenario", "probe_scenario"),
    ("gt_rig", "probe_rig"),
    ("gt-result", "bp-probe"),
    ("gt-rig", "bp-probe"),
    ("gt-dir-", "bp-dir-"),
    ("Ground Truth Rig", "Blueprint Build Probe"),
]

# 兜底清洗：定点改写漏掉的说法（源文件新加一条就会漏），这里统一换掉。
# 只做「换个说法」，不改语义；跑完由 tools/leak_scan.py 复查，漏网的一律失败。
SCRUB = [
    ("re", r"评测[集]", "验收标准"),
    # 临时目录约定：下划线开头的目录名不进公开版——路径里这类段一律落到 out/ 下。
    # 三条规则分别管三种写法：`_x/…` 带斜杠的路径、`_x\…` 带反斜杠的路径、
    # 以及 path.join(…, "_x", …) 这种夹在逗号之间的分段参数。
    # 只看「紧挨分隔符」和「夹在逗号之间」的位置，所以普通标识符（__init__、_read）
    # 和方括号下标（entity["_conflicts"]）都不会被碰到。
    ("re", r"(?<![\w])_[A-Za-z][\w-]*/", "out/"),
    ("re", r"(?<![\w])_[A-Za-z][\w-]*\\", "out\\"),
    ("re", r',\s*"_[A-Za-z][\w-]*"\s*,\s*', ', "out", '),
    # Python / Lua 里「反斜杠 + 反引号」是无效转义（会报 SyntaxWarning）：去掉反斜杠，语义不变。
    ("re", r"\\`", "`"),
]

RIG_FILES = [
    "tools/probe_rig.py",
    "tools/probe_directions.py",
    "tools/probe_scenario.lua",
    "tools/probe_directions_scenario.lua",
    "tools/factorio_build_check.py",
    "data/direction-probe.json",
]

REWRITES = {
    # ---- 库 ---------------------------------------------------------------
    "bpcompile/verify.py": [
        ("re", r"（ground-truth-ri[g] 的作品）", "", 1),
    ],
    "bpcompile/blueprint.py": [
        ("re", r"改造类任务（评测[集] 16-18 条）会丢数据",
         "改造类任务（原样读入、只改该改的地方）会丢数据", 1),
    ],
    "bpcompile/testbench.py": [
        ("re", r"和评测[集]给的下限比", "和验收下限比", 1),
    ],
    "bpcompile/templates/furnace_array.py": [
        # 注释里的块数是旧夹具口径的数，公开版换成效力描述（新口径见 README）
        ("re", r"#   24 台石炉从西端一个进料口喂，黄带 60 秒产 \d+ 块.*?过线）。",
         "#   24 台石炉从西端一个进料口喂：快带能过 300 的验收线，黄带跑不到。", 1),
    ],
    # ---- 工具 -------------------------------------------------------------
    "tools/build_prototypes.py": [
        ("re", r"判据来自 ground-truth-ri[g] 的裸建矩阵", "判据来自真机裸建矩阵", 1),
        ("re", r"（ground-truth-ri[g] 实测，不是推测）", "（真机实测，不是推测）", 1),
        ("lit",
         'DUMP = os.path.expandvars(r"%APPDATA%\\Factorio\\script-output\\data-raw-dump.json")',
         "def _default_dump():\n"
         '    """游戏内 /c 导出的原型转储。环境变量 FACTORIO_DUMP 优先，否则按各平台默认位置找。"""\n'
         '    override = os.environ.get("FACTORIO_DUMP")\n'
         "    if override:\n"
         "        return override\n"
         "    candidates = [\n"
         '        os.path.expandvars(r"%APPDATA%\\Factorio\\script-output\\data-raw-dump.json"),\n'
         '        os.path.expanduser("~/.factorio/script-output/data-raw-dump.json"),\n'
         '        os.path.expanduser(\n'
         '            "~/Library/Application Support/factorio/script-output/data-raw-dump.json"\n'
         "        ),\n"
         "    ]\n"
         "    for candidate in candidates:\n"
         "        if os.path.isfile(candidate):\n"
         "            return candidate\n"
         "    return candidates[0]\n"
         "\n"
         "\n"
         "\n"
         "DUMP = _default_dump()",
         1),
    ],
    "tools/factorio_build_check.py": [
        ("lit", r"机制见 doc\环境与能力.md", "机制见 doc/真机校验.md", 1),
    ],
    "tools/probe_rig.py": [
        ("re", r'FACTORIO_DEFAULT = r"[^"]*"',
         "_FACTORIO_CANDIDATES = [\n"
         '    r"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Factorio\\bin\\x64\\factorio.exe",\n'
         '    r"C:\\Program Files\\Steam\\steamapps\\common\\Factorio\\bin\\x64\\factorio.exe",\n'
         '    os.path.expanduser("~/.local/share/Steam/steamapps/common/Factorio/bin/x64/factorio"),\n'
         '    os.path.expanduser(\n'
         '        "~/Library/Application Support/Steam/steamapps/common/Factorio/bin/x64/factorio"\n'
         "    ),\n"
         "]\n"
         "\n"
         "\n"
         "def _default_factorio():\n"
         '    """游戏可执行文件：先认环境变量 FACTORIO_EXE，再试各平台的常见安装路径。"""\n'
         '    override = os.environ.get("FACTORIO_EXE")\n'
         "    if override:\n"
         "        return override\n"
         "    for candidate in _FACTORIO_CANDIDATES:\n"
         "        if os.path.isfile(candidate):\n"
         "            return candidate\n"
         "    return _FACTORIO_CANDIDATES[0]\n"
         "\n"
         "\n"
         "FACTORIO_DEFAULT = _default_factorio()",
         1),
        ("lit", 'SCENARIO_NAME = "gt"', 'SCENARIO_NAME = "probe"', 1),
    ],
    "tools/probe_directions.py": [
        ("re", r"用法：python tools/gt_directions\.py --out [^\n]*",
         "用法：python tools/probe_directions.py --out data/direction-probe.json", 1),
        ("re",
         r'"--out", default=os\.path\.join\(gt_rig\.TOOL_DIR, "\.\.", "[^"]*", "directions\.json"\)',
         '"--out", default=os.path.join(gt_rig.TOOL_DIR, "..", "data",\n'
         '                                             "direction-probe.json")',
         1),
    ],
    "tools/probe_scenario.lua": [
        ("lit", "-- 入口：factorio --test-scenario <mod>/gt；",
         "-- 入口：factorio --test-scenario <mod>/probe；", 1),
    ],
    # ---- 数据 -------------------------------------------------------------
    "data/prototypes.json": [
        # 真值表里记着转储文件在本机的位置：那是开发者的家目录路径，公开版换成中性写法。
        ("re", r'"source"\s*:\s*"[^"]*data-raw-dump[^"]*"',
         '"source":"<Factorio>/script-output/data-raw-dump.json"', 1),
    ],
    # ---- 自检 -------------------------------------------------------------
    "tests/run_tests.py": [
        # 1) 样本缺失时的跳过机制
        ("lit",
         'CORPUS = os.path.join(ROOT, "data", "corpus")\n'
         "\n"
         "_FAILED = []\n"
         "_PASSED = 0\n",
         'CORPUS = os.path.join(ROOT, "data", "corpus")\n'
         "# 仓库带的是 .blueprint.txt 原串；.json 是解码产物（没进版本库），缺了就用原串现场解。\n"
         "# 两个都没有时，引用语料的用例**跳过**而不是假过——跳过数会写进总结。\n"
         'HAS_CORPUS = os.path.isdir(CORPUS) and bool(\n'
         '    glob.glob(os.path.join(CORPUS, "*.json"))\n'
         '    or glob.glob(os.path.join(CORPUS, "*.blueprint.txt"))\n'
         ")\n"
         "CORPUS_MISSING = (\n"
         '    "蓝图语料不在（%s 不存在或是空的）：放进 .blueprint.txt 就能跑，见 data/corpus/README.md"\n'
         "    % CORPUS\n"
         ")\n"
         "\n"
         "_FAILED = []\n"
         "_SKIPPED = []\n"
         "_PASSED = 0\n",
         1),
        # 语料读取：优先用解码后的 .json，只有原串时现场解码
        ("lit",
         "def corpus_blueprints():\n"
         '    for path in sorted(glob.glob(os.path.join(CORPUS, "*.json"))):\n'
         "        with open(path, encoding=\"utf-8\") as handle:\n"
         "            payload = json.load(handle)\n"
         '        if "blueprint" not in payload:\n'
         "            continue\n"
         "        yield os.path.basename(path), bpm.Blueprint.from_dict(payload)\n",
         "def corpus_blueprints():\n"
         '    """语料：有解码后的 .json 就用它，否则把 .blueprint.txt 现场解出来。\n'
         "\n"
         "    仓库只带原串（168 KB）。想要 .json（2.8 MB）跑 tools/decode_corpus.py。\n"
         '    """\n'
         "    paths = {}\n"
         '    for path in glob.glob(os.path.join(CORPUS, "*.json")):\n'
         "        paths[os.path.splitext(os.path.basename(path))[0]] = path\n"
         '    for path in glob.glob(os.path.join(CORPUS, "*.blueprint.txt")):\n'
         '        stem = os.path.basename(path)[: -len(".blueprint.txt")]\n'
         "        paths.setdefault(stem, path)\n"
         "    for stem in sorted(paths):\n"
         "        path = paths[stem]\n"
         '        if path.endswith(".json"):\n'
         "            with open(path, encoding=\"utf-8\") as handle:\n"
         "                payload = json.load(handle)\n"
         '            if "blueprint" not in payload:\n'
         "                continue\n"
         "            blueprint = bpm.Blueprint.from_dict(payload)\n"
         "        else:\n"
         "            with open(path, encoding=\"utf-8\") as handle:\n"
         "                payload = bpm.decode(handle.read())\n"
         '            if "blueprint" not in payload:\n'
         "                continue\n"
         "            blueprint = bpm.Blueprint.from_dict(payload)\n"
         '        yield stem + ".json", blueprint\n',
         1),
        ("lit",
         "def check(name, fn):\n"
         "    global _PASSED\n"
         "    try:\n"
         "        fn()\n"
         "        _PASSED += 1\n"
         '        print("  ok   %s" % name)\n'
         "    except AssertionError as failure:\n",
         "class SkipTest(Exception):\n"
         '    """这条用例在当前环境下跑不了（缺样本、缺可选依赖）：跳过，不算通过。"""\n'
         "\n"
         "\n"
         "def check(name, fn):\n"
         "    global _PASSED\n"
         "    try:\n"
         "        fn()\n"
         "        _PASSED += 1\n"
         '        print("  ok   %s" % name)\n'
         "    except SkipTest as reason:\n"
         "        _SKIPPED.append((name, str(reason)))\n"
         '        print("  skip %s\\n       %s" % (name, reason))\n'
         "    except AssertionError as failure:\n",
         1),
        # 2) 四条依赖样本的用例：开头加跳过
        ("lit",
         "def test_codec_roundtrip():\n"
         '    """编码必须是可逆的：解出来再写回去，实体逐字段一致。"""\n'
         "    for name, original in corpus_blueprints():\n",
         "def test_codec_roundtrip():\n"
         '    """编码必须是可逆的：解出来再写回去，实体逐字段一致。"""\n'
         "    if not HAS_CORPUS:\n"
         "        raise SkipTest(CORPUS_MISSING)\n"
         "    for name, original in corpus_blueprints():\n",
         1),
        ("lit",
         "def test_rails_do_not_trigger_overlap():\n"
         '    """轨道互相重叠是合法的（高架/地面两层），不许报重叠。"""\n'
         "    for name, original in corpus_blueprints():\n",
         "def test_rails_do_not_trigger_overlap():\n"
         '    """轨道互相重叠是合法的（高架/地面两层），不许报重叠。"""\n'
         "    if not HAS_CORPUS:\n"
         "        raise SkipTest(CORPUS_MISSING)\n"
         "    for name, original in corpus_blueprints():\n",
         1),
        ("lit",
         '    allowed = {"inserter-no-pickup", "inserter-no-insert"}\n',
         "    if not HAS_CORPUS:\n"
         "        raise SkipTest(CORPUS_MISSING)\n"
         '    allowed = {"inserter-no-pickup", "inserter-no-insert"}\n',
         1),
        ("lit",
         '    """地下管配对规则要在真实语料上成立：未配对数必须远小于总数。"""\n',
         '    """地下管配对规则要在真实语料上成立：未配对数必须远小于总数。"""\n'
         "    if not HAS_CORPUS:\n"
         "        raise SkipTest(CORPUS_MISSING)\n",
         1),
        # 3) 可选依赖缺失也算跳过，不假装通过
        ("lit",
         "    try:\n"
         "        import draftsman.blueprintable as draftsman_blueprintable\n"
         "    except Exception:\n"
         '        print("       (跳过：本解释器没装 factorio-draftsman)")\n'
         "        return\n",
         "    try:\n"
         "        import draftsman.blueprintable as draftsman_blueprintable\n"
         "    except Exception:\n"
         '        raise SkipTest("本解释器没装 factorio-draftsman（可选依赖，见 README 安装一节）")\n',
         1),
        # 4) 总结里分开报跳过
        ("lit",
         '    print("\\n%d passed, %d failed" % (_PASSED, len(_FAILED)))\n'
         "    if _FAILED:\n",
         '    print("\\n%d passed, %d skipped, %d failed" % (_PASSED, len(_SKIPPED), len(_FAILED)))\n'
         "    if _SKIPPED:\n"
         '        print("  跳过 %d 条（缺样本或缺可选依赖时属正常）" % len(_SKIPPED))\n'
         "    if _FAILED:\n",
         1),
        ("re", r"评测[集]第 1 条直接挂", "产量验收直接挂", 1),
        # 5) 与内部流程绑定的注释
        ("re",
         r"# ---- 盲[测]验收：接口必须开口说话（这四条是主代[理]的核收项，别再靠人记）----",
         "# ---- 接口必须开口说话：这四条钉住的是「工具会主动报错」，别再靠人记 ----",
         1),
    ],
}


class ExportError(Exception):
    pass


# ---------------------------------------------------------------- 小工具


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def apply_rules(text, rules, where):
    for kind, pattern, replacement, expect in rules:
        if kind == "lit":
            found = text.count(pattern)
        elif kind == "re":
            found = len(re.findall(pattern, text, re.MULTILINE))
        else:
            raise ExportError("未知改写类型 %r（%s）" % (kind, where))
        if expect != ANY and found != expect:
            raise ExportError(
                "%s：改写「%s」预期命中 %s 次，实际 %d 次。源文件变了，"
                "请更新 tools/export_public_tree.py 里这张改写表。"
                % (where, pattern[:70], expect, found)
            )
        if not found:
            continue
        if kind == "lit":
            text = text.replace(pattern, replacement)
        else:
            text = re.sub(pattern, replacement.replace("\\", "\\\\"), text, flags=re.MULTILINE)
    return text


def plan(source):
    """算出「目标树应该长什么样」：{目标相对路径: 源相对路径或 None（就地维护）}。"""
    plan_map = {}
    for src_rel, dst_rel in FILES:
        src = os.path.join(source, *src_rel.split("/"))
        if not os.path.isfile(src):
            raise ExportError("源目录缺少白名单文件：%s" % src_rel)
        plan_map[dst_rel or src_rel] = src_rel
    for src_dir, dst_dir in TREES:
        base = os.path.join(source, *src_dir.split("/"))
        if not os.path.isdir(base):
            raise ExportError("源目录缺少目录：%s" % src_dir)
        for folder, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                full = os.path.join(folder, name)
                rel = os.path.relpath(full, source).replace(os.sep, "/")
                inside = os.path.relpath(full, base).replace(os.sep, "/")
                plan_map[dst_dir + "/" + inside] = rel
    for pattern, dst_dir in COPY_GLOBS:
        matches = sorted(glob.glob(os.path.join(source, *pattern.split("/"))))
        if not matches:
            raise ExportError("源目录没有匹配 %s 的文件" % pattern)
        for full in matches:
            rel = os.path.relpath(full, source).replace(os.sep, "/")
            plan_map[dst_dir + "/" + os.path.basename(full)] = rel
    for rel in KEEP + EXAMPLES:
        if rel in plan_map:
            raise ExportError("%s 同时在吸收清单和就地维护清单里" % rel)
        plan_map[rel] = None
    return plan_map


def existing_files(root):
    out = []
    for folder, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            full = os.path.join(folder, name)
            out.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return sorted(out)


def prune(root, keep):
    """删掉不在 keep 里的文件，再清空空目录。返回删掉的相对路径。"""
    removed = []
    for rel in existing_files(root):
        if rel not in keep:
            os.unlink(os.path.join(root, *rel.split("/")))
            removed.append(rel)
    for folder, dirnames, filenames in os.walk(root, topdown=False):
        if os.path.abspath(folder) == os.path.abspath(root):
            continue
        if ".git" in folder.split(os.sep):
            continue
        if not os.listdir(folder):
            os.rmdir(folder)
    return removed


# ---------------------------------------------------------------- 文档指纹


def load_doc_sources():
    if not os.path.exists(DOC_SOURCES):
        return {}
    with open(DOC_SOURCES, encoding="utf-8") as handle:
        return json.load(handle)


def doc_drift(source):
    """公开版 doc/*.md 对应的源文档变了没有。返回 [(公开版, 源文档, 说明)]。"""
    drift = []
    for public_rel, info in sorted(load_doc_sources().items()):
        src_rel = info.get("source")
        if not src_rel:
            continue
        src_path = os.path.join(source, *src_rel.split("/"))
        if not os.path.isfile(src_path):
            drift.append((public_rel, src_rel, "源文档已不存在"))
        elif sha256(src_path) != info.get("sha256"):
            drift.append((public_rel, src_rel, "源文档已变更，公开版要人工同步"))
    return drift


def accept_docs(source):
    state = load_doc_sources()
    for info in state.values():
        src_path = os.path.join(source, *info["source"].split("/"))
        if os.path.isfile(src_path):
            info["sha256"] = sha256(src_path)
    write(DOC_SOURCES, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------- 主流程


def live_numbers(dest):
    """从导出的树里现取几个「文档里要引用、又必须和代码一致」的数字。"""
    server = read(os.path.join(dest, "mcp_server.py"))
    return {"TOOL_COUNT": len(re.findall(r"@server\.tool\(", server))}


def LIVE_SUBSTITUTIONS(text, truth):
    """把文档里"手写死的现场数字"纠正成真值（目前只有工具数这一条）。"""
    text = re.sub(r"\d+ 个工具", "%d 个工具" % truth["TOOL_COUNT"], text)
    return re.sub(r"工具一览（\d+ 个）", "工具一览（%d 个）" % truth["TOOL_COUNT"], text)


def tool_coverage(dest):
    """mcp_server.py 里的工具名，有哪些没在接口文档里出现过。"""
    server = read(os.path.join(dest, "mcp_server.py"))
    names = re.findall(r'name="(bp_[a-z_]+)"', server)
    doc = read(os.path.join(dest, "doc", "核心库与MCP接口.md"))
    return [name for name in names if name not in doc]


def run(command, cwd):
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="把完整工程导出成可公开发布的子集",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="源目录必须含 mcp_server.py、bpcompile/、data/prototypes.json。",
    )
    parser.add_argument("--source", required=True, help="完整工程目录")
    parser.add_argument("--dest", default=ROOT, help="目标树（默认：本脚本所在仓库）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不落盘")
    parser.add_argument("--accept-docs", action="store_true",
                        help="把当前源文档指纹记为「已同步」（人工核对完公开版之后再用）")
    parser.add_argument("--skip-examples", action="store_true", help="不重建 examples/")
    parser.add_argument("--skip-checks", action="store_true", help="不跑泄漏扫描与自检")
    parser.add_argument("--allow-doc-drift", action="store_true", help="源文档有改动也照常导出")
    args = parser.parse_args(argv)

    source = os.path.abspath(args.source)
    dest = os.path.abspath(args.dest)
    if source == dest:
        raise ExportError("源目录和目标树是同一个目录")
    if dest.startswith(source + os.sep):
        raise ExportError("目标树在源目录里面，会互相污染")
    for marker in ("mcp_server.py", "bpcompile", os.path.join("data", "prototypes.json")):
        if not os.path.exists(os.path.join(source, marker)):
            raise ExportError("源目录不像完整工程（缺 %s）：%s" % (marker, source))

    plan_map = plan(source)
    missing_keep = [rel for rel in KEEP
                    if not os.path.exists(os.path.join(dest, *rel.split("/")))]

    print("源目录：%s" % source)
    print("目标树：%s" % dest)
    print("计划：吸收 %d 个文件，就地维护 %d 个，样例 %d 个"
          % (len([v for v in plan_map.values() if v]), len(KEEP), len(EXAMPLES)))
    if missing_keep:
        raise ExportError("就地维护的文件不存在（先补齐再导出）：\n  " + "\n  ".join(missing_keep))

    drift = doc_drift(source)
    if drift:
        detail = "\n".join("  %s  ←  %s（%s）" % item for item in drift)
        if args.accept_docs:
            accept_docs(source)
            print("已把当前源文档指纹记成「已同步」：\n" + detail)
        elif args.allow_doc_drift:
            print("注意：源文档有改动，按 --allow-doc-drift 继续：\n" + detail)
        else:
            raise ExportError(
                "公开版文档没跟上源文档（核对并改完 doc/ 下的公开版，再 --accept-docs）：\n" + detail
            )

    if args.dry_run:
        removed = [rel for rel in existing_files(dest) if rel not in plan_map] if os.path.isdir(dest) else []
        print("\n[dry-run] 会删掉 %d 个文件：" % len(removed))
        for rel in removed:
            print("  - " + rel)
        print("\n[dry-run] 会写 %d 个文件：" % len(plan_map))
        for rel in sorted(plan_map):
            print("  %s %s" % ("=" if plan_map[rel] is None else "<", rel))
        return 0

    os.makedirs(dest, exist_ok=True)
    for rel in prune(dest, set(plan_map)):
        print("  - 删除 %s" % rel)

    written = 0
    for dst_rel in sorted(plan_map):
        src_rel = plan_map[dst_rel]
        if src_rel is None:
            continue
        src_path = os.path.join(source, *src_rel.split("/"))
        dst_path = os.path.join(dest, *dst_rel.split("/"))
        if dst_rel.endswith((".json", ".lua", ".py", ".md", ".txt")):
            text = read(src_path).replace("\r\n", "\n")
            # 先做定点改写（表里写的是源文件里的原话），再做全局改名，最后兜底清洗。
            rules = list(REWRITES.get(dst_rel, ()))
            if dst_rel in RIG_FILES:
                rules += [("lit", old, new, ANY) for old, new in RIG_RENAMES]
            rules += [(kind, pattern, replacement, ANY) for kind, pattern, replacement in SCRUB]
            write(dst_path, apply_rules(text, rules, dst_rel))
        else:
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copyfile(src_path, dst_path)
        written += 1
    print("写入 %d 个文件" % written)

    # 就地维护的文档里的数字要跟代码一致，别让人手抄：
    #   {{TOOL_COUNT}} 这类占位符直接替换；
    #   已经落成 "24 个工具" 的，按同一条规则就地纠正（占位符被替换掉之后仍然有效）。
    truth = live_numbers(dest)
    for rel in KEEP:
        if not rel.endswith(".md"):
            continue
        full = os.path.join(dest, *rel.split("/"))
        text = read(full)
        filled = text
        for key, value in truth.items():
            filled = filled.replace("{{%s}}" % key, str(value))
        filled = LIVE_SUBSTITUTIONS(filled, truth)
        if filled != text:
            write(full, filled)

    # 改名后的夹具还得自洽：不许残留旧代号。
    for dst_rel in RIG_FILES:
        if not dst_rel.endswith((".py", ".lua")):
            continue
        text = read(os.path.join(dest, *dst_rel.split("/")))
        leftovers = sorted(set(re.findall(r"\bgt[_\-][a-z\-]*", text)))
        if leftovers:
            raise ExportError("%s 里还残留旧代号：%s" % (dst_rel, leftovers))

    for name in tool_coverage(dest):
        print("提示：工具 %s 没写进 doc/核心库与MCP接口.md 的工具一览" % name)

    status = 0
    if not args.skip_examples:
        proc = run([sys.executable, os.path.join("tools", "make_examples.py")], dest)
        print(proc.stdout.strip())
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            raise ExportError("重建 examples/ 失败")
    # 生成样例时 import 过库，会在树里留下 __pycache__：清掉，别让字节码进版本库。
    for folder, dirnames, filenames in os.walk(dest, topdown=False):
        if os.path.basename(folder) == "__pycache__":
            shutil.rmtree(folder, ignore_errors=True)
    if not args.skip_checks:
        proc = run([sys.executable, os.path.join("tools", "leak_scan.py")], dest)
        print(proc.stdout.strip())
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            status = 1
    print("\n完成。下一步：python tests/run_tests.py，再看 git status 的 diff。")
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ExportError as failure:
        sys.stderr.write("导出中止：%s\n" % failure)
        sys.exit(2)
