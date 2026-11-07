/**
# Copyright (C) 2024 iSoftStone Open Source Organization .
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
 */

#ifndef SLE_SERVER_ADV_H
#define SLE_SERVER_ADV_H

#include "errcode.h"
#include "sle_ssap_server.h"
#include <stdint.h>

#define SLE_UUID_SERVER_SERVICE 0x2222

/* Property UUID */
#define SLE_UUID_SERVER_NTF_REPORT 0x2323

/* Property Property */
#define SLE_UUID_TEST_PROPERTIES (SSAP_PERMISSION_READ | SSAP_PERMISSION_WRITE)

/* Operation indication */
#define SLE_UUID_TEST_OPERATION_INDICATION (SSAP_OPERATE_INDICATION_BIT_READ | SSAP_OPERATE_INDICATION_BIT_WRITE)

/* Descriptor Property */
#define SLE_UUID_TEST_DESCRIPTOR (SSAP_PERMISSION_READ | SSAP_PERMISSION_WRITE)

/* sle device name */
#define NAME_MAX_LENGTH 32  // 增加到32字节，支持更长的设备名称

/* 连接调度间隔12.5ms，单位125us */
#define SLE_CONN_INTV_MIN_DEFAULT 0x64
/* 连接调度间隔12.5ms，单位125us */
#define SLE_CONN_INTV_MAX_DEFAULT 0x64
/* 连接调度间隔25ms，单位125us */
#define SLE_ADV_INTERVAL_MIN_DEFAULT 0xC8
/* 连接调度间隔25ms，单位125us */
#define SLE_ADV_INTERVAL_MAX_DEFAULT 0xC8
/* 超时时间5000ms，单位10ms */
#define SLE_CONN_SUPERVISION_TIMEOUT_DEFAULT 0x1F4
/* 超时时间4990ms，单位10ms */
#define SLE_CONN_MAX_LATENCY 0x1F3
/* 广播发送功率 */
#define SLE_ADV_TX_POWER 10
/* 广播ID */
#define SLE_ADV_HANDLE_DEFAULT 1
/* 最大广播数据长度 */
#define SLE_ADV_DATA_LEN_MAX 251
/* 广播名称 */

#define SLE_SERVER_INIT_DELAY_MS 1000

/* Service UUID */
#define SLE_UUID_SERVER_SERVICE 0x2222

/* Property UUID */
#define SLE_UUID_SERVER_NTF_REPORT 0x2323

/* Property Property */
#define SLE_UUID_TEST_PROPERTIES (SSAP_PERMISSION_READ | SSAP_PERMISSION_WRITE)

/* Operation indication */
#define SLE_UUID_TEST_OPERATION_INDICATION (SSAP_OPERATE_INDICATION_BIT_READ | SSAP_OPERATE_INDICATION_BIT_WRITE)

/* Descriptor Property */
#define SLE_UUID_TEST_DESCRIPTOR (SSAP_PERMISSION_READ | SSAP_PERMISSION_WRITE)

#define OCTET_BIT_LEN 8               // 一个字节的位数
#define UUID_LEN_2 2                  // 2字节UUID长度
#define UUID_INDEX 14                 // UUID数组中特定字段的索引
#define BT_INDEX_5 5                  // Bluetooth地址数组索引
#define BT_INDEX_4 4                  // Bluetooth地址数组索引
#define BT_INDEX_0 0                  // Bluetooth地址数组索引
#define SLE_MTU_SIZE_DEFAULT 520      // SLE默认MTU大小
#define SLE_ADV_HANDLE_DEFAULT 1      // 默认广播句柄
#define UUID_16BIT_LEN 2              // 16位UUID长度
#define UUID_128BIT_LEN 16            // 128位UUID长度
#define SLE_SERVER_LOG "[sle server]" // 日志前缀
#define SLE_SERVER_INIT_DELAY_MS 1000 // 服务器初始化延迟（毫秒）
#define SLE_TRANSFER_SIZE 256         // UART传输数据块大小
#define DELAY_100MS 100               // 100毫秒延迟
#define TASK_SIZE 2048                // 任务栈大小
#define PRIO 25                       // 任务优先级
#define USLEEP_1000000 1000000        // 1秒微秒延迟

