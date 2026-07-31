import router from '@ohos.router'
import { MoleculeBackdrop, SectionHeader, StatusPill } from '../components/CommonStates'

interface GlossaryTerm {
  term: string
  english: string
  category: string
  summary: string
  projectUse: string
  caution: string
}

const CATEGORIES: string[] = ['全部', '基础', '结构表达', '性质与规则', '模型与复核']

const GLOSSARY_TERMS: GlossaryTerm[] = [
  {
    term: '化学信息学', english: 'Cheminformatics', category: '基础',
    summary: '使用计算机表示、检索、比较和分析化学结构与相关数据的领域。',
    projectUse: '本项目把结构图片转换为可计算的分子表达式，再进行性质计算、检索式存档和团队复核。',
    caution: '它帮助整理和分析信息，但不能代替化学实验。'
  },
  {
    term: '光学化学结构识别', english: 'OCSR', category: '基础',
    summary: '让模型从二维结构图中识别原子、化学键和连接关系，并输出机器可读结构。',
    projectUse: 'DECIMER 或 MolScribe 接收上传图片，返回预测 SMILES；RDKit 随后负责解析和校验。',
    caution: '模糊、裁切、多分子或非常规标注都可能导致识别错误，因此图片结果需要人工复核。'
  },
  {
    term: 'RDKit', english: 'RDKit', category: '基础',
    summary: '常用的开源化学信息学工具包，可解析分子、计算描述符并绘制结构图。',
    projectUse: '本项目用它验证 SMILES、规范化结构、生成重绘图和计算基础性质。',
    caution: '“能够被 RDKit 解析”只代表语法和结构基本有效，不代表该分子真实存在、安全或有效。'
  },
  {
    term: 'SMILES', english: 'Simplified Molecular Input Line Entry System', category: '结构表达',
    summary: '用一行字符描述原子及其连接关系，例如乙醇可写为 CCO。',
    projectUse: '它是识别结果、手动分析、结构纠正和复制分享中的核心文本表示。',
    caution: '同一分子可能有多种合法写法；字符很相似也可能代表完全不同的结构。'
  },
  {
    term: '规范 SMILES', english: 'Canonical SMILES', category: '结构表达',
    summary: '由同一套软件规则为分子生成的较稳定 SMILES 表达，便于去重和比较。',
    projectUse: '后端会把可解析结构转换为规范形式并记录标准化是否改变了原始输入。',
    caution: '不同软件的规范化算法可能得到不同字符串，不能仅凭字符串不同断定分子不同。'
  },
  {
    term: '异构 SMILES', english: 'Isomeric SMILES', category: '结构表达',
    summary: '在 SMILES 中进一步表达手性、双键几何等立体化学信息。',
    projectUse: '结果页在后端能够确定时展示该字段，帮助检查立体中心是否被保留。',
    caution: '原图没有清晰标出楔形键等信息时，模型不能可靠补全立体化学。'
  },
  {
    term: 'InChI 与 InChIKey', english: 'International Chemical Identifier', category: '结构表达',
    summary: 'InChI 是分层结构标识，InChIKey 是便于数据库检索的短哈希形式。',
    projectUse: '结果详情用它辅助确认化学身份并支持复制。',
    caution: 'InChIKey 不是可逆的完整结构，也不是化合物名称。'
  },
  {
    term: '分子描述符', english: 'Molecular Descriptors', category: '性质与规则',
    summary: '从分子结构计算出的数值特征，如分子量、LogP、TPSA、氢键供体和受体数。',
    projectUse: '结果页用描述符快速概括分子大小、极性、亲脂性和成键特征。',
    caution: '描述符是结构计算值或近似值，不等同于实验测量结果。'
  },
  {
    term: 'LogP', english: 'Partition Coefficient', category: '性质与规则',
    summary: '描述中性分子在油相与水相之间分配倾向的指标，常用于理解亲脂性。',
    projectUse: '本项目通过 RDKit 计算估计值，并用于 Lipinski 规则检查。',
    caution: 'LogP 不是溶解度，也不能单独判断吸收、安全性或药效。'
  },
  {
    term: 'TPSA', english: 'Topological Polar Surface Area', category: '性质与规则',
    summary: '根据分子拓扑估算的极性表面积，常用于粗略理解渗透和转运特征。',
    projectUse: '与分子量、LogP、氢键计数一起展示，帮助形成结构性质概览。',
    caution: 'TPSA 只是经验性结构指标，不能直接预测人体行为。'
  },
  {
    term: 'Lipinski 五规则', english: 'Rule of Five', category: '性质与规则',
    summary: '根据分子量、LogP、氢键供体和受体等指标进行的经验性筛选规则。',
    projectUse: '结果页逐项显示实际值、限制和是否通过，帮助发现明显超限项。',
    caution: '通过不等于安全、有效或可成药；不通过也不代表分子一定没有价值。'
  },
  {
    term: 'ADMET', english: 'Absorption, Distribution, Metabolism, Excretion, Toxicity', category: '性质与规则',
    summary: '吸收、分布、代谢、排泄和毒性相关性质的统称。',
    projectUse: '只有配置了相应模型时才展示预测值、概率与适用域；未启用时明确显示不可用。',
    caution: '预测不能替代体外、动物或临床实验，也不能作为医疗建议。'
  },
  {
    term: '置信度', english: 'Confidence', category: '模型与复核',
    summary: '模型对自身输出把握程度的数值或等级。',
    projectUse: '若模型提供经过校准的置信度，结果页会展示；否则显示“模型未提供”。',
    caution: '高置信度仍可能出错，低置信度也不等同于结构必然错误。'
  },
  {
    term: '图像质量分', english: 'Image Quality Score', category: '模型与复核',
    summary: '根据清晰度、对比度、墨迹占比、边界裁切等因素评估输入图片是否适合识别。',
    projectUse: '用于解释识别风险和给出重新上传建议，不参与证明化学结构正确。',
    caution: '图片清晰只能提高可识别性，不能保证模型输出正确。'
  },
  {
    term: '适用域', english: 'Applicability Domain', category: '模型与复核',
    summary: '模型训练数据能够较可靠覆盖的化学空间或输入范围。',
    projectUse: 'ADMET 预测若提供适用域，会提示当前分子是否接近训练数据分布。',
    caution: '超出适用域的预测不应被当作可靠结论。'
  },
  {
    term: '人工复核', english: 'Human Review', category: '模型与复核',
    summary: '由组员对原图、模型预测和重绘结构进行对照后确认、修正或标记无法确认。',
    projectUse: '复核操作记录成员、时间、原因和最终 SMILES，形成可追溯审计。',
    caution: '“已确认”表示组员完成了结构核对，不代表完成实验鉴定或安全性评价。'
  }
]

