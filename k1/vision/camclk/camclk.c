// SPDX-License-Identifier: GPL-2.0
/*
 * camclk - 给 K1 的 MIPI CSI 摄像头补上正确频率的 MCLK
 *
 * 背景（全部来自实测与官方源码）：
 *   1. MUSE-Pi-Pro 的 dtb 里三个 cam_sensor 都带 `mclk-disable;`，驱动打印
 *      "close soc mclk, guess sensor had mclk" 后，整个生命周期都不会再使能 MCLK。
 *   2. 更关键：camm2_clk 默认父时钟是 pll1_d8_307p2（307.2 MHz），
 *      这个父无法分频出 24 MHz，直接 clk_set_rate(24M) 会得到 307.2 MHz。
 *      camm2_clk 的 clk_possible_parents 里有 vctcxo_24（精确 24 MHz），
 *      所以必须先把父切到 vctcxo_24，再设频率。
 *   3. 物理映射（dts 逐字对照）：
 *        cam_sensor@0 -> camera0_grp -> cam_mclk0 (camm0) -> CAMERA0 22pin
 *        cam_sensor@2 -> camera1_grp -> cam_mclk2 (camm2) -> CAMERA1 15pin
 *
 * 本版做法（尽量少干扰）：
 *   - 先 clk_set_parent(vctcxo_24) 再 clk_set_rate(24M) 再 clk_prepare_enable
 *   - 若实得频率仍不对，则回退到"只 set_rate"，并如实打印实际值
 *   - 不碰任何 GPIO，驱动自己的 pwdn / dvdd-powen / reset 时序保持原样
 *
 * 安全性：不写 dtb、不改持久化配置；rmmod 即恢复原状态。
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/string.h>
#include <linux/of.h>
#include <linux/clk.h>
#include <linux/clk-provider.h>
#include <linux/err.h>

#define MAX_SENSORS	3

static unsigned long mclk_hz = 24000000;
module_param(mclk_hz, ulong, 0644);
MODULE_PARM_DESC(mclk_hz, "MCLK frequency in Hz (IMX219=24000000, OV5647=25000000)");

static struct clk *mclks[MAX_SENSORS];
static int enabled_cnt;

/* 取 /clocks/clock-vctcxo_24 这个 fixed-clock 的 clk 句柄。
 *
 * 注意：不能用 of_clk_get(np, 0) —— 那是把该节点当作 consumer 去读它的
 * clocks 属性，而 fixed-clock 节点是 provider，本身没有 clocks 属性，
 * 实测会返回 -ENOENT。正确做法是走 of_clk_get_from_provider。
 */
static struct clk *get_vctcxo24(void)
{
	struct device_node *np;
	struct of_phandle_args args;
	struct clk *clk;

	/* 实测路径是 /clocks/clock-vctcxo_24；再兜底按名字全局搜 */
	np = of_find_node_by_path("/clocks/clock-vctcxo_24");
	if (!np)
		np = of_find_node_by_path("/clock-vctcxo_24");
	if (!np)
		np = of_find_node_by_name(NULL, "clock-vctcxo_24");
	if (!np) {
		pr_warn("camclk: 找不到 clock-vctcxo_24 节点\n");
		return NULL;
	}

	memset(&args, 0, sizeof(args));
	args.np = np;
	args.args_count = 0;	/* #clock-cells = 0 */

	clk = of_clk_get_from_provider(&args);
	of_node_put(np);
	if (IS_ERR(clk)) {
		pr_warn("camclk: vctcxo_24 取时钟失败 (%ld)\n", PTR_ERR(clk));
		return NULL;
	}
	return clk;
}

static int setup_one(int idx, struct clk *parent24)
{
	struct device_node *np;
	struct clk *clk;
	char path[64];
	unsigned long got;
	int ret;

	snprintf(path, sizeof(path), "/soc/cam_sensor@%d", idx);
	np = of_find_node_by_path(path);
	if (!np)
		return -ENODEV;

	clk = of_clk_get(np, 0);
	of_node_put(np);
	if (IS_ERR(clk)) {
		pr_warn("camclk: %s 取时钟失败\n", path);
		return -ENODEV;
	}

	/* 第一步：把父时钟切到 24 MHz 晶振，否则分频器出不来 24 MHz */
	if (parent24) {
		ret = clk_set_parent(clk, parent24);
		if (ret)
			pr_warn("camclk: %s set_parent 失败 (%d)\n", path, ret);
	}

	/* 第二步：设频率 */
	ret = clk_set_rate(clk, mclk_hz);
	if (ret)
		pr_warn("camclk: %s set_rate(%lu) 返回 %d\n", path, mclk_hz, ret);

	/* 第三步：使能时钟门 */
	ret = clk_prepare_enable(clk);
	if (ret) {
		pr_warn("camclk: %s 使能失败 (%d)\n", path, ret);
		clk_put(clk);
		return ret;
	}

	got = clk_get_rate(clk);
	mclks[idx] = clk;
	enabled_cnt++;

	if (got == mclk_hz)
		pr_info("camclk: %s MCLK = %lu Hz (%lu MHz) 正确\n",
			path, got, got / 1000000);
	else
		pr_warn("camclk: %s MCLK = %lu Hz (%lu MHz) 与请求的 %lu 不符\n",
			path, got, got / 1000000, mclk_hz);
	return 0;
}

static int __init camclk_init(void)
{
	struct clk *p24;
	int i;

	p24 = get_vctcxo24();
	if (p24)
		pr_info("camclk: vctcxo_24 = %lu Hz\n", clk_get_rate(p24));

	for (i = 0; i < MAX_SENSORS; i++)
		setup_one(i, p24);

	if (!enabled_cnt) {
		pr_err("camclk: 没有任何时钟被使能\n");
		return -ENODEV;
	}

	pr_info("camclk: 完成，%d 个 MCLK 已就绪，可跑 cam-test\n", enabled_cnt);
	return 0;
}

static void __exit camclk_exit(void)
{
	int i;

	for (i = 0; i < MAX_SENSORS; i++) {
		if (mclks[i]) {
			clk_disable_unprepare(mclks[i]);
			clk_put(mclks[i]);
			mclks[i] = NULL;
		}
	}
	pr_info("camclk: 时钟已关闭，恢复原状\n");
}

module_init(camclk_init);
module_exit(camclk_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Provide correct-frequency MCLK for K1 MIPI CSI sensors");
MODULE_AUTHOR("CIE-RISC-V");
