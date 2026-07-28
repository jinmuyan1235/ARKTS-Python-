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
  VisionSample,
  UserProfile
} from '../model/VisionResult'
import { DEFAULT_API_BASE_URL, SettingsStore, StoredSettings } from '../services/SettingsStore'
import { ApiError, VisionApi } from '../services/VisionApi'
import {
  FormValidation,
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

@Entry
@Component
struct Index {
  @State currentTab: number = 0
  @State apiBaseUrl: string = DEFAULT_API_BASE_URL
  @State apiKey: string = ''
  @State imageUri: string = ''
  @State samples: VisionSample[] = []
  @State samplesLoading: boolean = false
  @State samplesError: string = ''
  @State activeJobId: string = ''
  @State jobStatus: string = ''
  @State jobMessage: string = ''
  @State jobCreatedAt: string = ''
  @State submitting: boolean = false
  @State polling: boolean = false
  @State smilesInput: string = ''
  @State smilesAnalyzing: boolean = false
  @State historyItems: HistoryItem[] = []
  @State historyQuery: string = ''
  @State historyStatus: string = 'all'
  @State favoritesOnly: boolean = false
  @State historyScope: string = 'all'
  @State historyOwnerUserId: string = ''
  @State historyOffset: number = 0
  @State historyHasMore: boolean = false
  @State historyLoading: boolean = false
  @State historyError: string = ''
  private historyRequestSerial: number = 0
  @State checking: boolean = false
  @State savingSettings: boolean = false
  @State serviceSummary: string = '尚未检测服务'
  @State authReady: boolean = false
  @State authenticated: boolean = false
  @State authMode: string = 'login'
  @State authUsername: string = ''
  @State authDisplayName: string = ''
  @State authRole: string = ''
  @State authPassword: string = ''
  @State authPasswordConfirm: string = ''
  @State authBusy: boolean = false
  @State authError: string = ''
  @State currentUser: UserProfile | undefined = undefined
  @State members: UserProfile[] = []
  @State membersLoading: boolean = false
  @State membersError: string = ''
  @State avatarBusy: boolean = false
  @State profileDisplayName: string = ''
  @State profileRole: string = ''
  @State profileSaving: boolean = false
  @State oldPassword: string = ''
  @State newPassword: string = ''
  @State newPasswordConfirm: string = ''
  @State passwordBusy: boolean = false
  @State completionAnalysisId: string = ''
  @State completionMessage: string = ''
  private pollTimer: number = -1

  aboutToAppear(): void {
    this.initialize()
  }

  aboutToDisappear(): void {
    this.stopPolling()
  }

  onPageShow(): void {
    if (this.authenticated && this.activeJobId.length > 0) {
      this.pollActiveJob(true)
    }
  }

  private async initialize(): Promise<void> {
    try {
      const settings: StoredSettings = await SettingsStore.load(getContext(this))
      this.apiBaseUrl = settings.apiBaseUrl
      this.apiKey = settings.apiKey
      this.activeJobId = settings.activeJobId
      VisionApi.setSessionToken(settings.authToken)
      if (this.isConfigured() && settings.authToken.length > 0) {
        try {
          this.currentUser = await VisionApi.me(this.apiBaseUrl, this.apiKey)
          this.syncProfileFields()
          this.authenticated = true
          await this.loadSamples(false)
          await this.loadMembers()
          if (this.activeJobId.length > 0 && this.currentTab === 0) {
            this.startPolling()
          }
        } catch (error) {
          VisionApi.setSessionToken('')
          await SettingsStore.saveAuthToken(getContext(this), '')
        }
      }
    } catch (error) {
      await promptAction.openToast({ message: `读取本地配置失败：${error}` })
    } finally {
      this.authReady = true
    }
  }

  private async submitAuth(): Promise<void> {
    if (this.authBusy) {
      return
    }
    const connectionValidation: FormValidation = validateConnectionForm(this.apiBaseUrl, this.apiKey)
    if (!connectionValidation.valid) {
      await promptAction.openToast({ message: connectionValidation.message })
      return
    }
    const authValidation: FormValidation = this.authMode === 'register' ?
      validateRegistrationForm(this.authUsername, this.authDisplayName, this.authPassword) :
      validateLoginForm(this.authUsername, this.authPassword)
    if (!authValidation.valid) {
      await promptAction.openToast({ message: authValidation.message })
      return
    }
    if (this.authMode === 'register') {
      if (this.authDisplayName.trim().length === 0) {
        await promptAction.openToast({ message: '请填写组员显示名称。' })
        return
      }
      if (this.authPassword !== this.authPasswordConfirm) {
        await promptAction.openToast({ message: '两次输入的密码不一致。' })
        return
      }
    }
    this.authBusy = true
    this.authError = ''
    try {
      await SettingsStore.saveConnection(getContext(this), this.apiBaseUrl, this.apiKey)
      VisionApi.setSessionToken('')
      let auth: AuthResult
      if (this.authMode === 'register') {
        auth = await VisionApi.register(
          this.apiBaseUrl,
          this.apiKey,
          this.authUsername.trim(),
          this.authDisplayName.trim(),
          this.authPassword,
          this.authRole.trim()
        )
      } else {
        auth = await VisionApi.login(
          this.apiBaseUrl,
          this.apiKey,
          this.authUsername.trim(),
          this.authPassword
        )
      }
      VisionApi.setSessionToken(auth.token)
      await SettingsStore.saveAuthToken(getContext(this), auth.token)
      this.currentUser = auth.user
      this.syncProfileFields()
      this.authenticated = true
      this.authPassword = ''
      this.authPasswordConfirm = ''
      await this.loadSamples(false)
      await this.loadMembers()
      if (this.activeJobId.length > 0) {
        this.startPolling()
      }
    } catch (error) {
      VisionApi.setSessionToken('')
      this.authError = `${error}`
    } finally {
      this.authBusy = false
    }
  }

  private async logout(): Promise<void> {
    try {
      await VisionApi.logout(this.apiBaseUrl, this.apiKey)
    } catch (error) {
    }
    this.stopPolling()
    VisionApi.setSessionToken('')
    await SettingsStore.saveAuthToken(getContext(this), '')
    this.authenticated = false
    this.currentUser = undefined
    this.members = []
    this.currentTab = 0
  }

  private async expireSession(): Promise<void> {
    this.stopPolling()
    VisionApi.setSessionToken('')
    await SettingsStore.saveAuthToken(getContext(this), '')
    this.authenticated = false
    this.currentUser = undefined
    this.members = []
    this.currentTab = 0
    await promptAction.openToast({ message: '登录已失效，请重新登录。' })
  }

  private async showError(error: Object): Promise<void> {
    if (error instanceof ApiError && error.statusCode === 401 && this.authenticated) {
      await this.expireSession()
      return
    }
    await promptAction.openToast({ message: `${error}` })
  }

  private syncProfileFields(): void {
    if (this.currentUser !== undefined) {
      this.profileDisplayName = this.currentUser.displayName
      this.profileRole = this.currentUser.role
    }
  }

  private async saveProfile(): Promise<void> {
    if (this.profileSaving || this.profileDisplayName.trim().length === 0) {
      return
    }
    this.profileSaving = true
    try {
      this.currentUser = await VisionApi.updateProfile(
        this.apiBaseUrl,
        this.apiKey,
        this.profileDisplayName,
        this.profileRole
      )
      this.syncProfileFields()
      await this.loadMembers()
      await promptAction.openToast({ message: '个人资料已保存。' })
    } catch (error) {
      await this.showError(error)
    } finally {
      this.profileSaving = false
    }
  }

  private async changePassword(): Promise<void> {
    if (this.passwordBusy) {
      return
    }
    if (this.oldPassword.length === 0 || this.newPassword.length < 8) {
      await promptAction.openToast({ message: '请填写旧密码，新密码至少 8 位。' })
      return
    }
    if (this.newPassword !== this.newPasswordConfirm) {
      await promptAction.openToast({ message: '两次输入的新密码不一致。' })
      return
    }
    this.passwordBusy = true
    try {
      const result: ChangePasswordResult = await VisionApi.changePassword(
        this.apiBaseUrl,
        this.apiKey,
        this.oldPassword,
        this.newPassword
      )
      VisionApi.setSessionToken(result.token)
      await SettingsStore.saveAuthToken(getContext(this), result.token)
      this.currentUser = result.user
      this.oldPassword = ''
      this.newPassword = ''
      this.newPasswordConfirm = ''
      await promptAction.openToast({ message: '密码已修改，其他旧会话已退出。' })
    } catch (error) {
      await this.showError(error)
    } finally {
      this.passwordBusy = false
    }
  }

  private async loadMembers(): Promise<void> {
    if (!this.authenticated) {
      return
    }
    this.membersLoading = true
    this.membersError = ''
    try {
      this.members = await VisionApi.members(this.apiBaseUrl, this.apiKey)
    } catch (error) {
      this.members = []
      this.membersError = `${error}`
      if (error instanceof ApiError && error.statusCode === 401) {
        await this.expireSession()
      }
    } finally {
      this.membersLoading = false
    }
  }

  private async chooseAvatar(): Promise<void> {
    if (this.avatarBusy) {
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
      this.avatarBusy = true
      this.currentUser = await VisionApi.uploadAvatar(this.apiBaseUrl, this.apiKey, selected.photoUris[0])
      await this.loadMembers()
      await promptAction.openToast({ message: '头像已更新。' })
    } catch (error) {
      await this.showError(error)
    } finally {
      this.avatarBusy = false
    }
  }

  private userInitial(user: UserProfile | undefined): string {
    if (user === undefined || user.displayName.length === 0) {
      return 'M'
    }
    return user.displayName.substring(0, 1).toUpperCase()
  }

  private isConfigured(): boolean {
    return this.apiBaseUrl.trim().length > 0 && this.apiKey.trim().length > 0
  }

  private async requireConfiguration(): Promise<boolean> {
    if (this.apiBaseUrl.trim().length === 0) {
      await promptAction.openToast({ message: '请先在设置页填写服务地址。' })
      this.currentTab = 3
      return false
    }
    if (this.apiKey.trim().length === 0) {
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
    this.samplesLoading = true
    this.samplesError = ''
    try {
      this.samples = await VisionApi.samples(this.apiBaseUrl, this.apiKey)
    } catch (error) {
      this.samplesError = `${error}`
      if (showError) {
        await this.showError(error)
      }
    } finally {
      this.samplesLoading = false
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
        this.imageUri = selected.photoUris[0]
      } else {
        await promptAction.openToast({ message: '模拟器图库为空时，请直接使用上方内置示例。' })
      }
    } catch (error) {
      await promptAction.openToast({ message: `选择图片失败：${error}` })
    }
  }

  private async submitSample(sample: VisionSample): Promise<void> {
    if (this.activeJobId.length > 0) {
      await promptAction.openToast({ message: '已有识别任务正在处理，请等待当前任务完成。' })
      return
    }
    if (this.submitting || !(await this.requireConfiguration())) {
      return
    }
    this.submitting = true
    try {
      const job: VisionJob = await VisionApi.createSampleJob(this.apiBaseUrl, this.apiKey, sample.id)
      await this.activateJob(job)
    } catch (error) {
      await this.showError(error)
    } finally {
      this.submitting = false
    }
  }

  private async submitImage(): Promise<void> {
    if (this.imageUri.length === 0) {
      await promptAction.openToast({ message: '请先选择一张分子结构图片。' })
      return
    }
    if (this.activeJobId.length > 0) {
      await promptAction.openToast({ message: '已有识别任务正在处理，请等待当前任务完成。' })
      return
    }
    if (this.submitting || !(await this.requireConfiguration())) {
      return
    }
    this.submitting = true
    try {
      const job: VisionJob = await VisionApi.createImageJob(this.apiBaseUrl, this.apiKey, this.imageUri)
      await this.activateJob(job)
    } catch (error) {
      await this.showError(error)
    } finally {
      this.submitting = false
    }
  }

  private async activateJob(job: VisionJob): Promise<void> {
    this.activeJobId = job.jobId
    this.jobStatus = job.status
    this.jobMessage = job.message
    this.jobCreatedAt = job.createdAt
    await SettingsStore.saveActiveJob(getContext(this), job.jobId)
    await promptAction.openToast({ message: '识别任务已提交，可在状态卡片查看进度。' })
    this.startPolling()
  }

  private startPolling(): void {
    this.stopPolling()
    if (this.currentTab !== 0 || this.activeJobId.length === 0) {
      return
    }
    this.pollActiveJob()
    this.pollTimer = setInterval(() => this.pollActiveJob(), 2000)
  }

  private stopPolling(): void {
    if (this.pollTimer >= 0) {
      clearInterval(this.pollTimer)
      this.pollTimer = -1
    }
  }

  private async pollActiveJob(ignoreTab: boolean = false): Promise<void> {
    if (this.polling || this.activeJobId.length === 0 || (!ignoreTab && this.currentTab !== 0)) {
      return
    }
    this.polling = true
    try {
      const job: VisionJob = await VisionApi.getJob(this.apiBaseUrl, this.apiKey, this.activeJobId)
      this.jobStatus = job.status
      this.jobMessage = job.message
      this.jobCreatedAt = job.createdAt
      if (job.status === 'completed' && job.result !== undefined) {
        this.stopPolling()
        this.activeJobId = ''
        await SettingsStore.saveActiveJob(getContext(this), '')
        this.completionAnalysisId = job.result.analysisId
        this.completionMessage = '分子结构识别已完成'
      } else if (job.status === 'failed') {
        this.stopPolling()
        this.activeJobId = ''
        await SettingsStore.saveActiveJob(getContext(this), '')
        await promptAction.openToast({ message: job.message })
      }
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 401) {
        await this.expireSession()
      } else {
        this.jobMessage = `暂时无法读取任务：${error}`
      }
    } finally {
      this.polling = false
    }
  }

  private async analyzeSmiles(): Promise<void> {
    if (this.smilesInput.trim().length === 0) {
      await promptAction.openToast({ message: '请输入一条 SMILES。' })
      return
    }
    if (this.smilesAnalyzing || !(await this.requireConfiguration())) {
      return
    }
    this.smilesAnalyzing = true
    try {
      const result: VisionAnalysisResult =
        await VisionApi.analyzeSmiles(this.apiBaseUrl, this.apiKey, this.smilesInput.trim())
      await this.openResult(result.analysisId)
    } catch (error) {
      await this.showError(error)
    } finally {
      this.smilesAnalyzing = false
    }
  }

  private async openResult(analysisId: string): Promise<void> {
    await router.pushUrl({
      url: 'pages/ResultDetail',
      params: { analysisId: analysisId }
    })
  }

  private async loadHistory(reset: boolean): Promise<void> {
    if (!(await this.requireConfiguration())) {
      return
    }
    const requestSerial = ++this.historyRequestSerial
    this.historyLoading = true
    this.historyError = ''
    const offset = reset ? 0 : this.historyOffset
    const query = this.historyQuery
    const status = this.historyStatus
    const favoritesOnly = this.favoritesOnly
    const scope = this.historyScope
    const ownerUserId = this.historyOwnerUserId
    try {
      const page: HistoryPage = await VisionApi.analyses(
        this.apiBaseUrl,
        this.apiKey,
        query,
        status,
        favoritesOnly,
        offset,
        scope,
        ownerUserId
      )
      if (requestSerial !== this.historyRequestSerial) {
        return
      }
      this.historyItems = reset ? page.items : this.historyItems.concat(page.items)
      this.historyOffset = offset + page.items.length
      this.historyHasMore = page.hasMore
    } catch (error) {
      if (requestSerial !== this.historyRequestSerial) {
        return
      }
      this.historyError = `${error}`
      await this.showError(error)
    } finally {
      if (requestSerial === this.historyRequestSerial) {
        this.historyLoading = false
      }
    }
  }

  private changeHistoryFilters(scope: string, ownerUserId: string, status: string, favoritesOnly: boolean): void {
    if (this.historyScope === scope && this.historyOwnerUserId === ownerUserId &&
      this.historyStatus === status && this.favoritesOnly === favoritesOnly) {
      return
    }
    this.historyScope = scope
    this.historyOwnerUserId = ownerUserId
    this.historyStatus = status
    this.favoritesOnly = favoritesOnly
    this.historyItems = []
    this.historyOffset = 0
    this.historyHasMore = false
    this.historyError = ''
    setTimeout(() => this.loadHistory(true), 0)
  }

  private async toggleFavorite(item: HistoryItem): Promise<void> {
    try {
      await VisionApi.favorite(this.apiBaseUrl, this.apiKey, item.analysisId, !item.isFavorite)
      await this.loadHistory(true)
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
      await VisionApi.deleteAnalysis(this.apiBaseUrl, this.apiKey, item.analysisId)
      await this.loadHistory(true)
      await promptAction.openToast({ message: '历史索引已删除，电脑端文件已保留。' })
    } catch (error) {
      await this.showError(error)
    }
  }

  private async saveSettings(): Promise<void> {
    const validation: FormValidation = validateConnectionForm(this.apiBaseUrl, this.apiKey)
    if (!validation.valid) {
      await promptAction.openToast({ message: validation.message })
      return
    }
    this.savingSettings = true
    try {
      await SettingsStore.saveConnection(getContext(this), this.apiBaseUrl, this.apiKey)
      await promptAction.openToast({ message: '设置已保存。' })
      await this.checkService()
    } catch (error) {
      await promptAction.openToast({ message: `保存设置失败：${error}` })
    } finally {
      this.savingSettings = false
    }
  }

  private async checkService(): Promise<void> {
    if (!(await this.requireConfiguration())) {
      return
    }
    this.checking = true
    try {
      const health: HealthResult = await VisionApi.health(this.apiBaseUrl, this.apiKey)
      this.serviceSummary = `已连接 · ${health.appMode} · ${health.backend}`
      await this.loadSamples(false)
      await promptAction.openToast({ message: '本地视觉服务连接成功。' })
    } catch (error) {
      this.serviceSummary = '连接失败'
      await this.showError(error)
    } finally {
      this.checking = false
    }
  }

  private onTabChanged(index: number): void {
    this.currentTab = index
    if (index === 0) {
      this.startPolling()
    } else {
      this.stopPolling()
    }
    if (index === 2) {
      this.loadHistory(true)
    }
  }

  @Builder
  private recognitionPage() {
    RecognitionView({
      currentUser: this.currentUser,
      activeJobId: this.activeJobId,
      jobStatus: this.jobStatus,
      jobMessage: this.jobMessage,
      jobCreatedAt: this.jobCreatedAt,
      samples: this.samples,
      samplesLoading: this.samplesLoading,
      samplesError: this.samplesError,
      serviceSummary: this.serviceSummary,
      submitting: this.submitting,
      imageUri: this.imageUri,
      onOpenSettings: (): void => { this.currentTab = 3 },
      onLoadSamples: (): void => { this.loadSamples(true) },
      onSubmitSample: (sample: VisionSample): void => { this.submitSample(sample) },
      onChooseImage: (): void => { this.chooseImage() },
      onSubmitImage: (): void => { this.submitImage() }
    })
  }

  @Builder
  private smilesPage() {
    SmilesView({
      currentUser: this.currentUser,
      smilesInput: $smilesInput,
      analyzing: this.smilesAnalyzing,
      onOpenSettings: (): void => { this.currentTab = 3 },
      onAnalyze: (): void => { this.analyzeSmiles() }
    })
  }

  @Builder
  private historyPage() {
    HistoryView({
      currentUser: this.currentUser,
      members: this.members,
      items: this.historyItems,
      query: $historyQuery,
      status: this.historyStatus,
      favoritesOnly: this.favoritesOnly,
      scope: this.historyScope,
      ownerUserId: this.historyOwnerUserId,
      loading: this.historyLoading,
      errorMessage: this.historyError,
      hasMore: this.historyHasMore,
      onOpenSettings: (): void => { this.currentTab = 3 },
      onReload: (reset: boolean): void => { this.loadHistory(reset) },
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
      currentUser: this.currentUser,
      members: this.members,
      membersLoading: this.membersLoading,
      membersError: this.membersError,
      displayName: $profileDisplayName,
      role: $profileRole,
      oldPassword: $oldPassword,
      newPassword: $newPassword,
      newPasswordConfirm: $newPasswordConfirm,
      apiBaseUrl: $apiBaseUrl,
      apiKey: $apiKey,
      avatarBusy: this.avatarBusy,
      profileSaving: this.profileSaving,
      passwordBusy: this.passwordBusy,
      savingSettings: this.savingSettings,
      checking: this.checking,
      serviceSummary: this.serviceSummary,
      onChooseAvatar: (): void => { this.chooseAvatar() },
      onSaveProfile: (): void => { this.saveProfile() },
      onChangePassword: (): void => { this.changePassword() },
      onSaveSettings: (): void => { this.saveSettings() },
      onCheckService: (): void => { this.checkService() },
      onLogout: (): void => { this.logout() }
    })
  }
  build() {
    if (!this.authReady) {
      Column({ space: 12 }) {
        Text('⌬').fontSize(52).fontColor($r('app.color.primary'))
        LoadingProgress().width(36).height(36).color($r('app.color.primary'))
        Text('正在连接分子工作台…').fontSize(14).fontColor($r('app.color.auth_text_secondary'))
      }
      .justifyContent(FlexAlign.Center)
      .width('100%')
      .height('100%')
      .backgroundColor($r('app.color.auth_background'))
    } else if (!this.authenticated) {
      AuthView({
        authMode: $authMode,
        username: $authUsername,
        displayName: $authDisplayName,
        role: $authRole,
        password: $authPassword,
        passwordConfirm: $authPasswordConfirm,
        apiBaseUrl: $apiBaseUrl,
        apiKey: $apiKey,
        busy: this.authBusy,
        errorMessage: this.authError,
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

        if (this.completionAnalysisId.length > 0) {
          CompletionBanner({
            message: this.completionMessage,
            onView: () => {
              const analysisId = this.completionAnalysisId
              this.completionAnalysisId = ''
              this.openResult(analysisId)
            },
            onDismiss: () => this.completionAnalysisId = ''
          })
            .margin({ left: 12, right: 12, top: 10 })
        }
      }
      .width('100%')
      .height('100%')
    }
  }
}
