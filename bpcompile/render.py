"""把蓝图渲染成 PNG。多模态模型看图比读坐标有效。

只画"看懂这张图需要什么"：实体位置、尺寸、朝向、种类、进出料口。
不追求像游戏截图——分辨率换可读性。
"""

import collections

from PIL import Image, ImageDraw, ImageFont

from . import geometry, protos

PALETTE = {
    "transport-belt": ((196, 158, 60), (60, 46, 12)),
    "underground-belt": ((150, 118, 40), (255, 235, 180)),
    "splitter": ((172, 138, 52), (60, 46, 12)),
    "inserter": ((208, 92, 46), (255, 240, 230)),
    "furnace": ((122, 122, 134), (255, 255, 255)),
    "assembling-machine": ((58, 110, 168), (240, 247, 255)),
    "pipe": ((47, 143, 90), (230, 255, 240)),
    "pipe-to-ground": ((32, 106, 66), (230, 255, 240)),
    "electric-pole": ((138, 106, 58), (255, 245, 220)),
    "container": ((122, 98, 58), (255, 245, 220)),
    "accumulator": ((80, 130, 130), (240, 255, 255)),
    "solar-panel": ((70, 110, 150), (240, 250, 255)),
    "lab": ((150, 110, 170), (255, 245, 255)),
}
DEFAULT_COLOR = ((110, 110, 110), (255, 255, 255))

ARROW = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}

# 每张电网一种颜色。用户是靠肉眼发现"12 根塔其实是 12 张网"的，所以这一层必须画出来。
NETWORK_COLORS = [
    (90, 190, 240), (250, 150, 80), (120, 220, 140), (230, 120, 200),
    (240, 220, 90), (150, 140, 250), (110, 220, 220), (240, 140, 140),
]


