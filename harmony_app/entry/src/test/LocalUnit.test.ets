import { describe, it, expect } from '@ohos/hypium'
import {
  friendlyHttpError,
  normalizeBaseUrl,
  planSensitiveMigration,
  resolveHistoryOwner,
  taskUiState,
  userFacingError,
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
  THEME_COLOR_ROLES,
  validateReviewInput
} from '../main/ets/services/UiPresentation'
import { AuthController } from '../main/ets/controllers/AuthController'
import { ConnectionController } from '../main/ets/controllers/ConnectionController'
import { JobController, pollBackoffMs } from '../main/ets/controllers/JobController'
import { HistoryController } from '../main/ets/controllers/HistoryController'
import { BatchController, isTerminalBatchStatus, MAX_MOBILE_BATCH_IMAGES } from '../main/ets/controllers/BatchController'
import { batchCategoryLabel, batchPercent } from '../main/ets/components/BatchView'
import { BatchJob } from '../main/ets/model/BatchResult'
import {
  DEFAULT_API_BASE_URL,
  normalizeStoredDocumentTask,
  normalizeStoredJobIds,
  normalizeStoredRegionTask
} from '../main/ets/services/SettingsStore'
import { moveRegionBox, normalizeRegionBox, RegionBox, resizeRegionBox } from '../main/ets/services/RegionGeometry'
import {
  isActiveTaskStatus,
  regionDraftSignature,
  taskRecoveryAction
} from '../main/ets/services/DocumentReviewState'
import { batchGridColumns, bottomNavigationPercent, editingTouchTarget } from '../main/ets/services/MobileLayout'
import {
  cropBoundsForRect,
  cropRectForAspect,
  fullCropRect,
  isUnchangedImageEdit,
  moveCropRect,
  normalizedRotationDegrees,
  resizeCropRect
} from '../main/ets/services/ImageEditGeometry'
import { documentUploadInfo } from '../main/ets/services/DocumentApi'
import { exportFileName, exportManifestPath, exportSizeLabel } from '../main/ets/services/ExportApi'
import { ExportItem } from '../main/ets/model/ExportResult'
import { feedbackQueryPath } from '../main/ets/services/DatasetApi'
import { feedbackStatusLabel } from '../main/ets/components/DatasetReviewView'
import { connectionNeedsSetup } from '../main/ets/components/ConnectionSetupCard'
import { AuthResult, HistoryItem, HistoryPage, ReviewRequest, VisionAnalysisResult, VisionJob } from '../main/ets/model/VisionResult'
import {
  FavoritePayload,
  favoriteRequestBody,
  historyQueryParams,
  reviewRequestBody
} from '../main/ets/services/AnalysisApi'
import {
  ApiError,
  apiErrorFromResponse,
  hasRequestBody,
  isTransportError,
  isUnauthorizedError,
  parseApiJson
} from '../main/ets/services/ApiClient'
import {
  AUTH_EXPIRED_JSON,
  AUTH_SUCCESS_JSON,
  HISTORY_PAGE_ONE_JSON,
  HISTORY_PAGE_TWO_JSON,
  JOB_COMPLETED_JSON,
  JOB_QUEUED_JSON,
  JOB_RUNNING_JSON,
  MALFORMED_JSON,
  REVIEW_CONFIRMED_JSON,
  REVIEW_UNABLE_JSON
} from './fixtures/BackendResponses'

