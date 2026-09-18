# Pinned GPU sources with spatial output, diagnostics, texture-plane and queue-wait corrections.
set(AOT_GPU_SOURCE "${CMAKE_SOURCE_DIR}/.tools/rexglue-src" CACHE PATH "Pinned ReXGlue source checkout")
execute_process(COMMAND git -C "${AOT_GPU_SOURCE}" rev-parse HEAD OUTPUT_VARIABLE _spatial_revision OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
execute_process(COMMAND git -C "${AOT_GPU_SOURCE}" diff HEAD --exit-code --quiet RESULT_VARIABLE _spatial_modified)
if(NOT _spatial_revision STREQUAL "f5337cdc947ff6d4c4196737e2c807a48f2a1fc2" OR NOT _spatial_modified EQUAL 0)
    message(FATAL_ERROR "Spatial GPU requires clean pinned ReXGlue f5337cd source")
endif()
find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(_spatial_root "${CMAKE_BINARY_DIR}/spatial-gpu")
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/prepare_spatial_upscale.py" "${AOT_GPU_SOURCE}" "${_spatial_root}" COMMAND_ERROR_IS_FATAL ANY)
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/prepare_texture_planes.py" "${AOT_GPU_SOURCE}" "${_spatial_root}" COMMAND_ERROR_IS_FATAL ANY)
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/prepare_submission_fence.py" "${AOT_GPU_SOURCE}" "${_spatial_root}" COMMAND_ERROR_IS_FATAL ANY)
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/prepare_occlusion_draws.py" "${AOT_GPU_SOURCE}" "${_spatial_root}" COMMAND_ERROR_IS_FATAL ANY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tools/prepare_occlusion_draws.py")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/src/occlusion_pending_members.inc")
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/prepare_command_submission.py" "${AOT_GPU_SOURCE}" "${_spatial_root}" COMMAND_ERROR_IS_FATAL ANY)
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/prepare_readback_retirement.py" "${AOT_GPU_SOURCE}" "${_spatial_root}" COMMAND_ERROR_IS_FATAL ANY)
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/prepare_completed_readback.py" "${AOT_GPU_SOURCE}" "${_spatial_root}" COMMAND_ERROR_IS_FATAL ANY)
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/prepare_projection_precision.py" "${AOT_GPU_SOURCE}" "${_spatial_root}" COMMAND_ERROR_IS_FATAL ANY)
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/prepare_zero_stencil.py" "${AOT_GPU_SOURCE}" "${_spatial_root}" COMMAND_ERROR_IS_FATAL ANY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tools/prepare_zero_stencil.py")
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/prepare_gpu_wait_patch.py" "${AOT_GPU_SOURCE}" "${_spatial_root}/base_command_processor.cpp" --trace-only COMMAND_ERROR_IS_FATAL ANY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tools/prepare_gpu_wait_patch.py")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tools/prepare_projection_precision.py" "${CMAKE_SOURCE_DIR}/tools/equipment_projection_variants.py" "${CMAKE_SOURCE_DIR}/tools/projection_fma_snippet.py")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tools/prepare_command_submission.py")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tools/prepare_readback_retirement.py")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tools/prepare_completed_readback.py")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tools/prepare_submission_fence.py")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tools/prepare_spatial_upscale.py" "${CMAKE_SOURCE_DIR}/tools/prepare_texture_planes.py")
set(_spatial_sources
    plugin_main.cpp flags.cpp register_file.cpp registers.cpp
    pipeline/shader/shader.cpp format/ucode.cpp sampler_info.cpp util/draw_extent_estimator.cpp
    util/draw.cpp packet_disassembler.cpp primitive_processor.cpp pipeline/render_target/cache.cpp
    shared_memory.cpp pipeline/shader/interpreter.cpp
    pipeline/shader/translator_disasm.cpp pipeline/shader/dxbc.cpp
    pipeline/shader/dxbc_translator_fetch.cpp pipeline/shader/dxbc_translator_memexport.cpp
    pipeline/shader/dxbc_translator_om.cpp d3d12/graphics_system.cpp d3d12/primitive_processor.cpp
    d3d12/shader.cpp d3d12/texture_cache.cpp d3d12/shared_memory.cpp
    d3d12/deferred_command_list.cpp)
list(TRANSFORM _spatial_sources PREPEND "${AOT_GPU_SOURCE}/src/graphics/")
add_library(aot_gpu_spatial SHARED ${_spatial_sources} "${_spatial_root}/command_processor.cpp" "${_spatial_root}/texture_cache.cpp"
    "${_spatial_root}/render_target_cache.cpp"
    "${_spatial_root}/base_command_processor.cpp" "${_spatial_root}/graphics_system.cpp"
    "${_spatial_root}/translator.cpp" "${_spatial_root}/pipeline_cache.cpp"
    "${_spatial_root}/dxbc_translator.cpp"
    "${_spatial_root}/dxbc_translator_alu.cpp"
    "${CMAKE_SOURCE_DIR}/src/projection_precision.cpp" "${CMAKE_SOURCE_DIR}/src/projection_precision_runtime.cpp"
    "${CMAKE_SOURCE_DIR}/src/gpu_vblank_wake.cpp"
    "${CMAKE_SOURCE_DIR}/src/spatial_upscaler.cpp" "${AOT_GPU_SOURCE}/thirdparty/dxbc/DXBCChecksum.cpp")
target_include_directories(aot_gpu_spatial BEFORE PRIVATE "${_spatial_root}/projection-include" "${_spatial_root}/include" "${_spatial_root}"
    "${CMAKE_SOURCE_DIR}" "${AOT_GPU_SOURCE}" "${AOT_GPU_SOURCE}/src"
    "${AOT_GPU_SOURCE}/thirdparty/renderdoc" "${AOT_GPU_SOURCE}/src/graphics/d3d12")
target_compile_definitions(aot_gpu_spatial PRIVATE REX_HAS_VULKAN=0 REXGLUE_BUILD_CONFIG="$<CONFIG>")
target_link_libraries(aot_gpu_spatial PRIVATE rex::runtime rex::xxhash d3d12 dxgi dxguid synchronization)
rexglue_apply_target_settings(aot_gpu_spatial)
set_target_properties(aot_gpu_spatial PROPERTIES OUTPUT_NAME rexgpu-spatial DEBUG_POSTFIX d RELWITHDEBINFO_POSTFIX rd)
