/**
 * @file main.c
 * @author qingyu
 * @brief 
 * @version 0.1
 * @date 2026-05-10
 * 
 * @copyright Copyright (c) 2026
 * 
 */

#include <zephyr/kernel.h>
#include "System_startup.h"

int main(void)
{
	System_Startup();

	while (1)
	{
		k_msleep(1000);
	}
}