def _pole_networks(blueprint):
    """按蓝图里真的写下来的铜线分网，返回 {实体号: 分量下标} 和分量列表。"""
    poles = [
        e for e in blueprint.entities if (protos.find(e["name"]) or {}).get("type") == "electric-pole"
    ]
    index = {p["entity_number"]: i for i, p in enumerate(poles)}
    parent = list(range(len(poles)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in blueprint.copper_wires():
        if a in index and b in index:
            ra, rb = find(index[a]), find(index[b])
            if ra != rb:
                parent[ra] = rb
    label = {}
    order = {}
    for i, pole in enumerate(poles):
        root = find(i)
        if root not in order:
            order[root] = len(order)
        label[pole["entity_number"]] = order[root]
    return label, poles


_CJK_FONTS = ("msyh.ttc", "msyhl.ttc", "simhei.ttf", "simsun.ttc", "deng.ttf", "segoeui.ttf")


def _font(size):
    for name in _CJK_FONTS:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _color_for(name):
    proto = protos.find(name) or {}
    return PALETTE.get(proto.get("type"), PALETTE.get(name, DEFAULT_COLOR))


def render(blueprint, path, cell_px=18, title=None, legend=True, notes=()):
    bounds = blueprint.bounds()
    if bounds is None:
        raise ValueError("空蓝图没有可画的东西")
    min_i, min_j, max_i, max_j = bounds
    width_cells = max_i - min_i + 1
    height_cells = max_j - min_j + 1
    if cell_px * width_cells > 1900:
        cell_px = max(4, 1900 // width_cells)
    margin = 14
    header = 34
    footer = 46 if legend else 14
    header = header + (16 if notes else 0)
    width = width_cells * cell_px + margin * 2
    height = height_cells * cell_px + margin * 2 + header + footer

    image = Image.new("RGB", (width, height), (24, 26, 30))
    draw = ImageDraw.Draw(image)
    small = _font(max(9, min(int(cell_px * 0.62), 15)))
    tiny = _font(max(8, min(int(cell_px * 0.5), 12)))
    head_font = _font(17)

    origin_x = margin
    origin_y = margin + header

    def to_px(cell):
        return (
            origin_x + (cell[0] - min_i) * cell_px,
            origin_y + (cell[1] - min_j) * cell_px,
        )

    def to_px_f(x, y):
        return (origin_x + (x - min_i) * cell_px, origin_y + (y - min_j) * cell_px)

    draw.text(
        (margin, margin - 2),
        title or blueprint.data.get("label") or "blueprint",
        font=head_font,
        fill=(238, 238, 240),
    )

    # grid
    if cell_px >= 8:
        line = (44, 48, 54)
        for i in range(width_cells + 1):
            x = origin_x + i * cell_px
            draw.line([(x, origin_y), (x, origin_y + height_cells * cell_px)], fill=line)
        for j in range(height_cells + 1):
            y = origin_y + j * cell_px
            draw.line([(origin_x, y), (origin_x + width_cells * cell_px, y)], fill=line)

    for entity in blueprint.entities:
        proto = protos.find(entity["name"]) or {}
        cells = sorted(blueprint.cells_of(entity))
        if not cells:
            continue
        left = min(cell[0] for cell in cells)
        top = min(cell[1] for cell in cells)
        right = max(cell[0] for cell in cells)
        bottom = max(cell[1] for cell in cells)
        x0, y0 = to_px((left, top))
        x1 = x0 + (right - left + 1) * cell_px
        y1 = y0 + (bottom - top + 1) * cell_px
        fill, ink = _color_for(entity["name"])
        inset = 1 if cell_px >= 10 else 0
        draw.rectangle([x0 + inset, y0 + inset, x1 - inset, y1 - inset], fill=fill)
        kind = proto.get("type", "")
        direction = geometry.canonical(entity.get("direction", 0))
        if kind in ("transport-belt", "underground-belt", "splitter", "loader"):
            _arrow(draw, (x0, y0, x1, y1), direction, ink, cell_px)
        elif kind == "inserter":
            _inserter_mark(draw, (x0, y0, x1, y1), direction, ink, cell_px)
        elif kind in ("furnace", "assembling-machine", "container", "lab", "rocket-silo"):
            label = _short(entity)
            if cell_px >= 12:
                tw = draw.textlength(label, font=small)
                draw.text(
                    ((x0 + x1 - tw) / 2, (y0 + y1) / 2 - small.size / 1.4),
                    label,
                    font=small,
                    fill=ink,
                )
        elif kind == "electric-pole":
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            r = max(2, cell_px // 4)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ink, width=2)
        elif kind == "pipe":
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            r = max(2, cell_px // 5)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ink)
        elif kind == "pipe-to-ground":
            _arrow(draw, (x0, y0, x1, y1), direction, ink, cell_px)

    image = _draw_power(image, blueprint, to_px_f)
    draw = ImageDraw.Draw(image)

    for cell, text in notes:
        x, y = to_px((cell[0], cell[1]))
        draw.rectangle(
            [x, y, x + cell_px, y + cell_px], outline=(250, 250, 120), width=max(1, cell_px // 8)
        )
        label = draw.textlength(text, font=tiny)
        # 标签贴着格子放，靠边时向内收，别被画布裁掉
        text_x = x + cell_px / 2 - label / 2
        if cell[0] - min_i < 2:
            text_x = x
        elif width_cells - (cell[0] - min_i) < 2:
            text_x = x + cell_px - label
        text_x = max(2, min(text_x, width - label - 2))
        draw.text((text_x, y - tiny.size - 2), text, font=tiny, fill=(250, 250, 120))

    if legend:
        _draw_legend(draw, blueprint, margin, height - footer + 6, tiny, small, width)
    image.save(path)
    return path


def _draw_power(image, blueprint, to_px_f):
    """画出每根塔的供电范围与铜线网。不同电网不同颜色——一眼能看出是不是各自为政。"""
    labels, poles = _pole_networks(blueprint)
    if not poles:
        return image
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(overlay)
    for pole in poles:
        supply = protos.pole_supply(pole["name"]) or 0
        if not supply:
            continue
        color = NETWORK_COLORS[labels.get(pole["entity_number"], 0) % len(NETWORK_COLORS)]
        x, y = pole["position"]["x"], pole["position"]["y"]
        corners = [to_px_f(x - supply, y - supply), to_px_f(x + supply, y + supply)]
        box = [corners[0][0], corners[0][1], corners[1][0], corners[1][1]]
        pen.rectangle(box, fill=color + (24,), outline=color + (150,), width=1)
    by_number = {p["entity_number"]: p for p in poles}
    for a, b in blueprint.copper_wires():
        if a not in by_number or b not in by_number:
            continue
        pa, pb = by_number[a], by_number[b]
        color = NETWORK_COLORS[labels.get(a, 0) % len(NETWORK_COLORS)]
        pen.line(
            [
                to_px_f(pa["position"]["x"], pa["position"]["y"]),
                to_px_f(pb["position"]["x"], pb["position"]["y"]),
            ],
            fill=color + (220,),
            width=3,
        )
    for pole in poles:
        color = NETWORK_COLORS[labels.get(pole["entity_number"], 0) % len(NETWORK_COLORS)]
        cx, cy = to_px_f(pole["position"]["x"], pole["position"]["y"])
        pen.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=color + (255,))
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def _short(entity):
    name = entity["name"]
    recipe = entity.get("recipe")
    table = {
        "stone-furnace": "SF",
        "steel-furnace": "StF",
        "electric-furnace": "EF",
        "assembling-machine-1": "A1",
        "assembling-machine-2": "A2",
        "assembling-machine-3": "A3",
        "chemical-plant": "CP",
        "oil-refinery": "OR",
        "foundry": "FD",
        "electromagnetic-plant": "EM",
        "biochamber": "BC",
        "wooden-chest": "ch",
        "iron-chest": "ch",
        "steel-chest": "ch",
        "lab": "lab",
    }
    if name in table:
        return table[name]
    if recipe:
        return recipe[:3]
    return name[:3]


def _arrow(draw, box, direction, ink, cell_px):
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    size = max(3, min((x1 - x0), (y1 - y0)) * 0.32)
    dx, dy = ARROW.get(direction, (0, -1))
    tip = (cx + dx * size, cy + dy * size)
    side = (-dy * size * 0.8, dx * size * 0.8)
    base = (cx - dx * size * 0.6, cy - dy * size * 0.6)
    draw.polygon(
        [tip, (base[0] + side[0], base[1] + side[1]), (base[0] - side[0], base[1] - side[1])],
        fill=ink,
    )


def _inserter_mark(draw, box, direction, ink, cell_px):
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = ARROW.get(direction, (0, -1))
    r = max(2.5, cell_px * 0.3)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ink, width=max(1, cell_px // 10))
    draw.line(
        [(cx - dx * r * 1.5, cy - dy * r * 1.5), (cx + dx * r * 1.5, cy + dy * r * 1.5)],
        fill=ink,
        width=max(1, cell_px // 9),
    )


def _draw_legend(draw, blueprint, x, y, tiny, small, width):
    counter = collections.Counter(entity["name"] for entity in blueprint.entities)
    parts = ["%s×%d" % (name, n) for name, n in counter.most_common(8)]
    text = "  ".join(parts)
    if len(text) > 150:
        text = text[:147] + "..."
    draw.text((x, y), text, font=small, fill=(186, 190, 198))
    bounds = blueprint.bounds()
    size = "%dx%d 格 · %d 个实体" % (
        bounds[2] - bounds[0] + 1,
        bounds[3] - bounds[1] + 1,
        len(blueprint.entities),
    )
    labels, poles = _pole_networks(blueprint)
    if poles:
        networks = len(set(labels.values()))
        wires = len([1 for a, b in blueprint.copper_wires() if a in {p["entity_number"] for p in poles}])
        flag = (110, 230, 130) if networks == 1 else (250, 120, 120)
        draw.text(
            (x, y + 18),
            size + "  ·  电网 %d 张（%d 根塔 / %d 根铜线）" % (networks, len(poles), wires),
            font=tiny,
            fill=flag,
        )
    else:
        draw.text((x, y + 18), size, font=tiny, fill=(140, 146, 156))
