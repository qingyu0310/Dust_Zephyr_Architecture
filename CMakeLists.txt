# SPDX-License-Identifier: Apache-2.0

cmake_minimum_required(VERSION 3.20.0)

# 项目选择 — 通过 Kconfig CONFIG_PRJ_TEST=y 控制 apps/ 和 threads/ 的加载
set(ACTIVE_PRJ "test" CACHE STRING "Active project")
set(PROJ_DIR projects)
set(CONFIG_SYM PRJ_TEST)

# SDK glue 路径：HPMicro 板级/SOC/DTS 补丁
if(DEFINED ENV{SDK_GLUE_DIR})
  set(SDK_GLUE_DIR "$ENV{SDK_GLUE_DIR}")
else()
  set(SDK_GLUE_DIR "D:/Zephyr_HPMicro/sdk_glue")
endif()

# 注册额外 board/soc/dts 搜索路径
list(APPEND BOARD_ROOT "${SDK_GLUE_DIR}")
list(APPEND SOC_ROOT "${SDK_GLUE_DIR}")
list(APPEND DTS_ROOT "${SDK_GLUE_DIR}")
list(APPEND DTS_ROOT "${SDK_GLUE_DIR}/dts")

# 未指定 config 分组时，使用 board 名作为分组
if(NOT DEFINED BOARD_CFG)
  set(BOARD_CFG ${BOARD})
endif()

# 按 BOARD_CFG/BOARD 加载 board overlay（如 st/puzhong/stm32f4_disco.overlay）
file(GLOB OVERLAY_FILES ${CMAKE_CURRENT_SOURCE_DIR}/${PROJ_DIR}/boards/*/${BOARD_CFG}/${BOARD}.overlay)
if(OVERLAY_FILES)
  list(POP_FRONT OVERLAY_FILES DTC_OVERLAY_FILE)
endif()

# 按 BOARD_CFG/BOARD 加载板级 Kconfig 覆写
file(GLOB PRJ_CONF_FILES ${CMAKE_CURRENT_SOURCE_DIR}/${PROJ_DIR}/boards/*/${BOARD_CFG}/${BOARD}.conf)
if(PRJ_CONF_FILES)
  list(POP_FRONT PRJ_CONF_FILES EXTRA_CONF_FILE)
endif()

set(ZEPHYR_EXTRA_MODULES
  "${SDK_GLUE_DIR}"
  "D:/Zephyr_HPMicro/modules/lib/CherryUSB"
)
set(ZEPHYR_SDK_INSTALL_DIR "D:/Zephyr/zephyr-sdk-0.16.8" CACHE PATH "")

# 加载板级烧录脚本（flash.sh 等）
file(GLOB BOARD_CMAKE "${CMAKE_CURRENT_SOURCE_DIR}/${PROJ_DIR}/boards/*/${BOARD_CFG}/board.cmake")
if(BOARD_CMAKE)
  include(${BOARD_CMAKE})
endif()

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(my_project)

# 系统入口
target_sources(app PRIVATE src/main.c)
target_include_directories(app PRIVATE src)
target_compile_options(app PRIVATE $<$<COMPILE_LANGUAGE:CXX>:-fno-threadsafe-statics>)

# CMSIS 头路径（core_cm4.h 等）
if(CONFIG_CPU_CORTEX_M)
  zephyr_include_directories("${ZEPHYR_BASE}/../modules/hal/cmsis/CMSIS/Core/Include")
  zephyr_include_directories("${CMAKE_CURRENT_SOURCE_DIR}/include")
  zephyr_compile_definitions(__PROGRAM_START)
endif()

# 应用模块（仅在 CONFIG_PRJ_TEST=y 时编译）
if(CONFIG_${CONFIG_SYM})
  add_subdirectory(drivers)
  add_subdirectory(algorithm)
  add_subdirectory(modules)
  add_subdirectory(topic)
  add_subdirectory(cmd)
  add_subdirectory(${PROJ_DIR})
endif()
