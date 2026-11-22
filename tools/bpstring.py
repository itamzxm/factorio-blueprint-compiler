#!/usr/bin/env python3
r"""Factorio 蓝图字符串 ⇄ JSON。

只做语料分析用的最小实现。**产品路径不要用这个编解码器**——编码层用 factorio-draftsman，
那里有更完整的 2.0/2.1 字段处理。这里留着是为了能在没有 venv 的情况下直接跑语料统计。

格式（实测 2.1.24）：
    <1 字节版本字符> + base64( zlib( UTF-8 JSON ) )
    1.1 及以前版本字符是 "0"；2.0/2.1 实测仍是 "0"，
    真正的世代差异在 JSON 内容里（见 doc/蓝图格式与坑.md）。
"""
import base64
import json
import zlib


def loads(string):
    s = string.strip()
    if not s:
        raise ValueError("空蓝图字符串")
    raw = base64.b64decode(s[1:])
    return json.loads(zlib.decompress(raw).decode("utf-8"))


def dumps(obj, version="0"):
    return version + base64.b64encode(zlib.compress(
        json.dumps(obj, separators=(",", ":")).encode("utf-8"), 9)).decode("ascii")


def iter_blueprints(obj):
    """把 blueprint / blueprint_book / deconstruction 摊平成 [(路径, blueprint_dict)]。"""
    if "blueprint" in obj:
        yield ("blueprint", obj["blueprint"])
    elif "blueprint_book" in obj:
        book = obj["blueprint_book"]
        for i, entry in enumerate(book.get("blueprints") or []):
            label = entry.get("index", i)
            for path, bp in iter_blueprints(entry):
                yield ("book/%s/%s" % (label, path), bp)
    else:
        yield ("other", obj)


def entity_list(bp):
    """蓝图层级的实体列表。嵌套蓝图（blueprint 里的 blueprints 字段，2.0 的"内嵌蓝图"）
    是独立坐标系，必须递归，否则坐标会混在一起。"""
    out = []
    for e in (bp.get("entities") or []):
        out.append(e)
    return out
