import http from '@ohos.net.http'
import { BatchJob } from '../model/BatchResult'
import { ApiClient, MultipartImageBody } from './ApiClient'

/** Batch image upload and lifecycle controls backed by the desktop BatchAnalyzer. */
export class BatchApi {
  static async create(baseUrl: string, apiKey: string, imageUris: string[]): Promise<BatchJob> {
    const multipart: MultipartImageBody =
      ApiClient.createImagesMultipart(imageUris, 'files', '----HarmonyBatch')
    return ApiClient.requestJson<BatchJob>(baseUrl, apiKey, '/api/v1/batch-jobs', http.RequestMethod.POST,
      '提交批量图片失败', multipart.contentType, multipart.body)
  }

  static async get(baseUrl: string, apiKey: string, jobId: string): Promise<BatchJob> {
    return ApiClient.requestJson<BatchJob>(baseUrl, apiKey,
      `/api/v1/batch-jobs/${encodeURIComponent(jobId)}`, http.RequestMethod.GET, '读取批量任务失败')
  }

  static async pause(baseUrl: string, apiKey: string, jobId: string): Promise<BatchJob> {
    return BatchApi.control(baseUrl, apiKey, jobId, 'pause', '暂停批量任务失败')
  }

  static async resume(baseUrl: string, apiKey: string, jobId: string): Promise<BatchJob> {
    return BatchApi.control(baseUrl, apiKey, jobId, 'resume', '继续批量任务失败')
  }

  static async cancel(baseUrl: string, apiKey: string, jobId: string): Promise<BatchJob> {
    return BatchApi.control(baseUrl, apiKey, jobId, 'cancel', '取消批量任务失败')
  }

  static async retryFailed(baseUrl: string, apiKey: string, jobId: string): Promise<BatchJob> {
    return BatchApi.control(baseUrl, apiKey, jobId, 'retry-failed', '重试失败图片失败')
  }

  private static async control(baseUrl: string, apiKey: string, jobId: string,
    action: string, errorAction: string): Promise<BatchJob> {
    return ApiClient.requestJson<BatchJob>(baseUrl, apiKey,
      `/api/v1/batch-jobs/${encodeURIComponent(jobId)}/${action}`, http.RequestMethod.POST, errorAction)
  }
}
