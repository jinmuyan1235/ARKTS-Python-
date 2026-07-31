import { HealthResult, UserProfile } from '../model/VisionResult'
import { TEAM_ROLE_OPTIONS } from '../model/TeamRoles'
import { AppEmptyState, AppErrorState, LoadingSkeleton, MoleculeBackdrop, SectionHeader, StatusPill } from './CommonStates'
import { ConnectionSetupCard } from './ConnectionSetupCard'
import { PageHeader, ProfileAvatar } from './PageChrome'

@Component
export struct SettingsView {
  @Prop isDeveloper: boolean = false
  @Prop currentUser: UserProfile | undefined = undefined
  @Prop members: UserProfile[] = []
  @Prop membersLoading: boolean = false
  @Prop membersError: string = ''
  @Prop displayName: string = ''
  @Prop role: string = ''
  @Prop oldPassword: string = ''
  @Prop newPassword: string = ''
  @Prop newPasswordConfirm: string = ''
  @Prop apiBaseUrl: string = ''
  @Prop apiKey: string = ''
  @Prop avatarBusy: boolean = false
  @Prop profileSaving: boolean = false
  @Prop passwordBusy: boolean = false
  @Prop savingSettings: boolean = false
  @Prop checking: boolean = false
  @Prop serviceSummary: string = ''
  @Prop serviceHealth: HealthResult | undefined = undefined
  @State activeSection: string = ''
  onChooseAvatar: () => void = () => {}
  onSaveProfile: () => void = () => {}
  onChangePassword: () => void = () => {}
  onSaveSettings: () => void = () => {}
  onCheckService: () => void = () => {}
  onLogout: () => void = () => {}
  onOpenGuide: () => void = () => {}
  onOpenDatasetReview: () => void = () => {}
  onDisplayNameChanged: (value: string) => void = (_value: string) => {}
  onRoleChanged: (value: string) => void = (_value: string) => {}
  onOldPasswordChanged: (value: string) => void = (_value: string) => {}
  onNewPasswordChanged: (value: string) => void = (_value: string) => {}
  onNewPasswordConfirmChanged: (value: string) => void = (_value: string) => {}
  onApiBaseUrlChanged: (value: string) => void = (_value: string) => {}
  onApiKeyChanged: (value: string) => void = (_value: string) => {}

  @Builder
  private cardTitle(symbol: string, title: string, caption: string) {
    SectionHeader({ symbol: symbol, title: title, caption: caption })
  }

  private sectionTitle(): string {
    if (this.activeSection === 'account') return '账号'
    if (this.activeSection === 'security') return '安全'
    if (this.activeSection === 'service') return '服务连接'
    if (this.activeSection === 'developer') return '开发工具'
    return '设置'
  }

  @Builder
  private settingsMenuItem(symbol: string, title: string, caption: string, section: string) {
    Row({ space: 12 }) {
      Text(symbol).fontSize(20).fontWeight(FontWeight.Bold).fontColor($r('app.color.primary'))
        .textAlign(TextAlign.Center).width(44).height(44)
        .backgroundColor($r('app.color.primary_soft')).borderRadius(14)
      Column({ space: 3 }) {
        Text(title).fontSize(16).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
        Text(caption).fontSize(11).fontColor($r('app.color.text_secondary')).maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }.alignItems(HorizontalAlign.Start).layoutWeight(1)
      Text('›').fontSize(22).fontColor($r('app.color.text_tertiary'))
        .textAlign(TextAlign.Center).width(32).height(44)
    }
    .alignItems(VerticalAlign.Center).width('100%').height(78).padding({ left: 16, right: 12 })
    .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') })
    .borderRadius($r('app.float.card_radius')).onClick(() => this.activeSection = section)
    .accessibilityText(`打开${title}设置`)
  }

