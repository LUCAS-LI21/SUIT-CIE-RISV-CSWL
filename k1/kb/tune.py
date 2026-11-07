# -*- coding: utf-8 -*-
"""检索阈值调参：分别看正样本与负样本的得分分布，找安全分界。"""
import sys
from rag import retriever

POS = [
    "猫能喝牛奶吗", "我家猫体温39.8度正常吗", "狗吃了巧克力怎么办", "葡萄能给狗吃吗",
    "柯基一天遛多久", "猫咪一天睡几个小时", "公猫尿不出来", "多久打一次疫苗",
    "狗中暑怎么急救", "猫粮狗粮能混着喂吗", "猫的正常体温是多少", "狗正常心率多少",
    "猫咪呕吐要紧吗", "猫砂盆要准备几个", "换粮怎么过渡", "狗能吃洋葱吗",
    "幼猫什么时候打疫苗", "狗狗洗澡多久一次", "猫咪应激怎么办", "狗摇尾巴什么意思",
]

NEG = [
    "冰箱冷藏室一般设置多少度", "今天天气怎么样", "帮我写一首诗", "北京到上海多远",
    "Python 怎么读文件", "推荐一部电影", "股票明天会涨吗", "一加一等于几",
    "怎么减肥", "我的手机没电了", "明天几点开会", "红烧肉怎么做",
    "如何学习英语", "水多少度沸腾", "猫和狗哪个更聪明", "宠物店在哪里",
    "猫粮多少钱一袋", "狗狗叫什么名字好",
]

print(f"{'类型':<6}{'问题':<26}{'最高分':>8}  命中条目")
print("-" * 70)
pos_scores, neg_scores = [], []
for q in POS:
    s, i = retriever.score(q)[0]
    pos_scores.append((s, q))
    print(f"{'正':<6}{q:<26}{s:>8}  {retriever.entries[i]['id']}")
print("-" * 70)
for q in NEG:
    s, i = retriever.score(q)[0]
    neg_scores.append((s, q))
    print(f"{'负':<6}{q:<26}{s:>8}  {retriever.entries[i]['id']}")
print("-" * 70)

pos_scores.sort()
neg_scores.sort(reverse=True)
print(f"\n正样本最低 3 个： {[(s, q) for s, q in pos_scores[:3]]}")
print(f"负样本最高 5 个： {[(s, q) for s, q in neg_scores[:5]]}")
print(f"\n正样本最低分 = {pos_scores[0][0]}   负样本最高分 = {neg_scores[0][0]}")
gap_lo, gap_hi = neg_scores[0][0], pos_scores[0][0]
if gap_hi > gap_lo:
    print(f"安全阈值区间： ({gap_lo:.2f}, {gap_hi:.2f})  建议取 {(gap_lo + gap_hi) / 2:.2f}")
else:
    print("重叠！正负样本得分有交叉，需要改检索策略。冲突项：")
    for ns, nq in neg_scores:
        if ns >= pos_scores[0][0]:
            print(f"  负样本 {nq} = {ns}  已超过正样本最低 {pos_scores[0][0]}")
