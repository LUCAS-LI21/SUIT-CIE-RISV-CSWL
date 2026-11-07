// common/utils/HttpService.ets
import { http } from '@kit.NetworkKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { util } from '@kit.ArkTS';
import { DeepSeekMessage } from '../../features/chat/services/DeepSeekService';

export interface HttpRequestConfig {
  url: string;
  method?: http.RequestMethod;
  headers?: Record<string, string>;
  body?: string;
  timeout?: number;
}

export interface HttpResponse {
  success: boolean;
  code: number;
  data: string | null;
  message: string | null;
}

// 后端接口响应类型
export interface Response {
  success: boolean;
  message: string;
  code?: string;
}

export class HttpService {
  private static instance: HttpService | null = null;
  private readonly defaultTimeout: number = 30000;

  private constructor() {
  }

  static getInstance(): HttpService {
    if (HttpService.instance === null) {
      HttpService.instance = new HttpService();
    }
    return HttpService.instance;
  }

  async request(config: HttpRequestConfig): Promise<HttpResponse> {
    const httpRequest = http.createHttp();
    const method: http.RequestMethod = config.method ?? http.RequestMethod.GET;
    const timeout: number = config.timeout ?? this.defaultTimeout;
    const headers: Record<string, string> = config.headers ?? {};

    const options: http.HttpRequestOptions = {
      method: method,
      header: headers,
      readTimeout: timeout,
      connectTimeout: timeout
    };

    if (config.body) {
      options.extraData = config.body;
    }

    try {
      const response = await httpRequest.request(config.url, options);
      const responseCode: number = response.responseCode;
      const resultData: string | Object | ArrayBuffer = response.result;

      if (responseCode === 200) {
        let parsedData: string;
        if (typeof resultData === 'string') {
          parsedData = resultData;
        } else {
          parsedData = JSON.stringify(resultData);
        }
        return {
          success: true,
          code: responseCode,
          data: parsedData,
          message: null
        };
      } else {
        return {
          success: false,
          code: responseCode,
          data: null,
          message: `请求失败，状态码：${responseCode}`
        };
      }
    } catch (err) {
      const error = err as BusinessError;
      console.error('HttpService request error:', error.message);
      return {
        success: false,
        code: -1,
        data: null,
        message: `网络请求异常：${error.message}`
      };
    } finally {
      httpRequest.destroy();
    }
  }

