import picker from '@ohos.file.picker'
import fs from '@ohos.file.fs'
import { common } from '@kit.AbilityKit'
import { fileUri } from '@kit.CoreFileKit'
import { systemShare } from '@kit.ShareKit'
import { ExportItem } from '../model/ExportResult'
import { exportFileName } from './ExportApi'

function safeCacheFileName(name: string): string {
  const cleaned: string = name.replace(/[^A-Za-z0-9._-]/g, '_')
  return cleaned.length > 0 ? cleaned : 'molecule-export.bin'
}
function exportUtd(item: ExportItem): string {
  if (item.format === 'pdf') return 'com.adobe.pdf'
  if (item.format === 'json') return 'general.json'
  if (item.format === 'csv') return 'general.comma-separated-values-text'
  if (item.format === 'zip' || (item.contentType !== null && item.contentType === 'application/zip')) {
    return 'general.archive'
  }
  return 'general.file'
}

export class ExportFileService {
  static async save(context: common.UIAbilityContext, item: ExportItem, content: ArrayBuffer): Promise<string> {
    const options = new picker.DocumentSaveOptions()
    options.newFileNames = [exportFileName(item)]
    const documentPicker = new picker.DocumentViewPicker(context)
    const uris: string[] = await documentPicker.save(options)
    if (uris.length === 0) return ''
    ExportFileService.write(uris[0], content)
    return uris[0]
  }

  static async share(context: common.UIAbilityContext, item: ExportItem, content: ArrayBuffer): Promise<void> {
    const name: string = safeCacheFileName(exportFileName(item))
    const path: string = `${context.cacheDir}/${new Date().getTime()}-${name}`
    ExportFileService.write(path, content)
    const uri: string = fileUri.getUriFromPath(path)
    const record: systemShare.SharedRecord = {
      utd: exportUtd(item),
      uri: uri,
      title: item.label,
      label: name,
      description: `Molecule Vision ${item.label}`
    }
    const controller = new systemShare.ShareController(new systemShare.SharedData(record))
    await controller.show(context, {
      selectionMode: systemShare.SelectionMode.SINGLE,
      previewMode: systemShare.SharePreviewMode.DETAIL
    })
  }

  private static write(pathOrUri: string, content: ArrayBuffer): void {
    const file = fs.openSync(pathOrUri, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC)
    try {
      fs.writeSync(file.fd, content)
    } finally {
      fs.closeSync(file)
    }
  }
}
