/*
 * Copyright (c) 2024 iSoftStone Education Co., Ltd.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <stdio.h>
#include <stdlib.h>

#include "MQTTClient.h"
#include "cJSON.h"
#include "cmsis_os2.h"
#include "config_network.h"
#include "iot.h"
#include "los_task.h"
#include "ohos_init.h"

#define MQTT_DEVICES_PWD "YOUR_PASSWORD"

#define HOST_ADDR "YOUR_SERVER_IP"

#define DEVICE_ID "YOUR_DEVICE_ID"

#define PUBLISH_TOPIC "$oc/devices/" DEVICE_ID "/sys/properties/report"
#define SUBCRIB_TOPIC                                                          \
  "$oc/devices/" DEVICE_ID "/sys/commands/+" /// request_id={request_id}"
#define RESPONSE_TOPIC                                                         \
  "$oc/devices/" DEVICE_ID "/sys/commands/response" /// request_id={request_id}"

#define MAX_BUFFER_LENGTH 512
#define MAX_STRING_LENGTH 64

static unsigned char sendBuf[MAX_BUFFER_LENGTH];
static unsigned char readBuf[MAX_BUFFER_LENGTH];

Network network;
MQTTClient client;

static unsigned int mqttConnectFlag = 0;

extern bool alarm_light_state;
extern bool beep_state;
extern bool auto_state;

/***************************************************************
* 函数名称: send_msg_to_mqtt
* 说    明: 发送信息到iot
* 参    数: e_iot_data *iot_data：数据
* 返 回 值: 无
***************************************************************/
void send_msg_to_mqtt(e_iot_data *iot_data) {
  int rc;
  MQTTMessage message;
  char *publishtopic = PUBLISH_TOPIC;
  char payload[MAX_BUFFER_LENGTH] = {0};
  char str[MAX_STRING_LENGTH] = {0};

  if (mqttConnectFlag == 0) {
    printf("mqtt not connect\n");
    return;
  }

  cJSON *root = cJSON_CreateObject();
  if (root != NULL) {
    cJSON *serv_arr = cJSON_AddArrayToObject(root, "services");
    cJSON *arr_item = cJSON_CreateObject();
    cJSON_AddStringToObject(arr_item, "service_id", "smart_security");
    cJSON *pro_obj = cJSON_CreateObject();
    cJSON_AddItemToObject(arr_item, "properties", pro_obj);

    memset(str, 0, MAX_BUFFER_LENGTH);
    // 烟雾浓度
    sprintf(str, "%5.2fppm", iot_data->smoke);
    cJSON_AddStringToObject(pro_obj, "smoke", str);
    // 人体感应
    if (iot_data->body == true) {
      cJSON_AddStringToObject(pro_obj, "body_state", "有人");
    } else {
      cJSON_AddStringToObject(pro_obj, "body_state", "无人");
    }
    // 警报灯状态
    if (iot_data->alarm_light_state == true) {
      cJSON_AddStringToObject(pro_obj, "alarm_light_state", "开启");
    } else {
      cJSON_AddStringToObject(pro_obj, "alarm_light_state", "关闭");
    }
    // 蜂鸣器状态
    if (iot_data->beep_state == true) {
      cJSON_AddStringToObject(pro_obj, "beep_state", "开启");
    } else {
      cJSON_AddStringToObject(pro_obj, "beep_state", "关闭");
    }
    // 自动状态模式
    if (iot_data->auto_state == true) {
      cJSON_AddStringToObject(pro_obj, "auto_state", "开启");
    } else {
      cJSON_AddStringToObject(pro_obj, "auto_state", "关闭");
    }
    
    cJSON_AddItemToArray(serv_arr, arr_item);

    char *palyload_str = cJSON_PrintUnformatted(root);
    strcpy(payload, palyload_str);

    cJSON_free(palyload_str);
    cJSON_Delete(root);
  }

  message.qos = 0;
  message.retained = 0;
  message.payload = payload;
  message.payloadlen = strlen(payload);

  printf("%s\r\n", payload);

  if ((rc = MQTTPublish(&client, publishtopic, &message)) != 0) {
    printf("Return code from MQTT publish is %d\n", rc);
    mqttConnectFlag = 0;
  } else {
    // printf("mqtt publish success:%s\n", payload);
  }
}

/***************************************************************
* 函数名称: set_light_state
* 说    明: 设置灯状态
* 参    数: cJSON *root
* 返 回 值: 无
***************************************************************/
void set_light_state(cJSON *root) {
  cJSON *para_obj = NULL;
  cJSON *status_obj = NULL;
  char *value = NULL;

  para_obj = cJSON_GetObjectItem(root, "paras");
  status_obj = cJSON_GetObjectItem(para_obj, "alarm_light_state");
  if (status_obj != NULL) {
    value = cJSON_GetStringValue(status_obj);
    if (!strcmp(value, "on")) {
      alarm_light_state = true;
    } else if (!strcmp(value, "off")) {
      alarm_light_state = false;
    }
  }
}

