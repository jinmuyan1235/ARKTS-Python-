import http from '@ohos.net.http'
import { FeedbackDetail, FeedbackManifestResult, FeedbackPage } from '../model/FeedbackResult'
import { ApiClient } from './ApiClient'

export function feedbackQueryPath(status: string, query: string = ''): string {
  return `/api/v1/feedback?status=${encodeURIComponent(status)}&query=${encodeURIComponent(query)}&offset=0&limit=100`
}

export class DatasetApi {
  static async list(baseUrl: string, apiKey: string, status: string, query: string = ''): Promise<FeedbackPage> {
    return ApiClient.requestJson<FeedbackPage>(baseUrl, apiKey, feedbackQueryPath(status, query),
      http.RequestMethod.GET, '加载反馈审核列表失败')
  }

  static async detail(baseUrl: string, apiKey: string, analysisId: string): Promise<FeedbackDetail> {
    return ApiClient.requestJson<FeedbackDetail>(baseUrl, apiKey,
      `/api/v1/feedback/${encodeURIComponent(analysisId)}`, http.RequestMethod.GET, '加载反馈详情失败')
  }

  static async review(baseUrl: string, apiKey: string, analysisId: string,
    action: string, note: string = '', duplicateOf: string = ''): Promise<FeedbackDetail> {
    const body: string = JSON.stringify({ action: action, note: note, duplicateOf: duplicateOf })
    return ApiClient.requestJson<FeedbackDetail>(baseUrl, apiKey,
      `/api/v1/feedback/${encodeURIComponent(analysisId)}/review`, http.RequestMethod.PUT,
      '更新反馈审核状态失败', 'application/json', body)
  }

  static async exportManifest(baseUrl: string, apiKey: string): Promise<FeedbackManifestResult> {
    return ApiClient.requestJson<FeedbackManifestResult>(baseUrl, apiKey, '/api/v1/feedback/manifest',
      http.RequestMethod.GET, '导出核验 Manifest 失败')
  }
}
