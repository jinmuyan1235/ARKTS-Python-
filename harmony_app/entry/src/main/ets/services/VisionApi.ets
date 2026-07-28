import {
  AuthResult,
  ChangePasswordResult,
  HealthResult,
  HistoryPage,
  ReviewRequest,
  UserProfile,
  VisionAnalysisResult,
  VisionJob,
  VisionSample
} from '../model/VisionResult'
import { ApiClient } from './ApiClient'
import { AuthApi } from './AuthApi'
import { JobApi } from './JobApi'
import { AnalysisApi } from './AnalysisApi'

export { ApiError } from './ApiClient'

/**
 * Compatibility facade for pages that have not migrated to domain APIs yet.
 * New code should import AuthApi, JobApi or AnalysisApi directly.
 */
export class VisionApi {
  static setSessionToken(token: string): void { ApiClient.setSessionToken(token) }

  static register(baseUrl: string, apiKey: string, username: string, displayName: string,
    password: string, role: string): Promise<AuthResult> {
    return AuthApi.register(baseUrl, apiKey, username, displayName, password, role)
  }

  static login(baseUrl: string, apiKey: string, username: string, password: string): Promise<AuthResult> {
    return AuthApi.login(baseUrl, apiKey, username, password)
  }

  static me(baseUrl: string, apiKey: string): Promise<UserProfile> { return AuthApi.me(baseUrl, apiKey) }

  static updateProfile(baseUrl: string, apiKey: string, displayName: string, role: string): Promise<UserProfile> {
    return AuthApi.updateProfile(baseUrl, apiKey, displayName, role)
  }

  static changePassword(baseUrl: string, apiKey: string, oldPassword: string,
    newPassword: string): Promise<ChangePasswordResult> {
    return AuthApi.changePassword(baseUrl, apiKey, oldPassword, newPassword)
  }

  static members(baseUrl: string, apiKey: string): Promise<UserProfile[]> { return AuthApi.members(baseUrl, apiKey) }
  static logout(baseUrl: string, apiKey: string): Promise<void> { return AuthApi.logout(baseUrl, apiKey) }

  static uploadAvatar(baseUrl: string, apiKey: string, imageUri: string): Promise<UserProfile> {
    return AuthApi.uploadAvatar(baseUrl, apiKey, imageUri)
  }

  static health(baseUrl: string, apiKey: string): Promise<HealthResult> { return JobApi.health(baseUrl, apiKey) }
  static samples(baseUrl: string, apiKey: string): Promise<VisionSample[]> { return JobApi.samples(baseUrl, apiKey) }

  static createSampleJob(baseUrl: string, apiKey: string, sampleId: string): Promise<VisionJob> {
    return JobApi.createSampleJob(baseUrl, apiKey, sampleId)
  }

  static createImageJob(baseUrl: string, apiKey: string, imageUri: string): Promise<VisionJob> {
    return JobApi.createImageJob(baseUrl, apiKey, imageUri)
  }

  static getJob(baseUrl: string, apiKey: string, jobId: string): Promise<VisionJob> {
    return JobApi.getJob(baseUrl, apiKey, jobId)
  }

  static analyzeSmiles(baseUrl: string, apiKey: string, smiles: string): Promise<VisionAnalysisResult> {
    return AnalysisApi.analyzeSmiles(baseUrl, apiKey, smiles)
  }

  static analyses(baseUrl: string, apiKey: string, query: string, status: string, favoritesOnly: boolean,
    offset: number, scope: string = 'all', ownerUserId: string = ''): Promise<HistoryPage> {
    return AnalysisApi.analyses(baseUrl, apiKey, query, status, favoritesOnly, offset, scope, ownerUserId)
  }

  static analysis(baseUrl: string, apiKey: string, analysisId: string): Promise<VisionAnalysisResult> {
    return AnalysisApi.analysis(baseUrl, apiKey, analysisId)
  }

  static favorite(baseUrl: string, apiKey: string, analysisId: string, favorite: boolean): Promise<void> {
    return AnalysisApi.favorite(baseUrl, apiKey, analysisId, favorite)
  }

  static deleteAnalysis(baseUrl: string, apiKey: string, analysisId: string): Promise<void> {
    return AnalysisApi.deleteAnalysis(baseUrl, apiKey, analysisId)
  }

  static review(baseUrl: string, apiKey: string, analysisId: string,
    payload: ReviewRequest): Promise<VisionAnalysisResult> {
    return AnalysisApi.review(baseUrl, apiKey, analysisId, payload)
  }
}
