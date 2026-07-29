import { FeedbackAuditRecord, FeedbackDetail, FeedbackItem } from '../model/FeedbackResult'
import { AppEmptyState, LoadingSkeleton, MoleculeBackdrop, SectionHeader, StatusPill } from './CommonStates'

export function feedbackStatusLabel(status: string): string {
  if (status === 'verified') return '已核验'
  if (status === 'rejected') return '已拒绝'
  if (status === 'returned') return '已退回'
  if (status === 'duplicate') return '重复项'
  if (status === 'license_unclear') return '许可不清'
  return '待审核'
}

@Component
export struct DatasetReviewView {
  @Prop items: FeedbackItem[] = []
  @Prop selected: FeedbackDetail | undefined = undefined
  @Prop status: string = 'pending'
  @Prop notes: string = ''
  @Prop loading: boolean = false
  @Prop busy: boolean = false
  @Prop errorMessage: string = ''
  onBack: () => void = () => {}
  onRefresh: () => void = () => {}
  onStatusChanged: (status: string) => void = (_status: string) => {}
  onSelect: (analysisId: string) => void = (_analysisId: string) => {}
  onNotesChanged: (value: string) => void = (_value: string) => {}
  onReview: (action: string) => void = (_action: string) => {}
  onExportManifest: () => void = () => {}

  @Builder
  private statusButton(value: string, label: string) {
    Button(label).height(36)
      .backgroundColor(this.status === value ? $r('app.color.primary') : $r('app.color.primary_soft'))
      .fontColor(this.status === value ? $r('app.color.text_on_primary') : $r('app.color.primary'))
      .onClick(() => this.onStatusChanged(value))
  }

  @Builder
  private itemRow(item: FeedbackItem) {
    Row({ space: 10 }) {
      if (item.imageUrl !== null) {
        Image(item.imageUrl).width(56).height(56).objectFit(ImageFit.Cover)
          .backgroundColor($r('app.color.surface_structure')).borderRadius(10)
      } else {
        Text('⌬').fontSize(22).width(56).height(56).textAlign(TextAlign.Center)
          .backgroundColor($r('app.color.surface_muted')).borderRadius(10)
      }
      Column({ space: 4 }) {
        Text(item.filename.length > 0 ? item.filename : item.analysisId)
          .fontSize(13).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary')).maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
        Text(item.correctedSmiles === null ? (item.predictedSmiles === null ? '--' : item.predictedSmiles) : item.correctedSmiles)
          .fontSize(11).fontColor($r('app.color.text_secondary')).maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
      }.alignItems(HorizontalAlign.Start).layoutWeight(1)
      StatusPill({ symbol: item.trainingEligibility.eligible ? '✓' : '!',
        label: feedbackStatusLabel(item.reviewStatus), tone: item.trainingEligibility.eligible ? 'success' : 'warning' })
    }
    .width('100%').padding(11).backgroundColor($r('app.color.surface'))
    .border({ width: 1, color: $r('app.color.border') }).borderRadius(13)
    .onClick(() => this.onSelect(item.analysisId))
  }

