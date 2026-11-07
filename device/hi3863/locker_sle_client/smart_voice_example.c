#include "iot_gpio.h"
#include "iot_gpio_ex.h"
#include "iot_errno.h"
#include "ohos_init.h"
#include "cmsis_os2.h"
#include "uart.h"
#include "iot_uart.h"
#include "mqtt_module.h"  /* 添加MQTT模块支持 */
#include <stdio.h>
#include <string.h>
#include <stdbool.h>

// -------------------------- 通用配置 --------------------------
#define PWM_GPIO        IOT_IO_NAME_GPIO_11
#define PWM_PORT        3
#define FREQUENCY       2000
#define LED_GPIO        2

// -------------------------- UART2配置 --------------------------
#define UART2_ID                2
#define BAUDRATE                115200
#define RECV_BUF_SIZE           64
#define RX_TRIGGER_THRESHOLD    1
#define CMD_FRAME_LEN           6
#define RESP_FRAME_LEN          5
#define VOICE_FRAME_LEN         5      // 语音触发帧：AA 55 [msg] 55 AA

// -------------------------- 定时发送配置 --------------------------
// 1 = 开启"定时自动发送"（循环播报 K1->K2->K3），0 = 仅靠按键触发
#define VOICE_TEST_ENABLE       0
// 每隔多少毫秒发送一帧（与时基无关，内部按真实时间换算）
#define VOICE_TEST_PERIOD_MS    3000

// -------------------------- 语音消息号（对应平台串口输入消息号）--------------------------
#define VOICE_MSG_HUNGRY        0x02   // K1：主人我饿了
#define VOICE_MSG_THIRSTY       0x03   // K2：主人我渴了
#define VOICE_MSG_PLAY          0x04   // K3：主人我想出去玩

// -------------------------- 全局变量 --------------------------
volatile bool g_light_ctrl_flag    = false;
volatile bool g_alert_ctrl_flag    = false;
volatile bool g_light_flag_updated = false;
volatile bool g_alert_flag_updated = false;
volatile bool g_uart2_inited       = false;

// 按键触发语音标志（由 adc_key_example.c 设置）
volatile bool g_k1_triggered = false;  // K1：主人我饿了
volatile bool g_k2_triggered = false;  // K2：主人我渴了
volatile bool g_k3_triggered = false;  // K3：主人我想出去玩

static unsigned char g_recv_buf[RECV_BUF_SIZE] = {0};
static volatile bool g_is_rx_processing = false;

// -------------------------- 命令映射表（接收 SU-03T 下发的控制指令）--------------------------
typedef struct {
    uint8_t cmd_frame[CMD_FRAME_LEN];
    uint8_t resp_frame[RESP_FRAME_LEN];
    volatile bool *ctrl_flag;
    bool target_flag_val;
    volatile bool *update_flag;
    const char *func_desc;
} CmdCtrlMap;

static CmdCtrlMap g_cmd_ctrl_map[] = {
    // 开灯：AA 55 01 00 55 AA → LED亮
    {
        {0xAA, 0x55, 0x01, 0x00, 0x55, 0xAA},
        {0xAA, 0x55, 0x00, 0x55, 0xAA},
        &g_light_ctrl_flag, true, &g_light_flag_updated, "Light On"
    },
    // 关灯：AA 55 01 01 55 AA → LED灭
    {
        {0xAA, 0x55, 0x01, 0x01, 0x55, 0xAA},
        {0xAA, 0x55, 0x01, 0x55, 0xAA},
        &g_light_ctrl_flag, false, &g_light_flag_updated, "Light Off"
    },
    // 开警报：AA 55 01 02 55 AA → 蜂鸣器响
    {
        {0xAA, 0x55, 0x01, 0x02, 0x55, 0xAA},
        {0xAA, 0x55, 0x02, 0x55, 0xAA},
        &g_alert_ctrl_flag, true, &g_alert_flag_updated, "Alert On"
    },
    // 关警报：AA 55 01 03 55 AA → 蜂鸣器停
    {
        {0xAA, 0x55, 0x01, 0x03, 0x55, 0xAA},
        {0xAA, 0x55, 0x03, 0x55, 0xAA},
        &g_alert_ctrl_flag, false, &g_alert_flag_updated, "Alert Off"
    }
};
#define MAP_COUNT (sizeof(g_cmd_ctrl_map) / sizeof(g_cmd_ctrl_map[0]))

// -------------------------- 硬件控制 --------------------------
static void led_init(void) {
    IoTGpioInit(LED_GPIO);
    IoTGpioSetDir(LED_GPIO, IOT_GPIO_DIR_OUT);
    IoTGpioSetOutputVal(LED_GPIO, 0);
}

