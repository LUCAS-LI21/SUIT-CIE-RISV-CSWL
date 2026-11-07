#include <stdio.h>
#include <stdlib.h>

#include "iot.h"
#include "cJSON.h"
#include "MQTTClient.h"

#define MAX_BUFFER_LENGTH 512
#define MAX_STRING_LENGTH 64

// MQTT连接配置
#define PUB_TOPIC "$oc/devices/YOUR_DEVICE_ID/sys/properties/report"
#define SUB_TOPIC "$oc/devices/YOUR_DEVICE_ID/sys/commands/#"
#define RESPONE_TOPIC "$oc/devices/YOUR_DEVICE_ID/sys/commands/response"

#define USERNAME "YOUR_MQTT_USERNAME"
// #define PASSWORD "YOUR_PASSWORD"
#define PASSWORD "YOUR_PASSWORD"
#define CLIENT_ID "YOUR_MQTT_CLIENT_ID"
#define HOST_NAME "YOUR_DEVICE_HOST"
#define HOST_ADDR "YOUR_SERVER_IP"
#define PORT 8883

#define DEVICE_ID "YOUR_DEVICE_ID"

static unsigned int mqttConnectFlag = 0;
static char pub_topic[128] = PUB_TOPIC;
static char sub_topic[128] = SUB_TOPIC;
static char response_topic[128] = RESPONE_TOPIC;
static char device_id[128] = DEVICE_ID;

static unsigned char send_Msg[MAX_BUFFER_LENGTH];
static unsigned char read_Msg[MAX_BUFFER_LENGTH];

Network network;
MQTTClient client;

// 发送数据
void SendMsg(IotData *iot_data)
{
    int rc;
    MQTTMessage message;
    char payload[MAX_BUFFER_LENGTH] = {0};
    char str[MAX_STRING_LENGTH] = {0};

    if (mqttConnectFlag == 0)
    {
        printf("mqtt not connect\n");
        return;
    }

    cJSON *root = cJSON_CreateObject();
    if (root != NULL)
    {
        cJSON *serv_arr = cJSON_AddArrayToObject(root, "services");
        cJSON *arr_item = cJSON_CreateObject();
        cJSON_AddStringToObject(arr_item, "service_id", "NECcontrol");
        cJSON *pro_obj = cJSON_CreateObject();
        cJSON_AddItemToObject(arr_item, "properties", pro_obj);

        memset(str, 0, MAX_BUFFER_LENGTH);
        // 空调温度
        sprintf(str, "%d", iot_data->AirConditionT);
        cJSON_AddStringToObject(pro_obj, "AirConditionT", str);
        // 空调状态
        if (iot_data->AirConditionStatus == true)
        {
            cJSON_AddStringToObject(pro_obj, "AirConditionStatus", "ON");
        }
        else
        {
            cJSON_AddStringToObject(pro_obj, "AirConditionStatus", "OFF");
        }
        // 车灯状态
        if (iot_data->LEDStatus == true)
        {
            cJSON_AddStringToObject(pro_obj, "LEDStatus", "ON");
        }
        else
        {
            cJSON_AddStringToObject(pro_obj, "LEDStatus", "OFF");
        }
        // 车锁状态
        if (iot_data->CarLockStaus == true)
        {
            cJSON_AddStringToObject(pro_obj, "CarLockStaus", "ON");
        }
        else
        {
            cJSON_AddStringToObject(pro_obj, "CarLockStaus", "OFF");
        }
        // 车窗状态
        if (iot_data->WindowsStatus == true)
        {
            cJSON_AddStringToObject(pro_obj, "WindowsStatus", "ON");
        }
        else
        {
            cJSON_AddStringToObject(pro_obj, "WindowsStatus", "OFF");
        }
        // 后备箱状态
        if (iot_data->TrunkStaus == true)
        {
            cJSON_AddStringToObject(pro_obj, "TrunkStaus", "ON");
        }
        else
        {
            cJSON_AddStringToObject(pro_obj, "TrunkStaus", "OFF");
        }

        cJSON_AddItemToArray(serv_arr, arr_item);

        char *palyload_str = cJSON_PrintUnformatted(root);
        strcpy(payload, palyload_str);
        // printf("Send Msg %s\n", payload);

        cJSON_free(palyload_str);
        cJSON_Delete(root);
    }

    message.qos = 0;
    message.retained = 0;
    message.payload = payload;
    message.payloadlen = strlen(payload);

    sprintf(pub_topic, "$oc/devices/%s/sys/properties/report", device_id);
    if ((rc = MQTTPublish(&client, pub_topic, &message)) != 0)
    {
        printf("Return code from MQTT publish is %d\n", rc);
        mqttConnectFlag = 0;
    }
    else
    {
        printf("mqtt publish success:%s\n", payload);
    }
}

