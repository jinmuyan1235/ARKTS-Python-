import { DEFAULT_API_BASE_URL } from '../services/SettingsStore'

/** Service address, API-key form and connection feedback state. */
@Observed
export class ConnectionController {
  apiBaseUrl: string = DEFAULT_API_BASE_URL
  apiKey: string = ''
  checking: boolean = false
  savingSettings: boolean = false
  serviceSummary: string = '尚未检测服务'
  private lastConnectedSummary: string = ''

  isConfigured(): boolean {
    return this.apiBaseUrl.trim().length > 0 && this.apiKey.trim().length > 0
  }

  markTransportFailure(): void {
    this.serviceSummary = '连接中断 · 请重新检测'
  }

  markConnected(appMode: string, backend: string): void {
    this.lastConnectedSummary = `已连接 · ${appMode} · ${backend}`
    this.serviceSummary = this.lastConnectedSummary
  }

  /** Restore the last verified connection label after any successful API call. */
  markReachable(): void {
    this.serviceSummary = this.lastConnectedSummary.length > 0 ?
      this.lastConnectedSummary : '已连接 · 服务通信正常'
  }
}
