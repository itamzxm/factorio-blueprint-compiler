#!/usr/bin/env python3
r"""发布前自检：扫公开树里的敏感词与敏感路径，命中即非零退出。

这个仓库的内容是从完整工程导出的（见 tools/export_public_tree.py）。导出过程会改写掉
开发期的字句，但改写表只能覆盖已知的地方——**这份扫描器是最后一道闸**：
导出脚本每次跑完自动调它，人工也可以随时单独跑。

用法：

    python tools/leak_scan.py                        # 扫本仓库
    python tools/leak_scan.py --words <词表>          # 换一份词表
    python tools/leak_scan.py --json                 # 机器可读
    python tools/leak_scan.py --self-test            # 验扫描器本身还有效

**词表不进仓库**：仓库是公开的，而词表本身就是「不该出现的字眼」的清单——
把它放进来，等于把要防的东西公开发布。所以它放在仓库外面。
默认读仓库根目录的上一级 leak-words.local.txt（也可用 --words 或环境变量 LEAK_WORDS 指定）。
没有词表时脚本**不假装通过**：它只做结构性检查，然后以退出码 3 告诉你「词表那层没查」。

词表格式（一行一条，# 开头是注释，空行忽略）：

    word:某某           # 正文里不许出现的子串（默认类型，可省略前缀，不分大小写）
    case:某某           # 同上，但区分大小写（想抓某个混合大小写的写法、又不想误伤全大写的环境变量时用）
    path:C:\\Users\\     # 正文里不许出现的正则
    dir:xxx             # 树里不许有的目录名（任一层的路径段）
    file:xxx.md         # 树里不许有的文件名

退出码：0 干净 / 1 有命中 / 2 用法错误 / 3 没找到词表（词表检查没做）
"""

import argparse
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_WORDS = os.path.join(os.path.dirname(REPO), "leak-words.local.txt")

# 内置规则：与具体项目无关的那几条（用户家目录路径之类）。项目自己的敏感词走词表。
BUILTIN = [
    ("path", r"[A-Za-z]:[/\\]Users[/\\]", "暴露开发者本机家目录路径"),
]

SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"}
SKIP_SUFFIX = (".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz")


class Rule:
    def __init__(self, kind, pattern, note=""):
        self.kind = kind
        self.pattern = pattern
        self.note = note
        if kind == "word":
            self.compiled = re.compile(re.escape(pattern), re.IGNORECASE)
        elif kind == "case":
            self.compiled = re.compile(re.escape(pattern))
        elif kind == "path":
            self.compiled = re.compile(pattern)
        elif kind in ("dir", "file"):
            self.compiled = None
            self.literal = pattern.lower()
        else:
            raise ValueError("未知规则类型：%r" % kind)

    def search_text(self, text):
        if self.compiled is None:
            return None
        return self.compiled.search(text)

    def matches_name(self, name):
        if self.kind not in ("dir", "file"):
            return False
        return name.lower() == self.literal


def load_rules(path):
    """读词表，返回 Rule 列表。格式错误直接抛，不静默吞掉一条规则。"""
    rules = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            kind = "word"
            body = raw
            if ":" in raw:
                head, tail = raw.split(":", 1)
                if head in ("word", "case", "path", "dir", "file"):
                    kind, body = head, tail.strip()
            if not body:
                raise ValueError("%s 第 %d 行：%r 没有内容" % (path, number, raw))
            if kind == "path":
                re.compile(body)  # 提前验一遍正则
            rules.append(Rule(kind, body))
    return rules


def iter_files(root):
    for folder, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            full = os.path.join(folder, name)
            yield full, os.path.relpath(full, root).replace(os.sep, "/")


def scan(root, rules, words_path=None):
    """返回 (命中列表, 正文扫过的文件数, 文件总数)。"""
    hits = []
    scanned = 0
    words_real = os.path.realpath(words_path) if words_path else None
    files = list(iter_files(root))

    # 先看路径本身：目录名/文件名就不许出现
    for folder, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in dirnames + filenames:
            for rule in rules:
                if rule.matches_name(name):
                    rel = os.path.relpath(os.path.join(folder, name), root)
                    hits.append({
                        "where": rel.replace(os.sep, "/"),
                        "rule": rule.kind,
                        "pattern": rule.pattern,
                        "note": rule.note or "路径里出现禁用名",
                    })
                    break

    for full, rel in files:
        if words_real and os.path.realpath(full) == words_real:
            continue
        if rel.endswith(SKIP_SUFFIX):
            continue
        scanned += 1
        try:
            with open(full, "rb") as handle:
                data = handle.read()
        except OSError as failure:
            hits.append({"where": rel, "rule": "io", "pattern": "", "note": str(failure)})
            continue
        if len(data) > 8 * 1024 * 1024:
            hits.append({"where": rel, "rule": "size", "pattern": "",
                         "note": "单文件超过 8MB，人工确认一下该不该进仓库"})
            continue
        text = data.decode("utf-8", errors="replace")
        for rule in rules:
            if rule.kind in ("dir", "file"):
                continue
            found = rule.search_text(text)
            if not found:
                continue
            start = max(0, found.start() - 40)
            snippet = text[start:found.end() + 40].replace("\n", " ").replace("\r", " ")
            line_number = text.count("\n", 0, found.start()) + 1
            hits.append({
                "where": "%s:%d" % (rel, line_number),
                "rule": rule.kind,
                "pattern": rule.pattern,
                "note": rule.note or "命中禁用内容",
                "snippet": snippet.strip()[:120],
            })
            # 同一文件里不同规则各报一条（同一条规则只报第一处），别让一条命中断掉其余检查。
    return hits, scanned, len(files)


