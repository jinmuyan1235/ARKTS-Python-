import { HistoryItem, UserProfile } from '../model/VisionResult'
import { AppEmptyState, AppErrorState, LoadingSkeleton, MoleculeBackdrop, StatusPill } from './CommonStates'
import { PageHeader } from './PageChrome'

@Component
export struct HistoryView {
  @Prop currentUser: UserProfile | undefined = undefined
  @Prop members: UserProfile[] = []
  @Prop items: HistoryItem[] = []
  @Link query: string
  @Prop status: string = 'all'
  @Prop favoritesOnly: boolean = false
  @Prop scope: string = 'all'
  @Prop ownerUserId: string = ''
  @Prop loading: boolean = false
  @Prop errorMessage: string = ''
  @Prop hasMore: boolean = false
  onOpenSettings: () => void = () => {}
  onReload: (reset: boolean) => void = (_reset: boolean) => {}
  onFiltersChanged: (scope: string, ownerUserId: string, status: string, favoritesOnly: boolean) => void =
    (_scope: string, _ownerUserId: string, _status: string, _favoritesOnly: boolean) => {}
  onToggleFavorite: (item: HistoryItem) => void = (_item: HistoryItem) => {}
  onOpenResult: (analysisId: string) => void = (_analysisId: string) => {}
  onDelete: (item: HistoryItem) => void = (_item: HistoryItem) => {}

  @Builder
  private scopeChip(label: string, scope: string, ownerUserId: string) {
    Button(label)
      .fontSize(13)
      .fontWeight(this.scope === scope && this.ownerUserId === ownerUserId ? FontWeight.Medium : FontWeight.Regular)
      .fontColor(this.scope === scope && this.ownerUserId === ownerUserId ?
        $r('app.color.text_on_primary') : $r('app.color.text_body'))
      .height(44)
      .padding({ left: 14, right: 14 })
      .backgroundColor(this.scope === scope && this.ownerUserId === ownerUserId ?
        $r('app.color.primary') : $r('app.color.surface_muted'))
      .border({ width: 1, color: this.scope === scope && this.ownerUserId === ownerUserId ?
        $r('app.color.primary') : $r('app.color.border') })
      .borderRadius(22)
      .margin({ right: 8, bottom: 8 })
      .stateEffect(true)
      .onClick(() => this.onFiltersChanged(scope, ownerUserId, this.status, this.favoritesOnly))
      .accessibilityText(`${this.scope === scope && this.ownerUserId === ownerUserId ? '已选择' : '选择'}${label}`)
  }

  @Builder
  private statusChip(label: string, status: string) {
    Button(label)
      .fontSize(13)
      .fontWeight(this.status === status ? FontWeight.Medium : FontWeight.Regular)
      .fontColor(this.status === status ? $r('app.color.text_on_primary') : $r('app.color.text_body'))
      .height(44)
      .padding({ left: 14, right: 14 })
      .backgroundColor(this.status === status ? $r('app.color.primary') : $r('app.color.surface_muted'))
      .border({ width: 1, color: this.status === status ? $r('app.color.primary') : $r('app.color.border') })
      .borderRadius(22)
      .margin({ right: 8, bottom: 8 })
      .stateEffect(true)
      .onClick(() => this.onFiltersChanged(this.scope, this.ownerUserId, status, this.favoritesOnly))
      .accessibilityText(`${this.status === status ? '已选择' : '选择'}${label}`)
  }

