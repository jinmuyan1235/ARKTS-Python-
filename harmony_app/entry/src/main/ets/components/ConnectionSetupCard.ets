export function connectionNeedsSetup(apiBaseUrl: string, apiKey: string): boolean {
  return apiBaseUrl.trim().length === 0 || apiKey.trim().length === 0
}

@Component
export struct ConnectionSetupCard {
  @Prop apiBaseUrl: string = ''
  @Prop apiKey: string = ''
  @Prop serviceSummary: string = ''
  @Prop checking: boolean = false
  @Prop saving: boolean = false
  @Prop forceExpanded: boolean = false
  @Prop showActions: boolean = false
  @State expanded: boolean = false
  onApiBaseUrlChanged: (value: string) => void = (_value: string) => {}
  onApiKeyChanged: (value: string) => void = (_value: string) => {}
  onCheck: () => void = () => {}
  onSave: () => void = () => {}

  private shouldShowForm(): boolean {
    return this.forceExpanded || this.expanded || connectionNeedsSetup(this.apiBaseUrl, this.apiKey)
  }

  build() {
    Column({ space: 10 }) {
      Row({ space: 10 }) {
        Text('⚗')
          .fontSize(20)
          .fontColor($r('app.color.primary'))
          .textAlign(TextAlign.Center)
          .width(36)
          .height(36)
          .backgroundColor($r('app.color.primary_soft'))
          .borderRadius(12)
        Column({ space: 2 }) {
          Text('电脑端识别服务')
            .fontSize(14)
            .fontWeight(FontWeight.Medium)
            .fontColor($r('app.color.text_primary'))
          Text(this.apiBaseUrl.trim().length > 0 ? this.apiBaseUrl : '首次使用需要先完成连接')
            .fontSize(10)
            .fontColor($r('app.color.text_secondary'))
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
            .width('100%')
        }
        .alignItems(HorizontalAlign.Start)
        .layoutWeight(1)
        if (!this.forceExpanded) {
          Text(this.shouldShowForm() ? '收起' : '配置')
            .fontSize(12)
            .fontWeight(FontWeight.Medium)
            .fontColor($r('app.color.primary'))
        }
      }
      .width('100%')
      .onClick(() => {
        if (!this.forceExpanded && !connectionNeedsSetup(this.apiBaseUrl, this.apiKey)) {
          this.expanded = !this.expanded
        }
      })
      .accessibilityText(this.shouldShowForm() ? '电脑端识别服务配置已展开' : '展开电脑端识别服务配置')

      if (this.shouldShowForm()) {
        if (connectionNeedsSetup(this.apiBaseUrl, this.apiKey)) {
          Text('首次连接：填写电脑服务地址和启动服务时设置的 API 密钥，然后保存或登录。')
            .fontSize(11)
            .lineHeight(17)
            .fontColor($r('app.color.text_body'))
            .width('100%')
            .padding(10)
            .backgroundColor($r('app.color.warning_soft'))
            .borderRadius(10)
        }
        TextInput({ text: this.apiBaseUrl, placeholder: '服务地址，例如 http://192.168.1.8:8000' })
          .width('100%')
          .height(44)
          .type(InputType.Normal)
          .backgroundColor($r('app.color.surface_soft'))
          .onChange((value: string) => {
            this.expanded = true
            this.onApiBaseUrlChanged(value)
          })
          .accessibilityText('电脑端识别服务地址')
        TextInput({ text: this.apiKey, placeholder: 'API 密钥' })
          .width('100%')
          .height(44)
          .type(InputType.Password)
          .backgroundColor($r('app.color.surface_soft'))
          .onChange((value: string) => {
            this.expanded = true
            this.onApiKeyChanged(value)
          })
          .accessibilityText('电脑端识别服务 API 密钥')
        if (this.serviceSummary.length > 0) {
          Text(this.serviceSummary)
            .fontSize(11)
            .fontColor(this.serviceSummary.startsWith('已连接') ?
              $r('app.color.success') : $r('app.color.text_secondary'))
            .maxLines(2)
            .width('100%')
        }
        if (this.showActions) {
          Row({ space: 10 }) {
            Button(this.checking ? '检测中…' : '检测连接')
              .layoutWeight(1)
              .height(44)
              .enabled(!this.checking && !this.saving)
              .backgroundColor($r('app.color.surface_muted'))
              .fontColor($r('app.color.primary'))
              .onClick(this.onCheck)
            Button(this.saving ? '保存中…' : '保存连接')
              .layoutWeight(1)
              .height(44)
              .enabled(!this.checking && !this.saving)
              .backgroundColor($r('app.color.primary'))
              .fontColor($r('app.color.text_on_primary'))
              .onClick(this.onSave)
          }
          .width('100%')
        }
      }
    }
    .alignItems(HorizontalAlign.Start)
    .width('100%')
    .padding(14)
    .backgroundColor($r('app.color.primary_faint'))
    .border({ width: 1, color: $r('app.color.border') })
    .borderRadius(14)
  }
}
