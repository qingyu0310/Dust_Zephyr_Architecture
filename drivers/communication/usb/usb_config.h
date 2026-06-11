/*
 * Copyright (c) 2022, sakumisu
 * Copyright (c) 2022-2025, HPMicro
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * CherryUSB 平台配置头文件。
 * 通过 Kconfig 的 CONFIG_CHERRYUSB_DEVICE_* 选择后端，支持多平台。
 * 添加新平台时：在对应 #elif 块中补充头文件、地址宏、EHCI 参数。
 */
#ifndef CHERRYUSB_CONFIG_H
#define CHERRYUSB_CONFIG_H

// =================== 平台头文件 ===================

// HPMicro 平台：提供 cacheline 大小、寄存器基址、地址转换
#ifdef CONFIG_CHERRYUSB_DEVICE_HPM
#include "hpm_soc.h"
#include "hpm_l1c_drv.h"
#endif

// =============== USB common Configuration ===============

#define CONFIG_USB_PRINTF(...) printf(__VA_ARGS__)

#ifndef CONFIG_USB_DBG_LEVEL
#define CONFIG_USB_DBG_LEVEL USB_DBG_INFO
#endif

// 速度选择：强制 FS 则取消 CONFIG_USB_HS
#if defined(CONFIG_USB_DEVICE_FS) || defined(CONFIG_USB_DEVICE_FORCE_FULL_SPEED)
#undef CONFIG_USB_HS
#else
#define CONFIG_USB_HS
#endif

#define CONFIG_USB_PRINTF_COLOR_ENABLE
#define CONFIG_USB_DCACHE_ENABLE

// DMA 与 cache 行对齐大小
#ifdef CONFIG_USB_DCACHE_ENABLE
#ifdef CONFIG_CHERRYUSB_DEVICE_HPM
#define CONFIG_USB_ALIGN_SIZE HPM_L1C_CACHELINE_SIZE  // HPM: 由硬件 cacheline 决定
#else
#define CONFIG_USB_ALIGN_SIZE 32   // 默认 32 字节对齐
#endif
#else
#define CONFIG_USB_ALIGN_SIZE 4    // 无 cache 时 4 字节对齐
#endif

// USB 设备描述符：VID/PID 也可通过 Kconfig 覆写
#define USBD_VID           0x34B7
#define USBD_PID           0xFFFF
#define USBD_MAX_POWER     200

// nocache 内存段：DMA 缓冲必须放在此处（HPM 要求，其他平台一般为空）
#ifdef CONFIG_CHERRYUSB_DEVICE_HPM
#define USB_NOCACHE_RAM_SECTION __attribute__((section(".nocache")))
#else
#define USB_NOCACHE_RAM_SECTION
#endif

// ============= USB Device Stack Configuration =============

// EP0 控制传输缓冲
#ifndef CONFIG_USBDEV_REQUEST_BUFFER_LEN
#define CONFIG_USBDEV_REQUEST_BUFFER_LEN 512
#endif

// 启用高级描述符注册 API
#define CONFIG_USBDEV_ADVANCE_DESC

// EP0 事件线程优先级与栈大小（仅 CONFIG_USBDEV_EP0_THREAD 时生效）
#ifndef CONFIG_USBDEV_EP0_PRIO
#define CONFIG_USBDEV_EP0_PRIO 4
#endif
#ifndef CONFIG_USBDEV_EP0_STACKSIZE
#define CONFIG_USBDEV_EP0_STACKSIZE 2048
#endif

// ============= USB Device Port Configuration =============

// 最大 USB 总线数：HPM 可能多控制器，其他平台一般为 1
#ifdef CONFIG_CHERRYUSB_DEVICE_HPM
#define CONFIG_USBDEV_MAX_BUS USB_SOC_MAX_COUNT
#else
#define CONFIG_USBDEV_MAX_BUS 1
#endif

// USB 控制器基址与中断号（由 board overlay 或 SoC 头提供）
#ifndef CONFIG_HPM_USBD_BASE
#ifdef CONFIG_CHERRYUSB_DEVICE_HPM
#define CONFIG_HPM_USBD_BASE HPM_USB0_BASE
#endif
#endif
#ifndef CONFIG_HPM_USBD_IRQn
#ifdef CONFIG_CHERRYUSB_DEVICE_HPM
#define CONFIG_HPM_USBD_IRQn IRQn_USB0
#endif
#endif

// ============== EHCI Configuration ==============

// HPM EHCI 控制器参数：帧列表、QH/qTD 池大小
#ifdef CONFIG_CHERRYUSB_DEVICE_HPM
#define CONFIG_USB_EHCI_HPMICRO         (1)
#define CONFIG_USB_EHCI_HCCR_OFFSET     (0x100u)
#define CONFIG_USB_EHCI_FRAME_LIST_SIZE 1024
#define CONFIG_USB_EHCI_QH_NUM          10
#define CONFIG_USB_EHCI_QTD_NUM         64
#endif

// ============= Addr Convert Configuration =============

// 物理地址 ↔ 系统地址转换（HPM cache 体系需要，其他平台通常为恒等映射）
#ifdef CONFIG_CHERRYUSB_DEVICE_HPM
#ifndef usb_phyaddr2ramaddr
#define usb_phyaddr2ramaddr(addr) core_local_mem_to_sys_address(0, addr)
#endif
#ifndef usb_ramaddr2phyaddr
#define usb_ramaddr2phyaddr(addr) sys_address_to_core_local_mem(0, addr)
#endif
#endif

#endif
