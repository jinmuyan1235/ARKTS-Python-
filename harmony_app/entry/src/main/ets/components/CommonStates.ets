@Component
export struct MoleculeBackdrop {
  build() {
    Image($r('app.media.lab_molecule_bg'))
      .width('100%')
      .height('100%')
      .objectFit(ImageFit.Cover)
      .accessibilityLevel('no')
  }
}

@Component
export struct SectionHeader {
  @Prop symbol: string = '⌬'
  @Prop title: string = ''
  @Prop caption: string = ''

  build() {
    Row({ space: 10 }) {
      Text(this.symbol)
        .fontSize(16)
        .fontColor($r('app.color.primary'))
        .textAlign(TextAlign.Center)
        .width(32)
        .height(32)
        .backgroundColor($r('app.color.primary_soft'))
        .borderRadius(10)
      Column({ space: 2 }) {
        Text(this.title)
          .fontSize($r('app.float.section_font_size'))
          .fontWeight(FontWeight.Medium)
          .fontColor($r('app.color.text_primary'))
        if (this.caption.length > 0) {
          Text(this.caption)
            .fontSize($r('app.float.caption_font_size'))
            .fontColor($r('app.color.text_secondary'))
        }
      }
      .alignItems(HorizontalAlign.Start)
      .layoutWeight(1)
    }
    .width('100%')
  }
}

@Component
export struct StatusPill {
  @Prop symbol: string = '·'
  @Prop label: string = ''
  @Prop tone: string = 'neutral'

  private pillForeground(): ResourceColor {
    if (this.tone === 'success') return $r('app.color.success')
    if (this.tone === 'warning') return $r('app.color.warning')
    if (this.tone === 'danger') return $r('app.color.danger')
    if (this.tone === 'primary') return $r('app.color.primary')
    return $r('app.color.text_secondary')
  }

  private pillBackground(): ResourceColor {
    if (this.tone === 'success') return $r('app.color.success_soft')
    if (this.tone === 'warning') return $r('app.color.warning_soft')
    if (this.tone === 'danger') return $r('app.color.danger_soft')
    if (this.tone === 'primary') return $r('app.color.primary_soft')
    return $r('app.color.surface_muted')
  }

  build() {
    Row({ space: 5 }) {
      Text(this.symbol).fontSize(12).fontWeight(FontWeight.Bold).fontColor(this.pillForeground())
      Text(this.label).fontSize(11).fontWeight(FontWeight.Medium).fontColor(this.pillForeground())
    }
    .padding({ left: 9, right: 9, top: 5, bottom: 5 })
    .backgroundColor(this.pillBackground())
    .borderRadius(12)
    .accessibilityText(this.label)
  }
}

@Component
export struct CompletionBanner {
  @Prop message: string = ''
  onView: () => void = () => {}
  onDismiss: () => void = () => {}

  build() {
    Row({ space: 10 }) {
      Text('✓')
        .fontSize(18)
        .fontWeight(FontWeight.Bold)
        .fontColor($r('app.color.text_on_primary'))
        .width(30)
        .height(30)
        .textAlign(TextAlign.Center)
        .backgroundColor($r('app.color.success'))
        .borderRadius(15)
      Column({ space: 2 }) {
        Text(this.message).fontSize(14).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
        Text('任务已保存到团队历史').fontSize(11).fontColor($r('app.color.text_secondary'))
      }
      .alignItems(HorizontalAlign.Start)
      .layoutWeight(1)
      Button('查看结果')
        .height(36)
        .fontSize(12)
        .fontColor($r('app.color.text_on_primary'))
        .backgroundColor($r('app.color.primary'))
        .onClick(this.onView)
      Text('×')
        .fontSize(22)
        .fontColor($r('app.color.text_secondary'))
        .onClick(this.onDismiss)
        .accessibilityText('关闭任务完成提醒')
    }
    .width('100%')
    .padding(12)
    .backgroundColor($r('app.color.success_soft'))
    .border({ width: 1, color: $r('app.color.border') })
    .borderRadius(16)
    .shadow({ radius: 10, color: $r('app.color.shadow'), offsetY: 3 })
  }
}

@Component
export struct AppEmptyState {
  @Prop symbol: string = '⌬'
  @Prop title: string = '暂无数据'
  @Prop message: string = ''

  build() {
    Column({ space: 8 }) {
      Text(this.symbol).fontSize(34).fontColor($r('app.color.primary'))
      Text(this.title).fontSize(17).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
      Text(this.message)
        .fontSize(13)
        .fontColor($r('app.color.text_secondary'))
        .textAlign(TextAlign.Center)
        .maxLines(3)
    }
    .justifyContent(FlexAlign.Center)
    .width('100%')
    .padding(24)
    .accessibilityGroup(true)
  }
}

@Component
export struct AppErrorState {
  @Prop title: string = '加载失败'
  @Prop message: string = '请检查服务后重试。'
  onRetry: () => void = () => {}

  build() {
    Column({ space: 10 }) {
      Text('!')
        .fontSize(22)
        .fontWeight(FontWeight.Bold)
        .fontColor($r('app.color.danger'))
        .textAlign(TextAlign.Center)
        .width(42)
        .height(42)
        .backgroundColor($r('app.color.danger_soft'))
        .borderRadius(14)
      Text(this.title).fontSize(17).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
      Text(this.message).fontSize(13).fontColor($r('app.color.text_secondary')).textAlign(TextAlign.Center)
      Button('重试')
        .height(44)
        .fontColor($r('app.color.text_on_primary'))
        .backgroundColor($r('app.color.primary'))
        .onClick(this.onRetry)
        .accessibilityText(`${this.title}，重试`)
    }
    .width('100%')
    .padding(20)
    .backgroundColor($r('app.color.surface'))
    .border({ width: 1, color: $r('app.color.border') })
    .borderRadius($r('app.float.card_radius'))
  }
}

@Component
export struct LoadingSkeleton {
  @Prop rows: number = 3
  @State pulseOpacity: number = 0.58

  aboutToAppear(): void {
    setTimeout(() => this.pulseOpacity = 1, 50)
  }

  build() {
    Column({ space: 10 }) {
      ForEach(Array.from({ length: this.rows }, (_item: undefined, index: number) => index), () => {
        Column({ space: 8 }) {
          Row().width('42%').height(12).backgroundColor($r('app.color.skeleton')).borderRadius(6)
          Row().width('88%').height(16).backgroundColor($r('app.color.skeleton_soft')).borderRadius(8)
          Row().width('66%').height(11).backgroundColor($r('app.color.skeleton_soft')).borderRadius(6)
        }
        .alignItems(HorizontalAlign.Start)
        .width('100%')
        .padding(14)
        .backgroundColor($r('app.color.surface'))
        .borderRadius(16)
      })
    }
    .width('100%')
    .opacity(this.pulseOpacity)
    .animation({ duration: 1200, curve: Curve.EaseInOut, iterations: -1, playMode: PlayMode.Alternate })
    .accessibilityText('内容正在加载')
  }
}
