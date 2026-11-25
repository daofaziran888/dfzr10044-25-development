import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ------------------------- 来源交接 -------------------------

export type HandoverInterval = {
  batch_id: string
  window_start: string
  window_end: string
  window_owner: string
  required_source?: string
  reason?: string
  segment_state?: string
  confirmation_id?: string
}

export type HandoverConfirmation = {
  confirmation_id: string
  batch_id: string
  original_source: string
  submitted_source: string
  window_owner: string
  window_start: string
  window_end: string
  status: string
  auth_mode: string
  verification_source: string
  pre_handover_window: boolean
  superseded_by: string | null
  reason: string
  row_count: number
}

export type HandoverDetail = {
  handover: {
    handover_id: string
    lake_table: string
    old_source: string
    new_source: string
    handover_at: string
    old_batch_id: string
    old_window_start: string
    old_window_end: string
    continuation_token: string
    row_count: number
    status: string
    old_writable: boolean
    restart_count: number
    pending_intervals: HandoverInterval[]
    conflict_intervals: HandoverInterval[]
  }
  segments: {
    segment_id: string
    batch_id: string
    original_source: string
    window_owner: string
    window_start: string
    window_end: string
    state: string
    confirmation_id: string | null
    row_count: number
  }[]
  confirmations: HandoverConfirmation[]
  pending_intervals: HandoverInterval[]
  pending_proxy_windows: HandoverInterval[]
  proxy_permission: {
    allowed: boolean
    can_proxy: boolean
    credential_holder: string
    credential_effective: boolean
    old_source_writable: boolean
    requires_old_confirmation: boolean
    summary: string
    pending_intervals: HandoverInterval[]
    conflict_intervals: HandoverInterval[]
  }
  cutoffs: { lake_table: string; table: string | null; by_source: Record<string, string> }
  events: { at: number; kind: string; message: string; detail: Record<string, unknown> }[]
  verification: { original_source: string; rule: string }
  entry: EntryRow | null
}

export type ReceiveResult = {
  ok: boolean
  message?: string
  error?: string
  submitted_source?: string
  proxy?: boolean
  confirmations?: HandoverConfirmation[]
  duplicates?: HandoverConfirmation[]
  rejected?: HandoverInterval[]
  encountered_late_confirmation?: Record<string, unknown> | null
  restart_required?: boolean
  cutoffs?: HandoverDetail['cutoffs']
  proxy_pending_windows?: HandoverInterval[]
  required_source?: string
  intervals?: HandoverInterval[]
}

export async function loadHandoverDetail(
  handoverId: string,
): Promise<HandoverDetail> {
  const response = await request(`/api/source_handover/${encodeURIComponent(handoverId)}/detail`)
  if (!response.ok) throw new Error('来源交接详情读取失败')
  return await response.json()
}

export async function receiveHandoverBatch(
  handoverId: string,
  payload: {
    submitted_source: string
    window_start: string
    window_end: string
    batch_id?: string
    row_count?: number
    credential?: string
    operator?: string
  },
): Promise<ReceiveResult> {
  const response = await request(
    `/api/source_handover/${encodeURIComponent(handoverId)}/receive`,
    { method: 'POST', body: JSON.stringify(payload) },
  )
  const body = await response.json()
  if (response.status === 409) {
    // 冲突时后端把说明放在 detail 里，原样交给页面展示确切区间
    return { ...(body.detail ?? {}), ok: false } as ReceiveResult
  }
  if (!response.ok) throw new Error(body.detail ?? '接收批次失败')
  return body as ReceiveResult
}

export async function verifyHandoverWindow(
  handoverId: string,
  windowStart: string,
  windowEnd: string,
): Promise<Record<string, unknown>> {
  const response = await request(
    `/api/source_handover/${encodeURIComponent(handoverId)}/verify`,
    { method: 'POST', body: JSON.stringify({ window_start: windowStart, window_end: windowEnd }) },
  )
  if (!response.ok) throw new Error('窗口核对失败')
  return await response.json()
}