  @Builder
  private detailCard(data: FeedbackDetail) {
    Column({ space: 12 }) {
      SectionHeader({ symbol: '⌬', title: '反馈详情', caption: `修订 v${data.revision}` })
      if (data.imageUrl !== null) {
        Image(data.imageUrl).width('100%').height(220).objectFit(ImageFit.Contain)
          .backgroundColor($r('app.color.surface_structure')).borderRadius(12)
      }
      Column({ space: 5 }) {
        Text('模型预测').fontSize(11).fontColor($r('app.color.text_secondary'))
        Text(data.predictedSmiles === null ? '--' : data.predictedSmiles)
          .fontSize(13).fontColor($r('app.color.text_primary')).width('100%')
        Text('人工修正').fontSize(11).fontColor($r('app.color.text_secondary')).margin({ top: 4 })
        Text(data.correctedSmiles === null ? '--' : data.correctedSmiles)
          .fontSize(13).fontWeight(FontWeight.Medium).fontColor($r('app.color.primary')).width('100%')
      }.alignItems(HorizontalAlign.Start).width('100%')
      Flex({ direction: FlexDirection.Row, wrap: FlexWrap.Wrap, justifyContent: FlexAlign.Start }) {
        StatusPill({ symbol: '§', label: data.sourceLicense.length > 0 ? data.sourceLicense : '未提供许可',
          tone: data.reviewStatus === 'license_unclear' ? 'danger' : 'primary' })
        StatusPill({ symbol: data.trainingEligibility.eligible ? '✓' : '!', label: data.trainingEligibility.label,
          tone: data.trainingEligibility.eligible ? 'success' : 'warning' })
          .margin({ left: 8, bottom: 6 })
      }.width('100%')
      Text(`来源：${data.sourceReference.length > 0 ? data.sourceReference : '未填写'}`)
        .fontSize(11).fontColor($r('app.color.text_secondary')).width('100%')
      if (!data.trainingEligibility.eligible) {
        Column({ space: 4 }) {
          ForEach(data.trainingEligibility.reasons, (reason: string) => {
            Text(`• ${reason}`).fontSize(11).fontColor($r('app.color.warning')).width('100%')
          })
        }.alignItems(HorizontalAlign.Start).width('100%').padding(10)
          .backgroundColor($r('app.color.warning_soft')).borderRadius(10)
      }
      TextArea({ text: this.notes, placeholder: '审核备注（可选）' })
        .width('100%').height(88).backgroundColor($r('app.color.surface_soft'))
        .onChange((value: string) => this.onNotesChanged(value))
      Row({ space: 7 }) {
        Button('核验通过').layoutWeight(1).height(40).enabled(!this.busy)
          .backgroundColor($r('app.color.success')).fontColor($r('app.color.text_on_primary'))
          .onClick(() => this.onReview('approve'))
        Button('退回').layoutWeight(1).height(40).enabled(!this.busy)
          .backgroundColor($r('app.color.warning_soft')).fontColor($r('app.color.warning'))
          .onClick(() => this.onReview('return'))
        Button('拒绝').layoutWeight(1).height(40).enabled(!this.busy)
          .backgroundColor($r('app.color.danger_soft')).fontColor($r('app.color.danger'))
          .onClick(() => this.onReview('reject'))
      }.width('100%')
      Button('标记来源许可不清').width('100%').height(40).enabled(!this.busy)
        .backgroundColor($r('app.color.surface_muted')).fontColor($r('app.color.text_body'))
        .onClick(() => this.onReview('license_unclear'))
      if (data.history.length > 0) {
        Divider().color($r('app.color.divider'))
        SectionHeader({ symbol: '≡', title: '审核记录', caption: `${data.history.length} 条` })
        ForEach(data.history.slice().reverse(), (record: FeedbackAuditRecord) => {
          Column({ space: 3 }) {
            Text(`${record.operation} · ${record.reviewer.length > 0 ? record.reviewer : '系统'}`)
              .fontSize(11).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary')).width('100%')
            Text(`${record.oldStatus}${record.oldStatus.length > 0 ? ' → ' : ''}${record.newStatus}  ${record.createdAt}`)
              .fontSize(10).fontColor($r('app.color.text_secondary')).width('100%')
            if (record.notes.length > 0) {
              Text(record.notes).fontSize(10).fontColor($r('app.color.text_body')).width('100%')
            }
          }.alignItems(HorizontalAlign.Start).width('100%').padding(9)
            .backgroundColor($r('app.color.surface_soft')).borderRadius(9)
        })
      }
    }
    .alignItems(HorizontalAlign.Start).width('100%').padding(15)
    .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') }).borderRadius(16)
  }

  build() {
    Stack() {
      MoleculeBackdrop()
      Column() {
        Row({ space: 10 }) {
          Button('‹').width(44).height(44).backgroundColor($r('app.color.primary_soft'))
            .fontColor($r('app.color.primary')).onClick(this.onBack)
          Column({ space: 2 }) {
            Text('反馈数据审核').fontSize(20).fontWeight(FontWeight.Bold).fontColor($r('app.color.text_primary'))
            Text('DATASET REVIEW').fontSize(8).fontColor($r('app.color.text_secondary'))
          }.alignItems(HorizontalAlign.Start).layoutWeight(1)
          Text('刷新').fontSize(14).fontColor($r('app.color.primary')).onClick(this.onRefresh)
        }.width('100%').height(62).padding({ left: 10, right: 18 }).backgroundColor($r('app.color.nav_background'))
        Scroll() {
          Column({ space: 13 }) {
            Scroll() {
              Row({ space: 7 }) {
                this.statusButton('pending', '待审核')
                this.statusButton('verified', '已核验')
                this.statusButton('returned', '已退回')
                this.statusButton('all', '全部')
              }
            }.scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')
            Button(this.busy ? '生成中…' : '导出已核验训练 Manifest').width('100%').height(42)
              .enabled(!this.busy).backgroundColor($r('app.color.primary')).fontColor($r('app.color.text_on_primary'))
              .onClick(this.onExportManifest)
            Text('训练 Manifest 由后端门禁生成；移动端不能把待审核样本直接标记为训练数据。')
              .fontSize(11).lineHeight(17).fontColor($r('app.color.text_secondary')).width('100%')
            if (this.loading) {
              LoadingSkeleton({ rows: 4 })
            } else if (this.items.length === 0) {
              AppEmptyState({ symbol: '◎', title: '当前筛选无反馈', message: '切换审核状态或稍后刷新。' })
            } else {
              ForEach(this.items, (item: FeedbackItem) => this.itemRow(item), (item: FeedbackItem) => item.analysisId)
            }
            if (this.selected !== undefined) {
              this.detailCard(this.selected)
            }
            if (this.errorMessage.length > 0) {
              Text(this.errorMessage).fontSize(12).fontColor($r('app.color.danger')).width('100%')
                .padding(11).backgroundColor($r('app.color.danger_soft')).borderRadius(10)
            }
          }.width('100%').padding({ left: 16, right: 16, top: 15, bottom: 28 })
        }.width('100%').layoutWeight(1).scrollBar(BarState.Off)
      }.width('100%').height('100%')
    }.width('100%').height('100%').backgroundColor($r('app.color.page_background'))
  }
}
