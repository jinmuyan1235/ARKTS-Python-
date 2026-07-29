import { UserProfile } from '../model/VisionResult'

/**
 * Authentication, account form and team-member UI state.
 * Network orchestration remains in Index during phase one to keep the current
 * login and Asset Store lifecycle unchanged.
 */
@Observed
export class AuthController {
  authReady: boolean = false
  authenticated: boolean = false
  authMode: string = 'login'
  authAccountType: string = 'user'
  authUsername: string = ''
  authDisplayName: string = ''
  authRole: string = ''
  authPassword: string = ''
  authPasswordConfirm: string = ''
  loginUsernameDraft: string = ''
  loginPasswordDraft: string = ''
  authBusy: boolean = false
  autoLoginBusy: boolean = false
  rememberPassword: boolean = false
  autoLogin: boolean = false
  rememberedUsernames: string[] = []
  authError: string = ''
  currentUser: UserProfile | undefined = undefined
  members: UserProfile[] = []
  membersLoading: boolean = false
  membersError: string = ''
  avatarBusy: boolean = false
  profileDisplayName: string = ''
  profileRole: string = ''
  profileSaving: boolean = false
  oldPassword: string = ''
  newPassword: string = ''
  newPasswordConfirm: string = ''
  passwordBusy: boolean = false

  syncProfileFields(): void {
    this.profileDisplayName = this.currentUser === undefined ? '' : this.currentUser.displayName
    this.profileRole = this.currentUser === undefined ? '' : this.currentUser.role
  }

  clearSensitiveFormFields(): void {
    this.authPassword = ''
    this.authPasswordConfirm = ''
    this.oldPassword = ''
    this.newPassword = ''
    this.newPasswordConfirm = ''
  }

  clearSession(): void {
    this.authenticated = false
    this.currentUser = undefined
    this.syncProfileFields()
    this.clearSensitiveFormFields()
  }
}
