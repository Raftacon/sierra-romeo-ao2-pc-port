// Host memory consumers and CPU-visible completion notifications must observe
// all earlier query results. In particular, short WAIT_REG_MEM loops otherwise
// skip PrepareForWait entirely and could spin on a permanently pending result.
switch (opcode) {
  case PM4_WAIT_REG_MEM:
  case PM4_WAIT_FOR_IDLE:
  case PM4_REG_TO_MEM:
  case PM4_MEM_WRITE:
  case PM4_COND_WRITE:
  case PM4_EVENT_WRITE:
  case PM4_EVENT_WRITE_SHD:
  case PM4_EVENT_WRITE_EXT:
  case PM4_INTERRUPT:
  case PM4_XE_SWAP:
  case PM4_INDIRECT_BUFFER:
  case PM4_INDIRECT_BUFFER_PFD:
  case PM4_LOAD_ALU_CONSTANT:
    {
      aot::OcclusionBarrierScope reason(opcode);
      PrepareForWait();
    }
    break;
  default:
    break;
}
