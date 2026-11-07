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
#include "sle_server_lib.h"
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
#include "string.h"
#include "uart.h"

#define printf(fmt, args...) osal_printk(fmt, ##args)

// SU-03T语音模块使用的UART端口号（根据硬件连接选择UART1）
#define SU03T_UART_PORT 1
// SU-03T通信波特率
#define SU03T_UART_BAUDRATE 9600

static char g_sleUuidAppUuid[UUID_LEN_2] = {0x12, 0x34}; // SLE应用UUID
static char g_slePropertyValue[OCTET_BIT_LEN] = {0x0};   // 属性初始值
static uint16_t g_sleConnHdl = 0;                        // SLE连接句柄
static uint8_t g_serverId = 0;                           // SLE服务器ID
static uint16_t g_serviceHandle = 0;                     // SLE服务句柄
static uint16_t g_propertyHandle = 0;                    // SLE属性句柄
uint16_t g_slePairHdl = 0;                               // SLE配对句柄

// 128位基础UUID
static uint8_t g_sleBase[] = {0x37, 0xBE, 0xA8, 0x80, 0xFC, 0x70, 0x11, 0xEA,
                              0xB7, 0x20, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
uint8_t g_receiveBuf[520] = {0};
static uint8_t g_sleLocalName[NAME_MAX_LENGTH] = "pet";

static void Encode2byteLittle(uint8_t *ptr, uint16_t data);
static void sle_uuid_set_base(SleUuid *out);
static void sle_uuid_setu2(uint16_t u2, SleUuid *out);
static void sle_uuid_print(SleUuid *uuid);
static void ssaps_mtu_changed_cbk(uint8_t serverId, uint16_t connId, SsapcExchangeInfo *mtu_size, errcode_t status);
static void ssaps_start_service_cbk(uint8_t serverId, uint16_t handle, errcode_t status);
static void ssaps_add_service_cbk(uint8_t serverId, SleUuid *uuid, uint16_t handle, errcode_t status);
static void ssaps_add_property_cbk(uint8_t serverId, SleUuid *uuid, uint16_t serviceHandle, uint16_t handle,
                                   errcode_t status);
static void ssaps_add_descriptor_cbk(uint8_t serverId, SleUuid *uuid, uint16_t serviceHandle, uint16_t propertyHandle,
                                     errcode_t status);
static void ssaps_delete_all_service_cbk(uint8_t serverId, errcode_t status);
static errcode_t sle_ssaps_register_cbks(SsapsReadRequestCallback ssaps_read_callback,
                                         SsapsWriteRequestCallback ssaps_write_callback);
static errcode_t sle_uuid_server_service_add(void);
static errcode_t add_property_sync(void);
static errcode_t sle_uuid_server_property_add(void);
static errcode_t sle_server_add(void);
errcode_t sle_server_send_report_by_handle(const uint8_t *data, uint8_t len);
static void sle_connect_state_changed_cbk(uint16_t connId, const SleAddr *addr, SleAcbStateType conn_state,
                                          SlePairStateType pair_state, SleDiscReasonType disc_reason);
static void sle_pair_complete_cbk(uint16_t connId, const SleAddr *addr, errcode_t status);
static errcode_t sle_conn_register_cbks(void);
void ssaps_read_request_callbacks(uint8_t serverId, uint16_t connId, ssaps_req_read_cb_t *read_cb_para,
                                  errcode_t status);
void ssaps_write_request_callbacks(uint8_t serverId, uint16_t connId, ssaps_req_write_cb_t *write_cb_para,
                                   errcode_t status);
errcode_t sle_server_init();
errcode_t sle_enable_server_cbk(void);

uint8_t g_sle_rx_data[256];
bool g_sle_new_data_ready = false;

static uint16_t SleSetAdvLocalName(uint8_t *advData, uint16_t maxLen)
{
    errno_t ret;
    uint8_t index = 0;

    uint8_t *localName = g_sleLocalName;
    uint8_t localNameLen = sizeof(g_sleLocalName) - 1;
    printf("%s localNameLen = %d\r\n", SLE_SERVER_LOG, localNameLen);
    printf("%s local_name: ", SLE_SERVER_LOG);
    for (uint8_t i = 0; i < localNameLen; i++)
    {
        printf("0x%02x ", localName[i]);
    }
    printf("\r\n");
    advData[index++] = localNameLen + 1;
    advData[index++] = SLE_ADV_DATA_TYPE_COMPLETE_LOCAL_NAME;
    ret = memcpy_s(&advData[index], maxLen - index, localName, localNameLen);
    if (ret != EOK)
    {
        printf("%s memcpy fail\r\n", SLE_SERVER_LOG);
        return 0;
    }
    return (uint16_t)index + localNameLen;
}

static uint16_t SleSetAdvData(uint8_t *advData)
{
    size_t len = 0;
    uint16_t idx = 0;
    errno_t ret = 0;

    len = sizeof(struct SleAdvCommonValue);
    struct SleAdvCommonValue advDiscLevel = {
        .length = len - 1,
        .type = SLE_ADV_DATA_TYPE_DISCOVERY_LEVEL,
        .value = SLE_ANNOUNCE_LEVEL_NORMAL,
    };
    ret = memcpy_s(&advData[idx], SLE_ADV_DATA_LEN_MAX - idx, &advDiscLevel, len);
    if (ret != EOK)
    {
        printf("%s adv_disc_level memcpy fail\r\n", SLE_SERVER_LOG);
        return 0;
    }
    idx += len;

    len = sizeof(struct SleAdvCommonValue);
    struct SleAdvCommonValue advAccessMode = {
        .length = len - 1,
        .type = SLE_ADV_DATA_TYPE_ACCESS_MODE,
        .value = 0,
    };
    ret = memcpy_s(&advData[idx], SLE_ADV_DATA_LEN_MAX - idx, &advAccessMode, len);
    if (ret != EOK)
    {
        printf("%s adv_access_mode memcpy fail\r\n", SLE_SERVER_LOG);
        return 0;
    }
    idx += len;

    return idx;
}

static uint16_t SleSetScanResponseData(uint8_t *scanRspData)
{
    uint16_t idx = 0;
    errno_t ret;
    size_t scanRspDataLen = sizeof(struct SleAdvCommonValue);

    struct SleAdvCommonValue txPowerLevel = {
        .length = scanRspDataLen - 1,
        .type = SLE_ADV_DATA_TYPE_TX_POWER_LEVEL,
        .value = SLE_ADV_TX_POWER,
    };
    ret = memcpy_s(scanRspData, SLE_ADV_DATA_LEN_MAX, &txPowerLevel, scanRspDataLen);
    if (ret != EOK)
    {
        printf("%s sle scan response data memcpy fail\r\n", SLE_SERVER_LOG);
        return 0;
    }
    idx += scanRspDataLen;

    /* set local name */
    idx += SleSetAdvLocalName(&scanRspData[idx], SLE_ADV_DATA_LEN_MAX - idx);
    return idx;
}

static int SleSetDefaultAnnounceParam(void)
{
    errno_t ret;
    SleAnnounceParam param = {0};
    uint8_t index;
    unsigned char local_addr[SLE_ADDR_LEN] = {0x78, 0x70, 0x60, 0x88, 0x96, 0x45};
    param.announceMode = SLE_ANNOUNCE_MODE_CONNECTABLE_SCANABLE;
    param.announceHandle = SLE_ADV_HANDLE_DEFAULT;
    param.announceGtRole = SLE_ANNOUNCE_ROLE_T_CAN_NEGO;
    param.announceLevel = SLE_ANNOUNCE_LEVEL_NORMAL;
    param.announceChannelMap = SLE_ADV_CHANNEL_MAP_DEFAULT;
    param.announceIntervalMin = SLE_ADV_INTERVAL_MIN_DEFAULT;
    param.announceIntervalMax = SLE_ADV_INTERVAL_MAX_DEFAULT;
    param.connIntervalMin = SLE_CONN_INTV_MIN_DEFAULT;
    param.connIntervalMax = SLE_CONN_INTV_MAX_DEFAULT;
    param.connMaxLatency = SLE_CONN_MAX_LATENCY;
    param.connSupervisionTimeout = SLE_CONN_SUPERVISION_TIMEOUT_DEFAULT;
    param.ownAddr.type = 0;
    ret = memcpy_s(param.ownAddr.addr, SLE_ADDR_LEN, local_addr, SLE_ADDR_LEN);
    if (ret != EOK)
    {
        printf("%s SleSetDefaultAnnounceParam data memcpy fail\r\n", SLE_SERVER_LOG);
        return 0;
    }
    printf("%s sle_local addr: ", SLE_SERVER_LOG);
    for (index = 0; index < SLE_ADDR_LEN; index++)
    {
        printf("0x%02x ", param.ownAddr.addr[index]);
    }
    printf("\r\n");
    return SleSetAnnounceParam(param.announceHandle, &param);
}

static int SleSetDefaultAnnounceData(void)
{
    errcode_t ret;
    uint8_t announceDataLen = 0;
    uint8_t seekDataLen = 0;
    SleAnnounceData data = {0};
    uint8_t advHandle = SLE_ADV_HANDLE_DEFAULT;
    uint8_t announceData[SLE_ADV_DATA_LEN_MAX] = {0};
    uint8_t seekRspData[SLE_ADV_DATA_LEN_MAX] = {0};
    uint8_t dataIndex = 0;

    announceDataLen = SleSetAdvData(announceData);
    data.announceData = announceData;
    data.announceDataLen = announceDataLen;

    printf("%s data.announce_data_len = %d\r\n", SLE_SERVER_LOG, data.announceDataLen);
    printf("%s data.announce_data: ", SLE_SERVER_LOG);
    for (dataIndex = 0; dataIndex < data.announceDataLen; dataIndex++)
    {
        printf("0x%02x ", data.announceData[dataIndex]);
    }
    printf("\r\n");

    seekDataLen = SleSetScanResponseData(seekRspData);
    data.seekRspData = seekRspData;
    data.seekRspDataLen = seekDataLen;

    printf("%s data.seek_rsp_data_len = %d\r\n", SLE_SERVER_LOG, data.seekRspDataLen);
    printf("%s data.seek_rsp_data: ", SLE_SERVER_LOG);
    for (dataIndex = 0; dataIndex < data.seekRspDataLen; dataIndex++)
    {
        printf("0x%02x ", data.seekRspData[dataIndex]);
    }
    printf("\r\n");

    ret = SleSetAnnounceData(advHandle, &data);
    if (ret == ERRCODE_SLE_SUCCESS)
    {
        printf("%s set announce data success.\r\n", SLE_SERVER_LOG);
    }
    else
    {
        printf("%s set adv param fail.\r\n", SLE_SERVER_LOG);
    }
    return ERRCODE_SLE_SUCCESS;
}

static void sle_announce_enable_cbk(uint32_t announceId, errcode_t status)
{
    printf("%s sle announce enable callback id:%02x, state:%x\r\n", SLE_SERVER_LOG, announceId, status);
}

static void sle_announce_disable_cbk(uint32_t announceId, errcode_t status)
{
    printf("%s sle announce disable callback id:%02x, state:%x\r\n", SLE_SERVER_LOG, announceId, status);
}

static void SleAnnounceTerminalCbk(uint32_t announceId)
{
    printf("%s sle announce terminal callback id:%02x\r\n", SLE_SERVER_LOG, announceId);
}

static void sle_enable_cbk(errcode_t status)
{
    printf("%s sle enable callback status:%x\r\n", SLE_SERVER_LOG, status);
    osal_msleep(SLE_SERVER_INIT_DELAY_MS);
    sle_enable_server_cbk();
}

errcode_t sle_announce_register_cbks(void)
{
    errcode_t ret;
    SleAnnounceSeekCallbacks seek_cbks = {0};
    seek_cbks.sleAnnounceEnableCb = sle_announce_enable_cbk;
    seek_cbks.sleAnnounceDisableCb = sle_announce_disable_cbk;
    seek_cbks.sleAnnounceTerminalCb = SleAnnounceTerminalCbk;
    seek_cbks.sleEnableCb = sle_enable_cbk;
    ret = SleAnnounceSeekRegisterCallbacks(&seek_cbks);
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle_announce_register_cbks,register_callbacks fail :%x\r\n", SLE_SERVER_LOG, ret);
        return ret;
    }
    return ERRCODE_SLE_SUCCESS;
}

