import { UserProfile, VisionSample } from '../model/VisionResult'
import { LocalTaskState } from '../controllers/JobController'
import { formatElapsed, taskPresentation } from '../services/UiPresentation'
import { AppErrorState, LoadingSkeleton, MoleculeBackdrop, SectionHeader, StatusPill } from './CommonStates'
import { PageHeader } from './PageChrome'

@Component
export struct RecognitionView {
  @Prop currentUser: UserProfile | undefined = undefined
  @Prop tasks: LocalTaskState[] = []
  @Prop samples: VisionSample[] = []
  @Prop samplesLoading: boolean = false
  @Prop samplesError: string = ''
  @Prop serviceSummary: string = ''
  @Prop submitting: boolean = false
  @Prop imageUri: string = ''
  @State nowMs: number = Date.now()
  private elapsedTimer: number = -1
  onOpenSettings: () => void = () => {}
  onOpenGuide: () => void = () => {}
  onLoadSamples: () => void = () => {}
  onSubmitSample: (sample: VisionSample) => void = (_sample: VisionSample) => {}
  onChooseImage: () => void = () => {}
  onSubmitImage: () => void = () => {}
  onOpenResult: (analysisId: string) => void = (_analysisId: string) => {}

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
      if (!taskPresentation(task.status).terminal && !task.networkWaiting) {
        LoadingProgress().width('100%').height(4).color($r('app.color.primary'))
      }
      if (task.status === 'completed' && task.analysisId.length > 0) {
        Button('查看结果')
          .width('100%').height(44)
          .fontColor($r('app.color.primary')).backgroundColor($r('app.color.primary_soft'))
          .onClick(() => this.onOpenResult(task.analysisId))
          .accessibilityText(`查看任务${task.jobId}的识别结果`)
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
            subtitle: '结构图进入电脑端模型，结果回到团队分子工作台。',
            currentUser: this.currentUser,
            onOpenSettings: this.onOpenSettings,
            onOpenGuide: this.onOpenGuide
          })

          Row({ space: 8 }) {
            Text('●')
              .fontSize(10)
              .fontColor(this.serviceSummary.startsWith('已连接') ? $r('app.color.success') : $r('app.color.warning'))
            Column({ space: 2 }) {
              Text('本地实验室服务').fontSize(11).fontColor($r('app.color.text_secondary'))
              Text(this.serviceSummary).fontSize(13).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
            }
            .alignItems(HorizontalAlign.Start)
            .layoutWeight(1)
            Text('设置').fontSize(12).fontColor($r('app.color.primary')).onClick(this.onOpenSettings)
          }
          .width('100%')
          .padding({ left: 14, right: 14, top: 10, bottom: 10 })
          .backgroundColor($r('app.color.surface'))
          .border({ width: 1, color: $r('app.color.border') })
          .borderRadius(15)
          .accessibilityText(`服务状态：${this.serviceSummary}`)

          if (this.tasks.length > 0) {
            Column({ space: 11 }) {
              SectionHeader({ symbol: '◷', title: '本地任务', caption: `${this.tasks.length} 条任务，断网后会自动续查` })
              ForEach(this.tasks, (task: LocalTaskState) => {
                this.taskCard(task)
              }, (task: LocalTaskState) => task.jobId)
            }
            .alignItems(HorizontalAlign.Start).width('100%').padding(16)
            .backgroundColor($r('app.color.surface'))
            .border({ width: 1, color: $r('app.color.border') })
            .borderRadius($r('app.float.card_radius'))
            .shadow({ radius: 10, color: $r('app.color.shadow'), offsetY: 3 })
          }

          Column({ space: 12 }) {
            Row() {
              Column() {
                SectionHeader({
                  symbol: '⌬',
                  title: '内置分子样例',
                  caption: '模拟器图库为空也可以完整演示'
                })
              }
              .layoutWeight(1)
              Column() {
                Text('刷新')
                  .fontSize(12)
                  .fontColor($r('app.color.primary'))
              }
              .justifyContent(FlexAlign.Center)
              .alignItems(HorizontalAlign.Center)
              .width(52)
              .height(44)
              .onClick(this.onLoadSamples)
              .accessibilityText('刷新内置分子样例')
            }
            .alignItems(VerticalAlign.Center)
            .width('100%')

            if (this.samplesLoading && this.samples.length === 0) {
              LoadingSkeleton({ rows: 2 })
            } else if (this.samplesError.length > 0 && this.samples.length === 0) {
              AppErrorState({ title: '样例加载失败', message: this.samplesError, onRetry: this.onLoadSamples })
            } else {
              ForEach(this.samples, (sample: VisionSample) => {
                Row({ space: 12 }) {
                  Image(sample.imageUrl)
                    .width(88).height(68)
                    .objectFit(ImageFit.Contain)
                    .backgroundColor($r('app.color.surface_structure'))
                    .borderRadius(12)
                    .accessibilityText(`${sample.name}分子结构图`)
                  Column({ space: 4 }) {
                    Text(sample.name).fontSize(16).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
                    Text('标准演示结构').fontSize(11).fontColor($r('app.color.text_secondary'))
                  }
                  .alignItems(HorizontalAlign.Start)
                  .layoutWeight(1)
                  Button('识别')
                    .height(44)
                    .enabled(!this.submitting)
                    .fontColor($r('app.color.text_on_primary'))
                    .backgroundColor($r('app.color.primary'))
                    .onClick(() => this.onSubmitSample(sample))
                    .accessibilityText(`识别示例${sample.name}`)
                }
                .width('100%')
                .padding(10)
                .backgroundColor($r('app.color.surface_soft'))
                .border({ width: 1, color: $r('app.color.divider') })
                .borderRadius(15)
              }, (sample: VisionSample) => sample.id)
            }
          }
          .alignItems(HorizontalAlign.Start)
          .width('100%')
          .padding(16)
          .backgroundColor($r('app.color.surface'))
          .border({ width: 1, color: $r('app.color.border') })
          .borderRadius($r('app.float.card_radius'))

          Column({ space: 12 }) {
            SectionHeader({ symbol: '＋', title: '本地结构图', caption: '支持 PNG、JPG、WEBP 与 BMP' })
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
                Text('选择一张化学结构图').fontSize(14).fontColor($r('app.color.text_secondary'))
                Text('模拟器图库为空时请使用上方样例').fontSize(11).fontColor($r('app.color.text_tertiary'))
              }
              .justifyContent(FlexAlign.Center)
              .width('100%').height(170)
              .backgroundColor($r('app.color.surface_soft'))
              .border({ width: 1, color: $r('app.color.border'), style: BorderStyle.Dashed })
              .borderRadius(14)
              .onClick(this.onChooseImage)
              .accessibilityText('选择分子结构图片')
            }
            Button(this.imageUri.length > 0 ? '重新选择图片' : '选择图片')
              .width('100%').height(44)
              .enabled(!this.submitting)
              .backgroundColor($r('app.color.primary_soft'))
              .fontColor($r('app.color.primary'))
              .onClick(this.onChooseImage)
            Button(this.submitting ? '正在提交…' : '提交后台识别')
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
