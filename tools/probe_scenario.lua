-- 真机蓝图校验场景模板。作业表与 tick 上限由 factorio_build_check.py 在生成时填入。
-- 入口：factorio --test-scenario <mod>/probe；退出码由 script.finish_test 决定。
-- 产物：<write-data>/script-output/bp-probe/result.json，write-data 由 --config 指向临时目录。
-- 场地：每个作业自建一张无水平地（见 new_surface），不碰任何用户存档与 mod。

local JOBS = __JOBS__
local TICK_LIMIT = __TICK_LIMIT__
local ARENA_HALF = 112
local TERRAIN = "__TERRAIN__"
local RUN_TICKS = __RUN_TICKS__
local FEED_ITEM = "__FEED_ITEM__"
local FLUID_PROBE = __FLUID_PROBE__
local FLUID_NAME = "__FLUID_NAME__"
local UNLOCK_RECIPES = __UNLOCK_RECIPES__
local PORT_CELLS = "__PORT_CELLS__"
local FLUID_PORT = "__FLUID_PORT__"
local SUPPLY_CELLS = "__SUPPLY_CELLS__"
local POWER_PROBE = __POWER_PROBE__
local POWER_SINGLE = __POWER_SINGLE__

local R = { env = {}, jobs = {}, tick_limit = TICK_LIMIT, stage = "load" }

local STATUS_NAME = {}
for name, value in pairs(defines.entity_status or {}) do STATUS_NAME[value] = name end

local function note(text) log("[GT] " .. tostring(text)) end

local function try(fn, ...)
  local ok, a = pcall(fn, ...)
  if ok then return true, a end
  return false, tostring(a)
end

local function status_name(value)
  if value == nil then return nil end
  return STATUS_NAME[value] or tostring(value)
end

local function key_count(tbl)
  local n = 0
  for _ in pairs(tbl) do n = n + 1 end
  return n
end

