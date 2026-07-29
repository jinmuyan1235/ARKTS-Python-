import http from '@ohos.net.http'
import { AuthResult, ChangePasswordResult, UserProfile } from '../model/VisionResult'
import { ApiClient, MultipartImageBody } from './ApiClient'

/** Account, session and team-member endpoints. */
export class AuthApi {
  static async register(
    baseUrl: string,
    apiKey: string,
    username: string,
    displayName: string,
    password: string,
    role: string,
    accountType: string
  ): Promise<AuthResult> {
    return ApiClient.requestJson<AuthResult>(baseUrl, apiKey, '/api/v1/auth/register', http.RequestMethod.POST,
      '注册失败', 'application/json', JSON.stringify({
        username: username, displayName: displayName, password: password, role: role, accountType: accountType
      }))
  }

  static async login(baseUrl: string, apiKey: string, username: string, password: string,
    accountType: string): Promise<AuthResult> {
    return ApiClient.requestJson<AuthResult>(baseUrl, apiKey, '/api/v1/auth/login', http.RequestMethod.POST,
      '登录失败', 'application/json', JSON.stringify({
        username: username, password: password, accountType: accountType
      }))
  }

  static async me(baseUrl: string, apiKey: string): Promise<UserProfile> {
    return ApiClient.requestJson<UserProfile>(baseUrl, apiKey, '/api/v1/auth/me', http.RequestMethod.GET,
      '读取登录状态失败')
  }

  static async updateProfile(baseUrl: string, apiKey: string, displayName: string, role: string): Promise<UserProfile> {
    return ApiClient.requestJson<UserProfile>(baseUrl, apiKey, '/api/v1/auth/me', http.RequestMethod.PUT,
      '保存个人资料', 'application/json', JSON.stringify({ displayName: displayName, role: role }))
  }

  static async changePassword(
    baseUrl: string,
    apiKey: string,
    oldPassword: string,
    newPassword: string
  ): Promise<ChangePasswordResult> {
    return ApiClient.requestJson<ChangePasswordResult>(baseUrl, apiKey, '/api/v1/auth/change-password',
      http.RequestMethod.POST, '修改密码', 'application/json', JSON.stringify({
        oldPassword: oldPassword, newPassword: newPassword
      }))
  }

  static async members(baseUrl: string, apiKey: string): Promise<UserProfile[]> {
    return ApiClient.requestJson<UserProfile[]>(baseUrl, apiKey, '/api/v1/auth/members', http.RequestMethod.GET,
      '读取组员失败')
  }

  static async logout(baseUrl: string, apiKey: string): Promise<void> {
    await ApiClient.requestVoid(baseUrl, apiKey, '/api/v1/auth/logout', http.RequestMethod.POST, '退出登录失败')
  }

  static async uploadAvatar(baseUrl: string, apiKey: string, imageUri: string): Promise<UserProfile> {
    const multipart: MultipartImageBody =
      ApiClient.createImageMultipart(imageUri, 'file', 'avatar.png', '----HarmonyAvatar')
    return ApiClient.requestJson<UserProfile>(baseUrl, apiKey, '/api/v1/auth/me/avatar', http.RequestMethod.POST,
      '上传头像失败', multipart.contentType, multipart.body)
  }
}
