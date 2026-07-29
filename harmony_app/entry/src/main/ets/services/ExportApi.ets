import http from '@ohos.net.http'
import { ExportItem, ExportManifest } from '../model/ExportResult'
import { ApiClient, ApiError } from './ApiClient'

const EXPORT_CONNECT_TIMEOUT_MS: number = 15000
const EXPORT_READ_TIMEOUT_MS: number = 60000

export function exportManifestPath(scope: string, resourceId: string): string {
  const encodedId: string = encodeURIComponent(resourceId)
  return scope === 'batch' ? `/api/v1/batch-jobs/${encodedId}/exports` :
    `/api/v1/analyses/${encodedId}/exports`
}
export function exportFileName(item: ExportItem): string {
  if (item.fileName !== null && item.fileName.trim().length > 0) {
    return item.fileName
  }
  return `molecule-export.${item.format}`
}

export function exportSizeLabel(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`
  if (sizeBytes < 1024 * 1024) return `${Math.round(sizeBytes / 1024)} KB`
  return `${Math.round(sizeBytes / 1024 / 1024 * 10) / 10} MB`
}

export class ExportApi {
  static async manifest(baseUrl: string, apiKey: string, scope: string,
    resourceId: string): Promise<ExportManifest> {
    return ApiClient.requestJson<ExportManifest>(baseUrl, apiKey,
      exportManifestPath(scope, resourceId), http.RequestMethod.GET, '读取导出中心失败')
  }

  /** Signed artifact URLs are self-authenticating and intentionally omit app credentials. */
  static async download(item: ExportItem): Promise<ArrayBuffer> {
    if (!item.available || item.downloadUrl === null || item.downloadUrl.length === 0) {
      throw new ApiError(409, item.reason.length > 0 ? item.reason : '该格式暂不可下载。')
    }
    const request = http.createHttp()
    try {
      const options: http.HttpRequestOptions = {
        method: http.RequestMethod.GET,
        expectDataType: http.HttpDataType.ARRAY_BUFFER,
        connectTimeout: EXPORT_CONNECT_TIMEOUT_MS,
        readTimeout: EXPORT_READ_TIMEOUT_MS
      }
      const response = await request.request(item.downloadUrl, options)
      if (response.responseCode < 200 || response.responseCode >= 300) {
        throw new ApiError(response.responseCode,
          response.responseCode === 410 ? '下载链接已过期，请刷新导出中心后重试。' : '下载导出文件失败，请重试。')
      }
      if (response.result instanceof ArrayBuffer) {
        return response.result
      }
      throw new ApiError(0, '导出文件内容异常，请重新下载。')
    } catch (error) {
      if (error instanceof ApiError) throw error
      throw new ApiError(0, '无法下载导出文件，请检查与电脑端的连接。')
    } finally {
      request.destroy()
    }
  }
}