errcode_t sle_server_adv_init(void)
{
    errcode_t ret;
    SleAddr addr;
    unsigned char local_addr[SLE_ADDR_LEN] = {0x78, 0x70, 0x60, 0x88, 0x96, 0x46};
    addr.type = 0;
    memcpy_s(&addr, SLE_ADDR_LEN, local_addr, SLE_ADDR_LEN);
    SleSetLocalAddr(&addr);
    SleSetDefaultAnnounceParam();
    SleSetDefaultAnnounceData();
    ret = SleStartAnnounce(SLE_ADV_HANDLE_DEFAULT);
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle_server_adv_init,sle_start_announce fail :%x\r\n", SLE_SERVER_LOG, ret);
        return ret;
    }
    return ERRCODE_SLE_SUCCESS;
}

static void Encode2byteLittle(uint8_t *ptr, uint16_t data)
{
    *(ptr + 1) = (uint8_t)(data >> 8); // 高字节
    *ptr = (uint8_t)data;              // 低字节
}

static void sle_uuid_set_base(SleUuid *out)
{
    errcode_t ret;
    // 复制128位基础UUID
    ret = memcpy_s(out->uuid, SLE_UUID_LEN, g_sleBase, SLE_UUID_LEN);
    if (ret != EOK)
    {
        printf("%s sle_uuid_set_base memcpy fail\n", SLE_SERVER_LOG);
        out->len = 0;
        return;
    }
    out->len = UUID_LEN_2; // 默认长度为2字节
}

