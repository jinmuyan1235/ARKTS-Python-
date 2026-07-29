import promptAction from '@ohos.promptAction'
import router from '@ohos.router'
import { common } from '@kit.AbilityKit'
import { DatasetReviewView } from '../components/DatasetReviewView'
import { FeedbackDetail, FeedbackItem, FeedbackManifestResult, FeedbackPage } from '../model/FeedbackResult'
import { ApiClient, isUnauthorizedError } from '../services/ApiClient'
import { userFacingError } from '../services/AppUtils'
import { DatasetApi } from '../services/DatasetApi'
import { ExportApi } from '../services/ExportApi'
import { ExportFileService } from '../services/ExportFileService'
import { SecureStore } from '../services/SecureStore'
import { SettingsStore, StoredSettings } from '../services/SettingsStore'

@Entry
@Component
struct DatasetReview {
  @State apiBaseUrl: string = ''
  @State apiKey: string = ''
  @State items: FeedbackItem[] = []
  @State selected: FeedbackDetail | undefined = undefined
  @State status: string = 'pending'
  @State notes: string = ''
  @State loading: boolean = true
  @State busy: boolean = false
  @State errorMessage: string = ''

  aboutToAppear(): void {
    this.initialize()
  }

  private async initialize(): Promise<void> {
    try {
      const settings: StoredSettings = await SettingsStore.load(getContext(this))
      this.apiBaseUrl = settings.apiBaseUrl
      this.apiKey = settings.apiKey
      ApiClient.setSessionToken(settings.authToken)
      await this.loadList()
    } catch (error) {
      this.loading = false
      await this.handleError(error as Object, '无法打开反馈审核模块。')
    }
  }

  private async loadList(): Promise<void> {
    this.loading = true
    this.errorMessage = ''
    try {
      const page: FeedbackPage = await DatasetApi.list(this.apiBaseUrl, this.apiKey, this.status)
      this.items = page.items
      if (this.items.length === 0) {
        this.selected = undefined
      } else {
        const selectedId: string = this.selected === undefined ? '' : this.selected.analysisId
        const stillVisible: FeedbackItem | undefined = this.items.find((item: FeedbackItem) => item.analysisId === selectedId)
        await this.select(stillVisible === undefined ? this.items[0].analysisId : stillVisible.analysisId)
      }
    } catch (error) {
      await this.handleError(error as Object, '加载反馈审核列表失败。')
    } finally {
      this.loading = false
    }
  }

  private async select(analysisId: string): Promise<void> {
    try {
      this.selected = await DatasetApi.detail(this.apiBaseUrl, this.apiKey, analysisId)
      this.notes = ''
    } catch (error) {
      await this.handleError(error as Object, '加载反馈详情失败。')
    }
  }

  private async changeStatus(value: string): Promise<void> {
    this.status = value
    this.selected = undefined
    await this.loadList()
  }

  private async review(action: string): Promise<void> {
    if (this.selected === undefined || this.busy) return
    this.busy = true
    this.errorMessage = ''
    try {
      this.selected = await DatasetApi.review(
        this.apiBaseUrl, this.apiKey, this.selected.analysisId, action, this.notes.trim())
      await promptAction.openToast({ message: '审核状态已更新。' })
      await this.loadList()
    } catch (error) {
      await this.handleError(error as Object, '更新审核状态失败。')
    } finally {
      this.busy = false
    }
  }

  private async exportManifest(): Promise<void> {
    if (this.busy) return
    this.busy = true
    try {
      const result: FeedbackManifestResult = await DatasetApi.exportManifest(this.apiBaseUrl, this.apiKey)
      const content: ArrayBuffer = await ExportApi.download(result.item)
      const uri: string = await ExportFileService.save(
        getContext(this) as common.UIAbilityContext, result.item, content)
      if (uri.length > 0) {
        await promptAction.openToast({ message: `已保存 ${result.exportedCount} 条核验样本的 Manifest。` })
      }
    } catch (error) {
      await this.handleError(error as Object, '导出核验 Manifest 失败。')
    } finally {
      this.busy = false
    }
  }

  private async handleError(error: Object, fallback: string): Promise<void> {
    if (isUnauthorizedError(error)) {
      await SecureStore.clearSession()
      ApiClient.setSessionToken('')
      await promptAction.openToast({ message: '登录已过期，请重新登录。' })
      router.back()
      return
    }
    this.errorMessage = userFacingError(error, fallback)
    await promptAction.openToast({ message: this.errorMessage })
  }

  build() {
    Stack() {
      DatasetReviewView({
        items: this.items,
        selected: this.selected,
        status: this.status,
        notes: this.notes,
        loading: this.loading,
        busy: this.busy,
        errorMessage: this.errorMessage,
        onBack: (): void => { router.back() },
        onRefresh: (): void => { this.loadList() },
        onStatusChanged: (value: string): void => { this.changeStatus(value) },
        onSelect: (analysisId: string): void => { this.select(analysisId) },
        onNotesChanged: (value: string): void => { this.notes = value },
        onReview: (action: string): void => { this.review(action) },
        onExportManifest: (): void => { this.exportManifest() }
      })
    }.width('100%').height('100%')
  }
}
