import { UserProfile } from '../model/VisionResult'
import { LocalTaskState } from '../controllers/JobController'
import { formatElapsed, taskPresentation } from '../services/UiPresentation'
import { MoleculeBackdrop, SectionHeader, StatusPill } from './CommonStates'
import { PageHeader } from './PageChrome'

@Component
export struct RecognitionView {
  @Prop isDeveloper: boolean = false
  @Prop currentUser: UserProfile | undefined = undefined
  @Prop tasks: LocalTaskState[] = []
  @Prop serviceSummary: string = ''
  @Prop submitting: boolean = false
  @Prop imageUri: string = ''
  @Prop builtInImages: string[] = []
  @State nowMs: number = Date.now()
  private elapsedTimer: number = -1
  onOpenSettings: () => void = () => {}
  onOpenGuide: () => void = () => {}
  onOpenDocuments: () => void = () => {}
  onChooseImage: () => void = () => {}
  onSelectBuiltInImage: (uri: string) => void = (_uri: string) => {}
  onSubmitImage: () => void = () => {}
  onOpenResult: (analysisId: string) => void = (_analysisId: string) => {}
  onPauseTask: (jobId: string) => void = (_jobId: string) => {}
  onResumeTask: (jobId: string) => void = (_jobId: string) => {}
  onCancelTask: (jobId: string) => void = (_jobId: string) => {}

  aboutToAppear(): void {
    this.elapsedTimer = setInterval(() => this.nowMs = Date.now(), 1000)
  }

  aboutToDisappear(): void {
    if (this.elapsedTimer >= 0) {
      clearInterval(this.elapsedTimer)
      this.elapsedTimer = -1
    }
  }

  private displayStatus(task: LocalTaskState): string {
    return task.networkWaiting ? 'network_wait' : task.status
  }

  private jobLabel(task: LocalTaskState): string {
    return taskPresentation(this.displayStatus(task)).label
  }

  private jobSymbol(task: LocalTaskState): string {
    return taskPresentation(this.displayStatus(task)).symbol
  }

  private jobTone(task: LocalTaskState): string {
    return taskPresentation(this.displayStatus(task)).tone
  }

