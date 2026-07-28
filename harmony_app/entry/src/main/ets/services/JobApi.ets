import http from '@ohos.net.http'
import { HealthResult, VisionJob, VisionSample } from '../model/VisionResult'
import { ApiClient, MultipartImageBody } from './ApiClient'

/** Service health, built-in samples and asynchronous recognition jobs. */
export class JobApi {
  static async health(baseUrl: string, apiKey: string): Promise<HealthResult> {
    return ApiClient.requestJson<HealthResult>(baseUrl, apiKey, '/api/v1/health', http.RequestMethod.GET,
      '服务检测失败')
  }

  static async samples(baseUrl: string, apiKey: string): Promise<VisionSample[]> {
    return ApiClient.requestJson<VisionSample[]>(baseUrl, apiKey, '/api/v1/samples', http.RequestMethod.GET,
      '加载示例失败')
  }

  static async createSampleJob(baseUrl: string, apiKey: string, sampleId: string): Promise<VisionJob> {
    return ApiClient.requestJson<VisionJob>(baseUrl, apiKey,
      `/api/v1/jobs/samples/${encodeURIComponent(sampleId)}`, http.RequestMethod.POST, '提交示例失败')
  }

  static async createImageJob(baseUrl: string, apiKey: string, imageUri: string): Promise<VisionJob> {
    const multipart: MultipartImageBody =
      ApiClient.createImageMultipart(imageUri, 'file', 'molecule.png', '----HarmonyVision')
    return ApiClient.requestJson<VisionJob>(baseUrl, apiKey, '/api/v1/jobs/images', http.RequestMethod.POST,
      '提交图片失败', multipart.contentType, multipart.body)
  }

  static async getJob(baseUrl: string, apiKey: string, jobId: string): Promise<VisionJob> {
    return ApiClient.requestJson<VisionJob>(baseUrl, apiKey, `/api/v1/jobs/${encodeURIComponent(jobId)}`,
      http.RequestMethod.GET, '读取任务失败')
  }
}
