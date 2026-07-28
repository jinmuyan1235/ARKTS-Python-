@Component
export struct AppShell {
  @Link currentTab: number
  @BuilderParam recognition: () => void
  @BuilderParam smiles: () => void
  @BuilderParam history: () => void
  @BuilderParam settings: () => void
  onTabChanged: (index: number) => void = (_index: number) => {}

  private previewTab(index: number): void {
    this.currentTab = index
  }

  private commitTab(index: number): void {
    this.currentTab = index
    this.onTabChanged(index)
  }

  @Builder
  private tabBar(icon: Resource, label: string, index: number) {
    Column({ space: 3 }) {
      Image(icon)
        .width(22)
        .height(22)
        .objectFit(ImageFit.Contain)
        .fillColor(this.currentTab === index ? $r('app.color.primary') : $r('app.color.text_secondary'))
        .accessibilityLevel('no')
        .constraintSize({ minWidth: 22, maxWidth: 22, minHeight: 22, maxHeight: 22 })
      Text(label)
        .fontSize(10)
        .lineHeight(14)
        .fontWeight(this.currentTab === index ? FontWeight.Medium : FontWeight.Regular)
        .fontColor(this.currentTab === index ? $r('app.color.primary') : $r('app.color.text_secondary'))
    }
    .justifyContent(FlexAlign.Center)
    .alignItems(HorizontalAlign.Center)
    .width(72)
    .height(50)
    .backgroundColor(this.currentTab === index ? $r('app.color.primary_soft') : Color.Transparent)
    .borderRadius(17)
    .animation({ duration: 180, curve: Curve.EaseOut })
    .accessibilityText(`${label}页面`)
  }

  build() {
    Tabs({ barPosition: BarPosition.End, index: this.currentTab }) {
      TabContent() {
        this.recognition()
      }
      .tabBar(this.tabBar($r('app.media.nav_recognition'), '识别', 0))

      TabContent() {
        this.smiles()
      }
      .tabBar(this.tabBar($r('app.media.nav_smiles'), 'SMILES', 1))

      TabContent() {
        this.history()
      }
      .tabBar(this.tabBar($r('app.media.nav_history'), '历史', 2))

      TabContent() {
        this.settings()
      }
      .tabBar(this.tabBar($r('app.media.nav_settings'), '设置', 3))
    }
    .barHeight(66)
    .barBackgroundColor($r('app.color.nav_background'))
    .scrollable(false)
    .animationDuration(140)
    .onAnimationStart((_index: number, targetIndex: number) => this.previewTab(targetIndex))
    .onChange((index: number) => this.commitTab(index))
    .width('100%')
    .height('100%')
  }
}
