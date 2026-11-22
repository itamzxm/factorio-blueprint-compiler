# Factorio 蓝图编译器

**给 AI 用的 Factorio 蓝图工具链：AI 说语义，代码展开几何，校验器报错带坐标，游戏做终审。**

你不写坐标，也不写蓝图字符串。你说"这里来 24 台石炉，喂铁矿出铁板"，
库把它摊成带、臂、电塔和铜线，静态检查告诉你哪一格有问题，
再把图送进真正的 Factorio 建一遍、跑 60 秒、把产量读回来。

    你说：  bp_furnace_array(count=24, furnace="stone-furnace", input_item="iron-ore")
    得到：  253 个实体的一张蓝图 + 三个接线口坐标 + 0 error / 0 warning / 0 note
    真机：  落地 300/300，60 秒 produced 330（理论 450），drained {"iron-plate": 326}

- 提供 MCP server（stdio），25 个工具，任何 MCP 客户端都能接。
- 也可以当普通 Python 库用：`bpcompile/`，不依赖 MCP。
- 实体尺寸/朝向/配方全是**查表**，不靠模型记忆——表来自游戏自己的原型转储。

## 它能干什么

**只有一条产线走完了全程验证：炉子阵列。** 其余两个模板的地基（配比层、直插、管道）都已就绪、
也有真机读数，但**产量没到验收线**——分开写，别混着读。

### 已验证

| 模板 | 60 秒真机读数 |
|---|---|
| `furnace_array`（24 台石炉） | **330 块铁板**（理论 450，73.3%），24/24 台在干活，`drained` 核对为 `iron-plate`；连跑三次逐项一致 |

### 地基已就绪、模板未验证

| 模板 | 到哪一步 | 卡在哪 |
|---|---|---|
| `green_circuit`（绿板）｜工具 `bp_green_circuit`｜**未通过验收线** | lint 0 error、真机落地 109/109、`by_product {copper-cable: 71, electronic-circuit: 26}` | 26 个 < 验收线 60。铜线堆着而绿板机饿着 → 缺**铁板** |
| `plastic_line`（塑料）｜工具 `bp_plastic_line`｜**部分验证、产能未达标** | 气通、能产出。48 实体、lint 0/0/0、落地 48/48；两台化工厂流程正常，`by_product: {plastic-bar: 18}` / 60 秒（理论 120，15%） | **煤没到炉子**：煤线终点落在 4 个收货钢箱里，y=−2 进料带是空的——夹具把煤线当成成品线收走了 |

数字都对应各自的模板参数（改 `target_rate` 规模就变），换配置要重测。

### 两条硬校验（比"能出图"更值钱的部分）

1. `primitives.machine_pair` / `INSERTER_RATE`：**搬运速率当场核算**，臂不够直接抛错「搬不动」。
   3 根快臂 6.93/秒 ≥ 绿板机需要的 3/秒；1 根 2.31/秒会被拒绝。
2. `row_station` 的**面预算 / 臂位容量**：一侧格子数 < 近端+远端臂数时抛错，并直说「加臂解决不了吞吐」。

### 与具体产线无关的部分

| 能力 | 证据 |
|---|---|
| 带（含地下段）、臂（自动算朝向）、机器网格、自动补电并连铜线、管道 | 单测 + 真机落地 |
| 静态检查：重叠 / 越界 / 地下件配对 / 断头带 / 臂的取放两侧 / 供电覆盖 / 电网连通 / 配方与机器类别 | 27 张真实蓝图零新类别误报 |
| 蓝图字符串 ⇄ JSON、PNG 预览、真机落地 + 产量三档验证 | `bp_verify` |

## 它不能干什么（别试，会浪费时间）

- **电路**（组合器、灯条）：没有电路原语。
- **火车 / 站台 / 装卸**：没有轨道与站台原语。
- **核电**：没有。
- **红板及更深的链条**：只有「铜线 → 绿板」和「石油气 → 塑料」两级模板；配比层能算台数，但没有第三级。
- **其它化工品**（硫酸、甲烷…）：管道原语有了，没有模板。
- **mod**：只支持 base / space-age / quality / elevated-rails / recycler，其它一律查不到。

缺的能力**不要自己硬造几何**：`bp_machine_grid` + `bp_belt` + `bp_inserter` 能拼出东西，
但拼出来的东西没经过任何验证，和"能跑"是两回事。

还有一件已经明确**没做完**的事：48 台电炉炼钢那条线，蓝图能建、lint 干净、真机 601/601 落地，
但 60 秒 `drained` 仍然是 `iron-plate`——产物不是钢板。原因未闭合。

## 安装

需要 Python 3.10+（开发与实测在 3.14 上做）。

    git clone <this-repo>
    cd factorio-blueprint-compiler
    python -m venv .venv
    .venv/Scripts/activate          # Linux/macOS：source .venv/bin/activate
    pip install -r requirements.txt

可选依赖：