  build() {
    Stack() {
      MoleculeBackdrop()
      Scroll() {
        Column({ space: 16 }) {
          PageHeader({
            title: this.sectionTitle(),
            subtitle: '',
            currentUser: this.currentUser,
            onOpenGuide: this.onOpenGuide
          })

          if (this.activeSection !== '') {
            Row() {
              Text('‹  返回设置')
                .fontSize(15).fontWeight(FontWeight.Medium).fontColor($r('app.color.primary'))
                .height(44).lineHeight(44).width(120)
            }
            .alignItems(VerticalAlign.Center).width('100%').height(44)
            .onClick(() => this.activeSection = '')
            .accessibilityText('返回设置主页')
          }

          if (this.activeSection === '') {
            this.settingsMenuItem('◉', '账号', '头像、显示名称与团队信息', 'account')
            this.settingsMenuItem('◇', '安全', '密码、会话与退出登录', 'security')
            this.settingsMenuItem('⌁', '服务连接', '服务地址、API Key 与连接检测', 'service')
            if (this.isDeveloper) {
              this.settingsMenuItem('◫', '开发工具', '模型状态、反馈审核与诊断', 'developer')
            }
          }

          if (this.activeSection === '') {
            Row({ space: 12 }) {
              Text('?').fontSize(20).fontWeight(FontWeight.Bold).fontColor($r('app.color.primary'))
                .textAlign(TextAlign.Center).width(44).height(44)
                .backgroundColor($r('app.color.primary_soft')).borderRadius(14)
              Column({ space: 3 }) {
                Text('术语与项目指南')
                  .fontSize(16).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
                Text('术语说明与使用帮助').fontSize(11).fontColor($r('app.color.text_secondary'))
                  .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
              }.alignItems(HorizontalAlign.Start).layoutWeight(1)
              Text('›').fontSize(22).fontColor($r('app.color.text_tertiary'))
                .textAlign(TextAlign.Center).width(32).height(44)
            }
            .alignItems(VerticalAlign.Center).width('100%').height(78)
            .padding({ left: 16, right: 12 })
            .backgroundColor($r('app.color.surface'))
            .border({ width: 1, color: $r('app.color.border') })
            .borderRadius($r('app.float.card_radius'))
            .onClick(this.onOpenGuide)
            .accessibilityText('打开化学信息学术语与项目指南')
          }

          if (this.activeSection === 'developer' && this.isDeveloper) {
            Row({ space: 12 }) {
              Text('◎').fontSize(21).fontWeight(FontWeight.Bold).fontColor($r('app.color.accent'))
                .textAlign(TextAlign.Center).width(44).height(44)
                .backgroundColor($r('app.color.primary_soft')).borderRadius(14)
              Column({ space: 3 }) {
                Text('反馈数据审核').fontSize(16).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
                Text('反馈样本审核与清单导出').fontSize(11).fontColor($r('app.color.text_secondary'))
                  .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
              }.alignItems(HorizontalAlign.Start).layoutWeight(1)
              Text('›').fontSize(22).fontColor($r('app.color.text_tertiary'))
                .textAlign(TextAlign.Center).width(32).height(44)
            }
            .alignItems(VerticalAlign.Center).width('100%').height(78)
            .padding({ left: 16, right: 12 })
            .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') })
            .borderRadius($r('app.float.card_radius')).onClick(this.onOpenDatasetReview)
            .accessibilityText('打开反馈数据审核模块')
          }

          if (this.activeSection === 'account' && this.currentUser !== undefined) {
            Column({ space: 14 }) {
              this.cardTitle('◉', '账号资料', `用户名 @${this.currentUser.username} 不可修改`)
              Row({ space: 13 }) {
                ProfileAvatar({ user: this.currentUser, avatarSize: 64 })
                Column({ space: 5 }) {
                  Text(this.currentUser.displayName)
                    .fontSize(19).fontWeight(FontWeight.Bold).fontColor($r('app.color.text_primary'))
                    .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
                  Text(this.isDeveloper ?
                    (this.currentUser.role.length > 0 ? this.currentUser.role : '开发者') : '普通用户')
                    .fontSize(12).fontColor($r('app.color.primary'))
                    .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
                  StatusPill({ symbol: '✓', label: '已登录', tone: 'success' })
                }
                .alignItems(HorizontalAlign.Start)
                .layoutWeight(1)
                Button(this.avatarBusy ? '上传中…' : '更换头像')
                  .height(44).enabled(!this.avatarBusy)
                  .backgroundColor($r('app.color.primary_soft')).fontColor($r('app.color.primary'))
                  .onClick(this.onChooseAvatar)
                  .accessibilityText('从图库更换账号头像')
              }
              .width('100%')
              TextInput({ text: this.displayName, placeholder: '显示名称' })
                .width('100%').height(46).backgroundColor($r('app.color.surface_soft'))
                .onChange((value: string) => this.onDisplayNameChanged(value))
              if (this.isDeveloper) {
                Text('任务分类')
                  .fontSize(12).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_body')).width('100%')
                Flex({ direction: FlexDirection.Row, wrap: FlexWrap.Wrap, justifyContent: FlexAlign.Start }) {
                  ForEach(TEAM_ROLE_OPTIONS, (option: string) => {
                    Text(option).fontSize(11)
                      .fontWeight(this.role === option ? FontWeight.Medium : FontWeight.Regular)
                      .fontColor(this.role === option ? $r('app.color.text_on_primary') : $r('app.color.text_body'))
                      .textAlign(TextAlign.Center).width('31%').height(32)
                      .backgroundColor(this.role === option ? $r('app.color.primary') : $r('app.color.surface_soft'))
                      .border({ width: 1, color: this.role === option ? $r('app.color.primary') : $r('app.color.border') })
                      .borderRadius(11).margin({ right: 6, bottom: 6 })
                      .onClick(() => this.onRoleChanged(option))
                  }, (option: string) => option)
                }.width('100%')
              } else {
                StatusPill({ symbol: '●', label: '普通用户', tone: 'primary' })
              }
              Button(this.profileSaving ? '保存中…' : '保存账号资料')
                .width('100%').height(46).enabled(!this.profileSaving)
                .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
                .onClick(this.onSaveProfile)
            }
            .alignItems(HorizontalAlign.Start)
            .width('100%').padding(18)
            .backgroundColor($r('app.color.surface'))
            .border({ width: 1, color: $r('app.color.border') })
            .borderRadius($r('app.float.card_radius'))
          }

          if (this.activeSection === 'account' && this.isDeveloper) {
            Column({ space: 14 }) {
            this.cardTitle('⌬', '项目组成员', `${this.members.length} 名成员共享分析历史`)
            if (this.membersLoading && this.members.length === 0) {
              LoadingSkeleton({ rows: 1 })
            } else if (this.membersError.length > 0 && this.members.length === 0) {
              AppErrorState({ title: '组员加载失败', message: this.membersError, onRetry: this.onCheckService })
            } else if (this.members.length === 0) {
              AppEmptyState({ symbol: '◉', title: '暂无其他成员', message: '注册新组员后会在这里展示头像、姓名和角色。' })
            } else {
              Scroll() {
                Row({ space: 14 }) {
                  ForEach(this.members, (member: UserProfile) => {
                    Column({ space: 6 }) {
                      ProfileAvatar({ user: member, avatarSize: 50 })
                      Text(member.displayName)
                        .fontSize(12).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary')).maxLines(1)
                        .textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%').textAlign(TextAlign.Center)
                      Text(member.role.length > 0 ? member.role : '组员')
                        .fontSize(10).fontColor($r('app.color.text_secondary')).maxLines(1)
                        .textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%').textAlign(TextAlign.Center)
                    }
                    .width(72)
                    .padding({ top: 10, bottom: 10 })
                    .backgroundColor($r('app.color.surface_soft'))
                    .borderRadius(14)
                    .accessibilityText(`${member.displayName}，${member.role.length > 0 ? member.role : '组员'}`)
                  }, (member: UserProfile) => member.userId)
                }
              }
              .scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')
            }
          }
          .alignItems(HorizontalAlign.Start)
          .width('100%').padding(18)
          .backgroundColor($r('app.color.surface'))
          .border({ width: 1, color: $r('app.color.border') })
            .borderRadius($r('app.float.card_radius'))
          }

          if (this.activeSection === 'security') {
            Column({ space: 12 }) {
            this.cardTitle('◇', '密码与会话安全', '修改密码后其他旧会话会立即失效')
            if (this.isDeveloper) {
              Row({ space: 8 }) {
                StatusPill({ symbol: '◆', label: 'API 密钥：Asset Store', tone: 'success' })
                StatusPill({ symbol: '◆', label: '令牌：Asset Store', tone: 'success' })
              }
              .width('100%')
            }
            TextInput({ text: this.oldPassword, placeholder: '当前密码' })
              .type(InputType.Password).height(46).backgroundColor($r('app.color.surface_soft'))
              .onChange((value: string) => this.onOldPasswordChanged(value))
            TextInput({ text: this.newPassword, placeholder: '新密码（至少 8 位）' })
              .type(InputType.Password).height(46).backgroundColor($r('app.color.surface_soft'))
              .onChange((value: string) => this.onNewPasswordChanged(value))
            TextInput({ text: this.newPasswordConfirm, placeholder: '再次输入新密码' })
              .type(InputType.Password).height(46).backgroundColor($r('app.color.surface_soft'))
              .onChange((value: string) => this.onNewPasswordConfirmChanged(value))
            Button(this.passwordBusy ? '正在更新…' : '修改密码并撤销旧会话')
              .width('100%').height(46).enabled(!this.passwordBusy)
              .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.primary_strong'))
              .onClick(this.onChangePassword)
          }
          .alignItems(HorizontalAlign.Start)
          .width('100%').padding(18)
          .backgroundColor($r('app.color.surface'))
          .border({ width: 1, color: $r('app.color.border') })
          .borderRadius($r('app.float.card_radius'))
          }

          if (this.activeSection === 'service') {
            ConnectionSetupCard({
            apiBaseUrl: this.apiBaseUrl,
            apiKey: this.apiKey,
            serviceSummary: this.serviceSummary,
            checking: this.checking,
            saving: this.savingSettings,
            forceExpanded: true,
            showActions: true,
            onApiBaseUrlChanged: this.onApiBaseUrlChanged,
            onApiKeyChanged: this.onApiKeyChanged,
            onCheck: this.onCheckService,
            onSave: this.onSaveSettings
          })
          }

          if (this.activeSection === 'developer' && this.isDeveloper) {
            Column({ space: 10 }) {
            this.cardTitle('◫', '模型运行状态', '')
            if (this.serviceHealth === undefined) {
              Text('点击“检测连接”获取状态')
                .fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
            } else {
              Row() {
                StatusPill({
                  symbol: this.serviceHealth.modelRuntime.state === 'ready' ? '✓' : '!',
                  label: this.serviceHealth.modelRuntime.label,
                  tone: this.serviceHealth.modelRuntime.state === 'ready' ? 'success' :
                    (this.serviceHealth.modelRuntime.state === 'unavailable' ? 'danger' : 'warning')
                })
                Blank()
                Text(this.serviceHealth.modelRuntime.modelName)
                  .fontSize(14).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
                  .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
              }.width('100%')
              Text(`${this.serviceHealth.modelRuntime.modelVersion === null ? '版本未知' : this.serviceHealth.modelRuntime.modelVersion} · ` +
                `${this.serviceHealth.modelRuntime.device === null ? '设备未知' : this.serviceHealth.modelRuntime.device}`)
                .fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
              Row({ space: 8 }) {
                StatusPill({ symbol: '◆', label: this.serviceHealth.modelRuntime.weights.label,
                  tone: this.serviceHealth.modelRuntime.weights.state === 'fail' ? 'danger' : 'primary' })
                StatusPill({ symbol: '◷', label: `预热 ${this.serviceHealth.modelRuntime.warmup.label}`,
                  tone: this.serviceHealth.modelRuntime.warmup.state === 'fail' ? 'danger' : 'primary' })
              }.width('100%')
              if (this.serviceHealth.taskQueue.active > 0) {
                Row({ space: 8 }) {
                  StatusPill({ symbol: '↻', label: `排队 ${this.serviceHealth.taskQueue.queued}`, tone: 'primary' })
                  StatusPill({ symbol: '▶', label: `运行 ${this.serviceHealth.taskQueue.running}`, tone: 'success' })
                  if (this.serviceHealth.taskQueue.paused > 0) {
                    StatusPill({ symbol: 'Ⅱ', label: `暂停 ${this.serviceHealth.taskQueue.paused}`, tone: 'warning' })
                  }
                }.width('100%')
              }
              if (this.serviceHealth.modelRuntime.failureReasons.length > 0) {
                Column({ space: 5 }) {
                  ForEach(this.serviceHealth.modelRuntime.failureReasons.slice(0, 3), (reason: string) => {
                    Text(`• ${reason}`).fontSize(11).lineHeight(17).fontColor($r('app.color.text_secondary')).width('100%')
                  })
                }.alignItems(HorizontalAlign.Start).width('100%')
                  .padding(10).backgroundColor($r('app.color.danger_soft')).borderRadius(10)
              }
              if (this.serviceHealth.taskQueue.failed > 0) {
                Text(`${this.serviceHealth.taskQueue.failed} 条历史任务中断`)
                  .fontSize(11).fontColor($r('app.color.text_tertiary')).width('100%')
              }
            }
          }
          .alignItems(HorizontalAlign.Start)
          .width('100%').padding(18)
          .backgroundColor($r('app.color.surface'))
          .border({ width: 1, color: $r('app.color.border') })
            .borderRadius($r('app.float.card_radius'))

            Column({ space: 8 }) {
            this.cardTitle('i', '连接提示', '')
            Text('使用电脑局域网 IP，并保持 8000 端口服务运行。')
              .fontSize(12).fontColor($r('app.color.text_secondary'))
          }
          .alignItems(HorizontalAlign.Start)
          .width('100%').padding(18)
          .backgroundColor($r('app.color.surface'))
            .borderRadius($r('app.float.card_radius'))
          }

          if (this.activeSection === 'security') {
            Button('退出当前账号')
            .width('100%').height(48)
            .backgroundColor($r('app.color.danger_soft')).fontColor($r('app.color.danger'))
            .onClick(this.onLogout)
            .accessibilityText('退出当前账号')
          }
        }
        .width('100%')
        .padding({ left: 16, right: 16, top: 18, bottom: 30 })
      }
      .width('100%').height('100%')
      .scrollBar(BarState.Off)
      .edgeEffect(EdgeEffect.Spring)
    }
    .width('100%').height('100%')
    .backgroundColor($r('app.color.page_background'))
  }
}