@Entry
@Component
struct KnowledgeGuide {
  @State query: string = ''
  @State category: string = '全部'
  @State expandedTerm: string = ''

  private leave(): void {
    router.back()
  }

  private visibleTerms(): GlossaryTerm[] {
    const keyword: string = this.query.trim().toLowerCase()
    return GLOSSARY_TERMS.filter((item: GlossaryTerm) => {
      const categoryMatches: boolean = this.category === '全部' || item.category === this.category
      const keywordMatches: boolean = keyword.length === 0 ||
        item.term.toLowerCase().includes(keyword) ||
        item.english.toLowerCase().includes(keyword) ||
        item.summary.toLowerCase().includes(keyword)
      return categoryMatches && keywordMatches
    })
  }

  private toggleTerm(term: string): void {
    this.expandedTerm = this.expandedTerm === term ? '' : term
  }

  @Builder
  private workflowStep(number: string, title: string, detail: string) {
    Row({ space: 11 }) {
      Text(number)
        .fontSize(13).fontWeight(FontWeight.Bold).fontColor($r('app.color.text_on_primary'))
        .textAlign(TextAlign.Center).width(30).height(30)
        .backgroundColor($r('app.color.primary')).borderRadius(15)
      Column({ space: 2 }) {
        Text(title).fontSize(14).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
        Text(detail).fontSize(12).fontColor($r('app.color.text_secondary'))
      }
      .alignItems(HorizontalAlign.Start).layoutWeight(1)
    }
    .alignItems(VerticalAlign.Top).width('100%')
  }

