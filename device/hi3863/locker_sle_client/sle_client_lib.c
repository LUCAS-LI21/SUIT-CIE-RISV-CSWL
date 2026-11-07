#include "sle_client_lib.h"
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
#include "osal_debug.h"
#include "osal_task.h"
#include "pinctrl.h"
#include "securec.h"
#include "sle_connection_manager.h"
#include "sle_device_discovery.h"
#include "sle_errcode.h"
#include "sle_ssap_client.h"
#include "string.h"

// 客户端应用的128位UUID，用于在SLE协议中标识自己
static char g_sleUuidAppUuid[] = {0x39, 0xBE, 0xA8, 0x80, 0xFC, 0x70, 0x11, 0xEA,
                                  0xB7, 0x20, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
// 用于存储查找服务结果的结构体
static ssapc_find_service_result_t g_sle_find_service_result = {0};
// SLE扫描相关的回调函数结构体
static sle_announce_seek_callbacks_t g_sle_seek_cbk = {0};
// SLE连接相关的回调函数结构体
static SleConnectionCallbacks g_sle_connect_cbk = {0};
// SLE SSAP客户端相关的回调函数结构体
static ssapc_callbacks_t g_sle_ssapc_cbk = {0};
// 存储远程SLE服务端的地址信息
static SleAddr g_sle_remote_addr = {0};
// 用于SLE发送数据的参数结构体
ssapc_write_param_t g_sle_send_param = {0};
// 当前SLE连接的连接ID
uint16_t g_sle_conn_id = 0;
// 客户端ID，在注册时由SLE协议栈分配
uint8_t g_client_id = 0;
// AT指令解析相关的前一个字符（当前代码未使用）
static uint8_t g_at_pre_char = 0;
// AT指令接收计数（当前代码未使用）
static uint32_t g_at_recv_cnt = 0;

uint8_t g_sle_rx_data[256];
bool g_sle_new_data_ready = false;

uint8_t receive_buf[520] = {0};
uint16_t get_g_sle_conn_id(void)
{
    return g_sle_conn_id;
}

ssapc_write_param_t *get_g_sle_send_param(void)
{
    return &g_sle_send_param;
}

void SleStartScan(void)
{
    SleSeekParam param = {0}; // 扫描参数结构体

    param.ownaddrtype = 0;                             // 自身地址类型，0代表公共地址
    param.filterduplicates = 0;                        // 是否过滤重复设备，0表示不过滤
    param.seekfilterpolicy = 0;                        // 扫描过滤策略
    param.seekphys = 1;                                // 扫描物理通道，1代表LE物理通道
    param.seekType[0] = 1;                             // 扫描类型，1代表主动扫描
    param.seekInterval[0] = SLE_SEEK_INTERVAL_DEFAULT; // 扫描间隔
    param.seekWindow[0] = SLE_SEEK_WINDOW_DEFAULT;     // 扫描窗口

    SleSetSeekParam(&param); // 设置扫描参数
    SleStartSeek();          // 开始扫描
}

static void sle_client_sample_sle_enable_cbk(errcode_t status)
{
    if (status != 0)
    { // 如果启动失败
        printf("%s sle_client_sample_sle_enable_cbk,status error\r\n", SLE_CLIENT_LOG);
    }
    else
    {                                   // 如果启动成功
        osal_msleep(SLE_TASK_DELAY_MS); // 等待1秒，确保系统稳定
        SleStartScan();                 // 开始扫描SLE设备
    }
}

static void sle_client_sample_seek_enable_cbk(errcode_t status)
{
    if (status != 0)
    { // 如果开启失败
        printf("%s sle_client_sample_seek_enable_cbk, status error\r\n", SLE_CLIENT_LOG);
    }
}

static void sle_client_sample_seek_result_info_cbk(SleSeekResultInfo *seek_result_data)
{
    if (seek_result_data == NULL)
    { // 如果结果指针为空
        printf("%s status error: seek_result_data is NULL\r\n", SLE_CLIENT_LOG);
        return;
    }
    
    // 打印原始广播数据（十六进制）
    printf("%s scan result - addr: %02x:%02x:%02x:%02x:%02x:%02x, data_len: %d, data: ", 
           SLE_CLIENT_LOG,
           seek_result_data->addr.addr[5], seek_result_data->addr.addr[4], 
           seek_result_data->addr.addr[3], seek_result_data->addr.addr[2],
           seek_result_data->addr.addr[1], seek_result_data->addr.addr[0],
           seek_result_data->dataLength);
    
    for (int i = 0; i < seek_result_data->dataLength && i < 50; i++)
    {
        printf("%02x ", seek_result_data->data[i]);
    }
    printf("\r\n");
    
    // 尝试将数据转换为字符串进行匹配（确保有结束符）
    char temp_buf[256] = {0};
    if (seek_result_data->dataLength > 0 && seek_result_data->dataLength < 255)
    {
        memcpy_s(temp_buf, sizeof(temp_buf), seek_result_data->data, seek_result_data->dataLength);
        temp_buf[seek_result_data->dataLength] = '\0';
        
        printf("%s scan data as string: [%s]\r\n", SLE_CLIENT_LOG, temp_buf);
        printf("%s looking for: [%s]\r\n", SLE_CLIENT_LOG, SLE_SERVER_NAME);
        
        // 在广播数据中查找服务端名称
        char *match_result = strstr(temp_buf, SLE_SERVER_NAME);
        if (match_result != NULL)
        {
            printf("%s ✅ Found target server! Match at position: %ld\r\n", SLE_CLIENT_LOG, match_result - temp_buf);
            // 保存服务端的地址
            memcpy_s(&g_sle_remote_addr, sizeof(sle_addr_t), &seek_result_data->addr, sizeof(sle_addr_t));
            SleStopSeek(); // 停止扫描，因为已经找到了目标
        }
        else
        {
            printf("%s ❌ No match found. Expected: [%s], Got: [%s]\r\n", SLE_CLIENT_LOG, SLE_SERVER_NAME, temp_buf);
        }
    }
}

static void sle_client_sample_seek_disable_cbk(errcode_t status)
{
    if (status != 0)
    { // 如果关闭失败
        printf("%s sle_client_sample_seek_disable_cbk,status error = %x\r\n", SLE_CLIENT_LOG, status);
    }
    else
    {                                               // 如果关闭成功
        SleConnectRemoteDevice(&g_sle_remote_addr); // 尝试连接之前找到的服务端设备
    }
}

static void SleClientSampleSeekCbkRegister(void)
{
    g_sle_seek_cbk.sle_enable_cb = sle_client_sample_sle_enable_cbk;
    g_sle_seek_cbk.seek_enable_cb = sle_client_sample_seek_enable_cbk;
    g_sle_seek_cbk.seek_result_cb = sle_client_sample_seek_result_info_cbk;
    g_sle_seek_cbk.seek_disable_cb = sle_client_sample_seek_disable_cbk;
    sle_announce_seek_register_callbacks(&g_sle_seek_cbk);
}

static void sle_client_sample_connect_state_changed_cbk(uint16_t conn_id, const SleAddr *addr,
                                                        SleAcbStateType conn_state, SlePairStateType pair_state,
                                                        SleDiscReasonType disc_reason)
{
    unused(addr);       // 标记参数未使用，避免编译警告
    unused(pair_state); // 标记参数未使用

    printf("%s conn state changed disc_reason:0x%x\r\n", SLE_CLIENT_LOG, disc_reason);
    g_sle_conn_id = conn_id; // 更新当前的连接ID

    if (conn_state == SLE_ACB_STATE_CONNECTED)
    { // 如果连接成功建立
        printf("%s SLE_ACB_STATE_CONNECTED\r\n", SLE_CLIENT_LOG);
        // 准备MTU交换请求
        SsapcExchangeInfo info = {0};
        info.mtuSize = SLE_MTU_SIZE_DEFAULT;     // 请求的MTU大小
        info.version = 1;                        // 协议版本
        SsapcExchangeInfoReq(0, conn_id, &info); // 发送MTU交换请求

        SlePairRemoteDevice(addr); // 开始与远程设备配对
    }
    else if (conn_state == SLE_ACB_STATE_NONE)
    { // 如果连接状态为无
        printf("%s SLE_ACB_STATE_NONE\r\n", SLE_CLIENT_LOG);
    }
    else if (conn_state == SLE_ACB_STATE_DISCONNECTED)
    { // 如果连接断开
        printf("%s SLE_ACB_STATE_DISCONNECTED\r\n", SLE_CLIENT_LOG);
        SleRemovePairedRemoteDevice(addr); // 移除配对信息
        SleStartScan();                    // 重新开始扫描，尝试重连
    }
    else
    { // 其他未知状态
        printf("%s status error \r\n", SLE_CLIENT_LOG);
    }
}

static void SleClientSampleConnectCbkRegister(void)
{
    g_sle_connect_cbk.connectStateChangedCb = sle_client_sample_connect_state_changed_cbk;
    SleConnectionRegisterCallbacks(&g_sle_connect_cbk);
}

static void sle_client_sample_exchange_info_cbk(uint8_t client_id, uint16_t conn_id, ssap_exchange_info_t *param,
                                                errcode_t status)
{
    printf("%s exchange_info_cbk ,pair complete client id:%d status: %d\r\n", SLE_CLIENT_LOG, client_id, status);
    printf("%s exchange mtu, mtu size: %d, version: %d.\r\n", SLE_CLIENT_LOG, param->mtu_size, param->version);

    // 准备查找服务的请求
    ssapc_find_structure_param_t find_param = {0};
    find_param.type = 1;         // 查找类型，1代表查找服务
    find_param.start_hdl = 1;    // 开始句柄
    find_param.end_hdl = 0xFFFF; // 结束句柄，0xFFFF代表所有

    // 发送查找服务请求
    int ret = ssapc_find_structure(client_id, conn_id, &find_param);
    printf(" ssapc_find_structure_errcode: %d\r\n", ret);
}

static void sle_client_sample_find_structure_cbk(uint8_t client_id, uint16_t conn_id,
                                                 ssapc_find_service_result_t *service, errcode_t status)
{
    printf("%s find structure cbk client: %d conn_id:%d status: %d \r\n", SLE_CLIENT_LOG, client_id, conn_id, status);
    printf("%s find structure start_hdl: [0x %02x], end_hdl:[0x %02x], uuid len:%d\r\n", SLE_CLIENT_LOG,
           service->start_hdl, service->end_hdl, service->uuid.len);

    // 保存找到的服务的句柄范围和UUID
    g_sle_find_service_result.start_hdl = service->start_hdl;
    g_sle_find_service_result.end_hdl = service->end_hdl;
    memcpy_s(&g_sle_find_service_result.uuid, sizeof(sle_uuid_t), &service->uuid, sizeof(sle_uuid_t));
}

static void sle_client_sample_find_property_cbk(uint8_t client_id, uint16_t conn_id,
                                                ssapc_find_property_result_t *property, errcode_t status)
{
    printf("%s sle_client_sample_find_property_cbk, client id: %d, conn id: %d, operate ind: %d,"
           "descriptors count:%dstatus:%dproperty->handle %d\r\n",
           SLE_CLIENT_LOG, client_id, conn_id, property->operate_indication, property->descriptors_count, status,
           property->handle);

    // 保存用于发送数据的特性句柄
    g_sle_send_param.handle = property->handle;
    g_sle_send_param.type = SSAP_PROPERTY_TYPE_VALUE; // 特性类型为值
}

static void sle_client_sample_find_structure_cmp_cbk(uint8_t client_id, uint16_t conn_id,
                                                     ssapc_find_structure_result_t *structure_result, errcode_t status)
{
    unused(conn_id); // 标记参数未使用

    printf("%s sle_client_sample_find_structure_cmp_cbk,client id: %d status: %d type: %d uuid len: %d \r\n",
           SLE_CLIENT_LOG, client_id, status, structure_result->type, structure_result->uuid.len);
}

static void sle_client_sample_write_cfm_cb(uint8_t client_id, uint16_t conn_id, ssapc_write_result_t *write_result,
                                           errcode_t status)
{
    printf("%s sle_client_sample_write_cfm_cb, conn_id:%d client id:%d status:%d handle:%02x type:%02x\r\n",
           SLE_CLIENT_LOG, conn_id, client_id, status, write_result->handle, write_result->type);
}

static void sle_client_sample_ssapc_cbk_register(ssapc_notification_callback notification_cb,
                                                 ssapc_indication_callback indication_cb)
{
    g_sle_ssapc_cbk.exchange_info_cb = sle_client_sample_exchange_info_cbk;
    g_sle_ssapc_cbk.find_structure_cb = sle_client_sample_find_structure_cbk;
    g_sle_ssapc_cbk.ssapc_find_property_cbk = sle_client_sample_find_property_cbk;
    g_sle_ssapc_cbk.find_structure_cmp_cb = sle_client_sample_find_structure_cmp_cbk;
    g_sle_ssapc_cbk.write_cfm_cb = sle_client_sample_write_cfm_cb;
    g_sle_ssapc_cbk.notification_cb = notification_cb; // 注册接收服务端通知的回调
    g_sle_ssapc_cbk.indication_cb = indication_cb;     // 注册接收服务端指示的回调
    ssapc_register_callbacks(&g_sle_ssapc_cbk);
}

static errcode_t sle_uuid_client_register(void)
{
    errcode_t ret;
    SleUuid app_uuid = {0}; // 客户端UUID结构体

    printf("[uuid client] ssapc_register_client \r\n");
    app_uuid.len = sizeof(g_sleUuidAppUuid); // 设置UUID长度
    // 复制UUID值
    if (memcpy_s(app_uuid.uuid, app_uuid.len, g_sleUuidAppUuid, sizeof(g_sleUuidAppUuid)) != EOK)
    {
        return ERRCODE_SLE_FAIL;
    }
    // 注册客户端，获取客户端ID
    ret = SsapcRegisterClient(&app_uuid, &g_client_id);

    return ret;
}

errcode_t sle_client_send_report_by_handle(const uint8_t *data, uint8_t len)
{
    ssapc_write_param_t param = {0}; // 写请求参数结构体

    // 注意：这里存在一个潜在的问题，它使用了服务的起始句柄作为特性句柄，这通常不正确。
    // 正确的做法应该是使用在sle_client_sample_find_property_cbk中找到的特性句柄g_sle_send_param.handle。
    param.handle = g_sle_find_service_result.start_hdl;
    param.type = 0;           // 写类型，0通常代表写请求
    param.data = receive_buf; // 指向发送数据的缓冲区
    param.data_len = len + 1; // 数据长度（这里+1可能是一个bug）

    // 将数据复制到发送缓冲区
    if (memcpy_s(param.data, param.data_len, data, len) != EOK)
    {
        return ERRCODE_SLE_FAIL;
    }

    // 发送写请求
    int ret = SsapWriteReq(g_client_id, g_sle_conn_id, &param);
    return ret;
}

void ssapc_notification_callbacks(uint8_t client_id, uint16_t conn_id, ssapc_handle_value_t *data, errcode_t status)
{
    (void)client_id;
    (void)conn_id;
    (void)status;

    // 将数据的最后一个字节置为字符串结束符'\0'，以便用printf打印
    data->data[data->data_len - 1] = '\0';
    memcpy(g_sle_rx_data, data->data, data->data_len);
    g_sle_new_data_ready = true;
}

void ssapc_indication_callbacks(uint8_t client_id, uint16_t conn_id, ssapc_handle_value_t *data, errcode_t status)
{
    (void)client_id;
    (void)conn_id;
    (void)data;
    (void)status;
    // 此处可以添加处理服务端指示的逻辑
}

void SleClientInit()
{
    // 设置本地SLE地址（可选，如果芯片有多个地址或需要指定地址时使用）
    uint8_t local_addr[SLE_ADDR_LEN] = {0x13, 0x67, 0x5c, 0x07, 0x00, 0x51};
    SleAddr local_address;
    local_address.type = 0; // 地址类型
    (void)memcpy_s(local_address.addr, SLE_ADDR_LEN, local_addr, SLE_ADDR_LEN);

    sle_uuid_client_register();          // 注册SLE客户端
    SleClientSampleSeekCbkRegister();    // 注册扫描回调
    SleClientSampleConnectCbkRegister(); // 注册连接回调
    // 注册SSAP客户端回调，并指定通知和指示的处理函数
    sle_client_sample_ssapc_cbk_register(ssapc_notification_callbacks, ssapc_indication_callbacks);

    EnableSle(); // 启动SLE协议栈

    SleSetLocalAddr(&local_address); // 应用本地地址设置
}
