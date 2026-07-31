import {
  cropRectForAspect,
  CropHandle,
  fullCropRect,
  ImageEditSpec,
  moveCropRect,
  NormalizedCropRect,
  normalizedRotationDegrees,
  resizeCropRect
} from '../services/ImageEditGeometry'

interface ImageAspectOption {
  label: string
  ratio: number
}

const IMAGE_ASPECT_OPTIONS: ImageAspectOption[] = [
  { label: '自由', ratio: 0 },
  { label: '1:1', ratio: 1 },
  { label: '4:3', ratio: 4 / 3 },
  { label: '3:4', ratio: 3 / 4 },
  { label: '16:9', ratio: 16 / 9 }
]
const EDITOR_PREVIEW_MAX_HEIGHT: number = 220

@Component
export struct ImageEditorView {
  @Prop imageUri: string = ''
  @Prop busy: boolean = false
  @Prop initialRotation: number = 0
  @State sourceWidth: number = 1
  @State sourceHeight: number = 1
  @State previewWidth: number = 280
  @State rotationDegrees: number = 0
  @State flipHorizontal: boolean = false
  @State flipVertical: boolean = false
  @State crop: NormalizedCropRect = fullCropRect()
  @State aspectLabel: string = '自由'
  private gestureStartCrop: NormalizedCropRect = fullCropRect()
  onCancel: () => void = () => {}
  onApply: (spec: ImageEditSpec) => void = (_spec: ImageEditSpec) => {}

  aboutToAppear(): void {
    this.rotationDegrees = normalizedRotationDegrees(this.initialRotation)
  }

  private rotatedWidth(): number {
    return this.rotationDegrees === 90 || this.rotationDegrees === 270 ?
      this.sourceHeight : this.sourceWidth
  }

  private rotatedHeight(): number {
    return this.rotationDegrees === 90 || this.rotationDegrees === 270 ?
      this.sourceWidth : this.sourceHeight
  }

  private frameWidth(): number {
    const aspect: number = this.rotatedWidth() / Math.max(1, this.rotatedHeight())
    return Math.max(1, Math.min(this.previewWidth, EDITOR_PREVIEW_MAX_HEIGHT * aspect))
  }

  private frameHeight(): number {
    return Math.max(1, this.frameWidth() * this.rotatedHeight() / Math.max(1, this.rotatedWidth()))
  }

  private sourceComponentWidth(): number {
    return this.rotationDegrees === 90 || this.rotationDegrees === 270 ?
      this.frameHeight() : this.frameWidth()
  }

  private sourceComponentHeight(): number {
    return this.rotationDegrees === 90 || this.rotationDegrees === 270 ?
      this.frameWidth() : this.frameHeight()
  }

  private cropWidthPercent(): string {
    return `${Math.max(1, (this.crop.right - this.crop.left) * 100)}%`
  }

  private cropHeightPercent(): string {
    return `${Math.max(1, (this.crop.bottom - this.crop.top) * 100)}%`
  }

  private beginCropGesture(): void {
    this.gestureStartCrop = {
      left: this.crop.left,
      top: this.crop.top,
      right: this.crop.right,
      bottom: this.crop.bottom
    }
  }

  private moveCrop(event: GestureEvent): void {
    if (this.busy) return
    this.crop = moveCropRect(
      this.gestureStartCrop,
      event.offsetX / Math.max(1, this.frameWidth()),
      event.offsetY / Math.max(1, this.frameHeight())
    )
    this.aspectLabel = '自由'
  }

  private resizeCrop(handle: CropHandle, event: GestureEvent): void {
    if (this.busy) return
    this.crop = resizeCropRect(
      this.gestureStartCrop,
      handle,
      event.offsetX / Math.max(1, this.frameWidth()),
      event.offsetY / Math.max(1, this.frameHeight())
    )
    this.aspectLabel = '自由'
  }

  private chooseAspect(label: string, ratio: number): void {
    if (this.busy) return
    this.aspectLabel = label
    if (ratio > 0) {
      this.crop = cropRectForAspect(this.rotatedWidth(), this.rotatedHeight(), ratio)
    }
  }

  private rotateEditor(delta: number): void {
    if (this.busy) return
    this.rotationDegrees = normalizedRotationDegrees(this.rotationDegrees + delta)
    this.crop = fullCropRect()
    this.aspectLabel = '自由'
  }

  private reset(): void {
    if (this.busy) return
    this.rotationDegrees = 0
    this.flipHorizontal = false
    this.flipVertical = false
    this.crop = fullCropRect()
    this.aspectLabel = '自由'
  }

  private apply(): void {
    const spec: ImageEditSpec = {
      rotationDegrees: this.rotationDegrees,
      flipHorizontal: this.flipHorizontal,
      flipVertical: this.flipVertical,
      crop: this.crop
    }
    this.onApply(spec)
  }

  @Builder
  private cropHandle(handle: CropHandle, alignment: Alignment) {
    Stack({ alignContent: alignment }) {
      Text('◆')
        .width(44).height(44).fontSize(14).fontColor('#FFFFFFFF')
        .textAlign(TextAlign.Center).backgroundColor(Color.Transparent)
        .textShadow({ radius: 3, color: '#A0000000', offsetX: 0, offsetY: 1 })
        .gesture(
          PanGesture({ direction: PanDirection.All, distance: 2 })
            .onActionStart(() => this.beginCropGesture())
            .onActionUpdate((event: GestureEvent) => this.resizeCrop(handle, event))
        )
        .accessibilityText('拖动调整裁剪框')
    }
    .width('100%').height('100%')
    .hitTestBehavior(HitTestMode.Transparent)
  }