typedef struct SleAdvCommonValue
{
    uint8_t type;
    uint8_t length;
    uint8_t value;
} SleAdvCommonT;

typedef enum SleAdvChannel
{
    SLE_ADV_CHANNEL_MAP_77 = 0x01,
    SLE_ADV_CHANNEL_MAP_78 = 0x02,
    SLE_ADV_CHANNEL_MAP_79 = 0x04,
    SLE_ADV_CHANNEL_MAP_DEFAULT = 0x07
} SleAdvChannelMapT;

typedef enum SleAdvData
{
    SLE_ADV_DATA_TYPE_DISCOVERY_LEVEL = 0x01,                         /* 发现等级 */
    SLE_ADV_DATA_TYPE_ACCESS_MODE = 0x02,                             /* 接入层能力 */
    SLE_ADV_DATA_TYPE_SERVICE_DATA_16BIT_UUID = 0x03,                 /* 标准服务数据信息 */
    SLE_ADV_DATA_TYPE_SERVICE_DATA_128BIT_UUID = 0x04,                /* 自定义服务数据信息 */
    SLE_ADV_DATA_TYPE_COMPLETE_LIST_OF_16BIT_SERVICE_UUIDS = 0x05,    /* 完整标准服务标识列表 */
    SLE_ADV_DATA_TYPE_COMPLETE_LIST_OF_128BIT_SERVICE_UUIDS = 0x06,   /* 完整自定义服务标识列表 */
    SLE_ADV_DATA_TYPE_INCOMPLETE_LIST_OF_16BIT_SERVICE_UUIDS = 0x07,  /* 部分标准服务标识列表 */
    SLE_ADV_DATA_TYPE_INCOMPLETE_LIST_OF_128BIT_SERVICE_UUIDS = 0x08, /* 部分自定义服务标识列表 */
    SLE_ADV_DATA_TYPE_SERVICE_STRUCTURE_HASH_VALUE = 0x09,            /* 服务结构散列值 */
    SLE_ADV_DATA_TYPE_SHORTENED_LOCAL_NAME = 0x0A,                    /* 设备缩写本地名称 */
    SLE_ADV_DATA_TYPE_COMPLETE_LOCAL_NAME = 0x0B,                     /* 设备完整本地名称 */
    SLE_ADV_DATA_TYPE_TX_POWER_LEVEL = 0x0C,                          /* 广播发送功率 */
    SLE_ADV_DATA_TYPE_SLB_COMMUNICATION_DOMAIN = 0x0D,                /* SLB通信域域名 */
    SLE_ADV_DATA_TYPE_SLB_MEDIA_ACCESS_LAYER_ID = 0x0E,               /* SLB媒体接入层标识 */
    SLE_ADV_DATA_TYPE_EXTENDED = 0xFE,                                /* 数据类型扩展 */
    SLE_ADV_DATA_TYPE_MANUFACTURER_SPECIFIC_DATA = 0xFF               /* 厂商自定义信息 */
} SleAdvDataType;

extern uint8_t g_sle_rx_data[256];
extern bool g_sle_new_data_ready;

errcode_t sle_server_adv_init(void);

errcode_t sle_announce_register_cbks(void);

errcode_t sle_server_init(void);

errcode_t sle_server_send_report_by_uuid(const uint8_t *data, uint8_t len);

errcode_t sle_server_send_report_by_handle(const uint8_t *data, uint8_t len);

typedef void (*SleUartServerMsgQueue)(uint8_t *bufferAddr, uint16_t bufferSize);

errcode_t sle_enable_server_cbk(void);

#endif