  @Builder
  private taskCard(task: LocalTaskState) {
    Column({ space: 10 }) {
      Row({ space: 10 }) {
        Text(this.jobSymbol(task))
          .fontSize(19).fontWeight(FontWeight.Bold).fontColor($r('app.color.primary'))
          .textAlign(TextAlign.Center).width(40).height(40)
          .backgroundColor($r('app.color.primary_soft')).borderRadius(13)
        Column({ space: 3 }) {
          Row({ space: 7 }) {
            Text(this.jobLabel(task)).fontSize(15).fontWeight(FontWeight.Medium)
              .fontColor($r('app.color.text_primary'))
            StatusPill({ symbol: this.jobSymbol(task), label: this.displayStatus(task), tone: this.jobTone(task) })
          }
          Text(task.message).fontSize(12).fontColor($r('app.color.text_secondary')).maxLines(3)
        }
        .alignItems(HorizontalAlign.Start).layoutWeight(1)
      }
      .width('100%')
      Row({ space: 8 }) {
        Text(task.inputKind === 'image' ? '图片任务' : (task.inputKind === 'sample' ? '内置样例' : '恢复中的任务'))
          .fontSize(10).fontColor($r('app.color.text_tertiary'))
        Text(`#${task.jobId.substring(Math.max(0, task.jobId.length - 8))}`)
          .fontSize(10).fontColor($r('app.color.text_tertiary'))
        Blank()
        if (taskPresentation(task.status).terminal) {
          Text('任务已结束').fontSize(10).fontColor($r('app.color.text_tertiary'))
        } else if (task.createdAt.length > 0) {
          Text(`已等待 ${formatElapsed(task.createdAt, this.nowMs)}`)
            .fontSize(10).fontColor($r('app.color.primary'))
        }
      }
      .width('100%')
      if (task.status === 'running' || task.status === 'pausing' || task.status === 'cancelling') {
        Row({ space: 8 }) {
          LoadingProgress().width(18).height(18).color($r('app.color.primary'))
          Text(task.status === 'running' ? '模型正在处理' : task.message)
            .fontSize(11).fontColor($r('app.color.text_secondary')).maxLines(1)
        }.width('100%')
      }
      if (task.status === 'completed' && task.analysisId.length > 0) {
        Button('查看结果')
          .width('100%').height(44)
          .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
          .onClick(() => this.onOpenResult(task.analysisId))
          .accessibilityText(`查看任务${task.jobId}的识别结果`)
      }
      if (task.status === 'queued' || task.status === 'running' || task.status === 'pausing') {
        Row({ space: 8 }) {
          Button(task.status === 'pausing' ? '继续' : '暂停').layoutWeight(1).height(42)
            .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
            .onClick(() => task.status === 'pausing' ? this.onResumeTask(task.jobId) : this.onPauseTask(task.jobId))
          Button('终止').layoutWeight(1).height(42)
            .fontColor($r('app.color.danger')).backgroundColor($r('app.color.danger_soft'))
            .onClick(() => this.onCancelTask(task.jobId))
        }.width('100%')
      } else if (task.status === 'paused') {
        Row({ space: 8 }) {
          Button('继续').layoutWeight(1).height(42)
            .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
            .onClick(() => this.onResumeTask(task.jobId))
          Button('终止').layoutWeight(1).height(42)
            .fontColor($r('app.color.danger')).backgroundColor($r('app.color.danger_soft'))
            .onClick(() => this.onCancelTask(task.jobId))
        }.width('100%')
      }
    }
    .width('100%').padding(14)
    .backgroundColor($r('app.color.surface_soft'))
    .border({ width: 1, color: $r('app.color.divider') })
    .borderRadius(15)
    .accessibilityText(`识别任务${task.jobId}：${this.jobLabel(task)}，${task.message}`)
  }

