import { HistoryItem, UserProfile } from '../model/VisionResult'
import { AppEmptyState, AppErrorState, LoadingSkeleton, MoleculeBackdrop, StatusPill } from './CommonStates'
import { PageHeader } from './PageChrome'
import { formatReportTime } from '../services/UiPresentation'

@Component
export struct HistoryView {
  @Prop isDeveloper: boolean = false
  @Prop currentUser: UserProfile | undefined = undefined
  @Prop members: UserProfile[] = []
  @Prop items: HistoryItem[] = []
  @Prop query: string = ''
  @Prop status: string = 'all'
  @Prop favoritesOnly: boolean = false
  @Prop scope: string = 'all'
  @Prop ownerUserId: string = ''
  @Prop loading: boolean = false
  @Prop refreshing: boolean = false
  @Prop loadingMore: boolean = false
  @Prop errorMessage: string = ''
  @Prop hasMore: boolean = false
  @State expandedAnalysisId: string = ''
  onOpenSettings: () => void = () => {}
  onOpenGuide: () => void = () => {}
  onReload: (reset: boolean) => void = (_reset: boolean) => {}
  onRefresh: () => void = () => {}
  onClearFilters: () => void = () => {}
  onFiltersChanged: (scope: string, ownerUserId: string, status: string, favoritesOnly: boolean) => void =
    (_scope: string, _ownerUserId: string, _status: string, _favoritesOnly: boolean) => {}
  onToggleFavorite: (item: HistoryItem) => void = (_item: HistoryItem) => {}
  onOpenResult: (analysisId: string) => void = (_analysisId: string) => {}
  onDelete: (item: HistoryItem) => void = (_item: HistoryItem) => {}
  onQueryChanged: (value: string) => void = (_value: string) => {}

  private hasActiveFilters(): boolean {
    return this.query.trim().length > 0 || this.status !== 'all' || this.favoritesOnly ||
      (this.isDeveloper && (this.scope !== 'all' || this.ownerUserId.length > 0))
  }

  private reviewLabel(item: HistoryItem): string {
    if (item.inputType !== 'image') return '无需图片复核'
    if (item.confirmed) return '结构已确认'
    if (item.needsReview) return '等待复核'
    return '未要求复核'
  }

  private reviewTone(item: HistoryItem): string {
    if (item.confirmed) return 'success'
    if (item.needsReview) return 'warning'
    return 'neutral'
  }

  private analysisLabel(item: HistoryItem): string {
    if (item.status !== 'success') return '分析失败'
    if (item.decision === 'rejected') return '识别拒绝'
    return '分析成功'
  }

  private analysisTone(item: HistoryItem): string {
    if (item.status !== 'success') return 'danger'
    return item.decision === 'rejected' ? 'warning' : 'success'
  }

  private isExpanded(item: HistoryItem): boolean {
    return this.expandedAnalysisId === item.analysisId
  }

  private toggleExpanded(item: HistoryItem): void {
    this.expandedAnalysisId = this.isExpanded(item) ? '' : item.analysisId
  }

  private pillForeground(tone: string): ResourceColor {
    if (tone === 'success') return $r('app.color.success')
    if (tone === 'warning') return $r('app.color.warning')
    if (tone === 'danger') return $r('app.color.danger')
    if (tone === 'primary') return $r('app.color.primary')
    return $r('app.color.text_secondary')
  }

  private pillBackground(tone: string): ResourceColor {
    if (tone === 'success') return $r('app.color.success_soft')
    if (tone === 'warning') return $r('app.color.warning_soft')
    if (tone === 'danger') return $r('app.color.danger_soft')
    if (tone === 'primary') return $r('app.color.primary_soft')
    return $r('app.color.surface_muted')
  }

  @Builder
  private historyPill(symbol: string, label: string, tone: string, width: number) {
    Row({ space: 4 }) {
      Text(symbol)
        .width(13)
        .fontSize(11)
        .fontWeight(FontWeight.Bold)
        .fontColor(this.pillForeground(tone))
        .textAlign(TextAlign.Center)
      Text(label)
        .fontSize(11)
        .fontWeight(FontWeight.Medium)
        .fontColor(this.pillForeground(tone))
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
    }
    .width(width)
    .height(28)
    .justifyContent(FlexAlign.Center)
    .alignItems(VerticalAlign.Center)
    .backgroundColor(this.pillBackground(tone))
    .borderRadius(14)
    .accessibilityText(label)
  }

