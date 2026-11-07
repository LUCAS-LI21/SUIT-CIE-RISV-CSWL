// common/config/RemoteConfig.ets
// K1 算力地址的唯一真相来源。
//
// 为什么单独一个文件
//   这套前端要跑在两个地方：PC 上的模拟器（现在）、3568 真板（最终）。
//   两者的「K1 在哪」完全不同，但上层代码不该关心这件事 —— 它只问
//   RemoteConfig.visionLatestUrl 要一个能用的地址。
//
// 为什么模拟器不能直接写 127.0.0.1
//   HarmonyOS 模拟器是 QEMU 虚拟机，127.0.0.1 指的是模拟器自己，
//   不是跑着 Python 服务的那台 PC。访客机访问宿主的地址由虚拟网络决定，
//   不同版本不一样（10.0.2.2 是最常见的，但并非唯一）。
//   与其赌一个，不如把候选列表按序探活，第一个 /health 通的就记住。
//   这样换机器、换模拟器版本都不用改代码，也不用重新编译。
//
// 端口约定（与 K1 上五个服务一一对应，改动必须同步 deploy/ 里的 systemd）
//   11434 ollama 原生        —— 前端不直连（会被网关仲裁绕过，见下）
//   11435 知识库 RAG
//   11436 视觉（PC 推帧或 K1 自采，schema 完全一致）
//   11437 人声 ASR
//   11438 LLM 网关           —— 前端只打这个，它在转发前后暂停/恢复视觉
//   11439 宠物叫声

export class RemoteConfig {
  // ── 部署形态开关：改这一行就切换模拟器 / 真板 ────────────────────
  //   'emulator' → 依次探测候选宿主地址（PC 上的模拟器）
  //   'board'    → 直连 K1 的网线地址（3568 eth0 ↔ K1 end0，实测 0.88 ms）
  //
  // 为什么写成方法而不是 `static readonly TARGET: string = 'emulator'`
  //   实测踩过两次：字段形式在 EntryAbility.onWindowStageCreate 里读到的是空值，
  //   导致 demoSkipLogin() 恒为 false、界面**永远停在引导页**，且不报任何错。
  //   同一份 HAP 有时能进主界面、有时不能，就是初始化时序在抖。
  //   改成方法后每次调用时才求值，字面量直接返回，没有时序问题。
  //   部署到 3568 前，把这里改成 return 'board' 即可，全项目只有这一处需要改。
  // Deveco 里点 Previewer 时用它：Previewer 是 PC 上的原生进程，不是 QEMU，
  // 127.0.0.1 就指宿主，直接命中 PC 上跑着的 11436 视觉服务。
  // 真模拟器（QEMU）与真板各自见下面的常量，上板前把这里改回 'board'。
  static target(): string {
    // 'board'    → 直连 K1 的网线地址（3568 eth0 ↔ K1 end0，实测 0.88 ms）★ 发布形态
    // 'emulator' → 依次探 10.0.2.2 / 127.0.0.1 / 192.168.88.10（PC 模拟器调试用）
    //
    // 为什么写成方法而不是 static 字段：字段形式在 EntryAbility 初始化阶段
    // 读到的是空值，界面会永远停在引导页且不报错；方法每次调用时求值。
    // 全项目只有这一处需要改。
    return 'board';
  }

  // 3568 与 K1 网线直连时，K1 在 end0 上的静态地址
  static readonly BOARD_K1_HOST: string = '192.168.88.10';

  // 候选地址，按序探活，第一个 /health 通的就记住。
  //
  // 模拟器形态下**只有宿主别名可能通**：模拟器是 QEMU 虚拟机，它的网络是 slirp，
  // 根本没有通往 192.168.88.0/24 的路由，所以 192.168.88.10 在模拟器里必然失败。
  // 首位 10.0.2.2 是 slirp 给宿主的固定别名；宿主上跑着 scripts/k1_portfwd.py，
  // 它把本地 11434/11436/11437/11438/11439 经「hdc → 3568 nc 中继 → SSH」转给 K1。
  // 所以模拟器拿到的仍然是 K1 板端的真实数据，不是 PC 上的推理替身。
  //
  // 为什么 PC 不能直连 K1：PC 的「以太网」网卡虽然 Link Up(1 Gbps)，但 K1 的
  // dnsmasq 租约表里只有 3568、ARP 表里也只有 3568 —— 这块网卡不在 K1 那条
  // 二层链路上，只能拿到 169.254 的 APIPA 地址，改静态地址又要管理员权限。
  static readonly EMULATOR_CANDIDATES: string[] = [
    '10.0.2.2',
    '127.0.0.1',
    '192.168.88.10'
  ];

