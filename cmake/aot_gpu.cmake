# Build only the GPU plugin against the installed, matching runtime ABI.
set(AOT_GPU_SOURCE "${CMAKE_SOURCE_DIR}/.tools/rexglue-src" CACHE PATH "Pinned ReXGlue source checkout")
execute_process(COMMAND git -C "${AOT_GPU_SOURCE}" rev-parse HEAD OUTPUT_VARIABLE _gpu_revision OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
if(NOT _gpu_revision STREQUAL "f5337cdc947ff6d4c4196737e2c807a48f2a1fc2")
    message(FATAL_ERROR "AOT_GPU_SOURCE must be ReXGlue SDK commit f5337cdc947ff6d4c4196737e2c807a48f2a1fc2")
endif()
execute_process(COMMAND git -C "${AOT_GPU_SOURCE}" diff HEAD --exit-code --quiet
    RESULT_VARIABLE _gpu_source_modified)
if(NOT _gpu_source_modified EQUAL 0)
    message(FATAL_ERROR "AOT_GPU_SOURCE has modified tracked files; use a clean pinned checkout")
endif()
find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(_gpu_patched "${CMAKE_BINARY_DIR}/gpu-test/command_processor.cpp")
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/prepare_gpu_patch.py"
    "${AOT_GPU_SOURCE}" "${_gpu_patched}" COMMAND_ERROR_IS_FATAL ANY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tools/prepare_gpu_patch.py")
set(_gpu_wait_patched "${CMAKE_BINARY_DIR}/gpu-test/packet_processor.cpp")
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/prepare_gpu_wait_patch.py"
    "${AOT_GPU_SOURCE}" "${_gpu_wait_patched}" COMMAND_ERROR_IS_FATAL ANY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tools/prepare_gpu_wait_patch.py")
set(_gpu_sources
    plugin_main.cpp flags.cpp register_file.cpp registers.cpp
    pipeline/shader/shader.cpp format/ucode.cpp sampler_info.cpp
    util/draw_extent_estimator.cpp
    util/draw.cpp packet_disassembler.cpp primitive_processor.cpp
    pipeline/render_target/cache.cpp shared_memory.cpp pipeline/texture/cache.cpp
    pipeline/shader/interpreter.cpp pipeline/shader/translator.cpp
    pipeline/shader/translator_disasm.cpp
    pipeline/shader/dxbc.cpp pipeline/shader/dxbc_translator.cpp
    pipeline/shader/dxbc_translator_alu.cpp pipeline/shader/dxbc_translator_fetch.cpp
    pipeline/shader/dxbc_translator_memexport.cpp pipeline/shader/dxbc_translator_om.cpp
    d3d12/graphics_system.cpp d3d12/primitive_processor.cpp d3d12/render_target_cache.cpp
    d3d12/shader.cpp d3d12/texture_cache.cpp
    d3d12/deferred_command_list.cpp d3d12/pipeline_cache.cpp)
list(TRANSFORM _gpu_sources PREPEND "${AOT_GPU_SOURCE}/src/graphics/")
add_library(aot_gpu_xenos SHARED EXCLUDE_FROM_ALL ${_gpu_sources} "${_gpu_patched}"
    "${_gpu_wait_patched}"
    "${CMAKE_BINARY_DIR}/gpu-test/graphics_system.cpp"
    "${CMAKE_BINARY_DIR}/gpu-test/shared_memory.cpp"
    "${AOT_GPU_SOURCE}/thirdparty/dxbc/DXBCChecksum.cpp")
set_source_files_properties("${_gpu_wait_patched}" "${_gpu_patched}" "${CMAKE_BINARY_DIR}/gpu-test/graphics_system.cpp"
    PROPERTIES INCLUDE_DIRECTORIES "${CMAKE_SOURCE_DIR}")
target_include_directories(aot_gpu_xenos BEFORE PRIVATE "${CMAKE_BINARY_DIR}/gpu-test/include"
    "${AOT_GPU_SOURCE}" "${AOT_GPU_SOURCE}/thirdparty/renderdoc"
    "${AOT_GPU_SOURCE}/src/graphics/d3d12")
target_compile_definitions(aot_gpu_xenos PRIVATE REX_HAS_VULKAN=0 REXGLUE_BUILD_CONFIG="$<CONFIG>")
target_link_libraries(aot_gpu_xenos PRIVATE rex::runtime rex::xxhash d3d12 dxgi dxguid)
rexglue_apply_target_settings(aot_gpu_xenos)
set_target_properties(aot_gpu_xenos PROPERTIES OUTPUT_NAME rexgpu-aot
    DEBUG_POSTFIX d RELWITHDEBINFO_POSTFIX rd RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/gpu-test")

# Read-only shader analysis using the exact same decoder as the pinned GPU.
add_executable(aot_shader_disasm EXCLUDE_FROM_ALL
    "${CMAKE_SOURCE_DIR}/tools/shader_disasm.cpp"
    "${AOT_GPU_SOURCE}/src/graphics/pipeline/shader/shader.cpp"
    "${AOT_GPU_SOURCE}/src/graphics/pipeline/shader/translator.cpp"
    "${AOT_GPU_SOURCE}/src/graphics/pipeline/shader/translator_disasm.cpp"
    "${AOT_GPU_SOURCE}/src/graphics/format/ucode.cpp")
target_link_libraries(aot_shader_disasm PRIVATE rex::runtime rex::xxhash)
rexglue_apply_target_settings(aot_shader_disasm)
