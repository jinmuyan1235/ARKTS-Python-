import { describe, it, expect } from '@ohos/hypium'
import {
  normalizeBaseUrl,
  planSensitiveMigration,
  resolveHistoryOwner,
  taskUiState
} from '../../../main/ets/services/AppUtils'
import {
  resolveLoadPhase,
  responsiveColumns,
  resultConclusion,
  qualityReasonLabel,
  taskPresentation,
  THEME_COLOR_ROLES
} from '../../../main/ets/services/UiPresentation'

export default function abilityTest() {
  describe('MoleculeVisionDeviceCore', () => {
    it('restoresActiveTaskState', 0, () => {
      expect(taskUiState('running').keepPolling).assertTrue()
      expect(taskUiState('completed').keepPolling).assertFalse()
    })

    it('keepsSecureValuesDuringUpgrade', 0, () => {
      const plan = planSensitiveMigration('asset-api', 'asset-token', 'legacy-api', 'legacy-token')
      expect(plan.apiKey).assertEqual('asset-api')
      expect(plan.authToken).assertEqual('asset-token')
    })

    it('supportsTeamHistoryScope', 0, () => {
      expect(resolveHistoryOwner('mine', 'u1', '')).assertEqual('u1')
      expect(resolveHistoryOwner('all', 'u1', 'u2')).assertEqual('u2')
      expect(normalizeBaseUrl('http://10.0.2.2:8000/')).assertEqual('http://10.0.2.2:8000')
    })

    it('supportsPhoneVisualBreakpoints', 0, () => {
      expect(responsiveColumns(320)).assertEqual(1)
      expect(responsiveColumns(480)).assertEqual(2)
    })

    it('exposesAccessibleVisualStates', 0, () => {
      expect(taskPresentation('queued').label).assertEqual('等待推理')
      expect(taskPresentation('failed').symbol).assertEqual('!')
      expect(resolveLoadPhase(false, 0, '')).assertEqual('empty')
      expect(THEME_COLOR_ROLES.indexOf('text_primary') >= 0).assertTrue()
    })

    it('supportsLaboratoryReviewStates', 0, () => {
      expect(resultConclusion('success', 'image', 'unable_to_confirm', false, true).symbol).assertEqual('?')
      expect(resultConclusion('success', 'image', 'confirmed', true, false).label).assertContain('确认')
      expect(qualityReasonLabel('border_ink')).assertContain('边缘')
    })
  })
}