  @Builder
  private scopeChip(label: string, scope: string, ownerUserId: string) {
    Button(label)
      .fontSize(13)
      .fontWeight(this.scope === scope && this.ownerUserId === ownerUserId ? FontWeight.Medium : FontWeight.Regular)
      .fontColor(this.scope === scope && this.ownerUserId === ownerUserId ?
        $r('app.color.text_on_primary') : $r('app.color.text_body'))
      .height(36)
      .padding({ left: 12, right: 12 })
      .backgroundColor(this.scope === scope && this.ownerUserId === ownerUserId ?
        $r('app.color.primary') : $r('app.color.surface_muted'))
      .border({ width: 1, color: this.scope === scope && this.ownerUserId === ownerUserId ?
        $r('app.color.primary') : $r('app.color.border') })
      .borderRadius(18)
      .margin({ right: 6 })
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
      .height(36)
      .padding({ left: 12, right: 12 })
      .backgroundColor(this.status === status ? $r('app.color.primary') : $r('app.color.surface_muted'))
      .border({ width: 1, color: this.status === status ? $r('app.color.primary') : $r('app.color.border') })
      .borderRadius(18)
      .margin({ right: 6 })
      .stateEffect(true)
      .onClick(() => this.onFiltersChanged(this.scope, this.ownerUserId, status, this.favoritesOnly))
      .accessibilityText(`${this.status === status ? '已选择' : '选择'}${label}`)
  }

