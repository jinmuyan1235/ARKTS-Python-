import { HistoryItem, HistoryPage } from '../model/VisionResult'

/** Append a page without duplicating an analysis if the dataset changed while paging. */
export function mergeHistoryItems(current: HistoryItem[], incoming: HistoryItem[]): HistoryItem[] {
  const merged: HistoryItem[] = current.slice()
  incoming.forEach((item: HistoryItem) => {
    const existingIndex: number = merged.findIndex((existing: HistoryItem) => existing.analysisId === item.analysisId)
    if (existingIndex >= 0) {
      merged[existingIndex] = item
    } else {
      merged.push(item)
    }
  })
  return merged
}

/** Shared-history data, filters, paging and stale-request protection. */
@Observed
export class HistoryController {
  items: HistoryItem[] = []
  query: string = ''
  status: string = 'all'
  favoritesOnly: boolean = false
  scope: string = 'all'
  ownerUserId: string = ''
  offset: number = 0
  hasMore: boolean = false
  loading: boolean = false
  refreshing: boolean = false
  loadingMore: boolean = false
  error: string = ''
  requestSerial: number = 0

  /**
   * A reset always supersedes older work. Pagination is ignored while another
   * request is active, preventing repeated onReachEnd events from loading the
   * same offset more than once.
   */
  beginRequest(reset: boolean = true, keepItems: boolean = false): number {
    if (!reset && (this.loading || this.refreshing || this.loadingMore || !this.hasMore)) {
      return 0
    }
    this.requestSerial += 1
    this.error = ''
    if (reset) {
      this.offset = 0
      this.hasMore = false
      if (!keepItems) {
        this.items = []
      }
      this.refreshing = keepItems && this.items.length > 0
      this.loading = !this.refreshing
      this.loadingMore = false
    } else {
      this.loadingMore = true
    }
    return this.requestSerial
  }

  canApply(serial: number): boolean {
    return serial > 0 && serial === this.requestSerial
  }

  applyPage(serial: number, page: HistoryPage, reset: boolean): boolean {
    if (!this.canApply(serial)) {
      return false
    }
    this.items = reset ? page.items : mergeHistoryItems(this.items, page.items)
    this.offset = page.nextOffset === null ? page.offset + page.items.length : page.nextOffset
    this.hasMore = page.hasMore
    return true
  }

  failRequest(serial: number, message: string): boolean {
    if (!this.canApply(serial)) {
      return false
    }
    this.error = message
    return true
  }

  finishRequest(serial: number): void {
    if (!this.canApply(serial)) {
      return
    }
    this.loading = false
    this.refreshing = false
    this.loadingMore = false
  }

  updateFilters(scope: string, ownerUserId: string, status: string, favoritesOnly: boolean): void {
    // Invalidate the old filter request before scheduling the replacement.
    this.requestSerial += 1
    this.scope = scope
    this.ownerUserId = ownerUserId
    this.status = status
    this.favoritesOnly = favoritesOnly
    this.items = []
    this.offset = 0
    this.hasMore = false
    this.error = ''
    this.loadingMore = false
    this.refreshing = false
  }

  updateQuery(value: string): void {
    if (this.query === value) {
      return
    }
    this.query = value
    // Text entry does not issue a request until search is submitted, but an
    // older in-flight response must no longer be allowed to update the list.
    this.requestSerial += 1
    this.loading = false
    this.refreshing = false
    this.loadingMore = false
  }

  clear(): void {
    this.items = []
    this.offset = 0
    this.hasMore = false
    this.loading = false
    this.refreshing = false
    this.loadingMore = false
    this.error = ''
    this.requestSerial += 1
  }
}
