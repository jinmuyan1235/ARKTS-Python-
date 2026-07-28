import { UserProfile } from '../model/VisionResult'
import { MoleculeBackdrop, SectionHeader, StatusPill } from './CommonStates'
import { PageHeader } from './PageChrome'

interface SmilesExample {
  name: string
  value: string
}

@Component
export struct SmilesView {
  @Prop currentUser: UserProfile | undefined = undefined
  @Link smilesInput: string
  @Prop analyzing: boolean = false
  private examples: SmilesExample[] = [
    { name: '阿司匹林', value: 'CC(=O)Oc1ccccc1C(=O)O' },
    { name: '咖啡因', value: 'Cn1c(=O)c2c(ncn2C)n(C)c1=O' },
    { name: '乙醇', value: 'CCO' }
  ]
  onOpenSettings: () => void = () => {}
  onAnalyze: () => void = () => {}

  build() {
    Stack() {
      MoleculeBackdrop()
      Scroll() {
        Column({ space: 16 }) {
          PageHeader({
            title: 'SMILES',
            subtitle: '校验分子表达式并生成可追溯的性质报告。',
            currentUser: this.currentUser,
            onOpenSettings: this.onOpenSettings
          })

          Column({ space: 14 }) {
            SectionHeader({ symbol: '⌬', title: '分子表达式', caption: '由电脑端 RDKit 完成合法性校验' })

            TextArea({ text: this.smilesInput, placeholder: '例如：CC(=O)Oc1ccccc1C(=O)O' })
              .width('100%')
              .height(160)
              .fontSize(15)
              .fontColor($r('app.color.text_primary'))
              .backgroundColor($r('app.color.surface_soft'))
              .border({ width: 1, color: $r('app.color.border') })
              .borderRadius(14)
              .onChange((value: string) => this.smilesInput = value)
              .accessibilityText('SMILES 输入框')

            Row() {
              StatusPill({ symbol: '✓', label: '非法结构不会写入历史', tone: 'success' })
              Blank()
              Text(`${this.smilesInput.length} 字符`).fontSize(11).fontColor($r('app.color.text_tertiary'))
            }
            .width('100%')

            Text('快速示例')
              .fontSize(12)
              .fontWeight(FontWeight.Medium)
              .fontColor($r('app.color.text_secondary'))
              .width('100%')
            Scroll() {
              Row({ space: 8 }) {
                ForEach(this.examples, (example: SmilesExample) => {
                  Text(example.name)
                    .fontSize(13)
                    .fontColor($r('app.color.primary'))
                    .padding({ left: 14, right: 14, top: 9, bottom: 9 })
                    .backgroundColor($r('app.color.primary_soft'))
                    .border({ width: 1, color: $r('app.color.border') })
                    .borderRadius(16)
                    .onClick(() => this.smilesInput = example.value)
                    .accessibilityText(`填入${example.name}的SMILES`)
                }, (example: SmilesExample) => example.name)
              }
            }
            .scrollable(ScrollDirection.Horizontal)
            .scrollBar(BarState.Off)
            .width('100%')

            Button(this.analyzing ? '正在校验并计算…' : '校验并计算分子性质')
              .width('100%')
              .height(52)
              .enabled(!this.analyzing && this.smilesInput.trim().length > 0)
              .fontColor($r('app.color.text_on_primary'))
              .backgroundColor($r('app.color.primary'))
              .onClick(this.onAnalyze)
              .accessibilityText('校验SMILES并计算分子性质')
          }
          .alignItems(HorizontalAlign.Start)
          .width('100%')
          .padding(18)
          .backgroundColor($r('app.color.surface'))
          .border({ width: 1, color: $r('app.color.border') })
          .borderRadius($r('app.float.card_radius'))
          .shadow({ radius: 10, color: $r('app.color.shadow'), offsetY: 3 })

          Column({ space: 10 }) {
            SectionHeader({ symbol: 'i', title: '输入提示', caption: '支持常见有机分子与离子结构' })
            Text('• 支持环闭合、芳香体系、电荷、同位素和立体化学标记。')
              .fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
            Text('• 计算完成后会自动进入结果页，并写入团队共享历史。')
              .fontSize(12).fontColor($r('app.color.text_secondary')).width('100%')
          }
          .alignItems(HorizontalAlign.Start)
          .width('100%')
          .padding(16)
          .backgroundColor($r('app.color.surface'))
          .borderRadius($r('app.float.card_radius'))
        }
        .width('100%')
        .padding({ left: 16, right: 16, top: 18, bottom: 28 })
      }
      .width('100%').height('100%')
      .scrollBar(BarState.Off)
    }
    .width('100%').height('100%')
    .backgroundColor($r('app.color.page_background'))
  }
}
