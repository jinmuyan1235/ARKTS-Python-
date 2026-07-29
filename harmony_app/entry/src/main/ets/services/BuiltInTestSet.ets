import fs from '@ohos.file.fs'
import { common } from '@kit.AbilityKit'
import { fileUri } from '@kit.CoreFileKit'

export interface BuiltInTestFiles {
  images: string[]
  document: string
}

const IMAGE_RAW_FILES: string[] = [
  'micro_gcpn_01.png',
  'micro_gcpn_02.png',
  'micro_jtvae_01.png',
  'micro_jtvae_02.png',
  'micro_selfies_01.png',
  'micro_selfies_02.png',
  'micro_screenshot.png',
  'micro_wechat.png'
]

const DOCUMENT_RAW_FILE: string = 'micro_ocsr_document.pdf'

/** Extracts HAP-bundled examples to app cache so existing upload code can read them as files. */
export class BuiltInTestSet {
  static async prepare(context: common.UIAbilityContext): Promise<BuiltInTestFiles> {
    const directory: string = `${context.cacheDir}/micro_test_set`
    if (!fs.accessSync(directory)) {
      fs.mkdirSync(directory, true)
    }
    const images: string[] = []
    for (let index: number = 0; index < IMAGE_RAW_FILES.length; index++) {
      images.push(await BuiltInTestSet.extract(context, directory, IMAGE_RAW_FILES[index]))
    }
    return {
      images: images,
      document: await BuiltInTestSet.extract(context, directory, DOCUMENT_RAW_FILE)
    }
  }

  private static async extract(
    context: common.UIAbilityContext,
    directory: string,
    rawName: string
  ): Promise<string> {
    const destination: string = `${directory}/${rawName}`
    const content: Uint8Array = await context.resourceManager.getRawFileContent(rawName)
    const file = fs.openSync(destination, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC)
    try {
      fs.writeSync(file.fd, content.buffer)
    } finally {
      fs.closeSync(file)
    }
    return fileUri.getUriFromPath(destination)
  }
}