  /**
   * 流式 POST：每收到一段就把**已累积的全文**回调出去，而不是等整包读完。
   *
   * 为什么要单独一个方法：
   *   普通 request() 会等上游把整句生成完才返回，于是「首字延迟」天然等于
   *   「整句延迟」——实测 1358 ms / 1358 ms，一模一样。用户按下发送后要盯着
   *   空屏幕等 1.3 秒，体感上就是卡。K1 网关已经把 ollama 的 SSE 做了边到边
   *   转发（实测首字 489 ms），前端必须流式读，否则那 900 ms 白省。
   *
   * 用法约定：onChunk 收到的是**累积文本**而不是增量——组件直接 setText 即可，
   * 不用自己拼接，避免丢包/重入时拼错。
   *
   * @returns 结束时返回完整响应文本（原始 SSE 全文），失败时 success=false
   */
  async requestStream(config: HttpRequestConfig, onChunk: (accumulated: string) => void): Promise<HttpResponse> {
    const httpRequest = http.createHttp();
    const method: http.RequestMethod = config.method ?? http.RequestMethod.POST;
    const timeout: number = config.timeout ?? 60000;
    const headers: Record<string, string> = config.headers ?? {};

    const options: http.HttpRequestOptions = {
      method: method,
      header: headers,
      readTimeout: timeout,
      connectTimeout: 10000
    };
    if (config.body) {
      options.extraData = config.body;
    }

    let raw = '';
    let answer = '';
    let lineBuf = '';
    let code: number = -1;
    // 采集器给的 ArrayBuffer 会被复用，必须立刻拷出来；而且中文是多字节，
    // 分包边界可能把一个字劈成两半。这里把已收到的字节整体累积，每次都
    // 整段解码——总量只有几 KB，O(n²) 的开销可以忽略，但绝不会出现半个汉字。
    let byteAcc: Uint8Array = new Uint8Array(0);
    let decodedLen: number = 0;
    const decoder = util.TextDecoder.create('utf-8');
    let seenFirst = false;
    const startedAt: number = Date.now();

    httpRequest.on('dataReceive', (data: ArrayBuffer) => {
      const piece = new Uint8Array(data);
      const merged: Uint8Array = new Uint8Array(byteAcc.length + piece.length);
      merged.set(byteAcc, 0);
      merged.set(piece, byteAcc.length);
      byteAcc = merged;

      const whole: string = decoder.decodeToString(byteAcc);
      if (whole.length <= decodedLen) {
        return;  // 这一包只凑齐了半个字，等下一包
      }
      const fresh: string = whole.substring(decodedLen);
      decodedLen = whole.length;
      raw += fresh;
      lineBuf += fresh;

      let idx: number = lineBuf.indexOf('\n');
      while (idx >= 0) {
        const line: string = lineBuf.substring(0, idx).trim();
        lineBuf = lineBuf.substring(idx + 1);
        idx = lineBuf.indexOf('\n');
        if (!line.startsWith('data:')) {
          continue;
        }
        const payload: string = line.substring(5).trim();
        if (payload === '[DONE]' || payload.length === 0) {
          continue;
        }
        try {
          const obj: Record<string, Object> = JSON.parse(payload) as Record<string, Object>;
          const choices = obj.choices as Array<Record<string, Object>>;
          if (choices !== undefined && choices.length > 0) {
            const delta = choices[0].delta as Record<string, Object>;
            if (delta !== undefined && typeof delta.content === 'string') {
              const t: string = delta.content as string;
              if (t.length > 0) {
                answer += t;
                if (!seenFirst) {
                  seenFirst = true;
                  console.info(`[HttpService] 首字 ${Date.now() - startedAt} ms`);
                }
                onChunk(answer);
              }
            }
          }
        } catch (e) {
          // 半个 JSON（分包边界正好落在对象中间）不算错误，等下一段补齐。
          // 真正解析不了的行直接跳过，不能因为一行坏数据丢掉整条回答。
        }
      }
    });

    try {
      code = await httpRequest.requestInStream(config.url, options);
      if (code === 200 && answer.length > 0) {
        return { success: true, code: code, data: answer, message: null };
      }
      return {
        success: false,
        code: code,
        data: answer.length > 0 ? answer : null,
        message: answer.length > 0 ? null : `流式响应为空，code=${code}`
      };
    } catch (err) {
      const error = err as BusinessError;
      console.error('[HttpService] requestStream 异常:', error.message);
      return { success: false, code: -1, data: null, message: `流式请求异常：${error.message}` };
    } finally {
      try {
        httpRequest.off('dataReceive');
      } catch (e) {
        // 老版本没有 off 重载，忽略
      }
      httpRequest.destroy();
    }
  }

  // ... postJson 和 get 方法保持不变 ...

  // 修改这个方法，为DeepSeek请求设置更长的超时时间
  async callDeepSeek(apiKey: string, messages: DeepSeekMessage[]): Promise<HttpResponse> {
    const url: string = 'https://api.deepseek.com/chat/completions';

    // 直接构建完整的 headers 对象，避免遍历覆盖问题
    const headers: Record<string, string> = {
      'Authorization': `Bearer ${apiKey}`,
      'Content-Type': 'application/json'
    };

    const body: Record<string, Object> = {
      'model': 'deepseek-chat',
      'messages': messages,
      'stream': false
    };

    const extendedTimeout: number = 60000;

    return this.request({
      url: url,
      method: http.RequestMethod.POST,
      headers: headers,
      body: JSON.stringify(body),
      timeout: extendedTimeout
    });
  }

  // 后端服务地址
  private readonly baseUrl: string = 'http://192.168.16.1:3000';

  // 发送验证码
  async sendCode(phone: string): Promise<HttpResponse> {
    const url = `${this.baseUrl}/api/send-code`;
    const body = JSON.stringify({ phone });

    return this.request({
      url: url,
      method: http.RequestMethod.POST,
      headers: {
        'Content-Type': 'application/json'
      },
      body: body
    });
  }

  // 注册
  async register(phone: string, password: string, code: string): Promise<HttpResponse> {
    const url = `${this.baseUrl}/api/register`;
    const body = JSON.stringify({ phone, password, code });

    return this.request({
      url: url,
      method: http.RequestMethod.POST,
      headers: {
        'Content-Type': 'application/json'
      },
      body: body
    });
  }

  // 登录
  async login(phone: string, password: string): Promise<HttpResponse> {
    const url = `${this.baseUrl}/api/login`;
    const body = JSON.stringify({ phone, password });

    return this.request({
      url: url,
      method: http.RequestMethod.POST,
      headers: {
        'Content-Type': 'application/json'
      },
      body: body
    });
  }
}