import promptAction from '@ohos.promptAction'
import router from '@ohos.router'
import { common } from '@kit.AbilityKit'
import { ExportCenterView } from '../components/ExportCenterView'
import { ExportItem, ExportManifest } from '../model/ExportResult'
import { ApiClient, ApiError, isUnauthorizedError } from '../services/ApiClient'
import { userFacingError } from '../services/AppUtils'
import { ExportApi } from '../services/ExportApi'
import { ExportFileService } from '../services/ExportFileService'
import { SecureStore } from '../services/SecureStore'
import { SettingsStore, StoredSettings } from '../services/SettingsStore'

interface ExportCenterParams {
  scope: string
  resourceId: string
}

@Entry
@Component
struct ExportCenter {
  @State apiBaseUrl: string = ''
  @State apiKey: string = ''
  @State scope: string = 'analysis'
  @State resourceId: string = ''
  @State manifest: ExportManifest | undefined = undefined
  @State loading: boolean = true
  @State busyFormat: string = ''
  @State errorMessage: string = ''

  aboutToAppear(): void {
    this.initialize()
  }

  private async initialize(): Promise<void> {
    try {
      const params = router.getParams() as ExportCenterParams
      this.scope = params.scope === 'batch' ? 'batch' : 'analysis'
      this.resourceId = params.resourceId
      const settings: StoredSettings = await SettingsStore.load(getContext(this))
      this.apiBaseUrl = settings.apiBaseUrl
      this.apiKey = settings.apiKey
      ApiClient.setSessionToken(settings.authToken)
      await this.refresh()
    } catch (error) {
      this.loading = false
      this.errorMessage = userFacingError(error as Object, '无法打开导出中心，请返回后重试。')
    }
  }

  private async refresh(): Promise<void> {
    if (this.resourceId.length === 0) return
    this.loading = true
    this.errorMessage = ''
    try {
      this.manifest = await ExportApi.manifest(this.apiBaseUrl, this.apiKey, this.scope, this.resourceId)
    } catch (error) {
      this.manifest = undefined
      await this.handleError(error as Object, '生成导出文件失败，请重试。')
    } finally {
      this.loading = false
    }
  }

  private async currentDownload(item: ExportItem): Promise<ArrayBuffer> {
    try {
      return await ExportApi.download(item)
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 410) {
        const refreshed: ExportManifest = await ExportApi.manifest(
          this.apiBaseUrl, this.apiKey, this.scope, this.resourceId)
        this.manifest = refreshed
        const fresh: ExportItem | undefined = refreshed.items.find((candidate: ExportItem) =>
          candidate.format === item.format)
        if (fresh !== undefined) return ExportApi.download(fresh)
      }
      if (error instanceof Error) throw error
      throw new ApiError(0, '下载导出文件失败，请重试。')
    }
  }

  private async save(item: ExportItem): Promise<void> {
    if (this.busyFormat.length > 0) return
    this.busyFormat = item.format
    this.errorMessage = ''
    try {
      const content: ArrayBuffer = await this.currentDownload(item)
      const uri: string = await ExportFileService.save(getContext(this) as common.UIAbilityContext, item, content)
      if (uri.length > 0) await promptAction.openToast({ message: `${item.label}已保存。` })
    } catch (error) {
      await this.handleError(error as Object, '保存导出文件失败，请重试。')
    } finally {
      this.busyFormat = ''
    }
  }

  private async share(item: ExportItem): Promise<void> {
    if (this.busyFormat.length > 0) return
    this.busyFormat = item.format
    this.errorMessage = ''
    try {
      const content: ArrayBuffer = await this.currentDownload(item)
      await ExportFileService.share(getContext(this) as common.UIAbilityContext, item, content)
    } catch (error) {
      await this.handleError(error as Object, '分享导出文件失败，请重试。')
    } finally {
      this.busyFormat = ''
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
      ExportCenterView({
        manifest: this.manifest,
        loading: this.loading,
        busyFormat: this.busyFormat,
        errorMessage: this.errorMessage,
        onBack: (): void => { router.back() },
        onRefresh: (): void => { this.refresh() },
        onSave: (item: ExportItem): void => { this.save(item) },
        onShare: (item: ExportItem): void => { this.share(item) }
      })
    }.width('100%').height('100%')
  }
}
