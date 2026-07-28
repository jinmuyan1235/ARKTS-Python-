import fs from '@ohos.file.fs'
import http from '@ohos.net.http'
import {
  AuthResult,
  ChangePasswordResult,
  HealthResult,
  HistoryPage,
  ReviewRequest,
  UserProfile,
  VisionAnalysisResult,
  VisionJob,
  VisionSample
} from '../model/VisionResult'
import { friendlyHttpError, normalizeBaseUrl } from './AppUtils'

const CONNECT_TIMEOUT_MS: number = 15000
const REQUEST_TIMEOUT_MS: number = 30000
const MAX_IMAGE_BYTES: number = 15 * 1024 * 1024
let sessionToken: string = ''

interface ErrorPayload {
  detail?: string
}

export class ApiError extends Error {
  statusCode: number

  constructor(statusCode: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.statusCode = statusCode
  }
}

function apiHeaders(apiKey: string, contentType: string = ''): Record<string, string> {
  const headers: Record<string, string> = {
    'Accept': 'application/json',
    'X-API-Key': apiKey.trim()
  }
  if (contentType.length > 0) {
    headers['Content-Type'] = contentType
  }
  if (sessionToken.length > 0) {
    headers['Authorization'] = `Bearer ${sessionToken}`
  }
  return headers
}

function asciiBytes(text: string): Uint8Array {
  const result = new Uint8Array(text.length)
  for (let index = 0; index < text.length; index++) {
    result[index] = text.charCodeAt(index) & 0xff
  }
  return result
}

