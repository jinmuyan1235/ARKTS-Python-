import preferences from '@ohos.data.preferences'
import { SecureStore } from './SecureStore'
import { planSensitiveMigration, SensitiveMigrationPlan } from './AppUtils'

export const DEFAULT_API_BASE_URL: string = 'http://192.168.5.13:8000'

export interface StoredSettings {
  apiBaseUrl: string
  apiKey: string
  activeJobId: string
  authToken: string
}

export class SettingsStore {
  private static readonly STORE_NAME: string = 'molecule_vision_mobile'
  private static readonly BASE_URL_KEY: string = 'api_base_url'
  private static readonly API_KEY_KEY: string = 'api_key'
  private static readonly ACTIVE_JOB_KEY: string = 'active_job_id'
  private static readonly AUTH_TOKEN_KEY: string = 'auth_token'

  static async load(context: Context): Promise<StoredSettings> {
    const store = await preferences.getPreferences(context, SettingsStore.STORE_NAME)
    let apiKey = await SecureStore.readApiKey()
    let authToken = await SecureStore.readAuthToken()
    const legacyApiKey = await store.get(SettingsStore.API_KEY_KEY, '') as string
    const legacyAuthToken = await store.get(SettingsStore.AUTH_TOKEN_KEY, '') as string
    const migration: SensitiveMigrationPlan =
      planSensitiveMigration(apiKey, authToken, legacyApiKey, legacyAuthToken)
    if (apiKey.length === 0 && migration.apiKey.length > 0) {
      await SecureStore.saveApiKey(migration.apiKey)
    }
    if (authToken.length === 0 && migration.authToken.length > 0) {
      await SecureStore.saveAuthToken(migration.authToken)
    }
    apiKey = migration.apiKey
    authToken = migration.authToken
    if (migration.clearLegacy) {
      await store.delete(SettingsStore.API_KEY_KEY)
      await store.delete(SettingsStore.AUTH_TOKEN_KEY)
      await store.flush()
    }
    return {
      apiBaseUrl: await store.get(SettingsStore.BASE_URL_KEY, DEFAULT_API_BASE_URL) as string,
      apiKey: apiKey,
      activeJobId: await store.get(SettingsStore.ACTIVE_JOB_KEY, '') as string,
      authToken: authToken
    }
  }

  static async saveConnection(context: Context, apiBaseUrl: string, apiKey: string): Promise<void> {
    const store = await preferences.getPreferences(context, SettingsStore.STORE_NAME)
    await store.put(SettingsStore.BASE_URL_KEY, apiBaseUrl.trim())
    await store.flush()
    await SecureStore.saveApiKey(apiKey)
  }

  static async saveActiveJob(context: Context, jobId: string): Promise<void> {
    const store = await preferences.getPreferences(context, SettingsStore.STORE_NAME)
    await store.put(SettingsStore.ACTIVE_JOB_KEY, jobId)
    await store.flush()
  }

  static async saveAuthToken(context: Context, token: string): Promise<void> {
    await SecureStore.saveAuthToken(token)
  }
}