  // 探测成功后写入这里。运行时才确定，所以不是 readonly。
  // 空串表示「还没探过」。
  static resolvedHost: string = '';

  // ── 端口 ────────────────────────────────────────────────────────
  static readonly PORT_VISION: number = 11436;
  static readonly PORT_ASR: number = 11437;
  static readonly PORT_LLM: number = 11438;
  static readonly PORT_VOICE: number = 11439;
  static readonly PORT_KB: number = 11435;

  // ── 超时（毫秒）──────────────────────────────────────────────────
  // 视觉/叫声是「拉最新缓存值」，板端序列化只要几毫秒；给 800/600 是留
  // USB 或 WiFi 抖动余量，同时不让它在链路异常时拖慢整条感知流程。
  static readonly TIMEOUT_VISION: number = 800;
  static readonly TIMEOUT_VOICE: number = 600;
  static readonly TIMEOUT_PROBE: number = 1200;
  // 知识库检索是纯 Python 字符串打分（36 条，无向量模型），
  // 实测命中/未命中都在几十毫秒内；给 2500 是覆盖板端首次 import 的冷启动。
  static readonly TIMEOUT_KB: number = 2500;
  // LLM 单次实测 3~10 秒（prefill≈20 tok/s、decode≈10 tok/s），
  // 冷加载要 8~11 秒，留 30 秒余量。
  static readonly TIMEOUT_LLM: number = 30000;

  // 模型名必须与 K1 上 ollama 里实际拉的一致
  static readonly LLM_MODEL: string = 'qwen3:0.6b';
  // 输出上限：decode 约 10 tok/s，24 token ≈ 2.4 秒。
  // 超过 32 会让等待明显变长，且 0.6B 容易开始说空话。
  static readonly LLM_MAX_TOKENS: number = 40;
  static readonly LLM_TEMPERATURE: number = 0.3;

  // ── 地址拼装 ────────────────────────────────────────────────────
  static host(): string {
    if (RemoteConfig.target() === 'board') {
      return RemoteConfig.BOARD_K1_HOST;
    }
    if (RemoteConfig.resolvedHost.length > 0) {
      return RemoteConfig.resolvedHost;
    }
    // 还没探过活时的兜底。
    // **必须是 10.0.2.2，不能是 127.0.0.1**：模拟器是 QEMU 虚拟机，
    // 127.0.0.1 指模拟器自己（上面没有任何 K1 服务），10.0.2.2 才是
    // slirp 给宿主机的固定别名。首页 HomeTab 不走 PetDataHub.ensureHost，
    // 它自己拉 11436/11439，所以这条兜底就是它第一次请求实际用的地址。
    // 探活一旦成功，resolvedHost 会覆盖这里。
    // （DevEco 的 Previewer 是 PC 原生进程，那时要用 127.0.0.1；
    //   它不属于本文件的演示形态，真要用请直接改这一行。）
    return RemoteConfig.EMULATOR_CANDIDATES[0];
  }

  static base(port: number): string {
    return `http://${RemoteConfig.host()}:${port}`;
  }

  static visionLatestUrl(): string {
    return `${RemoteConfig.base(RemoteConfig.PORT_VISION)}/v1/vision/latest`;
  }

  static voiceLatestUrl(): string {
    return `${RemoteConfig.base(RemoteConfig.PORT_VOICE)}/v1/voice/latest`;
  }

  static llmChatUrl(): string {
    return `${RemoteConfig.base(RemoteConfig.PORT_LLM)}/v1/chat/completions`;
  }

