import promptAction from '@ohos.promptAction'
import router from '@ohos.router'
import { DocumentJob, DocumentRegionInfo, DocumentResult } from '../model/DocumentResult'
import { ApiClient, ApiError, isTransportError, isUnauthorizedError } from '../services/ApiClient'
import { DocumentApi } from '../services/DocumentApi'
import { SettingsStore, StoredSettings } from '../services/SettingsStore'
import { userFacingError } from '../services/AppUtils'
import { moveRegionBox, normalizeRegionBox, RegionBox, resizeRegionBox } from '../services/RegionGeometry'
import { AppErrorState, LoadingSkeleton, MoleculeBackdrop, StatusPill } from '../components/CommonStates'
import { showDangerConfirmation } from '../services/ConfirmAction'
import { isActiveTaskStatus, regionDraftSignature } from '../services/DocumentReviewState'
import { editingTouchTarget } from '../services/MobileLayout'

interface DocumentReviewParams {
  documentId: string
}

const REGION_TYPES: string[] = ['molecule', 'reaction_like', 'text', 'table', 'figure', 'unknown']

@Entry
@Component
struct DocumentRegionReview {
  @State apiBaseUrl: string = ''
  @State apiKey: string = ''
  @State documentId: string = ''
  @State document: DocumentResult | undefined = undefined
  @State selectedIndex: number = 0
  @State x1: string = ''
  @State y1: string = ''
  @State x2: string = ''
  @State y2: string = ''
  @State regionType: string = 'unknown'
  @State confirmed: boolean = false
  @State note: string = ''
  @State loading: boolean = true
  @State saving: boolean = false
  @State recognizing: boolean = false
  @State errorMessage: string = ''
  @State recognitionMessage: string = ''
  @State recognitionStatus: string = ''
  @State regionFilter: string = 'molecule'
  @State coordinatesExpanded: boolean = false
  @State previewWidth: number = 1
  @State previewHeight: number = 280
  private gestureStartBox: RegionBox = { x1: 0, y1: 0, x2: 1, y2: 1 }
  private recognitionJobId: string = ''
  private recognitionRegionId: string = ''
  private savedDraftSignature: string = ''
  private pollTimer: number = -1

  aboutToAppear(): void {
    this.initialize()
  }

  aboutToDisappear(): void {
    this.stopPolling()
  }

  private async initialize(): Promise<void> {
    try {
      const params = router.getParams() as DocumentReviewParams
      this.documentId = params.documentId
      const settings: StoredSettings = await SettingsStore.load(getContext(this))
      this.apiBaseUrl = settings.apiBaseUrl
      this.apiKey = settings.apiKey
      ApiClient.setSessionToken(settings.authToken)
      await this.loadDocument()
      await this.restoreRecognitionTask(settings)
    } catch (error) {
      this.errorMessage = userFacingError(error as Object, '加载区域审核页面失败。')
    } finally {
      this.loading = false
    }
  }

  private async loadDocument(): Promise<void> {
    const selectedId: string = this.currentRegion() === undefined ? '' : (this.currentRegion() as DocumentRegionInfo).regionId
    this.document = await DocumentApi.document(this.apiBaseUrl, this.apiKey, this.documentId)
    if (this.document.regions.length > 0) {
      const keptIndex: number = selectedId.length > 0 ?
        this.document.regions.findIndex(item => item.regionId === selectedId) : -1
      const moleculeIndex: number = this.document.regions.findIndex(item =>
        item.regionType === 'molecule' && !item.confirmed)
      this.selectedIndex = keptIndex >= 0 ? keptIndex : (moleculeIndex >= 0 ? moleculeIndex : 0)
      this.syncForm(this.document.regions[this.selectedIndex])
    }
  }

