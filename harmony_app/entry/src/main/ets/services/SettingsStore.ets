import preferences from '@ohos.data.preferences'
import { SecureStore } from './SecureStore'
import { planSensitiveMigration, SensitiveMigrationPlan } from './AppUtils'

export const DEFAULT_API_BASE_URL: string = 'http://192.168.5.13:8000'

export interface StoredSettings {
  apiBaseUrl: string
  apiKey: string
  activeJobIds: string[]
  activeBatchJobId: string
  activeDocumentId: string
  activeDocumentJobId: string
  authToken: string
  lastUsername: string
  lastAccountType: string
  rememberPassword: boolean
  autoLogin: boolean
  rememberedUsernames: string[]
}

export interface StoredDocumentTask {
  documentId: string
  jobId: string
}

export function normalizeStoredDocumentTask(documentId: string, jobId: string): StoredDocumentTask {
  const normalizedDocumentId: string = documentId.trim()
  return {
    documentId: normalizedDocumentId,
    jobId: normalizedDocumentId.length === 0 ? '' : jobId.trim()
  }
}

export function normalizeStoredJobIds(jobIds: string[], legacyJobId: string = ''): string[] {
  const result: string[] = []
  const candidates: string[] = legacyJobId.trim().length > 0 ? [legacyJobId].concat(jobIds) : jobIds
  candidates.forEach((jobId: string) => {
    const normalized: string = jobId.trim()
    if (normalized.length > 0 && result.indexOf(normalized) < 0) {
      result.push(normalized)
    }
  })
  return result.slice(0, 20)
}

