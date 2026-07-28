import { describe, it, expect } from '@ohos/hypium'
import {
  friendlyHttpError,
  normalizeBaseUrl,
  planSensitiveMigration,
  resolveHistoryOwner,
  taskUiState,
  validateConnectionForm,
  validateLoginForm,
  validateRegistrationForm
} from '../main/ets/services/AppUtils'
import {
  compactText,
  formatReportTime,
  formatElapsed,
  qualityReasonLabel,
  resolveLoadPhase,
  responsiveColumns,
  resultConclusion,
  reviewActionLabel,
  reviewReasonLabel,
  taskPresentation,
  THEME_COLOR_ROLES
} from '../main/ets/services/UiPresentation'

export default function localUnitTest() {
  describe('MoleculeVisionCore', () => {
    it('normalizesServiceUrls', 0, () => {
      expect(normalizeBaseUrl(' http://192.168.5.13:8000/// ')).assertEqual('http://192.168.5.13:8000')
      expect(validateConnectionForm('192.168.5.13:8000', 'secret').valid).assertFalse()
      expect(validateConnectionForm('http://192.168.5.13:8000', '').valid).assertFalse()
      expect(validateConnectionForm('https://lab.local', 'secret').valid).assertTrue()
    })

    it('validatesAccountForms', 0, () => {
      expect(validateLoginForm('', 'password').valid).assertFalse()
      expect(validateRegistrationForm('ab', 'Team Member', '12345678').valid).assertFalse()
      expect(validateRegistrationForm('chemist_01', '', '12345678').valid).assertFalse()
      expect(validateRegistrationForm('chemist_01', 'Team Member', 'short').valid).assertFalse()
      expect(validateRegistrationForm('chemist_01', 'Team Member', 'safe-pass-123').valid).assertTrue()
    })

    it('plansSensitivePreferenceMigration', 0, () => {
      const legacy = planSensitiveMigration('', '', 'api-secret', 'session-token')
      expect(legacy.apiKey).assertEqual('api-secret')
      expect(legacy.authToken).assertEqual('session-token')
      expect(legacy.clearLegacy).assertTrue()
      const secure = planSensitiveMigration('secure-api', 'secure-token', 'old-api', 'old-token')
      expect(secure.apiKey).assertEqual('secure-api')
      expect(secure.authToken).assertEqual('secure-token')
      expect(secure.clearLegacy).assertTrue()
    })

    it('mapsTaskStateMachine', 0, () => {
      expect(taskUiState('queued').keepPolling).assertTrue()
      expect(taskUiState('running').terminal).assertFalse()
      expect(taskUiState('completed').success).assertTrue()
      expect(taskUiState('failed').terminal).assertTrue()
    })

    it('resolvesSharedHistoryFilters', 0, () => {
      expect(resolveHistoryOwner('mine', 'current-user', 'other-user')).assertEqual('current-user')
      expect(resolveHistoryOwner('all', 'current-user', 'other-user')).assertEqual('other-user')
      expect(resolveHistoryOwner('all', 'current-user', '')).assertEqual('')
    })

    it('mapsHttpErrorsWithoutRawJson', 0, () => {
      expect(friendlyHttpError(401, '{"detail":"raw"}', '加载')).assertContain('登录已失效')
      expect(friendlyHttpError(413, '', '上传')).assertContain('15 MB')
      expect(friendlyHttpError(422, 'SMILES 无效', '分析')).assertEqual('SMILES 无效')
      expect(friendlyHttpError(500, 'traceback', '分析')).assertContain('服务暂时不可用')
    })

    it('selectsResponsivePropertyColumns', 0, () => {
      expect(responsiveColumns(320)).assertEqual(1)
      expect(responsiveColumns(359)).assertEqual(1)
      expect(responsiveColumns(360)).assertEqual(2)
      expect(responsiveColumns(480)).assertEqual(2)
    })

    it('mapsVisualTaskStates', 0, () => {
      expect(taskPresentation('queued').label).assertEqual('等待推理')
      expect(taskPresentation('running').tone).assertEqual('primary')
      expect(taskPresentation('completed').terminal).assertTrue()
      expect(taskPresentation('failed').tone).assertEqual('danger')
    })

    it('resolvesAsyncContentStates', 0, () => {
      expect(resolveLoadPhase(true, 0, '')).assertEqual('loading')
      expect(resolveLoadPhase(false, 0, '连接失败')).assertEqual('error')
      expect(resolveLoadPhase(false, 0, '')).assertEqual('empty')
      expect(resolveLoadPhase(false, 3, '')).assertEqual('content')
    })

    it('formatsVisualTextAndElapsedTime', 0, () => {
      expect(formatElapsed('2026-07-27T00:00:00.000Z', Date.parse('2026-07-27T00:01:15.000Z')))
        .assertEqual('1 分 15 秒')
      expect(compactText('CC(=O)Oc1ccccc1C(=O)O', 10)).assertEqual('CC(=O)Oc1…')
    })

    it('definesCompleteSemanticThemeRoles', 0, () => {
      expect(THEME_COLOR_ROLES.length).assertLarger(9)
      expect(THEME_COLOR_ROLES.indexOf('page_background') >= 0).assertTrue()
      expect(THEME_COLOR_ROLES.indexOf('surface') >= 0).assertTrue()
      expect(THEME_COLOR_ROLES.indexOf('danger') >= 0).assertTrue()
    })

    it('presentsLaboratoryResultConclusions', 0, () => {
      expect(resultConclusion('success', 'image', 'unconfirmed', false, true).label).assertEqual('等待人工复核')
      expect(resultConclusion('success', 'image', 'confirmed', true, false).tone).assertEqual('success')
      expect(resultConclusion('success', 'image', 'unable_to_confirm', false, true).label).assertEqual('当前无法确认')
      expect(resultConclusion('success', 'smiles', 'not_required', true, false).message).assertContain('手动输入')
      expect(resultConclusion('failed', 'image', 'unconfirmed', false, true).tone).assertEqual('danger')
    })

    it('mapsReviewAndQualityAuditLabels', 0, () => {
      expect(qualityReasonLabel('low_contrast')).assertContain('对比度')
      expect(qualityReasonLabel('calibrated_confidence_missing')).assertContain('校准置信度')
      expect(reviewReasonLabel('multiple_molecules')).assertEqual('图片包含多个分子')
      expect(reviewActionLabel('revoke_confirmation')).assertEqual('撤销确认')
      expect(formatReportTime('2026-07-27T10:20:30.000Z')).assertEqual('2026-07-27 10:20:30')
    })
  })
}
