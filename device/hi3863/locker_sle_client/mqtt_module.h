/*
 * MQTT 配置文件
 * 
 * 说明：
 * 1. 根据实际环境修改配置参数
 * 2. 所有字符串值必须用双引号包裹
 */

#ifndef MQTT_MODULE_H
#define MQTT_MODULE_H

#include <stdbool.h>

// ============================================================================
// WiFi 配置 - 请根据实际情况修改
// ============================================================================

// WiFi热点名称（SSID）
#define WIFI_SSID "YOUR_WIFI_SSID"

// WiFi密码
#define WIFI_PSK  "2225070zhu"

// ============================================================================
// MQTT 服务器配置 - 请根据实际情况修改
// ============================================================================

// MQTT服务器IP地址或域名
#define MQTT_SERVER_IP   "YOUR_SERVER_IP"

// MQTT服务器端口
// 普通连接: "1883", SSL/TLS: "8883"
#define MQTT_SERVER_PORT "1883"

// ============================================================================
// MQTT 设备认证信息 - 请根据实际情况修改
// ============================================================================

// 设备ID（在云平台注册时获得）
#define DEVICE_ID       "YOUR_DEVICE_ID"

// MQTT用户名（通常与DEVICE_ID相同）
#define MQTT_USERNAME   "YOUR_MQTT_USERNAME"

// MQTT客户端ID（全局唯一标识）
// 建议格式: "{device_name}_{timestamp}"
#define MQTT_CLIENT_ID  "YOUR_MQTT_CLIENT_ID"

// MQTT密码/设备密钥
#define MQTT_PASSWORD   "YOUR_PASSWORD"

// ============================================================================
// MQTT 主题配置 - 根据实际需求修改
// ============================================================================

// 发布主题
#define PUBLISH_TOPIC   "ctrl"

// 订阅主题
#define SUBSCRIBE_TOPIC "ctrl2"

// 响应主题（可选）
#define RESPONSE_TOPIC  "ctrl/response"

// ============================================================================
// API 接口声明
// ============================================================================

/**
 * @brief 初始化MQTT模块
 * 
 * 功能说明:
 * 1. 创建独立的MQTT任务线程
 * 2. 自动连接WiFi热点
 * 3. 建立TCP连接到MQTT服务器
 * 4. 完成MQTT协议握手认证
 * 5. 订阅命令接收主题
 * 6. 进入循环保持连接状态
 * 
 * @return 0: 成功, -1: 失败
 * 
 * 使用示例:
 * ```c
 * if (MqttModuleInit() == 0) {
 *     printf("MQTT module initialized successfully\n");
 * } else {
 *     printf("Failed to initialize MQTT module\n");
 * }
 * ```
 */
int MqttModuleInit(void);

/**
 * @brief 发布SLE接收到的数据到云端
 * 
 * @param sle_data SLE接收到的数据字符串（最大512字节）
 * @return 0: 成功, -1: 失败（未连接或发布失败）
 */
int MqttPublishSleData(const char *sle_data);

/**
 * @brief 发布设备状态到云端
 * 
 * @param status 设备状态字符串（如: "online", "offline", "error"）
 * @return 0: 成功, -1: 失败（未连接或发布失败）
 */
int MqttPublishDeviceStatus(const char *status);

/**
 * @brief 发布K1按键触发消息（喂食）到ctrl主题
 * 发送格式: {"motor":"feed"}
 * 
 * @return 0: 成功, -1: 失败（未连接或发布失败）
 */
int MqttPublishK1FeedCommand(void);

/**
 * @brief 发布K2按键触发消息（喂水）到ctrl3主题
 * 发送格式: {"led":"water"}
 * 
 * @return 0: 成功, -1: 失败（未连接或发布失败）
 */
int MqttPublishK2WaterCommand(void);

/**
 * @brief 发布K3按键触发消息（逗宠）到ctrl主题
 * 发送格式: {"motor":"play_dog"}
 * 
 * @return 0: 成功, -1: 失败（未连接或发布失败）
 */
int MqttPublishK3PlayCommand(void);

/**
 * @brief 发布SLE开锁数据到ctrl3主题
 * 发送格式: {"sle_data":"key opening","device":"locker_sle_client"}
 * 
 * @return 0: 成功, -1: 失败（未连接或发布失败）
 */
int MqttPublishSleKeyOpening(void);


#endif /* MQTT_MODULE_H */