export class SettingsStore {
  private static readonly STORE_NAME: string = 'molecule_vision_mobile'
  private static readonly BASE_URL_KEY: string = 'api_base_url'
  private static readonly API_KEY_KEY: string = 'api_key'
  private static readonly ACTIVE_JOB_IDS_KEY: string = 'active_job_ids'
  // Kept only for one-time migration from the previous single-task version.
  private static readonly ACTIVE_JOB_KEY: string = 'active_job_id'
  private static readonly ACTIVE_BATCH_JOB_KEY: string = 'active_batch_job_id'
  private static readonly ACTIVE_DOCUMENT_ID_KEY: string = 'active_document_id'
  private static readonly ACTIVE_DOCUMENT_JOB_KEY: string = 'active_document_job_id'
  private static readonly AUTH_TOKEN_KEY: string = 'auth_token'
  private static readonly LAST_USERNAME_KEY: string = 'last_username'
  private static readonly LAST_ACCOUNT_TYPE_KEY: string = 'last_account_type'
  private static readonly REMEMBER_PASSWORD_KEY: string = 'remember_password'
  private static readonly AUTO_LOGIN_KEY: string = 'auto_login'
  private static readonly REMEMBERED_USERNAMES_KEY: string = 'remembered_usernames'

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
    const serializedJobIds = await store.get(SettingsStore.ACTIVE_JOB_IDS_KEY, '[]') as string
    const legacyJobId = await store.get(SettingsStore.ACTIVE_JOB_KEY, '') as string
    const activeJobIds: string[] = SettingsStore.parseJobIds(serializedJobIds, legacyJobId)
    if (legacyJobId.length > 0) {
      await store.put(SettingsStore.ACTIVE_JOB_IDS_KEY, JSON.stringify(activeJobIds))
      await store.delete(SettingsStore.ACTIVE_JOB_KEY)
      await store.flush()
    }
    const activeDocumentTask: StoredDocumentTask = normalizeStoredDocumentTask(
      await store.get(SettingsStore.ACTIVE_DOCUMENT_ID_KEY, '') as string,
      await store.get(SettingsStore.ACTIVE_DOCUMENT_JOB_KEY, '') as string
    )
    return {
      apiBaseUrl: await store.get(SettingsStore.BASE_URL_KEY, DEFAULT_API_BASE_URL) as string,
      apiKey: apiKey,
      activeJobIds: activeJobIds,
      activeBatchJobId: await store.get(SettingsStore.ACTIVE_BATCH_JOB_KEY, '') as string,
      activeDocumentId: activeDocumentTask.documentId,
      activeDocumentJobId: activeDocumentTask.jobId,
      authToken: authToken,
      lastUsername: await store.get(SettingsStore.LAST_USERNAME_KEY, '') as string,
      lastAccountType: await store.get(SettingsStore.LAST_ACCOUNT_TYPE_KEY, 'developer') as string,
      rememberPassword: await store.get(SettingsStore.REMEMBER_PASSWORD_KEY, false) as boolean,
      autoLogin: await store.get(SettingsStore.AUTO_LOGIN_KEY, false) as boolean,
      rememberedUsernames: SettingsStore.parseStringList(
        await store.get(SettingsStore.REMEMBERED_USERNAMES_KEY, '[]') as string
      )
    }
  }

  static async saveConnection(context: Context, apiBaseUrl: string, apiKey: string): Promise<void> {
    const store = await preferences.getPreferences(context, SettingsStore.STORE_NAME)
    await store.put(SettingsStore.BASE_URL_KEY, apiBaseUrl.trim())
    await store.flush()
    await SecureStore.saveApiKey(apiKey)
  }

  static async saveActiveJobs(context: Context, jobIds: string[]): Promise<void> {
    const store = await preferences.getPreferences(context, SettingsStore.STORE_NAME)
    const normalized: string[] = normalizeStoredJobIds(jobIds)
    await store.put(SettingsStore.ACTIVE_JOB_IDS_KEY, JSON.stringify(normalized))
    await store.flush()
  }

  static async saveAuthToken(context: Context, token: string): Promise<void> {
    await SecureStore.saveAuthToken(token)
  }

  static async saveActiveBatchJob(context: Context, jobId: string): Promise<void> {
    const store = await preferences.getPreferences(context, SettingsStore.STORE_NAME)
    await store.put(SettingsStore.ACTIVE_BATCH_JOB_KEY, jobId.trim())
    await store.flush()
  }

  static async saveActiveDocumentTask(context: Context, documentId: string, jobId: string): Promise<void> {
    const store = await preferences.getPreferences(context, SettingsStore.STORE_NAME)
    const task: StoredDocumentTask = normalizeStoredDocumentTask(documentId, jobId)
    await store.put(SettingsStore.ACTIVE_DOCUMENT_ID_KEY, task.documentId)
    await store.put(SettingsStore.ACTIVE_DOCUMENT_JOB_KEY, task.jobId)
    await store.flush()
  }

  static async saveLoginPreferences(
    context: Context,
    lastUsername: string,
    lastAccountType: string,
    rememberPassword: boolean,
    autoLogin: boolean,
    rememberedUsernames: string[]
  ): Promise<void> {
    const store = await preferences.getPreferences(context, SettingsStore.STORE_NAME)
    await store.put(SettingsStore.LAST_USERNAME_KEY, lastUsername.trim())
    await store.put(SettingsStore.LAST_ACCOUNT_TYPE_KEY,
      lastAccountType === 'developer' ? 'developer' : 'user')
    await store.put(SettingsStore.REMEMBER_PASSWORD_KEY, rememberPassword)
    await store.put(SettingsStore.AUTO_LOGIN_KEY, rememberPassword && autoLogin)
    await store.put(SettingsStore.REMEMBERED_USERNAMES_KEY, JSON.stringify(rememberedUsernames))
    await store.flush()
  }

  private static parseStringList(serialized: string): string[] {
    try {
      const parsed: Object = JSON.parse(serialized) as Object
      if (!Array.isArray(parsed)) return []
      const result: string[] = []
      const items: string[] = parsed as string[]
      items.forEach((value: string) => {
        const clean: string = value.trim()
        if (clean.length > 0 && result.indexOf(clean) < 0) result.push(clean)
      })
      return result
    } catch (_) {
      return []
    }
  }

  private static parseJobIds(serialized: string, legacyJobId: string): string[] {
    let values: string[] = []
    try {
      const parsed: Object = JSON.parse(serialized) as Object
      values = Array.isArray(parsed) ? parsed as string[] : []
    } catch (_) {
      values = []
    }
    return normalizeStoredJobIds(values, legacyJobId)
  }
}
