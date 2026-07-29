import { asset } from '@kit.AssetStoreKit'
import { util } from '@kit.ArkTS'

const API_KEY_ALIAS: string = 'molecule_vision_api_key'
const AUTH_TOKEN_ALIAS: string = 'molecule_vision_auth_token'
const PASSWORD_ALIAS_PREFIX: string = 'molecule_vision_password_'

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

  private static async write(alias: string, value: string, trimValue: boolean = true): Promise<void> {
    const normalized = trimValue ? value.trim() : value
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

  private static passwordAlias(username: string): string {
    return `${PASSWORD_ALIAS_PREFIX}${username.trim().toLowerCase()}`
  }

  static readPassword(username: string): Promise<string> {
    return SecureStore.read(SecureStore.passwordAlias(username))
  }

  static savePassword(username: string, value: string): Promise<void> {
    return SecureStore.write(SecureStore.passwordAlias(username), value, false)
  }

  static removePassword(username: string): Promise<void> {
    return SecureStore.remove(SecureStore.passwordAlias(username))
  }

  static async clearSession(): Promise<void> {
    await SecureStore.remove(AUTH_TOKEN_ALIAS)
  }
}
