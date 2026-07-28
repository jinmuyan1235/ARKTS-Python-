import { UserProfile } from '../model/VisionResult'
import { AppEmptyState, AppErrorState, LoadingSkeleton, MoleculeBackdrop, SectionHeader, StatusPill } from './CommonStates'
import { PageHeader, ProfileAvatar } from './PageChrome'

@Component
export struct SettingsView {
  @Prop currentUser: UserProfile | undefined = undefined
  @Prop members: UserProfile[] = []
  @Prop membersLoading: boolean = false
  @Prop membersError: string = ''
  @Link displayName: string
  @Link role: string
  @Link oldPassword: string
  @Link newPassword: string
  @Link newPasswordConfirm: string
  @Link apiBaseUrl: string
  @Link apiKey: string
  @Prop avatarBusy: boolean = false
  @Prop profileSaving: boolean = false
  @Prop passwordBusy: boolean = false
  @Prop savingSettings: boolean = false
  @Prop checking: boolean = false
  @Prop serviceSummary: string = ''
  onChooseAvatar: () => void = () => {}
  onSaveProfile: () => void = () => {}
  onChangePassword: () => void = () => {}
  onSaveSettings: () => void = () => {}
  onCheckService: () => void = () => {}
  onLogout: () => void = () => {}
  onOpenGuide: () => void = () => {}

  @Builder
  private cardTitle(symbol: string, title: string, caption: string) {
    SectionHeader({ symbol: symbol, title: title, caption: caption })
  }

