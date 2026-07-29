import { UserProfile } from '../model/VisionResult'
import { TEAM_ROLE_OPTIONS } from '../model/TeamRoles'
import { ProfileAvatar } from './PageChrome'

@Component
export struct AuthView {
  @Prop authMode: string = 'login'
  @Prop accountType: string = 'user'
  @Prop username: string = ''
  @Prop displayName: string = ''
  @Prop role: string = ''
  @Prop password: string = ''
  @Prop passwordConfirm: string = ''
  @Prop apiBaseUrl: string = ''
  @Prop apiKey: string = ''
  @Prop serviceSummary: string = ''
  @Prop busy: boolean = false
  @Prop errorMessage: string = ''
  @Prop members: UserProfile[] = []
  @Prop membersLoading: boolean = false
  @Prop rememberPassword: boolean = false
  @Prop autoLogin: boolean = false
  @Prop autoLoginBusy: boolean = false
  @Prop rememberedUsernames: string[] = []
  @State serviceExpanded: boolean = false
  onSubmit: () => void = () => {}
  onAuthModeChanged: (value: string) => void = (_value: string) => {}
  onAccountTypeChanged: (value: string) => void = (_value: string) => {}
  onUsernameChanged: (value: string) => void = (_value: string) => {}
  onDisplayNameChanged: (value: string) => void = (_value: string) => {}
  onRoleChanged: (value: string) => void = (_value: string) => {}
  onPasswordChanged: (value: string) => void = (_value: string) => {}
  onPasswordConfirmChanged: (value: string) => void = (_value: string) => {}
  onApiBaseUrlChanged: (value: string) => void = (_value: string) => {}
  onApiKeyChanged: (value: string) => void = (_value: string) => {}
  onSelectMember: (username: string) => void = (_username: string) => {}
  onRememberPasswordChanged: (value: boolean) => void = (_value: boolean) => {}
  onAutoLoginChanged: (value: boolean) => void = (_value: boolean) => {}

  @Builder
  private fieldLabel(label: string) {
    Text(label)
      .fontSize(12)
      .fontWeight(FontWeight.Medium)
      .fontColor($r('app.color.text_body'))
      .width('100%')
  }