static void sle_uuid_setu2(uint16_t u2, SleUuid *out)
{
    sle_uuid_set_base(out); // 设置基础UUID
    out->len = UUID_LEN_2;  // 长度为2字节
    // 将16位UUID编码到基础UUID的指定位置
    Encode2byteLittle(&out->uuid[UUID_INDEX], u2);
}

static void sle_uuid_print(SleUuid *uuid)
{
    if (uuid == NULL)
    {
        printf("%suuid_print, uuid is null\r\n", SLE_SERVER_LOG);
        return;
    }

    if (uuid->len == UUID_16BIT_LEN)
    {
        // 打印16位UUID
        printf("%s uuid: %02x %02x.\n", SLE_SERVER_LOG, uuid->uuid[14], uuid->uuid[15]);
    }
    else if (uuid->len == UUID_128BIT_LEN)
    {
        // 打印128位UUID
        printf("%s uuid: \n", SLE_SERVER_LOG);
        printf("%s 0x%02x 0x%02x 0x%02x 0x%02x\n", SLE_SERVER_LOG, uuid->uuid[0], uuid->uuid[1], uuid->uuid[2],
               uuid->uuid[3]);
        printf("%s 0x%02x 0x%02x 0x%02x 0x%02x\n", SLE_SERVER_LOG, uuid->uuid[4], uuid->uuid[5], uuid->uuid[6],
               uuid->uuid[7]);
        printf("%s 0x%02x 0x%02x 0x%02x 0x%02x\n", SLE_SERVER_LOG, uuid->uuid[8], uuid->uuid[9], uuid->uuid[10],
               uuid->uuid[11]);
        printf("%s 0x%02x 0x%02x 0x%02x 0x%02x\n", SLE_SERVER_LOG, uuid->uuid[12], uuid->uuid[13], uuid->uuid[14],
               uuid->uuid[15]);
    }
}

