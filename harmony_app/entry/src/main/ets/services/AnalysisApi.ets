import http from '@ohos.net.http'
import { HistoryPage, ReviewRequest, VisionAnalysisResult } from '../model/VisionResult'
import { ApiClient } from './ApiClient'

export interface FavoritePayload {
  favorite: boolean
}

export function favoriteRequestBody(favorite: boolean): string {
  const payload: FavoritePayload = { favorite: favorite }
  return JSON.stringify(payload)
}

export function reviewRequestBody(payload: ReviewRequest): string {
  return JSON.stringify(payload)
}

export function historyQueryParams(
  query: string,
  status: string,
  favoritesOnly: boolean,
  offset: number,
  scope: string,
  ownerUserId: string
): string {
  return `query=${encodeURIComponent(query.trim())}` +
    `&status=${encodeURIComponent(status)}` +
    `&favorites_only=${favoritesOnly}` +
    `&scope=${encodeURIComponent(scope)}` +
    `&ownerUserId=${encodeURIComponent(ownerUserId)}` +
    `&limit=20&offset=${offset}`
}

/** SMILES analysis, shared history, result detail and review endpoints. */
export class AnalysisApi {
  static async analyzeSmiles(baseUrl: string, apiKey: string, smiles: string): Promise<VisionAnalysisResult> {
    return ApiClient.requestJson<VisionAnalysisResult>(baseUrl, apiKey, '/api/v1/analyze-smiles',
      http.RequestMethod.POST, 'SMILES 分析失败', 'application/json', JSON.stringify({ smiles: smiles }))
  }

  static async analyses(
    baseUrl: string,
    apiKey: string,
    query: string,
    status: string,
    favoritesOnly: boolean,
    offset: number,
    scope: string = 'all',
    ownerUserId: string = ''
  ): Promise<HistoryPage> {
    const params: string = historyQueryParams(query, status, favoritesOnly, offset, scope, ownerUserId)
    return ApiClient.requestJson<HistoryPage>(baseUrl, apiKey, `/api/v1/analyses?${params}`,
      http.RequestMethod.GET, '加载历史失败')
  }

  static async analysis(baseUrl: string, apiKey: string, analysisId: string): Promise<VisionAnalysisResult> {
    return ApiClient.requestJson<VisionAnalysisResult>(baseUrl, apiKey,
      `/api/v1/analyses/${encodeURIComponent(analysisId)}`, http.RequestMethod.GET, '加载详情失败')
  }

  static async favorite(baseUrl: string, apiKey: string, analysisId: string, favorite: boolean): Promise<void> {
    // HarmonyOS RequestMethod does not expose PATCH; the backend keeps PATCH
    // as canonical and accepts this PUT compatibility call.
    await ApiClient.requestVoid(baseUrl, apiKey,
      `/api/v1/analyses/${encodeURIComponent(analysisId)}/favorite`, http.RequestMethod.PUT,
      '更新收藏失败', 'application/json', favoriteRequestBody(favorite))
  }

  static async deleteAnalysis(baseUrl: string, apiKey: string, analysisId: string): Promise<void> {
    await ApiClient.requestVoid(baseUrl, apiKey, `/api/v1/analyses/${encodeURIComponent(analysisId)}`,
      http.RequestMethod.DELETE, '删除历史索引失败')
  }

  static async review(
    baseUrl: string,
    apiKey: string,
    analysisId: string,
    payload: ReviewRequest
  ): Promise<VisionAnalysisResult> {
    return ApiClient.requestJson<VisionAnalysisResult>(baseUrl, apiKey,
      `/api/v1/analyses/${encodeURIComponent(analysisId)}/review`, http.RequestMethod.POST,
      '保存复核结果失败', 'application/json', reviewRequestBody(payload))
  }
}
