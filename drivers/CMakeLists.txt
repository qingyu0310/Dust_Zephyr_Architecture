if(CONFIG_DEV_GPIO)
	target_include_directories(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/device/gpio)
endif()

if(CONFIG_COM_UART OR CONFIG_COM_UART_DMA)
	target_sources(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/communication/uart/uart.cpp)
	target_include_directories(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/communication/uart)
endif()

if(CONFIG_COM_CAN)
	target_include_directories(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/communication/can)
endif()

if(CONFIG_COM_RS485)
	target_sources(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/communication/rs485/rs485.cpp)
	target_include_directories(app PRIVATE
		${CMAKE_CURRENT_SOURCE_DIR}/communication/rs485
		${CMAKE_CURRENT_SOURCE_DIR}/communication/uart
	)
endif()

