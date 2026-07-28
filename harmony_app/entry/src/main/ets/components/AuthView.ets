@Component
export struct AuthView {
  @Prop authMode: string = 'login'
  @Prop username: string = ''
  @Prop displayName: string = ''
  @Prop role: string = ''
  @Prop password: string = ''
  @Prop passwordConfirm: string = ''
  @Prop apiBaseUrl: string = ''
  @Prop apiKey: string = ''
  @Prop busy: boolean = false
  @Prop errorMessage: string = ''
  @State serviceExpanded: boolean = false
  onSubmit: () => void = () => {}
  onAuthModeChanged: (value: string) => void = (_value: string) => {}
  onUsernameChanged: (value: string) => void = (_value: string) => {}
  onDisplayNameChanged: (value: string) => void = (_value: string) => {}
  onRoleChanged: (value: string) => void = (_value: string) => {}
  onPasswordChanged: (value: string) => void = (_value: string) => {}
  onPasswordConfirmChanged: (value: string) => void = (_value: string) => {}
  onApiBaseUrlChanged: (value: string) => void = (_value: string) => {}
  onApiKeyChanged: (value: string) => void = (_value: string) => {}

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
        Column({ space: 18 }) {
          Column({ space: 6 }) {
            Text('⌬')
              .fontSize(56)
              .fontWeight(FontWeight.Bold)
              .fontColor('#5EE6E9')
              .shadow({ radius: 18, color: '#6623C6D9' })
              .accessibilityText('Molecule Vision 分子标志')
            Text('MOLECULE VISION')
              .fontSize(26)
              .fontWeight(FontWeight.Bold)
              .fontColor($r('app.color.auth_text'))
              .letterSpacing(1.5)
            Text('从二维化学结构图到可验证的分子数据')
              .fontSize(13)
              .fontColor($r('app.color.auth_text_secondary'))
          }
          .width('100%')
          .margin({ top: 24 })

          Column({ space: 14 }) {
            Row() {
              Text('登录')
                .fontSize(15)
                .fontWeight(FontWeight.Medium)
                .fontColor(this.authMode === 'login' ? $r('app.color.text_on_primary') : $r('app.color.text_secondary'))
                .textAlign(TextAlign.Center)
                .layoutWeight(1)
                .height(44)
                .backgroundColor(this.authMode === 'login' ? $r('app.color.primary') : Color.Transparent)
                .borderRadius(12)
                .onClick(() => this.onAuthModeChanged('login'))
                .accessibilityText('切换到登录')
              Text('注册组员')
                .fontSize(15)
                .fontWeight(FontWeight.Medium)
                .fontColor(this.authMode === 'register' ? $r('app.color.text_on_primary') : $r('app.color.text_secondary'))
                .textAlign(TextAlign.Center)
                .layoutWeight(1)
                .height(44)
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
              this.fieldLabel('组员资料')
              TextInput({ text: this.displayName, placeholder: '姓名或显示名称' })
                .width('100%').height(48)
                .backgroundColor($r('app.color.surface_soft'))
                .onChange((value: string) => this.onDisplayNameChanged(value))
              TextInput({ text: this.role, placeholder: '组内角色，例如算法、前端、测试' })
                .width('100%').height(48)
                .backgroundColor($r('app.color.surface_soft'))
                .onChange((value: string) => this.onRoleChanged(value))
            }

            this.fieldLabel('账号凭据')
            TextInput({ text: this.username, placeholder: '用户名（字母、数字或下划线）' })
              .width('100%').height(48)
              .backgroundColor($r('app.color.surface_soft'))
              .onChange((value: string) => this.onUsernameChanged(value))
            TextInput({ text: this.password, placeholder: '密码（至少 8 位）' })
              .width('100%').height(48)
              .backgroundColor($r('app.color.surface_soft'))
              .type(InputType.Password)
              .onChange((value: string) => this.onPasswordChanged(value))
            if (this.authMode === 'register') {
              TextInput({ text: this.passwordConfirm, placeholder: '再次输入密码' })
                .width('100%').height(48)
                .backgroundColor($r('app.color.surface_soft'))
                .type(InputType.Password)
                .onChange((value: string) => this.onPasswordConfirmChanged(value))
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

            Button(this.busy ? '正在连接实验室服务…' :
              (this.authMode === 'register' ? '创建账号并进入' : '登录工作台'))
              .width('100%')
              .height(50)
              .enabled(!this.busy)
              .fontColor($r('app.color.text_on_primary'))
              .backgroundColor($r('app.color.primary'))
              .onClick(this.onSubmit)
              .accessibilityText(this.authMode === 'register' ? '注册并进入工作台' : '登录工作台')

            Column({ space: 10 }) {
              Row({ space: 8 }) {
                Text('⚗').fontSize(20).fontColor($r('app.color.primary'))
                Column({ space: 2 }) {
                  Text('本地实验室服务').fontSize(14).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
                  Text(this.apiBaseUrl.length > 0 ? this.apiBaseUrl : '尚未配置')
                    .fontSize(10).fontColor($r('app.color.text_secondary')).maxLines(1)
                }
                .alignItems(HorizontalAlign.Start)
                .layoutWeight(1)
                Text(this.serviceExpanded ? '收起' : '配置')
                  .fontSize(12).fontColor($r('app.color.primary'))
              }
              .width('100%')
              .height(44)
              .onClick(() => this.serviceExpanded = !this.serviceExpanded)
              .accessibilityText(this.serviceExpanded ? '收起服务配置' : '展开服务配置')
              if (this.serviceExpanded || this.apiKey.length === 0) {
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
              Text('数据、账号与模型都保存在你的电脑，不上传到云端。')
                .fontSize(11).fontColor($r('app.color.text_secondary')).width('100%')
            }
            .alignItems(HorizontalAlign.Start)
            .width('100%')
            .padding({ left: 12, right: 12, top: 6, bottom: 12 })
            .backgroundColor($r('app.color.primary_faint'))
            .border({ width: 1, color: $r('app.color.border') })
            .borderRadius(14)
          }
          .alignItems(HorizontalAlign.Start)
          .width('100%')
          .padding(20)
          .backgroundColor($r('app.color.surface'))
          .border({ width: 1, color: $r('app.color.border') })
          .borderRadius(24)
          .shadow({ radius: 28, color: $r('app.color.shadow'), offsetY: 10 })

          Text('C₉H₈O₄  •  OCSR  •  RDKit  •  ADMET')
            .fontSize(12)
            .fontColor($r('app.color.auth_text_secondary'))
            .margin({ bottom: 24 })
        }
        .width('100%')
        .padding({ left: 18, right: 18 })
      }
      .width('100%')
      .height('100%')
      .scrollBar(BarState.Off)
      .edgeEffect(EdgeEffect.Spring)
    }
    .width('100%')
    .height('100%')
    .backgroundColor($r('app.color.auth_background'))
  }
}