  build() {
    Stack() {
      MoleculeBackdrop()
      Scroll() {
        Column({ space: 16 }) {
          PageHeader({
            title: '实验室设置',
            subtitle: '管理团队身份、安全会话与电脑端识别服务。',
            currentUser: this.currentUser,
            onOpenGuide: this.onOpenGuide
          })

          Row({ space: 12 }) {
            Text('?')
              .fontSize(21)
              .fontWeight(FontWeight.Bold)
              .fontColor($r('app.color.primary'))
              .textAlign(TextAlign.Center)
              .width(44)
              .height(44)
              .backgroundColor($r('app.color.primary_soft'))
              .borderRadius(14)
            Column({ space: 3 }) {
              Text('术语与项目指南')
                .fontSize(16).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
              Text('面向非化学信息学专家，了解结果字段和使用边界。')
                .fontSize(12).fontColor($r('app.color.text_secondary')).maxLines(2)
            }
            .alignItems(HorizontalAlign.Start)
            .layoutWeight(1)
            Text('查看 ›').fontSize(13).fontWeight(FontWeight.Medium).fontColor($r('app.color.primary'))
          }
          .alignItems(VerticalAlign.Center)
          .width('100%')
          .padding(16)
          .backgroundColor($r('app.color.surface'))
          .border({ width: 1, color: $r('app.color.border') })
          .borderRadius($r('app.float.card_radius'))
          .onClick(this.onOpenGuide)
          .accessibilityText('打开化学信息学术语与项目指南')

          if (this.currentUser !== undefined) {
            Column({ space: 14 }) {
              this.cardTitle('◉', '账号资料', `用户名 @${this.currentUser.username} 不可修改`)
              Row({ space: 13 }) {
                ProfileAvatar({ user: this.currentUser, avatarSize: 64 })
                Column({ space: 5 }) {
                  Text(this.currentUser.displayName)
                    .fontSize(19).fontWeight(FontWeight.Bold).fontColor($r('app.color.text_primary'))
                  Text(this.currentUser.role.length > 0 ? this.currentUser.role : '项目组成员')
                    .fontSize(12).fontColor($r('app.color.primary'))
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
                .onChange((value: string) => this.displayName = value)
              TextInput({ text: this.role, placeholder: '组内角色（仅展示）' })
                .width('100%').height(46).backgroundColor($r('app.color.surface_soft'))
                .onChange((value: string) => this.role = value)
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
                      Text(member.role.length > 0 ? member.role : '组员')
                        .fontSize(10).fontColor($r('app.color.text_secondary')).maxLines(1)
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

          Column({ space: 12 }) {
            this.cardTitle('◇', '密码与会话安全', '修改密码后其他旧会话会立即失效')
            Row({ space: 8 }) {
              StatusPill({ symbol: '◆', label: 'API 密钥：Asset Store', tone: 'success' })
              StatusPill({ symbol: '◆', label: '令牌：Asset Store', tone: 'success' })
            }
            .width('100%')
            TextInput({ text: this.oldPassword, placeholder: '当前密码' })
              .type(InputType.Password).height(46).backgroundColor($r('app.color.surface_soft'))
              .onChange((value: string) => this.oldPassword = value)
            TextInput({ text: this.newPassword, placeholder: '新密码（至少 8 位）' })
              .type(InputType.Password).height(46).backgroundColor($r('app.color.surface_soft'))
              .onChange((value: string) => this.newPassword = value)
            TextInput({ text: this.newPasswordConfirm, placeholder: '再次输入新密码' })
              .type(InputType.Password).height(46).backgroundColor($r('app.color.surface_soft'))
              .onChange((value: string) => this.newPasswordConfirm = value)
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

          Column({ space: 12 }) {
            this.cardTitle('⚗', '电脑端识别服务', '局域网连接 DECIMER、MolScribe 或演示后端')
            Text('服务地址').fontSize(12).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_body')).width('100%')
            TextInput({ text: this.apiBaseUrl, placeholder: 'http://192.168.5.13:8000' })
              .width('100%').height(46).type(InputType.Normal).backgroundColor($r('app.color.surface_soft'))
              .onChange((value: string) => this.apiBaseUrl = value)
            Text('API 密钥').fontSize(12).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_body')).width('100%')
            TextInput({ text: this.apiKey, placeholder: '与电脑启动命令中的 -ApiKey 一致' })
              .width('100%').height(46).type(InputType.Password).backgroundColor($r('app.color.surface_soft'))
              .onChange((value: string) => this.apiKey = value)
            Row() {
              StatusPill({ symbol: this.serviceSummary.startsWith('已连接') ? '✓' : '!', label: this.serviceSummary,
                tone: this.serviceSummary.startsWith('已连接') ? 'success' : 'warning' })
              Blank()
              Button(this.checking ? '检测中…' : '检测连接')
                .height(44).enabled(!this.checking)
                .backgroundColor($r('app.color.surface_muted')).fontColor($r('app.color.primary'))
                .onClick(this.onCheckService)
            }
            .width('100%')
            Button(this.savingSettings ? '正在保存…' : '保存连接设置')
              .width('100%').height(48).enabled(!this.savingSettings && !this.checking)
              .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.primary'))
              .onClick(this.onSaveSettings)
          }
          .alignItems(HorizontalAlign.Start)
          .width('100%').padding(18)
          .backgroundColor($r('app.color.surface'))
          .border({ width: 1, color: $r('app.color.border') })
          .borderRadius($r('app.float.card_radius'))

          Column({ space: 8 }) {
            this.cardTitle('i', '模拟器连接提示', '确保手机模拟器可以访问电脑')
            Text('• 使用电脑局域网 IP，不能使用 127.0.0.1。').fontSize(12).fontColor($r('app.color.text_secondary'))
            Text('• Windows 防火墙需允许 TCP 8000 端口。').fontSize(12).fontColor($r('app.color.text_secondary'))
            Text('• 保持电脑端 PowerShell 服务窗口运行。').fontSize(12).fontColor($r('app.color.text_secondary'))
          }
          .alignItems(HorizontalAlign.Start)
          .width('100%').padding(18)
          .backgroundColor($r('app.color.surface'))
          .borderRadius($r('app.float.card_radius'))

          Button('退出当前账号')
            .width('100%').height(48)
            .backgroundColor($r('app.color.danger_soft')).fontColor($r('app.color.danger'))
            .onClick(this.onLogout)
            .accessibilityText('退出当前账号')
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
