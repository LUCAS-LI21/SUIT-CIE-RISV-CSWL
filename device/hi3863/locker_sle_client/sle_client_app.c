#include "cmsis_os2.h"
#include "common_def.h"
#include "errcode.h"
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
#include "sle_client_lib.h"
#include "sle_common.h"
#include "sle_connection_manager.h"
#include "sle_device_discovery.h"
#include "sle_errcode.h"
#include "soc_osal.h"
#include "mqtt_module.h"  // 添加MQTT模块头文件

// SLE测试数据 (已禁用周期性发送)
// uint8_t send_date[] = "Hello SLE! Test: ABC123";  // 测试数据

void sle_rx_client(void *arg)
{
    unused(arg);
    uint32_t last_publish_time = 0;  // 【新增】上次发布时间戳
    const uint32_t MIN_PUBLISH_INTERVAL = 100;  // 【新增】最小发布间隔100ms
    
    while (1)
    {
        if (g_sle_new_data_ready == true)
        {
            printf("[SLE RX] Received: %s\r\n", g_sle_rx_data);
            
            // 【优化】限流保护：如果距离上次发布不足100ms，跳过本次发布
            uint32_t current_time = osKernelGetTickCount();
            if ((current_time - last_publish_time) < MIN_PUBLISH_INTERVAL) {
                printf("[SLE RX] ⚠️  Publish rate limited, skip this message\r\n");
                g_sle_new_data_ready = false;
                continue;
            }
            
            // 将SLE接收到的数据通过MQTT发布到云端
            int ret = MqttPublishSleData((const char *)g_sle_rx_data);
            if (ret == 0) {
                last_publish_time = current_time;  // 【优化】只在成功时更新时间
            }
            
            g_sle_new_data_ready = false;
        }
        osDelay(1);
    }
}

// 已禁用周期性发送任务 - 不需要验证连接成功后持续发送数据
/*
void sle_tx_client(void *arg)
{
    unused(arg);
    while (1)
    {
        // 发送测试数据到SLE服务端
        sle_client_send_report_by_handle(send_date, sizeof(send_date));
        osDelay(100);
    }
}
*/

static void sle_client_app(void)
{
    printf("[APP] Initializing SLE Client...\r\n");
    
    // 初始化SLE客户端
    SleClientInit();
    
    // 初始化MQTT模块（会自动连接WiFi和MQTT服务器）
    printf("[APP] Initializing MQTT module...\r\n");
    if (MqttModuleInit() != 0) {
        printf("[APP] ❌ Failed to initialize MQTT module!\r\n");
    } else {
        printf("[APP] ✅ MQTT module initialized successfully\r\n");
    }
    
    // 创建SLE接收任务
    osThreadAttr_t rx_attr = {0};
    rx_attr.name = "sle_rx_client";
    rx_attr.stack_size = 8192;
    rx_attr.priority = osPriorityNormal;
    osal_kthread_lock();
    if (osThreadNew(sle_rx_client, NULL, &rx_attr) == NULL)
    {
        printf("[APP] Failed to create SLE RX thread!\r\n");
    }
    osal_kthread_unlock();

    // 已禁用SLE发送任务 - 不需要周期性发送验证数据
    /*
    // 创建SLE发送任务
    osThreadAttr_t tx_attr = {0};
    tx_attr.name = "sle_tx_client";
    tx_attr.stack_size = 8192;
    tx_attr.priority = osPriorityNormal;
    osal_kthread_lock();
    if (osThreadNew(sle_tx_client, NULL, &tx_attr) == NULL)
    {
        printf("[APP] Failed to create SLE TX thread!\r\n");
    }
    osal_kthread_unlock();
    */
    
    printf("[APP] SLE Client initialized successfully\r\n");
}

APP_FEATURE_INIT(sle_client_app);