  build() {
    Stack() {
      MoleculeBackdrop()
      Column({ space: 12 }) {
        PageHeader({
          title: '团队历史',
          subtitle: '共享每一次识别、校验、确认与收藏。',
          currentUser: this.currentUser,
          onOpenSettings: this.onOpenSettings
        })

        Row({ space: 8 }) {
          TextInput({ text: this.query, placeholder: '搜索文件名或 SMILES' })
            .layoutWeight(1).height(46)
            .backgroundColor($r('app.color.surface'))
            .border({ width: 1, color: $r('app.color.border') })
            .onChange((value: string) => this.query = value)
            .onSubmit(() => this.onReload(true))
            .accessibilityText('搜索团队历史')
          Button('搜索')
            .height(46)
            .fontColor($r('app.color.text_on_primary'))
            .backgroundColor($r('app.color.primary'))
            .onClick(() => this.onReload(true))
        }
        .width('100%')

        Flex({ direction: FlexDirection.Row, wrap: FlexWrap.Wrap, justifyContent: FlexAlign.Start }) {
            this.scopeChip('全组', 'all', '')
            this.scopeChip('我的', 'mine', '')
            ForEach(this.members, (member: UserProfile) => {
              this.scopeChip(member.displayName, 'all', member.userId)
            }, (member: UserProfile) => member.userId)
        }
        .width('100%')

        Flex({ direction: FlexDirection.Row, wrap: FlexWrap.Wrap, justifyContent: FlexAlign.Start }) {
            this.statusChip('全部', 'all')
            this.statusChip('成功', 'success')
            this.statusChip('待复核', 'review_needed')
            this.statusChip('失败', 'failed')
            Row({ space: 6 }) {
              Text('★').fontSize(14).fontColor($r('app.color.favorite'))
              Text('仅收藏').fontSize(13).fontColor($r('app.color.text_body'))
              Toggle({ type: ToggleType.Switch, isOn: this.favoritesOnly })
                .width(40)
                .onChange((value: boolean) => {
                  this.onFiltersChanged(this.scope, this.ownerUserId, this.status, value)
                })
            }
            .height(40)
            .padding({ left: 10, right: 10 })
            .backgroundColor($r('app.color.surface_muted'))
            .borderRadius(17)
            .margin({ right: 8, bottom: 8 })
        }
        .width('100%')

        if (this.loading && this.items.length === 0) {
          Scroll() { LoadingSkeleton({ rows: 4 }) }.layoutWeight(1).scrollBar(BarState.Off)
        } else if (this.errorMessage.length > 0 && this.items.length === 0) {
          Column() {
            AppErrorState({ title: '历史加载失败', message: this.errorMessage, onRetry: () => this.onReload(true) })
          }
          .justifyContent(FlexAlign.Center)
          .layoutWeight(1)
        } else if (this.items.length === 0) {
          Column() {
            AppEmptyState({ symbol: '◷', title: '暂无团队历史', message: '识别图片或分析 SMILES 后，报告会安全地保存在这里。' })
          }
          .justifyContent(FlexAlign.Center)
          .layoutWeight(1)
        } else {
          List({ space: 10 }) {
            ForEach(this.items, (item: HistoryItem) => {
              ListItem() {
                Column({ space: 9 }) {
                  Row({ space: 8 }) {
                    StatusPill({ symbol: item.inputType === 'image' ? '▧' : '⌬',
                      label: item.inputType === 'image' ? '图片' : 'SMILES', tone: 'primary' })
                    StatusPill({ symbol: item.needsReview ? '!' : '✓',
                      label: item.status === 'success' ? (item.needsReview ? '待复核' : '已完成') : '失败',
                      tone: item.status !== 'success' ? 'danger' : (item.needsReview ? 'warning' : 'success') })
                    Blank()
                    Text(item.isFavorite ? '★' : '☆')
                      .fontSize(25)
                      .fontColor(item.isFavorite ? $r('app.color.favorite') : $r('app.color.text_tertiary'))
                      .onClick(() => this.onToggleFavorite(item))
                      .accessibilityText(item.isFavorite ? '取消收藏' : '收藏')
                  }
                  .width('100%')
                  Text(item.smiles.length > 0 ? item.smiles : '无有效 SMILES')
                    .fontSize(15).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
                    .maxLines(2).textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
                  Row({ space: 7 }) {
                    Text(item.createdBy === null ? '历史数据' : item.createdBy.displayName)
                      .fontSize(12).fontColor(item.createdBy === null ? $r('app.color.text_tertiary') : $r('app.color.primary'))
                    if (item.createdBy !== null && item.createdBy.role.length > 0) {
                      Text(item.createdBy.role)
                        .fontSize(10).fontColor($r('app.color.text_secondary'))
                        .padding({ left: 7, right: 7, top: 3, bottom: 3 })
                        .backgroundColor($r('app.color.surface_muted')).borderRadius(9)
                    }
                    Blank()
                    Text(item.createdAt.replace('T', ' ').substring(0, 16))
                      .fontSize(11).fontColor($r('app.color.text_tertiary'))
                  }
                  .width('100%')
                  Row({ space: 10 }) {
                    Button('查看报告')
                      .height(44).layoutWeight(1)
                      .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.primary'))
                      .onClick(() => this.onOpenResult(item.analysisId))
                    Button('移除索引')
                      .height(44).layoutWeight(1)
                      .backgroundColor($r('app.color.danger_soft')).fontColor($r('app.color.danger'))
                      .onClick(() => this.onDelete(item))
                  }
                  .width('100%')
                }
                .alignItems(HorizontalAlign.Start)
                .width('100%')
                .padding(15)
                .backgroundColor($r('app.color.surface'))
                .border({ width: 1, color: $r('app.color.border') })
                .borderRadius($r('app.float.card_radius'))
              }
            }, (item: HistoryItem) => item.analysisId)

            if (this.hasMore) {
              ListItem() {
                Button(this.loading ? '加载中…' : '加载更多')
                  .width('100%').height(44).enabled(!this.loading)
                  .backgroundColor($r('app.color.surface_muted')).fontColor($r('app.color.primary'))
                  .onClick(() => this.onReload(false))
              }
            }
          }
          .layoutWeight(1)
          .width('100%')
          .scrollBar(BarState.Off)
          .edgeEffect(EdgeEffect.Spring)
        }
      }
      .alignItems(HorizontalAlign.Start)
      .width('100%').height('100%')
      .padding({ left: 16, right: 16, top: 18, bottom: 12 })
    }
    .width('100%').height('100%')
    .backgroundColor($r('app.color.page_background'))
  }
}
