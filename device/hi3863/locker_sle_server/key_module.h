#ifndef KEY_MODULE_H
#define KEY_MODULE_H

#include <stdint.h>
#include <stdbool.h>

// ============================================================================
// 按键配置参数
// ============================================================================

// GPIO11 按键配置
#define GPIO_KEY_NUM        11          // 按键GPIO编号
#define KEY_DEBOUNCE_MS     20          // 消抖时间（毫秒）
#define KEY_POLL_INTERVAL   10          // 轮询间隔（毫秒）

// ============================================================================
// 按键状态定义
// ============================================================================

typedef enum {
    KEY_STATE_IDLE = 0,     // 空闲状态
    KEY_STATE_PRESSED,      // 按下状态
    KEY_STATE_RELEASED      // 释放状态
} KeyState;

// ============================================================================
// 按键回调函数类型
// ============================================================================

typedef void (*KeyCallback)(KeyState state);

// ============================================================================
// API 接口声明
// ============================================================================

/**
 * @brief 初始化按键模块
 * @return 0: 成功, -1: 失败
 */
int KeyModuleInit(void);

/**
 * @brief 注册按键回调函数
 * @param callback 回调函数指针
 * @return 0: 成功, -1: 失败
 */
int KeyRegisterCallback(KeyCallback callback);

/**
 * @brief 读取按键当前状态
 * @return true: 按下, false: 释放
 */
bool KeyIsPressed(void);

/**
 * @brief 按键扫描任务（需在独立线程中运行）
 * @param arg 任务参数（未使用）
 */
void KeyScanTask(void *arg);

#endif // KEY_MODULE_H
