import { BatchItemResult, BatchJob } from '../model/BatchResult'
import { UserProfile } from '../model/VisionResult'
import { MoleculeBackdrop, SectionHeader, StatusPill } from './CommonStates'
import { PageHeader } from './PageChrome'

interface BatchMetricItem {
  key: string
  label: string
  value: number
  tone: string
}

export function batchPercent(progress: number): number {
  return Math.max(0, Math.min(100, Math.round(progress * 100)))
}

export function batchCategoryLabel(category: string): string {
  if (category === 'accepted') return '已接受'
  if (category === 'accepted_with_warning') return '警告'
  if (category === 'review_needed') return '待复核'
  if (category === 'rejected') return '已拒绝'
  if (category === 'skipped') return '已跳过'
  return '失败'
}

function batchItemLabel(item: BatchItemResult): string {
  if (item.confirmed) return '已确认'
  if (item.reviewStatus === 'unable_to_confirm') return '无法确认'
  return batchCategoryLabel(item.category)
}

function batchItemTone(item: BatchItemResult): string {
  if (item.confirmed) return 'success'
  if (item.category === 'failed' || item.category === 'rejected') return 'danger'
  if (item.needsReview || item.reviewStatus === 'unable_to_confirm') return 'warning'
  return 'primary'
}

function batchStatusLabel(status: string): string {
  if (status === 'queued') return '等待处理'
  if (status === 'running') return '识别中'
  if (status === 'paused') return '已暂停'
  if (status === 'cancelling') return '正在取消'
  if (status === 'cancelled') return '已取消'
  if (status === 'completed') return '已完成'
  return '失败'
}

@Component
export struct BatchView {
  @Prop currentUser: UserProfile | undefined = undefined
  @Prop selectedUris: string[] = []
  @Prop job: BatchJob | undefined = undefined
  @Prop uploading: boolean = false
  @Prop controlBusy: boolean = false
  @Prop errorMessage: string = ''
  @Prop builtInImages: string[] = []
  onOpenSettings: () => void = () => {}
  onOpenGuide: () => void = () => {}
  onChooseImages: () => void = () => {}
  onUseBuiltInImages: () => void = () => {}
  onToggleBuiltInImage: (uri: string) => void = (_uri: string) => {}
  onClearSelection: () => void = () => {}
  onSubmit: () => void = () => {}
  onPause: () => void = () => {}
  onResume: () => void = () => {}
  onCancel: () => void = () => {}
  onRetryFailed: () => void = () => {}
  onOpenExports: (jobId: string) => void = (_jobId: string) => {}
  onOpenResult: (analysisId: string) => void = (_analysisId: string) => {}

  @Builder
  private categoryMetric(label: string, value: number, tone: string) {
    Column({ space: 3 }) {
      Text(`${value}`).fontSize(20).fontWeight(FontWeight.Bold)
        .fontColor(tone === 'danger' ? $r('app.color.danger') :
          (tone === 'warning' ? $r('app.color.warning') : $r('app.color.primary')))
      Text(label).fontSize(10).fontColor($r('app.color.text_secondary')).maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%').textAlign(TextAlign.Center)
    }
    .justifyContent(FlexAlign.Center)
    .alignItems(HorizontalAlign.Center)
    .layoutWeight(1)
    .height(62)
    .backgroundColor(tone === 'danger' ? $r('app.color.danger_soft') :
      (tone === 'warning' ? $r('app.color.warning_soft') : $r('app.color.primary_soft')))
    .borderRadius(12)
  }

  private categoryMetrics(job: BatchJob, secondRow: boolean): BatchMetricItem[] {
    if (secondRow) {
      return [
        { key: 'rejected', label: '已拒绝', value: job.categoryStats.rejected, tone: 'danger' },
        { key: 'failed', label: '失败', value: job.categoryStats.failed, tone: 'danger' },
        { key: 'skipped', label: '跳过', value: job.categoryStats.skipped, tone: 'warning' }
      ]
    }
    return [
      { key: 'accepted', label: '已接受', value: job.categoryStats.accepted, tone: 'primary' },
      { key: 'warning', label: '警告', value: job.categoryStats.acceptedWithWarning, tone: 'warning' },
      { key: 'review', label: '待复核', value: job.categoryStats.reviewNeeded, tone: 'warning' }
    ]
  }