| 包 | 什么时候要 |
|---|---|
| `lupa` | 要跑真机档（`bp_verify` 的 rig/run）时需要 |
| `factorio-draftsman` | 只用于交叉验证我们的编码器（有一条测试用它当独立解码器） |

要跑真机档还需要**本机装了 Factorio**（实测 2.1.24），并把路径告诉工具：

    export FACTORIO_EXE=/path/to/factorio      # Windows: set FACTORIO_EXE=...\factorio.exe

没设也能跑：工具会按各平台常见安装位置找。

## 用

### 1. 当 MCP server

    { "mcpServers": { "factorio-blueprint": {
        "command": "<项目>/.venv/Scripts/python.exe",
        "args": ["<项目>/mcp_server.py"] } } }

（Linux/macOS 把 command 换成 `<项目>/.venv/bin/python`。）

接上以后最小闭环：

    bp_furnace_array(count=24)     # 建阵列，返回接线口坐标
    bp_lint()                      # 必须 0 error / 0 warning / 0 note
    bp_render()                    # 出 PNG，自己看一眼
    bp_string()                    # 拿到能粘进游戏的那串

要量产量（唯一算数的验证）：

    bp_testbench(seconds=60)                 # 生成自测版：自带料、自带电
    bp_verify(modes="lint,run", run_seconds=60)

### 2. 当 Python 库

    from bpcompile import blueprint as bpm, lint
    from bpcompile.templates import furnace_array

    bp = bpm.Blueprint()
    info = furnace_array(bp, count=24, furnace="stone-furnace",
                         input_item="iron-ore", output_item="iron-plate")
    bp.normalize()
    print(lint.report(bp)["counts"])      # {'error': 0, 'warning': 0, 'note': 0}
    print(info["ports"])                  # 三个接线口格坐标
    print(bp.to_string()[:40])            # 游戏内蓝图串

### 3. 查表（不用记数字）

    python tools/query.py entity iron-chest      # 尺寸/朝向/速度/端口 + 完整 JSON
    python tools/query.py port stone-furnace 0   # 四个朝向的取货/放货位置
    python tools/query.py recipe steel-plate     # 耗时/进出料/哪些机器能做
    python tools/query.py who-makes iron-gear-wheel
    python tools/query.py table furnace          # 某 type 下全部实体及尺寸

### 4. 真机校验

    python tools/factorio_build_check.py examples/furnace24.blueprint.txt --text
    python tools/factorio_build_check.py examples/furnace24.testbench.blueprint.txt --run-seconds 60

一次约 2 秒（跑产量则加运行时间）。机制与边界见 [doc/真机校验.md](doc/真机校验.md)。

### 5. examples/

| 文件 | 是什么 |
|---|---|
| [examples/furnace24.blueprint.txt](examples/furnace24.blueprint.txt) | 24 台石炉，铁矿 → 铁板（玩家版，可直接粘进游戏） |
| [examples/furnace24.png](examples/furnace24.png) | 同一张图的预览 |
| [examples/smelt-steel24.blueprint.txt](examples/smelt-steel24.blueprint.txt) | 24 台电炉，铁板 → 钢板（玩家版） |
| [examples/smelt-steel24.png](examples/smelt-steel24.png) | 预览 |
| [examples/furnace24.testbench.blueprint.txt](examples/furnace24.testbench.blueprint.txt) | 自测版：夹具用的是隐藏原型，**只给测试用，不要发给玩家** |

样例不是手写的：`python tools/make_examples.py` 用库现算，`--check` 能告诉你仓库里的样例过期没有。

## 验证：三档，别混着读

| 档 | 怎么跑 | 判什么 | 不能判什么 |
|---|---|---|---|
| L1 静态 | `bp_lint` / `python tests/run_tests.py` | 重叠 / 越界 / 配对 / 覆盖 / 配方与机器类别 | 能不能跑 |
| L2 落地 | `bp_verify("lint,rig")` | 游戏认这张图、实体都建出来了 | **臂对着空气照样过** |
| L3 产量 | `bp_verify("lint,run", run_seconds=60)` | 真跑 N 秒的 `produced` / `drained` | 需要自测版的夹具才有料有电 |

自检：

    python tests/run_tests.py        # 期望 0 failed

需要样本蓝图的那几条用例，在样本不在时会**跳过**（打印 `skip` 并计入跳过数），不会假装通过。

### 一条硬规矩：先看产物身份，再看数量

`bp_verify` 的 `rig.production.drained` 是"输出箱里实际收到了什么"。**先读物品名，再读数字。**

    真事：48 台电炉声明产 steel-plate，produced 366 看着漂亮，
    drained 却是 {"iron-plate": 361}。数量对、物品错——
    这类错不会让任何断言变红，只有看 drained 才抓得住。

同理，炉子的产物**由进料决定**：蓝图里给炉子写 `recipe` 是无效字段（游戏自己导出的炉子蓝图里也没有它）。
所以模板的参数是 `input_item` / `output_item`，不是配方。

## 样本蓝图（不随本仓库发布）

