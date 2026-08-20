#
# Copyright (c) 2022 Nordic Semiconductor
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
#
cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(peripheral_cgms)

target_sources(app PRIVATE
  src/main.c
  src/sim_config.c
  src/config_service.c
  src/model_thread.c
  src/comm_thread.c
  src/models/cgmsim_cambridge.c
  src/models/cgmsim_uva_padova.c
  src/models/cgmsim_royparker.c
  src/models/cgmsim_deichmann.c
  src/models/cgmsim_sensors.c
)

target_include_directories(app PRIVATE src src/models)
