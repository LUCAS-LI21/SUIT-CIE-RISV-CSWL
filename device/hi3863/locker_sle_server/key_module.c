#include "key_module.h"
#include "iot_gpio.h"
#include "iot_errno.h"  
#include "hal_pinctrl.h"  
#include "cmsis_os2.h"
#include "osal_task.h"
#include <stdio.h>

// ============================================================================
// 全局变量
// ============================================================================

static KeyCallback g_key_callback = NULL;  // 按键回调函数
static KeyState g_last_key_state = KEY_STATE_IDLE;  // 上次按键状态

// ============================================================================
// 内部辅助函数
// ============================================================================

/**
 * @brief 读取GPIO电平并转换为按键状态
 * @return 按键状态
 */
static KeyState ReadKeyState(void)
{
    IotGpioValue level = IOT_GPIO_VALUE1;  // 使用正确的类型
    
    // 读取GPIO输入电平
    if (IoTGpioGetInputVal(GPIO_KEY_NUM, &level) != IOT_SUCCESS) {
        printf("[KEY] Failed to read GPIO%d\n", GPIO_KEY_NUM);
        return KEY_STATE_IDLE;
    }
    
    // WS63V100: 低电平表示按键按下（接地），高电平表示释放（上拉）
    return (level == IOT_GPIO_VALUE0) ? KEY_STATE_PRESSED : KEY_STATE_RELEASED;
}

/**
 * @brief 软件消抖处理
 * @param current_state 当前检测到的状态
 * @return 消抖后的稳定状态
 */
static KeyState DebounceKey(KeyState current_state)
{
    static KeyState stable_state = KEY_STATE_RELEASED;
    static uint32_t debounce_count = 0;
    
    if (current_state == stable_state) {
        // 状态一致，重置计数器
        debounce_count = 0;
        return stable_state;
    }
    
    // 状态不一致，累加计数器
    debounce_count++;
    
    // 连续检测到相同状态超过消抖阈值，认为状态稳定
    if (debounce_count >= (KEY_DEBOUNCE_MS / KEY_POLL_INTERVAL)) {
        stable_state = current_state;
        debounce_count = 0;
        return stable_state;
    }
    
    // 未达到阈值，返回之前的稳定状态
    return stable_state;
}

// ============================================================================
// API 实现
// ============================================================================

int KeyModuleInit(void)
{
    int ret;
    hal_pin_funcs_t *pin_funcs = NULL;
    
    printf("[KEY] Initializing key module on GPIO%d...\n", GPIO_KEY_NUM);
    
    // 1. 初始化GPIO
    ret = IoTGpioInit(GPIO_KEY_NUM);
    if (ret != IOT_SUCCESS) {
        printf("[KEY] ❌ Failed to init GPIO%d (ret=%d)\n", GPIO_KEY_NUM, ret);
        return -1;
    }
    printf("[KEY] ✅ GPIO%d initialized\n", GPIO_KEY_NUM);
    
    // 2. 设置为输入方向
    ret = IoTGpioSetDir(GPIO_KEY_NUM, IOT_GPIO_DIR_IN);
    if (ret != IOT_SUCCESS) {
        printf("[KEY] ❌ Failed to set GPIO%d as input (ret=%d)\n", GPIO_KEY_NUM, ret);
        return -1;
    }
    printf("[KEY] ✅ GPIO%d set as input\n", GPIO_KEY_NUM);
    
    // 3. 设置上拉电阻（使用 PinCtrl API）
    pin_funcs = hal_pin_get_funcs();
    if (pin_funcs != NULL && pin_funcs->set_pull != NULL) {
        ret = pin_funcs->set_pull((pin_t)GPIO_KEY_NUM, PIN_PULL_TYPE_UP);
        if (ret != ERRCODE_SUCC) {
            printf("[KEY] ⚠️  Failed to set pull-up for GPIO%d (ret=%d), using default config\n", 
                   GPIO_KEY_NUM, ret);
        } else {
            printf("[KEY] ✅ GPIO%d pull-up enabled via PinCtrl\n", GPIO_KEY_NUM);
        }
    } else {
        printf("[KEY] ⚠️  PinCtrl functions not available, using default pull config\n");
    }
    
    // 4. 读取初始状态
    KeyState initial_state = ReadKeyState();
    printf("[KEY] Initial key state: %s\n", 
           (initial_state == KEY_STATE_PRESSED) ? "PRESSED" : "RELEASED");
    
    printf("[KEY] Key module initialized successfully!\n");
    return 0;
}

int KeyRegisterCallback(KeyCallback callback)
{
    if (callback == NULL) {
        printf("[KEY] ❌ Invalid callback function\n");
        return -1;
    }
    
    g_key_callback = callback;
    printf("[KEY] Callback registered successfully\n");
    return 0;
}

bool KeyIsPressed(void)
{
    KeyState state = ReadKeyState();
    return (state == KEY_STATE_PRESSED);
}

void KeyScanTask(void *arg)
{
    (void)arg;
    
    printf("[KEY] Key scan task started\n");
    
    while (1) {
        // 1. 读取原始状态
        KeyState raw_state = ReadKeyState();
        
        // 2. 软件消抖
        KeyState stable_state = DebounceKey(raw_state);
        
        // 3. 检测状态变化
        if (stable_state != g_last_key_state) {
            g_last_key_state = stable_state;
            
            // 打印状态变化
            printf("[KEY] State changed: %s\n", 
                   (stable_state == KEY_STATE_PRESSED) ? "PRESSED" : "RELEASED");
            
            // 4. 调用回调函数
            if (g_key_callback != NULL) {
                g_key_callback(stable_state);
            }
        }
        
        // 5. 延时等待下次扫描
        osDelay(KEY_POLL_INTERVAL);
    }
}
