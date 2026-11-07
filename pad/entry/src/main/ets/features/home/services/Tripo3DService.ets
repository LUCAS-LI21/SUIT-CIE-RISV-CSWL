// Tripo3DService.ets
// Tripo3D API 服务 - 将宠物照片转换为3D GLB模型
// 官网: https://platform.tripo3d.ai/
// API文档: https://platform.tripo3d.ai/docs

import { http } from '@kit.NetworkKit';
import { fileIo } from '@kit.CoreFileKit';
import { util } from '@kit.ArkTS';

// Tripo3D API 配置
//
// 2026-11-07：这里原本硬编码着一把可用的 tsk_ 前缀密钥，已清空。
// 两个原因：密钥随源码分发等于公开；且当前部署无外网出口，本服务本来就不可达
// （第 7 章已知限制 D-05），3D 形象实际使用 rawfile 里预渲染好的 28 段动作视频。
// 需要恢复图生模能力时，请到 platform.tripo3d.ai 自行申请后填入，不要提交真实值。
const TRIPO_API_KEY = ''; // 到 https://platform.tripo3d.ai/ 自行申请后填入
const TRIPO_API_BASE = 'https://api.tripo3d.ai/v2/openapi';

// 生成任务状态
export interface TripoTaskStatus {
  task_id: string;
  status: string;
  progress: number;
  model_url: string;
  thumbnail_url: string;
  error: string;
}

// 图片转3D服务
export class Tripo3DService {
  private apiKey: string = TRIPO_API_KEY;

  constructor(apiKey?: string) {
    if (apiKey) {
      this.apiKey = apiKey;
    }
  }

  /**
   * 检查是否配置了API Key
   */
  isConfigured(): boolean {
    return this.apiKey.length > 0;
  }