def run_self_test():
    """造一棵假树：一个干净文件、一个带家目录路径的文件、一个禁用目录名。"""
    checks = []
    slash = chr(92)
    with tempfile.TemporaryDirectory() as sandbox:
        scan_dir = os.path.join(sandbox, "tree")
        os.makedirs(os.path.join(scan_dir, "hidden-dir"))
        clean = os.path.join(scan_dir, "clean.md")
        dirty = os.path.join(scan_dir, "dirty.md")
        inside = os.path.join(scan_dir, "hidden-dir", "x.txt")
        words = os.path.join(sandbox, "words.txt")
        with open(clean, "w", encoding="utf-8") as handle:
            handle.write("这是一份正常的说明文档，里面没有任何敏感内容。\n")
        with open(dirty, "w", encoding="utf-8") as handle:
            handle.write("转储在本机 C:" + slash + "Users" + slash + "someone" + slash + "dump.json\n")
        with open(inside, "w", encoding="utf-8") as handle:
            handle.write("ok\n")
        with open(words, "w", encoding="utf-8") as handle:
            handle.write("# 自检用词表\ndir:hidden-dir\n")
        rules = [Rule(kind, pattern, note) for kind, pattern, note in BUILTIN] + load_rules(words)
        hits, scanned, total = scan(scan_dir, rules, words)
        wheres = sorted({hit["where"] for hit in hits})
        checks.append(("扫到了文件", scanned == 3, "%d 个" % scanned))
        checks.append(("干净文件没被误报", not any(hit["where"] == "clean.md" for hit in hits), str(wheres)))
        checks.append(("抓到家目录路径", any(hit["where"].startswith("dirty.md") for hit in hits), str(wheres)))
        checks.append(("抓到禁用目录名", any(hit["where"].startswith("hidden-dir") for hit in hits), str(wheres)))
    ok = all(passed for _, passed, _ in checks)
    for label, passed, detail in checks:
        print("  %s %s  %s" % ("ok  " if passed else "FAIL", label, detail))
    print("自检%s" % ("通过" if ok else "失败"))
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="扫公开树里的敏感词与敏感路径；命中即非零退出",
        epilog="没有词表时只做结构性检查并以退出码 3 结束——不假装通过。",
    )
    parser.add_argument("root", nargs="?", default=REPO, help="要扫的目录（默认：本仓库根）")
    parser.add_argument("--words", default=os.environ.get("LEAK_WORDS") or DEFAULT_WORDS,
                        help="词表路径（默认 %s）" % DEFAULT_WORDS)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--quiet", action="store_true", help="只报结论")
    parser.add_argument("--allow-empty-words", action="store_true",
                        help="没有词表也当通过（不推荐：词表那一层就没查）")
    parser.add_argument("--self-test", action="store_true", help="自检：确认扫描器还能抓到东西")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        sys.stderr.write("不是目录：%s\n" % root)
        return 2

    rules = [Rule(kind, pattern, note) for kind, pattern, note in BUILTIN]
    words_used = None
    if args.words and os.path.isfile(args.words):
        try:
            extra = load_rules(args.words)
        except ValueError as failure:
            sys.stderr.write("词表有问题：%s\n" % failure)
            return 2
        rules += extra
        words_used = args.words
    hits, scanned, total = scan(root, rules, words_used)

    payload = {
        "root": root,
        "files": total,
        "scanned": scanned,
        "words": words_used,
        "rules": len(rules),
        "hits": hits,
        "ok": not hits and bool(words_used),
        "words_checked": bool(words_used),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not args.quiet:
            print("扫描 %s" % root)
            print("  文件 %d 个（正文扫了 %d 个，图片等跳过），规则 %d 条%s"
                  % (total, scanned, len(rules),
                     "（含词表 %s）" % words_used if words_used else "（只有内置结构规则）"))
        for hit in hits:
            print("  命中 %s  [%s] %s" % (hit["where"], hit["rule"], hit["pattern"]))
            if hit.get("snippet"):
                print("       …%s…" % hit["snippet"])
        if hits:
            print("  %d 处命中 → 不干净" % len(hits))
        elif args.quiet:
            print("0 命中")

    if hits:
        return 1
    if not words_used:
        if args.allow_empty_words:
            sys.stderr.write("注意：没有词表，只做了结构性检查（%s 不存在）\n" % args.words)
            return 0
        sys.stderr.write(
            "没有词表，敏感词那一层没查：%s\n"
            "  用 --words <文件> 指定，或把词表放到这个位置；"
            "确认只做结构检查时加 --allow-empty-words\n" % args.words
        )
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
