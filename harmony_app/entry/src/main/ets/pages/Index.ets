import photoAccessHelper from '@ohos.file.photoAccessHelper'
import promptAction from '@ohos.promptAction'
import router from '@ohos.router'
import {
  AuthResult,
  ChangePasswordResult,
  HealthResult,
  HistoryItem,
  HistoryPage,
  VisionAnalysisResult,
  VisionJob,
  VisionSample
} from '../model/VisionResult'
import { SettingsStore, StoredSettings } from '../services/SettingsStore'
import { ApiClient, ApiError, isTransportError, isUnauthorizedError } from '../services/ApiClient'
import { AuthApi } from '../services/AuthApi'
import { JobApi } from '../services/JobApi'
import { AnalysisApi } from '../services/AnalysisApi'
import {
  FormValidation,
  userFacingError,
  validateConnectionForm,
  validateLoginForm,
  validateRegistrationForm
} from '../services/AppUtils'
import { CompletionBanner } from '../components/CommonStates'
import { AuthView } from '../components/AuthView'
import { AppShell } from '../components/AppShell'
import { RecognitionView } from '../components/RecognitionView'
import { SmilesView } from '../components/SmilesView'
import { HistoryView } from '../components/HistoryView'
import { SettingsView } from '../components/SettingsView'
import { AuthController } from '../controllers/AuthController'
import { ConnectionController } from '../controllers/ConnectionController'
import { JobController } from '../controllers/JobController'
import { HistoryController } from '../controllers/HistoryController'

@Entry
@Component
struct Index {
  @State currentTab: number = 0
  // Phase one keeps orchestration in the page while moving each state domain
  // behind a small observable controller. Later phases can migrate behavior
  // without changing child component contracts again.
  @State auth: AuthController = new AuthController()
  @State connection: ConnectionController = new ConnectionController()
  @State job: JobController = new JobController()
  @State history: HistoryController = new HistoryController()
  private pollTimer: number = -1

  aboutToAppear(): void {
    this.initialize()
  }

  aboutToDisappear(): void {
    this.stopPolling()
  }

  onPageShow(): void {
    if (this.auth.authenticated && this.job.pendingCount() > 0) {
      this.refreshPendingTasks()
    }
  }