static void buzzer_init(void) {
    IoTGpioInit(PWM_GPIO);
    IoSetFunc(PWM_GPIO, 1);
    if (IoTPwmInit(PWM_PORT) != 0) {
        printf("[Buzzer] Init Failed!\n");
    }
    IoTPwmStart(PWM_PORT, 0, FREQUENCY);
}

static void update_hardware_state(void) {
    bool light_updated, alert_updated, light_state, alert_state;

    osKernelLock();
    light_state   = g_light_ctrl_flag;
    light_updated = g_light_flag_updated;
    if (light_updated) g_light_flag_updated = false;
    alert_state   = g_alert_ctrl_flag;
    alert_updated = g_alert_flag_updated;
    if (alert_updated) g_alert_flag_updated = false;
    osKernelUnlock();

    if (light_updated) {
        IoTGpioSetOutputVal(LED_GPIO, light_state ? 1 : 0);
        printf("[LED] %s\n", light_state ? "ON" : "OFF");
    }
    if (alert_updated) {
        IoTPwmStart(PWM_PORT, alert_state ? 50 : 0, FREQUENCY);
        printf("[Buzzer] %s\n", alert_state ? "ON" : "OFF");
    }
}

// -------------------------- 语音播报：统一发送接口 --------------------------
// 发送一帧 AA 55 [msg_id] 55 AA，并校验是否真正写出去
static void send_voice_frame(uint8_t msg_id) {
    if (!g_uart2_inited) {
        printf("[VOICE] UART2 not ready, skip msg=0x%02X\n", msg_id);
        return;
    }

    uint8_t frame[VOICE_FRAME_LEN] = {0xAA, 0x55, msg_id, 0x55, 0xAA};
    int written = IoTUartWrite(UART2_ID, frame, VOICE_FRAME_LEN);

    if (written == VOICE_FRAME_LEN) {
        printf("[VOICE] Send OK: AA 55 %02X 55 AA\n", msg_id);
    } else {
        // 发送失败/不完整：这才是排查 TX 的关键信息
        printf("[VOICE] Send FAIL: msg=0x%02X, written=%d (expect %d)\n",
               msg_id, written, VOICE_FRAME_LEN);
    }
}

// -------------------------- UART接收回调（接收 SU-03T 控制指令）--------------------------
static void uart2_rx_callback(const void *buffer, uint16_t length, bool error) {
    (void)error;

    if (g_is_rx_processing || buffer == NULL ||
        length != CMD_FRAME_LEN || length > RECV_BUF_SIZE) {
        return;
    }

    g_is_rx_processing = true;
    memcpy(g_recv_buf, buffer, length);

    printf("[UART2] Recv: ");
    for (uint16_t i = 0; i < length; i++) {
        printf("%02X ", g_recv_buf[i]);
    }
    printf("\n");

    bool cmd_matched = false;
    for (size_t i = 0; i < MAP_COUNT; i++) {
        if (memcmp(g_recv_buf, g_cmd_ctrl_map[i].cmd_frame, CMD_FRAME_LEN) == 0) {
            osDelay(1);
            IoTUartWrite(UART2_ID, g_cmd_ctrl_map[i].resp_frame, RESP_FRAME_LEN);

            osKernelLock();
            *(g_cmd_ctrl_map[i].ctrl_flag)   = g_cmd_ctrl_map[i].target_flag_val;
            *(g_cmd_ctrl_map[i].update_flag) = true;
            osKernelUnlock();

            printf("[UART2] %s\n", g_cmd_ctrl_map[i].func_desc);
            cmd_matched = true;
            break;
        }
    }

    if (!cmd_matched) {
        printf("[UART2] Unknown Cmd\n");
    }

    memset(g_recv_buf, 0, RECV_BUF_SIZE);
    g_is_rx_processing = false;
}

