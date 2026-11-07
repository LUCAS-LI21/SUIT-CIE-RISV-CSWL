# -*- coding: utf-8 -*-
"""MQTT 探针：连 broker、订全部主题、观察流量；可下发控制指令。

用来回答两个具体问题：
  1. 环境数据（pet 主题）是没人发，还是前端没收到？
  2. 喂食/喂水指令（ctrl / ctrl3）发出去后，设备有没有回执？

用法：
    python scripts/mqtt_probe.py                      # 监听 20 秒，打印所有主题流量
    python scripts/mqtt_probe.py 30                   # 监听 30 秒
    python scripts/mqtt_probe.py 20 '{"motor":"feed"}' ctrl   # 监听并下发一条指令
"""
import sys
import time

import paho.mqtt.client as mqtt

HOST = "119.29.133.108"
PORT = 1883
USER = "pet"
PASSWORD = "123456"

seen = {}


def build_client() -> mqtt.Client:
    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                        client_id="pc_probe_%d" % int(time.time() * 1000 % 1000000))
    except AttributeError:
        c = mqtt.Client(client_id="pc_probe_%d" % int(time.time() * 1000 % 1000000))
    return c


def on_connect(c, u, flags, rc):
    print("[probe] CONNECT rc=%s" % rc, flush=True)
    c.subscribe("#", 0)


def on_message(c, u, m):
    topic = m.topic
    payload = m.payload.decode("utf-8", errors="replace")
    seen[topic] = seen.get(topic, 0) + 1
    print("[probe] %s %-14s <- %s" % (time.strftime("%H:%M:%S"), topic, payload[:140]), flush=True)


def main() -> int:
    dur = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    payload = sys.argv[2] if len(sys.argv) > 2 else ""
    topic = sys.argv[3] if len(sys.argv) > 3 else ""

    cli = build_client()
    cli.username_pw_set(USER, PASSWORD)
    cli.on_connect = on_connect
    cli.on_message = on_message
    try:
        cli.connect(HOST, PORT, 20)
    except Exception as e:  # noqa: BLE001
        print("连接失败：%s" % e)
        return 1
    cli.loop_start()
    time.sleep(1)

    if payload and topic:
        info = cli.publish(topic, payload, qos=1)
        try:
            info.wait_for_publish(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        print("[probe] 已发布 %s <- %s (rc=%s)" % (topic, payload, info.rc), flush=True)

    time.sleep(dur)
    cli.loop_stop()
    cli.disconnect()

    print("[probe] ==== 订阅统计 ====")
    for t, n in sorted(seen.items(), key=lambda x: -x[1]):
        print("  %s: %d 条" % (t, n))
    if not seen:
        print("  （一条消息都没有）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
