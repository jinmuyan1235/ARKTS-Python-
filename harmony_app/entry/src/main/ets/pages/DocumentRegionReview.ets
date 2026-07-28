import promptAction from '@ohos.promptAction'
import router from '@ohos.router'
import { DocumentJob, DocumentRegionInfo, DocumentResult } from '../model/DocumentResult'
import { ApiClient } from '../services/ApiClient'
import { DocumentApi } from '../services/DocumentApi'
import { SettingsStore, StoredSettings } from '../services/SettingsStore'
import { userFacingError } from '../services/AppUtils'
import { AppErrorState, LoadingSkeleton, MoleculeBackdrop, StatusPill } from '../components/CommonStates'

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
  private recognitionJobId: string = ''
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
    } catch (error) {
      this.errorMessage = userFacingError(error as Object, '加载区域审核页面失败。')
    } finally {
      this.loading = false
    }
  }

  private async loadDocument(): Promise<void> {
    this.document = await DocumentApi.document(this.apiBaseUrl, this.apiKey, this.documentId)
    if (this.document.regions.length > 0) {
      this.selectedIndex = Math.min(this.selectedIndex, this.document.regions.length - 1)
      this.syncForm(this.document.regions[this.selectedIndex])
    }
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
  }

  private selectRegion(index: number): void {
    if (this.document === undefined || index < 0 || index >= this.document.regions.length) {
      return
    }
    this.selectedIndex = index
    this.syncForm(this.document.regions[index])
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
    if (region.bbox.length !== 4 || size <= 0) {
      return '0%'
    }
    return `${Math.max(0, Math.min(100, region.bbox[index] * 100 / size))}%`
  }

  private bboxSizePercent(region: DocumentRegionInfo, first: number, second: number, size: number): string {
    if (region.bbox.length !== 4 || size <= 0) {
      return '0%'
    }
    return `${Math.max(1, Math.min(100, (region.bbox[second] - region.bbox[first]) * 100 / size))}%`
  }

  private validCoordinates(): boolean {
    const values: number[] = [Number(this.x1), Number(this.y1), Number(this.x2), Number(this.y2)]
    return values.every(value => Number.isFinite(value)) && values[2] > values[0] && values[3] > values[1]
  }

  private async saveRegion(showToast: boolean = true): Promise<boolean> {
    const region = this.currentRegion()
    if (region === undefined || this.saving || !this.validCoordinates()) {
      if (!this.validCoordinates()) {
        await promptAction.openToast({ message: '请输入有效区域坐标，且 x2/y2 必须大于 x1/y1。' })
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
      await promptAction.openToast({ message: 'reaction_like 只进入反应流程分流，不执行 OCSR。' })
      return
    }
    if (this.regionType !== 'molecule' || !this.confirmed) {
      await promptAction.openToast({ message: '请将区域标记为 molecule，并开启人工确认。' })
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
      this.recognitionMessage = job.message
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
      this.recognitionMessage = job.message
      if (job.status === 'completed' || job.status === 'failed') {
        this.stopPolling()
        this.recognizing = false
        await this.loadDocument()
      }
    } catch (error) {
      this.stopPolling()
      this.recognizing = false
      this.recognitionMessage = userFacingError(error as Object, '查询区域识别任务失败。')
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
          Button('返回').onClick(() => router.back())
        }
        .justifyContent(FlexAlign.Center).width('100%').height('100%')
      } else {
        Scroll() {
          Column({ space: 14 }) {
            Row({ space: 10 }) {
              Button('‹').width(44).height(44).backgroundColor($r('app.color.primary_soft'))
                .fontColor($r('app.color.primary')).onClick(() => router.back())
              Column({ space: 2 }) {
                Text('区域审核').fontSize(22).fontWeight(FontWeight.Bold).fontColor($r('app.color.text_primary'))
                Text(`${this.document.filename} · ${this.document.regionCount} 个候选区域`)
                  .fontSize(11).fontColor($r('app.color.text_secondary'))
              }.alignItems(HorizontalAlign.Start)
            }.width('100%')

            Scroll() {
              Row({ space: 8 }) {
                ForEach(this.document.regions, (region: DocumentRegionInfo, index: number) => {
                  Text(`${index + 1} · ${region.regionType}`)
                    .fontSize(12).fontColor(this.selectedIndex === index ?
                      $r('app.color.text_on_primary') : $r('app.color.text_primary'))
                    .padding({ left: 14, right: 14, top: 10, bottom: 10 })
                    .backgroundColor(this.selectedIndex === index ?
                      $r('app.color.primary') : $r('app.color.surface_soft'))
                    .borderRadius(18).onClick(() => this.selectRegion(index))
                }, (region: DocumentRegionInfo) => region.regionId)
              }
            }
            .scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')

            if (this.currentRegion() !== undefined) {
              Column({ space: 12 }) {
                Stack() {
                  Image(this.currentPageUrl(this.currentRegion() as DocumentRegionInfo))
                    .width('100%').height(280).objectFit(ImageFit.Fill)
                    .backgroundColor($r('app.color.surface_structure')).borderRadius(14)
                  Row()
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
                    .border({ width: 3, color: $r('app.color.warning') })
                    .backgroundColor('#18F5A623')
                }
                .width('100%').height(280).clip(true)

                Row({ space: 8 }) {
                  TextInput({ text: this.x1, placeholder: 'x1' }).onChange(value => this.x1 = value)
                    .type(InputType.Number).layoutWeight(1)
                  TextInput({ text: this.y1, placeholder: 'y1' }).onChange(value => this.y1 = value)
                    .type(InputType.Number).layoutWeight(1)
                  TextInput({ text: this.x2, placeholder: 'x2' }).onChange(value => this.x2 = value)
                    .type(InputType.Number).layoutWeight(1)
                  TextInput({ text: this.y2, placeholder: 'y2' }).onChange(value => this.y2 = value)
                    .type(InputType.Number).layoutWeight(1)
                }.width('100%')

                Text('区域类型').fontSize(13).fontWeight(FontWeight.Medium)
                  .fontColor($r('app.color.text_primary')).width('100%')
                Scroll() {
                  Row({ space: 8 }) {
                    ForEach(REGION_TYPES, (type: string) => {
                      Text(type).fontSize(11)
                        .fontColor(this.regionType === type ? $r('app.color.text_on_primary') : $r('app.color.text_primary'))
                        .padding({ left: 12, right: 12, top: 9, bottom: 9 })
                        .backgroundColor(this.regionType === type ? $r('app.color.primary') : $r('app.color.surface_soft'))
                        .borderRadius(16).onClick(() => this.regionType = type)
                    })
                  }
                }.scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')

                Row() {
                  Column({ space: 3 }) {
                    Text('人工确认').fontSize(14).fontWeight(FontWeight.Medium)
                    Text('只有确认的 molecule 才允许进入 OCSR').fontSize(10)
                      .fontColor($r('app.color.text_secondary'))
                  }.alignItems(HorizontalAlign.Start).layoutWeight(1)
                  Toggle({ type: ToggleType.Switch, isOn: this.confirmed })
                    .onChange(value => this.confirmed = value)
                }.width('100%')
                TextInput({ text: this.note, placeholder: '审核备注（可选）' })
                  .onChange(value => this.note = value).width('100%')

                if (this.regionType === 'reaction_like') {
                  Row({ space: 8 }) {
                    Text('⇢').fontSize(18).fontColor($r('app.color.warning'))
                    Text('该区域只做反应流程分流，本阶段不解析。').fontSize(12)
                      .fontColor($r('app.color.text_secondary')).layoutWeight(1)
                  }.width('100%').padding(12).backgroundColor($r('app.color.warning_soft')).borderRadius(12)
                }

                Button(this.saving ? '正在保存…' : '保存区域').width('100%').height(46)
                  .enabled(!this.saving && !this.recognizing)
                  .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
                  .onClick(() => this.saveRegion())
                Button(this.recognizing ? '区域识别中…' : '确认并识别 molecule').width('100%').height(50)
                  .enabled(!this.saving && !this.recognizing && this.confirmed && this.regionType === 'molecule')
                  .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.primary'))
                  .onClick(() => this.recognizeRegion())
                if (this.recognitionMessage.length > 0) {
                  Text(this.recognitionMessage).fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
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