测试里有一批**真实玩家蓝图**（27 张原版 2.0/2.1 图）用来做误报校验和格式回归。
它们来自一个公开的蓝图分享站，站点 `robots.txt` 的 Content-Signal 是
`ai-train=no, use=reference`：**只能当本地参考样本，不能再发布，也不能拿来训练模型。**
所以它们不在这个仓库里。

想跑那几条用例，自己放一批同格式的样本到 `data/corpus/`：

    data/corpus/xxx.json          # 解码后的蓝图 JSON，顶层是 {"blueprint": {...}} 或 {"blueprint_book": ...}
    data/corpus/xxx.blueprint.txt # 可选的原始字符串

放进去之后 `python tests/run_tests.py` 会自动把它们算进去；不放就跳过。
**请不要把别人的蓝图再发布出去**——这也是这份仓库里没有它们的原因。

## 原型真值表（`data/prototypes.json`）

754 个实体、662 个配方、255 个物品、33 种流体的尺寸/朝向/速度/端口/配方真值表。
推断出来的字段都带 `*_source`，别把"推出来的"当"测出来的"。

它是从游戏自己的 `data-raw-dump.json`（游戏内 `/c` 命令导出）抽出来的**事实表**，不含任何游戏资源，
所以随仓库发布——不然没有游戏的人拿到这份代码什么都干不了。想自己重生成：

    # 游戏内控制台：/c 命令导出原型（约 28MB）
    python tools/build_prototypes.py

它还读同目录的 `data/direction-probe.json`（真机测出的"每种实体能转几档"），
没有那份文件时 `dirs` 会是 `null`，重生成前先跑 `tools/probe_directions.py`。

## 目录

    bpcompile/                 核心库
      geometry.py              方向与栅格几何
      protos.py                真值表查询入口（data/prototypes.json 是唯一数据源）
      blueprint.py             蓝图模型：增删实体、规范化、编解码
      lint.py                  静态缺陷（带格坐标、人话原因、修复建议）
      primitives.py            语义原语：带、臂、机器网格、供电、连铜线、管道、配比、直插（含搬运速率核算）
      templates/furnace_array.py  炉子阵列模板（唯一经过产量验证的模板）
      templates/green_circuit.py  绿板产线模板（两级 + 直插，产量未达验收线）
      templates/plastic_line.py   塑料产线模板（气通、产能未达标）
      render.py                PNG 预览
      verify.py                验证档位，rig 不可用时干净降级
      testbench.py             自测版夹具
      service.py               工具实现体（不经 MCP 也能直接调）
    mcp_server.py              MCP 门面，25 个工具
    data/                      真值表与实测读数
    doc/                       技术文档（格式与坑 / 接口 / 夹具 / 两个模板 / 选型 / 真机校验）
    examples/                  可直接粘进游戏的最小样例
    tests/run_tests.py         自检
    tools/                     命令行工具（查表、真机校验、重建样例、导出、泄漏扫描）

## 文档

| 文档 | 什么时候读 |
|---|---|
| [doc/蓝图格式与坑.md](doc/蓝图格式与坑.md) | 写编解码/校验之前。实体尺寸、方向、坐标规则、全部实测坑 |
| [doc/核心库与MCP接口.md](doc/核心库与MCP接口.md) | 要改库或接 MCP 客户端时 |
| [doc/炉子阵列模板.md](doc/炉子阵列模板.md) | 要用那个模板、或照它写新模板时 |
| [doc/绿板产线.md](doc/绿板产线.md) | 要拼两级产线、或想知道配比层怎么算台数时 |
| [doc/塑料产线.md](doc/塑料产线.md) | 要看"气路通了但产能不够"这类半成品长什么样时 |
| [doc/直插原语.md](doc/直插原语.md) | 要用 `machine_pair` 直插、或想知道臂搬不搬得动时 |
| [doc/自测版夹具.md](doc/自测版夹具.md) | 要量产量、要接自动化测试时 |
| [doc/真机校验.md](doc/真机校验.md) | 要把蓝图送进游戏验一遍时 |
| [doc/选型-为什么不用draftsman.md](doc/选型-为什么不用draftsman.md) | 想换编码层、或以为 draftsman 能用之前 |

## 已知的坑（先看这三条再动手）

1. **臂的 `direction` 指向取货侧**，不是放货侧。
2. **凡连接关系（电网、电路、管线、带子上下游），只认蓝图里实际写下的字段，不许用距离推。**
   没写 `wires` 的 12 根电塔 = 12 张独立电网。
3. **有些机器认 direction，但要求蓝图里同时写了 recipe 才生效**（`foundry` / `cryogenic-plant`），
   而另一些机器根本不能转。判据是真机读数，不是"有没有配方槽"。

## 许可

MIT，见 [LICENSE](LICENSE)。

Factorio 是 Wube Software 的作品；本项目与 Wube 无关，不含任何游戏资源文件。
`data/prototypes.json` 是从玩家自己安装的游戏里导出的数值事实表。
界面预览图由本项目生成。
