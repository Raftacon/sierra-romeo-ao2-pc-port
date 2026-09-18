// Included as D3D12CommandProcessor method definitions in the staged renderer.
// A guest interval may cross submissions and include internal RT-transfer draws.
// Only actual guest draws contribute, each with a complete host query pair.
void D3D12CommandProcessor::DisableHostOcclusionQueries() {
  PrepareForWait();
  aot::TraceOcclusion("disable", frame_current_, active_occlusion_query_.sample_count_address,
      active_occlusion_query_.valid, occlusion_query_resources_available_, active_occlusion_query_.host_index);
  active_occlusion_query_ = {};
  aot_occlusion_draw_indices_.clear();
  occlusion_query_cursor_ = 0;
  occlusion_query_resources_available_ = false;
}

bool D3D12CommandProcessor::BeginGuestOcclusionQuery(uint32_t address) {
  if (!REXCVAR_GET(occlusion_query_enable) || !occlusion_query_resources_available_) return false;
  if (active_occlusion_query_.valid) {
    DisableHostOcclusionQueries();
    return false;
  }
  // Finish an older owner before initializing either half of a reused pair.
  for (const auto& pending : aot_pending_occlusion_) {
    if (address == pending.address || uint64_t(address) == uint64_t(pending.address) + 32 ||
        uint64_t(address) + 32 == pending.address) {
      PrepareForWait();
      break;
    }
  }
  aot_occlusion_draw_indices_.clear();
  active_occlusion_query_.sample_count_address = address;
  active_occlusion_query_.valid = true;
  return true;
}

uint32_t D3D12CommandProcessor::BeginOcclusionQueryDraw() {
  if (!active_occlusion_query_.valid) return UINT32_MAX;
  // Retire completed guest intervals before the heap can wrap over any of
  // their indices. Current-interval indices remain reserved across the flush.
  if (aot_pending_occlusion_draws_ + aot_occlusion_draw_indices_.size() >= kMaxOcclusionQueries) {
    PrepareForWait();
  }
  if (aot_occlusion_draw_indices_.size() >= kMaxOcclusionQueries) {
    DisableHostOcclusionQueries();
    return UINT32_MAX;
  }
  uint32_t index;
  if (!AcquireOcclusionQueryIndex(index)) {
    DisableHostOcclusionQueries();
    return UINT32_MAX;
  }
  active_occlusion_query_.host_index = index;
  aot_occlusion_draw_indices_.push_back(index);
  deferred_command_list_.D3DBeginQuery(occlusion_query_heap_.Get(), D3D12_QUERY_TYPE_OCCLUSION, index);
  return index;
}

void D3D12CommandProcessor::EndOcclusionQueryDraw(uint32_t index) {
  if (index == UINT32_MAX) return;
  deferred_command_list_.D3DEndQuery(occlusion_query_heap_.Get(), D3D12_QUERY_TYPE_OCCLUSION, index);
  deferred_command_list_.D3DResolveQueryData(occlusion_query_heap_.Get(), D3D12_QUERY_TYPE_OCCLUSION,
      index, 1, occlusion_query_readback_.Get(), sizeof(uint64_t) * index);
}

bool D3D12CommandProcessor::EndGuestOcclusionQuery(
    uint32_t address, xenos::xe_gpu_depth_sample_counts* counts) {
  if (!REXCVAR_GET(occlusion_query_enable) || !occlusion_query_resources_available_ ||
      !active_occlusion_query_.valid || !occlusion_query_heap_ || !occlusion_query_readback_) return false;
  active_occlusion_query_ = {};
  if (aot_occlusion_draw_indices_.empty()) {
    WriteGuestOcclusionResult(counts, 0);
    aot::TraceOcclusion("result", frame_current_, address, false,
        occlusion_query_resources_available_, UINT32_MAX, 0, 0);
    return true;
  }
  const uint64_t scale = texture_cache_ ? uint64_t(texture_cache_->draw_resolution_scale_x()) *
                                         texture_cache_->draw_resolution_scale_y() : 1;
  aot_pending_occlusion_.push_back({address, counts, std::max(uint64_t(1), scale), {}});
  aot_pending_occlusion_.back().indices.swap(aot_occlusion_draw_indices_);
  aot_pending_occlusion_draws_ += aot_pending_occlusion_.back().indices.size();
  // Preserve the guest's pending markers until results are actually available.
  return REXCVAR_GET(aot_batch_occlusion_queries) || FlushOcclusionQueries();
}

bool D3D12CommandProcessor::FlushOcclusionQueries() {
  if (aot_pending_occlusion_.empty()) return true;
  if (!EndSubmission(false)) return false;
  const uint64_t submission = submission_current_ ? submission_current_ - 1 : 0;
  if (!CheckSubmissionFence(submission) || submission_completed_ < submission ||
      !occlusion_query_readback_mapping_) return false;
  aot::TraceOcclusion("batch_wait", frame_current_, 0, active_occlusion_query_.valid,
      occlusion_query_resources_available_, aot::occlusion_flush_reason,
      aot_pending_occlusion_.size(), aot_pending_occlusion_draws_);
  for (const auto& pending : aot_pending_occlusion_) {
    uint64_t samples = 0;
    for (uint32_t index : pending.indices) {
      const uint64_t draw_samples = occlusion_query_readback_mapping_[index];
      samples = draw_samples > UINT64_MAX - samples ? UINT64_MAX : samples + draw_samples;
    }
    aot::TraceOcclusion("result", frame_current_, pending.address, false,
        occlusion_query_resources_available_, pending.indices.back(), samples, pending.indices.size());
    // Equivalent rounding to NormalizeOcclusionSamples, without overflow and
    // using the scale captured when this guest interval ended.
    const uint64_t normalized = samples / pending.scale +
        (samples % pending.scale + (pending.scale >> 1)) / pending.scale;
    WriteGuestOcclusionResult(pending.counts, normalized);
  }
  if (aot_occlusion_draw_indices_.empty()) {
    aot_occlusion_draw_indices_.swap(aot_pending_occlusion_.back().indices);
    aot_occlusion_draw_indices_.clear();
  }
  aot_pending_occlusion_.clear();
  aot_pending_occlusion_draws_ = 0;
  return true;
}

void D3D12CommandProcessor::FlushOcclusionQueriesForMemory(const void* address, size_t length) {
  if (!length) return;
  const uintptr_t first = reinterpret_cast<uintptr_t>(address);
  for (const auto& pending : aot_pending_occlusion_) {
    const uintptr_t result = reinterpret_cast<uintptr_t>(pending.counts);
    // Difference comparisons avoid overflow when a range ends at UINTPTR_MAX.
    if (first <= result ? result - first < length : first - result < sizeof(*pending.counts)) {
      PrepareForWait();
      return;
    }
  }
}

void D3D12CommandProcessor::PrepareForWait() {
  if (!FlushOcclusionQueries()) {
    rex::FatalError("Occlusion query batch could not complete; refusing to publish unfinished results");
  }
}