  @Builder
  private termCard(item: GlossaryTerm) {
    Column({ space: 10 }) {
      Row({ space: 8 }) {
        Column({ space: 2 }) {
          Text(item.term).fontSize(17).fontWeight(FontWeight.Bold).fontColor($r('app.color.text_primary'))
          Text(item.english).fontSize(11).fontColor($r('app.color.text_tertiary')).maxLines(2)
        }
        .alignItems(HorizontalAlign.Start).layoutWeight(1)
        StatusPill({ symbol: '⌬', label: item.category, tone: 'primary' })
        Text(this.expandedTerm === item.term ? '⌃' : '⌄')
          .fontSize(16).fontWeight(FontWeight.Bold).fontColor($r('app.color.primary'))
          .width(24).height(28).textAlign(TextAlign.Center)
      }
      Text(item.summary)
        .fontSize(13).lineHeight(20).fontColor($r('app.color.text_body')).width('100%')
        .maxLines(this.expandedTerm === item.term ? 8 : 2)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
      if (this.expandedTerm === item.term) {
        Column({ space: 4 }) {
          Text('在本项目中').fontSize(12).fontWeight(FontWeight.Medium).fontColor($r('app.color.primary'))
          Text(item.projectUse).fontSize(12).lineHeight(18).fontColor($r('app.color.text_secondary'))
        }
        .alignItems(HorizontalAlign.Start).width('100%').padding(11)
        .backgroundColor($r('app.color.primary_faint')).borderRadius(12)
        Row({ space: 7 }) {
          Text('!').fontSize(12).fontWeight(FontWeight.Bold).fontColor($r('app.color.warning'))
          Text(item.caution).fontSize(11).lineHeight(17).fontColor($r('app.color.text_secondary')).layoutWeight(1)
        }
        .alignItems(VerticalAlign.Top).width('100%')
      }
    }
    .alignItems(HorizontalAlign.Start).width('100%').padding(16)
    .backgroundColor($r('app.color.surface'))
    .border({ width: 1, color: $r('app.color.border') })
    .borderRadius($r('app.float.card_radius'))
    .onClick(() => this.toggleTerm(item.term))
    .accessibilityText(`${item.term}，${this.expandedTerm === item.term ? '已展开' : '已折叠'}。${item.summary}`)
  }

