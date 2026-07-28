import { UserProfile } from '../model/VisionResult'

function userInitial(user: UserProfile | undefined): string {
  if (user === undefined) {
    return 'M'
  }
  const source = user.displayName.trim().length > 0 ? user.displayName : user.username
  return source.length > 0 ? source.substring(0, 1).toUpperCase() : 'M'
}

@Component
export struct ProfileAvatar {
  @Prop user: UserProfile | undefined = undefined
  @Prop avatarSize: number = 40

  build() {
    if (this.user !== undefined && this.user.avatarUrl !== null) {
      Image(this.user.avatarUrl)
        .width(this.avatarSize)
        .height(this.avatarSize)
        .objectFit(ImageFit.Cover)
        .borderRadius(this.avatarSize / 2)
        .border({ width: 2, color: $r('app.color.border') })
        .accessibilityText(`${this.user.displayName}的头像`)
    } else {
      Text(userInitial(this.user))
        .fontSize(this.avatarSize * 0.42)
        .fontWeight(FontWeight.Bold)
        .fontColor($r('app.color.text_on_primary'))
        .textAlign(TextAlign.Center)
        .width(this.avatarSize)
        .height(this.avatarSize)
        .backgroundColor($r('app.color.primary'))
        .borderRadius(this.avatarSize / 2)
        .accessibilityText('默认成员头像')
    }
  }
}

@Component
export struct PageHeader {
  @Prop title: string = ''
  @Prop subtitle: string = ''
  @Prop currentUser: UserProfile | undefined = undefined
  onOpenSettings: () => void = () => {}

  build() {
    Row({ space: 11 }) {
      Column() {
        Image($r('app.media.molecule_mark'))
          .width(27)
          .height(27)
          .objectFit(ImageFit.Contain)
          .accessibilityLevel('no')
      }
        .justifyContent(FlexAlign.Center)
        .alignItems(HorizontalAlign.Center)
        .width(44)
        .height(44)
        .backgroundColor($r('app.color.primary_soft'))
        .borderRadius(13)
        .accessibilityText('分子结构标志')
      Column({ space: 3 }) {
        Text(this.title)
          .fontSize($r('app.float.title_font_size'))
          .fontWeight(FontWeight.Bold)
          .fontColor($r('app.color.text_primary'))
        Text(this.subtitle)
          .fontSize($r('app.float.caption_font_size'))
          .fontColor($r('app.color.text_secondary'))
          .maxLines(2)
      }
      .alignItems(HorizontalAlign.Start)
      .layoutWeight(1)
      ProfileAvatar({ user: this.currentUser, avatarSize: 40 })
    }
    .alignItems(VerticalAlign.Center)
    .width('100%')
    .onClick(this.onOpenSettings)
    .accessibilityText(`${this.title}，打开账号设置`)
  }
}
