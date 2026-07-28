import { DocumentResult } from '../model/DocumentResult'
import { MoleculeBackdrop, SectionHeader, StatusPill } from './CommonStates'

@Component
export struct DocumentView {
  @Prop selectedUri: string = ''
  @Prop document: DocumentResult | undefined = undefined
  @Prop busy: boolean = false
  @Prop jobStatus: string = ''
  @Prop jobMessage: string = ''
  onChooseDocument: () => void = () => {}
  onUploadAndDetect: () => void = () => {}
  onOpenReview: () => void = () => {}
  onRefresh: () => void = () => {}

  private fileLabel(): string {
    if (this.document !== undefined) {
      return this.document.filename
    }
    return this.selectedUri.length > 0 ? '已选择本地文档' : '尚未选择文档'
  }

  build() {
    Stack() {
      MoleculeBackdrop()
      Scroll() {
        Column({ space: 16 }) {
          Column({ space: 5 }) {
            Text('文档结构识别').fontSize(25).fontWeight(FontWeight.Bold)
              .fontColor($r('app.color.text_primary'))
            Text('先检测区域，再由组员确认 molecule；反应式区域只分流不解析。')
              .fontSize(13).fontColor($r('app.color.text_secondary'))
          }
          .alignItems(HorizontalAlign.Start).width('100%')

          Column({ space: 12 }) {
            SectionHeader({ symbol: '▤', title: '上传化学文档', caption: '支持 PDF、PNG、JPG 和 ZIP 页面集合' })
            Column({ space: 6 }) {
              Text(this.fileLabel()).fontSize(15).fontWeight(FontWeight.Medium)
                .fontColor($r('app.color.text_primary'))
              Text(this.selectedUri.length > 0 || this.document !== undefined ?
                '文件只发送到当前电脑端服务。' : '从模拟器文件选择器中选择文档。')
                .fontSize(11).fontColor($r('app.color.text_secondary'))
            }
            .alignItems(HorizontalAlign.Start).width('100%').padding(14)
            .backgroundColor($r('app.color.surface_soft')).borderRadius(14)
            Button('选择文档').width('100%').height(46)
              .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
              .enabled(!this.busy).onClick(this.onChooseDocument)
            Button(this.busy ? '正在处理…' : '上传并检测区域').width('100%').height(50)
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
                  .fontColor($r('app.color.text_primary'))
                StatusPill({ symbol: '◷', label: this.jobStatus, tone: this.jobStatus === 'failed' ? 'danger' : 'primary' })
              }
              Text(this.jobMessage).fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
              if (this.jobStatus === 'queued' || this.jobStatus === 'running') {
                LoadingProgress().width('100%').height(4).color($r('app.color.primary'))
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
        .width('100%').padding({ left: 16, right: 16, top: 18, bottom: 28 })
      }
      .width('100%').height('100%').scrollBar(BarState.Off)
    }
    .width('100%').height('100%').backgroundColor($r('app.color.page_background'))
  }
}
