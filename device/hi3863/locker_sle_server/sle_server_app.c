#include <stdint.h> 
#include <string.h>  // 添加 string.h 以使用 strlen 函数
#include "cmsis_os2.h"
#include "errcode.h"
#include "iot_uart.h"
#include "ohos_init.h"
#include "ohos_sle_common.h"
#include "ohos_sle_connection_manager.h"
#include "ohos_sle_device_discovery.h"
#include "ohos_sle_errcode.h"
#include "ohos_sle_ssap_client.h"
#include "ohos_sle_ssap_server.h"
#include "osal_addr.h"
#include "osal_debug.h"
#include "osal_task.h"
#include "pinctrl.h"
#include "securec.h"
#include "sle_common.h"
#include "sle_connection_manager.h"
#include "sle_device_discovery.h"
#include "sle_errcode.h"
#include "sle_server_lib.h"
#include "soc_osal.h"
#include "uart.h"
#include "key_module.h"  // 添加按键模块头文件

//服务端发送的数据 - 按键按下提示字符串
const char *KEY_PRESSED_MSG = "key opening";

/**
 * @brief 按键状态变化回调函数
 * @param state 按键状态
 */
void OnKeyStateChanged(KeyState state)
{
    if (state == KEY_STATE_PRESSED) {
        printf("[APP] 🎯 Key pressed! Sending SLE data...\n");
        
        // 按键按下时发送"key opening"字符串到客户端
        sle_server_send_report_by_handle((uint8_t *)KEY_PRESSED_MSG, strlen(KEY_PRESSED_MSG));
        
        printf("[APP] ✅ Sent message: \"%s\" via SLE\n", KEY_PRESSED_MSG);
    } else {
        printf("[APP] Key released\n");
    }
}

void sle_rx_server(void *arg)
{
    unused(arg);
    while (1)
    {
        if (g_sle_new_data_ready == true)
        {
            printf("%s Received data from client: ", SLE_SERVER_LOG);
            // 以十六进制格式打印接收到的数据
            for (int i = 0; i < sizeof(g_sle_rx_data) && g_sle_rx_data[i] != 0; i++)
            {
                printf("0x%02X ", g_sle_rx_data[i]);
            }
            printf("\r\n");
            g_sle_new_data_ready = false;
        }
        osDelay(1);
    }
}

//已禁用：周期性发送测试数据的任务
void sle_tx_server(void *arg)
{
    unused(arg);
    while (1)
    {
        // 使用 KEY_PRESSED_MSG 替代已删除的 send_date
        sle_server_send_report_by_handle((uint8_t *)KEY_PRESSED_MSG, strlen(KEY_PRESSED_MSG));
        osDelay(100);
    }
}

static void sle_server_app(void)
{
    sle_server_init();
    
    // 初始化按键模块
    if (KeyModuleInit() != 0) {
        printf("[SLE Server] ❌ Failed to initialize key module!\n");
        return;
    }
    
    // 注册按键回调
    KeyRegisterCallback(OnKeyStateChanged);
    
    // 创建数据接收任务
    osThreadAttr_t rx_attr = {0};
    rx_attr.name = "sle_rx_server";
    rx_attr.stack_size = 8192;
    rx_attr.priority = osPriorityNormal;
    osal_kthread_lock();
    if (osThreadNew(sle_rx_server, NULL, &rx_attr) == NULL)
    {
        printf("[SLE Server] Failed to create sle_rx_server thread!\n");
    }
    osal_kthread_unlock();

    // 周期性发送任务（已禁用，由按键触发）
    /*
    osThreadAttr_t tx_attr = {0};
    tx_attr.name = "sle_tx_server";
    tx_attr.stack_size = 8192;
    tx_attr.priority = osPriorityNormal;
    osal_kthread_lock();
    if (osThreadNew(sle_tx_server, NULL, &tx_attr) == NULL)
    {
        printf("[SLE Server] Failed to create sle_tx_server thread!\n");
    }
    osal_kthread_unlock();
    */
    
    // 创建按键扫描任务
    osThreadAttr_t key_attr = {0};
    key_attr.name = "key_scan_task";
    key_attr.stack_size = 4096;
    key_attr.priority = osPriorityNormal;
    osal_kthread_lock();
    if (osThreadNew(KeyScanTask, NULL, &key_attr) == NULL)
    {
        printf("[SLE Server] ❌ Failed to create key_scan_task thread!\n");
    }
    else
    {
        printf("[SLE Server] ✅ Key scan task created\n");
    }
    osal_kthread_unlock();
    
    printf("[SLE Server] Server initialized, waiting for K1 key press (GPIO11) to send data...\n");
}

APP_FEATURE_INIT(sle_server_app);