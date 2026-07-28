export interface FormValidation {
  valid: boolean
  message: string
}

export interface SensitiveMigrationPlan {
  apiKey: string
  authToken: string
  clearLegacy: boolean
}

export interface TaskUiState {
  terminal: boolean
  success: boolean
  keepPolling: boolean
}

interface ErrorMessagePayload {
  detail?: string
}

export function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, '')
}

export function validateConnectionForm(baseUrl: string, apiKey: string): FormValidation {
  const normalized = normalizeBaseUrl(baseUrl)
  if (!normalized.startsWith('http://') && !normalized.startsWith('https://')) {
    return { valid: false, message: '服务地址必须以 http:// 或 https:// 开头。' }
  }
  const endpoint: string = normalized.toLowerCase()
  if (/^https?:\/\/(0\.0\.0\.0|127\.0\.0\.1|localhost|\[::1\])(?::\d+)?(?:\/|$)/.test(endpoint)) {
    return {
      valid: false,
      message: '模拟器不能使用 0.0.0.0、127.0.0.1 或 localhost，请填写电脑的局域网 IP，例如 http://192.168.5.13:8000。'
    }
  }
  if (apiKey.trim().length === 0) {
    return { valid: false, message: '请填写启动本地服务时设置的 API 密钥。' }
  }
  return { valid: true, message: '' }
}

export function validateLoginForm(username: string, password: string): FormValidation {
  if (username.trim().length === 0 || password.length === 0) {
    return { valid: false, message: '请输入用户名和密码。' }
  }
  return { valid: true, message: '' }
}

export function validateRegistrationForm(
  username: string,
  displayName: string,
  password: string
): FormValidation {
  if (!/^[A-Za-z0-9_.-]{3,32}$/.test(username.trim())) {
    return { valid: false, message: '用户名需为 3–32 位字母、数字、点、横线或下划线。' }
  }
  if (displayName.trim().length === 0) {
    return { valid: false, message: '请填写显示名称。' }
  }
  if (password.length < 8) {
    return { valid: false, message: '密码至少需要 8 位。' }
  }
  return { valid: true, message: '' }
}

export function isTerminalJobStatus(status: string): boolean {
  return status === 'completed' || status === 'failed'
}

export function taskUiState(status: string): TaskUiState {
  return {
    terminal: isTerminalJobStatus(status),
    success: status === 'completed',
    keepPolling: status === 'queued' || status === 'running'
  }
}

export function planSensitiveMigration(
  secureApiKey: string,
  secureAuthToken: string,
  legacyApiKey: string,
  legacyAuthToken: string
): SensitiveMigrationPlan {
  return {
    apiKey: secureApiKey.length > 0 ? secureApiKey : legacyApiKey,
    authToken: secureAuthToken.length > 0 ? secureAuthToken : legacyAuthToken,
    clearLegacy: legacyApiKey.length > 0 || legacyAuthToken.length > 0
  }
}

export function resolveHistoryOwner(
  scope: string,
  currentUserId: string,
  selectedOwnerUserId: string
): string {
  if (scope === 'mine') {
    return currentUserId
  }
  return selectedOwnerUserId
}

export function friendlyHttpError(status: number, detail: string, action: string): string {
  if (status === 401) {
    return '登录已失效，或 API 密钥不正确，请重新登录并检查设置。'
  }
  if (status === 403) {
    return '当前账号无权执行此操作。'
  }
  if (status === 404) {
    return '请求的数据已不存在，请刷新后重试。'
  }
  if (status === 413) {
    return '图片过大，请选择不超过 15 MB 的图片。'
  }
  if (status === 422 && detail.length > 0) {
    return detail
  }
  if (status >= 500) {
    return '本地识别服务暂时不可用，请检查电脑端服务后重试。'
  }
  return detail.length > 0 ? detail : `${action}失败，请稍后重试。`
}

/**
 * Last safety boundary before an error reaches a Toast or error card.
 * It accepts already-friendly ApiError messages, but rejects raw JSON,
 * object stringification, stack traces and low-level transport diagnostics.
 */
export function userFacingError(error: Object | string, fallback: string): string {
  let message: string = ''
  if (typeof error === 'string') {
    message = error.trim()
  } else if (error instanceof Error) {
    message = error.message.trim()
  }
  if (message.startsWith('{') || message.startsWith('[')) {
    try {
      const payload = JSON.parse(message) as ErrorMessagePayload
      message = typeof payload.detail === 'string' ? payload.detail.trim() : ''
    } catch (_) {
      message = ''
    }
  }
  message = message.replace(/^ApiError:\s*/i, '').replace(/^Error:\s*/i, '').trim()
  const technical: string = message.toLowerCase()
  if (technical.indexOf('econnrefused') >= 0 || technical.indexOf('enetunreach') >= 0 ||
    technical.indexOf('socket') >= 0 || technical.indexOf('network is unreachable') >= 0 ||
    technical.indexOf('timed out') >= 0 || technical.indexOf('timeout') >= 0) {
    return '无法连接电脑端服务，请检查网络、服务地址和电脑端进程后重试。'
  }
  if (message.length === 0 || technical.indexOf('[object object]') >= 0 ||
    technical.indexOf('traceback') >= 0 || technical.indexOf('syntaxerror') >= 0 ||
    technical.indexOf('unexpected token') >= 0 || technical.indexOf('\n    at ') >= 0 ||
    technical.indexOf('.ets:') >= 0 || technical.indexOf('.py:') >= 0) {
    return fallback
  }
  if (message.length > 180) {
    return `${message.substring(0, 179)}…`
  }
  return message
}