static void ssaps_mtu_changed_cbk(uint8_t serverId, uint16_t connId, SsapcExchangeInfo *mtu_size, errcode_t status)
{
    printf("%s ssaps_mtu_changed_cbk callback server_id:%x, conn_id:%x, mtu_size:%x, status:%x\r\n", SLE_SERVER_LOG,
           serverId, connId, mtu_size->mtuSize, status);
    if (g_slePairHdl == 0)
    {
        g_slePairHdl = connId + 1; // 保存配对句柄
    }
}

static void ssaps_start_service_cbk(uint8_t serverId, uint16_t handle, errcode_t status)
{
    printf("%s start service cbk callback server_id:%d, handle:%x, status:%x\r\n", SLE_SERVER_LOG, serverId, handle,
           status);
}

static void ssaps_add_service_cbk(uint8_t serverId, SleUuid *uuid, uint16_t handle, errcode_t status)
{
    printf("%s add service cbk callback server_id:%x, handle:%x, status:%x\r\n", SLE_SERVER_LOG, serverId, handle,
           status);
    sle_uuid_print(uuid); // 打印UUID
}

static void ssaps_add_property_cbk(uint8_t serverId, SleUuid *uuid, uint16_t serviceHandle, uint16_t handle,
                                   errcode_t status)
{
    printf("%s add property cbk callback server_id:%x, service_handle:%x,handle:%x, status:%x\r\n", SLE_SERVER_LOG,
           serverId, serviceHandle, handle, status);
    sle_uuid_print(uuid); // 打印UUID
}