  private async restoreRecognitionTask(settings: StoredSettings): Promise<void> {
    if (this.document === undefined) return
    let regionId: string = ''
    let jobId: string = ''
    if (settings.activeRegionDocumentId === this.documentId &&
      settings.activeRegionId.length > 0 && settings.activeRegionJobId.length > 0) {
      regionId = settings.activeRegionId
      jobId = settings.activeRegionJobId
    } else {
      const activeRegion = this.document.regions.find((region: DocumentRegionInfo) =>
        region.recognitionJobId.length > 0 && region.analysisId.length === 0)
      if (activeRegion !== undefined) {
        regionId = activeRegion.regionId
        jobId = activeRegion.recognitionJobId
      }
    }
    if (regionId.length === 0 || jobId.length === 0) return
    const regionIndex: number = this.document.regions.findIndex((region: DocumentRegionInfo) =>
      region.regionId === regionId)
    if (regionIndex < 0) {
      await SettingsStore.saveActiveRegionTask(getContext(this), '', '', '')
      return
    }
    this.selectRegionNow(regionIndex)
    this.recognitionRegionId = regionId
    this.recognitionJobId = jobId
    this.recognizing = true
    this.recognitionStatus = 'restoring'
    this.recognitionMessage = '正在恢复该区域未完成的识别任务…'
    this.startPolling()
  }

  private visibleIndices(): number[] {
    if (this.document === undefined) return []
    const indices: number[] = []
    this.document.regions.forEach((region: DocumentRegionInfo, index: number) => {
      if (this.regionFilter === 'all' ||
        (this.regionFilter === 'pending' && !region.confirmed) ||
        region.regionType === this.regionFilter) indices.push(index)
    })
    return indices
  }

  private filterLabel(filter: string): string {
    if (filter === 'molecule') return '分子'
    if (filter === 'pending') return '待审核'
    if (filter === 'reaction_like') return '反应'
    return '全部'
  }

  private typeLabel(type: string): string {
    if (type === 'molecule') return '分子'
    if (type === 'reaction_like') return '反应'
    if (type === 'text') return '文字'
    if (type === 'table') return '表格'
    if (type === 'figure') return '插图'
    return '未知'
  }

  private async changeFilter(filter: string): Promise<void> {
    if (!(await this.prepareRegionTransition())) return
    this.regionFilter = filter
    const indices: number[] = this.visibleIndices()
    if (indices.length > 0 && indices.indexOf(this.selectedIndex) < 0) this.selectRegionNow(indices[0])
  }

  private async moveRegion(offset: number): Promise<void> {
    const indices: number[] = this.visibleIndices()
    if (indices.length === 0) return
    const current: number = indices.indexOf(this.selectedIndex)
    const next: number = Math.max(0, Math.min(indices.length - 1, (current < 0 ? 0 : current) + offset))
    if (indices[next] === this.selectedIndex || !(await this.prepareRegionTransition())) return
    this.selectRegionNow(indices[next])
  }

  private regionPosition(): string {
    const indices: number[] = this.visibleIndices()
    const current: number = indices.indexOf(this.selectedIndex)
    return indices.length === 0 ? '0 / 0' : `${current + 1} / ${indices.length}`
  }

  private syncForm(region: DocumentRegionInfo): void {
    this.x1 = region.bbox.length === 4 ? region.bbox[0].toString() : '0'
    this.y1 = region.bbox.length === 4 ? region.bbox[1].toString() : '0'
    this.x2 = region.bbox.length === 4 ? region.bbox[2].toString() : '0'
    this.y2 = region.bbox.length === 4 ? region.bbox[3].toString() : '0'
    this.regionType = region.regionType
    this.confirmed = region.confirmed
    this.note = ''
    this.recognitionMessage = region.message
    this.savedDraftSignature = this.draftSignature()
  }

  private selectRegionNow(index: number): void {
    if (this.document === undefined || index < 0 || index >= this.document.regions.length) {
      return
    }
    this.selectedIndex = index
    this.syncForm(this.document.regions[index])
  }

  private draftSignature(): string {
    return regionDraftSignature({
      bbox: [Number(this.x1), Number(this.y1), Number(this.x2), Number(this.y2)],
      regionType: this.regionType,
      confirmed: this.confirmed,
      note: this.note
    })
  }

  private hasUnsavedChanges(): boolean {
    return this.currentRegion() !== undefined && this.draftSignature() !== this.savedDraftSignature
  }

