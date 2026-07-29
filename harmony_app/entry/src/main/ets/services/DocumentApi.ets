import http from '@ohos.net.http'
import {
  DocumentJob,
  DocumentRegionEdit,
  DocumentRegionInfo,
  DocumentResult
} from '../model/DocumentResult'
import { ApiClient, MultipartImageBody } from './ApiClient'

export interface DocumentUploadInfo {
  fileName: string
  contentType: string
}

export function documentUploadInfo(uri: string): DocumentUploadInfo {
  const cleanUri: string = uri.split('?')[0].toLowerCase()
  if (cleanUri.endsWith('.png')) {
    return { fileName: 'document.png', contentType: 'image/png' }
  }
  if (cleanUri.endsWith('.jpg') || cleanUri.endsWith('.jpeg')) {
    return { fileName: 'document.jpg', contentType: 'image/jpeg' }
  }
  if (cleanUri.endsWith('.zip')) {
    return { fileName: 'document.zip', contentType: 'application/zip' }
  }
  return { fileName: 'document.pdf', contentType: 'application/pdf' }
}

/** HTTP adapter for upload, detection, editing and confirmed region OCSR. */
export class DocumentApi {
  static async upload(baseUrl: string, apiKey: string, uri: string): Promise<DocumentResult> {
    const info: DocumentUploadInfo = documentUploadInfo(uri)
    const multipart: MultipartImageBody = ApiClient.createDocumentMultipart(
      uri, 'file', info.fileName, info.contentType, '----HarmonyDocument'
    )
    return ApiClient.requestJson<DocumentResult>(
      baseUrl, apiKey, '/api/v1/documents', http.RequestMethod.POST,
      '上传文档失败', multipart.contentType, multipart.body
    )
  }

  static async detect(baseUrl: string, apiKey: string, documentId: string): Promise<DocumentJob> {
    return ApiClient.requestJson<DocumentJob>(
      baseUrl, apiKey, `/api/v1/documents/${encodeURIComponent(documentId)}/detect`,
      http.RequestMethod.POST, '提交区域检测失败'
    )
  }

  static async document(baseUrl: string, apiKey: string, documentId: string): Promise<DocumentResult> {
    return ApiClient.requestJson<DocumentResult>(
      baseUrl, apiKey, `/api/v1/documents/${encodeURIComponent(documentId)}`,
      http.RequestMethod.GET, '读取文档检测结果失败'
    )
  }

  static async job(baseUrl: string, apiKey: string, jobId: string): Promise<DocumentJob> {
    return ApiClient.requestJson<DocumentJob>(
      baseUrl, apiKey, `/api/v1/jobs/${encodeURIComponent(jobId)}`,
      http.RequestMethod.GET, '查询文档任务失败'
    )
  }

  private static async control(baseUrl: string, apiKey: string, jobId: string, action: string): Promise<DocumentJob> {
    return ApiClient.requestJson<DocumentJob>(baseUrl, apiKey,
      `/api/v1/jobs/${encodeURIComponent(jobId)}/${action}`, http.RequestMethod.POST, '控制文档任务失败')
  }

  static pauseJob(baseUrl: string, apiKey: string, jobId: string): Promise<DocumentJob> {
    return DocumentApi.control(baseUrl, apiKey, jobId, 'pause')
  }

  static resumeJob(baseUrl: string, apiKey: string, jobId: string): Promise<DocumentJob> {
    return DocumentApi.control(baseUrl, apiKey, jobId, 'resume')
  }

  static cancelJob(baseUrl: string, apiKey: string, jobId: string): Promise<DocumentJob> {
    return DocumentApi.control(baseUrl, apiKey, jobId, 'cancel')
  }

  static async editRegion(
    baseUrl: string,
    apiKey: string,
    documentId: string,
    regionId: string,
    edit: DocumentRegionEdit
  ): Promise<DocumentRegionInfo> {
    return ApiClient.requestJson<DocumentRegionInfo>(
      baseUrl,
      apiKey,
      `/api/v1/documents/${encodeURIComponent(documentId)}/regions/${encodeURIComponent(regionId)}`,
      http.RequestMethod.PUT,
      '保存区域失败',
      'application/json',
      JSON.stringify(edit)
    )
  }

  static async recognizeRegion(
    baseUrl: string,
    apiKey: string,
    documentId: string,
    regionId: string,
    note: string = ''
  ): Promise<DocumentJob> {
    return ApiClient.requestJson<DocumentJob>(
      baseUrl,
      apiKey,
      `/api/v1/documents/${encodeURIComponent(documentId)}/regions/${encodeURIComponent(regionId)}/recognize`,
      http.RequestMethod.POST,
      '提交区域识别失败',
      'application/json',
      JSON.stringify({ confirmed: true, note: note })
    )
  }
}