static void ssaps_add_descriptor_cbk(uint8_t serverId, SleUuid *uuid, uint16_t serviceHandle, uint16_t propertyHandle,
                                     errcode_t status)
{
    printf("%s add descriptor cbk callback server_id:%x, service_handle:%x, property_handle:%x, status:%x\r\n",
           SLE_SERVER_LOG, serverId, serviceHandle, propertyHandle, status);
    sle_uuid_print(uuid); // 打印UUID
}

static void ssaps_delete_all_service_cbk(uint8_t serverId, errcode_t status)
{
    printf("%s delete all service callback server_id:%x, status:%x\r\n", SLE_SERVER_LOG, serverId, status);
}

static errcode_t sle_ssaps_register_cbks(SsapsReadRequestCallback ssaps_read_callback,
                                         SsapsWriteRequestCallback ssaps_write_callback)
{
    errcode_t ret;
    SsapsCallbacks ssaps_cbk = {0};
    ssaps_cbk.addServiceCb = ssaps_add_service_cbk;              // 添加服务回调
    ssaps_cbk.addPropertyCb = ssaps_add_property_cbk;            // 添加属性回调
    ssaps_cbk.addDescriptorCb = ssaps_add_descriptor_cbk;        // 添加描述符回调
    ssaps_cbk.startServiceCb = ssaps_start_service_cbk;          // 启动服务回调
    ssaps_cbk.deleteAllServiceCb = ssaps_delete_all_service_cbk; // 删除服务回调
    ssaps_cbk.mtuChangedCb = ssaps_mtu_changed_cbk;              // MTU改变回调
    ssaps_cbk.readRequestCb = ssaps_read_callback;               // 读请求回调
    ssaps_cbk.writeRequestCb = ssaps_write_callback;             // 写请求回调

    // 注册回调函数
    ret = SsapsRegisterCallbacks(&ssaps_cbk);
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle_ssaps_register_cbks fail :%x\r\n", SLE_SERVER_LOG, ret);
        return ret;
    }
    return ERRCODE_SLE_SUCCESS;
}

static errcode_t sle_uuid_server_service_add(void)
{
    errcode_t ret;
    SleUuid service_uuid = {0};
    // 设置服务UUID
    sle_uuid_setu2(SLE_UUID_SERVER_SERVICE, &service_uuid);
    // 添加服务
    ret = SsapsAddServiceSync(g_serverId, &service_uuid, 1, &g_serviceHandle);
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle uuid add service fail, ret:%x\r\n", SLE_SERVER_LOG, ret);
        return ERRCODE_SLE_FAIL;
    }
    return ERRCODE_SLE_SUCCESS;
}

