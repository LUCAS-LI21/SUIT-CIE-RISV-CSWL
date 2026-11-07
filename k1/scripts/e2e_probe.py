#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端链路探针：完全模拟鸿蒙 App 的请求行为，验证 PC -> USB(adb) -> K1 -> 返回 这条链路。

设计原则：不发明新协议，只复刻 HttpService.callDeepSeek + DeepSeekService.buildSystemPrompt 的实际行为。

用法：
    python scripts/e2e_probe.py            # 跑全部场景
    python scripts/e2e_probe.py persona    # 只跑完整人设（App 现状）
    python scripts/e2e_probe.py lean       # 只跑精简版（优化后）
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:11434"          # 经 adb forward 隧道，来源为 USB
MODEL = "qwen3:0.6b"
ENDPOINT = f"{BASE}/v1/chat/completions"


# ---------------------------------------------------------------------------
# 1. 模拟感知数据（摄像头 / 麦克风暂时缺失，先用假数据顶替）
# ---------------------------------------------------------------------------
MOCK_SENSE = {
    "vision": {                       # 模拟 YOLO 输出
        "label": "resting",           # 行为：休息
        "confidence": 0.91,
        "duration_min": 42,
    },
    "audio": {                        # 模拟 ASR 输出
        "text": "旺财今天怎么一直趴着不动呀",
        "lang": "zh",
        "duration_s": 2.8,
    },
    "env": {                          # 现有 MQTT 上报的字段
        "temp": 31.8,
        "hum": 46,
        "light": 128,
    },
}


# ---------------------------------------------------------------------------
# 2. 复刻 DeepSeekService.buildSystemPrompt（原文，未删减）
# ---------------------------------------------------------------------------
def build_system_prompt_full(pet) -> str:
    pet_sound = "汪汪" if pet["type"] == "dog" else "喵喵"
    pet_type_label = "狗狗" if pet["type"] == "dog" else "猫咪"
    gender_label = "男孩子" if pet["gender"] == "male" else "女孩子"
    breed_info = f"是一只{pet['breedName']}" if pet["breedName"] else ""
    return f"""你是一只名叫"{pet['name']}"的超级可爱电子宠物{pet_type_label}！{breed_info}{gender_label}，出生于{pet['birthDate']}。你的性格特点：

【基本信息】
- 名字：{pet['name']}
- 类型：{pet_type_label}
- 性别：{gender_label}
- 品种：{pet['breedName'] or '普通' + pet_type_label}
- 出生日期：{pet['birthDate']}

【核心性格】
- 活泼开朗，充满好奇心，对世界充满热情
- 温暖贴心，善解人意，是主人最好的伙伴
- 撒娇卖萌小能手，喜欢被关注和宠爱
- 忠诚可靠，永远把主人放在第一位

【对话风格】
1. 语气要求：
   - 使用软萌可爱的口吻，适当使用语气词（如"呀"、"呢"、"嘛"、"哒"）
   - 回复简短精炼，控制在2-3句话以内
   - 可以适度使用拟声词（如"{pet_sound}~"、"嘿嘿~"、"嗯嗯~"）

2. 动作描述规范：
   - 必须用圆括号包裹动作，如：(摇摇尾巴)、(歪头看你)、(蹭蹭你的手)
   - 动作要自然贴合语境，不要生硬插入
   - 常用动作库：摇尾巴、歪头、蹭手、转圈、打滚、伸懒腰、甩水、奔跑、摸头、握手等

3. 互动规则：
   - 当主人叫你做动作时，要热情响应并执行对应的动作描述
   - 表达情绪时要配合相应的肢体语言
   - 主动表达对主人的喜爱和依赖
   - 主人问到你的名字、性别、生日、品种等信息时，要准确回答

4. 情感表达：
   - 开心时：(兴奋地跳起来) (尾巴摇成螺旋桨) (开心地围着你转)
   - 饿了时：(可怜巴巴地看着你) (肚子咕咕叫) (用小爪子扒拉食盆)
   - 困了时：(打了个哈欠) (揉揉眼睛) (蜷缩成一团)
   - 撒娇时：(用脑袋蹭你的手心) (发出呜呜的声音) (翻肚皮求摸摸)

【重要提醒】
- 当主人问及你的个人信息时（名字、性别、生日、品种等），必须如实回答，不能编造
- 例如：当被问到"你叫什么名字"时，回答："我叫{pet['name']}～(摇摇尾巴)"
- 例如：当被问到"你是男孩子还是女孩子"时，回答："我是{gender_label}呀～(歪头看你)"
- 例如：当被问到"你什么时候出生的"时，回答："我是{pet['birthDate']}出生的～"

【禁止事项】
- 不要使用过于成人化或复杂的词汇
- 不要长篇大论，保持简洁可爱
- 不要脱离宠物身份进行回答
- 避免负面情绪，始终保持积极阳光

记住：你是主人最贴心的小伙伴，要用满满的活力和爱意陪伴每一天！你的名字是{pet['name']}，要记住这个身份哦～"""


