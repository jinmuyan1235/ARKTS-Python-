import photoAccessHelper from '@ohos.file.photoAccessHelper'
import promptAction from '@ohos.promptAction'
import router from '@ohos.router'
import { common } from '@kit.AbilityKit'
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
import { BatchJob } from '../model/BatchResult'
import { SettingsStore, StoredSettings } from '../services/SettingsStore'
import { ApiClient, ApiError, isTransportError, isUnauthorizedError } from '../services/ApiClient'
import { AuthApi } from '../services/AuthApi'
import { JobApi } from '../services/JobApi'
import { BatchApi } from '../services/BatchApi'
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
import { BatchView } from '../components/BatchView'
import { SmilesView } from '../components/SmilesView'
import { HistoryView } from '../components/HistoryView'
import { SettingsView } from '../components/SettingsView'
import { AuthController } from '../controllers/AuthController'
import { ConnectionController } from '../controllers/ConnectionController'
import { JobController } from '../controllers/JobController'
import { BatchController, MAX_MOBILE_BATCH_IMAGES } from '../controllers/BatchController'
import { HistoryController } from '../controllers/HistoryController'
import { BuiltInTestFiles, BuiltInTestSet } from '../services/BuiltInTestSet'
import { SecureStore } from '../services/SecureStore'
import { showDangerConfirmation } from '../services/ConfirmAction'
import { ImageEditService } from '../services/ImageEditService'
import { ImageEditSpec, isUnchangedImageEdit } from '../services/ImageEditGeometry'

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
  @State batch: BatchController = new BatchController()
  @State history: HistoryController = new HistoryController()
  @State serviceHealth: HealthResult | undefined = undefined
  @State builtInImages: string[] = []
  @State imageEditing: boolean = false
  private pollTimer: number = -1
  private batchPollTimer: number = -1

  aboutToAppear(): void {
    this.initialize()
  }

  aboutToDisappear(): void {
    this.stopPolling()
    this.stopBatchPolling()
  }

  onPageShow(): void {
    if (this.auth.authenticated && this.job.pendingCount() > 0) {
      this.refreshPendingTasks()
    }
    if (this.auth.authenticated && this.batch.pendingJobId().length > 0) {
      this.refreshBatchJob()
    }
    if (this.auth.authenticated && this.currentTab === 3) {
      this.loadHistory(true, true)
    }
  }

  private async initialize(): Promise<void> {
    try {
      try {
        const builtIn: BuiltInTestFiles = await BuiltInTestSet.prepare(
          getContext(this) as common.UIAbilityContext)
        this.builtInImages = builtIn.images
      } catch (_) {
        this.builtInImages = []
      }
      const settings: StoredSettings = await SettingsStore.load(getContext(this))
      this.connection.apiBaseUrl = settings.apiBaseUrl
      this.connection.apiKey = settings.apiKey
      this.job.restore(settings.activeJobIds)
      this.batch.restore(settings.activeBatchJobId)
      this.auth.authUsername = settings.lastUsername
      this.auth.authAccountType = settings.lastAccountType === 'developer' ? 'developer' : 'user'
      this.auth.rememberPassword = settings.rememberPassword
      this.auth.autoLogin = settings.rememberPassword && settings.autoLogin
      this.auth.rememberedUsernames = settings.rememberedUsernames
      if (this.auth.rememberPassword && this.auth.authUsername.length > 0) {
        this.auth.authPassword = await SecureStore.readPassword(this.auth.authUsername)
      }
      ApiClient.setSessionToken('')
      this.auth.authReady = true
      if (this.isConfigured() && this.auth.authAccountType === 'developer') {
        await this.loadMembers()
      }
      if (this.isConfigured() && this.auth.autoLogin && this.auth.authUsername.length > 0) {
        this.auth.autoLoginBusy = true
        this.auth.authBusy = true
        try {
          // Keep the login page visible long enough to make the transition understandable.
          await this.delay(900)
          if (settings.authToken.length > 0) {
            ApiClient.setSessionToken(settings.authToken)
            try {
              this.auth.currentUser = await AuthApi.me(this.connection.apiBaseUrl, this.connection.apiKey)
            } catch (sessionError) {
              // A remembered password remains useful when the previous bearer
              // session was revoked by logout or expired on the computer.
              if (!isUnauthorizedError(sessionError as Object) || this.auth.authPassword.length === 0) {
                if (sessionError instanceof ApiError) throw sessionError
                throw new Error('读取保存的登录状态失败。')
              }
              ApiClient.setSessionToken('')
              const fallbackLogin: AuthResult = await AuthApi.login(
                this.connection.apiBaseUrl, this.connection.apiKey,
                this.auth.authUsername, this.auth.authPassword, this.auth.authAccountType
              )
              ApiClient.setSessionToken(fallbackLogin.token)
              await SettingsStore.saveAuthToken(getContext(this), fallbackLogin.token)
              this.auth.currentUser = fallbackLogin.user
            }
          } else if (this.auth.authPassword.length > 0) {
            const login: AuthResult = await AuthApi.login(
              this.connection.apiBaseUrl, this.connection.apiKey,
              this.auth.authUsername, this.auth.authPassword, this.auth.authAccountType
            )
            ApiClient.setSessionToken(login.token)
            await SettingsStore.saveAuthToken(getContext(this), login.token)
            this.auth.currentUser = login.user
          }
          if (this.auth.currentUser === undefined ||
            this.auth.currentUser.username.toLowerCase() !== this.auth.authUsername.toLowerCase() ||
            this.auth.currentUser.accountType !== this.auth.authAccountType) {
            throw new Error('保存的登录状态与所选组员不一致。')
          }
          this.auth.syncProfileFields()
          this.auth.authenticated = true
          this.applyAccountAccess()
          await this.loadSamples(false)
          if (this.isDeveloperAccount()) await this.loadMembers()
          if (this.isDeveloperAccount()) this.refreshServiceHealth()
          if (this.job.pendingCount() > 0) {
            await this.refreshPendingTasks()
          }
          if (this.batch.pendingJobId().length > 0) {
            await this.refreshBatchJob()
          }
        } catch (error) {
          ApiClient.setSessionToken('')
          await SettingsStore.saveAuthToken(getContext(this), '')
          this.auth.currentUser = undefined
          this.auth.authError = userFacingError(error as Object, '自动登录失败，请手动登录。')
        } finally {
          this.auth.autoLoginBusy = false
          this.auth.authBusy = false
        }
      }
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '读取本地配置失败，请重新打开应用。') })
    } finally {
      this.auth.authReady = true
    }
  }

  private delay(milliseconds: number): Promise<void> {
    return new Promise<void>((resolve: () => void) => {
      setTimeout(() => resolve(), milliseconds)
    })
  }

  private async selectLoginMember(username: string): Promise<void> {
    this.auth.authAccountType = 'developer'
    this.auth.authMode = 'login'
    this.auth.authUsername = username
    this.auth.authError = ''
    const remembered: boolean = this.auth.rememberedUsernames.indexOf(username) >= 0
    this.auth.rememberPassword = remembered
    this.auth.authPassword = remembered ? await SecureStore.readPassword(username) : ''
    if (!remembered) this.auth.autoLogin = false
  }

  private isDeveloperAccount(): boolean {
    return this.auth.currentUser !== undefined && this.auth.currentUser.accountType === 'developer'
  }

  private applyAccountAccess(): void {
    if (!this.isDeveloperAccount()) {
      this.history.updateFilters('mine', '', this.history.status, this.history.favoritesOnly)
      this.auth.members = []
      this.serviceHealth = undefined
    }
  }

  private async changeAccountType(value: string): Promise<void> {
    const normalized: string = value === 'developer' ? 'developer' : 'user'
    if (normalized === this.auth.authAccountType) return
    this.auth.authAccountType = normalized
    this.auth.authMode = 'login'
    this.auth.authUsername = ''
    this.auth.authPassword = ''
    this.auth.authDisplayName = ''
    this.auth.authRole = ''
    this.auth.authPasswordConfirm = ''
    this.auth.authError = ''
    if (normalized === 'developer' && this.isConfigured()) {
      await this.loadMembers()
    } else {
      this.auth.members = []
      this.auth.membersError = ''
    }
  }

  private changeAuthMode(value: string): void {
    if (value === this.auth.authMode) {
      return
    }
    this.auth.authError = ''
    if (value === 'register') {
      this.auth.loginUsernameDraft = this.auth.authUsername
      this.auth.loginPasswordDraft = this.auth.authPassword
      this.auth.authMode = 'register'
      this.auth.authUsername = ''
      this.auth.authDisplayName = ''
      this.auth.authRole = ''
      this.auth.authPassword = ''
      this.auth.authPasswordConfirm = ''
      return
    }
    this.auth.authMode = 'login'
    this.auth.authUsername = this.auth.loginUsernameDraft
    this.auth.authPassword = this.auth.loginPasswordDraft
    this.auth.authDisplayName = ''
    this.auth.authRole = ''
    this.auth.authPasswordConfirm = ''
  }

  private rememberPasswordChanged(value: boolean): void {
    this.auth.rememberPassword = value
    if (!value) this.auth.autoLogin = false
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
      if (this.auth.authAccountType === 'developer' && this.auth.authRole.trim().length === 0) {
        await promptAction.openToast({ message: '请选择任务分类。' })
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
          this.auth.authAccountType === 'developer' ? this.auth.authRole.trim() : '',
          this.auth.authAccountType
        )
      } else {
        auth = await AuthApi.login(
          this.connection.apiBaseUrl,
          this.connection.apiKey,
          this.auth.authUsername.trim(),
          this.auth.authPassword,
          this.auth.authAccountType
        )
      }
      ApiClient.setSessionToken(auth.token)
      await SettingsStore.saveAuthToken(getContext(this), auth.token)
      const username: string = auth.user.username
      if (this.auth.rememberPassword) {
        await SecureStore.savePassword(username, this.auth.authPassword)
        if (this.auth.rememberedUsernames.indexOf(username) < 0) {
          this.auth.rememberedUsernames.push(username)
        }
      } else {
        await SecureStore.removePassword(username)
        const rememberedIndex: number = this.auth.rememberedUsernames.indexOf(username)
        if (rememberedIndex >= 0) this.auth.rememberedUsernames.splice(rememberedIndex, 1)
      }
      await SettingsStore.saveLoginPreferences(
        getContext(this), username, auth.user.accountType, this.auth.rememberPassword,
        this.auth.autoLogin, this.auth.rememberedUsernames
      )
      this.auth.currentUser = auth.user
      this.auth.syncProfileFields()
      this.auth.authenticated = true
      this.auth.authAccountType = auth.user.accountType
      this.applyAccountAccess()
      this.auth.authPassword = ''
      this.auth.authPasswordConfirm = ''
      await this.loadSamples(false)
      if (this.isDeveloperAccount()) await this.loadMembers()
      if (this.isDeveloperAccount()) this.refreshServiceHealth()
      if (this.job.pendingCount() > 0) {
        this.startPolling()
      }
      if (this.batch.pendingJobId().length > 0) {
        this.startBatchPolling()
      }
    } catch (error) {
      ApiClient.setSessionToken('')
      this.auth.authError = userFacingError(error as Object, '登录或注册失败，请检查输入后重试。')
    } finally {
      this.auth.authBusy = false
    }
  }

  private async logout(): Promise<void> {
    const username: string = this.auth.currentUser === undefined ?
      this.auth.authUsername : this.auth.currentUser.username
    try {
      await AuthApi.logout(this.connection.apiBaseUrl, this.connection.apiKey)
    } catch (error) {
    }
    this.stopPolling()
    this.stopBatchPolling()
    ApiClient.setSessionToken('')
    await SettingsStore.saveAuthToken(getContext(this), '')
    this.auth.clearSession()
    await this.restoreLoggedOutForm(username, '')
    this.currentTab = 0
  }

  private confirmLogout(): void {
    showDangerConfirmation({
      title: '退出当前账号？',
      message: '退出后需要重新登录。正在电脑端运行的任务不会被删除。',
      confirmText: '确认退出'
    }, () => this.logout())
  }

  private async expireSession(): Promise<void> {
    const username: string = this.auth.currentUser === undefined ?
      this.auth.authUsername : this.auth.currentUser.username
    this.stopPolling()
    this.stopBatchPolling()
    ApiClient.setSessionToken('')
    await SettingsStore.saveAuthToken(getContext(this), '')
    this.auth.clearSession()
    await this.restoreLoggedOutForm(username, '登录状态已失效，请重新登录。')
    this.currentTab = 0
    await promptAction.openToast({ message: '登录已失效，请重新登录。' })
  }

  private async restoreLoggedOutForm(username: string, errorMessage: string): Promise<void> {
    this.auth.authMode = 'login'
    this.auth.authUsername = username
    this.auth.authError = errorMessage
    const remembered: boolean = this.auth.rememberedUsernames.indexOf(username) >= 0
    this.auth.rememberPassword = remembered
    this.auth.authPassword = remembered ? await SecureStore.readPassword(username) : ''
    if (!remembered) this.auth.autoLogin = false
    await this.loadMembers()
    await this.refreshServiceHealth()
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
        this.isDeveloperAccount() ? this.auth.profileRole : ''
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
      if (this.auth.rememberPassword) {
        await SecureStore.savePassword(result.user.username, this.auth.newPassword)
      }
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
    if (this.auth.authenticated && !this.isDeveloperAccount()) {
      this.auth.members = []
      this.auth.membersError = ''
      return
    }
    this.auth.membersLoading = true
    this.auth.membersError = ''
    try {
      this.auth.members = await AuthApi.members(this.connection.apiBaseUrl, this.connection.apiKey)
    } catch (error) {
      this.auth.members = []
      this.auth.membersError = userFacingError(error as Object, '无法加载团队成员，请稍后重试。')
      if (isUnauthorizedError(error as Object) && this.auth.authenticated) {
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
      this.currentTab = 4
      return false
    }
    if (this.connection.apiKey.trim().length === 0) {
      await promptAction.openToast({ message: '请先在设置页填写 API 密钥。' })
      this.currentTab = 4
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
      options.isPhotoTakingSupported = true
      options.isEditSupported = true
      options.isPreviewForSingleSelectionSupported = true
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

  private async applyImageEdits(spec: ImageEditSpec): Promise<void> {
    if (this.job.imageUri.length === 0 || this.imageEditing) {
      await promptAction.openToast({ message: '请先选择图片。' })
      return
    }
    if (isUnchangedImageEdit(spec)) {
      await promptAction.openToast({ message: '图片没有发生变化。' })
      return
    }
    this.imageEditing = true
    try {
      this.job.imageUri = await ImageEditService.apply(
        getContext(this) as common.UIAbilityContext, this.job.imageUri, spec)
      this.job.completionAnalysisId = ''
      this.job.completionMessage = ''
      await promptAction.openToast({ message: '裁剪和旋转结果已应用。' })
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '编辑图片失败。') })
    } finally {
      this.imageEditing = false
    }
  }

  private selectBuiltInImage(uri: string): void {
    if (this.builtInImages.indexOf(uri) >= 0) {
      this.job.imageUri = uri
    }
  }

  private async chooseBatchImages(): Promise<void> {
    try {
      const remaining: number = this.batch.remainingCapacity()
      if (remaining <= 0) {
        await promptAction.openToast({ message: `每次最多选择 ${MAX_MOBILE_BATCH_IMAGES} 张图片。` })
        return
      }
      const options = new photoAccessHelper.PhotoSelectOptions()
      options.MIMEType = photoAccessHelper.PhotoViewMIMETypes.IMAGE_TYPE
      options.maxSelectNumber = remaining
      options.isPhotoTakingSupported = true
      const picker = new photoAccessHelper.PhotoViewPicker()
      const selected = await picker.select(options)
      if (selected.photoUris.length > 0) {
        this.batch.append(selected.photoUris)
      } else {
        await promptAction.openToast({ message: '请在图库中选择至少一张分子结构图片。' })
      }
    } catch (error) {
      await promptAction.openToast({ message: userFacingError(error as Object, '批量选择图片失败，请重试。') })
    }
  }

  private async useBuiltInBatch(): Promise<void> {
    if (this.builtInImages.length === 0) {
      await promptAction.openToast({ message: '内置测试集加载失败，请重新打开应用。' })
      return
    }
    const before: number = this.batch.selectedUris.length
    this.batch.append(this.builtInImages)
    await promptAction.openToast({ message: `已添加 ${this.batch.selectedUris.length - before} 张测试图。` })
  }

  private toggleBuiltInBatch(uri: string): void {
    if (this.builtInImages.indexOf(uri) < 0) return
    const next: string[] = this.batch.selectedUris.slice()
    const selectedIndex: number = next.indexOf(uri)
    if (selectedIndex >= 0) {
      next.splice(selectedIndex, 1)
    } else if (next.length < MAX_MOBILE_BATCH_IMAGES) {
      next.push(uri)
    }
    this.batch.select(next)
  }

  private async submitBatch(): Promise<void> {
    if (this.batch.selectedUris.length === 0) {
      await promptAction.openToast({ message: '请先选择要识别的图片。' })
      return
    }
    if (this.batch.pendingJobId().length > 0) {
      await promptAction.openToast({ message: '已有批量任务正在处理，请先完成或取消当前任务。' })
      return
    }
    if (this.batch.uploading || !(await this.requireConfiguration())) {
      return
    }
    this.batch.uploading = true
    this.batch.errorMessage = ''
    try {
      const created: BatchJob = await BatchApi.create(
        this.connection.apiBaseUrl,
        this.connection.apiKey,
        this.batch.selectedUris
      )
      this.connection.markReachable()
      this.batch.activate(created)
      this.batch.clearSelection()
      await SettingsStore.saveActiveBatchJob(getContext(this), this.batch.pendingJobId())
      this.startBatchPolling()
      await promptAction.openToast({ message: `已提交 ${created.total} 张图片。` })
    } catch (error) {
      this.batch.errorMessage = userFacingError(error as Object, '批量图片提交失败，请稍后重试。')
      await this.showError(error as Object)
    } finally {
      this.batch.uploading = false
    }
  }

  private startBatchPolling(): void {
    if (!this.auth.authenticated || this.batch.pendingJobId().length === 0 || this.batchPollTimer >= 0) {
      return
    }
    this.batchPollTimer = setInterval(() => this.refreshBatchJob(), 2000)
  }

  private stopBatchPolling(): void {
    if (this.batchPollTimer >= 0) {
      clearInterval(this.batchPollTimer)
      this.batchPollTimer = -1
    }
  }

  private async refreshBatchJob(): Promise<void> {
    const jobId: string = this.batch.pendingJobId()
    if (!this.auth.authenticated || jobId.length === 0 || this.batch.polling) {
      return
    }
    this.batch.polling = true
    try {
      const state: BatchJob = await BatchApi.get(this.connection.apiBaseUrl, this.connection.apiKey, jobId)
      this.connection.markReachable()
      this.batch.apply(state)
      await SettingsStore.saveActiveBatchJob(getContext(this), this.batch.pendingJobId())
      if (this.batch.pendingJobId().length === 0) {
        this.stopBatchPolling()
      }
    } catch (error) {
      if (isUnauthorizedError(error as Object)) {
        await this.expireSession()
      } else if (error instanceof ApiError && error.statusCode === 404) {
        await this.invalidateMissingBatchTask()
      } else {
        this.batch.errorMessage = userFacingError(error as Object, '暂时无法刷新批量进度，将自动重试。')
        if (isTransportError(error as Object)) this.connection.markTransportFailure()
      }
    } finally {
      this.batch.polling = false
    }
  }

  private async invalidateMissingBatchTask(): Promise<void> {
    this.stopBatchPolling()
    this.batch.invalidateMissing('该批量任务已被电脑端清理，缓存结果也已从当前页面移除。')
    await SettingsStore.saveActiveBatchJob(getContext(this), '')
  }

  private async controlBatch(action: string): Promise<void> {
    if (this.batch.job === undefined || this.batch.controlBusy) {
      return
    }
    this.batch.controlBusy = true
    this.batch.errorMessage = ''
    try {
      const jobId: string = this.batch.job.jobId
      let state: BatchJob
      if (action === 'pause') {
        state = await BatchApi.pause(this.connection.apiBaseUrl, this.connection.apiKey, jobId)
      } else if (action === 'resume') {
        state = await BatchApi.resume(this.connection.apiBaseUrl, this.connection.apiKey, jobId)
      } else {
        state = await BatchApi.cancel(this.connection.apiBaseUrl, this.connection.apiKey, jobId)
      }
      this.batch.apply(state)
      await SettingsStore.saveActiveBatchJob(getContext(this), this.batch.pendingJobId())
      if (this.batch.pendingJobId().length > 0) this.startBatchPolling()
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        await this.invalidateMissingBatchTask()
      } else {
        this.batch.errorMessage = userFacingError(error as Object, '批量任务控制失败，请重试。')
        await this.showError(error as Object)
      }
    } finally {
      this.batch.controlBusy = false
    }
  }

  private confirmBatchCancel(): void {
    showDangerConfirmation({
      title: '取消批量任务？',
      message: '未处理的图片将不再识别，已经完成的结果仍会保留。',
      confirmText: '确认取消'
    }, () => this.controlBatch('cancel'))
  }

  private async retryBatchFailed(): Promise<void> {
    if (this.batch.job === undefined || this.batch.controlBusy) {
      return
    }
    this.batch.controlBusy = true
    try {
      const retry: BatchJob = await BatchApi.retryFailed(
        this.connection.apiBaseUrl,
        this.connection.apiKey,
        this.batch.job.jobId
      )
      this.batch.activate(retry)
      await SettingsStore.saveActiveBatchJob(getContext(this), this.batch.pendingJobId())
      this.startBatchPolling()
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        await this.invalidateMissingBatchTask()
      } else {
        this.batch.errorMessage = userFacingError(error as Object, '失败项重试未能启动。')
        await this.showError(error as Object)
      }
    } finally {
      this.batch.controlBusy = false
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

  private async controlRecognitionJob(jobId: string, action: string): Promise<void> {
    try {
      let state: VisionJob
      if (action === 'pause') {
        state = await JobApi.pause(this.connection.apiBaseUrl, this.connection.apiKey, jobId)
      } else if (action === 'resume') {
        state = await JobApi.resume(this.connection.apiBaseUrl, this.connection.apiKey, jobId)
      } else {
        state = await JobApi.cancel(this.connection.apiBaseUrl, this.connection.apiKey, jobId)
      }
      this.job.applyServerJob(state)
      await SettingsStore.saveActiveJobs(getContext(this), this.job.pendingJobIds())
      if (this.job.pendingCount() > 0) this.startPolling()
    } catch (error) {
      await this.showError(error as Object)
    }
  }

  private confirmRecognitionCancel(jobId: string): void {
    showDangerConfirmation({
      title: '终止识别任务？',
      message: '任务终止后不能继续，已经生成的结果不会被删除。',
      confirmText: '确认终止'
    }, () => this.controlRecognitionJob(jobId, 'cancel'))
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

  private async openExportCenter(scope: string, resourceId: string): Promise<void> {
    await router.pushUrl({
      url: 'pages/ExportCenter',
      params: { scope: scope, resourceId: resourceId }
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

  private async openDatasetReview(): Promise<void> {
    await router.pushUrl({ url: 'pages/DatasetReview' })
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
    if (!this.isDeveloperAccount()) {
      scope = 'mine'
      ownerUserId = ''
    }
    if (this.history.scope === scope && this.history.ownerUserId === ownerUserId &&
      this.history.status === status && this.history.favoritesOnly === favoritesOnly) {
      return
    }
    this.history.updateFilters(scope, ownerUserId, status, favoritesOnly)
    setTimeout(() => this.loadHistory(true), 0)
  }

  private clearHistoryFilters(): void {
    this.history.query = ''
    this.history.updateFilters(this.isDeveloperAccount() ? 'all' : 'mine', '', 'all', false)
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
    showDangerConfirmation({
      title: '从历史中删除？',
      message: '只会从历史列表中移除，电脑上的报告和图片文件会保留。',
      confirmText: '确认删除'
    }, () => this.deleteHistory(item))
  }

  private async deleteHistory(item: HistoryItem): Promise<void> {
    try {
      await AnalysisApi.deleteAnalysis(this.connection.apiBaseUrl, this.connection.apiKey, item.analysisId)
      await this.loadHistory(true)
      await promptAction.openToast({ message: '历史记录已移除，电脑端文件仍然保留。' })
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
      this.serviceHealth = health
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

  private async refreshServiceHealth(): Promise<void> {
    if (!this.connection.isConfigured()) return
    try {
      this.serviceHealth = await JobApi.health(this.connection.apiBaseUrl, this.connection.apiKey)
      this.connection.markConnected(this.serviceHealth.appMode, this.serviceHealth.backend)
    } catch (error) {
      this.serviceHealth = undefined
      if (isTransportError(error as Object)) {
        this.connection.markTransportFailure()
      } else {
        this.connection.serviceSummary = '服务验证失败 · 请检查密钥'
      }
    }
  }

  private onTabChanged(index: number): void {
    this.currentTab = index
    if (this.job.pendingCount() > 0) {
      this.startPolling()
    }
    if (this.batch.pendingJobId().length > 0) {
      this.startBatchPolling()
    }
    if (index === 1 && this.batch.pendingJobId().length > 0) {
      this.refreshBatchJob()
    }
    if (index === 3) {
      this.loadHistory(true, this.history.items.length > 0)
    }
    if (index === 4) {
      if (this.isDeveloperAccount()) this.refreshServiceHealth()
    }
  }

  @Builder
  private recognitionPage() {
    RecognitionView({
      isDeveloper: this.isDeveloperAccount(),
      currentUser: this.auth.currentUser,
      tasks: this.job.tasks,
      serviceSummary: this.isDeveloperAccount() ? this.connection.serviceSummary :
        (this.connection.serviceSummary.startsWith('已连接') ? '服务正常' : '服务暂不可用'),
      submitting: this.job.submitting,
      imageEditing: this.imageEditing,
      imageUri: this.job.imageUri,
      builtInImages: this.builtInImages,
      onOpenSettings: (): void => { this.currentTab = 4 },
      onOpenGuide: (): void => { this.openKnowledgeGuide() },
      onOpenDocuments: (): void => { this.openDocumentWorkspace() },
      onChooseImage: (): void => { this.chooseImage() },
      onTakePhoto: (): void => { this.chooseImage() },
      onApplyImageEdits: (spec: ImageEditSpec): void => { this.applyImageEdits(spec) },
      onSelectBuiltInImage: (uri: string): void => { this.selectBuiltInImage(uri) },
      onSubmitImage: (): void => { this.submitImage() },
      onOpenResult: (analysisId: string): void => { this.openResult(analysisId) },
      onPauseTask: (jobId: string): void => { this.controlRecognitionJob(jobId, 'pause') },
      onResumeTask: (jobId: string): void => { this.controlRecognitionJob(jobId, 'resume') },
      onCancelTask: (jobId: string): void => { this.confirmRecognitionCancel(jobId) }
    })
  }

  @Builder
  private batchPage() {
    BatchView({
      currentUser: this.auth.currentUser,
      selectedUris: this.batch.selectedUris,
      job: this.batch.job,
      uploading: this.batch.uploading,
      controlBusy: this.batch.controlBusy,
      errorMessage: this.batch.errorMessage,
      builtInImages: this.builtInImages,
      onOpenSettings: (): void => { this.currentTab = 4 },
      onOpenGuide: (): void => { this.openKnowledgeGuide() },
      onChooseImages: (): void => { this.chooseBatchImages() },
      onUseBuiltInImages: (): void => { this.useBuiltInBatch() },
      onToggleBuiltInImage: (uri: string): void => { this.toggleBuiltInBatch(uri) },
      onRemoveSelected: (index: number): void => { this.batch.removeSelected(index) },
      onRemoveSelectedUris: (uris: string[]): void => { this.batch.removeSelectedUris(uris) },
      onClearSelection: (): void => { this.batch.clearSelection() },
      onSubmit: (): void => { this.submitBatch() },
      onPause: (): void => { this.controlBatch('pause') },
      onResume: (): void => { this.controlBatch('resume') },
      onCancel: (): void => { this.confirmBatchCancel() },
      onRetryFailed: (): void => { this.retryBatchFailed() },
      onOpenExports: (jobId: string): void => { this.openExportCenter('batch', jobId) },
      onOpenResult: (analysisId: string): void => { this.openResult(analysisId) }
    })
  }

  @Builder
  private smilesPage() {
    SmilesView({
      currentUser: this.auth.currentUser,
      smilesInput: this.job.smilesInput,
      analyzing: this.job.smilesAnalyzing,
      onOpenSettings: (): void => { this.currentTab = 4 },
      onOpenGuide: (): void => { this.openKnowledgeGuide() },
      onSmilesChanged: (value: string): void => { this.job.smilesInput = value },
      onAnalyze: (): void => { this.analyzeSmiles() }
    })
  }

  @Builder
  private historyPage() {
    HistoryView({
      isDeveloper: this.isDeveloperAccount(),
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
      onOpenSettings: (): void => { this.currentTab = 4 },
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
      isDeveloper: this.isDeveloperAccount(),
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
      serviceHealth: this.serviceHealth,
      onOpenGuide: (): void => { this.openKnowledgeGuide() },
      onOpenDatasetReview: (): void => { this.openDatasetReview() },
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
      onLogout: (): void => { this.confirmLogout() }
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
        accountType: this.auth.authAccountType,
        username: this.auth.authUsername,
        displayName: this.auth.authDisplayName,
        role: this.auth.authRole,
        password: this.auth.authPassword,
        passwordConfirm: this.auth.authPasswordConfirm,
        apiBaseUrl: this.connection.apiBaseUrl,
        apiKey: this.connection.apiKey,
        serviceSummary: this.connection.serviceSummary,
        busy: this.auth.authBusy,
        errorMessage: this.auth.authError,
        members: this.auth.members,
        membersLoading: this.auth.membersLoading,
        rememberPassword: this.auth.rememberPassword,
        autoLogin: this.auth.autoLogin,
        autoLoginBusy: this.auth.autoLoginBusy,
        rememberedUsernames: this.auth.rememberedUsernames,
        onAuthModeChanged: (value: string): void => { this.changeAuthMode(value) },
        onAccountTypeChanged: (value: string): void => { this.changeAccountType(value) },
        onUsernameChanged: (value: string): void => { this.auth.authUsername = value },
        onDisplayNameChanged: (value: string): void => { this.auth.authDisplayName = value },
        onRoleChanged: (value: string): void => { this.auth.authRole = value },
        onPasswordChanged: (value: string): void => { this.auth.authPassword = value },
        onPasswordConfirmChanged: (value: string): void => { this.auth.authPasswordConfirm = value },
        onApiBaseUrlChanged: (value: string): void => { this.connection.apiBaseUrl = value },
        onApiKeyChanged: (value: string): void => { this.connection.apiKey = value },
        onSelectMember: (username: string): void => { this.selectLoginMember(username) },
        onRememberPasswordChanged: (value: boolean): void => { this.rememberPasswordChanged(value) },
        onAutoLoginChanged: (value: boolean): void => { this.auth.autoLogin = value },
        onSubmit: () => this.submitAuth()
      })
    } else {
      Stack({ alignContent: Alignment.Top }) {
        AppShell({
          currentTab: $currentTab,
          recognition: (): void => this.recognitionPage(),
          batch: (): void => this.batchPage(),
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