static errcode_t add_property_sync(void)
{
    errcode_t ret;
    SsapsPropertyInfo property = {0};
    property.permissions = SLE_UUID_TEST_PROPERTIES;                 // 属性权限
    property.operateIndication = SLE_UUID_TEST_OPERATION_INDICATION; // 操作指示
    // 设置属性UUID
    sle_uuid_setu2(SLE_UUID_SERVER_NTF_REPORT, &property.uuid);
    property.valueLen = OCTET_BIT_LEN; // 属性值长度
    // 分配内存存储属性值
    property.value = (uint8_t *)osal_vmalloc(sizeof(g_slePropertyValue));
    if (property.value == NULL)
    {
        osal_vfree(property.value);
        return ERRCODE_SLE_FAIL;
    }
    // 复制属性初始值
    if (memcpy_s(property.value, sizeof(g_slePropertyValue), g_slePropertyValue, sizeof(g_slePropertyValue)) != EOK)
    {
        osal_vfree(property.value);
        return ERRCODE_SLE_FAIL;
    }
    // 添加属性
    ret = SsapsAddPropertySync(g_serverId, g_serviceHandle, &property, &g_propertyHandle);
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle add property fail, ret:%x\r\n", SLE_SERVER_LOG, ret);
        osal_vfree(property.value);
        return ERRCODE_SLE_FAIL;
    }
    osal_vfree(property.value); // 释放内存
    return ERRCODE_SLE_SUCCESS;
}

static errcode_t sle_uuid_server_property_add(void)
{
    errcode_t ret;
    SsapsDescInfo descriptor = {0};
    uint8_t ntfValue[] = {0x01, 0x02}; // 描述符值
    add_property_sync();               // 添加属性

    descriptor.permissions = SLE_UUID_TEST_DESCRIPTOR;                 // 描述符权限
    descriptor.type = SSAP_DESCRIPTOR_CLIENT_CONFIGURATION;            // 描述符类型
    descriptor.operateIndication = SLE_UUID_TEST_OPERATION_INDICATION; // 操作指示
    // 分配内存存储描述符值
    descriptor.value = (uint8_t *)osal_vmalloc(sizeof(ntfValue));
    if (descriptor.value == NULL)
    {
        return ERRCODE_SLE_FAIL;
    }
    // 复制描述符值
    if (memcpy_s(descriptor.value, sizeof(ntfValue), ntfValue, sizeof(ntfValue)) != EOK)
    {
        osal_vfree(descriptor.value);
        return ERRCODE_SLE_FAIL;
    }
    // 添加描述符
    ret = SsapsAddDescriptorSync(g_serverId, g_serviceHandle, g_propertyHandle, &descriptor);
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle add descriptor fail, ret:%x\r\n", SLE_SERVER_LOG, ret);
        osal_vfree(descriptor.value);
        return ERRCODE_SLE_FAIL;
    }
    osal_vfree(descriptor.value); // 释放内存
    return ERRCODE_SLE_SUCCESS;
}

static errcode_t sle_server_add(void)
{
    errcode_t ret;
    SleUuid app_uuid = {0};
    printf("%s sle add service in\r\n", SLE_SERVER_LOG);
    app_uuid.len = sizeof(g_sleUuidAppUuid);
    // 复制应用UUID
    if (memcpy_s(app_uuid.uuid, app_uuid.len, g_sleUuidAppUuid, sizeof(g_sleUuidAppUuid)) != EOK)
    {
        return ERRCODE_SLE_FAIL;
    }
    // 注册服务器
    SsapsRegisterServer(&app_uuid, &g_serverId);
    // 添加服务
    if (sle_uuid_server_service_add() != ERRCODE_SLE_SUCCESS)
    {
        SsapsUnregisterServer(g_serverId); // 注册失败则注销服务器
        return ERRCODE_SLE_FAIL;
    }
    // 添加属性和描述符
    if (sle_uuid_server_property_add() != ERRCODE_SLE_SUCCESS)
    {
        SsapsUnregisterServer(g_serverId); // 注册失败则注销服务器
        return ERRCODE_SLE_FAIL;
    }
    printf("%s sle add service, server_id:%x, service_handle:%x, property_handle:%x\r\n", SLE_SERVER_LOG, g_serverId,
           g_serviceHandle, g_propertyHandle);
    // 启动服务
    ret = SsapsStartService(g_serverId, g_serviceHandle);
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle add service fail, ret:%x\r\n", SLE_SERVER_LOG, ret);
        return ERRCODE_SLE_FAIL;
    }
    printf("%s sle add service out\r\n", SLE_SERVER_LOG);
    return ERRCODE_SLE_SUCCESS;
}

