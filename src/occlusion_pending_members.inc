  struct PendingOcclusionQuery {
    uint32_t address;
    xenos::xe_gpu_depth_sample_counts* counts;
    uint64_t scale;
    std::vector<uint32_t> indices;
  };
  std::vector<PendingOcclusionQuery> aot_pending_occlusion_;
  size_t aot_pending_occlusion_draws_ = 0;
  bool FlushOcclusionQueries();
  void FlushOcclusionQueriesForMemory(const void* address, size_t length);
  void PrepareForWait() override;