  /**
   * 本地知识库检索（K1:11435）。
   *
   * 接口是 GET /ask?q=...，不是 POST，也没有 JSON body。
   * 返回字段：hit / answer / source_answer / mode(strict|rewrite|miss) / warn / id / score。
   *
   * 为什么值得单独调用：这条链路上唯一「有出处」的回答来源。
   * qwen3:0.6b 只有 0.6B，问「猫能喝牛奶吗」它会编；知识库命中时
   * mode=strict 直接回原文，还带 warn 说明「未经模型改写」。
   */
  static kbAskUrl(q: string): string {
    return `${RemoteConfig.base(RemoteConfig.PORT_KB)}/ask?q=${encodeURIComponent(q)}`;
  }

  static asrBase(): string {
    return RemoteConfig.base(RemoteConfig.PORT_ASR);
  }

  /**
   * 本地语音识别（sherpa-onnx offline websocket server，K1:11440）。
   *
   * 为什么不是 11437：11437 是「K1 用板载麦克风常驻录音 + VAD 自动切句」，
   * 它只能听到 K1 自己那边的声音，收不到平板前面的主人说话；而且它一直占着
   * 一个识别进程（实测 100% CPU / 300 MB），前端却从来没消费过它的结果。
   * 11440 是「上传音频→返回文本」的按需接口，模型常驻加载一次，
   * 只在真的有人按下按钮时才算，静息时 0 CPU。
   *
   * 协议（sherpa-onnx/csrc/offline-websocket-server-impl.h）：
   *   前 4 字节 little-endian int32 = 采样率
   *   后 4 字节 little-endian int32 = 音频总字节数
   *   其余 = float32 单声道样本，归一化到 [-1, 1]
   *   发完音频后服务端回一条 JSON 文本，客户端发 "Done" 关闭。
   */
  static readonly PORT_ASR_WS: number = 11440;

  static asrWsUrl(): string {
    return `ws://${RemoteConfig.host()}:${RemoteConfig.PORT_ASR_WS}`;
  }

  /**
   * 板载麦克风录音 + 识别（K1:11441）。
   *
   * 为什么采集端搬到了 K1：2026-11-06 实测 3568 的 AudioCapturer 采到的是
   * 恒定直流偏置（rms/peak=0.878、零交叉率 0.0003），麦克风输入悬空，
   * 送进 ASR 必然返回空文本；同一时刻 K1 的 USB 声卡采到的是正常环境声
   * （rms/peak=0.185、零交叉率 0.134）。
   *
   * 分工：前端只 POST /v1/asr/start 与 /stop，录音和识别都在 K1 本地完成，
   * 不再上传音频（省掉 100~180 KB 上行与一段 WebSocket 协议）。
   */
  static readonly PORT_ASR_MIC: number = 11441;

  static micAsrUrl(path: string): string {
    return `http://${RemoteConfig.host()}:${RemoteConfig.PORT_ASR_MIC}${path}`;
  }

  static probeUrl(host: string): string {
    return `http://${host}:${RemoteConfig.PORT_VISION}/health`;
  }

  // ── 演示开关 ────────────────────────────────────────────────────
  /**
   * 跳过登录与建档引导，直接进主界面。
   *
   * 为什么需要：完整流程是 欢迎页 → 登录（要后端发验证码）→ 选品种
   * → 填信息 → 生成 3D → 主界面，六步才能看到「对话」页，
   * 而登录后端现在还是原型（见 HANDOFF §8），演示时根本走不通。
   *
   * 只在模拟器形态生效，真板形态必须为 false —— 否则交付的包会绕过登录。
   *
   * 写成方法而不是 static 字段：字段初始化器里引用同类其他静态成员，
   * 实测取不到值（恒为 false，页面停在欢迎页且不报错）。方法在调用时求值，没有这个问题。
   */
  static demoSkipLogin(): boolean {
    // 联调阶段临时直通主界面，方便上板后直接核对四路数据。
    // 交付前必须改回 `return RemoteConfig.target() === 'emulator';`（真板走完整登录）。
    return true;
  }
}
