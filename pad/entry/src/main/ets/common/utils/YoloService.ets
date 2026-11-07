// common/utils/YoloService.ets
// YOLO 后端 HTTP 轮询服务：每 1 秒拉一次 /analyze
// 复用 pet-wellness-master 的实现，适配 HarmonyOS 6.1 kit 路径

import { http } from '@kit.NetworkKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { util } from '@kit.ArkTS';

export interface YoloPet {
  id: number;
  type: string;          // "dog" | "cat"
  behavior: string;      // sleeping / running / walking / playing / ...
  confidence: number;
  bbox: number[];
  speed: number;
}

export interface YoloAnalyzeResult {
  connected: boolean;
  errorMsg: string;
  pets: YoloPet[];
  petCount: number;
  timestamp: string;
  rawJson: string;
}

// YOLO /analyze 接口返回的 JSON 原始结构（ArkTS 不允许 any/unknown，用具体接口描述）
interface YoloRawPet {
  id: number;
  type: string;
  behavior: string;
  confidence: number;
  bbox: number[];
  speed: number;
}

interface YoloRawResponse {
  status: string;
  timestamp: string;
  pet_count: number;
  pets: YoloRawPet[];
  history: Map<string, string>[];
  model: string;
  inference_ms: number;
}

export class YoloService {
  private static instance: YoloService | null = null;
  private httpRequest: http.HttpRequest | null = null;

  private constructor() {
  }

  static getInstance(): YoloService {
    if (YoloService.instance === null) {
      YoloService.instance = new YoloService();
    }
    return YoloService.instance;
  }

  // 拉一次最新检测结果
  async fetchOnce(serverHost: string): Promise<YoloAnalyzeResult> {
    if (this.httpRequest === null) {
      this.httpRequest = http.createHttp();
    }
    const url: string = `http://${serverHost}/analyze`;

    try {
      const response = await this.httpRequest.request(url, {
        method: http.RequestMethod.GET,
        header: { 'Content-Type': 'application/json' },
        readTimeout: 3000,
        connectTimeout: 3000
      });

      if (response.responseCode === 200) {
        const resultData: string | Object | ArrayBuffer = response.result;
        let jsonStr: string = '';
        if (typeof resultData === 'string') {
          jsonStr = resultData;
        } else if (resultData instanceof ArrayBuffer) {
          // 用 TextDecoder 将 ArrayBuffer 转为字符串（ArkTS 不支持 .apply()）
          const textDecoder = util.TextDecoder.create('utf-8', { ignoreBOM: true });
          jsonStr = textDecoder.decodeToString(new Uint8Array(resultData), { stream: false });
        } else {
          // Object 情况（最常见）：用 JSON.stringify 序列化
          jsonStr = JSON.stringify(resultData);
        }
        const parsed: YoloRawResponse = JSON.parse(jsonStr) as YoloRawResponse;

        const petsRaw: YoloRawPet[] = parsed.pets ?? [];
        const pets: YoloPet[] = [];
        for (let i: number = 0; i < petsRaw.length; i++) {
          const obj: YoloRawPet = petsRaw[i];
          pets.push({
            id: obj.id ?? 0,
            type: obj.type ?? 'unknown',
            behavior: obj.behavior ?? 'unknown',
            confidence: obj.confidence ?? 0,
            bbox: obj.bbox ?? [],
            speed: obj.speed ?? 0
          });
        }

        return {
          connected: true,
          errorMsg: '',
          pets: pets,
          petCount: parsed.pet_count ?? pets.length,
          timestamp: parsed.timestamp ?? '',
          rawJson: jsonStr
        };
      } else {
        return {
          connected: false,
          errorMsg: `HTTP ${response.responseCode}`,
          pets: [],
          petCount: 0,
          timestamp: '',
          rawJson: ''
        };
      }
    } catch (err) {
      const error = err as BusinessError;
      return {
        connected: false,
        errorMsg: error.message ?? '网络异常',
        pets: [],
        petCount: 0,
        timestamp: '',
        rawJson: ''
      };
    }
  }

  destroy(): void {
    if (this.httpRequest !== null) {
      this.httpRequest.destroy();
      this.httpRequest = null;
    }
    YoloService.instance = null;
  }
}
