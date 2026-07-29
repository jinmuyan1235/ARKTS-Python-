import picker from '@ohos.file.picker'
import promptAction from '@ohos.promptAction'
import router from '@ohos.router'
import { common } from '@kit.AbilityKit'
import { DocumentJob, DocumentResult } from '../model/DocumentResult'
import { DocumentView } from '../components/DocumentView'
import { ApiClient } from '../services/ApiClient'
import { DocumentApi } from '../services/DocumentApi'
import { SettingsStore, StoredSettings } from '../services/SettingsStore'
import { userFacingError } from '../services/AppUtils'
import { BuiltInTestFiles, BuiltInTestSet } from '../services/BuiltInTestSet'

@Entry
@Component
struct DocumentWorkspace {
  @State apiBaseUrl: string = ''
  @State apiKey: string = ''
  @State selectedUri: string = ''
  @State builtInDocument: string = ''
  @State document: DocumentResult | undefined = undefined
  @State jobStatus: string = ''
  @State jobMessage: string = ''
  @State busy: boolean = false
  private pollTimer: number = -1
  private activeJobId: string = ''

  aboutToAppear(): void {
    this.initialize()
  }

  aboutToDisappear(): void {
    this.stopPolling()
  }

  private async initialize(): Promise<void> {
    try {
      const builtIn: BuiltInTestFiles = await BuiltInTestSet.prepare(
        getContext(this) as common.UIAbilityContext)
      this.builtInDocument = builtIn.document
    } catch (_) {
      this.builtInDocument = ''
    }
    const settings: StoredSettings = await SettingsStore.load(getContext(this))
    this.apiBaseUrl = settings.apiBaseUrl
    this.apiKey = settings.apiKey
    ApiClient.setSessionToken(settings.authToken)
  }

  private async chooseDocument(): Promise<void> {
    try {
      const options = new picker.DocumentSelectOptions()
      options.maxSelectNumber = 1
      options.fileSuffixFilters = ['.pdf', '.zip', '.png', '.jpg', '.jpeg']
      const documentPicker = new picker.DocumentViewPicker(getContext(this))
      const uris: string[] = await documentPicker.select(options)
      if (uris.length > 0) {
        this.selectedUri = uris[0]
        this.document = undefined
        this.jobStatus = ''
        this.jobMessage = ''
      }
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '选择文档失败，请重试。') })
    }
  }

  private useBuiltInDocument(): void {
    if (this.builtInDocument.length === 0) return
    this.selectedUri = this.builtInDocument
    this.document = undefined
    this.jobStatus = ''
    this.jobMessage = ''
  }

  private async uploadAndDetect(): Promise<void> {
    if (this.busy || this.selectedUri.length === 0) {
      return
    }
    this.busy = true
    try {
      this.document = await DocumentApi.upload(this.apiBaseUrl, this.apiKey, this.selectedUri)
      const job: DocumentJob = await DocumentApi.detect(this.apiBaseUrl, this.apiKey, this.document.documentId)
      this.activeJobId = job.jobId
      this.jobStatus = job.status
      this.jobMessage = job.message
      this.startPolling()
    } catch (error) {
      this.busy = false
      await promptAction.openToast({ message: userFacingError(error as Object, '文档上传或区域检测失败。') })
    }
  }

  private startPolling(): void {
    this.stopPolling()
    this.pollTimer = setInterval(() => this.pollOnce(), 1800)
    this.pollOnce()
  }

  private stopPolling(): void {
    if (this.pollTimer >= 0) {
      clearInterval(this.pollTimer)
      this.pollTimer = -1
    }
  }

  private async pollOnce(): Promise<void> {
    if (this.activeJobId.length === 0) {
      return
    }
    try {
      const job: DocumentJob = await DocumentApi.job(this.apiBaseUrl, this.apiKey, this.activeJobId)
      this.jobStatus = job.status
      this.jobMessage = job.message
      if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
        this.stopPolling()
        this.busy = false
        if (this.document !== undefined) {
          this.document = await DocumentApi.document(this.apiBaseUrl, this.apiKey, this.document.documentId)
        }
      }
    } catch (error) {
      this.stopPolling()
      this.busy = false
      this.jobStatus = 'failed'
      this.jobMessage = userFacingError(error as Object, '查询区域检测任务失败。')
    }
  }

  private async controlJob(action: string): Promise<void> {
    if (this.activeJobId.length === 0) return
    try {
      let job: DocumentJob
      if (action === 'pause') {
        job = await DocumentApi.pauseJob(this.apiBaseUrl, this.apiKey, this.activeJobId)
      } else if (action === 'resume') {
        job = await DocumentApi.resumeJob(this.apiBaseUrl, this.apiKey, this.activeJobId)
      } else {
        job = await DocumentApi.cancelJob(this.apiBaseUrl, this.apiKey, this.activeJobId)
      }
      this.jobStatus = job.status
      this.jobMessage = job.message
      if (job.status === 'cancelled') {
        this.stopPolling()
        this.busy = false
      } else {
        this.startPolling()
      }
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '控制区域检测任务失败。') })
    }
  }

  private async refreshDocument(): Promise<void> {
    if (this.document === undefined) {
      return
    }
    try {
      this.document = await DocumentApi.document(this.apiBaseUrl, this.apiKey, this.document.documentId)
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '刷新文档失败。') })
    }
  }

  private async openReview(): Promise<void> {
    if (this.document === undefined) {
      return
    }
    await router.pushUrl({
      url: 'pages/DocumentRegionReview',
      params: { documentId: this.document.documentId }
    })
  }

  build() {
    Column() {
      Row({ space: 10 }) {
        Button('‹').width(44).height(44).backgroundColor($r('app.color.primary_soft'))
          .fontColor($r('app.color.primary')).onClick(() => router.back())
        Text('文档识别').fontSize(20).fontWeight(FontWeight.Bold).fontColor($r('app.color.text_primary'))
      }
      .width('100%').height(58).padding({ left: 12, right: 12 })
      .backgroundColor($r('app.color.surface'))
      DocumentView({
        selectedUri: this.selectedUri,
        document: this.document,
        busy: this.busy,
        jobStatus: this.jobStatus,
        jobMessage: this.jobMessage,
        onChooseDocument: (): void => { this.chooseDocument() },
        onUseBuiltInDocument: (): void => { this.useBuiltInDocument() },
        onUploadAndDetect: (): void => { this.uploadAndDetect() },
        onOpenReview: (): void => { this.openReview() },
        onRefresh: (): void => { this.refreshDocument() },
        onPauseJob: (): void => { this.controlJob('pause') },
        onResumeJob: (): void => { this.controlJob('resume') },
        onCancelJob: (): void => { this.controlJob('cancel') }
      })
      .layoutWeight(1)
    }
    .width('100%').height('100%').backgroundColor($r('app.color.page_background'))
  }
}