  @Builder
  private resultRow(item: BatchItemResult) {
    Row({ space: 10 }) {
      Column() {
        Text(item.status === 'success' ? '✓' : '!')
          .fontSize(16).fontWeight(FontWeight.Bold)
          .fontColor(item.status === 'success' ? $r('app.color.success') : $r('app.color.danger'))
      }
      .justifyContent(FlexAlign.Center).alignItems(HorizontalAlign.Center)
      .width(34).height(34)
      .backgroundColor(item.status === 'success' ? $r('app.color.success_soft') : $r('app.color.danger_soft'))
      .borderRadius(17)
      Column({ space: 3 }) {
        Text(item.filename).fontSize(13).fontWeight(FontWeight.Medium)
          .fontColor($r('app.color.text_primary')).maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
        Text(item.smiles === null || item.smiles.length === 0 ? item.message : item.smiles)
          .fontSize(11).fontColor($r('app.color.text_secondary')).maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .alignItems(HorizontalAlign.Start).layoutWeight(1)
      Text(batchItemLabel(item))
        .fontSize(10)
        .fontColor(batchItemTone(item) === 'success' ? $r('app.color.success') :
          (batchItemTone(item) === 'danger' ? $r('app.color.danger') :
            (batchItemTone(item) === 'warning' ? $r('app.color.warning') : $r('app.color.primary'))))
        .padding({ left: 8, right: 8, top: 5, bottom: 5 })
        .backgroundColor(batchItemTone(item) === 'success' ? $r('app.color.success_soft') :
          (batchItemTone(item) === 'danger' ? $r('app.color.danger_soft') :
            (batchItemTone(item) === 'warning' ? $r('app.color.warning_soft') : $r('app.color.primary_soft'))))
        .borderRadius(10)
    }
    .width('100%').padding(11)
    .backgroundColor($r('app.color.surface_soft'))
    .border({ width: 1, color: $r('app.color.divider') })
    .borderRadius(13)
    .onClick(() => {
      if (item.analysisId.length > 0) this.onOpenResult(item.analysisId)
    })
  }

  build() {
    Stack() {
      MoleculeBackdrop()
      Scroll() {
        Column({ space: 16 }) {
          PageHeader({
            title: '批量识别',
            subtitle: '多图识别',
            currentUser: this.currentUser,
            onOpenSettings: this.onOpenSettings,
            onOpenGuide: this.onOpenGuide
          })

          Column({ space: 12 }) {
            Row() {
              Column() {
                SectionHeader({ symbol: '▦', title: '图片', caption: '最多 30 张' })
              }.layoutWeight(1)
              Blank()
              if (this.selectedUris.length > 0) {
                Button('清空').height(34).fontSize(12)
                  .backgroundColor($r('app.color.danger_soft')).fontColor($r('app.color.danger'))
                  .onClick(this.onClearSelection)
              }
            }.width('100%')
            if (this.selectedUris.length > 0) {
              Scroll() {
                Row({ space: 8 }) {
                  ForEach(this.selectedUris.slice(0, 8), (uri: string, index: number) => {
                    Stack({ alignContent: Alignment.BottomEnd }) {
                      Image(uri).width(76).height(76).objectFit(ImageFit.Cover)
                        .backgroundColor($r('app.color.surface_structure')).borderRadius(10)
                      Text(`${index + 1}`).fontSize(9).fontColor($r('app.color.text_on_primary'))
                        .padding(4).backgroundColor($r('app.color.primary')).borderRadius(8)
                    }
                  })
                }
              }.scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')
              Text(`已选择 ${this.selectedUris.length} 张图片`)
                .fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
            } else {
              Text('尚未选择图片').fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
            }
            if (this.builtInImages.length > 0) {
              Text('点选内置测试图').fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
              Scroll() {
                Row({ space: 8 }) {
                  ForEach(this.builtInImages, (uri: string, index: number) => {
                    Stack({ alignContent: Alignment.BottomEnd }) {
                      Image(uri).width(66).height(58).objectFit(ImageFit.Cover)
                        .backgroundColor($r('app.color.surface_structure')).borderRadius(9)
                        .border({ width: this.selectedUris.indexOf(uri) >= 0 ? 2 : 1,
                          color: this.selectedUris.indexOf(uri) >= 0 ?
                            $r('app.color.primary') : $r('app.color.divider') })
                      Text(this.selectedUris.indexOf(uri) >= 0 ? '✓' : `${index + 1}`).fontSize(9)
                        .fontColor($r('app.color.text_on_primary')).textAlign(TextAlign.Center)
                        .width(20).height(20).backgroundColor($r('app.color.primary')).borderRadius(10)
                    }
                    .onClick(() => this.onToggleBuiltInImage(uri))
                    .accessibilityText(`切换第${index + 1}张内置测试图`)
                  })
                }
              }.scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')
            }
            Row({ space: 10 }) {
              Button('相册').layoutWeight(1).height(44).enabled(!this.uploading)
                .backgroundColor($r('app.color.primary_soft')).fontColor($r('app.color.primary'))
                .onClick(this.onChooseImages)
              Button('全选内置').layoutWeight(1).height(44)
                .enabled(!this.uploading && this.builtInImages.length > 0)
                .backgroundColor($r('app.color.primary_soft')).fontColor($r('app.color.primary'))
                .onClick(this.onUseBuiltInImages)
            }.width('100%')
            Button(this.uploading ? '上传中…' : '开始识别').width('100%').height(46)
              .enabled(!this.uploading && this.selectedUris.length > 0)
              .backgroundColor($r('app.color.primary')).fontColor($r('app.color.text_on_primary'))
              .onClick(this.onSubmit)
          }
          .alignItems(HorizontalAlign.Start).width('100%').padding(16)
          .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') })
          .borderRadius($r('app.float.card_radius'))

          if (this.job !== undefined) {
            Column({ space: 12 }) {
              Row() {
                Column() {
                  SectionHeader({ symbol: '↻', title: '任务进度', caption: this.job.backend })
                }.layoutWeight(1)
                Blank()
                StatusPill({ symbol: '•', label: batchStatusLabel(this.job.status),
                  tone: this.job.status === 'failed' ? 'danger' :
                    (this.job.status === 'paused' ? 'warning' : 'primary') })
              }.width('100%')
              Row() {
                Text(`${this.job.completed} / ${this.job.total}`)
                  .fontSize(14).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
                Blank()
                Text(`${batchPercent(this.job.progress)}%`).fontSize(16).fontWeight(FontWeight.Bold)
                  .fontColor($r('app.color.primary'))
              }.width('100%')
              Progress({ value: batchPercent(this.job.progress), total: 100, type: ProgressType.Linear })
                .width('100%').height(8).color($r('app.color.primary')).backgroundColor($r('app.color.surface_muted'))
              Text(this.job.currentFile === null ? this.job.message : `正在处理：${this.job.currentFile}`)
                .fontSize(12).fontColor($r('app.color.text_secondary')).width('100%').maxLines(2)
              Row({ space: 6 }) {
                ForEach(this.categoryMetrics(this.job, false), (metric: BatchMetricItem) => {
                  this.categoryMetric(metric.label, metric.value, metric.tone)
                }, (metric: BatchMetricItem) => `${metric.key}:${metric.value}`)
              }.width('100%')
              Row({ space: 6 }) {
                ForEach(this.categoryMetrics(this.job, true), (metric: BatchMetricItem) => {
                  this.categoryMetric(metric.label, metric.value, metric.tone)
                }, (metric: BatchMetricItem) => `${metric.key}:${metric.value}`)
              }.width('100%')
              Row({ space: 8 }) {
                if (this.job.status === 'queued' || this.job.status === 'running') {
                  Button('暂停').layoutWeight(1).height(40).enabled(!this.controlBusy).onClick(this.onPause)
                }
                if (this.job.status === 'paused') {
                  Button('继续').layoutWeight(1).height(40).enabled(!this.controlBusy).onClick(this.onResume)
                }
                if (this.job.status !== 'completed' && this.job.status !== 'failed' &&
                  this.job.status !== 'cancelled' && this.job.status !== 'cancelling') {
                  Button('取消').layoutWeight(1).height(40).enabled(!this.controlBusy)
                    .backgroundColor($r('app.color.danger_soft')).fontColor($r('app.color.danger')).onClick(this.onCancel)
                }
                if ((this.job.status === 'completed' || this.job.status === 'failed' ||
                  this.job.status === 'cancelled') && this.job.categoryStats.failed > 0) {
                  Button('重试失败项').layoutWeight(1).height(40).enabled(!this.controlBusy)
                    .backgroundColor($r('app.color.warning_soft')).fontColor($r('app.color.warning'))
                    .onClick(this.onRetryFailed)
                }
              }.width('100%')
              if ((this.job.status === 'completed' || this.job.status === 'failed' ||
                this.job.status === 'cancelled') && this.job.results.length > 0) {
                Button('打开导出中心').width('100%').height(42)
                  .backgroundColor($r('app.color.primary')).fontColor($r('app.color.text_on_primary'))
                  .onClick(() => this.onOpenExports(this.job === undefined ? '' : this.job.jobId))
                  .accessibilityText('导出批量识别结果')
              }
            }
            .alignItems(HorizontalAlign.Start).width('100%').padding(16)
            .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') })
            .borderRadius($r('app.float.card_radius'))

            if (this.job.results.length > 0) {
              Column({ space: 8 }) {
                SectionHeader({ symbol: '≡', title: '逐项结果', caption: `${this.job.results.length} 项已返回` })
                ForEach(this.job.results, (item: BatchItemResult) => this.resultRow(item),
                  (item: BatchItemResult) =>
                    `${item.analysisId}:${item.filename}:${item.confirmed}:${item.reviewStatus}:${item.category}`)
              }
              .alignItems(HorizontalAlign.Start).width('100%').padding(16)
              .backgroundColor($r('app.color.surface')).borderRadius($r('app.float.card_radius'))
            }
          }

          if (this.errorMessage.length > 0) {
            Text(this.errorMessage).fontSize(12).fontColor($r('app.color.danger')).width('100%')
              .padding(12).backgroundColor($r('app.color.danger_soft')).borderRadius(12)
          }
        }
        .width('100%').padding({ left: 16, right: 16, top: 18, bottom: 28 })
      }
      .width('100%').height('100%').scrollBar(BarState.Off)
    }
    .width('100%').height('100%').backgroundColor($r('app.color.page_background'))
  }
}