/***************************************************************
* 函数名称: set_beep_state
* 说    明: 设置蜂鸣器状态
* 参    数: cJSON *root
* 返 回 值: 无
***************************************************************/
void set_beep_state(cJSON *root) {
  cJSON *para_obj = NULL;
  cJSON *status_obj = NULL;
  char *value = NULL;

  para_obj = cJSON_GetObjectItem(root, "paras");
  status_obj = cJSON_GetObjectItem(para_obj, "beep_state");
  if (status_obj != NULL) {
    value = cJSON_GetStringValue(status_obj);
    if (!strcmp(value, "on")) {
      beep_state = true;
    } else if (!strcmp(value, "off")) {
      beep_state = false;
    }
  }
}

/***************************************************************
* 函数名称: set_light_state
* 说    明: 设置自动模式状态
* 参    数: cJSON *root
* 返 回 值: 无
***************************************************************/
void set_auto_state(cJSON *root) {
  cJSON *para_obj = NULL;
  cJSON *status_obj = NULL;
  char *value = NULL;

  para_obj = cJSON_GetObjectItem(root, "paras");
  status_obj = cJSON_GetObjectItem(para_obj, "auto_state");
  if (status_obj != NULL) {
    value = cJSON_GetStringValue(status_obj);
    if (!strcmp(value, "on")) {
      auto_state = true;
    } else if (!strcmp(value, "off")) {
      auto_state = false;
    }
  }
}

/***************************************************************
* 函数名称: mqtt_message_arrived
* 说    明: 接收mqtt数据
* 参    数: MessageData *data
* 返 回 值: 无
***************************************************************/
void mqtt_message_arrived(MessageData *data) {
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
  sprintf(rsptopic, "%s/request_id=%s", RESPONSE_TOPIC, request_id);
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
  if ((rc = MQTTPublish(&client, rsptopic, &message)) != 0) {
    printf("Return code from MQTT publish is %d\n", rc);
    mqttConnectFlag = 0;
  }

  /*{"command_name":"cmd","paras":{"cmd_value":"1"},"service_id":"server"}*/
  root =
      cJSON_ParseWithLength(data->message->payload, data->message->payloadlen);
  if (root != NULL) {
    cmd_name = cJSON_GetObjectItem(root, "command_name");
    if (cmd_name != NULL) {
      cmd_name_str = cJSON_GetStringValue(cmd_name);
      if (!strcmp(cmd_name_str, "light")) {
        set_light_state(root);
      } else if (!strcmp(cmd_name_str, "beep")) {
        set_beep_state(root);
      } else if (!strcmp(cmd_name_str, "auto")) {
        set_auto_state(root);
      }
    }
  }

  cJSON_Delete(root);
}

/***************************************************************
* 函数名称: wait_message
* 说    明: 等待信息
* 参    数: 无
* 返 回 值: 无
***************************************************************/
int wait_message() {
  uint8_t rec = MQTTYield(&client, 5000);
  if (rec != 0) {
    mqttConnectFlag = 0;
  }
  if (mqttConnectFlag == 0) {
    return 0;
  }
  return 1;
}

/***************************************************************
* 函数名称: mqtt_init
* 说    明: mqtt初始化
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void mqtt_init() {
  int rc;

  printf("Starting MQTT...\n");
  /*网络初始化*/
  NetworkInit(&network);

begin:
  /* 连接网络*/
  printf("NetworkConnect  ...\n");
  NetworkConnect(&network, HOST_ADDR, 1883);
  printf("MQTTClientInit  ...\n");
  /*MQTT客户端初始化*/
  MQTTClientInit(&client, &network, 2000, sendBuf, sizeof(sendBuf), readBuf,
                 sizeof(readBuf));

  MQTTString clientId = MQTTString_initializer;
  clientId.cstring = DEVICE_ID;

  MQTTString userName = MQTTString_initializer;
  userName.cstring = DEVICE_ID;

  MQTTString password = MQTTString_initializer;
  password.cstring = MQTT_DEVICES_PWD;

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
  if (rc != 0) {
    printf("MQTTConnect: %d\n", rc);
    NetworkDisconnect(&network);
    MQTTDisconnect(&client);
    osDelay(200);
    goto begin;
  }

  printf("MQTTSubscribe  ...\n");
  rc = MQTTSubscribe(&client, SUBCRIB_TOPIC, 0, mqtt_message_arrived);
  if (rc != 0) {
    printf("MQTTSubscribe: %d\n", rc);
    osDelay(200);
    goto begin;
  }

  mqttConnectFlag = 1;
}

/***************************************************************
* 函数名称: mqtt_is_connected
* 说    明: mqtt连接状态
* 参    数: 无
* 返 回 值: unsigned int 状态
***************************************************************/
unsigned int mqtt_is_connected() { return mqttConnectFlag; }