errcode_t sle_server_send_report_by_handle(const uint8_t *data, uint8_t len)
{
    SsapsNtfInd param = {0};

    param.handle = g_propertyHandle;       // 属性句柄
    param.type = SSAP_PROPERTY_TYPE_VALUE; // 属性类型
    param.value = g_receiveBuf;            // 数据缓冲区
    param.valueLen = len + 1;              // 数据长度（+1可能是为了兼容某些协议）
    // 复制数据
    if (memcpy_s(param.value, param.valueLen, data, len) != EOK)
    {
        return ERRCODE_SLE_FAIL;
    }
    // 发送通知
    return SsapsNotifyIndicate(g_serverId, g_sleConnHdl, &param);
}

static void sle_connect_state_changed_cbk(uint16_t connId, const SleAddr *addr, SleAcbStateType conn_state,
                                          SlePairStateType pair_state, SleDiscReasonType disc_reason)
{
    printf("%s connect state changed callback conn_id:0x%02x, conn_state:0x%x, pair_state:0x%x, disc_reason:0x%x\r\n",
           SLE_SERVER_LOG, connId, conn_state, pair_state, disc_reason);
    printf("%s connect state changed callback addr:%02x:**:**:**:%02x:%02x\r\n", SLE_SERVER_LOG, addr->addr[BT_INDEX_0],
           addr->addr[BT_INDEX_4], addr->addr[BT_INDEX_5]);

    if (conn_state == OH_SLE_ACB_STATE_CONNECTED)
    {
        g_sleConnHdl = connId; // 保存连接句柄
        // 设置MTU大小
        ssap_exchange_info_t parameter = {0};
        parameter.mtu_size = SLE_MTU_SIZE_DEFAULT;
        parameter.version = 1;
        ssaps_set_info(g_serverId, &parameter);
    }
    else if (conn_state == OH_SLE_ACB_STATE_DISCONNECTED)
    {
        g_sleConnHdl = 0;                         // 重置连接句柄
        g_slePairHdl = 0;                         // 重置配对句柄
        SleStartAnnounce(SLE_ADV_HANDLE_DEFAULT); // 重新开始广播
    }
}

static void sle_pair_complete_cbk(uint16_t connId, const SleAddr *addr, errcode_t status)
{
    printf("%s pair complete conn_id:%02x, status:%x\r\n", SLE_SERVER_LOG, connId, status);
    printf("%s pair complete addr: %02x:**:**:**: %02x: %02x\r\n", SLE_SERVER_LOG, addr->addr[BT_INDEX_0],
           addr->addr[BT_INDEX_4], addr->addr[BT_INDEX_5]);
    g_slePairHdl = connId + 1; // 保存配对句柄
}

static errcode_t sle_conn_register_cbks(void)
{
    errcode_t ret;
    SleConnectionCallbacks conn_cbks = {0};
    conn_cbks.connectStateChangedCb = sle_connect_state_changed_cbk; // 连接状态改变回调
    conn_cbks.pairCompleteCb = sle_pair_complete_cbk;                // 配对完成回调

    // 注册回调函数
    ret = SleConnectionRegisterCallbacks(&conn_cbks);
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle_conn_register_cbks fail :%x\r\n", SLE_SERVER_LOG, ret);
        return ret;
    }
    return ERRCODE_SLE_SUCCESS;
}

void ssaps_read_request_callbacks(uint8_t serverId, uint16_t connId, ssaps_req_read_cb_t *read_cb_para,
                                  errcode_t status)
{
    (void)serverId;
    (void)connId;
    (void)read_cb_para;
    (void)status;
    // 此处可添加读请求处理逻辑
}