  private async prepareRegionTransition(): Promise<boolean> {
    if (this.recognizing) {
      await promptAction.openToast({ message: '当前区域正在识别，请暂停、终止或等待完成后再切换。' })
      return false
    }
    if (!this.hasUnsavedChanges()) return true
    return this.saveRegion(false)
  }

  private async leavePage(): Promise<void> {
    if (this.saving) return
    if (this.hasUnsavedChanges() && !(await this.saveRegion(false))) return
    router.back()
  }

  onBackPress(): boolean {
    this.leavePage()
    return true
  }

  private currentRegion(): DocumentRegionInfo | undefined {
    if (this.document === undefined || this.document.regions.length === 0) {
      return undefined
    }
    return this.document.regions[this.selectedIndex]
  }

  private currentPageUrl(region: DocumentRegionInfo): string {
    if (this.document === undefined) {
      return ''
    }
    const page = this.document.pages.find(item => item.pageNumber === region.pageNumber)
    return page === undefined ? '' : page.previewUrl
  }

  private currentPageWidth(region: DocumentRegionInfo): number {
    if (this.document === undefined) {
      return 1
    }
    const page = this.document.pages.find(item => item.pageNumber === region.pageNumber)
    return page === undefined ? 1 : Math.max(page.width, 1)
  }

  private currentPageHeight(region: DocumentRegionInfo): number {
    if (this.document === undefined) {
      return 1
    }
    const page = this.document.pages.find(item => item.pageNumber === region.pageNumber)
    return page === undefined ? 1 : Math.max(page.height, 1)
  }

  private bboxPercent(region: DocumentRegionInfo, index: number, size: number): string {
    const values: number[] = [Number(this.x1), Number(this.y1), Number(this.x2), Number(this.y2)]
    if (!values.every(value => Number.isFinite(value)) || size <= 0) {
      return '0%'
    }
    return `${Math.max(0, Math.min(100, values[index] * 100 / size))}%`
  }

  private bboxSizePercent(region: DocumentRegionInfo, first: number, second: number, size: number): string {
    const values: number[] = [Number(this.x1), Number(this.y1), Number(this.x2), Number(this.y2)]
    if (!values.every(value => Number.isFinite(value)) || size <= 0) {
      return '0%'
    }
    return `${Math.max(1, Math.min(100, (values[second] - values[first]) * 100 / size))}%`
  }

  private draftBox(): RegionBox {
    const region = this.currentRegion()
    const values: number[] = [Number(this.x1), Number(this.y1), Number(this.x2), Number(this.y2)]
    return normalizeRegionBox(values,
      region === undefined ? 1 : this.currentPageWidth(region),
      region === undefined ? 1 : this.currentPageHeight(region))
  }

  private beginBoxGesture(): void {
    if (this.recognizing) return
    this.gestureStartBox = this.draftBox()
  }

  private applyBox(box: RegionBox): void {
    this.x1 = Math.round(box.x1).toString()
    this.y1 = Math.round(box.y1).toString()
    this.x2 = Math.round(box.x2).toString()
    this.y2 = Math.round(box.y2).toString()
  }

  private coordinateValue(index: number): number {
    const box: RegionBox = this.draftBox()
    if (index === 0) return box.x1
    if (index === 1) return box.y1
    if (index === 2) return box.x2
    return box.y2
  }

  private updateDraftCoordinate(index: number, value: number): void {
    if (this.recognizing) return
    const box: RegionBox = this.draftBox()
    const region = this.currentRegion()
    if (region === undefined) return
    const minimumSize: number = 8
    if (index === 0) {
      box.x1 = Math.max(0, Math.min(box.x2 - minimumSize, value))
    } else if (index === 1) {
      box.y1 = Math.max(0, Math.min(box.y2 - minimumSize, value))
    } else if (index === 2) {
      box.x2 = Math.max(box.x1 + minimumSize, Math.min(this.currentPageWidth(region), value))
    } else {
      box.y2 = Math.max(box.y1 + minimumSize, Math.min(this.currentPageHeight(region), value))
    }
    this.applyBox(box)
  }

