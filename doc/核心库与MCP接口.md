# 核心库与 MCP 接口

> 一句话：`bpcompile/` 是库，`mcp_server.py` 是给 AI 客户端用的门面。
> 坐标一律**格坐标（整数）**，格 `(i,j)` 覆盖 `x∈[i,i+1)`。不是实体中心的 .5 坐标。

## 目录

| 文件 | 职责 | 一年后要改它的人从哪进 |
|---|---|---|
| `bpcompile/geometry.py` | 方向与栅格几何：占格、吸附、旋转、象限收敛 | 改坐标系约定时 |
| `bpcompile/protos.py` | `data/prototypes.json` 的唯一查询入口 | 要新字段时先改这里 |
| `bpcompile/blueprint.py` | 蓝图模型：增删实体、规范化、编解码 | 改蓝图格式时 |
| `bpcompile/lint.py` | 静态缺陷列表（产品灵魂） | 加一条检查 / 调误报时 |
| `bpcompile/primitives.py` | 语义原语：带、臂、机器网格、供电、管道，以及配比（`machine_count` / `row_station`） | 加一种"不会摆错的动作"时 |
| `bpcompile/templates/furnace_array.py` | 炉子阵列模板（唯一过了产量验证的） | 加模板时照它抄 |
| `bpcompile/templates/green_circuit.py` | 绿板产线模板（两级，用 `row_station` 拼） | 要拼多级链条时 |
| `bpcompile/render.py` | PNG 预览 | 看图看不清时 |
| `bpcompile/verify.py` | 验证档位（lint → 真机），可插拔 | 接新的验证后端时 |
| `bpcompile/service.py` | MCP 工具的实现体（不经过 MCP 也能直接测） | 改工具行为时 |
| `mcp_server.py` | MCP 门面，25 个工具 | 加工具时 |

## 跑

    python tests/run_tests.py       # 自检（会用到 data/corpus/ 里的 27 张真实蓝图）
    python mcp_server.py            # stdio MCP server

MCP 客户端配置示例（Claude Desktop / 其它 MCP 客户端通用形状）：

    { "mcpServers": { "factorio-blueprint": {
        "command": "<项目>/.venv/Scripts/python.exe",     # Linux/macOS：<项目>/.venv/bin/python
        "args": ["<项目>/mcp_server.py"] } } }

## 工具一览（25 个）

蓝图进出：`bp_new` `bp_load` `bp_status` `bp_string` `bp_save`
查表：`bp_entity` `bp_recipe` `bp_find_recipe`
生成：`bp_furnace_array` `bp_green_circuit` `bp_plastic_line` `bp_add_entity` `bp_remove_at` `bp_belt` `bp_inserter`
`bp_inserter_between` `bp_inserter_pair` `bp_machine_grid` `bp_power`
检查：`bp_lint` `bp_verify` `bp_render` `bp_normalize` `bp_declare_port` `bp_testbench`

会话里维护一张"当前蓝图"，改完直接 `bp_lint`，不用把蓝图串传回来。

## 三个必须记住的语义

1. **臂的 `direction` 指向取货侧**，不是放货侧。拿不准用 `bp_inserter_between`，朝向由代码算。
2. **带配方槽的机器写非 0 方向可能被游戏忽略**。真机实测只有 `foundry` / `cryogenic-plant` 带
   `direction_requires_recipe` 标记：裸建 + direction=8 → 游戏读回 0；补上 recipe 才认。
   `Blueprint.add` 在生成期就抛错，`lint` 在检查期再报一次。**别用"有没有配方槽"去推**——
   化工厂是方的但裸建也认方向，EM plant / biochamber 根本不能转。
3. **lint 不判 direction 非法**。真实蓝图里 `assembling-machine-3` 就在用 `direction=12`，
   真机探针却把它读成"不可旋转"。放行集合统一取 `direction_accept`（现为 0/4/8/12），
   轨道类完全跳过（它们的碰撞盒跟曲线不是一回事）。

## lint 的严重度分档

| 档 | 含义 | 例子 |
|---|---|---|
| `error` | 游戏里一定错 | 实体重叠、越界、地下带没配对、臂对着空气 |
| `warning` | 几乎肯定是 bug，但可能有正当理由 | 带怼非带实体、地下管没配对、供电不到 |
| `note` | 信息，可能是设计意图 | 断头带（没人取货的带尾）、孤立管道 |

"0 缺陷"指 error 与 warning 都为 0。note 单独计数，`bp_lint` 的 `counts` 里分开给。

## 验证边界

`bp_verify` 的 `rig` 档证明的是**"游戏认这张蓝图、实体都落地了"**，不是"它能跑"。
一根臂对着空气照样落地通过。逻辑正确性只有 lint 那一道。
真机档要本机装了 Factorio 和 lupa；没有就干净降级到 lint-only，不会假装验过。
怎么跑真机档、它到底保证了什么，见 `doc/真机校验.md`。