function concatBytes(parts: Uint8Array[]): ArrayBuffer {
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

function readImage(uri: string): Uint8Array {
  const file = fs.openSync(uri, fs.OpenMode.READ_ONLY)
  try {
    const size = fs.statSync(file.fd).size
    if (size <= 0) {
      throw new Error('所选图片为空。')
    }
    if (size > MAX_IMAGE_BYTES) {
      throw new Error('图片不能超过 15 MB。')
    }
    const buffer = new ArrayBuffer(size)
    const bytesRead = fs.readSync(file.fd, buffer)
    if (bytesRead !== size) {
      throw new Error('图片读取不完整，请重新选择。')
    }
    return new Uint8Array(buffer)
  } finally {
    fs.closeSync(file)
  }
}

function responseText(result: string | Object | ArrayBuffer): string {
  if (typeof result === 'string') {
    return result
  }
  return `${result}`
}

function assertSuccess(code: number, text: string, action: string): void {
  if (code < 200 || code >= 300) {
    let detail: string = ''
    try {
      const payload = JSON.parse(text) as ErrorPayload
      detail = payload.detail || ''
    } catch (_) {
      detail = ''
    }
    throw new ApiError(code, friendlyHttpError(code, detail, action))
  }
}

export class VisionApi {
  static setSessionToken(token: string): void {
    sessionToken = token.trim()
  }

  static async register(
    baseUrl: string,
    apiKey: string,
    username: string,
    displayName: string,
    password: string,
    role: string
  ): Promise<AuthResult> {
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/auth/register`, {
        method: http.RequestMethod.POST,
        header: apiHeaders(apiKey, 'application/json'),
        extraData: JSON.stringify({
          username: username,
          displayName: displayName,
          password: password,
          role: role
        }),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '注册失败')
      return JSON.parse(text) as AuthResult
    } finally {
      request.destroy()
    }
  }

  static async login(baseUrl: string, apiKey: string, username: string, password: string): Promise<AuthResult> {
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/auth/login`, {
        method: http.RequestMethod.POST,
        header: apiHeaders(apiKey, 'application/json'),
        extraData: JSON.stringify({ username: username, password: password }),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '登录失败')
      return JSON.parse(text) as AuthResult
    } finally {
      request.destroy()
    }
  }

  static async me(baseUrl: string, apiKey: string): Promise<UserProfile> {
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/auth/me`, {
        method: http.RequestMethod.GET,
        header: apiHeaders(apiKey),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '读取登录状态失败')
      return JSON.parse(text) as UserProfile
    } finally {
      request.destroy()
    }
  }

  static async updateProfile(
    baseUrl: string,
    apiKey: string,
    displayName: string,
    role: string
  ): Promise<UserProfile> {
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/auth/me`, {
        method: http.RequestMethod.PUT,
        header: apiHeaders(apiKey, 'application/json'),
        extraData: JSON.stringify({ displayName: displayName, role: role }),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '保存个人资料')
      return JSON.parse(text) as UserProfile
    } finally {
      request.destroy()
    }
  }

  static async changePassword(
    baseUrl: string,
    apiKey: string,
    oldPassword: string,
    newPassword: string
  ): Promise<ChangePasswordResult> {
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/auth/change-password`, {
        method: http.RequestMethod.POST,
        header: apiHeaders(apiKey, 'application/json'),
        extraData: JSON.stringify({ oldPassword: oldPassword, newPassword: newPassword }),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '修改密码')
      return JSON.parse(text) as ChangePasswordResult
    } finally {
      request.destroy()
    }
  }

  static async members(baseUrl: string, apiKey: string): Promise<UserProfile[]> {
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/auth/members`, {
        method: http.RequestMethod.GET,
        header: apiHeaders(apiKey),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '读取组员失败')
      return JSON.parse(text) as UserProfile[]
    } finally {
      request.destroy()
    }
  }

  static async logout(baseUrl: string, apiKey: string): Promise<void> {
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/auth/logout`, {
        method: http.RequestMethod.POST,
        header: apiHeaders(apiKey),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      assertSuccess(response.responseCode, responseText(response.result), '退出登录失败')
    } finally {
      request.destroy()
    }
  }

  static async uploadAvatar(baseUrl: string, apiKey: string, imageUri: string): Promise<UserProfile> {
    const imageBytes = readImage(imageUri)
    const boundary = `----HarmonyAvatar${new Date().getTime()}`
    const prefix =
      `--${boundary}\r\n` +
      'Content-Disposition: form-data; name="file"; filename="avatar.png"\r\n' +
      'Content-Type: image/png\r\n\r\n'
    const suffix = `\r\n--${boundary}--\r\n`
    const body = concatBytes([asciiBytes(prefix), imageBytes, asciiBytes(suffix)])
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/auth/me/avatar`, {
        method: http.RequestMethod.POST,
        header: apiHeaders(apiKey, `multipart/form-data; boundary=${boundary}`),
        extraData: body,
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '上传头像失败')
      return JSON.parse(text) as UserProfile
    } finally {
      request.destroy()
    }
  }

  static async health(baseUrl: string, apiKey: string): Promise<HealthResult> {
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/health`, {
        method: http.RequestMethod.GET,
        header: apiHeaders(apiKey),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '服务检测失败')
      return JSON.parse(text) as HealthResult
    } finally {
      request.destroy()
    }
  }

  static async samples(baseUrl: string, apiKey: string): Promise<VisionSample[]> {
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/samples`, {
        method: http.RequestMethod.GET,
        header: apiHeaders(apiKey),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '加载示例失败')
      return JSON.parse(text) as VisionSample[]
    } finally {
      request.destroy()
    }
  }

  static async createSampleJob(baseUrl: string, apiKey: string, sampleId: string): Promise<VisionJob> {
    const request = http.createHttp()
    try {
      const response = await request.request(
        `${normalizeBaseUrl(baseUrl)}/api/v1/jobs/samples/${encodeURIComponent(sampleId)}`,
        {
          method: http.RequestMethod.POST,
          header: apiHeaders(apiKey),
          connectTimeout: CONNECT_TIMEOUT_MS,
          readTimeout: REQUEST_TIMEOUT_MS
        }
      )
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '提交示例失败')
      return JSON.parse(text) as VisionJob
    } finally {
      request.destroy()
    }
  }

  static async createImageJob(baseUrl: string, apiKey: string, imageUri: string): Promise<VisionJob> {
    const imageBytes = readImage(imageUri)
    const boundary = `----HarmonyVision${new Date().getTime()}`
    const prefix =
      `--${boundary}\r\n` +
      'Content-Disposition: form-data; name="file"; filename="molecule.png"\r\n' +
      'Content-Type: image/png\r\n\r\n'
    const suffix = `\r\n--${boundary}--\r\n`
    const body = concatBytes([asciiBytes(prefix), imageBytes, asciiBytes(suffix)])
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/jobs/images`, {
        method: http.RequestMethod.POST,
        header: apiHeaders(apiKey, `multipart/form-data; boundary=${boundary}`),
        extraData: body,
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '提交图片失败')
      return JSON.parse(text) as VisionJob
    } finally {
      request.destroy()
    }
  }

  static async getJob(baseUrl: string, apiKey: string, jobId: string): Promise<VisionJob> {
    const request = http.createHttp()
    try {
      const response = await request.request(
        `${normalizeBaseUrl(baseUrl)}/api/v1/jobs/${encodeURIComponent(jobId)}`,
        {
          method: http.RequestMethod.GET,
          header: apiHeaders(apiKey),
          connectTimeout: CONNECT_TIMEOUT_MS,
          readTimeout: REQUEST_TIMEOUT_MS
        }
      )
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '读取任务失败')
      return JSON.parse(text) as VisionJob
    } finally {
      request.destroy()
    }
  }

  static async analyzeSmiles(baseUrl: string, apiKey: string, smiles: string): Promise<VisionAnalysisResult> {
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/analyze-smiles`, {
        method: http.RequestMethod.POST,
        header: apiHeaders(apiKey, 'application/json'),
        extraData: JSON.stringify({ smiles: smiles }),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, 'SMILES 分析失败')
      return JSON.parse(text) as VisionAnalysisResult
    } finally {
      request.destroy()
    }
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
    const params =
      `query=${encodeURIComponent(query)}` +
      `&status=${encodeURIComponent(status)}` +
      `&favorites_only=${favoritesOnly}` +
      `&scope=${encodeURIComponent(scope)}` +
      `&ownerUserId=${encodeURIComponent(ownerUserId)}` +
      `&limit=20&offset=${offset}`
    const request = http.createHttp()
    try {
      const response = await request.request(`${normalizeBaseUrl(baseUrl)}/api/v1/analyses?${params}`, {
        method: http.RequestMethod.GET,
        header: apiHeaders(apiKey),
        connectTimeout: CONNECT_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '加载历史失败')
      return JSON.parse(text) as HistoryPage
    } finally {
      request.destroy()
    }
  }

  static async analysis(baseUrl: string, apiKey: string, analysisId: string): Promise<VisionAnalysisResult> {
    const request = http.createHttp()
    try {
      const response = await request.request(
        `${normalizeBaseUrl(baseUrl)}/api/v1/analyses/${encodeURIComponent(analysisId)}`,
        {
          method: http.RequestMethod.GET,
          header: apiHeaders(apiKey),
          connectTimeout: CONNECT_TIMEOUT_MS,
          readTimeout: REQUEST_TIMEOUT_MS
        }
      )
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '加载详情失败')
      return JSON.parse(text) as VisionAnalysisResult
    } finally {
      request.destroy()
    }
  }

  static async favorite(
    baseUrl: string,
    apiKey: string,
    analysisId: string,
    favorite: boolean
  ): Promise<void> {
    const request = http.createHttp()
    try {
      const response = await request.request(
        `${normalizeBaseUrl(baseUrl)}/api/v1/analyses/${encodeURIComponent(analysisId)}/favorite`,
        {
          // HarmonyOS RequestMethod does not expose PATCH; the backend keeps
          // PATCH as canonical and accepts this PUT compatibility call.
          method: http.RequestMethod.PUT,
          header: apiHeaders(apiKey, 'application/json'),
          extraData: JSON.stringify({ favorite: favorite }),
          connectTimeout: CONNECT_TIMEOUT_MS,
          readTimeout: REQUEST_TIMEOUT_MS
        }
      )
      assertSuccess(response.responseCode, responseText(response.result), '更新收藏失败')
    } finally {
      request.destroy()
    }
  }

  static async deleteAnalysis(baseUrl: string, apiKey: string, analysisId: string): Promise<void> {
    const request = http.createHttp()
    try {
      const response = await request.request(
        `${normalizeBaseUrl(baseUrl)}/api/v1/analyses/${encodeURIComponent(analysisId)}`,
        {
          method: http.RequestMethod.DELETE,
          header: apiHeaders(apiKey),
          connectTimeout: CONNECT_TIMEOUT_MS,
          readTimeout: REQUEST_TIMEOUT_MS
        }
      )
      assertSuccess(response.responseCode, responseText(response.result), '删除历史索引失败')
    } finally {
      request.destroy()
    }
  }

  static async review(
    baseUrl: string,
    apiKey: string,
    analysisId: string,
    payload: ReviewRequest
  ): Promise<VisionAnalysisResult> {
    const request = http.createHttp()
    try {
      const response = await request.request(
        `${normalizeBaseUrl(baseUrl)}/api/v1/analyses/${encodeURIComponent(analysisId)}/review`,
        {
          method: http.RequestMethod.POST,
          header: apiHeaders(apiKey, 'application/json'),
          extraData: JSON.stringify(payload),
          connectTimeout: CONNECT_TIMEOUT_MS,
          readTimeout: REQUEST_TIMEOUT_MS
        }
      )
      const text = responseText(response.result)
      assertSuccess(response.responseCode, text, '保存复核结果失败')
      return JSON.parse(text) as VisionAnalysisResult
    } finally {
      request.destroy()
    }
  }
}