  @Builder
  private historyCard(item: HistoryItem) {
    Column({ space: 9 }) {
      Row() {
        Row({ space: 6 }) {
          this.historyPill(item.inputType === 'image' ? '▧' : '⌬',
            item.inputType === 'image' ? '图片' : 'SMILES', 'primary', 64)
          this.historyPill(item.status === 'success' && item.decision !== 'rejected' ? '✓' : '!',
            this.analysisLabel(item), this.analysisTone(item), 74)
          this.historyPill(item.confirmed ? '✓' : (item.needsReview ? '!' : '·'),
            this.reviewLabel(item), this.reviewTone(item), 94)
        }
        .layoutWeight(1)
        Text(item.isFavorite ? '★' : '☆')
          .fontSize(25).width(44).height(44).textAlign(TextAlign.Center)
          .fontColor(item.isFavorite ? $r('app.color.favorite') : $r('app.color.text_tertiary'))
          .onClick(() => this.onToggleFavorite(item))
          .accessibilityText(item.isFavorite ? '取消收藏' : '收藏')
      }
      .width('100%').alignItems(VerticalAlign.Top)
      if (item.filename.length > 0) {
        Text(item.filename).fontSize(12).fontColor($r('app.color.text_secondary')).maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
      }
      Text(item.smiles.length > 0 ? item.smiles : '无有效 SMILES')
        .fontSize(15).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
        .maxLines(this.isExpanded(item) ? 2 : 1)
        .textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
      Row({ space: 8 }) {
        Text(item.createdBy === null ? '历史数据' :
          (item.createdBy.role.length > 0 ? `${item.createdBy.displayName} · ${item.createdBy.role}` : item.createdBy.displayName))
          .fontSize(12).fontColor(item.createdBy === null ? $r('app.color.text_tertiary') : $r('app.color.primary'))
          .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis }).layoutWeight(1)
        Text(formatReportTime(item.createdAt))
          .fontSize(11).fontColor($r('app.color.text_tertiary')).maxLines(1).width(112)
          .textAlign(TextAlign.End)
        Text(this.isExpanded(item) ? '收起' : '详情')
          .fontSize(11).fontWeight(FontWeight.Medium).fontColor($r('app.color.primary'))
          .width(36).height(28).textAlign(TextAlign.Center)
          .onClick(() => this.toggleExpanded(item))
          .accessibilityText(this.isExpanded(item) ? '收起历史记录操作' : '展开历史记录操作')
      }
      .width('100%')
      if (this.isExpanded(item)) {
        Row({ space: 10 }) {
          Button('查看报告').height(44).layoutWeight(1)
            .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.primary'))
            .onClick(() => this.onOpenResult(item.analysisId))
          Button('从历史删除').height(44).layoutWeight(1)
            .backgroundColor($r('app.color.danger_soft')).fontColor($r('app.color.danger'))
            .onClick(() => this.onDelete(item))
        }
        .width('100%')
      }
    }
    .alignItems(HorizontalAlign.Start).width('100%').padding(15)
    .backgroundColor($r('app.color.surface'))
    .border({ width: 1, color: $r('app.color.border') })
    .borderRadius($r('app.float.card_radius'))
  }

  build() {
    Stack() {
      MoleculeBackdrop()
      Column({ space: 12 }) {
        PageHeader({
          title: this.isDeveloper ? '团队历史' : '我的历史',
          subtitle: this.isDeveloper ? '识别与复核记录' : '个人识别记录',
          currentUser: this.currentUser,
          onOpenSettings: this.onOpenSettings,
          onOpenGuide: this.onOpenGuide
        })

        Row({ space: 8 }) {
          TextInput({ text: this.query, placeholder: '搜索文件名或 SMILES' })
            .layoutWeight(1).height(46)
            .backgroundColor($r('app.color.surface'))
            .border({ width: 1, color: $r('app.color.border') })
            .onChange((value: string) => this.onQueryChanged(value))
            .onSubmit(() => this.onReload(true))
            .accessibilityText('搜索团队历史')
          Button('搜索')
            .height(46)
            .fontColor($r('app.color.text_on_primary'))
            .backgroundColor($r('app.color.primary'))
            .onClick(() => this.onReload(true))
        }
        .width('100%')

        if (this.isDeveloper) {
          Scroll() {
            Row() {
              this.scopeChip('全组', 'all', '')
              this.scopeChip('我的', 'mine', '')
              ForEach(this.members, (member: UserProfile) => {
                this.scopeChip(member.displayName, 'all', member.userId)
              }, (member: UserProfile) => member.userId)
            }.width('100%')
          }
          .width('100%').scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off)
          .edgeEffect(EdgeEffect.None)
        }

        Row({ space: 8 }) {
          Scroll() {
            Row() {
              this.statusChip('全部', 'all')
              this.statusChip('成功', 'success')
              this.statusChip('待复核', 'review_needed')
              this.statusChip('识别拒绝', 'rejected')
              this.statusChip('失败', 'failed')
            }
          }
          .layoutWeight(1).scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off)
          .edgeEffect(EdgeEffect.None)
          Row({ space: 5 }) {
            Text('★').fontSize(13).fontColor($r('app.color.favorite'))
            Text('收藏').fontSize(12).fontColor($r('app.color.text_body'))
            Toggle({ type: ToggleType.Switch, isOn: this.favoritesOnly })
              .width(36)
              .onChange((value: boolean) => {
                this.onFiltersChanged(this.scope, this.ownerUserId, this.status, value)
              })
          }
          .height(36).padding({ left: 8, right: 8 })
          .backgroundColor($r('app.color.surface_muted'))
          .border({ width: 1, color: $r('app.color.border') })
          .borderRadius(18)
        }
        .width('100%')

        if (this.loading && this.items.length === 0) {
          Scroll() { LoadingSkeleton({ rows: 4 }) }.layoutWeight(1).scrollBar(BarState.Off)
        } else if (this.errorMessage.length > 0 && this.items.length === 0) {
          Column() {
            AppErrorState({ title: '历史加载失败', message: this.errorMessage, onRetry: () => this.onReload(true) })
          }
          .justifyContent(FlexAlign.Center).layoutWeight(1)
        } else {
          Refresh({ refreshing: this.refreshing, promptText: '下拉刷新团队历史' }) {
            List({ space: 10 }) {
              if (this.items.length === 0) {
                ListItem() {
                  Column({ space: 12 }) {
                    AppEmptyState({
                      symbol: this.hasActiveFilters() ? '⌕' : '◷',
                      title: this.hasActiveFilters() ? '没有符合筛选条件的记录' : '暂无团队历史',
                      message: this.hasActiveFilters() ? '可以清除筛选后查看全部团队记录。' :
                        '识别图片或分析 SMILES 后，报告会安全地保存在这里。'
                    })
                    if (this.hasActiveFilters()) {
                      Button('清除全部筛选').height(44)
                        .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
                        .onClick(() => this.onClearFilters())
                    }
                  }
                  .justifyContent(FlexAlign.Center).width('100%').constraintSize({ minHeight: 320 })
                }
              } else {
                ForEach(this.items, (item: HistoryItem) => {
                  ListItem() { this.historyCard(item) }
                }, (item: HistoryItem) =>
                  `${item.analysisId}:${item.status}:${item.decision}:${item.confirmed}:${item.needsReview}:${item.isFavorite}`)
              }

              if (this.errorMessage.length > 0 && this.items.length > 0) {
                ListItem() {
                  Row({ space: 8 }) {
                    Text('部分记录加载失败').fontSize(12).fontColor($r('app.color.danger')).layoutWeight(1)
                    Button('重试').height(40).fontSize(12)
                      .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
                      .onClick(() => this.onReload(this.items.length === 0))
                  }
                  .width('100%').padding(10).backgroundColor($r('app.color.danger_soft')).borderRadius(12)
                }
              }

              if (this.loadingMore) {
                ListItem() {
                  Row({ space: 8 }) {
                    LoadingProgress().width(20).height(20).color($r('app.color.primary'))
                    Text('正在加载更多记录…').fontSize(12).fontColor($r('app.color.text_secondary'))
                  }
                  .justifyContent(FlexAlign.Center).width('100%').height(48)
                }
              } else if (this.hasMore) {
                ListItem() {
                  Text('继续上滑加载更多').fontSize(12).fontColor($r('app.color.text_tertiary'))
                    .width('100%').height(44).textAlign(TextAlign.Center)
                }
              } else if (this.items.length > 0) {
                ListItem() {
                  Text('已加载全部记录').fontSize(11).fontColor($r('app.color.text_tertiary'))
                    .width('100%').height(40).textAlign(TextAlign.Center)
                }
              }
            }
            .width('100%').scrollBar(BarState.Off).edgeEffect(EdgeEffect.Spring)
            .onReachEnd(() => {
              if (this.hasMore && !this.loadingMore && !this.refreshing) {
                this.onReload(false)
              }
            })
          }
          .layoutWeight(1).width('100%')
          .onRefreshing(() => this.onRefresh())
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