  build() {
    Stack() {
      MoleculeBackdrop()
      Scroll() {
        Column({ space: 15 }) {
          PageHeader({
            title: '识别',
            subtitle: '图片转 SMILES',
            currentUser: this.currentUser,
            onOpenSettings: this.onOpenSettings,
            onOpenGuide: this.onOpenGuide
          })

          Row({ space: 8 }) {
            Text('●')
              .fontSize(10)
              .fontColor(this.serviceSummary.startsWith('已连接') ? $r('app.color.success') : $r('app.color.warning'))
            Text(this.serviceSummary).fontSize(13).fontWeight(FontWeight.Medium)
              .fontColor($r('app.color.text_primary')).layoutWeight(1).maxLines(1)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
            Text('设置').fontSize(12).fontColor($r('app.color.primary')).onClick(this.onOpenSettings)
          }
          .width('100%')
          .padding({ left: 14, right: 14, top: 10, bottom: 10 })
          .backgroundColor($r('app.color.surface'))
          .border({ width: 1, color: $r('app.color.border') })
          .borderRadius(15)
          .accessibilityText(`服务状态：${this.serviceSummary}`)

          if (this.isDeveloper) {
            Row({ space: 12 }) {
              Text('▤').fontSize(22).fontWeight(FontWeight.Bold).fontColor($r('app.color.primary'))
                .width(44).height(44).textAlign(TextAlign.Center)
                .backgroundColor($r('app.color.primary_soft')).borderRadius(14)
              Text('文档识别').fontSize(16).fontWeight(FontWeight.Medium)
                .fontColor($r('app.color.text_primary')).layoutWeight(1)
              Button('进入').height(44).fontColor($r('app.color.text_on_primary'))
                .backgroundColor($r('app.color.primary')).onClick(this.onOpenDocuments)
            }
            .width('100%').padding(14).backgroundColor($r('app.color.surface'))
            .border({ width: 1, color: $r('app.color.border') }).borderRadius(15)
          }

          if (this.tasks.length > 0) {
            Column({ space: 11 }) {
              SectionHeader({ symbol: '◷', title: '任务', caption: `${this.tasks.length} 条` })
              ForEach(this.tasks, (task: LocalTaskState) => {
                this.taskCard(task)
              }, (task: LocalTaskState) => `${task.jobId}:${task.status}:${task.updatedAt}:${task.networkWaiting}`)
            }
            .alignItems(HorizontalAlign.Start).width('100%').padding(16)
            .backgroundColor($r('app.color.surface'))
            .border({ width: 1, color: $r('app.color.border') })
            .borderRadius($r('app.float.card_radius'))
            .shadow({ radius: 10, color: $r('app.color.shadow'), offsetY: 3 })
          }

          Column({ space: 12 }) {
            SectionHeader({ symbol: '＋', title: '图片识别', caption: `${this.builtInImages.length} 张内置测试图` })
            if (this.imageUri.length > 0) {
              Image(this.imageUri)
                .width('100%').height(210)
                .objectFit(ImageFit.Contain)
                .backgroundColor($r('app.color.surface_structure'))
                .borderRadius(14)
                .accessibilityText('已选择的分子结构图片')
            } else {
              Column({ space: 8 }) {
                Text('＋').fontSize(34).fontColor($r('app.color.primary'))
                Text('选择图片').fontSize(14).fontColor($r('app.color.text_secondary'))
              }
              .justifyContent(FlexAlign.Center)
              .width('100%').height(170)
              .backgroundColor($r('app.color.surface_soft'))
              .border({ width: 1, color: $r('app.color.border'), style: BorderStyle.Dashed })
              .borderRadius(14)
                .onClick(this.onChooseImage)
                .accessibilityText('选择分子结构图片')
            }
            if (this.builtInImages.length > 0) {
              Text('点选内置测试图').fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
              Scroll() {
                Row({ space: 8 }) {
                  ForEach(this.builtInImages, (uri: string, index: number) => {
                    Stack({ alignContent: Alignment.BottomEnd }) {
                      Image(uri).width(66).height(58).objectFit(ImageFit.Cover)
                        .backgroundColor($r('app.color.surface_structure')).borderRadius(9)
                        .border({ width: this.imageUri === uri ? 2 : 1,
                          color: this.imageUri === uri ? $r('app.color.primary') : $r('app.color.divider') })
                      Text(this.imageUri === uri ? '✓' : `${index + 1}`).fontSize(9)
                        .fontColor($r('app.color.text_on_primary')).textAlign(TextAlign.Center)
                        .width(20).height(20).backgroundColor($r('app.color.primary')).borderRadius(10)
                    }
                    .onClick(() => this.onSelectBuiltInImage(uri))
                    .accessibilityText(`选择第${index + 1}张内置测试图`)
                  })
                }
              }.scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')
            }
            Button('从相册选择').width('100%').height(44).enabled(!this.submitting)
              .backgroundColor($r('app.color.primary_soft')).fontColor($r('app.color.primary'))
              .onClick(this.onChooseImage)
            Button(this.submitting ? '提交中…' : '开始识别')
              .width('100%').height(50)
              .enabled(!this.submitting && this.imageUri.length > 0)
              .fontColor($r('app.color.text_on_primary'))
              .backgroundColor($r('app.color.primary'))
              .onClick(this.onSubmitImage)
          }
          .alignItems(HorizontalAlign.Start)
          .width('100%')
          .padding(16)
          .backgroundColor($r('app.color.surface'))
          .border({ width: 1, color: $r('app.color.border') })
          .borderRadius($r('app.float.card_radius'))
        }
        .width('100%')
        .padding({ left: 16, right: 16, top: 18, bottom: 28 })
      }
      .width('100%').height('100%')
      .scrollBar(BarState.Off)
      .edgeEffect(EdgeEffect.Spring)
    }
    .width('100%').height('100%')
    .backgroundColor($r('app.color.page_background'))
  }
}
