import { asset } from '@kit.AssetStoreKit'
import { util } from '@kit.ArkTS'

const API_KEY_ALIAS: string = 'molecule_vision_api_key'
const AUTH_TOKEN_ALIAS: string = 'molecule_vision_auth_token'

function bytes(value: string): Uint8Array {
  return new util.TextEncoder().encodeInto(value)
}

function text(value: Uint8Array): string {
  return new util.TextDecoder().decode(value)
}

export class SecureStore {
  private static queryFor(alias: string): asset.AssetMap {
    const query: asset.AssetMap = new Map()
    query.set(asset.Tag.ALIAS, bytes(alias))
    query.set(asset.Tag.RETURN_TYPE, asset.ReturnType.ALL)
    return query
  }

  private static async read(alias: string): Promise<string> {
    try {
      const records = await asset.query(SecureStore.queryFor(alias))
      if (records.length === 0) {
        return ''
      }
      const secret = records[0].get(asset.Tag.SECRET)
      return secret instanceof Uint8Array ? text(secret) : ''
    } catch (_) {
      return ''
    }
  }

  private static async write(alias: string, value: string): Promise<void> {
    const normalized = value.trim()
    await SecureStore.remove(alias)
    if (normalized.length === 0) {
      return
    }
    const attributes: asset.AssetMap = new Map()
    attributes.set(asset.Tag.ALIAS, bytes(alias))
    attributes.set(asset.Tag.SECRET, bytes(normalized))
    attributes.set(asset.Tag.ACCESSIBILITY, asset.Accessibility.DEVICE_FIRST_UNLOCKED)
    await asset.add(attributes)
  }

  private static async remove(alias: string): Promise<void> {
    const query: asset.AssetMap = new Map()
    query.set(asset.Tag.ALIAS, bytes(alias))
    try {
      await asset.remove(query)
    } catch (_) {
      // Removing a missing asset is intentionally idempotent.
    }
  }

  static readApiKey(): Promise<string> {
    return SecureStore.read(API_KEY_ALIAS)
  }

  static saveApiKey(value: string): Promise<void> {
    return SecureStore.write(API_KEY_ALIAS, value)
  }

  static readAuthToken(): Promise<string> {
    return SecureStore.read(AUTH_TOKEN_ALIAS)
  }

  static saveAuthToken(value: string): Promise<void> {
    return SecureStore.write(AUTH_TOKEN_ALIAS, value)
  }

  static async clearSession(): Promise<void> {
    await SecureStore.remove(AUTH_TOKEN_ALIAS)
  }
}
