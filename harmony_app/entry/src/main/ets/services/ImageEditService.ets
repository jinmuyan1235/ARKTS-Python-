import fs from '@ohos.file.fs'
import image from '@ohos.multimedia.image'
import { common } from '@kit.AbilityKit'
import { fileUri } from '@kit.CoreFileKit'
import {
  cropBoundsForRect,
  CropBounds,
  ImageEditSpec,
  isUnchangedImageEdit,
  normalizedRotationDegrees
} from './ImageEditGeometry'

export class ImageEditService {
  static async apply(
    context: common.UIAbilityContext,
    uri: string,
    spec: ImageEditSpec
  ): Promise<string> {
    if (isUnchangedImageEdit(spec)) return uri

    // file:// and PhotoAccessHelper URIs are both readable through fs.openSync.
    // Passing those URIs directly to createImageSource returns undefined on
    // some emulator builds, so keep the descriptor open for the whole decode.
    const input = fs.openSync(uri, fs.OpenMode.READ_ONLY)
    let source: image.ImageSource
    try {
      source = image.createImageSource(input.fd)
    } catch (_) {
      fs.closeSync(input)
      throw new Error('无法打开当前图片，请重新选择。')
    }
    if (source === undefined) {
      fs.closeSync(input)
      throw new Error('无法解码当前图片，请重新选择。')
    }

    let pixelMap: image.PixelMap | undefined = undefined
    try {
      pixelMap = await source.createPixelMap()
      if (pixelMap === undefined) {
        throw new Error('图片解码失败，请换用 PNG 或 JPG 图片。')
      }
      const rotation: number = normalizedRotationDegrees(spec.rotationDegrees)
      if (rotation !== 0) {
        await pixelMap.rotate(rotation)
      }
      if (spec.flipHorizontal || spec.flipVertical) {
        await pixelMap.flip(spec.flipHorizontal, spec.flipVertical)
      }
      const info: image.ImageInfo = await pixelMap.getImageInfo()
      const bounds: CropBounds = cropBoundsForRect(info.size.width, info.size.height, spec.crop)
      if (bounds.x > 0 || bounds.y > 0 ||
        bounds.width < info.size.width || bounds.height < info.size.height) {
        const region: image.Region = {
          x: bounds.x,
          y: bounds.y,
          size: { width: bounds.width, height: bounds.height }
        }
        await pixelMap.crop(region)
      }
      return await ImageEditService.persist(context, pixelMap)
    } finally {
      if (pixelMap !== undefined) {
        await pixelMap.release()
      }
      try {
        await source.release()
      } finally {
        fs.closeSync(input)
      }
    }
  }

  private static async persist(
    context: common.UIAbilityContext,
    pixelMap: image.PixelMap
  ): Promise<string> {
    const packer: image.ImagePacker = image.createImagePacker()
    try {
      const content: ArrayBuffer = await packer.packing(pixelMap, {
        format: 'image/png',
        quality: 100
      })
      const path: string = `${context.cacheDir}/molecule-vision-edited-${Date.now()}.png`
      const output = fs.openSync(path, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC)
      try {
        fs.writeSync(output.fd, content)
      } finally {
        fs.closeSync(output)
      }
      return fileUri.getUriFromPath(path)
    } finally {
      await packer.release()
    }
  }
}