  private currentPageAspectRatio(): number {
    const region = this.currentRegion()
    if (region === undefined) return 1
    return this.currentPageWidth(region) / this.currentPageHeight(region)
  }

  @Builder
  private coordinateSlider(label: string, index: number, maximum: number) {
    Row({ space: 8 }) {
      Text(label).fontSize(11).fontColor($r('app.color.text_body')).width(28)
      Slider({
        value: this.coordinateValue(index),
        min: 0,
        max: Math.max(1, maximum),
        step: 1
      })
        .layoutWeight(1)
        .selectedColor($r('app.color.primary'))
        .trackColor($r('app.color.surface_muted'))
        .blockColor($r('app.color.primary'))
        .trackThickness(4)
        .enabled(!this.recognizing)
        .onChange((value: number) => this.updateDraftCoordinate(index, value))
      Text(`${Math.round(this.coordinateValue(index))}`)
        .fontSize(10).fontColor($r('app.color.text_secondary')).width(44).textAlign(TextAlign.End)
    }
    .width('100%').height(34)
  }

  private moveDraftBox(event: GestureEvent): void {
    const region = this.currentRegion()
    if (region === undefined || this.recognizing || this.isScrollAxisEvent(event)) return
    this.applyBox(moveRegionBox(this.gestureStartBox, event.offsetX, event.offsetY,
      this.previewWidth, this.previewHeight, this.currentPageWidth(region), this.currentPageHeight(region)))
  }

  private resizeDraftBox(event: GestureEvent): void {
    const region = this.currentRegion()
    if (region === undefined || this.recognizing || this.isScrollAxisEvent(event)) return
    this.applyBox(resizeRegionBox(this.gestureStartBox, event.offsetX, event.offsetY,
      this.previewWidth, this.previewHeight, this.currentPageWidth(region), this.currentPageHeight(region)))
  }

  private isScrollAxisEvent(event: GestureEvent): boolean {
    return (event.axisHorizontal !== undefined && Math.abs(event.axisHorizontal) > 0) ||
      (event.axisVertical !== undefined && Math.abs(event.axisVertical) > 0)
  }

  private validCoordinates(): boolean {
    const values: number[] = [Number(this.x1), Number(this.y1), Number(this.x2), Number(this.y2)]
    return values.every(value => Number.isFinite(value)) && values[2] > values[0] && values[3] > values[1]
  }

