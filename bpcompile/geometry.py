"""方向与栅格几何。

坐标系：position 是实体中心，单位格；屏幕 y 向下。
direction 是 0/4/8/12 循环：0=北(-y) 4=东(+x) 8=南(+y) 12=西(-x)。
格 (i, j) 覆盖 x∈[i,i+1) y∈[j,j+1)；实体占的格从 floor(center - size/2) 起算 size 格。
"""

DIRECTIONS = (0, 4, 8, 12)

DIRECTION_VECTORS = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}

DIRECTION_NAMES = {0: "north", 4: "east", 8: "south", 12: "west"}

NAME_DIRECTIONS = {
    "north": 0, "n": 0, "up": 0,
    "east": 4, "e": 4, "right": 4,
    "south": 8, "s": 8, "down": 8,
    "west": 12, "w": 12, "left": 12,
}


def normalize_direction(value):
    """direction 接受 0/4/8/12、英文方位名，或原版的 16 分度整数。"""
    if value is None:
        return 0
    if isinstance(value, str):
        key = value.strip().lower()
        if key not in NAME_DIRECTIONS:
            raise ValueError("认不出的方位 %r，可用：north/east/south/west 或 0/4/8/12" % value)
        return NAME_DIRECTIONS[key]
    direction = int(value)
    if direction % 4:
        raise ValueError(
            "direction=%r 不是 4 的倍数。普通实体只用 0/4/8/12；0-15 全量只出现在轨道信号类" % value
        )
    return direction % 16


def canonical(direction):
    """把任意 direction 收到 0/4/8/12。

    轨道信号类会用 0-15 全量（实测 12 个值），游戏按"落在哪个 90° 象限"处理碰撞盒，
    所以几何计算一律用象限，不用原始值。
    """
    if direction is None:
        return 0
    return ((int(direction) % 16) // 4) * 4


def vector(direction):
    return DIRECTION_VECTORS[canonical(direction)]


def opposite(direction):
    return (canonical(direction) + 8) % 16


def direction_from_vector(dx, dy):
    """把整数轴向向量换成 direction；非轴向量返回 None。"""
    for direction, (vx, vy) in DIRECTION_VECTORS.items():
        if (vx, vy) == (dx, dy):
            return direction
    return None


def rotate_point(x, y, direction):
    """把朝北坐标系下的相对坐标转到 direction 朝向（见 doc/蓝图格式与坑.md 5.2）。"""
    direction = canonical(direction)
    if direction == 0:
        return (x, y)
    if direction == 4:
        return (-y, x)
    if direction == 8:
        return (-x, -y)
    return (y, -x)


def footprint(width, height, direction):
    """转 90° 后宽高互换。"""
    if canonical(direction) in (4, 12):
        return (height, width)
    return (width, height)


def cell_of(x, y):
    import math

    return (int(math.floor(x)), int(math.floor(y)))


def cell_center(cell):
    return (cell[0] + 0.5, cell[1] + 0.5)


def cells_of(center_x, center_y, width, height, direction):
    """实体占据的格集合。"""
    width, height = footprint(width, height, direction)
    left, top = cell_of(center_x - width / 2.0, center_y - height / 2.0)
    return frozenset((left + i, top + j) for i in range(width) for j in range(height))


def snapped_center(center_x, center_y, width, height, direction):
    """把一个中心挪到该尺寸合法的奇偶上（1 格实体落 .5，2 格实体落整数）。"""
    width, height = footprint(width, height, direction)
    left, top = cell_of(center_x - width / 2.0, center_y - height / 2.0)
    return (left + width / 2.0, top + height / 2.0)


def is_snapped(center_x, center_y, width, height, direction, tolerance=1e-6):
    sx, sy = snapped_center(center_x, center_y, width, height, direction)
    return abs(sx - center_x) < tolerance and abs(sy - center_y) < tolerance


def offset(cell, direction, distance=1):
    dx, dy = vector(direction)
    return (cell[0] + dx * distance, cell[1] + dy * distance)


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def axis_between(a, b):
    """两点若在同一行/列，返回 (direction, 距离)；否则 None。"""
    if a[0] == b[0] and a[1] != b[1]:
        return (8 if b[1] > a[1] else 0, abs(b[1] - a[1]))
    if a[1] == b[1] and a[0] != b[0]:
        return (4 if b[0] > a[0] else 12, abs(b[0] - a[0]))
    return None


def format_cell(cell):
    return "(%d,%d)" % (cell[0], cell[1])


def format_position(position):
    return "(%g,%g)" % (position[0], position[1])