// 接受MQTT数据
void GetMqttMsg(MessageData *data)
{
    int rc;
    cJSON *root = NULL;
    cJSON *cmd_name = NULL;
    char *cmd_name_str = NULL;
    char *request_id_idx = NULL;
    char request_id[20] = {0};
    MQTTMessage message;
    char payload[MAX_BUFFER_LENGTH];

    char rsptopic[128] = {0};

    printf("Message arrived on topic %.*s: %.*s\n",
           data->topicName->lenstring.len, data->topicName->lenstring.data,
           data->message->payloadlen, data->message->payload);

    // get request id
    request_id_idx = strstr(data->topicName->lenstring.data, "request_id=");
    strncpy(request_id, request_id_idx + 11, 19);
    // printf("request_id = %s\n", request_id);

    // create response topic
    sprintf(response_topic, "$oc/devices/%s/sys/commands/response", device_id);
    sprintf(rsptopic, "%s/request_id=%s", response_topic, request_id);
    // printf("rsptopic = %s\n", rsptopic);

    // response message
    message.qos = 0;
    message.retained = 0;
    message.payload = payload;
    sprintf(payload, "{ \
    \"result_code\": 0, \
    \"response_name\": \"COMMAND_RESPONSE\", \
    \"paras\": { \
        \"result\": \"success\" \
    } \
    }");
    message.payloadlen = strlen(payload);

    // publish the msg to responese topic
    if ((rc = MQTTPublish(&client, rsptopic, &message)) != 0)
    {
        printf("Return code from MQTT publish is %d\n", rc);
        mqttConnectFlag = 0;
    }


    /*{"command_name":"cmd","paras":{"cmd_value":"1"},"service_id":"server"}*/
    root = cJSON_ParseWithLength(data->message->payload, data->message->payloadlen);
    printf("%s\n", root->child);
    // if (root != NULL)
    // {
    //     cmd_name = cJSON_GetObjectItem(root, "command_name");
    //     if (cmd_name != NULL)
    //     {
    //         cmd_name_str = cJSON_GetStringValue(cmd_name);
    //         if (!strcmp(cmd_name_str, "light_control"))
    //         {
    //             set_light_state(root);
    //         }
    //         else if (!strcmp(cmd_name_str, "motor_control"))
    //         {
    //             set_motor_state(root);
    //         }
    //         else if (!strcmp(cmd_name_str, "auto_control"))
    //         {
    //             set_auto_state(root);
    //         }
    //     }
    // }

    cJSON_Delete(root);
}

// 等待信息
int WaitMsg()
{
    uint8_t ret = MQTTYield(&client, 5000);
    if (ret != 0)
    {
        mqttConnectFlag = 0;
    }
    if (mqttConnectFlag == 0)
    {
        return 0;
    }
    return 1;
}

// MQTT初始化
void MQTT_Init()
{
    int rc;

    printf("Starting MQTT...\n");

    /*网络初始化*/
    NetworkInit(&network);

begin:
    /* 连接网络*/
    printf("NetworkConnect  ...\n");
    NetworkConnect(&network, HOST_ADDR, PORT);
    printf("MQTTClientInit  ...\n");
    /*MQTT客户端初始化*/
    MQTTClientInit(&client, &network, 2000, send_Msg, sizeof(send_Msg), read_Msg, sizeof(read_Msg));

    MQTTString clientId = MQTTString_initializer;
    clientId.cstring = device_id;

    MQTTString userName = MQTTString_initializer;
    userName.cstring = USERNAME;
    printf("userName: %s\n", userName.cstring);

    MQTTString password = MQTTString_initializer;
    password.cstring = PASSWORD;
    printf("passwor: %s\n", password.cstring);

    MQTTPacket_connectData data = MQTTPacket_connectData_initializer;
    data.clientID = clientId;
    data.username = userName;
    data.password = password;
    data.willFlag = 0;
    data.MQTTVersion = 4;
    data.keepAliveInterval = 60;
    data.cleansession = 1;

    printf("MQTTConnect  ...\n");
    rc = MQTTConnect(&client, &data);
    if (rc != 0)
    {
        printf("MQTTConnect: %d\n", rc);
        NetworkDisconnect(&network);
        MQTTDisconnect(&client);
        osDelay(200);
        goto begin;
    }

    printf("MQTTSubscribe  ...\n");
    sprintf(sub_topic, "$oc/devices/%s/sys/commands/#", device_id);
    rc = MQTTSubscribe(&client, sub_topic, 0, GetMqttMsg);
    if (rc != 0)
    {
        printf("MQTTSubscribe: %d\n", rc);
        osDelay(200);
        goto begin;
    }
    mqttConnectFlag = 1;
}

// mqtt链接状态
unsigned int MqttConnectStatus() { return mqttConnectFlag; }
