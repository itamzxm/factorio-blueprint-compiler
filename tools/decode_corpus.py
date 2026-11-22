#!/usr/bin/env python3
r"""把 data/corpus/ 里的蓝图串解码成 JSON。

蓝图串的格式是 `<1 字节版本字符> + base64(zlib(UTF-8 JSON))`——本脚本只用标准库，
所以它同时也是"这格式长什么样"的活文档。

用法：

    python tools/decode_corpus.py            # 27 张全解成 <id>.json（放回 data/corpus/）
    python tools/decode_corpus.py --check    # 只解码校验，不写文件
    python tools/decode_corpus.py --list     # 只看清单：标题、作者、实体数、来源
    python tools/decode_corpus.py --out DIR  # 解到别处
    python tools/decode_corpus.py --id=<id>  # 只解一张打到标准输出（id 以 - 开头，必须用 = 形式）

仓库里只带 `.blueprint.txt`（168 KB，原串）。`.json` 是解码产物（2.8 MB），跑一次这个脚本就有，
所以没进版本库。解码出来的 `.json` 会让 `tests/run_tests.py` 里那几条"真实蓝图"用例真正跑起来
（没解之前它们会跳过）。
"""

import argparse
import base64
import glob
import json
import os
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "data", "corpus")
SUFFIX = ".blueprint.txt"


def decode_text(text):
    """蓝图串 → 解码后的 dict（顶层是 {"blueprint": …} 或 {"blueprint_book": …}）。"""
    body = "".join(text.split())
    if not body:
        raise ValueError("空文件")
    # 第一个字符是版本字节（实测都是 "0"），后面才是 base64
    payload = base64.b64decode(body[1:] + "=" * (-len(body[1:]) % 4))
    return json.loads(zlib.decompress(payload).decode("utf-8"))


def corpus_files():
    return sorted(glob.glob(os.path.join(CORPUS, "*" + SUFFIX)))


def load_index():
    path = os.path.join(CORPUS, "index.json")
    if not os.path.isfile(path):
        return {"items": []}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(description="解码 data/corpus/ 里的蓝图串")
    parser.add_argument("--out", default=CORPUS, help="解到哪个目录（默认 data/corpus）")
    parser.add_argument("--check", action="store_true", help="只解码校验，不写文件")
    parser.add_argument("--list", action="store_true", help="只列清单，不解码")
    parser.add_argument("-i", "--id", help="只解这一张，结果打到标准输出")
    args = parser.parse_args(argv)

    if args.list:
        index = load_index()
        for item in index.get("items") or []:
            print("%-26s %-42s %-14s %5s 实体  %s" % (
                item.get("id"),
                (item.get("title") or "")[:42],
                (item.get("author") or "—")[:14],
                item.get("entities"),
                item.get("source"),
            ))
        print("共 %d 张" % len(index.get("items") or []))
        return 0

    if args.id:
        path = os.path.join(CORPUS, args.id + SUFFIX)
        if not os.path.isfile(path):
            sys.stderr.write("没有这张：%s\n" % path)
            return 2
        with open(path, encoding="utf-8") as handle:
            print(json.dumps(decode_text(handle.read()), ensure_ascii=False, indent=1))
        return 0

    files = corpus_files()
    if not files:
        sys.stderr.write("data/corpus/ 里没有 *.blueprint.txt\n")
        return 2

    written = 0
    skipped = 0
    failures = []
    for path in files:
        stem = os.path.basename(path)[: -len(SUFFIX)]
        try:
            with open(path, encoding="utf-8") as handle:
                payload = decode_text(handle.read())
        except Exception as failure:  # noqa: BLE001
            failures.append((stem, str(failure)))
            continue
        target = os.path.join(args.out, stem + ".json")
        if args.check:
            continue
        if os.path.isfile(target):
            skipped += 1
            continue
        os.makedirs(args.out, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        written += 1

    for stem, reason in failures:
        sys.stderr.write("解码失败 %s：%s\n" % (stem, reason))
    if args.check:
        print("解码校验：%d 张，失败 %d 张" % (len(files) - len(failures), len(failures)))
    else:
        print("解码 %d 张：新写 %d 个 .json，已存在跳过 %d 个，失败 %d 张"
              % (len(files), written, skipped, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
