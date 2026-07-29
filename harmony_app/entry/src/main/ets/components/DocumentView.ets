import { DocumentResult } from '../model/DocumentResult'
import { MoleculeBackdrop, SectionHeader, StatusPill } from './CommonStates'
import { taskPresentation } from '../services/UiPresentation'

@Component
export struct DocumentView {
  @Prop selectedUri: string = ''
  @Prop document: DocumentResult | undefined = undefined
  @Prop busy: boolean = false
  @Prop jobStatus: string = ''
  @Prop jobMessage: string = ''
  onChooseDocument: () => void = () => {}
  onUseBuiltInDocument: () => void = () => {}
  onUploadAndDetect: () => void = () => {}
  onOpenReview: () => void = () => {}
  onRefresh: () => void = () => {}
  onPauseJob: () => void = () => {}
  onResumeJob: () => void = () => {}
  onCancelJob: () => void = () => {}

  private fileLabel(): string {
    if (this.document !== undefined) {
      return this.document.filename
    }
    return this.selectedUri.length > 0 ? '已选择本地文档' : '尚未选择文档'
  }

  build() {
    Stack({ alignContent: Alignment.TopStart }) {
      MoleculeBackdrop()
      Scroll() {
        Column({ space: 16 }) {
          Column({ space: 12 }) {
            SectionHeader({ symbol: '▤', title: '文档', caption: 'PDF、图片或 ZIP' })
            Column({ space: 6 }) {
              Text(this.fileLabel()).fontSize(15).fontWeight(FontWeight.Medium)
                .fontColor($r('app.color.text_primary')).maxLines(1)
                .textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
            }
            .alignItems(HorizontalAlign.Start).width('100%').padding(14)
            .backgroundColor($r('app.color.surface_soft')).borderRadius(14)
            Row({ space: 10 }) {
              Button('文件').layoutWeight(1).height(44)
                .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
                .enabled(!this.busy).onClick(this.onChooseDocument)
              Button('内置 PDF').layoutWeight(1).height(44)
                .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
                .enabled(!this.busy).onClick(this.onUseBuiltInDocument)
            }.width('100%')
            Button(this.busy ? '处理中…' : '检测区域').width('100%').height(50)
              .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.primary'))
              .enabled(!this.busy && this.selectedUri.length > 0).onClick(this.onUploadAndDetect)
          }
          .alignItems(HorizontalAlign.Start).width('100%').padding(16)
          .backgroundColor($r('app.color.surface')).borderRadius($r('app.float.card_radius'))
          .border({ width: 1, color: $r('app.color.border') })

          if (this.jobStatus.length > 0) {
            Column({ space: 10 }) {
              Row({ space: 8 }) {
                Text('区域检测任务').fontSize(16).fontWeight(FontWeight.Medium)
                  .fontColor($r('app.color.text_primary')).layoutWeight(1).maxLines(1)
                  .textOverflow({ overflow: TextOverflow.Ellipsis })
                StatusPill({ symbol: taskPresentation(this.jobStatus).symbol,
                  label: taskPresentation(this.jobStatus).label, tone: taskPresentation(this.jobStatus).tone })
              }
              Text(this.jobMessage).fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
              if (this.jobStatus === 'queued' || this.jobStatus === 'running' ||
                this.jobStatus === 'pausing' || this.jobStatus === 'cancelling') {
                LoadingProgress().width('100%').height(4).color($r('app.color.primary'))
              }
              if (this.jobStatus === 'queued' || this.jobStatus === 'running' || this.jobStatus === 'pausing') {
                Row({ space: 8 }) {
                  Button(this.jobStatus === 'pausing' ? '继续' : '暂停').layoutWeight(1).height(42)
                    .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
                    .onClick(this.jobStatus === 'pausing' ? this.onResumeJob : this.onPauseJob)
                  Button('终止').layoutWeight(1).height(42)
                    .fontColor($r('app.color.danger')).backgroundColor($r('app.color.danger_soft'))
                    .onClick(this.onCancelJob)
                }.width('100%')
              } else if (this.jobStatus === 'paused') {
                Row({ space: 8 }) {
                  Button('继续').layoutWeight(1).height(42).onClick(this.onResumeJob)
                    .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
                  Button('终止').layoutWeight(1).height(42).onClick(this.onCancelJob)
                    .fontColor($r('app.color.danger')).backgroundColor($r('app.color.danger_soft'))
                }.width('100%')
              }
            }
            .alignItems(HorizontalAlign.Start).width('100%').padding(16)
            .backgroundColor($r('app.color.surface')).borderRadius($r('app.float.card_radius'))
            .border({ width: 1, color: $r('app.color.border') })
          }

          if (this.document !== undefined && this.document.pages.length > 0) {
            Column({ space: 12 }) {
              SectionHeader({
                symbol: '⌬', title: '检测结果',
                caption: `${this.document.pageCount} 页 · ${this.document.regionCount} 个候选区域`
              })
              Image(this.document.pages[0].previewUrl).width('100%').height(220)
                .objectFit(ImageFit.Contain).backgroundColor($r('app.color.surface_structure')).borderRadius(14)
                .accessibilityText('文档第一页区域检测预览')
              Row({ space: 10 }) {
                Button('刷新').height(46).layoutWeight(1)
                  .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
                  .onClick(this.onRefresh)
                Button('审核区域').height(46).layoutWeight(2)
                  .fontColor($r('app.color.text_on_primary')).backgroundColor($r('app.color.primary'))
                  .onClick(this.onOpenReview)
              }
            }
            .alignItems(HorizontalAlign.Start).width('100%').padding(16)
            .backgroundColor($r('app.color.surface')).borderRadius($r('app.float.card_radius'))
            .border({ width: 1, color: $r('app.color.border') })
          }
        }
        .width('100%').constraintSize({ minHeight: '100%' })
        .padding({ left: 16, right: 16, top: 18, bottom: 28 })
      }
      .width('100%').height('100%').scrollBar(BarState.Off)
    }
    .width('100%').height('100%').backgroundColor($r('app.color.page_background'))
  }
}
