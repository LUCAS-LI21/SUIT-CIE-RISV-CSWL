#ifndef SLE_CLIENT_LIB
#define SLE_CLIENT_LIB
#include "errcode.h"
#include "sle_ssap_client.h"
#include <stdint.h>

#define SLE_MTU_SIZE_DEFAULT 520
// SLE扫描间隔，单位为BLE的时间单位（0.625ms），100代表62.5ms
#define SLE_SEEK_INTERVAL_DEFAULT 100
// SLE扫描窗口大小，单位同上，100代表62.5ms。扫描窗口必须小于等于扫描间隔
#define SLE_SEEK_WINDOW_DEFAULT 100
// 16位UUID的长度
#define UUID_16BIT_LEN 2
// 128位UUID的长度
#define UUID_128BIT_LEN 16
// SLE UART任务延迟，单位为毫秒
#define SLE_TASK_DELAY_MS 1000
// 等待SLE核心就绪的最长时间，单位为毫秒
#define SLE_WAIT_SLE_CORE_READY_MS 5000
// 如果没有定义服务端名称，则使用默认名称
#ifndef SLE_SERVER_NAME
#define SLE_SERVER_NAME "pet"
#endif
// 日志前缀，用于标识本模块的打印信息
#define SLE_CLIENT_LOG "[sle uart client]"
// 2字节UUID的长度
#define UUID_LEN_2 2
// 延时100毫秒
#define DELAY_100MS 100
// 任务栈大小，单位为字节
#define TASK_SIZE 2048
// 任务优先级，数值越大优先级越低
#define PRIO 25
// 微秒级延时，1秒 = 1000000微秒
#define USLEEP_1000000 1000000
// UART单次传输的最大数据大小
#define SLE_TRANSFER_SIZE 256

extern uint8_t g_sle_rx_data[256];
extern bool g_sle_new_data_ready;

void SleClientInit(void);

void SleStartScan(void);

uint16_t get_g_sle_conn_id(void);

ssapc_write_param_t *get_g_sle_send_param(void);

int uart_sle_client_send_data(uint8_t *data, uint8_t length);
errcode_t sle_client_send_report_by_handle(const uint8_t *data, uint8_t len);
#endif