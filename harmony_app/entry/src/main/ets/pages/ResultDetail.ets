import promptAction from '@ohos.promptAction'
import router from '@ohos.router'
import fs from '@ohos.file.fs'
import { componentSnapshot } from '@kit.ArkUI'
import { common } from '@kit.AbilityKit'
import { pasteboard } from '@kit.BasicServicesKit'
import { fileUri } from '@kit.CoreFileKit'
import { image } from '@kit.ImageKit'
import { systemShare } from '@kit.ShareKit'
import {
  AdmetItem,
  LipinskiCheck,
  ReviewEvent,
  VisionAnalysisResult
} from '../model/VisionResult'
import { SettingsStore, StoredSettings } from '../services/SettingsStore'
import { SecureStore } from '../services/SecureStore'
import { ApiClient, isUnauthorizedError } from '../services/ApiClient'
import { userFacingError } from '../services/AppUtils'
import { AnalysisApi } from '../services/AnalysisApi'
import {
  formatReportTime,
  qualityReasonLabel,
  responsiveColumns,
  resultConclusion,
  ResultConclusion,
  REVIEW_REASON_CODES,
  reviewActionLabel,
  reviewReasonLabel,
  ReviewValidation,
  validateReviewInput
} from '../services/UiPresentation'
import { AppErrorState, LoadingSkeleton, MoleculeBackdrop, SectionHeader, StatusPill } from '../components/CommonStates'

interface ResultParams {
  analysisId: string
}

@Entry
@Component
struct ResultDetail {
  @State apiBaseUrl: string = ''
  @State apiKey: string = ''
  @State analysisId: string = ''
  @State result: VisionAnalysisResult | undefined = undefined
  @State correctionSmiles: string = ''
  @State loading: boolean = true
  @State saving: boolean = false
  @State favoriteBusy: boolean = false
  @State shareBusy: boolean = false
  @State loadError: string = ''
  @State actionError: string = ''
  @State contentWidth: number = 400
  @State previewMode: string = 'structure'
  @State previewOpen: boolean = false
  @State identityExpanded: boolean = false
  @State propertiesExpanded: boolean = false
  @State qualityExpanded: boolean = false
  @State admetExpanded: boolean = false
  @State auditExpanded: boolean = false
  @State correctionExpanded: boolean = false
  @State unableExpanded: boolean = false
  @State revokeConfirming: boolean = false
  @State unableReason: string = ''
  @State unableNote: string = ''

  aboutToAppear(): void {
    this.initialize()
  }

  private async initialize(): Promise<void> {
    try {
      const params = router.getParams() as ResultParams
      this.analysisId = params.analysisId
      const settings: StoredSettings = await SettingsStore.load(getContext(this))
      this.apiBaseUrl = settings.apiBaseUrl
      this.apiKey = settings.apiKey
      ApiClient.setSessionToken(settings.authToken)
      await this.loadResult()
    } catch (error) {
      this.loading = false
      this.loadError = userFacingError(error as Object, '加载结果失败，请返回后重试。')
      await promptAction.openToast({ message: this.loadError })
    }
  }

  private async loadResult(): Promise<void> {
    this.loading = true
    this.loadError = ''
    try {
      const data: VisionAnalysisResult = this.normalizeResult(
        await AnalysisApi.analysis(this.apiBaseUrl, this.apiKey, this.analysisId)
      )
      this.result = data
      this.correctionSmiles = data.smiles === null ? '' : data.smiles
      this.previewMode = data.inputType === 'image' && data.sourceImageUrl !== null ? 'source' : 'structure'
    } catch (error) {
      this.loadError = userFacingError(error as Object, '无法加载分析结果，请稍后重试。')
      await this.handleActionError(error as Object)
    } finally {
      this.loading = false
    }
  }

  private normalizeResult(data: VisionAnalysisResult): VisionAnalysisResult {
    data.message = userFacingError(data.message, data.status === 'success' ?
      '分析已完成。' : '分析未完成，请重新提交。')
    if (data.sourceImageUrl === undefined) {
      data.sourceImageUrl = null
    }
    if (data.identity === undefined) {
      data.identity = {
        rawSmiles: data.predictedSmiles,
        canonicalSmiles: data.canonicalSmiles,
        standardizedSmiles: data.canonicalSmiles,
        isomericSmiles: data.canonicalSmiles,
        inchi: null,
        inchiKey: null,
        formula: data.formula,
        formalCharge: null,
        fragmentCount: null,
        stereocenterCount: null,
        heavyAtomCount: null
      }
    }
    if (data.imageQuality === undefined) {
      data.imageQuality = {
        score: data.imageQualityScore,
        passed: null,
        width: null,
        height: null,
        contrast: null,
        blurVariance: null,
        inkRatio: null,
        borderInkRatio: null,
        reasonCodes: []
      }
    }
    data.imageQuality.reasonCodes = data.imageQuality.reasonCodes === undefined ? [] : data.imageQuality.reasonCodes
    if (data.lipinskiDetail === undefined) {
      data.lipinskiDetail = {
        passed: data.lipinskiPassed,
        summary: '',
        checks: []
      }
    }
    data.lipinskiDetail.checks = data.lipinskiDetail.checks === undefined ? [] : data.lipinskiDetail.checks
    data.lipinskiDetail.summary = data.lipinskiDetail.summary === undefined ? '' : data.lipinskiDetail.summary
    if (data.recognitionTrace === undefined) {
      data.recognitionTrace = {
        modelName: data.backend,
        modelVersion: null,
        resultOrigin: null,
        selectedStrategy: null,
        attemptCount: null,
        strategyAgreement: null,
        finalSource: null,
        standardizationChanged: null,
        standardizationWarnings: []
      }
    }
    data.recognitionTrace.standardizationWarnings = data.recognitionTrace.standardizationWarnings === undefined ? [] :
      data.recognitionTrace.standardizationWarnings
    if (data.review === undefined) {
      data.review = {
        required: data.inputType === 'image',
        status: data.reviewStatus,
        confirmed: data.confirmed,
        reviewedAt: null,
        lastConfirmedAt: null,
        reason: null,
        note: null,
        reviewedBy: null,
        events: []
      }
    }
    // The review object is the authoritative state returned by the review API.
    // Synchronize legacy top-level aliases so every result card refreshes at once.
    data.review.events = data.review.events === undefined ? [] : data.review.events
    data.reviewStatus = data.review.status
    data.confirmed = data.review.confirmed
    data.admet = data.admet === undefined ? { status: 'unavailable', message: '', items: [] } : data.admet
    data.admet.items = data.admet.items === undefined ? [] : data.admet.items
    data.admet.message = data.admet.message === undefined ? '' : data.admet.message
    data.reasonCodes = data.reasonCodes === undefined ? [] : data.reasonCodes
    data.warnings = data.warnings === undefined ? [] : data.warnings
    return data
  }