local WATER_TILES = {}
for tile_name, tile in pairs(prototypes.tile or {}) do
  local mask = tile.collision_mask
  if mask and mask["water-tile"] then WATER_TILES[#WATER_TILES + 1] = tile_name end
end

local DLC_PROBES = {
  ["entity:recycler"] = "entity", ["entity:crusher"] = "entity",
  ["entity:foundry"] = "entity", ["entity:electromagnetic-plant"] = "entity",
  ["entity:biochamber"] = "entity", ["entity:cryogenic-plant"] = "entity",
  ["entity:big-mining-drill"] = "entity", ["entity:agricultural-tower"] = "entity",
  ["entity:heating-tower"] = "entity", ["entity:fusion-reactor"] = "entity",
  ["entity:fusion-generator"] = "entity", ["entity:space-platform-hub"] = "entity",
  ["entity:asteroid-collector"] = "entity", ["entity:cargo-bay"] = "entity",
  ["entity:thruster"] = "entity", ["entity:rail-ramp"] = "entity",
  ["entity:rail-support"] = "entity", ["entity:lightning-rod"] = "entity",
  ["item:quality-module-3"] = "item", ["tile:foundation"] = "tile",
  ["tile:space-platform-foundation"] = "tile", ["recipe:tungsten-carbide"] = "recipe",
}

local function env_block()
  local env = { active_mods = {}, feature_flags = {}, prototypes = {} }
  for k, v in pairs(script.active_mods) do env.active_mods[k] = v end
  local ok_flags, flags = try(function() return script.feature_flags end)
  if ok_flags and type(flags) == "table" then
    for k, v in pairs(flags) do env.feature_flags[k] = v end
  end
  env.players = key_count(game.players)
  for probe, kind in pairs(DLC_PROBES) do
    local ok_lookup, found = try(function()
      local dict = prototypes[kind]
      local name = probe:sub(#kind + 2)
      return dict[name] ~= nil
    end)
    env.prototypes[probe] = (ok_lookup and found) or false
  end
  return env
end

-- 平地：拿默认地图设置，把海拔/湿度/温度压成常数 → 单一地形、无水体，再关掉自动生成物。
local function make_settings(seed)
  local settings = game.default_map_gen_settings
  settings.seed = seed
  settings.no_enemies_mode = true
  settings.peaceful_mode = true
  settings.autoplace_controls = {}
  settings.default_enable_all_autoplace_controls = false
  settings.property_expression_names = settings.property_expression_names or {}
  settings.property_expression_names["elevation"] = "100"
  settings.property_expression_names["moisture"] = "50"
  settings.property_expression_names["temperature"] = "50"
  settings.property_expression_names["aux"] = "50"
  settings.cliff_settings = { name = "cliff", cliff_elevation_0 = 0, cliff_elevation_interval = 1000, richness = 1 }
  return settings
end

local function new_surface(index)
  local ok, surface = try(function() return game.create_surface("bp-probe-" .. index, make_settings(2000 + index)) end)
  local mode = "flat"
  if not ok then
    ok, surface = try(function() return game.create_surface("bp-probe-" .. index) end)
    mode = ok and "default" or "failed"
  end
  if not ok then return nil, mode end
  local chunk_radius = math.ceil(ARENA_HALF / 32) + 1
  try(function() surface.request_to_generate_chunks({ x = 0, y = 0 }, chunk_radius) end)
  try(function() surface.force_generate_chunk_requests() end)
  return surface, mode
end

local function wipe(surface, area)
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid then try(function() entity.destroy() end) end
  end
end

local function clear_area(surface, area)
  local destroyed = 0
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid then
      try(function() entity.destroy() end)
      destroyed = destroyed + 1
    end
  end
  local landfill = {}
  for _, tile in pairs(surface.find_tiles_filtered{ area = area, name = WATER_TILES }) do
    landfill[#landfill + 1] = { name = "landfill", position = tile.position }
  end
  local water_filled, ok_fill = 0, true
  if #landfill > 0 then
    water_filled = #landfill
    ok_fill = (try(function() surface.set_tiles(landfill) end))
  end
  return destroyed, water_filled, ok_fill
end

local function snapshot(surface, area, exclude_units)
  local out = { entities = {}, ghosts = {}, tile_ghosts = {} }
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid and not (exclude_units and exclude_units[entity.unit_number]) then
      if entity.type == "resource" then
        -- 矿是地形，不算蓝图实体
      elseif entity.name == "tile-ghost" then
        out.tile_ghosts[#out.tile_ghosts + 1] = { x = entity.position.x, y = entity.position.y }
      elseif entity.name == "entity-ghost" then
        local ghost_name = "?"
        try(function() ghost_name = entity.ghost_name end)
        out.ghosts[#out.ghosts + 1] = { name = ghost_name, x = entity.position.x, y = entity.position.y }
      else
        local status_value = nil
        try(function() status_value = entity.status end)
        out.entities[#out.entities + 1] = {
          name = entity.name, x = entity.position.x, y = entity.position.y,
          status = status_name(status_value), direction = entity.direction,
        }
      end
    end
  end
  return out
end

local function validate(bp)
  local issues = {}
  local entities = bp.entities
  if type(entities) ~= "table" then return issues, 0 end
  local has_quality = (prototypes.quality ~= nil)
  for index, entity in ipairs(entities) do
    local name = entity.name
    if type(name) ~= "string" then
      issues[#issues + 1] = { kind = "missing_name", index = index }
    elseif prototypes.entity[name] == nil then
      issues[#issues + 1] = { kind = "unknown_entity", index = index, name = name }
    end
    if type(entity.recipe) == "string" and prototypes.recipe[entity.recipe] == nil then
      issues[#issues + 1] = { kind = "unknown_recipe", index = index, name = name, value = entity.recipe }
    end
    if type(entity.item) == "string" and prototypes.item[entity.item] == nil then
      issues[#issues + 1] = { kind = "unknown_item", index = index, name = name, value = entity.item }
    end
    if has_quality and type(entity.quality) == "string" and prototypes.quality[entity.quality] == nil then
      issues[#issues + 1] = { kind = "unknown_quality", index = index, name = name, value = entity.quality }
    end
  end
  return issues, #entities
end

local function count_names(list)
  local counts = {}
  for _, name in ipairs(list) do counts[name] = (counts[name] or 0) + 1 end
  return counts
end

-- 单独拿这个实体拼一张单块蓝图，在空地角上走同一条 build_blueprint 路径，看游戏给不给它建鬼影。
-- 给 → 说明是蓝图坐标处被挤掉；不给 → 说明这个实体在这个场地上连鬼影都落不了。
local function probe_single(surface, force, name)
  local probe_origin = { x = ARENA_HALF - 24, y = ARENA_HALF - 24 }
  local box = { { probe_origin.x - 12, probe_origin.y - 12 }, { probe_origin.x + 12, probe_origin.y + 12 } }
  for _, entity in pairs(surface.find_entities_filtered{ area = box }) do
    if entity.valid then try(function() entity.destroy() end) end
  end
  local stack = game.create_inventory(1)[1]
  stack.clear()
  try(function() stack.set_stack{ name = "blueprint" } end)
  local ok_set = try(function()
    stack.set_blueprint_entities{ { entity_number = 1, name = name, position = { x = 0, y = 0 } } }
  end)
  if not ok_set then return -1 end
  local ok_build, ret = try(function()
    return stack.build_blueprint{ surface = surface, force = force, position = probe_origin,
                                 build_mode = defines.build_mode.superforced, skip_fog_of_war = true }
  end)
  local ghosts = (ok_build and type(ret) == "table") and #ret or -1
  for _, entity in pairs(surface.find_entities_filtered{ area = box }) do
    if entity.valid then try(function() entity.destroy() end) end
  end
  stack.clear()
  return ghosts
end

-- 蓝图里写了、但既没建成也没留下鬼影的实体：把游戏自己的判定带回来。
local function find_missing(expected_names, after_build, force, surface)
  local want = count_names(expected_names)
  local got = {}
  for _, entity in pairs(after_build.entities or {}) do got[entity.name] = (got[entity.name] or 0) + 1 end
  for _, ghost in pairs(after_build.ghosts or {}) do got[ghost.name] = (got[ghost.name] or 0) + 1 end
  local missing = {}
  local probe_position = { x = ARENA_HALF - 12, y = ARENA_HALF - 12 }
  for name, count in pairs(want) do
    local deficit = count - (got[name] or 0)
    if deficit > 0 then
      local entry = { name = name, count = deficit }
      entry.solo_ghosts = probe_single(surface, force, name)
      local can_place = try(function()
        return surface.can_place_entity{ name = name, position = probe_position, force = force,
                                         build_check_type = defines.build_check_type.manual }
      end)
      entry.can_place_at_probe = (can_place == true)
      missing[#missing + 1] = entry
    end
  end
  return missing
end

-- ── L3 夹具：给悬空的带子接上喂料/收货箱子，给电网塞一个太阳能场，然后跑给它看 ──
local function direction_vector(direction)
  if direction == 4 then return 1, 0 end
  if direction == 8 then return 0, 1 end
  if direction == 12 then return -1, 0 end
  return 0, -1
end

local function tile_of(position)
  return math.floor(position.x), math.floor(position.y)
end


-- 供电覆盖探针：先给电网接上发电机，再逐实体读状态。no_power 就是"没被任何电塔覆盖到"。
local function setup_power(surface, force, area)
  local result = { poles = 0, generators = 0, generator_kind = nil, units = {} }
  local occupied = {}
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid then
      occupied[math.floor(entity.position.x) .. "," .. math.floor(entity.position.y)] = true
    end
  end
  local poles = surface.find_entities_filtered{ area = area, type = "electric-pole" }
  result.poles = #poles
  if #poles > 0 then
    try(function() surface.always_day = true end)
    local generator_budget = POWER_SINGLE and 1 or #poles
    local used = 0
    for _, pole in pairs(poles) do
      local pole_x, pole_y = math.floor(pole.position.x), math.floor(pole.position.y)
      local placed = (used >= generator_budget)
      for _, delta in ipairs({ { 1, 0 }, { -1, 0 }, { 0, 1 }, { 0, -1 }, { 2, 0 }, { 0, 2 } }) do
        if not placed then
          local tile_x, tile_y = pole_x + delta[1], pole_y + delta[2]
          if not occupied[tile_x .. "," .. tile_y] then
            local ok_gen, generator = try(function()
              return surface.create_entity{ name = "electric-energy-interface",
                                            position = { x = tile_x + 0.5, y = tile_y + 0.5 }, force = force }
            end)
            if ok_gen and generator ~= nil then
              try(function() generator.power_production = 100000000 end)
              try(function() generator.electric_buffer_size = 1000000000 end)
              result.generators = result.generators + 1
              result.generator_kind = result.generator_kind or "electric-energy-interface"
              result.units[generator.unit_number] = true
              occupied[tile_x .. "," .. tile_y] = true
              used = used + 1
              placed = true
            end
          end
        end
      end
    end
  end
  return result
end

local function power_report(surface, area, exclude_units)
  local report = { entities = {}, poles = {}, networks = {} }
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid and not (exclude_units and exclude_units[entity.unit_number]) then
      local ok_status, status = try(function() return entity.status end)
      local ok_net, network_id = try(function() return entity.electric_network_id end)
      local ok_connected, connected = try(function() return entity.is_connected_to_electric_network() end)
      local entry = {
        unit = entity.unit_number, name = entity.name, type = entity.type,
        pos = { x = entity.position.x, y = entity.position.y },
        status = status_name(ok_status and status or nil),
        network = ok_net and network_id or nil,
        connected = ok_connected and connected or nil,
      }
      report.entities[#report.entities + 1] = entry
      if entity.type == "electric-pole" then
        report.poles[#report.poles + 1] = { unit = entity.unit_number, name = entity.name,
                                           pos = entry.pos, network = entry.network }
      end
    end
  end
  local counts = {}
  for _, entry in pairs(report.entities) do
    if entry.network ~= nil then counts[tostring(entry.network)] = (counts[tostring(entry.network)] or 0) + 1 end
  end
  report.network_counts = counts
  return report
end

-- 夹具喂什么，跟机器自己要炼的东西走：钢铁线喂铁板、冶炼线喂矿石。
-- 机器空闲时 get_recipe() 可能是 nil，这时用蓝图声明的配方（declared）来定原料。
local function all_ingredients(surface, area, declared)
  local counts = {}
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid and (entity.type == "furnace" or entity.type == "assembling-machine") then
      local ok_recipe, recipe = try(function() return entity.get_recipe() end)
      local recipe_name = (ok_recipe and recipe) and recipe.name or nil
      if recipe_name == nil and declared ~= nil then
        recipe_name = declared[math.floor(entity.position.x) .. "," .. math.floor(entity.position.y)]
      end
      if recipe_name ~= nil then
        local ok_proto, recipe_prototype = try(function() return prototypes.recipe[recipe_name] end)
        if ok_proto and recipe_prototype ~= nil then
          local ok_ingredients, ingredients = try(function() return recipe_prototype.ingredients end)
          for _, ingredient in pairs((ok_ingredients and ingredients) or {}) do
            if ingredient.type == "item" and ingredient.name ~= "coal" then
              counts[ingredient.name] = (counts[ingredient.name] or 0) + 1
            end
          end
        end
      end
    end
  end
  local list = {}
  for item_name, count in pairs(counts) do list[#list + 1] = { name = item_name, count = count } end
  table.sort(list, function(a, b)
    if a.count == b.count then return a.name < b.name end
    return a.count > b.count
  end)
  return list
end

local function primary_ingredient(surface, area, declared)
  local counts = {}
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid and (entity.type == "furnace" or entity.type == "assembling-machine") then
      local ok_recipe, recipe = try(function() return entity.get_recipe() end)
      local recipe_name = (ok_recipe and recipe) and recipe.name or nil
      if recipe_name == nil and declared ~= nil then
        recipe_name = declared[math.floor(entity.position.x) .. "," .. math.floor(entity.position.y)]
      end
      if recipe_name ~= nil then
        local ok_proto, recipe_prototype = try(function() return prototypes.recipe[recipe_name] end)
        if ok_proto and recipe_prototype ~= nil then
          local ok_ingredients, ingredients = try(function() return recipe_prototype.ingredients end)
          for _, ingredient in pairs((ok_ingredients and ingredients) or {}) do
            if ingredient.type == "item" and ingredient.name ~= "coal" then
              counts[ingredient.name] = (counts[ingredient.name] or 0) + 1
            end
          end
        end
      end
    end
  end
  local best, best_count = nil, 0
  for item_name, count in pairs(counts) do
    if count > best_count then best, best_count = item_name, count end
  end
  return best
end

local function setup_fixtures(surface, force, area, declared, forced_feed_item)
  local fixtures = { feeds = {}, sinks = {}, refills = 0, units = {} }
  -- 声明驱动：声明里有东西 → 只按声明供，启发式全关；没声明 → 一格都不灌
  fixtures.declared_supply = {}
  if SUPPLY_CELLS ~= nil and SUPPLY_CELLS ~= "" then
    local offset_x, offset_y = 0, 0
    if res ~= nil and res.recipes ~= nil and res.recipes.offset ~= nil then
      offset_x, offset_y = res.recipes.offset[1], res.recipes.offset[2]
    end
    for cell in string.gmatch(SUPPLY_CELLS, "[^;]+") do
      local sx, sy, item = string.match(cell, "(-?%d+%.?%d*),(-?%d+%.?%d*):(.+)")
      if sx ~= nil and item ~= nil then
        fixtures.declared_supply[#fixtures.declared_supply + 1] = {
          x = math.floor(tonumber(sx) + offset_x), y = math.floor(tonumber(sy) + offset_y),
          item = item, cell = { tonumber(sx), tonumber(sy) },
        }
      end
    end
  end
  fixtures.self_supplied = #fixtures.declared_supply > 0
    or (surface.count_entities_filtered{ area = area, name = "infinity-chest" } or 0) > 0
  local feed_ingredients = nil
  local feed_belt_index = 0
  -- 声明的输出口（蓝图坐标 → 世界坐标）。没声明就退化成"末端带"启发式，并在回包里标出来。
  local port_tiles = {}
  local port_count = 0
  if PORT_CELLS ~= nil and PORT_CELLS ~= "" then
    local offset_x, offset_y = 0, 0
    if res ~= nil and res.recipes ~= nil and res.recipes.offset ~= nil then
      offset_x, offset_y = res.recipes.offset[1], res.recipes.offset[2]
    end
    for cell in string.gmatch(PORT_CELLS, "[^;]+") do
      local sx, sy = string.match(cell, "(-?%d+%.?%d*),(-?%d+%.?%d*)")
      if sx ~= nil and sy ~= nil then
        local wx = math.floor(tonumber(sx) + offset_x)
        local wy = math.floor(tonumber(sy) + offset_y)
        port_tiles[wx .. "," .. wy] = true
        port_count = port_count + 1
      end
    end
  end
  fixtures.port_count = port_count
  fixtures.port_tiles = port_tiles
  local occupied = {}
  local function mark(x, y) occupied[x .. "," .. y] = true end
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid then
      local x, y = tile_of(entity.position)
      mark(x, y)
    end
  end

  -- 机器（炉子等）放货的目标带 = 出口带，不能往里喂料（会把产物堵死）
  local output_belt_units = {}
  -- 用位置判出口带（插入机的 pickup_target 在刚建好时还读不到，位置一直有效）：
  -- 取货位落在机器上、放货位落在带上 → 那条带就是这台机器产物的出口带。
  local belt_by_tile = {}
  local machine_tiles = {}
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid then
      local x, y = tile_of(entity.position)
      if entity.type == "transport-belt" or entity.type == "underground-belt" then
        belt_by_tile[x .. "," .. y] = entity
      elseif entity.type == "furnace" or entity.type == "assembling-machine" then
        machine_tiles[x .. "," .. y] = true
      end
    end
  end
  local function machine_near(position)
    local x, y = tile_of(position)
    for _, machine in pairs(surface.find_entities_filtered{ area = { { x - 1, y - 1 }, { x + 1, y + 1 } } }) do
      if machine.valid and (machine.type == "furnace" or machine.type == "assembling-machine") then
        return true
      end
    end
    return false
  end
  local output_belt_units = {}
  fixtures.geo = { inserters = 0, pickup_machine = 0, drop_belt = 0, matched = 0, sample = {} }
  for _, entity in pairs(surface.find_entities_filtered{ area = area, type = "inserter" }) do
    if entity.valid then
      fixtures.geo.inserters = fixtures.geo.inserters + 1
      local ok_pickup, pickup_position = try(function() return entity.pickup_position end)
      local ok_drop, drop_position = try(function() return entity.drop_position end)
      if ok_pickup and ok_drop and pickup_position ~= nil and drop_position ~= nil then
        local px, py = tile_of(pickup_position)
        local dx, dy = tile_of(drop_position)
        local pickup_is_machine = machine_near(pickup_position)
        if pickup_is_machine then fixtures.geo.pickup_machine = fixtures.geo.pickup_machine + 1 end
        if belt_by_tile[dx .. "," .. dy] ~= nil then fixtures.geo.drop_belt = fixtures.geo.drop_belt + 1 end
        if pickup_is_machine and belt_by_tile[dx .. "," .. dy] ~= nil then
          fixtures.geo.matched = fixtures.geo.matched + 1
          output_belt_units[belt_by_tile[dx .. "," .. dy].unit_number] = true
        end
        if #fixtures.geo.sample < 3 then
          fixtures.geo.sample[#fixtures.geo.sample + 1] = {
            name = entity.name, pickup = { px, py }, drop = { dx, dy },
            pickup_is_machine = pickup_is_machine,
            drop_is_belt = belt_by_tile[dx .. "," .. dy] ~= nil,
          }
        end
      end
    end
  end
  fixtures.drain_mode = (port_count > 0) and "declared-ports" or "terminal-heuristic"
  fixtures.output_belt_count = 0
  for _ in pairs(output_belt_units) do fixtures.output_belt_count = fixtures.output_belt_count + 1 end
  -- 先记下"蓝图自己的插入机"（我自己的夹具插入机还没建，不能算进去）
  fixtures.blueprint_inserters = {}
  for _, inserter in pairs(surface.find_entities_filtered{ area = area, type = "inserter" }) do
    if inserter.valid then fixtures.blueprint_inserters[inserter.unit_number] = true end
  end

  -- 出口带所在的整条"连通带系统"都不喂料（否则会把产物线塞满）
  local output_line_units = {}
  local function flood(belt)
    if belt == nil or output_line_units[belt.unit_number] then return end
    output_line_units[belt.unit_number] = true
    local ok_n, neighbours = try(function() return belt.belt_neighbours end)
    local inputs = (ok_n and neighbours and neighbours.inputs) or {}
    local outputs = (ok_n and neighbours and neighbours.outputs) or {}
    for _, other in pairs(inputs) do flood(other) end
    for _, other in pairs(outputs) do flood(other) end
  end
  for _, entity in pairs(surface.find_entities_filtered{ area = area, type = "transport-belt" }) do
    if entity.valid and output_belt_units[entity.unit_number] then flood(entity) end
  end

  -- 喂料带所在的整条连通带系统都不放收货箱：否则刚喂进去的料会被自己放的插入机搬走
  local feed_line_units = {}
  local function flood_line(belt)
    if belt == nil or feed_line_units[belt.unit_number] then return end
    feed_line_units[belt.unit_number] = true
    local ok_n, neighbours = try(function() return belt.belt_neighbours end)
    local inputs = (ok_n and neighbours and neighbours.inputs) or {}
    local outputs = (ok_n and neighbours and neighbours.outputs) or {}
    for _, other in pairs(inputs) do flood_line(other) end
    for _, other in pairs(outputs) do flood_line(other) end
  end
  for _, entity in pairs(surface.find_entities_filtered{ area = area, type = "transport-belt" }) do
    if entity.valid and not output_belt_units[entity.unit_number] then
      local ok_n, neighbours = try(function() return entity.belt_neighbours end)
      local inputs = (ok_n and neighbours and neighbours.inputs) or {}
      if #inputs == 0 then flood_line(entity) end
    end
  end
  fixtures.output_line_count = 0
  for _ in pairs(output_line_units) do fixtures.output_line_count = fixtures.output_line_count + 1 end

  local sink_units = {}
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid and (entity.type == "transport-belt" or entity.type == "underground-belt") then
      local ok_n, neighbours = try(function() return entity.belt_neighbours end)
      local outputs = (ok_n and neighbours and neighbours.outputs) or {}
      local ok_g, ground = try(function() return entity.belt_to_ground_type end)
      if #outputs == 0 and not (entity.type == "underground-belt" and tostring(ground) == "input") then
        sink_units[entity.unit_number] = true
      end
    end
  end



  -- 带子断头 → 接喂料箱；带子断尾 → 接收货箱
  local blueprints_by_hand = {}
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid and (entity.type == "transport-belt" or entity.type == "underground-belt") then
      local ok_n, neighbours = try(function() return entity.belt_neighbours end)
      local inputs = (ok_n and neighbours and neighbours.inputs) and #neighbours.inputs or 0
      local outputs = (ok_n and neighbours and neighbours.outputs) and #neighbours.outputs or 0
      local ok_g, ground = try(function() return entity.belt_to_ground_type end)
      local is_underground_output = (entity.type == "underground-belt" and tostring(ground) == "output")
      local is_underground_input = (entity.type == "underground-belt" and tostring(ground) == "input")
      local direction = select(2, try(function() return entity.direction end)) or 0
      local vx, vy = direction_vector(direction)
      local bx, by = tile_of(entity.position)

      if not fixtures.self_supplied and inputs == 0 and not is_underground_output
         and not output_belt_units[entity.unit_number]
         and not output_line_units[entity.unit_number]
         and not (fixtures.supplied_belt_units or {})[entity.unit_number] then
        -- 一个永续箱只喂一种物品（实测：一个箱子挂两个 filter 时，臂只会搬第一种）
        fixtures.supplied_belt_units = fixtures.supplied_belt_units or {}
  do
    for _, inserter in pairs(surface.find_entities_filtered{ area = area, type = "inserter" }) do
      if inserter.valid then
        local ok_pickup, pickup = try(function() return inserter.pickup_target end)
        local ok_drop, drop = try(function() return inserter.drop_target end)
        if ok_pickup and ok_drop and pickup ~= nil and drop ~= nil
           and (pickup.name == "infinity-chest" or tostring(pickup.type):find("container") ~= nil)
           and (drop.type == "transport-belt" or drop.type == "underground-belt") then
          fixtures.supplied_belt_units[drop.unit_number] = true
        end
      end
    end
  end

  local function spawn_feed(offset_x, offset_y, facing, item_name)
          local chest_x, chest_y = bx + offset_x * 2, by + offset_y * 2
          local ins_x, ins_y = bx + offset_x, by + offset_y
          if occupied[chest_x .. "," .. chest_y] or occupied[ins_x .. "," .. ins_y] then return false end
          local ok_chest, chest = try(function()
            return surface.create_entity{ name = "infinity-chest", position = { x = chest_x + 0.5, y = chest_y + 0.5 }, force = force }
          end)
          if not (ok_chest and chest ~= nil) then return false end
          try(function()
            chest.set_infinity_container_filter(1, { name = item_name, count = 400, mode = "at-least" })
          end)
          local ok_ins, feeder = try(function()
            return surface.create_entity{ name = "burner-inserter", position = { x = ins_x + 0.5, y = ins_y + 0.5 },
                                          direction = facing, force = force }
          end)
          if not (ok_ins and feeder ~= nil) then
            try(function() chest.destroy() end)
            return false
          end
          fixtures.feeds[#fixtures.feeds + 1] = chest
          fixtures.feed_belts = fixtures.feed_belts or {}
          fixtures.feed_belts[#fixtures.feed_belts + 1] = { belt = entity, item = item_name or main_item }
          fixtures.feed_belt_units = fixtures.feed_belt_units or {}
          fixtures.feed_belt_units[entity.unit_number] = true
          fixtures.burners = fixtures.burners or {}
          fixtures.burners[#fixtures.burners + 1] = feeder
          fixtures.units[chest.unit_number] = true
          fixtures.units[feeder.unit_number] = true
          mark(chest_x, chest_y); mark(ins_x, ins_y)
          return true
        end
        -- 主喂料：从带子后方（上游）喂"机器配方要的原料"（读不到才退回铁矿）
        -- 没有默认值：推不出原料就不喂（喂错比不喂更糟——它会静默地测错）
        fixtures.needs_fuel = false
        for _, machine in pairs(surface.find_entities_filtered{ area = area }) do
          if machine.valid and machine.type == "furnace" and machine.name ~= "electric-furnace" then
            fixtures.needs_fuel = true
          end
        end
        local main_item = primary_ingredient(surface, area, declared)
        local feed_source = "declared"
        if forced_feed_item ~= nil and forced_feed_item ~= "" then
          main_item = forced_feed_item
          feed_source = "explicit"
        elseif main_item == nil then
          -- 石炉/钢炉按矿石炼，这是类型事实；电炉不猜（它可能炼钢、也可能烧石头）
          local only_solid_furnaces = true
          for _, machine in pairs(surface.find_entities_filtered{ area = area }) do
            if machine.valid and machine.name == "electric-furnace" then only_solid_furnaces = false end
          end
          if only_solid_furnaces then
            main_item = "iron-ore"
            feed_source = "inferred-furnace"
          end
        end
        fixtures.main_item_source = feed_source
        fixtures.main_item = main_item
        if main_item ~= nil then
          spawn_feed(-vx, -vy, (direction + 8) % 16, main_item)
        else
          fixtures.no_feed_item = true
        end
        -- 侧喂：从带子侧面喂煤，喂到另一条道上
        local perp_x, perp_y = -vy, vx
        local perp_direction = 8
        if perp_x == 1 then perp_direction = 4 elseif perp_x == -1 then perp_direction = 12
        elseif perp_y == -1 then perp_direction = 0 end
        spawn_feed(perp_x, perp_y, perp_direction, "coal")
      end

      if outputs == 0 and not is_underground_input
         and not ((fixtures.feed_belt_units or {})[entity.unit_number])
         and not feed_line_units[entity.unit_number] then
        local chest_x, chest_y = bx + vx * 2, by + vy * 2
        local ins_x, ins_y = bx + vx, by + vy
        if not occupied[chest_x .. "," .. chest_y] and not occupied[ins_x .. "," .. ins_y] then
          local ok_ins, collector = try(function()
            return surface.create_entity{ name = "burner-inserter", position = { x = ins_x + 0.5, y = ins_y + 0.5 },
                                          direction = (direction + 8) % 16, force = force }
          end)
          local ok_chest, chest = try(function()
            return surface.create_entity{ name = "steel-chest", position = { x = chest_x + 0.5, y = chest_y + 0.5 }, force = force }
          end)
          if ok_ins and collector ~= nil and ok_chest and chest ~= nil then
            fixtures.sinks[#fixtures.sinks + 1] = chest
            fixtures.sink_belts = fixtures.sink_belts or {}
            fixtures.sink_belts[#fixtures.sink_belts + 1] = entity
            fixtures.burners = fixtures.burners or {}
            fixtures.burners[#fixtures.burners + 1] = collector
            fixtures.units[chest.unit_number] = true
            fixtures.units[collector.unit_number] = true
            mark(chest_x, chest_y); mark(ins_x, ins_y)
          end
        end
      end
    end
  end

  -- 电：发电机只要能落在某根杆的供电范围里就会自动并网（不用拉线）。
  -- 所以给蓝图自己的每根杆旁边塞一台电接口，实在塞不下就塞太阳能板。
  local poles = surface.find_entities_filtered{ area = area, type = "electric-pole" }
  fixtures.poles = #poles
  fixtures.generators = 0
  fixtures.generator_kind = nil
  if #poles > 0 then
    try(function() surface.always_day = true end)
    for _, pole in pairs(poles) do
      local pole_x, pole_y = tile_of(pole.position)
      local placed_here = false
      for _, delta in ipairs({ { 1, 0 }, { -1, 0 }, { 0, 1 }, { 0, -1 }, { 2, 0 }, { 0, 2 } }) do
        if not placed_here then
          local tile_x, tile_y = pole_x + delta[1], pole_y + delta[2]
          if not occupied[tile_x .. "," .. tile_y] then
            local ok_gen, generator = try(function()
              return surface.create_entity{ name = "electric-energy-interface",
                                            position = { x = tile_x + 0.5, y = tile_y + 0.5 }, force = force }
            end)
            if ok_gen and generator ~= nil then
              fixtures.generators = fixtures.generators + 1
              fixtures.generator_kind = fixtures.generator_kind or "electric-energy-interface"
              fixtures.units[generator.unit_number] = true
              occupied[tile_x .. "," .. tile_y] = true
              placed_here = true
            end
          end
        end
      end
    end
  end


  -- 只按声明供固体：每格一个永续箱 + 一只臂，往那一格注
  for _, entry in ipairs(fixtures.declared_supply) do
    local target_x, target_y = entry.x, entry.y
    local function occupied_at(cx, cy) return occupied[cx .. "," .. cy] ~= nil end
    local placed = false
    for _, delta in ipairs({ { 1, 0 }, { -1, 0 }, { 0, 1 }, { 0, -1 }, { 2, 0 }, { -2, 0 }, { 0, 2 }, { 0, -2 } }) do
      if not placed then
        local ins_x, ins_y = target_x + delta[1], target_y + delta[2]
        local chest_x = target_x + delta[1] * 2
        local chest_y = target_y + delta[2] * 2
        if not occupied_at(ins_x, ins_y) and not occupied_at(chest_x, chest_y) then
          local facing = (delta[1] == 1 and 12) or (delta[1] == -1 and 4) or (delta[2] == 1 and 8) or (delta[2] == -1 and 0)
          local ok_ins, feeder = try(function()
            return surface.create_entity{ name = "fast-inserter", position = { x = ins_x + 0.5, y = ins_y + 0.5 },
                                          direction = facing, force = force }
          end)
          local ok_chest, chest = false, nil
          if ok_ins and feeder ~= nil then
            ok_chest, chest = try(function()
              return surface.create_entity{ name = "infinity-chest", position = { x = chest_x + 0.5, y = chest_y + 0.5 }, force = force }
            end)
          end
          if ok_chest and chest ~= nil then
            try(function() chest.set_infinity_container_filter(1, { name = entry.item, count = 400, mode = "at-least" }) end)
            fixtures.feeds[#fixtures.feeds + 1] = chest
            fixtures.units[chest.unit_number] = true
            fixtures.units[feeder.unit_number] = true
            fixtures.declared_placed = fixtures.declared_placed or {}
            fixtures.declared_placed[#fixtures.declared_placed + 1] = {
              cell = entry.cell, item = entry.item,
              at = { ins_x + 0.5, ins_y + 0.5 }, chest = { chest_x + 0.5, chest_y + 0.5 },
            }
            placed = true
          else
            if ok_ins and feeder ~= nil then try(function() feeder.destroy() end) end
          end
        end
      end
    end
    if not placed then
      fixtures.supply_errors = fixtures.supply_errors or {}
      fixtures.supply_errors[#fixtures.supply_errors + 1] = {
        code = "supply_placement_failed", severity = "error",
        message = string.format("声明供给格 (%s,%s) 周围没有地方放箱子+臂，%s 没供上", entry.cell[1], entry.cell[2], entry.item),
      }
    end
  end
  return fixtures
end

-- 直接往带子上按节拍灌物品：机械臂只有 ~0.83 件/秒，喂不饱 24 台炉子（需要 7.5 矿石/秒）。
-- 这就是 Creative Mod 的"无限带"在做的事，只是这里用 insert_at 实现。
local function inject_belts(fixtures, tick)
  if not fixtures.feed_belts then return end
  local injected = 0
  for _, record in pairs(fixtures.feed_belts) do
    local belt = (record.belt ~= nil) and record.belt or record
    local belt_item = record.item or fixtures.main_item or "iron-ore"
    if belt.valid then
      local ok_line1, line1 = try(function() return belt.get_transport_line(1) end)
      local ok_line2, line2 = try(function() return belt.get_transport_line(2) end)
      if ok_line1 and line1 ~= nil then
        local ok1 = try(function() return line1.insert_at(0, { name = belt_item, count = 1 }) end)
        if ok1 then injected = injected + 1 end
      end
      if ok_line2 and line2 ~= nil then
        local lane2_item = fixtures.needs_fuel and "coal" or belt_item
        local ok2 = try(function() return line2.insert_at(0, { name = lane2_item, count = 1 }) end)
        if ok2 then injected = injected + 1 end
      end
    end
  end
  fixtures.injected_total = (fixtures.injected_total or 0) + injected
end

-- 出口带按节拍清空并计数：这是"数产物"的夹具，不改变蓝图本身
local function compute_drain_set(surface, area, fixtures, port_tiles)
  -- 声明了输出口 → 只清这些格子上的带子；没声明 → 退化成"末端带"启发式
  if port_tiles ~= nil and next(port_tiles) ~= nil then
    local list = {}
    for _, belt in pairs(surface.find_entities_filtered{ area = area, type = "transport-belt" }) do
      if belt.valid then
        local bx, by = math.floor(belt.position.x), math.floor(belt.position.y)
        local hit = false
        for dx = -1, 1 do
          for dy = -1, 1 do
            if port_tiles[(bx + dx) .. "," .. (by + dy)] then hit = true end
          end
        end
        if hit then list[#list + 1] = belt end
      end
    end
    return list, {}
  end
  local picked_belt_units = {}
  local blueprint_inserters = nil
  if fixtures ~= nil then blueprint_inserters = true end
  if blueprint_inserters then
    local belt_tiles = {}
    for _, belt in pairs(surface.find_entities_filtered{ area = area, type = "transport-belt" }) do
      if belt.valid then
        belt_tiles[math.floor(belt.position.x) .. "," .. math.floor(belt.position.y)] = belt.unit_number
      end
    end
    for _, inserter in pairs(surface.find_entities_filtered{ area = area, type = "inserter" }) do
      if inserter.valid and not ((fixtures.units or {})[inserter.unit_number]) then
        local ok_pickup, pickup = try(function() return inserter.pickup_position end)
        if ok_pickup and pickup ~= nil then
          local hit = belt_tiles[math.floor(pickup.x) .. "," .. math.floor(pickup.y)]
          if hit ~= nil then picked_belt_units[hit] = true end
        end
      end
    end
  end
  local output_belt_units = {}
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid and entity.type == "inserter" then
      local ok_pickup, pickup = try(function() return entity.pickup_target end)
      local ok_drop, drop = try(function() return entity.drop_target end)
      if ok_pickup and ok_drop and pickup ~= nil and drop ~= nil then
        if (pickup.type == "furnace" or pickup.type == "assembling-machine")
           and (drop.type == "transport-belt" or drop.type == "underground-belt") then
          if not picked_belt_units[drop.unit_number] then
            output_belt_units[drop.unit_number] = true
          end
        end
      end
    end
  end
  local seen = {}
  local list = {}
  local function collect_downstream(belt, depth)
    if depth <= 0 or belt == nil or seen[belt.unit_number] then return end
    seen[belt.unit_number] = true
    list[#list + 1] = belt
    local ok_n, neighbours = try(function() return belt.belt_neighbours end)
    local outputs = (ok_n and neighbours and neighbours.outputs) or {}
    for _, downstream in pairs(outputs) do collect_downstream(downstream, depth - 1) end
  end
  for _, entity in pairs(surface.find_entities_filtered{ area = area, type = "transport-belt" }) do
    if entity.valid and output_belt_units[entity.unit_number] then
      collect_downstream(entity, 80)
    end
  end
  return list, output_belt_units
end

local function drain_outputs(fixtures, surface)
  if fixtures.drain_belts == nil then
    fixtures.drain_belts, fixtures.output_belt_units = compute_drain_set(surface, fixtures.area, fixtures, fixtures.port_tiles)
    fixtures.drain_scope = { mode = fixtures.drain_mode or "none", belts = {} }
    for _, belt in pairs(fixtures.drain_belts or {}) do
      fixtures.drain_scope.belts[#fixtures.drain_scope.belts + 1] = { math.floor(belt.position.x), math.floor(belt.position.y) }
    end
  end
  fixtures.drained = fixtures.drained or {}
  for _, belt in pairs(fixtures.drain_belts) do
    if belt.valid then
      for lane = 1, 2 do
        local ok_line, line = try(function() return belt.get_transport_line(lane) end)
        if ok_line and line ~= nil then
          local ok_contents, contents = try(function() return line.get_contents() end)
          if ok_contents and type(contents) == "table" then
            for _, item in pairs(contents) do
              if item.count and item.count > 0 then
                fixtures.drained[tostring(item.name)] = (fixtures.drained[tostring(item.name)] or 0) + item.count
              end
            end
          end
          try(function() line.clear() end)
        end
      end
    end
  end
end

local function feed_chests(fixtures)
  for _, burner in pairs(fixtures.burners or {}) do
    if burner.valid then
      local ok_fuel, fuel = try(function() return burner.get_inventory(defines.inventory.fuel) end)
      if ok_fuel and fuel ~= nil then
        pcall(function() fuel.insert{ name = "coal", count = 10 } end)
      end
    end
  end
  for _, chest in pairs(fixtures.feeds or {}) do
    if chest.valid then
      local ok_inv, inventory = try(function() return chest.get_inventory(defines.inventory.chest) end)
      -- 永续箱自己按 filter 供货，这里只补燃料（燃烧臂）
      if ok_inv and inventory ~= nil and fixtures.needs_fuel then
        pcall(function() inventory.insert{ name = "coal", count = 50 } end)
      end
    end
  end
end

local function fixture_diagnostics(fixtures)
  local out = { feeds = {}, burners = {} }
  for _, chest in pairs(fixtures.feeds or {}) do
    if chest.valid then
      local ok_inv, inventory = try(function() return chest.get_inventory(defines.inventory.chest) end)
      local ok_contents, contents = try(function() return inventory.get_contents() end)
      out.feeds[#out.feeds + 1] = { unit = chest.unit_number, contents = (ok_contents and contents) or nil }
    end
  end
  for _, belt in pairs(fixtures.feed_belts or {}) do
    if belt.valid then
      local lane_counts = {}
      for lane = 1, 2 do
        local ok_line, line = try(function() return belt.get_transport_line(lane) end)
        if ok_line and line ~= nil then
          local ok_count, count = try(function() return line.get_item_count() end)
          lane_counts[lane] = (ok_count and count) or nil
        end
      end
      out.feeds[#out.feeds + 1] = { kind = "belt", unit = belt.unit_number, lanes = lane_counts }
    end
  end
  for _, burner in pairs(fixtures.burners or {}) do
    if burner.valid then
      local ok_status, status = try(function() return burner.status end)
      local ok_fuel, fuel = try(function() return burner.get_inventory(defines.inventory.fuel).get_contents() end)
      out.burners[#out.burners + 1] = { name = burner.name, status = status_name(ok_status and status or nil),
                                       fuel = (ok_fuel and fuel) or nil }
    end
  end
  return out
end

local function production_report(surface, area, memory, declared, inputs, declared_by_name)
  local machines = {}
  local total = 0
  local recipe_votes = {}
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid and (entity.type == "furnace" or entity.type == "assembling-machine") then
      local ok_products, products = try(function() return entity.products_finished end)
      local ok_recipe, recipe = try(function() return entity.get_recipe() end)
      local ok_status, status = try(function() return entity.status end)
      local ok_speed, speed = try(function() return entity.prototype.crafting_speed end)
      local ok_energy, energy = try(function() return recipe and recipe.energy or nil end)
      local recipe_name = (ok_recipe and recipe) and recipe.name or nil
      local recipe_energy = (ok_energy and energy) or nil
      local game_recipe = recipe_name
      local declared_recipe = nil
      if declared ~= nil then
        declared_recipe = declared[math.floor(entity.position.x) .. "," .. math.floor(entity.position.y)]
        if declared_recipe == nil and declared_by_name ~= nil then
          declared_recipe = declared_by_name[entity.name]
        end
      end
      local recipe_source = "game"
      if recipe_name == nil and declared_recipe ~= nil then
        recipe_name = declared_recipe
        recipe_source = "blueprint"
        local ok_proto, recipe_prototype = try(function() return prototypes.recipe[declared_recipe] end)
        if ok_proto and recipe_prototype ~= nil then
          local ok_energy2, energy2 = try(function() return recipe_prototype.energy end)
          recipe_energy = (ok_energy2 and energy2) or recipe_energy
        end
      elseif recipe_name ~= nil and declared_recipe ~= nil and declared_recipe ~= recipe_name then
        -- 游戏里炼的和蓝图声明的不是同一个配方：理论值按**蓝图声明**算（那才是这张图想要的产量），
        -- 达成率不给（见 python 侧 recipe_mismatch）。
        recipe_source = "mismatch"
        recipe_name = declared_recipe
        local ok_proto, recipe_prototype = try(function() return prototypes.recipe[declared_recipe] end)
        if ok_proto and recipe_prototype ~= nil then
          local ok_energy3, energy3 = try(function() return recipe_prototype.energy end)
          recipe_energy = (ok_energy3 and energy3) or recipe_energy
        end
      end
      -- 机器空闲时 get_recipe() 会是 nil，用前面采样记下的"最后一任配方"兜住；
      -- 连记忆都没有就如实报 null，绝不猜一个配方来当分母。
      if recipe_name == nil and memory ~= nil then
        local remembered = memory[entity.unit_number]
        if remembered ~= nil then
          recipe_name = remembered.name
          recipe_energy = remembered.energy
        end
      end
      local ok_speed, speed = try(function() return entity.crafting_speed end)
      local crafting_speed = (ok_speed and speed) or nil
      local seconds_per_item = nil
      if crafting_speed ~= nil and recipe_energy ~= nil and recipe_energy > 0 and crafting_speed > 0 then
        seconds_per_item = recipe_energy / crafting_speed
      end
      if recipe_name ~= nil then
        recipe_votes[recipe_name] = (recipe_votes[recipe_name] or 0) + 1
      end
      local entry = {
        entity = entity.unit_number, name = entity.name,
        recipe = recipe_name,
        recipe_source = recipe_source,
        game_recipe = game_recipe,
        blueprint_recipe = declared_recipe,
        recipe_energy = recipe_energy,
        crafting_speed = crafting_speed,
        seconds_per_item = seconds_per_item,
        products_finished = (ok_products and products) or 0,
        status = status_name(ok_status and status or nil),
        input = (function()
          local ok_input, input_inventory = try(function() return entity.get_inventory(defines.inventory.crafter_input) end)
          if not ok_input or input_inventory == nil then return nil end
          local ok_contents, contents = try(function() return input_inventory.get_contents() end)
          return (ok_contents and contents) or nil
        end)(),
        input_max_total = (inputs ~= nil and inputs[entity.unit_number] ~= nil)
                            and inputs[entity.unit_number].max_total or nil,
        output = (function()
          local ok_output, output_inventory = try(function() return entity.get_inventory(defines.inventory.crafter_output) end)
          if not ok_output or output_inventory == nil then return nil end
          local ok_contents, contents = try(function() return output_inventory.get_contents() end)
          return (ok_contents and contents) or nil
        end)(),
      }
      total = total + entry.products_finished
      machines[#machines + 1] = entry
    end
  end
  local belt_items = 0
  local belt_tiles = 0
  local belt_item_names = {}
  for _, belt in pairs(surface.find_entities_filtered{ area = area, type = "transport-belt" }) do
    if belt.valid then
      belt_tiles = belt_tiles + 1
      for lane = 1, 2 do
        local ok_line, line = try(function() return belt.get_transport_line(lane) end)
        if ok_line and line ~= nil then
          local ok_count, count = try(function() return line.get_item_count() end)
          if ok_count and count then belt_items = belt_items + count end
          local ok_contents, contents = try(function() return line.get_contents() end)
          if ok_contents and type(contents) == "table" then
            for _, item in pairs(contents) do
              belt_item_names[tostring(item.name)] = (belt_item_names[tostring(item.name)] or 0) + (item.count or 0)
            end
          end
        end
      end
    end
  end
  local furnace_inputs = {}
  for _, furnace in pairs(surface.find_entities_filtered{ area = area }) do
    if furnace.valid and furnace.type == "furnace" then
      local ok_inv, inventory = try(function() return furnace.get_inventory(defines.inventory.furnace_source) end)
      if ok_inv and inventory ~= nil then
        local ok_contents, contents = try(function() return inventory.get_contents() end)
        furnace_inputs[#furnace_inputs + 1] = { unit = furnace.unit_number,
                                                contents = (ok_contents and contents) or nil }
        if #furnace_inputs >= 3 then break end
      end
    end
  end
  -- 饿着的机器 get_recipe() 是 nil，公式里就少算一台；用本作业里出现最多的配方给它补上
  local dominant_recipe, dominant_votes = nil, 0
  for name, votes in pairs(recipe_votes) do
    if votes > dominant_votes then dominant_recipe, dominant_votes = name, votes end
  end
  if dominant_recipe ~= nil then
    local ok_proto, recipe_prototype = try(function() return prototypes.recipe[dominant_recipe] end)
    local dominant_energy = nil
    if ok_proto and recipe_prototype ~= nil then
      dominant_energy = select(2, try(function() return recipe_prototype.energy end))
    end
    for _, entry in ipairs(machines) do
      if entry.recipe == nil and entry.crafting_speed ~= nil and dominant_energy ~= nil then
        entry.recipe = dominant_recipe
        entry.recipe_source = "inferred"
        entry.recipe_energy = dominant_energy
        entry.seconds_per_item = dominant_energy / entry.crafting_speed
      end
    end
  end
  local belt_contents = {}
  for _, belt in pairs(surface.find_entities_filtered{ area = area, type = "transport-belt" }) do
    if belt.valid then
      local lanes = {}
      for lane = 1, 2 do
        local ok_line, line = try(function() return belt.get_transport_line(lane) end)
        if ok_line and line ~= nil then
          local ok_contents, contents = try(function() return line.get_contents() end)
          if ok_contents and type(contents) == "table" and #contents > 0 then
            lanes[lane] = contents
          end
        end
      end
      if lanes[1] ~= nil or lanes[2] ~= nil then
        belt_contents[#belt_contents + 1] = {
          y = math.floor(belt.position.y), x = math.floor(belt.position.x),
          dir = belt.direction, lanes = lanes,
        }
      end
    end
  end
  local chests = {}
  for _, entity in pairs(surface.find_entities_filtered{ area = area, name = "steel-chest" }) do
    if entity.valid then
      local ok_inv, inventory = try(function() return entity.get_inventory(defines.inventory.chest) end)
      if ok_inv and inventory ~= nil then
        local ok_contents, contents = try(function() return inventory.get_contents() end)
        if ok_contents and type(contents) == "table" then
          chests[#chests + 1] = { entity = entity.unit_number,
                                  pos = { entity.position.x, entity.position.y },
                                  contents = contents }
        end
      end
    end
  end
  return { belt_contents = belt_contents, machines = machines, total_products = total, chests = chests,
           belt_items = belt_items, belt_tiles = belt_tiles, belt_item_names = belt_item_names,
           furnace_inputs = furnace_inputs }
end


-- 蓝图里的配方不会凭空出现在建出来的机器上（炉子的 get_recipe() 会是 nil，随后自己按进料选配方）。
-- 这里按"蓝图坐标 + 位移 = 世界坐标"把配方显式写回去，否则测的是"机器自己挑的配方"，不是蓝图要的。
local function apply_blueprint_recipes(surface, area, job)
  local built = {}
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid and entity.type ~= "resource" then
      built[#built + 1] = { name = entity.name, x = entity.position.x, y = entity.position.y, entity = entity }
    end
  end
  if #built == 0 then return { applied = 0, offset = nil } end
  local blueprint_entities = job.json.entities or {}
  -- 位移用"同名实体配对里出现次数最多的 (dx,dy)"来定，别拿第一个同名就信（同名传送带有几百个）
  local votes = {}
  local built_by_name = {}
  for _, candidate in ipairs(built) do
    built_by_name[candidate.name] = built_by_name[candidate.name] or {}
    table.insert(built_by_name[candidate.name], candidate)
  end
  for _, blueprint_entity in ipairs(blueprint_entities) do
    local candidates = built_by_name[blueprint_entity.name]
    if candidates ~= nil and blueprint_entity.position ~= nil then
      for _, candidate in ipairs(candidates) do
        local dx = math.floor((candidate.x - blueprint_entity.position.x) * 2 + 0.5) / 2
        local dy = math.floor((candidate.y - blueprint_entity.position.y) * 2 + 0.5) / 2
        local key = dx .. "," .. dy
        votes[key] = (votes[key] or 0) + 1
      end
    end
  end
  local best_key, best_votes = nil, 0
  for key, count in pairs(votes) do
    if count > best_votes then best_key, best_votes = key, count end
  end
  local offset_x, offset_y = 0, 0
  if best_key ~= nil then
    local sx, sy = best_key:match("(%-?%d+%.?%d*),(%-?%d+%.?%d*)")
    offset_x, offset_y = tonumber(sx) or 0, tonumber(sy) or 0
  end
  local applied, failed = 0, 0
  local first_error = nil
  for _, blueprint_entity in ipairs(blueprint_entities) do
    if blueprint_entity.recipe ~= nil and blueprint_entity.position ~= nil then
      local target_x = blueprint_entity.position.x + offset_x
      local target_y = blueprint_entity.position.y + offset_y
      for _, candidate in ipairs(built) do
        if candidate.name == blueprint_entity.name
           and math.abs(candidate.x - target_x) < 0.6 and math.abs(candidate.y - target_y) < 0.6 then
          local ok_set, set_error = try(function() return candidate.entity.set_recipe(blueprint_entity.recipe) end)
          if ok_set then
            applied = applied + 1
          else
            failed = failed + 1
            if first_error == nil then
              first_error = tostring(set_error) .. " | target=" .. tostring(candidate.name)
                .. " recipe=" .. tostring(blueprint_entity.recipe)
            end
          end
          break
        end
      end
    end
  end
  -- 蓝图声明的配方（按世界坐标记下来）：炉子的固定配方在 build_blueprint+revive 路径上会丢，
  -- 这份表用来说明"蓝图要的是哪个配方"，以及和游戏实际用的配方是否一致。
  local declared = {}
  local declared_by_name = {}
  local name_votes = {}
  for _, blueprint_entity in ipairs(blueprint_entities) do
    if blueprint_entity.recipe ~= nil and blueprint_entity.position ~= nil then
      local world_x = blueprint_entity.position.x + offset_x
      local world_y = blueprint_entity.position.y + offset_y
      declared[math.floor(world_x) .. "," .. math.floor(world_y)] = blueprint_entity.recipe
      name_votes[blueprint_entity.name] = name_votes[blueprint_entity.name] or {}
      name_votes[blueprint_entity.name][blueprint_entity.recipe] =
        (name_votes[blueprint_entity.name][blueprint_entity.recipe] or 0) + 1
    end
  end
  for entity_name, votes in pairs(name_votes) do
    local best, best_count = nil, 0
    for recipe_name, count in pairs(votes) do
      if count > best_count then best, best_count = recipe_name, count end
    end
    declared_by_name[entity_name] = best
  end
  return { applied = applied, failed = failed, first_error = first_error,
           offset = { offset_x, offset_y }, declared = declared, declared_by_name = declared_by_name }
end


-- 流体判据第四条：往管网里灌流体，然后看目标机器/储罐里到底有没有。
-- 源用 infinity-pipe（它自己就是一根管道，接在管网上就会并进同一个管段）。

-- 机器启动后才知道配方：把自动判断的流体名纠正过来（只改过滤器，不重建实体）
local function refine_fluid_source(surface, locals_state, recipe_memory)
  if FLUID_NAME ~= nil and FLUID_NAME ~= "" then return end
  if locals_state == nil or locals_state.fluid_source == nil or not locals_state.fluid_source.placed then return end
  local current = locals_state.fluid_source.fluid
  local wanted = nil
  for _, recipe_name in pairs(recipe_memory or {}) do
    local ok_proto, proto = try(function() return prototypes.recipe[recipe_name] end)
    if ok_proto and proto ~= nil then
      for _, ingredient in pairs(proto.ingredients) do
        if ingredient.type == "fluid" and wanted == nil then wanted = ingredient.name end
      end
    end
  end
  if wanted == nil or wanted == current then return end
  local ok_entity, entity = try(function() return surface.find_entity("infinity-pipe", locals_state.fluid_source.at) end)
  if not ok_entity or entity == nil then return end
  local ok_set = try(function() entity.set_infinity_pipe_filter{ name = wanted, percentage = 1.0 } end)
  if ok_set then
    locals_state.fluid_source.fluid = wanted
    locals_state.fluid_source.refined_from = current
  end
end

local function setup_fluid_source(surface, force, area)
  -- 没点名灌什么，就从机器配方里读"它需要哪种液体"，别默认 water
  local fluid_name = FLUID_NAME
  if fluid_name == nil or fluid_name == "" then
    -- 优先读蓝图自己声明的配方（机器还没跑起来时 get_recipe() 可能是 nil）
    local declared_recipes = {}
    if res ~= nil and res.recipes ~= nil then
      for _, recipe_name in pairs(res.recipes.declared_by_name or {}) do
        declared_recipes[recipe_name] = true
      end
      for _, recipe_name in pairs(res.recipes.declared or {}) do
        declared_recipes[recipe_name] = true
      end
    end
    for recipe_name in pairs(declared_recipes) do
      local ok_proto, proto = try(function() return prototypes.recipe[recipe_name] end)
      if ok_proto and proto ~= nil then
        for _, ingredient in pairs(proto.ingredients) do
          if ingredient.type == "fluid" and fluid_name == nil then fluid_name = ingredient.name end
        end
      end
    end
  end
  if fluid_name == nil or fluid_name == "" then
    for _, machine in pairs(surface.find_entities_filtered{ area = area }) do
      if machine.valid and (machine.type == "assembling-machine" or machine.type == "furnace") then
        local ok_recipe, recipe = try(function() return machine.get_recipe() end)
        if ok_recipe and recipe ~= nil then
          local ok_proto, proto = try(function() return prototypes.recipe[recipe.name] end)
          if ok_proto and proto ~= nil then
            for _, ingredient in pairs(proto.ingredients) do
              if ingredient.type == "fluid" and fluid_name == nil then
                fluid_name = ingredient.name
              end
            end
          end
        end
      end
    end
  end
  if fluid_name == nil or fluid_name == "" then fluid_name = "water" end
  local result = { placed = false, fluid = fluid_name }
  local anchor = nil
  -- 1) 模板自己带的 infinity-pipe：把它当"声明的流体口"，连流体名一起采纳
  for _, entity in pairs(surface.find_entities_filtered{ area = area, name = "infinity-pipe" }) do
    if entity.valid then
      anchor = { entity.position.x, entity.position.y }
      local ok_settings, settings = try(function() return entity.get_infinity_pipe_filter() end)
      if ok_settings and settings ~= nil and settings.name ~= nil then
        result.template_fluid = settings.name
      end
      result.template_source = { anchor[1], anchor[2] }
      break
    end
  end
  -- 2) 调用方声明的 --fluid-port（蓝图坐标 → 世界坐标）
  if FLUID_PORT ~= nil and FLUID_PORT ~= "" then
    local sx, sy = string.match(FLUID_PORT, "(-?%d+%.?%d*),(-?%d+%.?%d*)")
    if sx ~= nil and sy ~= nil then
      local ox, oy = 0, 0
      if res ~= nil and res.recipes ~= nil and res.recipes.offset ~= nil then
        ox, oy = res.recipes.offset[1], res.recipes.offset[2]
      end
      anchor = { tonumber(sx) + ox, tonumber(sy) + oy }
      result.declared_port = { anchor[1], anchor[2] }
    end
  end
  -- 绝不能盖掉蓝图自己的实体：先记下所有被占的格
  local occupied = {}
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid then
      occupied[math.floor(entity.position.x) .. "," .. math.floor(entity.position.y)] = true
    end
  end
  local pipes = surface.find_entities_filtered{ area = area, type = "pipe" }
  if #pipes == 0 then
    pipes = surface.find_entities_filtered{ area = area, type = "pipe-to-ground" }
  end
  -- 有锚点就按"离锚点最近"排，没有就按原顺序
  if anchor ~= nil then
    table.sort(pipes, function(a, b)
      local da = math.abs(a.position.x - anchor[1]) + math.abs(a.position.y - anchor[2])
      local db = math.abs(b.position.x - anchor[1]) + math.abs(b.position.y - anchor[2])
      return da < db
    end)
    result.anchor_distance = math.abs(pipes[1].position.x - anchor[1]) + math.abs(pipes[1].position.y - anchor[2])
  end
  for _, pipe in pairs(pipes) do
    local px, py = tile_of(pipe.position)
    for _, delta in ipairs({ { 1, 0 }, { -1, 0 }, { 0, 1 }, { 0, -1 } }) do
      local tx, ty = px + delta[1], py + delta[2]
      if not occupied[tx .. "," .. ty] then
        local ok_source, source = try(function()
          return surface.create_entity{ name = "infinity-pipe", position = { x = tx + 0.5, y = ty + 0.5 }, force = force }
        end)
        if ok_source and source ~= nil then
          local ok_filter = try(function()
            source.set_infinity_pipe_filter{ name = fluid_name, percentage = 1.0 }
          end)
          local ok_segment, segment = try(function() return source.get_fluid_segment_id(1) end)
          if ok_filter and ok_segment and segment ~= nil then
            result.placed = true
            result.at = { tx + 0.5, ty + 0.5 }
            result.segment = segment
            result.unit = source.unit_number
            return result
          end
          try(function() source.destroy() end)
        end
      end
    end
  end
  return result
end

local function fluid_report(surface, area, exclude_units)
  local report = { entities = {}, by_segment = {} }
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid and not (exclude_units and exclude_units[entity.unit_number])
       and (entity.type == "pipe" or entity.type == "pipe-to-ground" or entity.type == "storage-tank"
            or entity.type == "furnace" or entity.type == "assembling-machine" or entity.type == "chemical-plant"
            or entity.type == "boiler" or entity.type == "offshore-pump" or entity.type == "pump") then
      local ok_contents, contents = try(function() return entity.get_fluid_contents() end)
      local ok_segment, segment = try(function() return entity.get_fluid_segment_id(1) end)
      local ok_recipe, recipe = try(function() return entity.get_recipe() end)
      local entry = {
        unit = entity.unit_number, name = entity.name, type = entity.type,
        pos = { entity.position.x, entity.position.y },
        contents = (ok_contents and type(contents) == "table") and contents or {},
        segment = (ok_segment and segment) or nil,
        -- 有配方才会吸液；没配方的机器读出来是 0，但那是"前提不成立"，不是缺陷
        recipe = (ok_recipe and recipe) and recipe.name or nil,
        has_fluid_port = (entity.type == "storage-tank" or entity.type == "boiler"
                          or entity.type == "chemical-plant" or entity.type == "oil-refinery"
                          or entity.type == "furnace" or entity.type == "assembling-machine"),
      }
      report.entities[#report.entities + 1] = entry
      if entry.segment ~= nil and type(entry.contents) == "table" then
        local bucket = report.by_segment[tostring(entry.segment)] or {}
        for fluid, amount in pairs(entry.contents) do
          bucket[tostring(fluid)] = (bucket[tostring(fluid)] or 0) + amount
        end
        report.by_segment[tostring(entry.segment)] = bucket
      end
    end
  end
  return report
end


-- 蓝图验证问的是"这张图能不能跑"，不是"玩家研究过没有"。
-- 全新存档里 electronic-circuit / copper-cable 之类都是未解锁，组装机会报 recipe_not_researched，
-- 整条产线就量不出产量。默认把（非隐藏的）配方全解锁，需要"保持原样"时用 --keep-locked。
local function unlock_all_recipes(force)
  local unlocked, failed = 0, 0
  local ok_recipes, recipes = try(function() return force.recipes end)
  if not ok_recipes or recipes == nil then return { unlocked = 0, failed = 0, error = "no recipes table" } end
  for name, recipe in pairs(recipes) do
    if not recipe.hidden then
      local ok_set = try(function() recipe.enabled = true end)
      if ok_set then unlocked = unlocked + 1 else failed = failed + 1 end
    end
  end
  return { unlocked = unlocked, failed = failed }
end

-- 蓝图坐标 → 世界坐标的位移不是 0（游戏按包围盒居中放置）。矿脉、水塘都要按真实落点铺，
-- 所以先在远处试建一次量出位移。返回 (dx, dy)，量不到就返回 nil。
local function measure_offset(surface, force, job)
  local probe_origin = { x = 240, y = 240 }
  local probe_box = { { probe_origin.x - 60, probe_origin.y - 60 }, { probe_origin.x + 60, probe_origin.y + 60 } }
  try(function() surface.request_to_generate_chunks(probe_origin, 4) end)
  try(function() surface.force_generate_chunk_requests() end)
  wipe(surface, probe_box)
  local offset_x, offset_y = nil, nil
  local probe_stack = game.create_inventory(1)[1]
  probe_stack.clear()
  try(function() probe_stack.set_stack{ name = "blueprint" } end)
  local ok_import = try(function() return probe_stack.import_stack(job.blob) end)
  if ok_import then
    try(function()
      return probe_stack.build_blueprint{ surface = surface, force = force, position = probe_origin,
                                          build_mode = defines.build_mode.superforced, skip_fog_of_war = true }
    end)
    local blueprint_positions = {}
    for _, blueprint_entity in ipairs(job.json.entities or {}) do
      if blueprint_entity.position ~= nil and blueprint_entity.name ~= nil then
        blueprint_positions[#blueprint_positions + 1] = { name = blueprint_entity.name,
                                                          x = blueprint_entity.position.x,
                                                          y = blueprint_entity.position.y }
      end
    end
    for _, built_entity in pairs(surface.find_entities_filtered{ area = probe_box }) do
      if built_entity.valid and built_entity.position ~= nil then
        local built_name = built_entity.name
        if built_name == "entity-ghost" then built_name = select(2, try(function() return built_entity.ghost_name end)) end
        for _, blueprint_entity in ipairs(blueprint_positions) do
          if blueprint_entity.name == built_name then
            local dx = built_entity.position.x - blueprint_entity.x
            local dy = built_entity.position.y - blueprint_entity.y
            if offset_x == nil then offset_x, offset_y = dx, dy end
          end
        end
      end
    end
    wipe(surface, probe_box)
  end
  if offset_x == nil then return nil end
  return offset_x - probe_origin.x, offset_y - probe_origin.y
end

local function stage_job(job, index, force)
  local res = { id = job.id }
  local bp = job.json
  if type(bp) ~= "table" then
    res.stage = "bad_json"
    return res
  end

  local issues, expected = validate(bp)
  res.issues = issues
  res.expected_entities = expected
  res.blueprint_label = bp.label
  res.blueprint_tiles = type(bp.tiles) == "table" and #bp.tiles or 0
  res.expected_names = {}
  for _, entity in ipairs(bp.entities or {}) do
    res.expected_names[#res.expected_names + 1] = tostring(entity.name)
  end

  if UNLOCK_RECIPES then
    res.recipes_unlocked = unlock_all_recipes(force)
  end
  local surface, terrain_mode = new_surface(index)
  if surface == nil then
    res.stage = "surface_failed"
    return res
  end
  res.terrain_mode = terrain_mode
  local area = { { -ARENA_HALF, -ARENA_HALF }, { ARENA_HALF, ARENA_HALF } }
  res.area = area
  local destroyed, water_filled, fill_ok = clear_area(surface, area)
  local center_tile = nil
  try(function() center_tile = surface.get_tile(0, 0).name end)
  res.arena = { destroyed_entities = destroyed, water_tiles_filled = water_filled,
                landfill_ok = fill_ok, center_tile = center_tile }

  -- 地形模式：ore = 整片矿；water = 一小片池塘（水会按水泵自己要的水位再补）
  local terrain_setup = { mode = TERRAIN }
  if TERRAIN == "ore" then
    local offset_x, offset_y = measure_offset(surface, force, job)
    terrain_setup.probe_offset = offset_x and { offset_x, offset_y } or nil
    local shift_x, shift_y = offset_x or 0, offset_y or 0
    local min_x, min_y, max_x, max_y = nil, nil, nil, nil
    for _, blueprint_entity in ipairs(job.json.entities or {}) do
      local blueprint_position = blueprint_entity.position
      if blueprint_position ~= nil then
        min_x = math.min(min_x or blueprint_position.x, blueprint_position.x)
        max_x = math.max(max_x or blueprint_position.x, blueprint_position.x)
        min_y = math.min(min_y or blueprint_position.y, blueprint_position.y)
        max_y = math.max(max_y or blueprint_position.y, blueprint_position.y)
      end
    end
    local from_x = math.max(-ARENA_HALF + 2, math.floor((min_x or -48) + shift_x - 4))
    local to_x = math.min(ARENA_HALF - 2, math.ceil((max_x or 48) + shift_x + 4))
    local from_y = math.max(-ARENA_HALF + 2, math.floor((min_y or -48) + shift_y - 4))
    local to_y = math.min(ARENA_HALF - 2, math.ceil((max_y or 48) + shift_y + 4))
    local placed, failed = 0, 0
    for tile_x = from_x, to_x do
      for tile_y = from_y, to_y do
        local ok_ore = try(function()
          return surface.create_entity{ name = "iron-ore", position = { x = tile_x + 0.5, y = tile_y + 0.5 }, amount = 5000 }
        end)
        if ok_ore then placed = placed + 1 else failed = failed + 1 end
      end
    end
    terrain_setup.ore_entities = placed
    terrain_setup.ore_failed = failed
    terrain_setup.ore_area = { from_x, from_y, to_x, to_y }
  elseif TERRAIN == "water" then
    -- 先量出"蓝图坐标 → 世界坐标"的位移，再按水泵真实落点造它要的那格水。
    local offset_x, offset_y = measure_offset(surface, force, job)
    terrain_setup.probe_offset = offset_x and { offset_x, offset_y } or nil

    -- 蓝图自己的占地留成陆地（用探出来的位移修正），外围铺水
    local min_x, min_y, max_x, max_y = nil, nil, nil, nil
    for _, blueprint_entity in ipairs(job.json.entities or {}) do
      local blueprint_position = blueprint_entity.position
      if blueprint_position ~= nil then
        min_x = math.min(min_x or blueprint_position.x, blueprint_position.x)
        max_x = math.max(max_x or blueprint_position.x, blueprint_position.x)
        min_y = math.min(min_y or blueprint_position.y, blueprint_position.y)
        max_y = math.max(max_y or blueprint_position.y, blueprint_position.y)
      end
    end
    if min_x ~= nil then
      local shift_x = offset_x or 0
      local shift_y = offset_y or 0
      local pond = {}
      for tile_x = math.floor(min_x + shift_x - 6), math.ceil(max_x + shift_x + 6) do
        for tile_y = math.floor(min_y + shift_y - 6), math.ceil(max_y + shift_y + 6) do
          local on_land = (tile_x >= min_x + shift_x - 1 and tile_x <= max_x + shift_x + 1
                           and tile_y >= min_y + shift_y - 1 and tile_y <= max_y + shift_y + 1)
          if not on_land then
            pond[#pond + 1] = { name = "water", position = { x = tile_x, y = tile_y } }
          end
        end
      end
      terrain_setup.pond_tiles = #pond
      terrain_setup.pond_ok = try(function() surface.set_tiles(pond) end)
      terrain_setup.land_bbox = { min_x + shift_x, min_y + shift_y, max_x + shift_x, max_y + shift_y }
    end

    -- 每台水泵再补一格它自己要的水（位置来自探针量出的世界坐标）
    local watered = 0
    for _, blueprint_entity in ipairs(job.json.entities or {}) do
      if blueprint_entity.name == "offshore-pump" and blueprint_entity.position ~= nil then
        local world_x = blueprint_entity.position.x + (offset_x or 0)
        local world_y = blueprint_entity.position.y + (offset_y or 0)
        local ok_temp, temp_pump = try(function()
          return surface.create_entity{ name = "offshore-pump", position = { x = world_x, y = world_y },
                                        direction = blueprint_entity.direction or 0, force = force }
        end)
        if ok_temp and temp_pump ~= nil then
          local ok_tile, source_tile = try(function() return temp_pump.get_fluid_source_tile() end)
          if ok_tile and source_tile ~= nil then
            if try(function() surface.set_tiles{ { name = "water", position = source_tile } } end) then
              watered = watered + 1
            end
          end
          try(function() temp_pump.destroy() end)
        end
      end
    end
    terrain_setup.pumps_watered = watered
  end
  res.terrain = terrain_setup


  local stack = game.create_inventory(1)[1]
  stack.clear()
  try(function() stack.set_stack{ name = "blueprint" } end)
  local import_ok, import_code = try(function() return stack.import_stack(job.blob) end)
  res.import_ok = import_ok
  res.import_code = import_ok and import_code or nil
  res.import_error = (not import_ok) and tostring(import_code) or nil
  if not import_ok then
    res.stage = "import_threw"
    return res
  end

  local parsed_ok, parsed = try(function() return stack.get_blueprint_entities() end)
  res.game_parsed_entities = (parsed_ok and type(parsed) == "table") and #parsed or nil
  res.game_parsed_names = {}
  if parsed_ok and type(parsed) == "table" then
    for _, entity in ipairs(parsed) do res.game_parsed_names[#res.game_parsed_names + 1] = entity.name end
  end
  local setup_ok, setup_value = try(function() return stack.is_blueprint_setup() end)
  res.is_blueprint_setup = setup_ok and setup_value or nil

  local build_ok, build_ret = try(function()
    return stack.build_blueprint{
      surface = surface, force = force, position = { x = 0, y = 0 },
      build_mode = defines.build_mode.superforced, skip_fog_of_war = true,
    }
  end)
  res.build_ok = build_ok
  res.build_error = (not build_ok) and tostring(build_ret) or nil
  res.build_returned = (build_ok and type(build_ret) == "table") and #build_ret or nil

  local ghosts = {}
  for _, ghost in pairs(surface.find_entities_filtered{ area = area, name = "entity-ghost" }) do
    local ghost_name, px, py = "?", 0, 0
    try(function() ghost_name = ghost.ghost_name end)
    try(function() px = ghost.position.x; py = ghost.position.y end)
    local can_place = try(function()
      return surface.can_place_entity{ name = ghost_name, position = { x = px, y = py }, force = force }
    end)
    local revive_ok, real = try(function()
      local _, entity = ghost.revive()
      return entity
    end)
    ghosts[#ghosts + 1] = {
      name = ghost_name, x = px, y = py,
      can_place = can_place and true or false,
      revived = (revive_ok and real ~= nil),
    }
  end

  if TERRAIN == "water" then
    local fed = 0
    for _, pump in pairs(surface.find_entities_filtered{ area = area, name = "offshore-pump" }) do
      local ok_tile, source_tile = try(function() return pump.get_fluid_source_tile() end)
      if ok_tile and source_tile ~= nil then
        local ok_set = try(function()
          surface.set_tiles{ { name = "water", position = source_tile } }
        end)
        if ok_set then fed = fed + 1 end
      end
    end
    res.terrain.pumps_watered = fed
  end

  if POWER_PROBE then
    res.power_setup = setup_power(surface, force, area)
  end
  res.recipes = apply_blueprint_recipes(surface, area, job)
  if FLUID_PROBE then
    res.fluid_source = setup_fluid_source(surface, force, area)
  end
  if RUN_TICKS > 0 then
    -- 夹具留到第一个 tick 再建：这时插入机的 pickup/drop 目标才有效，
    -- 否则算不出哪条带是"机器放货的出口带"，会把产物线当输入线来喂。
    res.needs_fixtures = true
  end
  res.ghost_detail = ghosts

  local tile_ghosts = {}
  for _, ghost in pairs(surface.find_entities_filtered{ area = area, name = "tile-ghost" }) do
    tile_ghosts[#tile_ghosts + 1] = ghost
  end
  local tiles_revived = 0
  for _, ghost in pairs(tile_ghosts) do
    local ok_revive = try(function() ghost.revive() end)
    if ok_revive then tiles_revived = tiles_revived + 1 end
  end
  res.tiles = { expected = res.blueprint_tiles, ghosts = #tile_ghosts, revived = tiles_revived }

  local snapshot_exclude = {}
  for unit in pairs((res.fixtures ~= nil and res.fixtures.units) or {}) do snapshot_exclude[unit] = true end
  for unit in pairs((res.power_setup ~= nil and res.power_setup.units) or {}) do snapshot_exclude[unit] = true end
  if res.fluid_source ~= nil and res.fluid_source.unit ~= nil then
    snapshot_exclude[res.fluid_source.unit] = true
  end
  res.after_build = snapshot(surface, area, snapshot_exclude)
  res.missing = find_missing(res.expected_names, res.after_build, force, surface)
  res.stage = "built"
  return res
end


-- 建完之后的邻接真相：不问我们自己的几何算法，问游戏。
-- 插入机取/放位上到底有没有东西用 pickup_target / drop_target；
-- 带子用 belt_neighbours + underground_belt_neighbour；
-- 管道/液口看 fluidbox_neighbours 与同一管段的 segment id。
local function adjacency_block(surface, area)
  local out = { entities = {}, inserters = {}, problems = {} }
  local function position_of(entity)
    local ok, value = try(function() return entity.position end)
    if ok and value ~= nil then return { x = value.x, y = value.y } end
    return nil
  end
  local function ref(entity)
    if entity == nil then return nil end
    local pos = position_of(entity)
    return { name = entity.name, unit = entity.unit_number, pos = pos }
  end

  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid and entity.type ~= "resource" then
      local ok_entity_status, entity_status = try(function() return entity.status end)
      local record = { status = status_name(ok_entity_status and entity_status or nil),
                       unit = entity.unit_number, name = entity.name, type = entity.type,
                       pos = position_of(entity), direction = select(2, try(function() return entity.direction end)) }
      local ok_recipe, recipe = try(function() return entity.get_recipe() end)
      if ok_recipe and recipe ~= nil then record.recipe = recipe.name end
      if entity.type == "inserter" then
        local ok_pickup, pickup = try(function() return entity.pickup_position end)
        local ok_drop, drop = try(function() return entity.drop_position end)
        local ok_pt, pickup_target = try(function() return entity.pickup_target end)
        local ok_dt, drop_target = try(function() return entity.drop_target end)
        record.pickup = (ok_pickup and pickup) and { x = pickup.x, y = pickup.y } or nil
        record.drop = (ok_drop and drop) and { x = drop.x, y = drop.y } or nil
        record.pickup_target = (ok_pt and pickup_target) and ref(pickup_target) or nil
        record.drop_target = (ok_dt and drop_target) and ref(drop_target) or nil
        out.inserters[#out.inserters + 1] = record
      elseif entity.type == "transport-belt" or entity.type == "underground-belt"
             or entity.type == "splitter" or entity.type == "loader" or entity.type == "linked-belt" then
        local ok_n, neighbours = try(function() return entity.belt_neighbours end)
        record.belt_inputs = 0
        record.belt_outputs = 0
        record.belt_input_names = {}
        record.belt_output_names = {}
        if ok_n and neighbours then
          for _, other in pairs(neighbours.inputs or {}) do
            record.belt_inputs = record.belt_inputs + 1
            record.belt_input_names[#record.belt_input_names + 1] = other.name
          end
          for _, other in pairs(neighbours.outputs or {}) do
            record.belt_outputs = record.belt_outputs + 1
            record.belt_output_names[#record.belt_output_names + 1] = other.name
          end
        end
        local ok_u, pair_entity = try(function() return entity.underground_belt_neighbour end)
        record.underground_pair = (ok_u and pair_entity ~= nil)
        local ok_g, ground_type = try(function() return entity.belt_to_ground_type end)
        record.belt_to_ground_type = ok_g and tostring(ground_type) or nil
      end


      if entity.type == "mining-drill" then
        local ok_area, mining_area = try(function() return entity.mining_area end)
        if ok_area and mining_area ~= nil then
          record.mining_area = { { mining_area.left_top.x, mining_area.left_top.y },
                                 { mining_area.right_bottom.x, mining_area.right_bottom.y } }
          local resources = surface.find_entities_filtered{ area = mining_area, type = "resource" }
          record.resources_in_area = #resources
          local names = {}
          for _, resource in pairs(resources) do
            names[tostring(resource.name)] = (names[tostring(resource.name)] or 0) + 1
          end
          record.resource_names = names
        end
        local ok_target, mining_target = try(function() return entity.mining_target end)
        record.mining_target = (ok_target and mining_target) and { name = mining_target.name } or nil
      end
      if entity.name == "offshore-pump" then
        local ok_tile, source_tile = try(function() return entity.get_fluid_source_tile() end)
        record.source_tile = (ok_tile and source_tile) and { x = source_tile.x, y = source_tile.y } or nil
        local ok_fluid, source_fluid = try(function() return entity.get_fluid_source_fluid() end)
        record.source_fluid = (ok_fluid and source_fluid) or nil
        local ok_contents, contents = try(function() return entity.get_fluid_contents() end)
        record.fluid_contents = (ok_contents and type(contents) == "table") and contents or {}
        -- 周边管子里的水：泵真的把水推出去，管子里才会有水（比"能落地"硬）
        local nearby = {}
        local pump_x, pump_y = tile_of(entity.position)
        for _, neighbour in pairs(surface.find_entities_filtered{ area = { { pump_x - 2, pump_y - 3 }, { pump_x + 2, pump_y + 3 } } }) do
          if neighbour.valid and neighbour.unit_number ~= entity.unit_number
             and (neighbour.type == "pipe" or neighbour.type == "pipe-to-ground" or neighbour.type == "storage-tank") then
            local ok_near, near_contents = try(function() return neighbour.get_fluid_contents() end)
            if ok_near and type(near_contents) == "table" then
              for name, count in pairs(near_contents) do
                nearby[tostring(name)] = (nearby[tostring(name)] or 0) + count
              end
            end
          end
        end
        record.nearby_pipe_fluids = nearby
      end

      local fluid_boxes = {}
      local ok_count, count = try(function() return entity.fluids_count end)
      local box_count = (ok_count and type(count) == "number") and count or 0
      for index = 1, box_count do
        local box = { index = index }
        local ok_nb, neighbours = try(function() return entity.get_fluid_box_neighbours(index) end)
        box.neighbours = (ok_nb and type(neighbours) == "table") and #neighbours or 0
        local ok_pc, connections = try(function() return entity.get_fluid_box_pipe_connections(index) end)
        box.expected_connections = (ok_pc and type(connections) == "table") and #connections or 0
        local ok_has, has = try(function() return entity.has_fluid_segment(index) end)
        if ok_has and has then
          box.segment = select(2, try(function() return entity.get_fluid_segment_id(index) end))
        end
        fluid_boxes[#fluid_boxes + 1] = box
      end
      record.fluid_boxes = fluid_boxes
      out.entities[#out.entities + 1] = record
    end
  end
  return out
end


script.on_init(function()
  R.env = env_block()
  R.stage = "on_init"
  local force = game.forces.player
  for index, job in ipairs(JOBS) do
    local ok_job, res = try(function() return stage_job(job, index, force) end)
    if not ok_job then res = { id = job.id, stage = "job_threw", error = tostring(res) } end
    R.jobs[index] = res
  end
  R.stage = "waiting"
end)

-- 采样每台机器的配方（空闲时 get_recipe() 返回 nil，所以记住最后见到的那个）
local function sample_recipes(surface, area, memory, inputs)
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid and (entity.type == "furnace" or entity.type == "assembling-machine") then
      if inputs ~= nil then
        local ok_inv, inventory = try(function() return entity.get_inventory(defines.inventory.crafter_input) end)
        if ok_inv and inventory ~= nil then
          local ok_contents, contents = try(function() return inventory.get_contents() end)
          if ok_contents and type(contents) == "table" then
            local total = 0
            for _, item in pairs(contents) do total = total + (item.count or 0) end
            local record = inputs[entity.unit_number] or { max_total = 0, last = nil }
            if total > record.max_total then record.max_total = total end
            record.last = contents
            inputs[entity.unit_number] = record
          end
        end
      end
      local ok_recipe, recipe = try(function() return entity.get_recipe() end)
      if ok_recipe and recipe ~= nil then
        local ok_energy, energy = try(function() return recipe.energy end)
        memory[entity.unit_number] = { name = recipe.name, energy = (ok_energy and energy) or nil }
      end
    end
  end
end

-- 夹具节拍：每 2 tick 往输入带灌一次（≈30 件/秒，远高于带子容量，保证喂饱），
-- 每 4 tick 清一次出口带并计数。
script.on_nth_tick(2, function()
  if R.stage ~= "waiting" or RUN_TICKS <= 0 then return end
  for index, job in ipairs(JOBS) do
    local res = R.jobs[index]
    if res.fixtures ~= nil then
      inject_belts(res.fixtures, game.tick)
      if game.tick % 4 == 0 then
        local job_surface = game.surfaces["bp-probe-" .. index]
        if job_surface ~= nil then
          local ok_drain, drain_error = try(function() return drain_outputs(res.fixtures, job_surface) end)
          if not ok_drain then res.fixtures.drain_error = tostring(drain_error) end
        end
      end
    end
  end
end)

script.on_nth_tick(10, function()
  if R.stage ~= "waiting" then return end
  if RUN_TICKS > 0 then
    for index, job in ipairs(JOBS) do
      local res = R.jobs[index]
      if res.needs_fixtures then
        local job_surface = game.surfaces["bp-probe-" .. index]
        if job_surface ~= nil then
          local ok_fix, fixtures_or_error = try(function()
            return setup_fixtures(job_surface, game.forces.player, res.area,
                                  (res.recipes ~= nil and res.recipes.declared) or nil, FEED_ITEM)
          end)
          if ok_fix then
            res.fixtures = fixtures_or_error
            feed_chests(res.fixtures)
            res.fixtures_setup_tick = game.tick
          else
            res.fixtures_error = tostring(fixtures_or_error)
          end
          res.needs_fixtures = nil
        end
      end
    end
  end
  if RUN_TICKS > 0 and game.tick % 10 == 0 then
    for index, job in ipairs(JOBS) do
      local res = R.jobs[index]
      local job_surface = game.surfaces["bp-probe-" .. index]
      if job_surface ~= nil and res.area ~= nil then
        res.recipe_memory = res.recipe_memory or {}
        res.machine_input = res.machine_input or {}
        pcall(function() sample_recipes(job_surface, res.area, res.recipe_memory, res.machine_input) end)
      end
    end
  end

  if RUN_TICKS > 0 and game.tick % 120 == 0 then
    for index, job in ipairs(JOBS) do
      local res = R.jobs[index]
      local job_surface = game.surfaces["bp-probe-" .. index]
      if job_surface ~= nil and res.area ~= nil then
        res.belt_samples = res.belt_samples or {}
        if #res.belt_samples < 900 then
          for _, belt in pairs(job_surface.find_entities_filtered{ area = res.area, type = "transport-belt" }) do
            if belt.valid then
              local lanes = {}
              for lane = 1, 2 do
                local ok_line, line = try(function() return belt.get_transport_line(lane) end)
                if ok_line and line ~= nil then
                  local ok_contents, contents = try(function() return line.get_contents() end)
                  if ok_contents and type(contents) == "table" and #contents > 0 then lanes[lane] = contents end
                end
              end
              if lanes[1] ~= nil or lanes[2] ~= nil then
                res.belt_samples[#res.belt_samples + 1] = {
                  tick = game.tick, x = math.floor(belt.position.x), y = math.floor(belt.position.y),
                  dir = belt.direction, lanes = lanes,
                }
              end
            end
          end
        end
      end
    end
  end
  if FLUID_PROBE and game.tick % 30 == 0 then
    for index, job in ipairs(JOBS) do
      local res = R.jobs[index]
      local job_surface = game.surfaces["bp-probe-" .. index]
      if job_surface ~= nil then
        pcall(function() refine_fluid_source(job_surface, res, res.recipe_memory or {}) end)
      end
    end
  end
  if RUN_TICKS > 0 and game.tick % 30 == 0 then
    for index, job in ipairs(JOBS) do
      local res = R.jobs[index]
      if res.fixtures ~= nil then feed_chests(res.fixtures) end
    end
  end
  if game.tick < TICK_LIMIT then return end
  R.stage = "collecting"
  for index, job in ipairs(JOBS) do
    local res = R.jobs[index]
    if res.area then
      local surface = game.surfaces["bp-probe-" .. index]
      if surface and surface.valid then
        res.after_ticks = snapshot(surface, res.area, (res.fixtures ~= nil and res.fixtures.units) or nil)
        res.adjacency = adjacency_block(surface, res.area)
        if FLUID_PROBE then
          res.fluids = fluid_report(surface, res.area,
                                    (res.fixtures ~= nil and res.fixtures.units) or nil)
          res.fluids.source = res.fluid_source
        end
        if POWER_PROBE then
          res.power = power_report(surface, res.area, (res.fixtures ~= nil and res.fixtures.units) or
                                   (res.power_setup ~= nil and res.power_setup.units) or nil)
        end
        if RUN_TICKS > 0 then
          res.belt_samples = res.belt_samples or {}
          for _, belt in pairs(surface.find_entities_filtered{ area = res.area, type = "transport-belt" }) do
            if belt.valid then
              local y = math.floor(belt.position.y)
              if true then
                local lanes = {}
                for lane = 1, 2 do
                  local ok_line, line = try(function() return belt.get_transport_line(lane) end)
                  if ok_line and line ~= nil then
                    local ok_contents, contents = try(function() return line.get_contents() end)
                    if ok_contents and type(contents) == "table" and #contents > 0 then lanes[lane] = contents end
                  end
                end
                if lanes[1] ~= nil or lanes[2] ~= nil then
                  res.belt_samples[#res.belt_samples + 1] = { tick = game.tick, x = math.floor(belt.position.x), y = y, lanes = lanes }
                end
              end
            end
          end
          local ok_prod, prod_or_error = try(function()
            return production_report(surface, res.area, res.recipe_memory or {},
                                     (res.recipes ~= nil and res.recipes.declared) or nil,
                                     res.machine_input or {},
                                     (res.recipes ~= nil and res.recipes.declared_by_name) or nil)
          end)
          res.production = ok_prod and prod_or_error or { machines = {}, total_products = 0, chests = {},
                                                          error = tostring(prod_or_error) }
          if res.fixtures ~= nil then
            local ok_diag, diag = try(function() return fixture_diagnostics(res.fixtures) end)
            res.production.fixture_diagnostics = ok_diag and diag or { error = tostring(diag) }
            res.production.drained = res.fixtures.drained
            res.production.drained_scope = res.fixtures.drain_scope
            res.production.injected_total = res.fixtures.injected_total
          end
        end
      end
    end
  end
  R.tick_end = game.tick
  R.stage = "writing"
  local ok_write, err_write = try(function()
    helpers.write_file("bp-probe/result.json", helpers.table_to_json(R))
  end)
  if not ok_write then
    R.write_error = tostring(err_write)
    note("write_file failed: " .. tostring(err_write))
  end
  R.stage = "done"
  note("done jobs=" .. #JOBS)
  script.finish_test(true)
end)
