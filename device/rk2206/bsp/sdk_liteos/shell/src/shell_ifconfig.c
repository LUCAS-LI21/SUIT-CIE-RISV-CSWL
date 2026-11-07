#include <stddef.h>
#include <string.h>
#include <securec.h>
#include <stdlib.h>
#include <stdio.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>

#include "lwip/if_api.h"
#include "lwip/netifapi.h"
#include "wifi_device.h"
#include "wifi_hotspot_config.h"

#include "los_tick.h"
#include "los_task.h"
#include "los_config.h"
#include "los_interrupt.h"
#include "los_debug.h"
#include "los_compiler.h"

#include "config_network.h"
#include "lz_hardware.h"

#if (LOGCFG_SHELL == 1)
#include "shcmd.h"
#include "shell.h"
#endif // LOGCFG_SHELL

// 查看IP地址
int shell_ifconfig(int argc, const char *argv[])
{
    WifiLinkedInfo wifi_info;
    int gw, netmask;
    int retry = 5;
    
    memset(&wifi_info, 0, sizeof(wifi_info));
    for (int i = 0; i < retry; i++)
    {
        // 获取WiFi连接信息
        if (GetLinkedInfo(&wifi_info) == WIFI_SUCCESS)
        {
            // 确认是否连接
            if ((wifi_info.connState == WIFI_CONNECTED) && (wifi_info.ipAddress != 0))
            {
                printf("inet %s\n", inet_ntoa(wifi_info.ipAddress));
                if (WIFI_SUCCESS == GetLocalWifiNetmask(&netmask))
                {
                    printf("netmask %s\n", inet_ntoa(netmask));
                }
                if (WIFI_SUCCESS == GetLocalWifiGw(&gw))
                {
                    printf("gateway %s\n", inet_ntoa(gw));
                }
                break;
            }
            else
            {
                printf("WiFi disconnect...\n");
            }
        }
        else
        {
            printf("GetLinkedInfo failed\n");
        }
        
        LOS_Msleep(1000);
    }
    
    return 0;
}