  private async saveRegion(showToast: boolean = true): Promise<boolean> {
    const region = this.currentRegion()
    if (region === undefined || this.saving || !this.validCoordinates()) {
      if (!this.validCoordinates()) {
        await promptAction.openToast({ message: '识别框位置无效，请在图片上重新调整。' })
      }
      return false
    }
    this.saving = true
    try {
      await DocumentApi.editRegion(this.apiBaseUrl, this.apiKey, this.documentId, region.regionId, {
        bbox: [Number(this.x1), Number(this.y1), Number(this.x2), Number(this.y2)],
        regionType: this.regionType,
        confirmed: this.confirmed,
        note: this.note
      })
      await this.loadDocument()
      if (showToast) {
        await promptAction.openToast({ message: '区域和人工确认状态已保存。' })
      }
      return true
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '保存区域失败。') })
      return false
    } finally {
      this.saving = false
    }
  }

  private async recognizeRegion(): Promise<void> {
    const region = this.currentRegion()
    if (region === undefined || this.recognizing) {
      return
    }
    if (this.regionType === 'reaction_like') {
      await promptAction.openToast({ message: '反应流程区域只做分类，本阶段不进行分子结构识别。' })
      return
    }
    if (this.regionType !== 'molecule' || !this.confirmed) {
      await promptAction.openToast({ message: '请选择“分子”类型并开启人工确认。' })
      return
    }
    if (!(await this.saveRegion(false))) {
      return
    }
    this.recognizing = true
    try {
      const current = this.currentRegion()
      if (current === undefined) {
        return
      }
      const job: DocumentJob = await DocumentApi.recognizeRegion(
        this.apiBaseUrl, this.apiKey, this.documentId, current.regionId, this.note
      )
      this.recognitionJobId = job.jobId
      this.recognitionRegionId = current.regionId
      this.recognitionStatus = job.status
      this.recognitionMessage = job.message
      await SettingsStore.saveActiveRegionTask(
        getContext(this), this.documentId, this.recognitionRegionId, this.recognitionJobId)
      this.startPolling()
    } catch (error) {
      this.recognizing = false
      await promptAction.openToast({ message: userFacingError(error as Object, '提交区域识别失败。') })
    }
  }

  private startPolling(): void {
    this.stopPolling()
    this.pollTimer = setInterval(() => this.pollRecognition(), 1800)
    this.pollRecognition()
  }

  private stopPolling(): void {
    if (this.pollTimer >= 0) {
      clearInterval(this.pollTimer)
      this.pollTimer = -1
    }
  }

  private async pollRecognition(): Promise<void> {
    if (this.recognitionJobId.length === 0) {
      return
    }
    try {
      const job = await DocumentApi.job(this.apiBaseUrl, this.apiKey, this.recognitionJobId)
      this.recognitionStatus = job.status
      this.recognitionMessage = job.message
      if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
        this.stopPolling()
        this.recognizing = false
        this.recognitionJobId = ''
        this.recognitionRegionId = ''
        await SettingsStore.saveActiveRegionTask(getContext(this), '', '', '')
        await this.loadDocument()
      }
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        this.stopPolling()
        this.recognizing = false
        this.recognitionStatus = 'missing'
        this.recognitionMessage = '该区域任务已被电脑端清理，可重新提交识别。'
        this.recognitionJobId = ''
        this.recognitionRegionId = ''
        await SettingsStore.saveActiveRegionTask(getContext(this), '', '', '')
      } else if (isUnauthorizedError(error as Object)) {
        this.stopPolling()
        this.recognitionStatus = 'auth_required'
        this.recognitionMessage = '登录已失效，任务记录仍保留；重新登录后会继续恢复。'
      } else {
        this.recognitionStatus = 'network_wait'
        this.recognitionMessage = isTransportError(error as Object) ?
          '连接暂时中断，正在保留任务并等待网络恢复…' : '暂时无法查询任务，稍后自动重试。'
      }
    }
  }

  private async controlRecognition(action: string): Promise<void> {
    if (this.recognitionJobId.length === 0) return
    try {
      let job: DocumentJob
      if (action === 'pause') {
        job = await DocumentApi.pauseJob(this.apiBaseUrl, this.apiKey, this.recognitionJobId)
      } else if (action === 'resume') {
        job = await DocumentApi.resumeJob(this.apiBaseUrl, this.apiKey, this.recognitionJobId)
      } else {
        job = await DocumentApi.cancelJob(this.apiBaseUrl, this.apiKey, this.recognitionJobId)
      }
      this.recognitionStatus = job.status
      this.recognitionMessage = job.message
      if (job.status === 'cancelled') {
        this.recognizing = false
        this.stopPolling()
        this.recognitionJobId = ''
        this.recognitionRegionId = ''
        await SettingsStore.saveActiveRegionTask(getContext(this), '', '', '')
      } else {
        this.startPolling()
      }
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '控制区域识别失败。') })
    }
  }

  private confirmCancelRecognition(): void {
    showDangerConfirmation({
      title: '终止区域识别？',
      message: '终止后当前区域不会继续识别，已经保存的审核内容仍会保留。',
      confirmText: '确认终止'
    }, () => this.controlRecognition('cancel'))
  }

  private async saveAndNext(): Promise<void> {
    if (await this.saveRegion(false)) {
      await this.moveRegion(1)
      await promptAction.openToast({ message: '已保存，进入下一项。' })
    }
  }

  private async openAnalysis(region: DocumentRegionInfo): Promise<void> {
    if (region.analysisId.length === 0) {
      return
    }
    await router.pushUrl({ url: 'pages/ResultDetail', params: { analysisId: region.analysisId } })
  }

  build() {
    Stack() {
      MoleculeBackdrop()
      if (this.loading) {
        LoadingSkeleton({ rows: 5 })
      } else if (this.errorMessage.length > 0) {
        AppErrorState({ title: '区域审核加载失败', message: this.errorMessage, onRetry: (): void => { this.initialize() } })
      } else if (this.document === undefined || this.document.regions.length === 0) {
        Column({ space: 12 }) {
          Text('没有可审核区域').fontSize(18).fontWeight(FontWeight.Medium)
          Text('请返回文档识别页重新检测，或换用更清晰的文档。').fontSize(13)
            .fontColor($r('app.color.text_secondary'))
          Button('返回').onClick(() => this.leavePage())
        }
        .justifyContent(FlexAlign.Center).width('100%').height('100%')
      } else {
        Scroll() {
          Column({ space: 14 }) {
            Row({ space: 10 }) {
              Button('‹').width(44).height(44).backgroundColor($r('app.color.primary_soft'))
                .fontColor($r('app.color.primary')).onClick(() => this.leavePage())
              Column({ space: 2 }) {
                Text('区域审核').fontSize(22).fontWeight(FontWeight.Bold).fontColor($r('app.color.text_primary'))
                Text(`${this.document.filename} · ${this.document.regionCount} 个候选区域`)
                  .fontSize(11).fontColor($r('app.color.text_secondary')).maxLines(1)
                  .textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
              }.alignItems(HorizontalAlign.Start).layoutWeight(1)
            }.width('100%')

            Column({ space: 11 }) {
              Text('筛选区域，再逐项确认。只有“分子 + 人工确认”会进入结构识别。')
                .fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
              Row({ space: 7 }) {
                ForEach(['molecule', 'pending', 'reaction_like', 'all'], (filter: string) => {
                  Text(this.filterLabel(filter)).fontSize(11).textAlign(TextAlign.Center).layoutWeight(1)
                    .fontColor(this.regionFilter === filter ?
                      $r('app.color.text_on_primary') : $r('app.color.text_primary'))
                    .padding({ top: 10, bottom: 10 })
                    .backgroundColor(this.regionFilter === filter ?
                      $r('app.color.primary') : $r('app.color.surface_soft'))
                    .borderRadius(16).enabled(!this.saving && !this.recognizing)
                    .onClick(() => this.changeFilter(filter))
                })
              }.width('100%')
              Row({ space: 10 }) {
                Button('上一项').layoutWeight(1).height(42).onClick(() => this.moveRegion(-1))
                  .enabled(!this.saving && !this.recognizing)
                  .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
                Text(this.regionPosition()).fontSize(14).fontWeight(FontWeight.Medium)
                  .fontColor($r('app.color.text_primary')).textAlign(TextAlign.Center).width(72)
                Button('下一项').layoutWeight(1).height(42).onClick(() => this.moveRegion(1))
                  .enabled(!this.saving && !this.recognizing)
                  .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
              }.width('100%')
            }
            .alignItems(HorizontalAlign.Start).width('100%').padding(14)
            .backgroundColor($r('app.color.surface')).borderRadius(16)
            .border({ width: 1, color: $r('app.color.border') })

            if (this.currentRegion() !== undefined) {
              Column({ space: 12 }) {
                Stack() {
                  Image(this.currentPageUrl(this.currentRegion() as DocumentRegionInfo))
                    .width('100%').height('100%').objectFit(ImageFit.Contain)
                    .backgroundColor($r('app.color.surface_structure')).borderRadius(14)
                  Stack() {
                    Row()
                      .width('100%')
                      .height('100%')
                      .backgroundColor('#18F5A623')
                      .gesture(
                        PanGesture({ direction: PanDirection.All, distance: 8 })
                          .onActionStart(() => this.beginBoxGesture())
                          .onActionUpdate((event: GestureEvent) => this.moveDraftBox(event))
                      )
                      .accessibilityText('拖动识别框内部可移动位置')
                    Text('✥')
                      .width(editingTouchTarget())
                      .height(editingTouchTarget())
                      .fontSize(13)
                      .fontWeight(FontWeight.Bold)
                      .fontColor($r('app.color.primary'))
                      .textAlign(TextAlign.Center)
                      .backgroundColor(Color.Transparent)
                      .align(Alignment.TopStart)
                      .gesture(
                        PanGesture({ direction: PanDirection.All, distance: 5 })
                          .onActionStart(() => this.beginBoxGesture())
                          .onActionUpdate((event: GestureEvent) => this.moveDraftBox(event))
                      )
                      .accessibilityText('拖动移动识别框')
                    Text('◢')
                      .width(editingTouchTarget())
                      .height(editingTouchTarget())
                      .fontSize(13)
                      .fontWeight(FontWeight.Bold)
                      .fontColor($r('app.color.warning'))
                      .textAlign(TextAlign.Center)
                      .backgroundColor(Color.Transparent)
                      .align(Alignment.BottomEnd)
                      .gesture(
                        PanGesture({ direction: PanDirection.All, distance: 5 })
                          .onActionStart(() => this.beginBoxGesture())
                          .onActionUpdate((event: GestureEvent) => this.resizeDraftBox(event))
                      )
                      .accessibilityText('拖动调整识别框大小')
                  }
                    .position({
                      x: this.bboxPercent(this.currentRegion() as DocumentRegionInfo, 0,
                        this.currentPageWidth(this.currentRegion() as DocumentRegionInfo)),
                      y: this.bboxPercent(this.currentRegion() as DocumentRegionInfo, 1,
                        this.currentPageHeight(this.currentRegion() as DocumentRegionInfo))
                    })
                    .width(this.bboxSizePercent(this.currentRegion() as DocumentRegionInfo, 0, 2,
                      this.currentPageWidth(this.currentRegion() as DocumentRegionInfo)))
                    .height(this.bboxSizePercent(this.currentRegion() as DocumentRegionInfo, 1, 3,
                      this.currentPageHeight(this.currentRegion() as DocumentRegionInfo)))
                    .border({ width: 2, color: $r('app.color.warning') })
                    .accessibilityText('识别框。拖动框内移动，右下角调整大小')
                }
                .width('100%').aspectRatio(this.currentPageAspectRatio()).clip(true)
                .onAreaChange((_oldArea: Area, newArea: Area) => {
                  this.previewWidth = Math.max(1, Number(newArea.width))
                  this.previewHeight = Math.max(1, Number(newArea.height))
                })

                Row() {
                  Text(`第 ${(this.currentRegion() as DocumentRegionInfo).pageNumber} 页 · ` +
                    this.typeLabel((this.currentRegion() as DocumentRegionInfo).regionType))
                    .fontSize(12).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
                  Blank()
                  Text(this.coordinatesExpanded ? '收起精确调整' : '精确调整').fontSize(11)
                    .fontColor($r('app.color.primary'))
                }.width('100%').onClick(() => this.coordinatesExpanded = !this.coordinatesExpanded)
                Text('滚轮不会改变边框。拖动框内任意空白位置移动，拖动右下角调整大小。')
                  .fontSize(11).fontColor($r('app.color.text_secondary')).width('100%')
                if (this.coordinatesExpanded) {
                  Column({ space: 2 }) {
                    this.coordinateSlider('左', 0,
                      this.currentPageWidth(this.currentRegion() as DocumentRegionInfo))
                    this.coordinateSlider('上', 1,
                      this.currentPageHeight(this.currentRegion() as DocumentRegionInfo))
                    this.coordinateSlider('右', 2,
                      this.currentPageWidth(this.currentRegion() as DocumentRegionInfo))
                    this.coordinateSlider('下', 3,
                      this.currentPageHeight(this.currentRegion() as DocumentRegionInfo))
                  }
                  .width('100%').padding(10)
                  .backgroundColor($r('app.color.surface_soft')).borderRadius(12)
                }

                Text('区域类型').fontSize(13).fontWeight(FontWeight.Medium)
                  .fontColor($r('app.color.text_primary')).width('100%')
                Scroll() {
                  Row({ space: 8 }) {
                    ForEach(REGION_TYPES, (type: string) => {
                      Text(this.typeLabel(type)).fontSize(11)
                        .fontColor(this.regionType === type ? $r('app.color.text_on_primary') : $r('app.color.text_primary'))
                        .padding({ left: 12, right: 12, top: 9, bottom: 9 })
                        .backgroundColor(this.regionType === type ? $r('app.color.primary') : $r('app.color.surface_soft'))
                        .borderRadius(16).enabled(!this.recognizing).onClick(() => this.regionType = type)
                    })
                  }
                }.scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')

                Row() {
                  Column({ space: 3 }) {
                    Text('人工确认').fontSize(14).fontWeight(FontWeight.Medium)
                    Text('只有人工确认的分子区域才会开始结构识别').fontSize(10)
                      .fontColor($r('app.color.text_secondary'))
                  }.alignItems(HorizontalAlign.Start).layoutWeight(1)
                  Toggle({ type: ToggleType.Switch, isOn: this.confirmed })
                    .enabled(!this.recognizing)
                    .onChange(value => this.confirmed = value)
                }.width('100%')
                TextInput({ text: this.note, placeholder: '审核备注（可选）' })
                  .enabled(!this.recognizing).onChange(value => this.note = value).width('100%')

                if (this.regionType === 'reaction_like') {
                  Row({ space: 8 }) {
                    Text('⇢').fontSize(18).fontColor($r('app.color.warning'))
                    Text('该区域只做反应流程分流，本阶段不解析。').fontSize(12)
                      .fontColor($r('app.color.text_secondary')).layoutWeight(1)
                  }.width('100%').padding(12).backgroundColor($r('app.color.warning_soft')).borderRadius(12)
                }

                Button(this.saving ? '正在保存…' : '保存并下一项').width('100%').height(46)
                  .enabled(!this.saving && !this.recognizing)
                  .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
                  .onClick(() => this.saveAndNext())
                Button(this.recognizing ? '区域识别中…' : '确认并识别分子').width('100%').height(50)
                  .enabled(!this.saving && !this.recognizing && this.confirmed && this.regionType === 'molecule')
                  .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.primary'))
                  .onClick(() => this.recognizeRegion())
                if (this.recognitionMessage.length > 0) {
                  Text(this.recognitionMessage).fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
                }
                if (this.recognizing && (this.recognitionStatus === 'queued' ||
                  this.recognitionStatus === 'running' || this.recognitionStatus === 'pausing')) {
                  Row({ space: 8 }) {
                    Button(this.recognitionStatus === 'pausing' ? '继续' : '暂停').layoutWeight(1).height(42)
                      .onClick(() => this.controlRecognition(this.recognitionStatus === 'pausing' ? 'resume' : 'pause'))
                      .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
                    Button('终止').layoutWeight(1).height(42).onClick(() => this.confirmCancelRecognition())
                      .fontColor($r('app.color.danger')).backgroundColor($r('app.color.danger_soft'))
                  }.width('100%')
                } else if (this.recognizing && this.recognitionStatus === 'paused') {
                  Row({ space: 8 }) {
                    Button('继续').layoutWeight(1).height(42).onClick(() => this.controlRecognition('resume'))
                    Button('终止').layoutWeight(1).height(42).onClick(() => this.confirmCancelRecognition())
                      .fontColor($r('app.color.danger')).backgroundColor($r('app.color.danger_soft'))
                  }.width('100%')
                }
                if ((this.currentRegion() as DocumentRegionInfo).analysisId.length > 0) {
                  Button('查看分子结果').width('100%').height(46)
                    .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.success'))
                    .onClick(() => this.openAnalysis(this.currentRegion() as DocumentRegionInfo))
                }
              }
              .alignItems(HorizontalAlign.Start).width('100%').padding(16)
              .backgroundColor($r('app.color.surface')).borderRadius($r('app.float.card_radius'))
              .border({ width: 1, color: $r('app.color.border') })
            }
          }
          .width('100%').padding({ left: 14, right: 14, top: 16, bottom: 28 })
        }
        .width('100%').height('100%').scrollBar(BarState.Off)
      }
    }
    .width('100%').height('100%').backgroundColor($r('app.color.page_background'))
  }
}