  /**
   * 第一步：上传图片获取file_token
   * @param imageUri 本地图片的 file:// 或 photo:// URI
   * @returns file_token
   */
  async uploadImage(imageUri: string): Promise<string> {
    return new Promise<string>((resolve: (value: string) => void, reject: (reason: Error) => void) => {
      try {
        console.info(`[Tripo3D] 开始上传图片, 原始URI: ${imageUri}`);

        // 将相册URI转换为可读路径（photo:// / datashare:// → 沙箱文件）
        this.resolvePhotoUri(imageUri).then((resolvedPath: string) => {
          console.info(`[Tripo3D] 解析后路径: ${resolvedPath}`);

          // 读取图片为Base64
          const file = fileIo.openSync(resolvedPath, fileIo.OpenMode.READ_ONLY);
          const stat = fileIo.statSync(resolvedPath);
          console.info(`[Tripo3D] 图片大小: ${stat.size} bytes`);

          if (stat.size === 0) {
            fileIo.closeSync(file);
            reject(new Error('图片文件为空'));
            return;
          }

          // 图片大小限制检查（10MB以内）
          if (stat.size > 10 * 1024 * 1024) {
            fileIo.closeSync(file);
            reject(new Error('图片过大，请选择10MB以内的图片'));
            return;
          }

          const buffer = new ArrayBuffer(stat.size);
          fileIo.readSync(file.fd, buffer);
          fileIo.closeSync(file);

          const base64 = this.arrayBufferToBase64(buffer);
          console.info(`[Tripo3D] Base64编码完成, 长度: ${base64.length}`);

          // 调用Tripo3D上传接口
          const httpRequest = http.createHttp();
          const url = `${TRIPO_API_BASE}/upload`;
          const options: http.HttpRequestOptions = {
            method: http.RequestMethod.POST,
            header: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${this.apiKey}`
            },
            extraData: JSON.stringify({
              image: base64
            })
          };

          httpRequest.request(url, options, (err: Error | null, data?: http.HttpResponse) => {
            httpRequest.destroy();
            if (err) {
              reject(new Error(`网络请求失败: ${err.message}`));
              return;
            }
            if (!data) {
              reject(new Error('上传请求无响应'));
              return;
            }
            console.info(`[Tripo3D] 上传API响应码: ${data.responseCode}`);

            if (data.responseCode === 200) {
              try {
                const result = JSON.parse(data.result as string) as Record<string, Object>;
                console.info(`[Tripo3D] API返回: ${JSON.stringify(result)}`);
                const fileToken = result['file_token'] as string;
                if (fileToken) {
                  resolve(fileToken);
                } else {
                  const resultStr = data.result as string;
                  reject(new Error('API返回异常，缺少file_token: ' + resultStr));
                }
              } catch (parseErr) {
                const resultStr = data !== undefined && data.result !== undefined ? String(data.result) : '';
                reject(new Error(`API响应解析失败: ${String(parseErr)}, 原始数据: ${resultStr}`));
              }
            } else {
              const resultCode = data.responseCode;
              const resultStr = data.result !== undefined ? String(data.result) : '';
              reject(new Error(`API错误(HTTP ${resultCode}): ${resultStr}`));
            }
          });
        }).catch((resolveErr: Error) => {
          reject(resolveErr);
        });
      } catch (error) {
        reject(error as Error);
      }
    });
  }

  /**
   * 第二步：创建图片转3D任务
   * @param fileToken 上传图片获取的file_token
   * @returns 任务ID
   */
  async createImageTo3DTask(fileToken: string): Promise<string> {
    return new Promise<string>((resolve: (value: string) => void, reject: (reason: Error) => void) => {
      const httpRequest = http.createHttp();
      const url = `${TRIPO_API_BASE}/task`;
      const options: http.HttpRequestOptions = {
        method: http.RequestMethod.POST,
        header: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.apiKey}`
        },
        extraData: JSON.stringify({
          type: 'image_to_model',
          file_token: fileToken,
          model_version: 'v2.0' // 使用最新版本
        })
      };

      httpRequest.request(url, options, (err: Error | null, data?: http.HttpResponse) => {
        httpRequest.destroy();
        if (err) {
          reject(new Error(`创建任务失败: ${err.message}`));
          return;
        }
        if (!data) {
          reject(new Error('创建任务请求无响应'));
          return;
        }
        if (data.responseCode === 200) {
          const result = JSON.parse(data.result as string) as Record<string, Object>;
          const taskId = result['task_id'] as string;
          if (taskId) {
            resolve(taskId);
          } else {
            reject(new Error('API返回数据格式异常，缺少task_id'));
          }
        } else {
          const resultCode = data.responseCode;
          const resultStr = data.result !== undefined ? String(data.result) : '';
          reject(new Error(`API错误 ${resultCode}: ${resultStr}`));
        }
      });
    });
  }

  /**
   * 第三步：轮询任务状态，等待3D模型生成完成
   * @param taskId 任务ID
   * @param onProgress 进度回调
   * @returns 模型下载URL
   */
  async pollTaskStatus(
    taskId: string,
    onProgress?: (progress: number, status: string) => void
  ): Promise<string> {
    const maxAttempts = 60; // 最多轮询60次（约5分钟）
    const interval = 5000; // 每5秒查询一次

    for (let i = 0; i < maxAttempts; i++) {
      const status = await this.getTaskStatus(taskId);
      const progressNum = status.progress;
      onProgress?.(isNaN(progressNum) ? 0 : progressNum, status.status);

      if (status.status === 'success' && status.model_url) {
        return status.model_url;
      }
      if (status.status === 'failed') {
        throw new Error(`3D模型生成失败: ${status.error || '未知错误'}`);
      }

      // 等待后继续轮询
      await new Promise<void>((resolve: () => void) => setTimeout(resolve, interval));
    }

    throw new Error('生成超时，请稍后重试');
  }

  /**
   * 获取任务状态
   */
  async getTaskStatus(taskId: string): Promise<TripoTaskStatus> {
    return new Promise<TripoTaskStatus>((resolve: (value: TripoTaskStatus) => void, reject: (reason: Error) => void) => {
      const httpRequest = http.createHttp();
      const url = `${TRIPO_API_BASE}/task/${taskId}`;

      const options: http.HttpRequestOptions = {
        method: http.RequestMethod.GET,
        header: {
          'Authorization': `Bearer ${this.apiKey}`
        }
      };

      httpRequest.request(url, options, (err: Error | null, data?: http.HttpResponse) => {
        httpRequest.destroy();
        if (err) {
          reject(new Error(`查询任务失败: ${err.message}`));
          return;
        }
        if (!data) {
          reject(new Error('查询任务请求无响应'));
          return;
        }
        if (data.responseCode === 200) {
          const result = JSON.parse(data.result as string) as Record<string, Object>;
          const output = result['output'] as Record<string, Object> | undefined;
          resolve({
            task_id: taskId,
            status: (result['status'] as string) || 'pending',
            progress: (result['progress'] as number) || 0,
            model_url: (output?.['model_url'] as string) || '',
            thumbnail_url: (output?.['thumbnail_url'] as string) || '',
            error: (result['error'] as string) || ''
          });
        } else {
          const resultCode = data.responseCode;
          reject(new Error(`查询API错误 ${resultCode}`));
        }
      });
    });
  }

  /**
   * 第四步：下载GLB模型到本地缓存目录
   * @param modelUrl GLB模型下载URL
   * @param fileName 保存的文件名
   * @returns 本地文件路径
   */
  async downloadModel(modelUrl: string, fileName: string): Promise<string> {
    return new Promise<string>((resolve: (value: string) => void, reject: (reason: Error) => void) => {
      const httpRequest = http.createHttp();

      const options: http.HttpRequestOptions = {
        method: http.RequestMethod.GET,
        expectDataType: http.HttpDataType.ARRAY_BUFFER
      };

      httpRequest.request(modelUrl, options, async (err: Error | null, data?: http.HttpResponse) => {
        httpRequest.destroy();
        if (err) {
          reject(new Error(`下载模型失败: ${err.message}`));
          return;
        }
        if (!data) {
          reject(new Error('下载模型请求无响应'));
          return;
        }
        if (data.responseCode === 200 && data.result instanceof ArrayBuffer) {
          try {
            // 保存到应用缓存目录
            const cacheDir = getContext(this).cacheDir;
            const filePath = `${cacheDir}/${fileName}`;
            const file = fileIo.openSync(filePath, fileIo.OpenMode.CREATE | fileIo.OpenMode.READ_WRITE);
            fileIo.writeSync(file.fd, data.result as ArrayBuffer);
            fileIo.closeSync(file);
            resolve(filePath);
          } catch (writeError) {
            reject(writeError as Error);
          }
        } else {
          const resultCode = data.responseCode;
          reject(new Error(`下载失败: HTTP ${resultCode}`));
        }
      });
    });
  }

  // ========== 工具方法 ==========

  /**
   * 验证并确保图片 URI 可读
   * 注意：现在图片在 Pet3DGeneratePage 中已经被复制到沙箱，
   * 这里主要做简单的验证和直接返回
   */
  private async resolvePhotoUri(imageUri: string): Promise<string> {
    console.info(`[Tripo3D] ===== 验证图片路径: ${imageUri}`);

    // 验证文件是否存在且可读
    try {
      if (imageUri.startsWith('file://') || imageUri.startsWith('/')) {
        fileIo.accessSync(imageUri);
        const stat = fileIo.statSync(imageUri);
        
        if (stat.size > 0 && stat.size <= 10 * 1024 * 1024) { // 限制10MB以内
          console.info(`[Tripo3D] ✅ 文件验证通过, 大小: ${stat.size} bytes`);
          return imageUri;
        } else if (stat.size === 0) {
          throw new Error('图片文件为空');
        } else {
          throw new Error('图片过大（超过10MB限制）');
        }
      }
      
      // 尝试其他格式
      const srcFile = fileIo.openSync(imageUri, fileIo.OpenMode.READ_ONLY);
      fileIo.closeSync(srcFile);
      return imageUri;
    } catch (e) {
      throw new Error(
        `无法读取图片文件\n` +
        `路径: ${imageUri}\n` +
        `错误: ${String(e)}`
      );
    }
  }

  private arrayBufferToBase64(buffer: ArrayBuffer): string {
    const bytes = new Uint8Array(buffer);
    const base64Helper = new util.Base64Helper();
    return base64Helper.encodeToStringSync(bytes);
  }
}
