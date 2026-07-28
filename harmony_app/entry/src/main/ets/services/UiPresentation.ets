export type LoadPhase = 'idle' | 'loading' | 'content' | 'empty' | 'error'

export interface TaskPresentation {
  symbol: string
  label: string
  tone: string
  terminal: boolean
}

export interface ResultConclusion {
  symbol: string
  label: string
  message: string
  tone: string
}

export const THEME_COLOR_ROLES: string[] = [
  'page_background',
  'surface',
  'primary',
  'text_primary',
  'text_secondary',
  'border',
  'success',
  'warning',
  'danger',
  'skeleton'
]

export function responsiveColumns(widthVp: number): number {
  return widthVp < 360 ? 1 : 2
}

export function resolveLoadPhase(loading: boolean, itemCount: number, error: string): LoadPhase {
  if (loading) {
    return 'loading'
  }
  if (error.trim().length > 0) {
    return 'error'
  }
  return itemCount > 0 ? 'content' : 'empty'
}

export function taskPresentation(status: string): TaskPresentation {
  if (status === 'queued') {
    return { symbol: '◷', label: '等待推理', tone: 'warning', terminal: false }
  }
  if (status === 'running') {
    return { symbol: '⌬', label: '模型识别中', tone: 'primary', terminal: false }
  }
  if (status === 'completed') {
    return { symbol: '✓', label: '识别完成', tone: 'success', terminal: true }
  }
  if (status === 'failed') {
    return { symbol: '!', label: '任务失败', tone: 'danger', terminal: true }
  }
  return { symbol: '·', label: '尚未提交', tone: 'neutral', terminal: false }
}

export function formatElapsed(createdAt: string, nowMs: number): string {
  const createdMs = Date.parse(createdAt)
  if (!Number.isFinite(createdMs) || nowMs <= createdMs) {
    return '刚刚'
  }
  const seconds = Math.floor((nowMs - createdMs) / 1000)
  if (seconds < 60) {
    return `${seconds} 秒`
  }
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `${minutes} 分 ${remainder} 秒`
}

export function compactText(value: string, maxLength: number): string {
  const normalized = value.trim()
  if (maxLength < 2 || normalized.length <= maxLength) {
    return normalized
  }
  return `${normalized.substring(0, maxLength - 1)}…`
}

export function resultConclusion(
  status: string,
  inputType: string,
  reviewStatus: string,
  confirmed: boolean,
  needsReview: boolean
): ResultConclusion {
  if (status !== 'success') {
    return { symbol: '!', label: '分析失败', message: '当前报告没有可用结构，请返回识别页重新提交。', tone: 'danger' }
  }
  if (inputType !== 'image') {
    return { symbol: '✓', label: '性质计算完成', message: '该结构来自手动输入，无需进行图片结构复核。', tone: 'success' }
  }
  if (confirmed || reviewStatus === 'confirmed') {
    return { symbol: '✓', label: '结构已人工确认', message: '组员已核对当前结构，可继续用于团队内部分析。', tone: 'success' }
  }
  if (reviewStatus === 'unable_to_confirm') {
    return { symbol: '?', label: '当前无法确认', message: '建议更换清晰、完整且仅包含一个分子的结构图后重新识别。', tone: 'warning' }
  }
  if (needsReview || reviewStatus === 'unconfirmed') {
    return { symbol: '!', label: '等待人工复核', message: '请对照上传原图与重绘结构，确认或修正模型结果。', tone: 'warning' }
  }
  return { symbol: '✓', label: '分析完成', message: '结构和分子性质已计算完成。', tone: 'success' }
}

export function qualityReasonLabel(code: string): string {
  const labels: Record<string, string> = {
    'low_contrast': '图像对比度偏低，键线或元素文字可能不够清晰',
    'blurred': '图像清晰度不足，建议使用更清晰的结构图',
    'too_small': '图片尺寸偏小，细节可能丢失',
    'low_ink_ratio': '有效结构线条较少或图片留白较多',
    'high_ink_ratio': '图像内容过密，可能包含文字或多个结构',
    'border_ink': '结构靠近图片边缘，可能存在裁切',
    'calibrated_confidence_missing': '模型未提供已校准置信度，需要人工核对',
    'backend_disagreement': '多个识别策略结果不一致，需要人工核对',
    'low_image_quality': '图片质量不足，识别结果需要人工核对'
  }
  return labels[code] === undefined ? code.replaceAll('_', ' ') : labels[code]
}

export function reviewReasonLabel(code: string | null): string {
  if (code === null || code.length === 0) {
    return '未填写原因'
  }
  const labels: Record<string, string> = {
    'image_unclear': '图片模糊',
    'structure_incomplete': '结构残缺或被裁切',
    'multiple_molecules': '图片包含多个分子',
    'model_result_unreliable': '模型结果不可信',
    'other': '其他原因'
  }
  return labels[code] === undefined ? code : labels[code]
}

export function reviewActionLabel(action: string): string {
  const labels: Record<string, string> = {
    'confirm_structure': '确认结构',
    'revoke_confirmation': '撤销确认',
    'unable_to_confirm': '标记无法确认',
    'candidate_changed': '候选结构已变更'
  }
  return labels[action] === undefined ? action : labels[action]
}

export function formatReportTime(value: string): string {
  if (value.length < 10) {
    return value.length === 0 ? '--' : value
  }
  return value.replace('T', ' ').substring(0, Math.min(19, value.length))
}
