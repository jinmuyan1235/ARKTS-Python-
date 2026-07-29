import fs from '@ohos.file.fs'
import http from '@ohos.net.http'
import { friendlyHttpError, normalizeBaseUrl } from './AppUtils'

const CONNECT_TIMEOUT_MS: number = 15000
const REQUEST_TIMEOUT_MS: number = 30000
const MAX_IMAGE_BYTES: number = 15 * 1024 * 1024
const MAX_DOCUMENT_BYTES: number = 100 * 1024 * 1024
const MAX_BATCH_IMAGES: number = 30
const MAX_BATCH_TOTAL_BYTES: number = 120 * 1024 * 1024

interface ErrorPayload {
  detail?: string
}

export interface MultipartImageBody {
  contentType: string
  body: ArrayBuffer
}

export class ApiError extends Error {
  statusCode: number

  constructor(statusCode: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.statusCode = statusCode
  }
}

export function parseApiJson<T>(text: string, action: string): T {
  try {
    const parsed = JSON.parse(text) as T
    if (parsed === null || parsed === undefined) {
      throw new Error('empty response')
    }
    return parsed
  } catch (_) {
    throw new ApiError(0, `${action}返回的数据格式异常，请刷新后重试。`)
  }
}

export function apiErrorFromResponse(code: number, text: string, action: string): ApiError | null {
  if (code >= 200 && code < 300) {
    return null
  }
  let detail: string = ''
  try {
    const payload = JSON.parse(text) as ErrorPayload
    detail = typeof payload.detail === 'string' ? payload.detail.trim() : ''
  } catch (_) {
    detail = ''
  }
  return new ApiError(code, friendlyHttpError(code, detail, action))
}

export function isUnauthorizedError(error: Object): boolean {
  return error instanceof ApiError && error.statusCode === 401
}

export function isTransportError(error: Object): boolean {
  return error instanceof ApiError && error.statusCode === 0
}

/**
 * HarmonyOS NetStack rejects an empty `extraData` value for body-less POST
 * requests (local error 401). Omit the option entirely when there is no body.
 */
export function hasRequestBody(body: string | ArrayBuffer): boolean {
  return typeof body === 'string' ? body.length > 0 : body.byteLength > 0
}

/**
 * Shared HTTP foundation for all domain APIs.
 *
 * It owns authentication headers, timeout policy, response/error mapping and
 * multipart image encoding. Domain APIs only describe endpoints and DTOs.
 */
export class ApiClient {
  private static sessionToken: string = ''

  static setSessionToken(token: string): void {
    ApiClient.sessionToken = token.trim()
  }

