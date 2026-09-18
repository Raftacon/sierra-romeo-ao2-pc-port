// Runs on the scene client's game thread. Modify only a live checkpoint panel;
// values, ownership, visibility and fade timers stay with the original scene.
{
  static uint32_t last_checkpoint_panel = 0;
  uint32_t checkpoint_panel = 0;
  for (uint32_t i = 0; i < count; ++i) {
    const auto scene = read(scenes + i * 4);
    if (Readable(memory, scene, 0x22C) && read(scene) == 0x82044A60)
      checkpoint_panel = scene;
  }
  if (!checkpoint_panel) { last_checkpoint_panel = 0; checkpoint_root_image = 0; }
  if (checkpoint_panel && checkpoint_panel != last_checkpoint_panel) {
    const auto font_name = name("hud_action_font_z");
    const auto template_name = name("CheckpointInfo_UIScene");
    uint32_t readable_font = 0, template_scene = 0;
    if (object_count <= 300000 && Readable(memory, objects, object_count * 4)) {
      for (uint32_t i = 0; i < object_count; ++i) {
        const auto object = read(objects + i * 4);
        if (object &&
            *memory->TranslateVirtual<rex::be<uint64_t>*>(object + 0x2C) == font_name &&
            Readable(memory, object, 0x70)) {
          readable_font = object;
        }
        if (object && read(object) == 0x82044A60 &&
            *memory->TranslateVirtual<rex::be<uint64_t>*>(object + 0x2C) == template_name)
          template_scene = object;
      }
    }
    if (readable_font && Readable(memory, template_scene, 0x180)) {
      std::vector<uint32_t> templates{template_scene};
      for (size_t index = 0; index < templates.size() && index < 128; ++index) {
        const auto node = templates[index];
        if (!Readable(memory, node, 0x6C)) continue;
        const auto children = read(node + 0x60), size = read(node + 0x64);
        if (size <= 64 && templates.size() + size <= 128 && Readable(memory, children, size * 4))
          for (uint32_t i = 0; i < size; ++i) templates.push_back(read(children + i * 4));
      }
      auto call_ui = [&](uint32_t receiver, const char* method, auto parameters) {
        const auto id = name(method);
        frame.ctx.r3.u64 = receiver; frame.ctx.r4.u64 = id; frame.ctx.r5.u64 = 0;
        sub_824621B8(frame.ctx, base);
        const auto function = frame.ctx.r3.u32;
        auto* invoke = runtime->function_dispatcher()->GetFunction(read(read(receiver) + 0xE4));
        if (!function || !invoke) {
          REXLOG_WARN("Checkpoint UI method unavailable: method={}, receiver={:08X}, function={:08X}, target={:08X}",
                      method, receiver, function, read(read(receiver) + 0xE4));
          return false;
        }
        std::memset(base + sp + 0x90, 0, 16);
        parameters();
        frame.ctx.r3.u64 = receiver; frame.ctx.r4.u64 = function;
        frame.ctx.r5.u64 = sp + 0x90; frame.ctx.r6.u64 = 0; frame.ctx.r7.s64 = -7;
        invoke(frame.ctx, base);
        return true;
      };
      std::vector<uint32_t> widgets{checkpoint_panel};
      unsigned labels = 0;
      for (size_t index = 0; index < widgets.size() && index < 128; ++index) {
        const auto widget = widgets[index];
        if (!Readable(memory, widget, 0x180)) continue;
        const auto children = read(widget + 0x60), size = read(widget + 0x64);
        if (size <= 64 && widgets.size() + size <= 128 && Readable(memory, children, size * 4))
          for (uint32_t i = 0; i < size; ++i) widgets.push_back(read(children + i * 4));
        const auto type = read(widget);
        if (type != 0x820D9CC0 && type != 0x82049F90) continue; // UIImage / UILabel
        uint32_t archetype = 0;
        const auto widget_name = static_cast<uint64_t>(*memory->TranslateVirtual<rex::be<uint64_t>*>(widget + 0x2C));
        for (const auto candidate : templates) {
          if (Readable(memory, candidate, 0x50) && read(candidate) == type &&
              *memory->TranslateVirtual<rex::be<uint64_t>*>(candidate + 0x2C) == widget_name) {
            archetype = candidate; break;
          }
        }
        if (!Readable(memory, archetype, 0x50) || read(archetype) != type) continue;
        // Use the immutable archetype, so reactivation never scales twice.
        const bool panel_root = Readable(memory, read(widget + 0x28), 4) &&
            read(read(widget + 0x28)) == 0x820D9090;
        if (panel_root && type == 0x820D9CC0) checkpoint_root_image = widget;
        for (unsigned face = 0; face < 4; ++face) {
          const uint8_t evaluation = base[archetype + 0x4C + face];
          float value = *memory->TranslateVirtual<rex::be<float>*>(archetype + 0x3C + 4 * face);
          if (!std::isfinite(value)) continue;
          if (evaluation == 3 && !(panel_root && face == 0)) value *= 1.30f;
          // Keep the enlarged table above the native objective list as well.
          else if (panel_root && face == 1 && evaluation == 5) value = .17f;
          else continue;
          // The heading's ink backing needs clearance above the rounded panel.
          if (type == 0x82049F90 && widget_name == (name("UILabel") | 3) && face == 1)
            value -= 8.f;
          call_ui(widget, "SetPosition", [&] {
            *memory->TranslateVirtual<rex::be<float>*>(sp + 0x90) = value;
            base[sp + 0x94] = uint8_t(face); base[sp + 0x95] = evaluation;
          });
        }
        if (type == 0x820D9CC0 && Readable(memory, widget, 0x364)) {
          // Replace the interior only. Retain the original edge/corner artwork;
          // the canvas hook clips translucent texels outside the corner curve.
          const auto instance = static_cast<uint32_t>(widget_name);
          const auto renderer = read(widget + 0x360);
          if (Readable(memory, renderer, 0x110))
            call_ui(renderer, "SetOpacity", [&] {
              *memory->TranslateVirtual<rex::be<float>*>(sp + 0x90) =
                  instance == 5 || instance == 2 ? 0.f : 1.f;
            });
        }
        if (type == 0x82049F90 && Readable(memory, widget, 0x368)) {
          const auto renderer = read(widget + 0x364);
          if (Readable(memory, renderer, 0x110) && read(renderer) == 0x820E6A98 &&
              call_ui(renderer, "SetFont", [&] { write(sp + 0x90, readable_font); })) ++labels;
        }
      }
      if (labels == 9) {
        last_checkpoint_panel = checkpoint_panel;
        REXLOG_INFO("Checkpoint table readability layout: scene={:08X}, labels={}", checkpoint_panel, labels);
      }
    }
  }
}
