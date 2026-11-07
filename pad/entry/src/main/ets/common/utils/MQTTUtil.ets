import { MqttAsync, MqttClient, MqttMessage, MqttPublishOptions, MqttQos } from '@ohos/mqtt';
import { AppConstants } from '../constants/AppConstants';
import { hilog } from '@kit.PerformanceAnalysisKit';

const DOMAIN = 0x0001;
const TAG = 'MQTTUtil';

export default class MQTTUtil {
  private mqttAsyncClient: MqttClient | null = null;

  // 主动断开 MQTT 连接
  async disconnect(): Promise<void> {
    if (this.mqttAsyncClient) {
      try {
        await this.mqttAsyncClient.disconnect();
      } catch (e) {
        hilog.warn(DOMAIN, TAG, `[disconnect] 断开失败: %{public}s`, JSON.stringify(e));
      }
      this.mqttAsyncClient = null;
    }
  }

  // 连接并订阅 pet 主题（供宠物监护页面使用）
  async connectPet(callback: (err: Error, data: MqttMessage) => void, clientId: string = 'pet') {
    hilog.info(DOMAIN, TAG, `[connectPet] 开始连接 broker: %{public}s, clientId: %{public}s`, AppConstants.MQTT_URL, clientId);

    // 先断开旧连接
    if (this.mqttAsyncClient) {
      try {
        await this.mqttAsyncClient.disconnect();
      } catch (e) {
        hilog.warn(DOMAIN, TAG, `[connectPet] 断开旧连接失败: %{public}s`, JSON.stringify(e));
      }
      this.mqttAsyncClient = null;
    }

    //1、实例化 MqttClient
    this.mqttAsyncClient = MqttAsync.createMqtt({
      url: AppConstants.MQTT_URL,
      clientId: clientId,
      persistenceType: 1,
    });

    //2、连接
    // connectTimeout 单位是毫秒。原来是 300，公网 broker（119.29.133.108）
    // 在三个页面并发建连时经常超过 300 ms 直接判失败——日志只有「开始连接」
    // 没有「连接成功」，订阅也永远轮不到。放到 5 秒，失败再重试一次。
    let isConnect = await this.mqttAsyncClient.connect(
      {
        userName: AppConstants.MQTT_USER,
        password: AppConstants.MQTT_PASSWORD,
        connectTimeout: 5000
      }
    );
    if (!isConnect) {
      hilog.warn(DOMAIN, TAG, `[connectPet] 首次连接失败，2 秒后重试（clientId=%{public}s）`, clientId);
      await new Promise<void>((resolve) => setTimeout(() => resolve(), 2000));
      try {
        isConnect = await this.mqttAsyncClient.connect(
          {
            userName: AppConstants.MQTT_USER,
            password: AppConstants.MQTT_PASSWORD,
            connectTimeout: 5000
          }
        );
      } catch (e) {
        hilog.error(DOMAIN, TAG, `[connectPet] 重试异常: %{public}s`, JSON.stringify(e));
      }
    }
    if (!isConnect) {
      hilog.error(DOMAIN, TAG, `[connectPet] 连接失败（clientId=%{public}s）`, clientId);
      return;
    }
    hilog.info(DOMAIN, TAG, `[connectPet] 连接成功`);

    //3、设置回调
    this.mqttAsyncClient.messageArrived(callback)
    hilog.info(DOMAIN, TAG, `[connectPet] messageArrived 回调已设置`);

    //4、订阅 pet 主题
    await this.mqttAsyncClient.subscribe({
      topic: 'pet',
      qos: 2
    })
    hilog.info(DOMAIN, TAG, `[connectPet] 已订阅主题: pet`);

    //5、订阅 ctrl 主题（接收外部喂食记录等）
    await this.mqttAsyncClient.subscribe({
      topic: 'ctrl',
      qos: 2
    })
    hilog.info(DOMAIN, TAG, `[connectPet] 已订阅主题: ctrl`);

    //6、订阅 ctrl3 主题（接收自动喂水记录等）
    await this.mqttAsyncClient.subscribe({
      topic: 'ctrl3',
      qos: 2
    })
    hilog.info(DOMAIN, TAG, `[connectPet] 已订阅主题: ctrl3`);
  }

  // 连接并订阅 locker2 主题（供我的页面使用，接收开锁喂食指令）
  async connectLocker(callback: (err: Error, data: MqttMessage) => void) {
    hilog.info(DOMAIN, TAG, `[connectLocker] 开始连接 broker: %{public}s`, AppConstants.MQTT_URL);

    //1、实例化 MqttClient
    this.mqttAsyncClient = MqttAsync.createMqtt({
      url: AppConstants.MQTT_URL,
      clientId: 'lwq_locker',
      persistenceType: 1,
    });

    //2、连接
    let isConnect = await this.mqttAsyncClient.connect(
      {
        userName: AppConstants.MQTT_USER,
        password: AppConstants.MQTT_PASSWORD,
        connectTimeout: 300
      }
    );
    if (!isConnect) {
      hilog.error(DOMAIN, TAG, `[connectLocker] 连接失败`);
      return;
    }
    hilog.info(DOMAIN, TAG, `[connectLocker] 连接成功`);

    //3、设置回调
    this.mqttAsyncClient.messageArrived(callback)
    hilog.info(DOMAIN, TAG, `[connectLocker] messageArrived 回调已设置`);

    //4、订阅 locker3 主题
    this.mqttAsyncClient.subscribe({
      topic: 'locker3',
      qos: 2
    })
    hilog.info(DOMAIN, TAG, `[connectLocker] 已订阅主题: locker2`);
  }

  // 发布MQTT消息
  async publish(topic: string, payload: string, qos: number = 1): Promise<boolean> {
    if (!this.mqttAsyncClient) {
      hilog.error(DOMAIN, TAG, `[publish] client未初始化`);
      return false;
    }

    try {
      const publishOptions: MqttPublishOptions = {
        topic: topic,
        qos: qos as MqttQos,
        payload: payload
      };

      await this.mqttAsyncClient.publish(publishOptions);
      hilog.info(DOMAIN, TAG, `[publish] 发布成功 => topic: %{public}s, payload: %{public}s`, topic, payload);
      return true;
    } catch (error) {
      hilog.error(DOMAIN, TAG, `[publish] 发布失败: %{public}s`, JSON.stringify(error));
      return false;
    }
  }
}
