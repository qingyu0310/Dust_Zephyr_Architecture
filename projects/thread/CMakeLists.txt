if(CONFIG_TRD_GPIO)
	target_sources(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/gpio/trd_gpio.cpp)
	target_include_directories(app PRIVATE
		${CMAKE_CURRENT_SOURCE_DIR}
		${CMAKE_CURRENT_SOURCE_DIR}/gpio
	)
endif()

if(CONFIG_TRD_CHASSIS)
	target_sources(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/chassis/trd_chassis.cpp)
	target_include_directories(app PRIVATE
		${CMAKE_CURRENT_SOURCE_DIR}
		${CMAKE_CURRENT_SOURCE_DIR}/chassis
	)
endif()

if(CONFIG_TRD_GIMBAL)
	target_sources(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/gimbal/trd_gimbal.cpp)
	target_include_directories(app PRIVATE
		${CMAKE_CURRENT_SOURCE_DIR}
		${CMAKE_CURRENT_SOURCE_DIR}/gimbal
	)
endif()

if(CONFIG_TRD_CAN_TX)
	target_sources(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/can/trd_can_tx.cpp)
	target_include_directories(app PRIVATE
		${CMAKE_CURRENT_SOURCE_DIR}
		${CMAKE_CURRENT_SOURCE_DIR}/can
	)
endif()

if(CONFIG_TRD_TFLM)
	target_sources(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tflm/trd_tflm.cpp)
	target_include_directories(app PRIVATE
		${CMAKE_CURRENT_SOURCE_DIR}
		${CMAKE_CURRENT_SOURCE_DIR}/tflm
	)
endif()
