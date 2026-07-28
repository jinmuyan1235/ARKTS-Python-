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

export function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, '')
}

export function validateConnectionForm(baseUrl: string, apiKey: string): FormValidation {
  const normalized = normalizeBaseUrl(baseUrl)
  if (!normalized.startsWith('http://') && !normalized.startsWith('https://')) {
    return { valid: false, message: '服务地址必须以 http:// 或 https:// 开头。' }
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