  private displayNumber(value: number | null, suffix: string = ''): string {
    return value === null ? '--' : `${value}${suffix}`
  }

  private displayText(value: string | null): string {
    return value === null || value.length === 0 ? '--' : value
  }

  private selectedPreviewUrl(data: VisionAnalysisResult): string {
    if (this.previewMode === 'source' && data.sourceImageUrl !== null) {
      return data.sourceImageUrl
    }
    return data.structureImageUrl === null ? '' : data.structureImageUrl
  }

  private async handleActionError(error: Object): Promise<void> {
    if (isUnauthorizedError(error)) {
      await SecureStore.clearSession()
      ApiClient.setSessionToken('')
      await promptAction.openToast({ message: '登录已过期，请重新登录。' })
      this.leaveResult()
      return
    }
    this.actionError = userFacingError(error, '操作失败，请稍后重试。')
  }

  private leaveResult(): void {
    router.back()
  }

  private async copyText(value: string | null, label: string): Promise<void> {
    if (value === null || value.length === 0) {
      return
    }
    try {
      await pasteboard.getSystemPasteboard().setData(pasteboard.createPlainTextData(value))
      await promptAction.openToast({ message: `${label}已复制。` })
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '复制失败，请稍后重试。') })
    }
  }

  private shareSummary(data: VisionAnalysisResult): string {
    const reviewText = data.confirmed ? '已人工确认' :
      (data.review.status === 'unable_to_confirm' ? '当前无法确认' : '尚未人工确认')
    const reviewer = data.review.reviewedBy === null ? '' : `  复核人: ${data.review.reviewedBy.displayName}`
    return `Molecule Vision 分析 #${data.analysisId}\n` +
      `SMILES: ${this.displayText(data.smiles)}\n` +
      `分子式: ${this.displayText(data.formula)}\n` +
      `分子量: ${this.displayNumber(data.molecularWeight)}  LogP: ${this.displayNumber(data.logP)}  ` +
      `TPSA: ${this.displayNumber(data.tpsa)}\n` +
      `状态: ${reviewText}${reviewer}  时间: ${data.createdAt}\n` +
      '仅供教学与早期分析参考，不能替代实验和专业决策。'
  }

  private async copyFinalSmiles(): Promise<void> {
    if (this.result === undefined || this.result.smiles === null) {
      return
    }
    await this.copyText(this.result.smiles, '最终 SMILES')
  }

  private async copyShareText(): Promise<void> {
    if (this.result === undefined) {
      return
    }
    try {
      await pasteboard.getSystemPasteboard().setData(
        pasteboard.createPlainTextData(this.shareSummary(this.result))
      )
      await promptAction.openToast({ message: '分析摘要已复制，可手动粘贴分享。' })
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '复制失败，请稍后重试。') })
    }
  }

  private async shareCard(): Promise<void> {
    if (this.result === undefined || this.shareBusy) {
      return
    }
    this.shareBusy = true
    try {
      const pixelMap = await componentSnapshot.get('molecule-share-card')
      const packer = image.createImagePacker()
      const buffer = await packer.packing(pixelMap, {
        format: 'image/png',
        quality: 100
      })
      await packer.release()
      const context = getContext(this) as common.UIAbilityContext
      const path = `${context.cacheDir}/molecule-vision-${this.result.analysisId}.png`
      const file = fs.openSync(path, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC)
      try {
        fs.writeSync(file.fd, buffer)
      } finally {
        fs.closeSync(file)
      }
      const cardUri = fileUri.getUriFromPath(path)
      const record: systemShare.SharedRecord = {
        utd: 'general.png',
        uri: cardUri,
        thumbnailUri: cardUri,
        title: 'Molecule Vision 分析结果',
        label: `分析 #${this.result.analysisId}`,
        description: this.shareSummary(this.result)
      }
      const sharedData = new systemShare.SharedData(record)
      const controller = new systemShare.ShareController(sharedData)
      await controller.show(context, {
        selectionMode: systemShare.SelectionMode.SINGLE,
        previewMode: systemShare.SharePreviewMode.DETAIL
      })
    } catch (error) {
      await this.copyShareText()
      await promptAction.openToast({ message: '图片分享未完成，分析摘要已复制，可稍后重试。' })
    } finally {
      this.shareBusy = false
    }
  }

  private async submitReview(
    action: string,
    smiles: string = '',
    reason: string = '',
    note: string = '',
    correctionRequired: boolean = false
  ): Promise<void> {
    if (this.result === undefined || this.saving) {
      return
    }
    const validation: ReviewValidation = validateReviewInput(
      action,
      smiles,
      reason,
      note,
      this.result.inputType,
      this.result.confirmed,
      correctionRequired
    )
    if (!validation.valid) {
      this.actionError = validation.message
      return
    }
    this.saving = true
    this.actionError = ''
    try {
      const updated = await AnalysisApi.review(this.apiBaseUrl, this.apiKey, this.result.analysisId, {
        action: action,
        smiles: smiles.trim().length > 0 ? smiles.trim() : undefined,
        reason: reason.length > 0 ? reason : undefined,
        note: note.trim().length > 0 ? note.trim() : undefined
      })
      const normalized = this.normalizeResult(updated)
      this.result = normalized
      this.correctionSmiles = normalized.smiles === null ? '' : normalized.smiles
      this.correctionExpanded = false
      this.unableExpanded = false
      this.revokeConfirming = false
      this.unableReason = ''
      this.unableNote = ''
      // Re-read the report through the loading branch. ArkUI can otherwise
      // retain a stale @Builder subtree when only a nested result object is replaced.
      await this.loadResult()
      const message = action === 'revoke' ? '已撤销结构确认。' :
        (action === 'unable_to_confirm' ? '已记录为当前无法确认。' :
          (smiles.trim().length > 0 ? '修正后的结构已重新计算并确认。' : '已确认当前结构。'))
      await promptAction.openToast({ message: message })
    } catch (error) {
      await this.handleActionError(error as Object)
    } finally {
      this.saving = false
    }
  }

  private async saveReview(): Promise<void> {
    if (this.result === undefined || this.saving) {
      return
    }
    await this.submitReview('confirm', this.correctionSmiles, '', '', true)
  }

  private async confirmCurrent(): Promise<void> {
    await this.submitReview('confirm')
  }

  private async markUnable(): Promise<void> {
    await this.submitReview('unable_to_confirm', '', this.unableReason, this.unableNote)
  }

  private async revokeConfirmation(): Promise<void> {
    await this.submitReview('revoke')
  }

  private async toggleFavorite(): Promise<void> {
    if (this.result === undefined || this.favoriteBusy) {
      return
    }
    this.favoriteBusy = true
    try {
      await AnalysisApi.favorite(
        this.apiBaseUrl,
        this.apiKey,
        this.result.analysisId,
        !this.result.isFavorite
      )
      await this.loadResult()
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '更新收藏失败，请稍后重试。') })
    } finally {
      this.favoriteBusy = false
    }
  }

  @Builder
  private metricCard(label: string, value: string) {
    Column({ space: 4 }) {
      Text(label).fontSize(12).fontColor($r('app.color.text_secondary'))
      Text(value)
        .fontSize(17)
        .fontWeight(FontWeight.Medium)
        .fontColor($r('app.color.text_primary'))
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
    }
    .alignItems(HorizontalAlign.Start)
    .width(responsiveColumns(this.contentWidth) === 1 ? '100%' : '48%')
    .padding(12)
    .backgroundColor($r('app.color.surface_soft'))
    .border({ width: 1, color: $r('app.color.divider') })
    .borderRadius(12)
  }

  private conclusion(data: VisionAnalysisResult): ResultConclusion {
    return resultConclusion(data.status, data.inputType, data.review.status, data.confirmed, data.needsReview)
  }

  private confidenceText(value: number | null): string {
    if (value === null) {
      return '模型未提供已校准置信度'
    }
    return value >= 0 && value <= 1 ? `${(value * 100).toFixed(1)}%` : `${value}`
  }

  private reviewReasons(): string[] {
    return REVIEW_REASON_CODES
  }

  @Builder
  private expandableHeader(title: string, caption: string, expanded: boolean, onToggle: () => void) {
    Row({ space: 10 }) {
      Column({ space: 2 }) {
        Text(title).fontSize(17).fontWeight(FontWeight.Bold).fontColor($r('app.color.text_primary'))
        if (caption.length > 0) {
          Text(caption).fontSize(12).fontColor($r('app.color.text_secondary')).maxLines(2)
        }
      }
      .alignItems(HorizontalAlign.Start)
      .layoutWeight(1)
      Text(expanded ? '⌃' : '⌄')
        .fontSize(22)
        .fontColor($r('app.color.primary'))
    }
    .width('100%')
    .constraintSize({ minHeight: 44 })
    .onClick(onToggle)
    .accessibilityText(`${title}，${expanded ? '收起' : '展开'}`)
  }

  @Builder
  private copyableField(label: string, value: string | null) {
    Column({ space: 5 }) {
      Row() {
        Text(label).fontSize(12).fontColor($r('app.color.text_secondary')).layoutWeight(1)
        if (value !== null && value.length > 0) {
          Text('复制')
            .fontSize(12)
            .fontColor($r('app.color.primary'))
            .width(52)
            .height(44)
            .textAlign(TextAlign.Center)
            .onClick(() => this.copyText(value, label))
            .accessibilityText(`复制${label}`)
        }
      }
      .width('100%')
      Text(this.displayText(value))
        .fontSize(14)
        .fontColor($r('app.color.text_primary'))
        .copyOption(CopyOptions.LocalDevice)
        .width('100%')
    }
    .alignItems(HorizontalAlign.Start)
    .width('100%')
    .padding(11)
    .backgroundColor($r('app.color.surface_soft'))
    .borderRadius(11)
  }

  @Builder
  private conclusionCard(data: VisionAnalysisResult) {
    Column({ space: 12 }) {
      Row({ space: 10 }) {
        Text(this.conclusion(data).symbol)
          .fontSize(22)
          .fontWeight(FontWeight.Bold)
          .fontColor(this.conclusion(data).tone === 'danger' ? $r('app.color.danger') :
            (this.conclusion(data).tone === 'warning' ? $r('app.color.warning') : $r('app.color.success')))
          .width(44).height(44).textAlign(TextAlign.Center)
          .backgroundColor(this.conclusion(data).tone === 'danger' ? $r('app.color.danger_soft') :
            (this.conclusion(data).tone === 'warning' ? $r('app.color.warning_soft') : $r('app.color.success_soft')))
          .borderRadius(14)
        Column({ space: 4 }) {
          Text(this.conclusion(data).label)
            .fontSize(20).fontWeight(FontWeight.Bold).fontColor($r('app.color.text_primary'))
          Text(this.conclusion(data).message)
            .fontSize(13).lineHeight(19).fontColor($r('app.color.text_body')).width('100%')
        }
        .alignItems(HorizontalAlign.Start).layoutWeight(1)
        Text(data.isFavorite ? '★' : '☆')
          .fontSize(27)
          .fontColor(data.isFavorite ? $r('app.color.favorite') : $r('app.color.text_tertiary'))
          .width(44).height(44).textAlign(TextAlign.Center)
          .enabled(!this.favoriteBusy)
          .onClick(() => this.toggleFavorite())
          .accessibilityText(data.isFavorite ? '取消收藏' : '收藏结果')
      }
      .width('100%').alignItems(VerticalAlign.Top)

      Row({ space: 7 }) {
        StatusPill({ symbol: data.status === 'success' ? '✓' : '!',
          label: data.status === 'success' ? '报告已生成' : '分析失败',
          tone: data.status === 'success' ? 'success' : 'danger' })
        if (data.riskLevel !== null && data.riskLevel.length > 0) {
          StatusPill({ symbol: '!', label: `风险 ${data.riskLevel}`, tone: 'warning' })
        }
      }
      .width('100%')

      Divider().color($r('app.color.divider'))
      Text(`${data.createdBy === null ? '历史数据' : data.createdBy.displayName} · ${formatReportTime(data.createdAt)}`)
        .fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
      Text(`分析编号 ${data.analysisId}`)
        .fontSize(10).fontColor($r('app.color.text_tertiary')).maxLines(1).width('100%')
    }
    .alignItems(HorizontalAlign.Start)
    .width('100%').padding(16)
    .backgroundColor($r('app.color.surface'))
    .border({ width: 1, color: $r('app.color.border') }).borderRadius(18)
  }

  @Builder
  private evidenceCard(data: VisionAnalysisResult) {
    Column({ space: 12 }) {
      SectionHeader({ symbol: '⌬', title: '识别依据', caption: '对照上传图片与模型重绘结构后再进行确认' })
      if (data.inputType === 'image') {
        Row({ space: 8 }) {
          Button('上传原图')
            .layoutWeight(1).height(44)
            .enabled(data.sourceImageUrl !== null)
            .fontColor(this.previewMode === 'source' ? $r('app.color.text_on_primary') : $r('app.color.primary'))
            .backgroundColor(this.previewMode === 'source' ? $r('app.color.primary') : $r('app.color.primary_soft'))
            .onClick(() => this.previewMode = 'source')
          Button('重绘结构')
            .layoutWeight(1).height(44)
            .fontColor(this.previewMode === 'structure' ? $r('app.color.text_on_primary') : $r('app.color.primary'))
            .backgroundColor(this.previewMode === 'structure' ? $r('app.color.primary') : $r('app.color.primary_soft'))
            .onClick(() => this.previewMode = 'structure')
        }
        .width('100%')
      }
      if (this.selectedPreviewUrl(data).length > 0) {
        Image(this.selectedPreviewUrl(data))
          .width('100%').height(250)
          .objectFit(ImageFit.Contain)
          .backgroundColor($r('app.color.surface_structure'))
          .border({ width: 1, color: $r('app.color.divider') }).borderRadius(12)
          .onClick(() => this.previewOpen = true)
          .accessibilityText(`${this.previewMode === 'source' ? '上传原图' : '重绘结构'}，点击全屏查看`)
        Text('点击图片可全屏查看细节')
          .fontSize(11).fontColor($r('app.color.text_tertiary')).width('100%').textAlign(TextAlign.Center)
      } else {
        Text(data.inputType === 'image' ? '这条旧报告没有可用的原始图片，请查看重绘结构。' : '当前没有可用结构图。')
          .fontSize(13).fontColor($r('app.color.text_secondary')).width('100%')
          .padding(14).backgroundColor($r('app.color.surface_soft')).borderRadius(10)
      }
    }
    .alignItems(HorizontalAlign.Start)
    .width('100%').padding(16)
    .backgroundColor($r('app.color.surface'))
    .border({ width: 1, color: $r('app.color.border') }).borderRadius(18)
  }

  @Builder
  private reviewReasonChip(code: string) {
    Button(reviewReasonLabel(code))
      .height(44)
      .fontSize(12)
      .fontColor(this.unableReason === code ? $r('app.color.text_on_primary') : $r('app.color.text_body'))
      .backgroundColor(this.unableReason === code ? $r('app.color.primary') : $r('app.color.surface_soft'))
      .border({ width: 1, color: this.unableReason === code ? $r('app.color.primary') : $r('app.color.border') })
      .onClick(() => {
        this.unableReason = code
        this.actionError = ''
      })
  }

  @Builder
  private reviewPanel(data: VisionAnalysisResult) {
    Column({ space: 11 }) {
      SectionHeader({ symbol: '✓', title: '人工复核', caption: '团队共享操作会记录组员、时间与原因' })
      Row({ space: 8 }) {
        StatusPill({ symbol: data.confirmed ? '✓' : (data.review.status === 'unable_to_confirm' ? '?' : '!'),
          label: data.confirmed ? '已确认' :
            (data.review.status === 'unable_to_confirm' ? '无法确认' : '等待复核'),
          tone: data.confirmed ? 'success' : 'warning' })
        if (data.review.reviewedBy !== null) {
          Text(`${data.review.reviewedBy.displayName} · ${formatReportTime(data.review.reviewedAt === null ? '' : data.review.reviewedAt)}`)
            .fontSize(12).fontColor($r('app.color.text_secondary')).layoutWeight(1).maxLines(2)
        }
      }
      .width('100%')
      if (data.review.status === 'unable_to_confirm') {
        Text(`原因：${reviewReasonLabel(data.review.reason)}`)
          .fontSize(13).fontColor($r('app.color.warning')).width('100%')
        if (data.review.note !== null && data.review.note.length > 0) {
          Text(data.review.note).fontSize(13).fontColor($r('app.color.text_body')).width('100%')
        }
      }

      if (data.inputType === 'image' && !data.confirmed) {
        Button(this.saving ? '正在提交…' : '确认当前结构')
          .width('100%').height(46).enabled(!this.saving)
          .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.primary'))
          .onClick(() => this.confirmCurrent())
          .accessibilityText('确认当前模型结构')
        Row({ space: 8 }) {
          Button('修正 SMILES')
            .layoutWeight(1).height(44)
            .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
            .onClick(() => {
              this.correctionExpanded = !this.correctionExpanded
              this.unableExpanded = false
              this.actionError = ''
            })
          Button('无法确认')
            .layoutWeight(1).height(44)
            .fontColor($r('app.color.warning')).backgroundColor($r('app.color.warning_soft'))
            .onClick(() => {
              this.unableExpanded = !this.unableExpanded
              this.correctionExpanded = false
              this.actionError = ''
            })
        }
        .width('100%')
        if (this.correctionExpanded) {
          Column({ space: 9 }) {
            Text('模型原始 SMILES').fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
            Text(this.displayText(data.predictedSmiles)).fontSize(13).fontColor($r('app.color.text_body')).width('100%')
            TextArea({ text: this.correctionSmiles, placeholder: '输入核对后的 SMILES' })
              .width('100%').height(94)
              .maxLength(4096)
              .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') })
              .borderRadius(11).onChange((value: string) => {
                this.correctionSmiles = value
                this.actionError = ''
              })
            Text('合法输入会重绘结构并重新计算性质；非法输入不会覆盖原报告。')
              .fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
            Text(`${this.correctionSmiles.length}/4096`)
              .fontSize(11).fontColor($r('app.color.text_tertiary')).width('100%').textAlign(TextAlign.End)
            Button(this.saving ? '正在校验…' : '保存修正并确认')
              .width('100%').height(46)
              .enabled(!this.saving && this.correctionSmiles.trim().length > 0)
              .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.primary'))
              .onClick(() => this.saveReview())
          }
          .width('100%').padding(12).backgroundColor($r('app.color.primary_faint')).borderRadius(12)
        }
        if (this.unableExpanded) {
          Column({ space: 9 }) {
            Text('请选择无法确认的原因').fontSize(13).fontWeight(FontWeight.Medium)
              .fontColor($r('app.color.text_primary')).width('100%')
            Flex({ direction: FlexDirection.Row, wrap: FlexWrap.Wrap, justifyContent: FlexAlign.Start }) {
              ForEach(this.reviewReasons(), (code: string) => {
                this.reviewReasonChip(code)
              })
            }
            .width('100%')
            TextArea({ text: this.unableNote, placeholder: this.unableReason === 'other' ? '请填写原因（必填）' : '补充说明（可选）' })
              .width('100%').height(86)
              .maxLength(300)
              .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') })
              .borderRadius(11).onChange((value: string) => {
                this.unableNote = value
                this.actionError = ''
              })
            Text(`${this.unableNote.length}/300${this.unableReason === 'other' ? ' · 必填' : ' · 可选'}`)
              .fontSize(11)
              .fontColor(this.unableReason === 'other' && this.unableNote.trim().length === 0 ?
                $r('app.color.warning') : $r('app.color.text_tertiary'))
              .width('100%').textAlign(TextAlign.End)
            Button(this.saving ? '正在记录…' : '确认标记为无法确认')
              .width('100%').height(46)
              .enabled(!this.saving && this.unableReason.length > 0 &&
                (this.unableReason !== 'other' || this.unableNote.trim().length > 0))
              .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.warning'))
              .onClick(() => this.markUnable())
          }
          .width('100%').padding(12).backgroundColor($r('app.color.warning_soft')).borderRadius(12)
        }
      } else if (data.inputType === 'image' && data.confirmed) {
        if (!this.revokeConfirming) {
          Button('撤销结构确认')
            .width('100%').height(44)
            .fontColor($r('app.color.danger')).backgroundColor($r('app.color.danger_soft'))
            .onClick(() => this.revokeConfirming = true)
        } else {
          Column({ space: 8 }) {
            Text('撤销后该结果会重新进入待复核状态，历史记录不会删除。')
              .fontSize(13).fontColor($r('app.color.danger')).width('100%')
            Row({ space: 8 }) {
              Button('取消').layoutWeight(1).height(44)
                .fontColor($r('app.color.text_body')).backgroundColor($r('app.color.surface_soft'))
                .onClick(() => this.revokeConfirming = false)
              Button(this.saving ? '正在撤销…' : '确认撤销').layoutWeight(1).height(44).enabled(!this.saving)
                .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.danger'))
                .onClick(() => this.revokeConfirmation())
            }
            .width('100%')
          }
          .width('100%').padding(12).backgroundColor($r('app.color.danger_soft')).borderRadius(12)
        }
      } else {
        Text('手动输入的 SMILES 已由后端完成合法性校验，不需要图片结构复核。')
          .fontSize(13).fontColor($r('app.color.text_secondary')).width('100%')
      }

      if (this.actionError.length > 0) {
        Text(this.actionError).fontSize(13).fontColor($r('app.color.danger')).width('100%')
          .padding(10).backgroundColor($r('app.color.danger_soft')).borderRadius(10)
      }
    }
    .alignItems(HorizontalAlign.Start)
    .width('100%').padding(16)
    .backgroundColor($r('app.color.surface'))
    .border({ width: 1, color: $r('app.color.border') }).borderRadius(18)
  }

  @Builder
  private identityCard(data: VisionAnalysisResult) {
    Column({ space: 10 }) {
      this.expandableHeader('结构身份', 'SMILES、InChI 与分子组成', this.identityExpanded,
        () => this.identityExpanded = !this.identityExpanded)
      if (this.identityExpanded) {
        this.copyableField('最终 SMILES', data.smiles)
        this.copyableField('模型预测 SMILES', data.predictedSmiles)
        this.copyableField('原始 SMILES', data.identity.rawSmiles)
        this.copyableField('标准 SMILES', data.identity.canonicalSmiles)
        this.copyableField('规范化 SMILES', data.identity.standardizedSmiles)
        this.copyableField('异构 SMILES', data.identity.isomericSmiles)
        this.copyableField('InChIKey', data.identity.inchiKey)
        this.copyableField('InChI', data.identity.inchi)
        Flex({ direction: FlexDirection.Row, wrap: FlexWrap.Wrap, justifyContent: FlexAlign.SpaceBetween }) {
          this.metricCard('分子式', this.displayText(data.identity.formula))
          this.metricCard('形式电荷', this.displayNumber(data.identity.formalCharge))
          this.metricCard('片段数', this.displayNumber(data.identity.fragmentCount))
          this.metricCard('立体中心', this.displayNumber(data.identity.stereocenterCount))
          this.metricCard('重原子数', this.displayNumber(data.identity.heavyAtomCount))
        }
        .width('100%')
      }
    }
    .alignItems(HorizontalAlign.Start).width('100%').padding(16)
    .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') }).borderRadius(18)
  }

  @Builder
  private lipinskiCheckRow(check: LipinskiCheck) {
    Row({ space: 10 }) {
      Text(check.passed === false ? '!' : '✓')
        .fontSize(14).fontWeight(FontWeight.Bold)
        .fontColor(check.passed === false ? $r('app.color.warning') : $r('app.color.success'))
        .width(30).height(30).textAlign(TextAlign.Center)
        .backgroundColor(check.passed === false ? $r('app.color.warning_soft') : $r('app.color.success_soft'))
        .borderRadius(10)
      Column({ space: 2 }) {
        Text(check.name).fontSize(13).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
        Text(check.message.length > 0 ? check.message :
          `${this.displayNumber(check.value)} / 限制 ${this.displayNumber(check.limit)}`)
          .fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
      }
      .alignItems(HorizontalAlign.Start).layoutWeight(1)
    }
    .width('100%').padding(10).backgroundColor($r('app.color.surface_soft')).borderRadius(10)
  }

  @Builder
  private propertiesCard(data: VisionAnalysisResult) {
    Column({ space: 11 }) {
      this.expandableHeader('性质与规则', `${this.displayText(data.formula)} · MW ${this.displayNumber(data.molecularWeight)}`,
        this.propertiesExpanded, () => this.propertiesExpanded = !this.propertiesExpanded)
      if (this.propertiesExpanded) {
        Flex({ direction: FlexDirection.Row, wrap: FlexWrap.Wrap, justifyContent: FlexAlign.SpaceBetween }) {
          this.metricCard('分子量', this.displayNumber(data.molecularWeight))
          this.metricCard('LogP', this.displayNumber(data.logP))
          this.metricCard('TPSA', this.displayNumber(data.tpsa, ' Å²'))
          this.metricCard('氢键供体 / 受体', `${this.displayNumber(data.hbd)} / ${this.displayNumber(data.hba)}`)
          this.metricCard('环 / 可旋转键', `${this.displayNumber(data.ringCount)} / ${this.displayNumber(data.rotatableBonds)}`)
        }
        .width('100%')
        Divider().color($r('app.color.divider'))
        Text(data.lipinskiDetail.passed === null ? 'Lipinski 未计算' :
          (data.lipinskiDetail.passed ? '✓ Lipinski 规则检查通过' : '! Lipinski 存在超限项'))
          .fontSize(14).fontWeight(FontWeight.Medium)
          .fontColor(data.lipinskiDetail.passed === false ? $r('app.color.warning') : $r('app.color.success'))
          .width('100%')
        if (data.lipinskiDetail.summary.length > 0) {
          Text(data.lipinskiDetail.summary).fontSize(13).fontColor($r('app.color.text_body')).width('100%')
        }
        ForEach(data.lipinskiDetail.checks, (check: LipinskiCheck) => {
          this.lipinskiCheckRow(check)
        })
        Text('规则检查仅用于早期性质参考；通过不代表安全、有效或具备可成药性。')
          .fontSize(12).lineHeight(18).fontColor($r('app.color.warning')).width('100%')
          .padding(10).backgroundColor($r('app.color.warning_soft')).borderRadius(10)
      }
    }
    .alignItems(HorizontalAlign.Start).width('100%').padding(16)
    .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') }).borderRadius(18)
  }

  @Builder
  private qualityCard(data: VisionAnalysisResult) {
    Column({ space: 10 }) {
      this.expandableHeader('识别质量与模型', `质量 ${this.displayNumber(data.imageQuality.score)} · ${data.backend}`,
        this.qualityExpanded, () => this.qualityExpanded = !this.qualityExpanded)
      if (this.qualityExpanded) {
        Row({ space: 8 }) {
          StatusPill({
            symbol: data.imageQuality.passed === null ? '·' : (data.imageQuality.passed ? '✓' : '!'),
            label: data.imageQuality.passed === null ? '质量结论未提供' :
              (data.imageQuality.passed ? '图像质量通过' : '图像质量需关注'),
            tone: data.imageQuality.passed === false ? 'warning' :
              (data.imageQuality.passed === true ? 'success' : 'neutral')
          })
        }
        .width('100%')
        Text(`置信度：${this.confidenceText(data.confidence)}`).fontSize(14).fontColor($r('app.color.text_body'))
        Text(`质量分：${this.displayNumber(data.imageQuality.score)}`)
          .fontSize(14).fontColor($r('app.color.text_body'))
        Text(`图片尺寸：${this.displayNumber(data.imageQuality.width)} × ${this.displayNumber(data.imageQuality.height)} px`)
          .fontSize(14).fontColor($r('app.color.text_body'))
        Text(`对比度 / 清晰度：${this.displayNumber(data.imageQuality.contrast)} / ${this.displayNumber(data.imageQuality.blurVariance)}`)
          .fontSize(14).fontColor($r('app.color.text_body'))
        Text(`墨迹占比 / 边缘墨迹：${this.displayNumber(data.imageQuality.inkRatio)} / ${this.displayNumber(data.imageQuality.borderInkRatio)}`)
          .fontSize(14).fontColor($r('app.color.text_body')).width('100%')
        Text(`推理耗时：${this.displayNumber(data.inferenceTimeMs, ' ms')}`)
          .fontSize(14).fontColor($r('app.color.text_body'))
        Text(`模型：${this.displayText(data.recognitionTrace.modelName)} ${this.displayText(data.recognitionTrace.modelVersion)}`)
          .fontSize(14).fontColor($r('app.color.text_body')).width('100%')
        Text(`结果来源：${this.displayText(data.recognitionTrace.resultOrigin)}；最终结构来源：${this.displayText(data.recognitionTrace.finalSource)}`)
          .fontSize(14).fontColor($r('app.color.text_body')).width('100%')
        Text(`设备 / 策略：${this.displayText(data.device)} / ${this.displayText(data.recognitionTrace.selectedStrategy)}`)
          .fontSize(14).fontColor($r('app.color.text_body')).width('100%')
        Text(`策略尝试：${this.displayNumber(data.recognitionTrace.attemptCount)}；结果一致：${data.recognitionTrace.strategyAgreement === null ? '未提供' : (data.recognitionTrace.strategyAgreement ? '是' : '否')}`)
          .fontSize(14).fontColor($r('app.color.text_body')).width('100%')
        Text(`标准化是否改变结构：${data.recognitionTrace.standardizationChanged === null ? '未提供' :
          (data.recognitionTrace.standardizationChanged ? '是' : '否')}`)
          .fontSize(14).fontColor($r('app.color.text_body')).width('100%')
        if (data.decisionMessage !== null && data.decisionMessage.length > 0) {
          Text(data.decisionMessage).fontSize(13).fontColor($r('app.color.text_secondary')).width('100%')
        }
        ForEach(data.reasonCodes, (reason: string) => {
          Text(`• ${qualityReasonLabel(reason)}`).fontSize(13).fontColor($r('app.color.warning')).width('100%')
        })
        ForEach(data.imageQuality.reasonCodes, (reason: string) => {
          Text(`• ${qualityReasonLabel(reason)}`).fontSize(13).fontColor($r('app.color.warning')).width('100%')
        })
        ForEach(data.warnings, (warning: string) => {
          Text(`• ${warning}`).fontSize(13).fontColor($r('app.color.warning')).width('100%')
        })
        ForEach(data.recognitionTrace.standardizationWarnings, (warning: string) => {
          Text(`• 标准化：${warning}`).fontSize(13).fontColor($r('app.color.warning')).width('100%')
        })
      }
    }
    .alignItems(HorizontalAlign.Start).width('100%').padding(16)
    .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') }).borderRadius(18)
  }

  @Builder
  private admetCard(data: VisionAnalysisResult) {
    Column({ space: 10 }) {
      this.expandableHeader('ADMET', data.admet.status === 'available' ? '预测可用' : '当前未启用预测',
        this.admetExpanded, () => this.admetExpanded = !this.admetExpanded)
      if (this.admetExpanded) {
        if (data.admet.message.length > 0) {
          Text(data.admet.message).fontSize(13).fontColor($r('app.color.text_secondary')).width('100%')
        }
        if (data.admet.items.length === 0) {
          Text('未启用或当前没有可用预测，不展示推测数据。')
            .fontSize(13).fontColor($r('app.color.text_secondary')).width('100%')
        } else {
          ForEach(data.admet.items, (item: AdmetItem) => {
            Column({ space: 4 }) {
              Row() {
                Text(this.displayText(item.target)).fontSize(13).fontWeight(FontWeight.Medium).layoutWeight(1)
                Text(item.prediction === null ? '--' : `${item.prediction}`)
                  .fontSize(13).fontColor($r('app.color.primary'))
              }
              .width('100%')
              Text(`概率：${item.probability === null ? '--' : `${(item.probability * 100).toFixed(1)}%`} · 适用域：${item.insideDomain === null ? '未知' : (item.insideDomain ? '域内' : '域外')}`)
                .fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
              if (item.message !== null && item.message.length > 0) {
                Text(item.message).fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
              }
            }
            .width('100%').padding(11).backgroundColor($r('app.color.surface_soft')).borderRadius(10)
          })
        }
        Text('ADMET 结果仅为模型估计，不能替代实验、毒理研究或专业决策。')
          .fontSize(12).lineHeight(18).fontColor($r('app.color.warning')).width('100%')
      }
    }
    .alignItems(HorizontalAlign.Start).width('100%').padding(16)
    .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') }).borderRadius(18)
  }

  @Builder
  private reviewEventRow(event: ReviewEvent) {
    Column({ space: 4 }) {
      Row() {
        Text(reviewActionLabel(event.action)).fontSize(13).fontWeight(FontWeight.Medium)
          .fontColor($r('app.color.text_primary')).layoutWeight(1)
        Text(formatReportTime(event.createdAt)).fontSize(11).fontColor($r('app.color.text_tertiary'))
      }
      .width('100%')
      Text(event.actor === null ? '历史操作' : `${event.actor.displayName}${event.actor.role.length > 0 ? ` · ${event.actor.role}` : ''}`)
        .fontSize(12).fontColor($r('app.color.primary')).width('100%')
      if (event.reason !== null) {
        Text(`原因：${reviewReasonLabel(event.reason)}`).fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
      }
      if (event.note !== null && event.note.length > 0) {
        Text(event.note).fontSize(12).fontColor($r('app.color.text_body')).width('100%')
      }
      if (event.smiles !== null && event.smiles.length > 0) {
        Row() {
          Text(`SMILES：${event.smiles}`)
            .fontSize(12).fontColor($r('app.color.text_secondary')).layoutWeight(1).maxLines(3)
          Text('复制').fontSize(12).fontColor($r('app.color.primary')).width(44).height(44)
            .textAlign(TextAlign.Center).onClick(() => this.copyText(event.smiles, '复核 SMILES'))
        }
        .width('100%')
      }
    }
    .alignItems(HorizontalAlign.Start).width('100%').padding(11)
    .backgroundColor($r('app.color.surface_soft')).borderRadius(10)
  }

  @Builder
  private auditCard(data: VisionAnalysisResult) {
    Column({ space: 9 }) {
      this.expandableHeader('复核记录', `${data.review.events.length} 条操作记录`, this.auditExpanded,
        () => this.auditExpanded = !this.auditExpanded)
      if (this.auditExpanded) {
        if (data.review.events.length === 0) {
          Text('暂无人工复核记录。').fontSize(13).fontColor($r('app.color.text_secondary')).width('100%')
        } else {
          ForEach(data.review.events, (event: ReviewEvent) => {
            this.reviewEventRow(event)
          })
        }
      }
    }
    .alignItems(HorizontalAlign.Start).width('100%').padding(16)
    .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') }).borderRadius(18)
  }

  @Builder
  private sharePreview(data: VisionAnalysisResult) {
    Column({ space: 12 }) {
      Row() {
        Column({ space: 2 }) {
          Text('MOLECULE VISION').fontSize(19).fontWeight(FontWeight.Bold).fontColor('#083B45')
          Text('LAB REVIEW REPORT').fontSize(9).fontColor('#4B7D83')
        }
        .alignItems(HorizontalAlign.Start)
        Blank()
        Text('⌬').fontSize(34).fontColor($r('app.color.primary'))
      }
      .width('100%')
      if (data.structureImageUrl !== null) {
        Image(data.structureImageUrl).width('100%').height(180).objectFit(ImageFit.Contain)
          .backgroundColor($r('app.color.surface_structure')).borderRadius(10)
      }
      Text(this.displayText(data.smiles)).fontSize(13).fontWeight(FontWeight.Medium)
        .fontColor('#102A3A').maxLines(3).width('100%')
      Row() {
        Text(data.confirmed ? '✓ 已人工确认' : (data.review.status === 'unable_to_confirm' ? '? 无法确认' : '△ 尚未确认'))
          .fontSize(12).fontColor(data.confirmed ? $r('app.color.success') : $r('app.color.warning'))
        Blank()
        Text(this.displayText(data.formula)).fontSize(14).fontWeight(FontWeight.Medium).fontColor($r('app.color.primary'))
      }
      .width('100%')
      Row() {
        Text(`MW ${this.displayNumber(data.molecularWeight)}`).fontSize(11).fontColor('#667085')
        Blank()
        Text(`LogP ${this.displayNumber(data.logP)}`).fontSize(11).fontColor('#667085')
        Blank()
        Text(`TPSA ${this.displayNumber(data.tpsa)}`).fontSize(11).fontColor('#667085')
      }
      .width('100%')
      if (data.review.reviewedBy !== null) {
        Text(`复核：${data.review.reviewedBy.displayName} · ${formatReportTime(data.review.reviewedAt === null ? '' : data.review.reviewedAt)}`)
          .fontSize(10).fontColor('#667085').width('100%')
      }
      Divider().color('#B7DCDD')
      Row() {
        Text(formatReportTime(data.createdAt)).fontSize(9).fontColor('#82939A')
        Blank()
        Text(`#${data.analysisId.substring(0, 10)}`).fontSize(9).fontColor('#82939A')
      }
      .width('100%')
      Text('仅供教学与早期分析参考，不能替代实验和专业决策。')
        .fontSize(9).fontColor('#82939A').width('100%')
    }
    .id('molecule-share-card')
    .alignItems(HorizontalAlign.Start).width('100%').padding(18)
    .backgroundColor('#F7FFFF').border({ width: 1, color: '#B7DCDD' }).borderRadius(18)
  }

  @Builder
  private resultContent(data: VisionAnalysisResult) {
    Column({ space: 15 }) {
      this.conclusionCard(data)
      this.evidenceCard(data)
      if (data.status === 'success') {
        this.reviewPanel(data)
        this.identityCard(data)
        this.propertiesCard(data)
        this.qualityCard(data)
        this.admetCard(data)
        this.auditCard(data)
        this.sharePreview(data)
        Row({ space: 9 }) {
          Button('复制 SMILES').height(44).layoutWeight(1)
            .backgroundColor($r('app.color.primary_soft')).fontColor($r('app.color.primary'))
            .onClick(() => this.copyFinalSmiles()).accessibilityText('复制最终 SMILES')
          Button(this.shareBusy ? '生成中…' : '分享结果卡片').height(44).layoutWeight(1)
            .enabled(!this.shareBusy).backgroundColor($r('app.color.primary'))
            .onClick(() => this.shareCard()).accessibilityText('分享不含原图和敏感信息的结果卡片')
        }
        .width('100%')
      }
      Text(data.message).fontSize(13).fontColor($r('app.color.text_secondary')).width('100%')
        .padding({ left: 4, right: 4 })
    }
    .width('100%')
  }

  build() {
    Stack() {
      MoleculeBackdrop()
      Column() {
        Row() {
          Text('‹')
            .fontSize(36)
            .fontColor($r('app.color.text_primary'))
            .width(44).height(48)
            .onClick(() => this.leaveResult())
            .accessibilityText('返回上一页')
          Column({ space: 2 }) {
            Text('结果详情').fontSize(20).fontWeight(FontWeight.Bold).fontColor($r('app.color.text_primary'))
            Text('CHEMICAL STRUCTURE REPORT').fontSize(8).fontColor($r('app.color.text_secondary'))
          }
          .alignItems(HorizontalAlign.Start)
          .layoutWeight(1)
          Text('刷新')
            .fontSize(14).fontColor($r('app.color.primary'))
            .height(44)
            .onClick(() => this.loadResult())
            .accessibilityText('刷新分析结果')
        }
        .width('100%')
        .height(62)
        .padding({ left: 10, right: 18 })
        .backgroundColor($r('app.color.nav_background'))
        .border({ width: { bottom: 1 }, color: $r('app.color.divider') })

        if (this.loading) {
          Scroll() {
            Column({ space: 14 }) {
              Row({ space: 8 }) {
                LoadingProgress().width(24).height(24).color($r('app.color.primary'))
                Text('正在读取分子报告…').fontSize(14).fontColor($r('app.color.text_secondary'))
              }
              .width('100%').padding(14)
              LoadingSkeleton({ rows: 4 })
            }
            .width('100%').padding(16)
          }
          .layoutWeight(1).scrollBar(BarState.Off)
        } else if (this.result === undefined) {
          Column() {
            AppErrorState({
              title: '无法加载分析结果',
              message: this.loadError.length > 0 ? this.loadError : '报告不存在或电脑端服务暂时不可用。',
              onRetry: () => this.loadResult()
            })
          }
          .justifyContent(FlexAlign.Center)
          .padding(18)
          .layoutWeight(1)
          .width('100%')
        } else {
          Scroll() {
            this.resultContent(this.result)
          }
          .width('100%')
          .layoutWeight(1)
          .padding({ left: 16, right: 16, top: 14, bottom: 28 })
          .scrollBar(BarState.Off)
          .edgeEffect(EdgeEffect.Spring)
        }
      }
      .width('100%').height('100%')
      .onAreaChange((_oldArea: Area, newArea: Area) => this.contentWidth = Number(newArea.width))

      if (this.previewOpen && this.result !== undefined && this.selectedPreviewUrl(this.result).length > 0) {
        Column({ space: 8 }) {
          Row() {
            Text(this.previewMode === 'source' ? '上传原图' : '重绘结构')
              .fontSize(16).fontWeight(FontWeight.Medium).fontColor(Color.White)
            Blank()
            Button('关闭')
              .height(44).fontColor(Color.White).backgroundColor('#335C6D76')
              .onClick(() => this.previewOpen = false)
              .accessibilityText('关闭全屏图片预览')
          }
          .width('100%')
          Image(this.selectedPreviewUrl(this.result))
            .width('100%').layoutWeight(1)
            .objectFit(ImageFit.Contain)
            .backgroundColor($r('app.color.surface_structure'))
            .borderRadius(12)
          Text('原始图片仅用于当前结果核对，不会加入分享卡片。')
            .fontSize(12).fontColor('#D7E9EC').width('100%').textAlign(TextAlign.Center)
        }
        .width('100%').height('100%')
        .padding({ left: 16, right: 16, top: 18, bottom: 18 })
        .backgroundColor('#F2071B2C')
        .zIndex(20)
      }
    }
    .width('100%').height('100%')
    .backgroundColor($r('app.color.page_background'))
  }
}