// -------------------------- UART初始化 --------------------------
static int uart2_init(void) {
    IoSetFunc(IOT_IO_NAME_GPIO_7, IOT_IO_FUNC_GPIO_7_UART2_RXD);
    IoSetFunc(IOT_IO_NAME_GPIO_8, IOT_IO_FUNC_GPIO_8_UART2_TXD);

    IotUartAttribute uart_attr = {
        .baudRate = BAUDRATE,
        .dataBits = IOT_UART_DATA_BIT_8,
        .stopBits = IOT_UART_STOP_BIT_1,
        .parity   = IOT_UART_PARITY_NONE,
        .rxBlock  = IOT_UART_BLOCK_STATE_NONE_BLOCK,
        .txBlock  = IOT_UART_BLOCK_STATE_BLOCK,
        .pad      = 0
    };

    if (IoTUartInit(UART2_ID, &uart_attr) != IOT_SUCCESS) {
        printf("[UART2] Init Failed!\n");
        return -1;
    }

    // 标记为已初始化：只要 IoTUartInit 成功，TX 就已经可用
    g_uart2_inited = true;
    printf("[UART2] Init OK, Baudrate=%d\n", BAUDRATE);

    // RX 回调仅用于"接收 SU-03T 下发的控制指令"。
    // 注册失败不影响 TX（语音播报），所以这里只告警、不反初始化、不退出。
    errcode_t ret = uapi_uart_register_rx_callback(
        UART2_ID, UART_RX_CONDITION_FULL_OR_IDLE,
        RX_TRIGGER_THRESHOLD, uart2_rx_callback
    );
    if (ret != ERRCODE_SUCC) {
        printf("[UART2] Register RX Callback Failed! ErrCode=%d (TX still works)\n", ret);
    } else {
        printf("[UART2] RX Callback Registered\n");
    }

    return 0;
}

// -------------------------- 主控线程 --------------------------
static void uart2_task_thread(void *arg) {
    (void)arg;

    if (uart2_init() != 0) {
        printf("[UART2] Task Init Failed!\n");
        return;
    }

    led_init();
    buzzer_init();
    printf("[Hardware] Init OK\n");

    // ---- 定时发送的时间基准（与系统节拍频率无关）----
    uint32_t tick_freq = osKernelGetTickFreq();           // ticks / second
    if (tick_freq == 0) tick_freq = 1000;                 // 兜底
    uint32_t period_ticks = (uint32_t)(((uint64_t)VOICE_TEST_PERIOD_MS * tick_freq) / 1000U);
    if (period_ticks == 0) period_ticks = 1;
    uint32_t last_send_tick = osKernelGetTickCount();

    // 定时轮播的语音序列：K1 -> K2 -> K3 循环
    static const uint8_t test_seq[] = {VOICE_MSG_HUNGRY, VOICE_MSG_THIRSTY, VOICE_MSG_PLAY};
    const uint32_t test_seq_len = sizeof(test_seq) / sizeof(test_seq[0]);
    uint32_t test_idx = 0;

#if VOICE_TEST_ENABLE
    printf("[TEST] Timed send enabled: every %d ms, cycle K1/K2/K3\n", VOICE_TEST_PERIOD_MS);
#else
    printf("[TEST] Timed send disabled, key-trigger only\n");
#endif

    while (1) {
        update_hardware_state();

        // ---- 按键触发（实时，最高优先）----
        if (g_k1_triggered) { 
            g_k1_triggered = false; 
            send_voice_frame(VOICE_MSG_HUNGRY);
            MqttPublishK1FeedCommand();  // 发送MQTT消息到ctrl主题: {"motor":"feed"}
        }
        if (g_k2_triggered) { 
            g_k2_triggered = false; 
            send_voice_frame(VOICE_MSG_THIRSTY);
            MqttPublishK2WaterCommand();  // 发送MQTT消息到ctrl3主题: {"led":"water"}
        }
        if (g_k3_triggered) { 
            g_k3_triggered = false; 
            send_voice_frame(VOICE_MSG_PLAY);
            MqttPublishK3PlayCommand();  // 发送MQTT消息到ctrl主题: {"motor":"play_dog"}
        }

#if VOICE_TEST_ENABLE
        // ---- 定时自动发送（按真实时间到点才发，循环 K1/K2/K3）----
        uint32_t now = osKernelGetTickCount();
        if ((uint32_t)(now - last_send_tick) >= period_ticks) {
            last_send_tick = now;
            send_voice_frame(test_seq[test_idx]);
            test_idx = (test_idx + 1) % test_seq_len;
        }
#endif

        osDelay(5);   // 5 ticks 轮询；按键与定时都在此循环内
    }
}

// -------------------------- 系统入口 --------------------------
static void smart_voice_system_init(void) {
    osThreadAttr_t uart2_attr = {0};
    uart2_attr.name       = "uart2_task_thread";
    uart2_attr.stack_size = 4096;
    uart2_attr.priority   = osPriorityNormal;
    if (osThreadNew(uart2_task_thread, NULL, &uart2_attr) == NULL) {
        printf("[System] Create UART2 Thread Failed!\n");
    }

    printf("[System] Start!\n");
}

APP_FEATURE_INIT(smart_voice_system_init);