  private async initialize(): Promise<void> {
    try {
      const settings: StoredSettings = await SettingsStore.load(getContext(this))
      this.connection.apiBaseUrl = settings.apiBaseUrl
      this.connection.apiKey = settings.apiKey
      this.job.restore(settings.activeJobIds)
      ApiClient.setSessionToken(settings.authToken)
      if (this.isConfigured() && settings.authToken.length > 0) {
        try {
          this.auth.currentUser = await AuthApi.me(this.connection.apiBaseUrl, this.connection.apiKey)
          this.auth.syncProfileFields()
          this.auth.authenticated = true
          await this.loadSamples(false)
          await this.loadMembers()
          if (this.job.pendingCount() > 0) {
            await this.refreshPendingTasks()
          }
        } catch (error) {
          ApiClient.setSessionToken('')
          await SettingsStore.saveAuthToken(getContext(this), '')
        }
      }
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '读取本地配置失败，请重新打开应用。') })
    } finally {
      this.auth.authReady = true
    }
  }

  private async submitAuth(): Promise<void> {
    if (this.auth.authBusy) {
      return
    }
    const connectionValidation: FormValidation = validateConnectionForm(this.connection.apiBaseUrl, this.connection.apiKey)
    if (!connectionValidation.valid) {
      await promptAction.openToast({ message: connectionValidation.message })
      return
    }
    const authValidation: FormValidation = this.auth.authMode === 'register' ?
      validateRegistrationForm(this.auth.authUsername, this.auth.authDisplayName, this.auth.authPassword) :
      validateLoginForm(this.auth.authUsername, this.auth.authPassword)
    if (!authValidation.valid) {
      await promptAction.openToast({ message: authValidation.message })
      return
    }
    if (this.auth.authMode === 'register') {
      if (this.auth.authDisplayName.trim().length === 0) {
        await promptAction.openToast({ message: '请填写组员显示名称。' })
        return
      }
      if (this.auth.authPassword !== this.auth.authPasswordConfirm) {
        await promptAction.openToast({ message: '两次输入的密码不一致。' })
        return
      }
    }
    this.auth.authBusy = true
    this.auth.authError = ''
    try {
      await SettingsStore.saveConnection(getContext(this), this.connection.apiBaseUrl, this.connection.apiKey)
      ApiClient.setSessionToken('')
      let auth: AuthResult
      if (this.auth.authMode === 'register') {
        auth = await AuthApi.register(
          this.connection.apiBaseUrl,
          this.connection.apiKey,
          this.auth.authUsername.trim(),
          this.auth.authDisplayName.trim(),
          this.auth.authPassword,
          this.auth.authRole.trim()
        )
      } else {
        auth = await AuthApi.login(
          this.connection.apiBaseUrl,
          this.connection.apiKey,
          this.auth.authUsername.trim(),
          this.auth.authPassword
        )
      }
      ApiClient.setSessionToken(auth.token)
      await SettingsStore.saveAuthToken(getContext(this), auth.token)
      this.auth.currentUser = auth.user
      this.auth.syncProfileFields()
      this.auth.authenticated = true
      this.auth.authPassword = ''
      this.auth.authPasswordConfirm = ''
      await this.loadSamples(false)
      await this.loadMembers()
      if (this.job.pendingCount() > 0) {
        this.startPolling()
      }
    } catch (error) {
      ApiClient.setSessionToken('')
      this.auth.authError = userFacingError(error as Object, '登录或注册失败，请检查输入后重试。')
    } finally {
      this.auth.authBusy = false
    }
  }

  private async logout(): Promise<void> {
    try {
      await AuthApi.logout(this.connection.apiBaseUrl, this.connection.apiKey)
    } catch (error) {
    }
    this.stopPolling()
    ApiClient.setSessionToken('')
    await SettingsStore.saveAuthToken(getContext(this), '')
    this.auth.clearSession()
    this.currentTab = 0
  }

  private async expireSession(): Promise<void> {
    this.stopPolling()
    ApiClient.setSessionToken('')
    await SettingsStore.saveAuthToken(getContext(this), '')
    this.auth.clearSession()
    this.currentTab = 0
    await promptAction.openToast({ message: '登录已失效，请重新登录。' })
  }

  private async showError(error: Object): Promise<void> {
    if (isUnauthorizedError(error) && this.auth.authenticated) {
      await this.expireSession()
      return
    }
    if (isTransportError(error)) {
      this.connection.markTransportFailure()
    }
    await promptAction.openToast({ message: userFacingError(error, '操作失败，请稍后重试。') })
  }

  private async saveProfile(): Promise<void> {
    if (this.auth.profileSaving || this.auth.profileDisplayName.trim().length === 0) {
      return
    }
    this.auth.profileSaving = true
    try {
      this.auth.currentUser = await AuthApi.updateProfile(
        this.connection.apiBaseUrl,
        this.connection.apiKey,
        this.auth.profileDisplayName,
        this.auth.profileRole
      )
      this.auth.syncProfileFields()
      await this.loadMembers()
      await promptAction.openToast({ message: '个人资料已保存。' })
    } catch (error) {
      await this.showError(error)
    } finally {
      this.auth.profileSaving = false
    }
  }

  private async changePassword(): Promise<void> {
    if (this.auth.passwordBusy) {
      return
    }
    if (this.auth.oldPassword.length === 0 || this.auth.newPassword.length < 8) {
      await promptAction.openToast({ message: '请填写旧密码，新密码至少 8 位。' })
      return
    }
    if (this.auth.newPassword !== this.auth.newPasswordConfirm) {
      await promptAction.openToast({ message: '两次输入的新密码不一致。' })
      return
    }
    this.auth.passwordBusy = true
    try {
      const result: ChangePasswordResult = await AuthApi.changePassword(
        this.connection.apiBaseUrl,
        this.connection.apiKey,
        this.auth.oldPassword,
        this.auth.newPassword
      )
      ApiClient.setSessionToken(result.token)
      await SettingsStore.saveAuthToken(getContext(this), result.token)
      this.auth.currentUser = result.user
      this.auth.clearSensitiveFormFields()
      await promptAction.openToast({ message: '密码已修改，其他旧会话已退出。' })
    } catch (error) {
      await this.showError(error)
    } finally {
      this.auth.passwordBusy = false
    }
  }

  private async loadMembers(): Promise<void> {
    if (!this.auth.authenticated) {
      return
    }
    this.auth.membersLoading = true
    this.auth.membersError = ''
    try {
      this.auth.members = await AuthApi.members(this.connection.apiBaseUrl, this.connection.apiKey)
    } catch (error) {
      this.auth.members = []
      this.auth.membersError = userFacingError(error as Object, '无法加载团队成员，请稍后重试。')
      if (isUnauthorizedError(error as Object)) {
        await this.expireSession()
      }
    } finally {
      this.auth.membersLoading = false
    }
  }

  private async chooseAvatar(): Promise<void> {
    if (this.auth.avatarBusy) {
      return
    }
    try {
      const options = new photoAccessHelper.PhotoSelectOptions()
      options.MIMEType = photoAccessHelper.PhotoViewMIMETypes.IMAGE_TYPE
      options.maxSelectNumber = 1
      const picker = new photoAccessHelper.PhotoViewPicker()
      const selected = await picker.select(options)
      if (selected.photoUris.length === 0) {
        await promptAction.openToast({ message: '模拟器图库为空时，可之后在有图片的设备上传头像。' })
        return
      }
      this.auth.avatarBusy = true
      this.auth.currentUser = await AuthApi.uploadAvatar(this.connection.apiBaseUrl, this.connection.apiKey, selected.photoUris[0])
      await this.loadMembers()
      await promptAction.openToast({ message: '头像已更新。' })
    } catch (error) {
      await this.showError(error)
    } finally {
      this.auth.avatarBusy = false
    }
  }

  private isConfigured(): boolean {
    return this.connection.isConfigured()
  }

  private async requireConfiguration(): Promise<boolean> {
    if (this.connection.apiBaseUrl.trim().length === 0) {
      await promptAction.openToast({ message: '请先在设置页填写服务地址。' })
      this.currentTab = 3
      return false
    }
    if (this.connection.apiKey.trim().length === 0) {
      await promptAction.openToast({ message: '请先在设置页填写 API 密钥。' })
      this.currentTab = 3
      return false
    }
    return true
  }

  private async loadSamples(showError: boolean): Promise<void> {
    if (!this.isConfigured()) {
      return
    }
    this.job.samplesLoading = true
    this.job.samplesError = ''
    try {
      this.job.samples = await JobApi.samples(this.connection.apiBaseUrl, this.connection.apiKey)
      this.connection.markReachable()
    } catch (error) {
      this.job.samplesError = userFacingError(error as Object, '无法加载内置示例，请稍后重试。')
      if (showError) {
        await this.showError(error)
      }
    } finally {
      this.job.samplesLoading = false
    }
  }

  private async chooseImage(): Promise<void> {
    try {
      const options = new photoAccessHelper.PhotoSelectOptions()
      options.MIMEType = photoAccessHelper.PhotoViewMIMETypes.IMAGE_TYPE
      options.maxSelectNumber = 1
      const picker = new photoAccessHelper.PhotoViewPicker()
      const selected = await picker.select(options)
      if (selected.photoUris.length > 0) {
        this.job.imageUri = selected.photoUris[0]
      } else {
        await promptAction.openToast({ message: '模拟器图库为空时，请直接使用上方内置示例。' })
      }
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '选择图片失败，请重新打开图库。') })
    }
  }

  private async submitSample(sample: VisionSample): Promise<void> {
    if (!(await this.requireConfiguration())) {
      return
    }
    const sourceKey: string = `sample:${sample.id}`
    if (!this.job.beginSubmission(sourceKey)) {
      await promptAction.openToast({ message: '该任务已提交或正在提交，请勿重复点击。' })
      return
    }
    try {
      const job: VisionJob = await JobApi.createSampleJob(this.connection.apiBaseUrl, this.connection.apiKey, sample.id)
      this.connection.markReachable()
      await this.activateJob(job, sourceKey)
    } catch (error) {
      await this.showError(error)
    } finally {
      this.job.finishSubmission()
    }
  }

  private async submitImage(): Promise<void> {
    if (this.job.imageUri.length === 0) {
      await promptAction.openToast({ message: '请先选择一张分子结构图片。' })
      return
    }
    if (!(await this.requireConfiguration())) {
      return
    }
    const sourceKey: string = `image:${this.job.imageUri}`
    if (!this.job.beginSubmission(sourceKey)) {
      await promptAction.openToast({ message: '这张图片已有任务正在处理，请勿重复提交。' })
      return
    }
    try {
      const job: VisionJob = await JobApi.createImageJob(this.connection.apiBaseUrl, this.connection.apiKey, this.job.imageUri)
      this.connection.markReachable()
      await this.activateJob(job, sourceKey)
    } catch (error) {
      await this.showError(error)
    } finally {
      this.job.finishSubmission()
    }
  }

  private async activateJob(job: VisionJob, sourceKey: string): Promise<void> {
    this.job.activate(job, sourceKey)
    await SettingsStore.saveActiveJobs(getContext(this), this.job.pendingJobIds())
    await promptAction.openToast({ message: '识别任务已提交，可在状态卡片查看进度。' })
    this.startPolling()
  }

  private startPolling(): void {
    if (!this.auth.authenticated || this.job.pendingCount() === 0) {
      return
    }
    if (this.pollTimer >= 0) {
      return
    }
    // A stable heartbeat is more reliable than recursively replacing
    // setTimeout on HarmonyOS page lifecycle transitions. JobController's
    // nextPollAt still enforces normal intervals and exponential backoff.
    this.pollTimer = setInterval(() => this.pollDueTasks(), 1000)
  }

  private stopPolling(): void {
    if (this.pollTimer >= 0) {
      clearInterval(this.pollTimer)
      this.pollTimer = -1
    }
  }

  private async pollDueTasks(): Promise<void> {
    if (!this.auth.authenticated) {
      return
    }
    const dueJobIds: string[] = this.job.dueJobIds(Date.now())
    await Promise.all(dueJobIds.map((jobId: string) => this.pollTask(jobId)))
    await SettingsStore.saveActiveJobs(getContext(this), this.job.pendingJobIds())
    if (this.job.pendingCount() === 0) {
      this.stopPolling()
    }
  }

  /**
   * App/page resume must reconcile immediately instead of waiting for a stale
   * local timer. beginPolling still prevents duplicate requests.
   */
  private async refreshPendingTasks(): Promise<void> {
    if (!this.auth.authenticated || this.job.pendingCount() === 0) {
      return
    }
    this.stopPolling()
    const pendingIds: string[] = this.job.pendingJobIds()
    await Promise.all(pendingIds.map((jobId: string) => this.pollTask(jobId, true)))
    await SettingsStore.saveActiveJobs(getContext(this), this.job.pendingJobIds())
    if (this.job.pendingCount() > 0) {
      this.startPolling()
    }
  }

  private async pollTask(jobId: string, force: boolean = false): Promise<void> {
    if (!this.job.beginPolling(jobId, Date.now(), force)) {
      return
    }
    try {
      const serverJob: VisionJob = await JobApi.getJob(this.connection.apiBaseUrl, this.connection.apiKey, jobId)
      this.connection.markReachable()
      this.job.applyServerJob(serverJob)
      if (serverJob.status === 'completed') {
        const analysisId: string = serverJob.result === undefined ? serverJob.analysisId : serverJob.result.analysisId
        this.job.completionAnalysisId = analysisId
        this.job.completionMessage = '分子结构识别已完成'
      } else if (serverJob.status === 'failed') {
        await promptAction.openToast({ message: serverJob.message })
      }
    } catch (error) {
      if (isUnauthorizedError(error as Object)) {
        await this.expireSession()
      } else if (error instanceof ApiError && error.statusCode === 404) {
        this.job.markUnavailable(jobId, '任务不存在或已被电脑端清理。')
      } else {
        if (isTransportError(error as Object)) {
          this.connection.markTransportFailure()
          this.job.markNetworkFailure(jobId, '网络暂时不可用', Date.now())
        } else {
          this.job.markNetworkFailure(jobId, '服务暂时无法查询任务', Date.now())
        }
      }
    } finally {
      this.job.finishPolling(jobId)
    }
  }

  private async analyzeSmiles(): Promise<void> {
    if (this.job.smilesInput.trim().length === 0) {
      await promptAction.openToast({ message: '请输入一条 SMILES。' })
      return
    }
    if (this.job.smilesAnalyzing || !(await this.requireConfiguration())) {
      return
    }
    this.job.smilesAnalyzing = true
    try {
      const result: VisionAnalysisResult =
        await AnalysisApi.analyzeSmiles(this.connection.apiBaseUrl, this.connection.apiKey, this.job.smilesInput.trim())
      this.connection.markReachable()
      await this.openResult(result.analysisId)
    } catch (error) {
      await this.showError(error)
    } finally {
      this.job.smilesAnalyzing = false
    }
  }

  private async openResult(analysisId: string): Promise<void> {
    await router.pushUrl({
      url: 'pages/ResultDetail',
      params: { analysisId: analysisId }
    })
  }

  private async openKnowledgeGuide(): Promise<void> {
    await router.pushUrl({
      url: 'pages/KnowledgeGuide'
    })
  }

  private async openDocumentWorkspace(): Promise<void> {
    await router.pushUrl({
      url: 'pages/DocumentWorkspace'
    })
  }

  private async loadHistory(reset: boolean, keepItems: boolean = false): Promise<void> {
    if (!(await this.requireConfiguration())) {
      return
    }
    const requestSerial = this.history.beginRequest(reset, keepItems)
    if (requestSerial === 0) {
      return
    }
    const offset = reset ? 0 : this.history.offset
    const query = this.history.query
    const status = this.history.status
    const favoritesOnly = this.history.favoritesOnly
    const scope = this.history.scope
    const ownerUserId = this.history.ownerUserId
    try {
      const page: HistoryPage = await AnalysisApi.analyses(
        this.connection.apiBaseUrl,
        this.connection.apiKey,
        query,
        status,
        favoritesOnly,
        offset,
        scope,
        ownerUserId
      )
      this.connection.markReachable()
      this.history.applyPage(requestSerial, page, reset)
    } catch (error) {
      const message: string = userFacingError(error as Object, '历史加载失败，请稍后重试。')
      if (this.history.failRequest(requestSerial, message)) {
        await this.showError(error)
      }
    } finally {
      this.history.finishRequest(requestSerial)
    }
  }

  private changeHistoryFilters(scope: string, ownerUserId: string, status: string, favoritesOnly: boolean): void {
    if (this.history.scope === scope && this.history.ownerUserId === ownerUserId &&
      this.history.status === status && this.history.favoritesOnly === favoritesOnly) {
      return
    }
    this.history.updateFilters(scope, ownerUserId, status, favoritesOnly)
    setTimeout(() => this.loadHistory(true), 0)
  }

  private clearHistoryFilters(): void {
    this.history.query = ''
    this.history.updateFilters('all', '', 'all', false)
    setTimeout(() => this.loadHistory(true), 0)
  }

  private async toggleFavorite(item: HistoryItem): Promise<void> {
    try {
      await AnalysisApi.favorite(this.connection.apiBaseUrl, this.connection.apiKey, item.analysisId, !item.isFavorite)
      await this.loadHistory(true, true)
    } catch (error) {
      await this.showError(error)
    }
  }

  private confirmDelete(item: HistoryItem): void {
    AlertDialog.show({
      title: '删除历史索引',
      message: '仅从手机历史列表移除，电脑上的报告和图片文件会保留。',
      primaryButton: {
        value: '取消',
        action: () => {
        }
      },
      secondaryButton: {
        value: '删除',
        fontColor: $r('app.color.danger'),
        action: () => this.deleteHistory(item)
      }
    })
  }

  private async deleteHistory(item: HistoryItem): Promise<void> {
    try {
      await AnalysisApi.deleteAnalysis(this.connection.apiBaseUrl, this.connection.apiKey, item.analysisId)
      await this.loadHistory(true)
      await promptAction.openToast({ message: '历史索引已删除，电脑端文件已保留。' })
    } catch (error) {
      await this.showError(error)
    }
  }

  private async saveSettings(): Promise<void> {
    const validation: FormValidation = validateConnectionForm(this.connection.apiBaseUrl, this.connection.apiKey)
    if (!validation.valid) {
      await promptAction.openToast({ message: validation.message })
      return
    }
    this.connection.savingSettings = true
    try {
      await SettingsStore.saveConnection(getContext(this), this.connection.apiBaseUrl, this.connection.apiKey)
      await promptAction.openToast({ message: '设置已保存。' })
      await this.checkService()
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '保存设置失败，请稍后重试。') })
    } finally {
      this.connection.savingSettings = false
    }
  }

  private async checkService(): Promise<void> {
    if (!(await this.requireConfiguration())) {
      return
    }
    this.connection.checking = true
    try {
      const health: HealthResult = await JobApi.health(this.connection.apiBaseUrl, this.connection.apiKey)
      // Health validates the address and API key only. Verify the bearer
      // session as well so "connected" means recognition endpoints are usable.
      if (this.auth.authenticated) {
        this.auth.currentUser = await AuthApi.me(this.connection.apiBaseUrl, this.connection.apiKey)
        this.auth.syncProfileFields()
      }
      this.connection.markConnected(health.appMode, health.backend)
      await this.loadSamples(false)
      await promptAction.openToast({ message: '本地视觉服务连接成功。' })
    } catch (error) {
      this.connection.serviceSummary = '连接失败'
      await this.showError(error)
    } finally {
      this.connection.checking = false
    }
  }

  private onTabChanged(index: number): void {
    this.currentTab = index
    if (this.job.pendingCount() > 0) {
      this.startPolling()
    }
    if (index === 2) {
      this.loadHistory(true, this.history.items.length > 0)
    }
  }

  @Builder
  private recognitionPage() {
    RecognitionView({
      currentUser: this.auth.currentUser,
      tasks: this.job.tasks,
      samples: this.job.samples,
      samplesLoading: this.job.samplesLoading,
      samplesError: this.job.samplesError,
      serviceSummary: this.connection.serviceSummary,
      submitting: this.job.submitting,
      imageUri: this.job.imageUri,
      onOpenSettings: (): void => { this.currentTab = 3 },
      onOpenGuide: (): void => { this.openKnowledgeGuide() },
      onOpenDocuments: (): void => { this.openDocumentWorkspace() },
      onLoadSamples: (): void => { this.loadSamples(true) },
      onSubmitSample: (sample: VisionSample): void => { this.submitSample(sample) },
      onChooseImage: (): void => { this.chooseImage() },
      onSubmitImage: (): void => { this.submitImage() },
      onOpenResult: (analysisId: string): void => { this.openResult(analysisId) }
    })
  }

  @Builder
  private smilesPage() {
    SmilesView({
      currentUser: this.auth.currentUser,
      smilesInput: this.job.smilesInput,
      analyzing: this.job.smilesAnalyzing,
      onOpenSettings: (): void => { this.currentTab = 3 },
      onOpenGuide: (): void => { this.openKnowledgeGuide() },
      onSmilesChanged: (value: string): void => { this.job.smilesInput = value },
      onAnalyze: (): void => { this.analyzeSmiles() }
    })
  }

  @Builder
  private historyPage() {
    HistoryView({
      currentUser: this.auth.currentUser,
      members: this.auth.members,
      items: this.history.items,
      query: this.history.query,
      status: this.history.status,
      favoritesOnly: this.history.favoritesOnly,
      scope: this.history.scope,
      ownerUserId: this.history.ownerUserId,
      loading: this.history.loading,
      refreshing: this.history.refreshing,
      loadingMore: this.history.loadingMore,
      errorMessage: this.history.error,
      hasMore: this.history.hasMore,
      onOpenSettings: (): void => { this.currentTab = 3 },
      onOpenGuide: (): void => { this.openKnowledgeGuide() },
      onQueryChanged: (value: string): void => { this.history.updateQuery(value) },
      onReload: (reset: boolean): void => { this.loadHistory(reset) },
      onRefresh: (): void => { this.loadHistory(true, true) },
      onClearFilters: (): void => { this.clearHistoryFilters() },
      onFiltersChanged: (scope: string, ownerUserId: string, status: string, favoritesOnly: boolean): void => {
        this.changeHistoryFilters(scope, ownerUserId, status, favoritesOnly)
      },
      onToggleFavorite: (item: HistoryItem): void => { this.toggleFavorite(item) },
      onOpenResult: (analysisId: string): void => { this.openResult(analysisId) },
      onDelete: (item: HistoryItem): void => { this.confirmDelete(item) }
    })
  }

  @Builder
  private settingsPage() {
    SettingsView({
      currentUser: this.auth.currentUser,
      members: this.auth.members,
      membersLoading: this.auth.membersLoading,
      membersError: this.auth.membersError,
      displayName: this.auth.profileDisplayName,
      role: this.auth.profileRole,
      oldPassword: this.auth.oldPassword,
      newPassword: this.auth.newPassword,
      newPasswordConfirm: this.auth.newPasswordConfirm,
      apiBaseUrl: this.connection.apiBaseUrl,
      apiKey: this.connection.apiKey,
      avatarBusy: this.auth.avatarBusy,
      profileSaving: this.auth.profileSaving,
      passwordBusy: this.auth.passwordBusy,
      savingSettings: this.connection.savingSettings,
      checking: this.connection.checking,
      serviceSummary: this.connection.serviceSummary,
      onOpenGuide: (): void => { this.openKnowledgeGuide() },
      onDisplayNameChanged: (value: string): void => { this.auth.profileDisplayName = value },
      onRoleChanged: (value: string): void => { this.auth.profileRole = value },
      onOldPasswordChanged: (value: string): void => { this.auth.oldPassword = value },
      onNewPasswordChanged: (value: string): void => { this.auth.newPassword = value },
      onNewPasswordConfirmChanged: (value: string): void => { this.auth.newPasswordConfirm = value },
      onApiBaseUrlChanged: (value: string): void => { this.connection.apiBaseUrl = value },
      onApiKeyChanged: (value: string): void => { this.connection.apiKey = value },
      onChooseAvatar: (): void => { this.chooseAvatar() },
      onSaveProfile: (): void => { this.saveProfile() },
      onChangePassword: (): void => { this.changePassword() },
      onSaveSettings: (): void => { this.saveSettings() },
      onCheckService: (): void => { this.checkService() },
      onLogout: (): void => { this.logout() }
    })
  }
  build() {
    if (!this.auth.authReady) {
      Column({ space: 12 }) {
        Text('⌬').fontSize(52).fontColor($r('app.color.primary'))
        LoadingProgress().width(36).height(36).color($r('app.color.primary'))
        Text('正在连接分子工作台…').fontSize(14).fontColor($r('app.color.auth_text_secondary'))
      }
      .justifyContent(FlexAlign.Center)
      .width('100%')
      .height('100%')
      .backgroundColor($r('app.color.auth_background'))
    } else if (!this.auth.authenticated) {
      AuthView({
        authMode: this.auth.authMode,
        username: this.auth.authUsername,
        displayName: this.auth.authDisplayName,
        role: this.auth.authRole,
        password: this.auth.authPassword,
        passwordConfirm: this.auth.authPasswordConfirm,
        apiBaseUrl: this.connection.apiBaseUrl,
        apiKey: this.connection.apiKey,
        busy: this.auth.authBusy,
        errorMessage: this.auth.authError,
        onAuthModeChanged: (value: string): void => { this.auth.authMode = value },
        onUsernameChanged: (value: string): void => { this.auth.authUsername = value },
        onDisplayNameChanged: (value: string): void => { this.auth.authDisplayName = value },
        onRoleChanged: (value: string): void => { this.auth.authRole = value },
        onPasswordChanged: (value: string): void => { this.auth.authPassword = value },
        onPasswordConfirmChanged: (value: string): void => { this.auth.authPasswordConfirm = value },
        onApiBaseUrlChanged: (value: string): void => { this.connection.apiBaseUrl = value },
        onApiKeyChanged: (value: string): void => { this.connection.apiKey = value },
        onSubmit: () => this.submitAuth()
      })
    } else {
      Stack({ alignContent: Alignment.Top }) {
        AppShell({
          currentTab: $currentTab,
          recognition: (): void => this.recognitionPage(),
          smiles: (): void => this.smilesPage(),
          history: (): void => this.historyPage(),
          settings: (): void => this.settingsPage(),
          onTabChanged: (index: number) => this.onTabChanged(index)
        })

        if (this.job.completionAnalysisId.length > 0) {
          CompletionBanner({
            message: this.job.completionMessage,
            onView: () => {
              const analysisId = this.job.completionAnalysisId
              this.job.completionAnalysisId = ''
              this.openResult(analysisId)
            },
            onDismiss: () => this.job.completionAnalysisId = ''
          })
            .margin({ left: 12, right: 12, top: 10 })
        }
      }
      .width('100%')
      .height('100%')
    }
  }
}