export default function localUnitTest() {
  describe('MoleculeVisionCore', () => {
    it('mapsExportCenterPathsNamesAndSizes', 0, () => {
      expect(exportManifestPath('analysis', 'analysis 1')).assertEqual('/api/v1/analyses/analysis%201/exports')
      expect(exportManifestPath('batch', 'batch/1')).assertEqual('/api/v1/batch-jobs/batch%2F1/exports')
      expect(exportSizeLabel(512)).assertEqual('512 B')
      expect(exportSizeLabel(2048)).assertEqual('2 KB')
      const item: ExportItem = {
        format: 'sdf', label: 'SDF 正式结构', available: false, reason: '仅限人工确认结果', formalOnly: true,
        fileName: null, contentType: null, sizeBytes: 0, downloadUrl: null, expiresAt: null
      }
      expect(exportFileName(item)).assertEqual('molecule-export.sdf')
    })

    it('mapsFeedbackReviewRoutesAndServerStatuses', 0, () => {
      expect(feedbackQueryPath('pending', 'CC N')).assertContain('status=pending')
      expect(feedbackQueryPath('pending', 'CC N')).assertContain('query=CC%20N')
      expect(feedbackStatusLabel('verified')).assertEqual('已核验')
      expect(feedbackStatusLabel('license_unclear')).assertEqual('许可不清')
      expect(feedbackStatusLabel('unknown')).assertEqual('待审核')
    })

    it('mapsDocumentUploadTypes', 0, () => {
      expect(documentUploadInfo('file://paper.PDF').contentType).assertEqual('application/pdf')
      expect(documentUploadInfo('file://page.png?token=1').contentType).assertEqual('image/png')
      expect(documentUploadInfo('file://pages.zip').fileName).assertEqual('document.zip')
    })

    it('tracksBatchProgressControlsAndPersistence', 0, () => {
      const controller = new BatchController()
      controller.restore('batch-restored')
      expect(controller.pendingJobId()).assertEqual('batch-restored')
      const imageUris: string[] = []
      for (let index: number = 0; index < MAX_MOBILE_BATCH_IMAGES + 5; index++) {
        imageUris.push(`file://image-${index}.png`)
      }
      controller.select(imageUris)
      expect(controller.selectedUris.length).assertEqual(MAX_MOBILE_BATCH_IMAGES)
      controller.removeSelected(0)
      expect(controller.selectedUris.length).assertEqual(MAX_MOBILE_BATCH_IMAGES - 1)
      expect(controller.remainingCapacity()).assertEqual(1)
      controller.append(['file://image-1.png', 'file://new-image.png'])
      expect(controller.selectedUris.length).assertEqual(MAX_MOBILE_BATCH_IMAGES)
      expect(controller.selectedUris[MAX_MOBILE_BATCH_IMAGES - 1]).assertEqual('file://new-image.png')
      controller.removeSelectedUris(['file://image-2.png', 'file://image-3.png'])
      expect(controller.selectedUris.indexOf('file://image-2.png')).assertEqual(-1)
      expect(controller.selectedUris.indexOf('file://image-3.png')).assertEqual(-1)
      expect(controller.remainingCapacity()).assertEqual(2)
      const job: BatchJob = {
        jobId: 'batch-restored', status: 'running', message: '识别中', backend: 'demo', source: 'upload',
        parentJobId: null, total: 10, completed: 4, progress: 0.4, currentFile: 'image-5.png', currentIndex: 5,
        categoryStats: {
          accepted: 2, acceptedWithWarning: 1, reviewNeeded: 0, rejected: 0, failed: 1, skipped: 0
        },
        results: [], createdAt: '2026-07-28T00:00:00Z', startedAt: '2026-07-28T00:00:01Z',
        finishedAt: null, updatedAt: '2026-07-28T00:00:04Z'
      }
      controller.apply(job)
      expect(controller.canPause()).assertTrue()
      expect(controller.canCancel()).assertTrue()
      expect(batchPercent(job.progress)).assertEqual(40)
      expect(batchCategoryLabel('review_needed')).assertEqual('待复核')
      job.status = 'completed'
      controller.apply(job)
      expect(controller.pendingJobId()).assertEqual('')
      expect(controller.canRetryFailed()).assertTrue()
      expect(isTerminalBatchStatus('cancelled')).assertTrue()
      controller.invalidateMissing('任务已清理')
      expect(controller.job === undefined).assertTrue()
      expect(controller.activeJobId).assertEqual('')
      expect(controller.errorMessage).assertEqual('任务已清理')
    })

    it('guidesFirstConnectionForEveryAccountType', 0, () => {
      expect(connectionNeedsSetup('', '')).assertTrue()
      expect(connectionNeedsSetup('http://192.168.1.8:8000', '')).assertTrue()
      expect(connectionNeedsSetup('http://192.168.1.8:8000', 'local-key')).assertFalse()
    })

    it('movesAndResizesDocumentRecognitionBoxes', 0, () => {
      const box: RegionBox = normalizeRegionBox([100, 200, 300, 500], 1000, 1200)
      const moved: RegionBox = moveRegionBox(box, 10, -20, 100, 240, 1000, 1200)
      expect(Math.round(moved.x1)).assertEqual(200)
      expect(Math.round(moved.y1)).assertEqual(100)
      expect(Math.round(moved.x2)).assertEqual(400)
      expect(Math.round(moved.y2)).assertEqual(400)
      const resized: RegionBox = resizeRegionBox(box, 20, 24, 100, 240, 1000, 1200)
      expect(Math.round(resized.x2)).assertEqual(500)
      expect(Math.round(resized.y2)).assertEqual(620)
    })

    it('keepsDocumentAndJobIdsConsistentForRestartRecovery', 0, () => {
      const active = normalizeStoredDocumentTask(' document-1 ', ' job-1 ')
      expect(active.documentId).assertEqual('document-1')
      expect(active.jobId).assertEqual('job-1')
      const cleared = normalizeStoredDocumentTask('', 'orphan-job')
      expect(cleared.documentId).assertEqual('')
      expect(cleared.jobId).assertEqual('')
      const region = normalizeStoredRegionTask('document-1', 'region-2', 'job-3')
      expect(region.regionId).assertEqual('region-2')
      expect(region.jobId).assertEqual('job-3')
      const orphan = normalizeStoredRegionTask('', 'region-2', 'job-3')
      expect(orphan.regionId).assertEqual('')
      expect(orphan.jobId).assertEqual('')
    })

    it('protectsDocumentDraftsAndClassifiesRecoveryErrors', 0, () => {
      const saved = regionDraftSignature({
        bbox: [10.2, 20.4, 100.1, 120.2], regionType: 'molecule', confirmed: false, note: ''
      })
      const unchanged = regionDraftSignature({
        bbox: [10, 20, 100, 120], regionType: 'molecule', confirmed: false, note: ' '
      })
      const edited = regionDraftSignature({
        bbox: [11, 20, 100, 120], regionType: 'molecule', confirmed: true, note: ''
      })
      expect(saved).assertEqual(unchanged)
      expect(saved === edited).assertFalse()
      expect(isActiveTaskStatus('network_wait')).assertTrue()
      expect(isActiveTaskStatus('auth_required')).assertTrue()
      expect(isActiveTaskStatus('completed')).assertFalse()
      expect(taskRecoveryAction(0, false)).assertEqual('keep')
      expect(taskRecoveryAction(401, true)).assertEqual('reauthenticate')
      expect(taskRecoveryAction(404, true)).assertEqual('clear_job')
      expect(taskRecoveryAction(404, false)).assertEqual('clear_document')
    })

    it('usesPhoneDefaultsAndResponsiveTouchTargets', 0, () => {
      expect(DEFAULT_API_BASE_URL).assertEqual('')
      expect(bottomNavigationPercent(5)).assertEqual(20)
      expect(bottomNavigationPercent(0)).assertEqual(100)
      expect(batchGridColumns(320)).assertEqual(2)
      expect(batchGridColumns(360)).assertEqual(3)
      expect(editingTouchTarget()).assertLarger(39)
    })

    it('calculatesSafeInAppCropBounds', 0, () => {
      const moved = moveCropRect({ left: 0.1, top: 0.1, right: 0.8, bottom: 0.8 }, 0.4, 0.4)
      expect(moved.right).assertEqual(1)
      expect(moved.bottom).assertEqual(1)
      const resized = resizeCropRect(fullCropRect(), 'bottom_right', -0.2, -0.3)
      expect(resized.right).assertEqual(0.8)
      expect(resized.bottom).assertEqual(0.7)
      const square = cropRectForAspect(1000, 800, 1)
      expect(square.right - square.left < square.bottom - square.top).assertTrue()
      const bounds = cropBoundsForRect(1000, 800, {
        left: 0.1, top: 0.1, right: 0.9, bottom: 0.9
      })
      expect(bounds.x).assertEqual(100)
      expect(bounds.y).assertEqual(80)
      expect(bounds.width).assertEqual(800)
      expect(bounds.height).assertEqual(640)
      expect(normalizedRotationDegrees(-90)).assertEqual(270)
      expect(normalizedRotationDegrees(450)).assertEqual(90)
      expect(isUnchangedImageEdit({
        rotationDegrees: 0,
        flipHorizontal: false,
        flipVertical: false,
        crop: fullCropRect()
      })).assertTrue()
    })

    it('normalizesServiceUrls', 0, () => {
      expect(normalizeBaseUrl(' http://192.168.5.13:8000/// ')).assertEqual('http://192.168.5.13:8000')
      expect(validateConnectionForm('192.168.5.13:8000', 'secret').valid).assertFalse()
      expect(validateConnectionForm('http://192.168.5.13:8000', '').valid).assertFalse()
      expect(validateConnectionForm('http://0.0.0.0:8000', 'secret').valid).assertFalse()
      expect(validateConnectionForm('http://127.0.0.1:8000', 'secret').valid).assertFalse()
      expect(validateConnectionForm('http://localhost:8000', 'secret').message).assertContain('局域网 IP')
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

    it('keepsFeatureStateInsideDomainControllers', 0, () => {
      const connection = new ConnectionController()
      connection.apiBaseUrl = 'http://192.168.5.13:8000'
      connection.apiKey = 'local-key'
      expect(connection.isConfigured()).assertTrue()
      connection.markConnected('production', 'decimer')
      connection.markTransportFailure()
      expect(connection.serviceSummary).assertContain('连接中断')
      connection.markReachable()
      expect(connection.serviceSummary).assertEqual('已连接 · production · decimer')

      const job = new JobController()
      job.activate({
        jobId: 'job-1', status: 'queued', message: '等待处理', inputKind: 'sample', analysisId: '',
        createdAt: '2026-07-28T00:00:00Z', updatedAt: '2026-07-28T00:00:00Z'
      }, 'sample:aspirin')
      expect(job.tasks[0].jobId).assertEqual('job-1')
      expect(job.tasks[0].status).assertEqual('queued')

      const history = new HistoryController()
      expect(history.beginRequest()).assertEqual(1)
      history.updateFilters('mine', '', 'success', true)
      expect(history.scope).assertEqual('mine')
      expect(history.status).assertEqual('success')
      expect(history.favoritesOnly).assertTrue()

      const auth = new AuthController()
      auth.authenticated = true
      auth.authPassword = 'temporary-password'
      auth.clearSession()
      expect(auth.authenticated).assertFalse()
      expect(auth.authPassword).assertEqual('')
    })

    it('restoresMultipleJobsAndBacksOffWithoutDuplicatePolling', 0, () => {
      const jobs = new JobController()
      jobs.restore(['job-a', 'job-b', 'job-a'])
      expect(jobs.tasks.length).assertEqual(2)
      expect(jobs.dueJobIds(Date.now() + 1).length).assertEqual(2)
      expect(jobs.beginPolling('job-a', Date.now() + 1)).assertTrue()
      expect(jobs.beginPolling('job-a', Date.now() + 1)).assertFalse()
      expect(jobs.markNetworkFailure('job-a', '网络断开', 1000)).assertEqual(2000)
      expect(jobs.tasks[0].networkWaiting).assertTrue()
      expect(pollBackoffMs(2)).assertEqual(4000)
      expect(pollBackoffMs(10)).assertEqual(30000)
      const persisted = normalizeStoredJobIds(['job-b', 'job-a', ''], 'job-a')
      expect(persisted.length).assertEqual(2)
      expect(persisted[0]).assertEqual('job-a')

      const forced = new JobController()
      forced.restore(['job-1'])
      forced.applyServerJob(parseApiJson<VisionJob>(JOB_COMPLETED_JSON, '读取任务'))
      expect(forced.pendingJobIds().length).assertEqual(0)

      const restored = new JobController()
      restored.restore(['restored-sample'])
      restored.applyServerJob({
        jobId: 'restored-sample', status: 'running', message: '识别中', inputKind: 'sample', analysisId: '',
        createdAt: '2026-07-28T00:00:00Z', updatedAt: '2026-07-28T00:00:01Z'
      })
      expect(restored.hasPendingSource('sample:aspirin')).assertTrue()
    })

    it('resolvesSharedHistoryFilters', 0, () => {
      expect(resolveHistoryOwner('mine', 'current-user', 'other-user')).assertEqual('current-user')
      expect(resolveHistoryOwner('all', 'current-user', 'other-user')).assertEqual('other-user')
      expect(resolveHistoryOwner('all', 'current-user', '')).assertEqual('')
    })

    it('protectsHistoryFiltersFromStaleRequestsAndDuplicatePaging', 0, () => {
      const history = new HistoryController()
      const oldRequest: number = history.beginRequest(true)
      history.updateFilters('mine', '', 'review_needed', true)
      const currentRequest: number = history.beginRequest(true)
      const item: HistoryItem = {
        analysisId: 'analysis-1', createdAt: '2026-07-28T00:00:00Z', inputType: 'image', filename: 'molecule.png',
        backend: 'decimer', decision: 'accepted', status: 'success', smiles: 'CCO', isFavorite: true,
        confirmed: false, needsReview: true, artifactStatus: 'available', createdBy: null
      }
      const firstPage: HistoryPage = {
        items: [item], offset: 0, limit: 20, hasMore: true, nextOffset: 1
      }
      expect(history.applyPage(oldRequest, firstPage, true)).assertFalse()
      expect(history.applyPage(currentRequest, firstPage, true)).assertTrue()
      history.finishRequest(currentRequest)
      expect(history.items.length).assertEqual(1)
      expect(history.offset).assertEqual(1)

      const pageRequest: number = history.beginRequest(false)
      expect(pageRequest > 0).assertTrue()
      expect(history.beginRequest(false)).assertEqual(0)
      const finalPage: HistoryPage = {
        items: [item], offset: 1, limit: 20, hasMore: false, nextOffset: null
      }
      expect(history.applyPage(pageRequest, finalPage, false)).assertTrue()
      history.finishRequest(pageRequest)
      expect(history.items.length).assertEqual(1)
      expect(history.hasMore).assertFalse()

      const beforeQuery: number = history.requestSerial
      history.updateQuery(' aspirin ')
      expect(history.requestSerial > beforeQuery).assertTrue()
    })

    it('buildsCompleteHistoryQueryParameters', 0, () => {
      const params: string = historyQueryParams(' caffeine ', 'review_needed', true, 20, 'all', 'member 1')
      expect(params).assertContain('query=caffeine')
      expect(params).assertContain('status=review_needed')
      expect(params).assertContain('favorites_only=true')
      expect(params).assertContain('scope=all')
      expect(params).assertContain('ownerUserId=member%201')
      expect(params).assertContain('limit=20&offset=20')
    })

    it('mapsHttpErrorsWithoutRawJson', 0, () => {
      expect(friendlyHttpError(401, '{"detail":"raw"}', '加载')).assertContain('登录已失效')
      expect(friendlyHttpError(401, '用户名或密码错误。', '登录')).assertContain('用户名或密码')
      expect(friendlyHttpError(401, 'API 密钥错误或缺失。', '检测')).assertContain('API 密钥')
      expect(friendlyHttpError(413, '', '上传')).assertContain('15 MB')
      expect(friendlyHttpError(422, 'SMILES 无效', '分析')).assertEqual('SMILES 无效')
      expect(friendlyHttpError(500, 'traceback', '分析')).assertContain('服务暂时不可用')
    })

    it('parsesBackendJsonFixturesAndRejectsMalformedResponses', 0, () => {
      const auth: AuthResult = parseApiJson<AuthResult>(AUTH_SUCCESS_JSON, '登录')
      expect(auth.user.displayName).assertEqual('杨佩骏')
      expect(auth.token).assertEqual('session-token')
      const job: VisionJob = parseApiJson<VisionJob>(JOB_QUEUED_JSON, '读取任务')
      expect(job.status).assertEqual('queued')
      const page: HistoryPage = parseApiJson<HistoryPage>(HISTORY_PAGE_ONE_JSON, '加载历史')
      expect(page.items[0].createdBy?.role).assertEqual('复核员')

      let parseMessage: string = ''
      try {
        parseApiJson<VisionJob>(MALFORMED_JSON, '读取任务')
      } catch (error) {
        parseMessage = userFacingError(error as Object, '响应解析失败。')
      }
      expect(parseMessage).assertContain('数据格式异常')
      expect(parseMessage.indexOf('SyntaxError') < 0).assertTrue()
      expect(parseMessage.indexOf('{') < 0).assertTrue()
    })

    it('omitsEmptyPostBodyRejectedByHarmonyNetStack', 0, () => {
      expect(hasRequestBody('')).assertFalse()
      expect(hasRequestBody(new ArrayBuffer(0))).assertFalse()
      expect(hasRequestBody('{}')).assertTrue()
      expect(hasRequestBody(new ArrayBuffer(1))).assertTrue()
    })

    it('expiresLoginWithoutLeakingRawErrorPayloads', 0, () => {
      const expired: ApiError | null = apiErrorFromResponse(401, AUTH_EXPIRED_JSON, '读取账号')
      expect(expired === null).assertFalse()
      if (expired !== null) {
        expect(isUnauthorizedError(expired)).assertTrue()
        expect(userFacingError(expired, '登录失败')).assertContain('登录已失效')
      }
      const auth = new AuthController()
      const signedIn: AuthResult = parseApiJson<AuthResult>(AUTH_SUCCESS_JSON, '登录')
      auth.authenticated = true
      auth.currentUser = signedIn.user
      auth.authPassword = 'temporary-secret'
      auth.clearSession()
      expect(auth.authenticated).assertFalse()
      expect(auth.currentUser === undefined).assertTrue()
      expect(auth.authPassword).assertEqual('')
    })

    it('drivesTaskLifecycleRecoveryAndNetworkRetryFromFixtures', 0, () => {
      const jobs = new JobController()
      jobs.restore(['job-1'])
      expect(jobs.tasks[0].status).assertEqual('restoring')
      jobs.applyServerJob(parseApiJson<VisionJob>(JOB_QUEUED_JSON, '读取任务'))
      expect(jobs.tasks[0].status).assertEqual('queued')
      jobs.applyServerJob(parseApiJson<VisionJob>(JOB_RUNNING_JSON, '读取任务'))
      expect(jobs.tasks[0].status).assertEqual('running')
      expect(jobs.beginPolling('job-1', jobs.tasks[0].nextPollAt)).assertTrue()
      jobs.markNetworkFailure('job-1', '网络暂时不可用', 1000)
      expect(jobs.tasks[0].networkWaiting).assertTrue()
      expect(jobs.tasks[0].failureCount).assertEqual(1)
      jobs.applyServerJob(parseApiJson<VisionJob>(JOB_RUNNING_JSON, '读取任务'))
      expect(jobs.tasks[0].networkWaiting).assertFalse()
      expect(jobs.tasks[0].failureCount).assertEqual(0)
      jobs.applyServerJob(parseApiJson<VisionJob>(JOB_COMPLETED_JSON, '读取任务'))
      expect(jobs.tasks[0].status).assertEqual('completed')
      expect(jobs.tasks[0].analysisId).assertEqual('analysis-1')
      expect(jobs.pendingCount()).assertEqual(0)
    })

    it('paginatesHistoryAndPreservesFavoriteFixtureState', 0, () => {
      const history = new HistoryController()
      const first: HistoryPage = parseApiJson<HistoryPage>(HISTORY_PAGE_ONE_JSON, '加载历史')
      const second: HistoryPage = parseApiJson<HistoryPage>(HISTORY_PAGE_TWO_JSON, '加载历史')
      const firstRequest: number = history.beginRequest(true)
      expect(history.applyPage(firstRequest, first, true)).assertTrue()
      history.finishRequest(firstRequest)
      expect(history.hasMore).assertTrue()
      const secondRequest: number = history.beginRequest(false)
      expect(history.applyPage(secondRequest, second, false)).assertTrue()
      history.finishRequest(secondRequest)
      expect(history.items.length).assertEqual(2)
      expect(history.items[1].isFavorite).assertTrue()
      expect(history.offset).assertEqual(2)
      expect(history.hasMore).assertFalse()
    })

    it('parsesHumanReviewStatesAndAppliesReviewRules', 0, () => {
      const confirmed: VisionAnalysisResult = parseApiJson<VisionAnalysisResult>(REVIEW_CONFIRMED_JSON, '加载复核')
      expect(confirmed.review.status).assertEqual('confirmed')
      expect(confirmed.review.events[0].action).assertEqual('confirm_structure')
      expect(confirmed.review.reviewedBy?.displayName).assertEqual('杨佩骏')
      const unable: VisionAnalysisResult = parseApiJson<VisionAnalysisResult>(REVIEW_UNABLE_JSON, '加载复核')
      expect(unable.review.status).assertEqual('unable_to_confirm')
      expect(unable.review.reason).assertEqual('image_unclear')
      expect(validateReviewInput('unable_to_confirm', '', 'other', '', 'image', false).valid).assertFalse()
      expect(validateReviewInput('revoke', '', '', '', 'image', confirmed.confirmed).valid).assertTrue()
      const request: ReviewRequest = { action: 'unable_to_confirm', reason: 'image_unclear', note: '原图模糊' }
      const serialized: ReviewRequest = parseApiJson<ReviewRequest>(reviewRequestBody(request), '复核请求')
      expect(serialized.action).assertEqual('unable_to_confirm')
      expect(serialized.reason).assertEqual('image_unclear')
    })

    it('serializesFavoriteRequestsAndParsesUpdatedState', 0, () => {
      const payload: FavoritePayload = parseApiJson<FavoritePayload>(favoriteRequestBody(true), '收藏请求')
      expect(payload.favorite).assertTrue()
      const page: HistoryPage = parseApiJson<HistoryPage>(HISTORY_PAGE_TWO_JSON, '加载收藏结果')
      expect(page.items[0].isFavorite).assertTrue()
    })

    it('sanitizesNetworkObjectsJsonAndTechnicalStacks', 0, () => {
      expect(userFacingError(new Error('ECONNREFUSED 192.168.5.13:8000'), '操作失败'))
        .assertContain('无法连接电脑端服务')
      expect(userFacingError(AUTH_EXPIRED_JSON, '登录失败')).assertContain('登录会话已失效')
      expect(userFacingError(new Object(), '操作失败，请重试。')).assertEqual('操作失败，请重试。')
      expect(userFacingError('Traceback\n    at internal.py:42', '服务返回异常。')).assertEqual('服务返回异常。')
      const transportError = new ApiError(0, '无法连接电脑端服务。')
      expect(isTransportError(transportError)).assertTrue()
      const connection = new ConnectionController()
      connection.serviceSummary = '已连接 · production · decimer'
      connection.markTransportFailure()
      expect(connection.serviceSummary).assertContain('连接中断')
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
      expect(taskPresentation('restoring').label).assertEqual('恢复任务')
      expect(taskPresentation('network_wait').tone).assertEqual('warning')
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

    it('validatesReviewActionsAgainstBackendRules', 0, () => {
      expect(validateReviewInput('confirm', '', '', '', 'image', false).valid).assertTrue()
      expect(validateReviewInput('confirm', '', '', '', 'image', false, true).message)
        .assertContain('SMILES')
      expect(validateReviewInput('confirm', 'C'.repeat(4097), '', '', 'image', false).valid).assertFalse()
      expect(validateReviewInput('unable_to_confirm', '', '', '', 'image', false).message)
        .assertContain('原因')
      expect(validateReviewInput('unable_to_confirm', '', 'other', '', 'image', false).message)
        .assertContain('备注')
      expect(validateReviewInput('unable_to_confirm', '', 'image_unclear', '补充说明', 'image', false).valid)
        .assertTrue()
      expect(validateReviewInput('unable_to_confirm', '', 'image_unclear', '字'.repeat(301), 'image', false).valid)
        .assertFalse()
      expect(validateReviewInput('unable_to_confirm', '', 'image_unclear', '', 'image', true).message)
        .assertContain('撤销')
      expect(validateReviewInput('revoke', '', '', '', 'image', false).valid).assertFalse()
      expect(validateReviewInput('revoke', '', '', '', 'image', true).valid).assertTrue()
      expect(validateReviewInput('confirm', '', '', '', 'smiles', true).message).assertContain('不需要')
    })
  })
}