  build() {
    Stack() {
      Image($r('app.media.auth_chemistry_bg'))
        .width('100%')
        .height('100%')
        .objectFit(ImageFit.Cover)
        .accessibilityLevel('no')
      Scroll() {
        Column({ space: 10 }) {
          Column({ space: 3 }) {
            Text('MOLECULE VISION')
              .fontSize(22)
              .fontWeight(FontWeight.Bold)
              .fontColor($r('app.color.auth_text'))
              .letterSpacing(1.2)
            Text('从二维化学结构图到可验证的分子数据')
              .fontSize(11)
              .fontColor($r('app.color.auth_text_secondary'))
          }
          .width('100%')
          .margin({ top: 8 })

          Column({ space: 10 }) {
            Row() {
              Text('普通用户')
                .fontSize(12).fontWeight(FontWeight.Medium).textAlign(TextAlign.Center)
                .fontColor(this.accountType === 'user' ? $r('app.color.primary') : $r('app.color.text_secondary'))
                .layoutWeight(1).height(32)
                .backgroundColor(this.accountType === 'user' ? $r('app.color.surface') : Color.Transparent)
                .borderRadius(10).onClick(() => this.onAccountTypeChanged('user'))
              Text('开发者')
                .fontSize(12).fontWeight(FontWeight.Medium).textAlign(TextAlign.Center)
                .fontColor(this.accountType === 'developer' ? $r('app.color.primary') : $r('app.color.text_secondary'))
                .layoutWeight(1).height(32)
                .backgroundColor(this.accountType === 'developer' ? $r('app.color.surface') : Color.Transparent)
                .borderRadius(10).onClick(() => this.onAccountTypeChanged('developer'))
            }
            .width('100%').padding(3).backgroundColor($r('app.color.primary_faint')).borderRadius(12)

            Row() {
              Text('登录')
                .fontSize(15)
                .fontWeight(FontWeight.Medium)
                .fontColor(this.authMode === 'login' ? $r('app.color.text_on_primary') : $r('app.color.text_secondary'))
                .textAlign(TextAlign.Center)
                .layoutWeight(1)
                .height(38)
                .backgroundColor(this.authMode === 'login' ? $r('app.color.primary') : Color.Transparent)
                .borderRadius(12)
                .onClick(() => this.onAuthModeChanged('login'))
                .accessibilityText('切换到登录')
              Text(this.accountType === 'developer' ? '注册开发者' : '注册用户')
                .fontSize(15)
                .fontWeight(FontWeight.Medium)
                .fontColor(this.authMode === 'register' ? $r('app.color.text_on_primary') : $r('app.color.text_secondary'))
                .textAlign(TextAlign.Center)
                .layoutWeight(1)
                .height(38)
                .backgroundColor(this.authMode === 'register' ? $r('app.color.primary') : Color.Transparent)
                .borderRadius(12)
                .onClick(() => this.onAuthModeChanged('register'))
                .accessibilityText('切换到注册组员')
            }
            .width('100%')
            .padding(4)
            .backgroundColor($r('app.color.surface_muted'))
            .borderRadius(15)

            if (this.authMode === 'register') {
              this.fieldLabel(this.accountType === 'developer' ? '开发者资料' : '用户资料')
              TextInput({ text: this.displayName, placeholder: '姓名或显示名称' })
                .width('100%').height(43)
                .backgroundColor($r('app.color.surface_soft'))
                .onChange((value: string) => this.onDisplayNameChanged(value))
              if (this.accountType === 'developer') {
                this.fieldLabel('任务分类')
                Flex({ direction: FlexDirection.Row, wrap: FlexWrap.Wrap, justifyContent: FlexAlign.Start }) {
                  ForEach(TEAM_ROLE_OPTIONS, (option: string) => {
                    Text(option)
                      .fontSize(11)
                      .fontWeight(this.role === option ? FontWeight.Medium : FontWeight.Regular)
                      .fontColor(this.role === option ? $r('app.color.text_on_primary') : $r('app.color.text_body'))
                      .textAlign(TextAlign.Center).width('31%').height(32)
                      .backgroundColor(this.role === option ? $r('app.color.primary') : $r('app.color.surface_soft'))
                      .border({ width: 1, color: this.role === option ? $r('app.color.primary') : $r('app.color.border') })
                      .borderRadius(11).margin({ right: 6, bottom: 6 })
                      .onClick(() => this.onRoleChanged(option))
                  }, (option: string) => option)
                }.width('100%')
              }
            }

            if (this.authMode === 'login' && this.accountType === 'developer') {
              Row() {
                Text('选择组员').fontSize(12).fontWeight(FontWeight.Medium)
                  .fontColor($r('app.color.text_body'))
                Blank()
                Text(this.membersLoading ? '加载中…' : `${this.members.length} 人`)
                  .fontSize(11).fontColor($r('app.color.text_secondary'))
              }.width('100%')
              if (this.members.length > 0) {
                Scroll() {
                  Row({ space: 9 }) {
                    ForEach(this.members, (member: UserProfile) => {
                      Column({ space: 5 }) {
                        ProfileAvatar({ user: member, avatarSize: 38 })
                        Text(member.displayName).fontSize(12).fontWeight(FontWeight.Medium)
                          .fontColor(this.username === member.username ?
                            $r('app.color.primary') : $r('app.color.text_primary'))
                          .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
                        Text(member.role.length > 0 ? member.role : member.username).fontSize(9)
                          .fontColor($r('app.color.text_secondary')).maxLines(1)
                          .textOverflow({ overflow: TextOverflow.Ellipsis })
                        Text(this.rememberedUsernames.indexOf(member.username) >= 0 ? '已记住' : ' ')
                          .fontSize(9)
                          .fontColor(this.rememberedUsernames.indexOf(member.username) >= 0 ?
                            $r('app.color.success') : Color.Transparent)
                      }
                      .width(82).height(110)
                      .justifyContent(FlexAlign.Center)
                      .padding({ top: 7, bottom: 7, left: 5, right: 5 })
                      .backgroundColor(this.username === member.username ?
                        $r('app.color.primary_faint') : $r('app.color.surface_soft'))
                      .border({ width: 2,
                        color: this.username === member.username ? $r('app.color.primary') : $r('app.color.border') })
                      .borderRadius(15).onClick(() => this.onSelectMember(member.username))
                    }, (member: UserProfile) => member.userId)
                  }
                }.scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')
              }
            }

            this.fieldLabel('账号凭据')
            TextInput({ text: this.username, placeholder: '用户名（字母、数字或下划线）' })
              .width('100%').height(43)
              .backgroundColor($r('app.color.surface_soft'))
              .onChange((value: string) => this.onUsernameChanged(value))
            TextInput({ text: this.password, placeholder: '密码（至少 8 位）' })
              .width('100%').height(43)
              .backgroundColor($r('app.color.surface_soft'))
              .type(InputType.Password)
              .onChange((value: string) => this.onPasswordChanged(value))
            if (this.authMode === 'register') {
              TextInput({ text: this.passwordConfirm, placeholder: '再次输入密码' })
                .width('100%').height(43)
                .backgroundColor($r('app.color.surface_soft'))
                .type(InputType.Password)
                .onChange((value: string) => this.onPasswordConfirmChanged(value))
            }

            if (this.authMode === 'login') {
              Row() {
                Row({ space: 7 }) {
                  Toggle({ type: ToggleType.Checkbox, isOn: this.rememberPassword })
                    .onChange((value: boolean) => this.onRememberPasswordChanged(value))
                  Text('记住密码').fontSize(12).fontColor($r('app.color.text_primary'))
                }
                Blank()
                Row({ space: 7 }) {
                  Toggle({ type: ToggleType.Checkbox, isOn: this.autoLogin })
                    .enabled(this.rememberPassword)
                    .onChange((value: boolean) => this.onAutoLoginChanged(value))
                  Text('自动登录').fontSize(12)
                    .fontColor(this.rememberPassword ? $r('app.color.text_primary') : $r('app.color.text_tertiary'))
                }
              }.width('100%')
            }

            if (this.errorMessage.length > 0) {
              Row({ space: 8 }) {
                Text('!').fontSize(14).fontWeight(FontWeight.Bold).fontColor($r('app.color.danger'))
                Text(this.errorMessage).fontSize(12).fontColor($r('app.color.danger')).layoutWeight(1)
              }
              .width('100%')
              .padding(10)
              .backgroundColor($r('app.color.danger_soft'))
              .borderRadius(12)
              .accessibilityText(`账号操作失败：${this.errorMessage}`)
            }

            Button(this.busy ? '正在连接实验室服务…' : (this.authMode === 'register' ?
              (this.accountType === 'developer' ? '创建开发者账号' : '创建用户账号') : '登录工作台'))
              .width('100%')
              .height(46)
              .enabled(!this.busy)
              .fontColor($r('app.color.text_on_primary'))
              .backgroundColor($r('app.color.primary'))
              .onClick(this.onSubmit)
              .accessibilityText(this.authMode === 'register' ? '注册并进入工作台' : '登录工作台')

            Column({ space: 10 }) {
              Row({ space: 8 }) {
                Text('⚗').fontSize(20).fontColor($r('app.color.primary'))
                Column({ space: 2 }) {
                  Text(this.accountType === 'developer' ? '本地实验室服务' : '本地识别服务')
                    .fontSize(14).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
                  Text(this.accountType === 'developer' ?
                    (this.apiBaseUrl.length > 0 ? this.apiBaseUrl : '尚未配置') :
                    (this.serviceSummary.startsWith('已连接') ? '服务可用' : '服务暂不可用'))
                    .fontSize(10).fontColor($r('app.color.text_secondary')).maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
                }
                .alignItems(HorizontalAlign.Start)
                .layoutWeight(1)
                if (this.accountType === 'developer') {
                  Text(this.serviceExpanded ? '收起' : '配置')
                    .fontSize(12).fontColor($r('app.color.primary'))
                }
              }
              .width('100%')
              .height(38)
              .onClick(() => {
                if (this.accountType === 'developer') this.serviceExpanded = !this.serviceExpanded
              })
              .accessibilityText(this.accountType === 'developer' ?
                (this.serviceExpanded ? '收起服务配置' : '展开服务配置') : '识别服务状态')
              if (this.accountType === 'developer' && (this.serviceExpanded || this.apiKey.length === 0)) {
                TextInput({ text: this.apiBaseUrl, placeholder: '电脑服务地址' })
                  .width('100%').height(44)
                  .backgroundColor($r('app.color.surface'))
                  .onChange((value: string) => this.onApiBaseUrlChanged(value))
                TextInput({ text: this.apiKey, placeholder: '电脑启动命令中的 API 密钥' })
                  .width('100%').height(44)
                  .backgroundColor($r('app.color.surface'))
                  .type(InputType.Password)
                  .onChange((value: string) => this.onApiKeyChanged(value))
              }
              if (this.accountType === 'developer' && this.serviceSummary.length > 0) {
                Text(this.serviceSummary).fontSize(11)
                  .fontColor(this.serviceSummary.startsWith('已连接') ?
                    $r('app.color.success') : $r('app.color.warning')).width('100%')
              }
            }
            .alignItems(HorizontalAlign.Start)
            .width('100%')
            .padding({ left: 10, right: 10, top: 4, bottom: 8 })
            .backgroundColor($r('app.color.primary_faint'))
            .border({ width: 1, color: $r('app.color.border') })
            .borderRadius(14)
          }
          .alignItems(HorizontalAlign.Start)
          .width('100%')
          .padding(14)
          .backgroundColor($r('app.color.surface'))
          .border({ width: 1, color: $r('app.color.border') })
          .borderRadius(20)
          .shadow({ radius: 28, color: $r('app.color.shadow'), offsetY: 10 })
        }
        .width('100%')
        .padding({ left: 14, right: 14, bottom: 8 })
      }
      .width('100%')
      .height('100%')
      .scrollBar(BarState.Off)
      .edgeEffect(EdgeEffect.Spring)
      if (this.autoLoginBusy) {
        Column({ space: 14 }) {
          LoadingProgress().width(42).height(42).color($r('app.color.primary'))
          Text('正在自动登录').fontSize(18).fontWeight(FontWeight.Bold)
            .fontColor($r('app.color.text_primary'))
          Text(this.username.length > 0 ? `正在验证 ${this.username}` : '正在验证上次账号')
            .fontSize(12).fontColor($r('app.color.text_secondary'))
        }
        .justifyContent(FlexAlign.Center).width('82%').padding({ top: 30, bottom: 30 })
        .backgroundColor($r('app.color.surface')).borderRadius(22)
        .border({ width: 1, color: $r('app.color.border') })
        .shadow({ radius: 26, color: $r('app.color.shadow') })
      }
    }
    .width('100%')
    .height('100%')
    .backgroundColor($r('app.color.auth_background'))
  }
}
