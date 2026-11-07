/**
 * 本地算力自证脚本 —— 证明"回答确实来自 K1 板子"，而不是这台电脑或云端
 * 用法：双击 verify-local.bat，或 node verify-local.js
 */
const { execSync } = require('child_process');

const ADB = 'E:\\AndroidStudio\\android\\sdk\\platform-tools\\adb.exe';
const BOARD = 'BPMIK102040643128';
const OLLAMA = 'http://127.0.0.1:11434';
const KB = 'http://127.0.0.1:11435';

const sh = (cmd) => execSync(cmd, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
const adb = (cmd) => sh(`"${ADB}" -s ${BOARD} shell "${cmd}"`).trim();

function line(n, title) {
  console.log('\n' + '─'.repeat(64));
  console.log(`[${n}] ${title}`);
  console.log('─'.repeat(64));
}

(async () => {
  console.log('K1 本地算力核验 —— 回答到底是板子算的，还是别处算的');

  line(1, '板子是否真的连着（电脑侧）');
  console.log(sh(`"${ADB}" devices`).trim());

  line(2, '板子自己的开机时刻与负载（板内执行）');
  console.log('开机时刻 :', adb('uptime -s'));
  console.log('已运行   :', adb('uptime').replace(/\s+/g, ' '));
  console.log('CPU      :', adb("grep -m1 'model name' /proc/cpuinfo || grep -m1 Processor /proc/cpuinfo").replace(/\s+/g, ' '));

  line(3, '两个服务都跑在板子上（板内进程与端口）');
  console.log(adb("ss -lntp 2>/dev/null | grep -E '11434|11435' || echo '端口未监听'"));
  console.log('\n模型常驻情况：');
  try {
    console.log(adb('HOME=/root ollama ps'));
  } catch (e) {
    console.log('（模型当前未加载，第一次提问会冷加载约 10 秒）');
  }

  line(4, '电脑侧谁在占用 11435 端口（关键：应为 adb.exe，不是 python）');
  try {
    const ns = sh('netstat -ano | findstr ":11435" | findstr LISTENING').trim().split('\n')[0] || '';
    const pid = ns.trim().split(/\s+/).pop();
    if (pid) {
      const who = sh(`tasklist /FI "PID eq ${pid}" /NH`).trim().split('\n')[0];
      console.log(ns.trim());
      console.log('占用进程 :', who.replace(/\s+/g, ' '));
      console.log(
        /adb/i.test(who)
          ? '→ 是 adb 的端口转发，说明请求被送到板子，本机没有任何知识库进程'
          : '→ 警告：占用者不是 adb，请核对'
      );
    }
  } catch (e) {
    console.log('（没有监听：隧道未建立，请先跑 start-chat.bat）');
  }

  line(5, '知识库检索：板子上算，10ms 级');
  const t1 = Date.now();
  const kbRes = JSON.parse(sh(`curl -s -m 10 --noproxy "*" "${KB}/ask?q=${encodeURIComponent('狗能吃巧克力吗')}"`));
  const kbMs = Date.now() - t1;
  console.log('命中 :', kbRes.hit, '| 条目 :', kbRes.id, '| 得分 :', kbRes.score, '| 模式 :', kbRes.mode);
  console.log('往返 :', kbMs + 'ms（含转发）');
  console.log('回答 :', kbRes.answer.slice(0, 70) + '…');

  line(6, '板端日志：刚才这次查询在板子上留了痕');
  console.log(adb('journalctl -u petkb --no-pager -n 3 | grep ASK'));

  line(7, '模型生成：在板子的 CPU 上跑');
  const body = JSON.stringify({
    model: 'qwen3:0.6b',
    messages: [{ role: 'user', content: '用一句话说明你是谁' }],
    stream: false,
    options: { num_predict: 40 },
  });
  const t2 = Date.now();
  const gen = JSON.parse(
    sh(`curl -s -m 180 --noproxy "*" -H "Content-Type: application/json" -d "${body.replace(/"/g, '\\"')}" "${OLLAMA}/api/chat"`)
  );
  const total = (Date.now() - t2) / 1000;
  const ec = gen.eval_count || 0;
  const ed = (gen.eval_duration || 1) / 1e9;
  console.log('回答 :', (gen.message && gen.message.content || '').replace(/\n/g, ' ').slice(0, 80));
  console.log(`生成 : ${ec} tok / ${ed.toFixed(2)}s = ${ec && ed ? (ec / ed).toFixed(2) : '-'} tok/s`);
  console.log(`冷加载: ${((gen.load_duration || 0) / 1e9).toFixed(2)}s  总耗时: ${total.toFixed(2)}s`);
  console.log('负载 :', adb('cat /proc/loadavg'));

  line(8, '板端日志：刚才这次生成记在板子的 ollama 里');
  console.log(adb('journalctl -u ollama --no-pager -n 5 | grep "api/chat" | tail -2'));

  console.log('\n' + '═'.repeat(64));
  console.log('判定依据：');
  console.log('  ① 11435 在本机由 adb.exe 持有 → 知识库进程在板子上，不在电脑上');
  console.log('  ② 知识库查询在板端 journal 留下 [ASK] 记录（得分可对上）');
  console.log('  ③ 模型生成在板端 journal 留下 POST /api/chat 记录（耗时对得上）');
  console.log('  ④ 板端 CPU 只有 8 核 RISC-V，速度 8-12 tok/s，是这颗芯片的真实水平');
  console.log('═'.repeat(64));
})();