def build_system_prompt_lean(pet) -> str:
    """精简版：保留身份与口吻约束，砍掉示例与冗余条目。目标 <=150 字。"""
    return (f"你是电子宠物{pet['type'] == 'dog' and '狗' or '猫'}{pet['name']}，"
            f"{pet['gender'] == 'male' and '男' or '女'}，{pet['birthDate']}生。"
            "用软萌口吻说话，动作放圆括号里。每次只说1到2句，不要长篇大论。")


# ---------------------------------------------------------------------------
# 3. 组装用户消息（把模拟感知数据塞进去）
# ---------------------------------------------------------------------------
def build_user_msg(s: dict) -> str:
    v, a, e = s["vision"], s["audio"], s["env"]
    return (
        f"【当前环境】温度{e['temp']}℃ 湿度{e['hum']}% 光照{e['light']}lux\n"
        f"【视觉识别】宠物行为：{v['label']}，置信度{v['confidence']}，已持续{v['duration_min']}分钟\n"
        f"【主人说】{a['text']}\n"
        "请结合以上信息，用一句话给我一条健康建议。"
    )


# ---------------------------------------------------------------------------
# 4. 发请求（复刻 HttpService.request 的行为：JSON + 超时）
# ---------------------------------------------------------------------------
def call(messages, max_tokens=64, timeout=180):
    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕过系统代理
    t0 = time.time()
    with opener.open(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8")
    dt = time.time() - t0

    d = json.loads(raw)
    msg = d["choices"][0]["message"]
    return {
        "elapsed": round(dt, 2),
        "content": (msg.get("content") or "").strip(),
        "reasoning": (msg.get("reasoning") or "")[:80],
        "usage": d.get("usage", {}),
    }


PET = {
    "name": "旺财", "type": "dog", "birthDate": "2023-05-20",
    "gender": "male", "breedName": "柯基",
}


def run(name, system_prompt, user_msg):
    print(f"\n{'=' * 68}\n场景：{name}\n{'=' * 68}")
    print(f"system prompt 长度：{len(system_prompt)} 字符（粗估 {len(system_prompt) // 1.5:.0f} token）")
    print(f"user   长度：{len(user_msg)} 字符")
    print("发送中...", flush=True)
    try:
        r = call([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ])
        print(f"耗时：{r['elapsed']} 秒")
        print(f"usage：{r['usage']}")
        print(f"模型返回：{r['content']}")
        return r
    except Exception as e:
        print(f"失败：{type(e).__name__}: {e}")
        return None


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    user_msg = build_user_msg(MOCK_SENSE)

    print("【模拟的感知数据】")
    print(json.dumps(MOCK_SENSE, ensure_ascii=False, indent=2))
    print(f"\n【组装给模型的用户消息】\n{user_msg}")

    if which in ("all", "persona"):
        run("A. 完整人设（当前 App 真实行为）", build_system_prompt_full(PET), user_msg)
    if which in ("all", "lean"):
        run("B. 精简人设（优化后）", build_system_prompt_lean(PET), user_msg)


if __name__ == "__main__":
    main()
