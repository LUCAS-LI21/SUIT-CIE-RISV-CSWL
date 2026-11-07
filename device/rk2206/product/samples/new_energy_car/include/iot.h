#include <stdbool.h>
#include "MQTTClient.h"

typedef struct
{
    int AirConditionT; // 空调温度
    bool AirConditionStatus; // 空调状态
    bool WindowsStatus;
    bool TrunkStaus;
    bool CarLockStaus;
    bool LEDStatus;
} IotData;

void GetMqttMsg(MessageData *data);
void MQTT_Init();
unsigned int MqttConnectStatus();
void SendMsg(IotData *iot_data);
int WaitMsg();