  @Builder
  private toolButton(label: string, active: boolean, action: () => void) {
    Button(label)
      .layoutWeight(1).height(40).fontSize(12)
      .enabled(!this.busy)
      .backgroundColor(active ? $r('app.color.primary') : $r('app.color.surface_muted'))
      .fontColor(active ? $r('app.color.text_on_primary') : $r('app.color.text_body'))
      .onClick(action)
  }

  build() {
    Column({ space: 12 }) {
      Row() {
        Column({ space: 2 }) {
          Text('编辑当前图片').fontSize(17).fontWeight(FontWeight.Medium)
            .fontColor($r('app.color.text_primary'))
          Text('拖动框移动，拖动四角调整范围')
            .fontSize(11).fontColor($r('app.color.text_secondary'))
        }.alignItems(HorizontalAlign.Start).layoutWeight(1)
        Text('重置').fontSize(12).fontWeight(FontWeight.Medium)
          .fontColor($r('app.color.primary')).width(48).height(40).textAlign(TextAlign.Center)
          .enabled(!this.busy).onClick(() => this.reset())
      }.width('100%').alignItems(VerticalAlign.Center)

      Stack({ alignContent: Alignment.Center }) {
        Stack({ alignContent: Alignment.Center }) {
          Image(this.imageUri)
            .width(this.sourceComponentWidth()).height(this.sourceComponentHeight())
            .objectFit(ImageFit.Fill)
            .rotate({ angle: this.rotationDegrees })
            .scale({ x: this.flipHorizontal ? -1 : 1, y: this.flipVertical ? -1 : 1 })
            .onComplete((event) => {
              if (event !== undefined && event.width > 0 && event.height > 0) {
                this.sourceWidth = event.width
                this.sourceHeight = event.height
              }
            })
          Stack() {
            Row().width('100%').height('100%')
              .backgroundColor('#1200A6B2')
              .gesture(
                PanGesture({ direction: PanDirection.All, distance: 3 })
                  .onActionStart(() => this.beginCropGesture())
                  .onActionUpdate((event: GestureEvent) => this.moveCrop(event))
              )
            Row().width(1).height('100%').backgroundColor('#88FFFFFF')
              .position({ x: '33%', y: 0 })
            Row().width(1).height('100%').backgroundColor('#88FFFFFF')
              .position({ x: '66%', y: 0 })
            Row().width('100%').height(1).backgroundColor('#88FFFFFF')
              .position({ x: 0, y: '33%' })
            Row().width('100%').height(1).backgroundColor('#88FFFFFF')
              .position({ x: 0, y: '66%' })
            this.cropHandle('top_left', Alignment.TopStart)
            this.cropHandle('top_right', Alignment.TopEnd)
            this.cropHandle('bottom_left', Alignment.BottomStart)
            this.cropHandle('bottom_right', Alignment.BottomEnd)
          }
          .position({ x: `${this.crop.left * 100}%`, y: `${this.crop.top * 100}%` })
          .width(this.cropWidthPercent()).height(this.cropHeightPercent())
          .border({ width: 2, color: '#FFFFFFFF' })
        }
        .width(this.frameWidth()).height(this.frameHeight())
      }
      .width('100%').height(240).clip(true)
      .backgroundColor('#FF172126').borderRadius(14)
      .onAreaChange((_oldArea: Area, newArea: Area) => {
        this.previewWidth = Math.max(1, Number(newArea.width) - 20)
      })

      Text('裁剪比例').fontSize(12).fontWeight(FontWeight.Medium)
        .fontColor($r('app.color.text_primary')).width('100%')
      Scroll() {
        Row({ space: 8 }) {
          ForEach(IMAGE_ASPECT_OPTIONS, (item: ImageAspectOption) => {
            Button(item.label)
              .height(36).fontSize(11).padding({ left: 14, right: 14 })
              .enabled(!this.busy)
              .backgroundColor(this.aspectLabel === item.label ?
                $r('app.color.primary') : $r('app.color.surface_muted'))
              .fontColor(this.aspectLabel === item.label ?
                $r('app.color.text_on_primary') : $r('app.color.text_body'))
              .onClick(() => this.chooseAspect(item.label, item.ratio))
          })
        }
      }.scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')

      Row({ space: 8 }) {
        this.toolButton('↶ 左转', false, () => this.rotateEditor(-90))
        this.toolButton('↷ 右转', false, () => this.rotateEditor(90))
        this.toolButton('↔ 水平', this.flipHorizontal, () => this.flipHorizontal = !this.flipHorizontal)
        this.toolButton('↕ 垂直', this.flipVertical, () => this.flipVertical = !this.flipVertical)
      }.width('100%')

      Row({ space: 10 }) {
        Button('取消').layoutWeight(1).height(44).enabled(!this.busy)
          .backgroundColor($r('app.color.surface_muted')).fontColor($r('app.color.text_body'))
          .onClick(this.onCancel)
        Button(this.busy ? '处理中…' : '应用编辑').layoutWeight(1).height(44).enabled(!this.busy)
          .backgroundColor($r('app.color.primary')).fontColor($r('app.color.text_on_primary'))
          .onClick(() => this.apply())
      }.width('100%')
    }
    .width('100%').padding(14)
    .backgroundColor($r('app.color.surface_soft'))
    .border({ width: 1, color: $r('app.color.border') }).borderRadius(16)
  }
}