void ssaps_write_request_callbacks(uint8_t serverId, uint16_t connId, ssaps_req_write_cb_t *write_cb_para,
                                   errcode_t status)
{
    (void)serverId;
    (void)connId;
    (void)status;
    
    // 打印接收到的原始数据（十六进制格式）
    printf("%s Received %d bytes from SLE client: ", SLE_SERVER_LOG, write_cb_para->length);
    for (int i = 0; i < write_cb_para->length; i++)
    {
        printf("0x%02X ", write_cb_para->value[i]);
    }
    printf("\r\n");
    
    // 尝试以字符串形式打印（添加结束符）
    char temp_buf[256] = {0};
    if (write_cb_para->length < 255)
    {
        memcpy(temp_buf, write_cb_para->value, write_cb_para->length);
        temp_buf[write_cb_para->length] = '\0';
        printf("%s As string: [%s]\r\n", SLE_SERVER_LOG, temp_buf);
    }
    
    // 保存接收到的数据
    if (write_cb_para->length <= sizeof(g_sle_rx_data))
    {
        memcpy(g_sle_rx_data, write_cb_para->value, write_cb_para->length);
        g_sle_new_data_ready = true;
    }
    
    // 暂时注释掉 UART 转发，先验证 SLE 通信是否正常
    /*
    // 通过UART将SLE接收到的数据转发给SU-03T语音模块
    if (write_cb_para->length > 0 && write_cb_para->length <= 256)
    {
        if (write_cb_para->value[0] == 0xAA && write_cb_para->value[1] == 0x55)
        {
            printf("%s Valid SU-03T frame detected, forwarding to UART%d...\r\n", 
                   SLE_SERVER_LOG, SU03T_UART_PORT);
            
            int32_t ret = IoTUartWrite(SU03T_UART_PORT, write_cb_para->value, write_cb_para->length);
            if (ret == 0)
            {
                printf("%s ✅ Successfully sent %d bytes to SU-03T via UART\r\n", 
                       SLE_SERVER_LOG, write_cb_para->length);
            }
            else
            {
                printf("%s ❌ Failed to send to UART, error code: %d\r\n", 
                       SLE_SERVER_LOG, ret);
            }
        }
    }
    */
}

errcode_t sle_server_init()
{
    errcode_t ret;
    
    // 初始化UART用于SU-03T语音模块通信
    IotUartAttribute uart_attr = {
        .baudRate = SU03T_UART_BAUDRATE,  // 波特率9600
        .dataBits = 8,                     // 8位数据位
        .stopBits = 1,                     // 1位停止位
        .parity = 0,                       // 无校验
    };
    
    ret = IoTUartInit(SU03T_UART_PORT, &uart_attr);
    if (ret != 0)
    {
        printf("%s ⚠️ Failed to init UART%d for SU-03T, error: %d\r\n", 
               SLE_SERVER_LOG, SU03T_UART_PORT, ret);
        // UART初始化失败不阻断SLE服务启动，继续执行
    }
    else
    {
        printf("%s ✅ UART%d initialized for SU-03T (baudrate: %d)\r\n", 
               SLE_SERVER_LOG, SU03T_UART_PORT, SU03T_UART_BAUDRATE);
    }
    
    // 注册广播回调函数
    ret = sle_announce_register_cbks();
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle_server_init, sle_announce_register_cbks fail :%x\r\n", SLE_SERVER_LOG, ret);
        return ret;
    }
    // 注册连接回调函数
    ret = sle_conn_register_cbks();
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle_server_init, sle_conn_register_cbks fail :%x\r\n", SLE_SERVER_LOG, ret);
        return ret;
    }
    // 注册SSAP回调函数
    ret = sle_ssaps_register_cbks(ssaps_read_request_callbacks, ssaps_write_request_callbacks);
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle_server_init, sle_ssaps_register_cbks fail :%x\r\n", SLE_SERVER_LOG, ret);
        return ret;
    }
    // 启用SLE
    ret = EnableSle();
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle_server_init, enable_sle fail :%x\r\n", SLE_SERVER_LOG, ret);
        return ret;
    }
    printf("%s init ok\r\n", SLE_SERVER_LOG);
    return ERRCODE_SLE_SUCCESS;
}

errcode_t sle_enable_server_cbk(void)
{
    errcode_t ret;
    // 添加服务器
    ret = sle_server_add();
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle_server_init, sle_server_add fail :%x\r\n", SLE_SERVER_LOG, ret);
        return ret;
    }
    // 初始化广播
    ret = sle_server_adv_init();
    if (ret != ERRCODE_SLE_SUCCESS)
    {
        printf("%s sle_server_init, sle_server_adv_init fail :%x\r\n", SLE_SERVER_LOG, ret);
        return ret;
    }
    return ERRCODE_SLE_SUCCESS;
}