  static async requestJson<T>(
    baseUrl: string,
    apiKey: string,
    path: string,
    method: http.RequestMethod,
    action: string,
    contentType: string = '',
    body: string | ArrayBuffer = ''
  ): Promise<T> {
    const request = http.createHttp()
    try {
      const options: http.HttpRequestOptions = {
        method: method,
        header: ApiClient.headers(apiKey, contentType),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      }
      if (hasRequestBody(body)) {
        options.extraData = body
      }
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}${path}`, options)
      const text: string = ApiClient.responseText(response.result)
      ApiClient.assertSuccess(response.responseCode, text, action)
      return parseApiJson<T>(text, action)
    } catch (error) {
      if (error instanceof ApiError) {
        throw error
      }
      throw new ApiError(0, `${action}：无法连接电脑端服务，请检查网络和服务地址后重试。`)
    } finally {
      request.destroy()
    }
  }

  static async requestVoid(
    baseUrl: string,
    apiKey: string,
    path: string,
    method: http.RequestMethod,
    action: string,
    contentType: string = '',
    body: string | ArrayBuffer = ''
  ): Promise<void> {
    const request = http.createHttp()
    try {
      const options: http.HttpRequestOptions = {
        method: method,
        header: ApiClient.headers(apiKey, contentType),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      }
      if (hasRequestBody(body)) {
        options.extraData = body
      }
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}${path}`, options)
      ApiClient.assertSuccess(response.responseCode, ApiClient.responseText(response.result), action)
    } catch (error) {
      if (error instanceof ApiError) {
        throw error
      }
      throw new ApiError(0, `${action}：无法连接电脑端服务，请检查网络和服务地址后重试。`)
    } finally {
      request.destroy()
    }
  }

  static createImageMultipart(
    imageUri: string,
    fieldName: string,
    fileName: string,
    boundaryPrefix: string
  ): MultipartImageBody {
    const imageBytes: Uint8Array = ApiClient.readImage(imageUri)
    const boundary: string = `${boundaryPrefix}${new Date().getTime()}`
    const prefix: string =
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="${fieldName}"; filename="${fileName}"\r\n` +
      'Content-Type: image/png\r\n\r\n'
    const suffix: string = `\r\n--${boundary}--\r\n`
    return {
      contentType: `multipart/form-data; boundary=${boundary}`,
      body: ApiClient.concatBytes([ApiClient.asciiBytes(prefix), imageBytes, ApiClient.asciiBytes(suffix)])
    }
  }

  static createDocumentMultipart(
    documentUri: string,
    fieldName: string,
    fileName: string,
    fileContentType: string,
    boundaryPrefix: string
  ): MultipartImageBody {
    const documentBytes: Uint8Array = ApiClient.readDocument(documentUri)
    const boundary: string = `${boundaryPrefix}${new Date().getTime()}`
    const prefix: string =
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="${fieldName}"; filename="${fileName}"\r\n` +
      `Content-Type: ${fileContentType}\r\n\r\n`
    const suffix: string = `\r\n--${boundary}--\r\n`
    return {
      contentType: `multipart/form-data; boundary=${boundary}`,
      body: ApiClient.concatBytes([ApiClient.asciiBytes(prefix), documentBytes, ApiClient.asciiBytes(suffix)])
    }
  }

  static createImagesMultipart(
    imageUris: string[],
    fieldName: string,
    boundaryPrefix: string
  ): MultipartImageBody {
    if (imageUris.length === 0) {
      throw new Error('请至少选择一张图片。')
    }
    if (imageUris.length > MAX_BATCH_IMAGES) {
      throw new Error(`单次最多选择 ${MAX_BATCH_IMAGES} 张图片。`)
    }
    const boundary: string = `${boundaryPrefix}${new Date().getTime()}`
    const parts: Uint8Array[] = []
    let totalBytes: number = 0
    imageUris.forEach((uri: string, index: number) => {
      const imageBytes: Uint8Array = ApiClient.readImage(uri)
      totalBytes += imageBytes.byteLength
      if (totalBytes > MAX_BATCH_TOTAL_BYTES) {
        throw new Error('所选图片总大小不能超过 120 MB。')
      }
      const suffix: string = ApiClient.imageSuffix(uri)
      const contentType: string = suffix === '.jpg' || suffix === '.jpeg' ? 'image/jpeg' :
        (suffix === '.webp' ? 'image/webp' : (suffix === '.bmp' ? 'image/bmp' : 'image/png'))
      const prefix: string =
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="${fieldName}"; filename="batch_${index + 1}${suffix}"\r\n` +
        `Content-Type: ${contentType}\r\n\r\n`
      parts.push(ApiClient.asciiBytes(prefix))
      parts.push(imageBytes)
      parts.push(ApiClient.asciiBytes('\r\n'))
    })
    parts.push(ApiClient.asciiBytes(`--${boundary}--\r\n`))
    return {
      contentType: `multipart/form-data; boundary=${boundary}`,
      body: ApiClient.concatBytes(parts)
    }
  }

  private static headers(apiKey: string, contentType: string): Record<string, string> {
    const headers: Record<string, string> = {
      'Accept': 'application/json',
      'X-API-Key': apiKey.trim()
    }
    if (contentType.length > 0) {
      headers['Content-Type'] = contentType
    }
    if (ApiClient.sessionToken.length > 0) {
      headers['Authorization'] = `Bearer ${ApiClient.sessionToken}`
    }
    return headers
  }

  private static readImage(uri: string): Uint8Array {
    const file = fs.openSync(uri, fs.OpenMode.READ_ONLY)
    try {
      const size: number = fs.statSync(file.fd).size
      if (size <= 0) {
        throw new Error('所选图片为空。')
      }
      if (size > MAX_IMAGE_BYTES) {
        throw new Error('图片不能超过 15 MB。')
      }
      const buffer = new ArrayBuffer(size)
      const bytesRead: number = fs.readSync(file.fd, buffer)
      if (bytesRead !== size) {
        throw new Error('图片读取不完整，请重新选择。')
      }
      return new Uint8Array(buffer)
    } finally {
      fs.closeSync(file)
    }
  }

  private static imageSuffix(uri: string): string {
    const clean: string = uri.split('?')[0].toLowerCase()
    const supported: string[] = ['.png', '.jpg', '.jpeg', '.webp', '.bmp']
    for (let index: number = 0; index < supported.length; index++) {
      if (clean.endsWith(supported[index])) {
        return supported[index]
      }
    }
    return '.png'
  }

  private static readDocument(uri: string): Uint8Array {
    const file = fs.openSync(uri, fs.OpenMode.READ_ONLY)
    try {
      const size: number = fs.statSync(file.fd).size
      if (size <= 0) {
        throw new Error('所选文档为空。')
      }
      if (size > MAX_DOCUMENT_BYTES) {
        throw new Error('文档不能超过 100 MB。')
      }
      const buffer = new ArrayBuffer(size)
      const bytesRead: number = fs.readSync(file.fd, buffer)
      if (bytesRead !== size) {
        throw new Error('文档读取不完整，请重新选择。')
      }
      return new Uint8Array(buffer)
    } finally {
      fs.closeSync(file)
    }
  }

  private static asciiBytes(text: string): Uint8Array {
    const result = new Uint8Array(text.length)
    for (let index = 0; index < text.length; index++) {
      result[index] = text.charCodeAt(index) & 0xff
    }
    return result
  }

  private static concatBytes(parts: Uint8Array[]): ArrayBuffer {
    let total: number = 0
    parts.forEach((part: Uint8Array) => total += part.byteLength)
    const result = new Uint8Array(total)
    let offset: number = 0
    parts.forEach((part: Uint8Array) => {
      result.set(part, offset)
      offset += part.byteLength
    })
    return result.buffer
  }

  private static responseText(result: string | Object | ArrayBuffer): string {
    if (typeof result === 'string') {
      return result
    }
    if (result instanceof ArrayBuffer) {
      return ''
    }
    try {
      return JSON.stringify(result)
    } catch (_) {
      return ''
    }
  }

  private static assertSuccess(code: number, text: string, action: string): void {
    const error: ApiError | null = apiErrorFromResponse(code, text, action)
    if (error !== null) {
      throw error
    }
  }
}