  build() {
    Stack() {
      MoleculeBackdrop()
      Column({ space: 0 }) {
        Row({ space: 10 }) {
          Column() {
            Text('‹').fontSize(30).fontColor($r('app.color.primary'))
          }
          .justifyContent(FlexAlign.Center).alignItems(HorizontalAlign.Center)
          .width(44).height(44).backgroundColor($r('app.color.primary_soft')).borderRadius(14)
          .onClick(() => this.leave()).accessibilityText('返回上一页')
          Column({ space: 2 }) {
            Text('术语与项目指南')
              .fontSize($r('app.float.title_font_size')).fontWeight(FontWeight.Bold)
              .fontColor($r('app.color.text_primary'))
            Text('读懂 Molecule Vision 的结果与边界')
              .fontSize($r('app.float.caption_font_size')).fontColor($r('app.color.text_secondary'))
          }
          .alignItems(HorizontalAlign.Start).layoutWeight(1)
          Text('⌬').fontSize(26).fontColor($r('app.color.accent')).accessibilityLevel('no')
        }
        .width('100%').padding({ left: 16, right: 16, top: 14, bottom: 12 })
        .backgroundColor($r('app.color.nav_background'))

        Scroll() {
          Column({ space: 16 }) {
            Column({ space: 12 }) {
              SectionHeader({ symbol: '⌬', title: '这个项目在做什么', caption: '从结构图片到可复核的分子数据' })
              Text('Molecule Vision 把计算机视觉与化学信息学连接起来：模型负责“看图”，化学工具负责“读结构”，组员负责最终核对。')
                .fontSize(13).lineHeight(20).fontColor($r('app.color.text_body')).width('100%')
              this.workflowStep('1', '上传或选择示例', '输入一张二维分子结构图，或手动填写 SMILES。')
              this.workflowStep('2', '模型识别', 'OCSR 模型预测原子、化学键和 SMILES。')
              this.workflowStep('3', '化学计算', 'RDKit 校验结构、规范化并计算描述符与规则。')
              this.workflowStep('4', '人工复核', '组员对照原图和重绘结构，确认、修正或标记无法确认。')
            }
            .alignItems(HorizontalAlign.Start).width('100%').padding(17)
            .backgroundColor($r('app.color.surface'))
            .border({ width: 1, color: $r('app.color.border') })
            .borderRadius($r('app.float.card_radius'))

            Column({ space: 11 }) {
              SectionHeader({ symbol: '?', title: '查找术语', caption: `${this.visibleTerms().length} 个相关词条` })
              TextInput({ text: this.query, placeholder: '搜索 SMILES、LogP、置信度…' })
                .width('100%').height(46).backgroundColor($r('app.color.surface_soft'))
                .onChange((value: string) => this.query = value)
                .accessibilityText('搜索化学信息学术语')
              Scroll() {
                Row({ space: 8 }) {
                  ForEach(CATEGORIES, (item: string) => {
                    Button(item)
                      .height(44).fontSize(12)
                      .fontColor(this.category === item ? $r('app.color.text_on_primary') : $r('app.color.text_body'))
                      .backgroundColor(this.category === item ? $r('app.color.primary') : $r('app.color.surface_muted'))
                      .borderRadius(18)
                      .onClick(() => this.category = item)
                      .accessibilityText(`筛选${item}术语`)
                  }, (item: string) => item)
                }
              }
              .scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')
            }
            .alignItems(HorizontalAlign.Start).width('100%').padding(16)
            .backgroundColor($r('app.color.surface'))
            .borderRadius($r('app.float.card_radius'))

            if (this.visibleTerms().length === 0) {
              Column({ space: 8 }) {
                Text('⌕').fontSize(32).fontColor($r('app.color.primary'))
                Text('没有找到相关术语').fontSize(16).fontWeight(FontWeight.Medium).fontColor($r('app.color.text_primary'))
                Text('尝试缩短关键词或切换到“全部”。').fontSize(12).fontColor($r('app.color.text_secondary'))
              }
              .width('100%').padding(24).backgroundColor($r('app.color.surface')).borderRadius($r('app.float.card_radius'))
            } else {
              ForEach(this.visibleTerms(), (item: GlossaryTerm) => {
                this.termCard(item)
              }, (item: GlossaryTerm) => item.term)
            }

            Column({ space: 8 }) {
              SectionHeader({ symbol: '!', title: '使用边界', caption: '先确认结构，再解释性质' })
              Text('识别结果、描述符、Lipinski 与 ADMET 都属于辅助信息。它们不能证明化合物身份、纯度、安全性、疗效或可成药性，也不能替代实验、专业鉴定和医疗建议。')
                .fontSize(12).lineHeight(19).fontColor($r('app.color.text_body')).width('100%')
            }
            .alignItems(HorizontalAlign.Start).width('100%').padding(16)
            .backgroundColor($r('app.color.warning_soft'))
            .border({ width: 1, color: $r('app.color.border') })
            .borderRadius($r('app.float.card_radius'))
          }
          .width('100%').padding({ left: 16, right: 16, top: 8, bottom: 28 })
        }
        .width('100%').layoutWeight(1).scrollBar(BarState.Off).edgeEffect(EdgeEffect.Spring)
      }
      .width('100%').height('100%')
    }
    .width('100%').height('100%').backgroundColor($r('app.color.page_background'))
  }
}
