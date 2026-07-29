import { ExportItem, ExportManifest } from '../model/ExportResult'
import { exportSizeLabel } from '../services/ExportApi'
import { AppErrorState, LoadingSkeleton, MoleculeBackdrop, SectionHeader, StatusPill } from './CommonStates'

@Component
export struct ExportCenterView {
  @Prop manifest: ExportManifest | undefined = undefined
  @Prop loading: boolean = false
  @Prop busyFormat: string = ''
  @Prop errorMessage: string = ''
  onBack: () => void = () => {}
  onRefresh: () => void = () => {}
  onSave: (item: ExportItem) => void = (_item: ExportItem) => {}
  onShare: (item: ExportItem) => void = (_item: ExportItem) => {}

  @Builder
  private exportRow(item: ExportItem) {
    Column({ space: 10 }) {
      Row({ space: 9 }) {
        Column({ space: 3 }) {
          Text(item.label).fontSize(16).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
            .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
          Text(item.fileName === null ? item.format.toUpperCase() : item.fileName)
            .fontSize(11).fontColor($r('app.color.text_secondary')).maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
        }.alignItems(HorizontalAlign.Start).layoutWeight(1)
        if (item.formalOnly) {
          StatusPill({ symbol: item.available ? '✓' : '!', label: '正式结构',
            tone: item.available ? 'success' : 'warning' })
        } else if (item.available) {
          Text(exportSizeLabel(item.sizeBytes)).fontSize(11).fontColor($r('app.color.text_secondary'))
        }
      }.width('100%')

      if (!item.available) {
        Text(item.reason.length > 0 ? item.reason : '当前结果不满足此格式的导出条件。')
          .fontSize(12).fontColor($r('app.color.warning')).width('100%')
          .padding(9).backgroundColor($r('app.color.warning_soft')).borderRadius(9)
      } else {
        Row({ space: 9 }) {
          Button(this.busyFormat === item.format ? '处理中…' : '保存到文件')
            .layoutWeight(1).height(40).enabled(this.busyFormat.length === 0)
            .backgroundColor($r('app.color.primary_soft')).fontColor($r('app.color.primary'))
            .onClick(() => this.onSave(item))
          Button('分享').layoutWeight(1).height(40).enabled(this.busyFormat.length === 0)
            .backgroundColor($r('app.color.primary')).fontColor($r('app.color.text_on_primary'))
            .onClick(() => this.onShare(item))
        }.width('100%')
      }
    }
    .alignItems(HorizontalAlign.Start).width('100%').padding(14)
    .backgroundColor($r('app.color.surface')).border({ width: 1, color: $r('app.color.border') }).borderRadius(15)
  }

  build() {
    Stack() {
      MoleculeBackdrop()
      Column() {
        Row({ space: 10 }) {
          Button('‹').width(44).height(44).backgroundColor($r('app.color.primary_soft'))
            .fontColor($r('app.color.primary')).onClick(this.onBack).accessibilityText('返回上一页')
          Column({ space: 2 }) {
            Text('导出中心').fontSize(20).fontWeight(FontWeight.Bold).fontColor($r('app.color.text_primary'))
            Text('EXPORT CENTER').fontSize(8).fontColor($r('app.color.text_secondary'))
          }.alignItems(HorizontalAlign.Start).layoutWeight(1)
          Text('刷新').fontSize(14).fontColor($r('app.color.primary')).height(44)
            .onClick(this.onRefresh).accessibilityText('刷新短时下载链接')
        }
        .width('100%').height(62).padding({ left: 10, right: 18 })
        .backgroundColor($r('app.color.nav_background'))
        .border({ width: { bottom: 1 }, color: $r('app.color.divider') })

        if (this.loading) {
          LoadingSkeleton({ rows: 6 })
        } else if (this.manifest === undefined) {
          AppErrorState({ title: '无法打开导出中心', message: this.errorMessage,
            onRetry: this.onRefresh })
        } else {
          Scroll() {
            Column({ space: 12 }) {
              Column({ space: 7 }) {
                SectionHeader({ symbol: '⇩', title: this.manifest.scope === 'batch' ? '批量结果导出' : '单条结果导出',
                  caption: `链接约 ${this.manifest.expiresInSeconds} 秒有效` })
                Text('CSV、JSON 和 PDF 可用于查看结果；SMI、MOL、SDF 与正式结构 ZIP 仅包含人工确认结果。')
                  .fontSize(12).fontColor($r('app.color.text_secondary')).lineHeight(19).width('100%')
              }
              .alignItems(HorizontalAlign.Start).width('100%').padding(14)
              .backgroundColor($r('app.color.primary_faint')).borderRadius(15)
              ForEach(this.manifest.items, (item: ExportItem) => this.exportRow(item),
                (item: ExportItem) => item.format)
              if (this.errorMessage.length > 0) {
                Text(this.errorMessage).fontSize(12).fontColor($r('app.color.danger')).width('100%')
                  .padding(11).backgroundColor($r('app.color.danger_soft')).borderRadius(11)
              }
            }.width('100%').padding({ left: 16, right: 16, top: 16, bottom: 28 })
          }.width('100%').layoutWeight(1).scrollBar(BarState.Off)
        }
      }.width('100%').height('100%')
    }.width('100%').height('100%').backgroundColor($r('app.color.page_background'))
  }
}
