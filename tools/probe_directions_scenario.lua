-- 方向档数实测场景。由 probe_directions.py 生成并运行。
-- 三条线同时测，按可信度排序：
--   ① blueprint_read：按编译器实际走的路——把 direction 写进单块蓝图、建出来、读回。
--      游戏自己会规整掉不支持的朝向，这是"游戏到底认不认"的硬证据。
--   ② create_read：create_entity 时给 direction 再读回（有些类型会无视这个参数）。
--   ③ assign_read：建好后写 entity.direction 再读回（只作对照）。

local ARENA_HALF = 48

local function try(fn, ...)
  local ok, a = pcall(fn, ...)
  if ok then return true, a end
  return false, tostring(a)
end

local function make_settings()
  local settings = game.default_map_gen_settings
  settings.seed = 4242
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

local function record(line, value)
  line.seen[#line.seen + 1] = value
  local key = tostring(value)
  line.counts[key] = (line.counts[key] or 0) + 1
end

local function wipe(surface, area)
  for _, entity in pairs(surface.find_entities_filtered{ area = area }) do
    if entity.valid then try(function() entity.destroy() end) end
  end
end

script.on_init(function()
  local out = { rows = {}, env = { active_mods = {}, feature_flags = {} } }
  for name, version in pairs(script.active_mods) do out.env.active_mods[name] = version end
  local ok_flags, flags = try(function() return script.feature_flags end)
  if ok_flags and type(flags) == "table" then
    for flag, enabled in pairs(flags) do out.env.feature_flags[flag] = enabled end
  end

  local surface = game.create_surface("dir-probe", make_settings())
  surface.request_to_generate_chunks({ x = 0, y = 0 }, 3)
  surface.force_generate_chunk_requests()
  wipe(surface, { { -ARENA_HALF, -ARENA_HALF }, { ARENA_HALF, ARENA_HALF } })

  local force = game.forces.player
  local probe_area = { { -8, -8 }, { 20, 20 } }
  local probe_origin = { x = 8, y = 8 }
  local stack = game.create_inventory(1)[1]

  -- 蓝图里实体的位置要落在半个格子上：偶数宽高用整数、奇数宽高用 .5。落错了连鬼影都建不出来
  -- （crusher 2x3 放在整数格上 → build_blueprint 返回 0）。
  local function placement_offset(prototype)
    local ok_box, box = try(function() return prototype.collision_box end)
    if not ok_box or box == nil then return 0, 0 end
    local width = math.floor(box.right_bottom.x - box.left_top.x + 0.5)
    local height = math.floor(box.right_bottom.y - box.left_top.y + 0.5)
    return (width % 2 == 0) and 0 or 0.5, (height % 2 == 0) and 0 or 0.5
  end

  -- 有些机器（foundry / cryogenic-plant 之类）的 direction 只有在实体带配方时才被保留：
  -- 不带 recipe 建出来会被规整成 0。挑一个配方还不够（不同配方表现可能不同），所以给几个候选轮着试。
  local function probe_recipes(prototype)
    local categories = prototype.crafting_categories
    if type(categories) ~= "table" then return {} end
    local visible, hidden = {}, {}
    for recipe_name, recipe in pairs(prototypes.recipe) do
      local recipe_categories = recipe.categories
      local fits = false
      if type(recipe_categories) == "table" then
        for key, value in pairs(recipe_categories) do
          local category = (type(value) == "string") and value or key
          if categories[category] then fits = true break end
        end
      end
      if fits then
        if recipe.hidden_from_player_crafting then hidden[#hidden + 1] = recipe_name
        else visible[#visible + 1] = recipe_name end
      end
    end
    table.sort(visible)
    table.sort(hidden)
    local out = {}
    for _, name in ipairs(visible) do if #out < 3 then out[#out + 1] = name end end
    for _, name in ipairs(hidden) do if #out < 3 then out[#out + 1] = name end end
    return out
  end

  for name, prototype in pairs(prototypes.entity) do
    local row = { name = name, type = prototype.type, placeable_items = {} }
    local items = prototype.items_to_place_this
    if type(items) == "table" then
      for _, item in ipairs(items) do row.placeable_items[#row.placeable_items + 1] = item.item or item.name end
    end
    row.placeable = #row.placeable_items > 0

    if row.placeable then
      local offset_x, offset_y = placement_offset(prototype)
      row.placement_offset = { offset_x, offset_y }
      local entity_position = { x = offset_x, y = offset_y }
      local build_position = { x = probe_origin.x + offset_x, y = probe_origin.y + offset_y }
      local recipes = probe_recipes(prototype)
      row.probe_recipes = recipes
      local recipe = recipes[1]
      local blueprint_line = { seen = {}, counts = {}, errors = {} }
      local create_line = { seen = {}, counts = {}, errors = {} }
      local assign_line = { seen = {}, counts = {}, errors = {} }

      -- ① 蓝图线：direction 写进蓝图再建，读回游戏真正保留的朝向。
      -- 一个配方测不出（比如该配方下朝向无意义）就换下一个候选配方，取档数最多的那次。
      local bare_line = { seen = {}, counts = {}, errors = {} }
      local candidate_count = math.max(#recipes, 0) + 1
      for candidate_index = 1, candidate_count do
        local candidate = (candidate_index == 1) and nil or recipes[candidate_index - 1]
        local candidate_line = { seen = {}, counts = {}, errors = {} }
        for direction = 0, 15 do
          stack.clear()
          try(function() stack.set_stack{ name = "blueprint" } end)
          local ok_set = try(function()
            stack.set_blueprint_entities{ { entity_number = 1, name = name, position = entity_position,
                                           direction = direction, recipe = candidate } }
          end)
          if ok_set then
            local ok_build, ret = try(function()
              return stack.build_blueprint{ surface = surface, force = force, position = build_position,
                                           build_mode = defines.build_mode.superforced, skip_fog_of_war = true }
            end)
            if ok_build and type(ret) == "table" and #ret > 0 then
              for _, ghost in pairs(surface.find_entities_filtered{ area = probe_area, name = "entity-ghost" }) do
                local ok_revive, real = try(function() local _, revived = ghost.revive(); return revived end)
                if ok_revive and real ~= nil then
                  local read_ok, read = try(function() return real.direction end)
                  if read_ok and read ~= nil then record(candidate_line, read) end
                  try(function() real.destroy() end)
                end
              end
            else
              candidate_line.errors[#candidate_line.errors + 1] = { direction = direction }
            end
          else
            break
          end
          wipe(surface, probe_area)
        end
        if candidate == nil then bare_line = candidate_line end
        local distinct = {}
        for _, value in pairs(candidate_line.seen) do distinct[value] = true end
        local count = 0
        for _ in pairs(distinct) do count = count + 1 end
        local best_distinct = {}
        for _, value in pairs(blueprint_line.seen) do best_distinct[value] = true end
        local best_count = 0
        for _ in pairs(best_distinct) do best_count = best_count + 1 end
        if count > best_count then
          blueprint_line = candidate_line
          row.probe_recipe = candidate
        end
      end
      row.bare_line = bare_line

      -- ② create 线
      for direction = 0, 15 do
        local ok, entity = pcall(function()
          return surface.create_entity{ name = name, position = build_position, direction = direction,
                                        force = force, recipe = recipe }
        end)
        if ok and entity ~= nil then
          local read_ok, read = try(function() return entity.direction end)
          if read_ok and read ~= nil then record(create_line, read) end
          try(function() entity.destroy() end)
        elseif #create_line.errors < 3 then
          create_line.errors[#create_line.errors + 1] = { direction = direction, error = tostring(entity) }
        end
        wipe(surface, probe_area)
      end

      -- ③ assign 线
      local ok_entity, entity = pcall(function()
        return surface.create_entity{ name = name, position = build_position, force = force }
      end)
      if ok_entity and entity ~= nil then
        for direction = 0, 15 do
          local ok_assign, assign_error = try(function() entity.direction = direction end)
          if not ok_assign and #assign_line.errors < 3 then
            assign_line.errors[#assign_line.errors + 1] = { direction = direction, error = tostring(assign_error) }
          end
          local read_ok, read = try(function() return entity.direction end)
          if read_ok and read ~= nil then record(assign_line, read) end
        end
        try(function() entity.destroy() end)
      end
      wipe(surface, probe_area)

      row.blueprint_line = blueprint_line
      row.create_line = create_line
      row.assign_line = assign_line
    end
    out.rows[#out.rows + 1] = row
  end

  stack.clear()
  helpers.write_file("bp-probe/result.json", helpers.table_to_json(out))
  log("[GT] directions done rows=" .. #out.rows)
  script.finish_test(true)
end)
