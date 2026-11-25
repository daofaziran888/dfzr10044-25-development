<template>
  <section class="page" data-testid="handover-detail">
    <header class="page-head">
      <div>
        <h2>来源交接详情 · {{ handoverId }}</h2>
        <p class="page-desc">
          核对待代送窗口、窗口归属段与关联的批次确认；交接前窗口仍沿原来源核对。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/source_handover" data-testid="back-to-list">返回交接清单</RouterLink>
        <button class="btn" data-testid="refresh-detail" type="button" @click="reload">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="detail-error">{{ errorMessage }}</p>

    <template v-if="detail">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">交接状态</span>
          <strong class="stat-value" data-testid="handover-status">{{ detail.handover.status }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">旧源可写</span>
          <strong class="stat-value">{{ detail.handover.old_writable ? '可写' : '暂不可写' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">表级采集截止点</span>
          <strong class="stat-value" data-testid="table-cutoff">{{ detail.cutoffs.table ?? '未建立' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">待代送窗口</span>
          <strong class="stat-value" data-testid="pending-count">{{ detail.pending_proxy_windows.length }}</strong>
        </article>
      </div>

      <section class="detail-block">
        <h3>交接约定</h3>
        <dl class="detail-grid">
          <div><dt>所属湖表</dt><dd>{{ detail.handover.lake_table }}</dd></div>
          <div><dt>旧来源 → 新来源</dt><dd>{{ detail.handover.old_source }} → {{ detail.handover.new_source }}</dd></div>
          <div><dt>交接时刻</dt><dd>{{ detail.handover.handover_at }}（新源从此接收）</dd></div>
          <div><dt>原在途批次</dt><dd>{{ detail.handover.old_batch_id }}</dd></div>
          <div>
            <dt>原在途窗口</dt>
            <dd>{{ detail.handover.old_window_start }} ~ {{ detail.handover.old_window_end }}</dd>
          </div>
          <div><dt>原批归属</dt><dd>{{ detail.verification.original_source }}（代送不改变原批归属）</dd></div>
          <div><dt>接续凭据持有人</dt><dd>{{ detail.proxy_permission.credential_holder }}</dd></div>
          <div><dt>重启次数</dt><dd>{{ detail.handover.restart_count }}</dd></div>
        </dl>
        <p class="hint" data-testid="permission-summary">{{ detail.proxy_permission.summary }}</p>
      </section>

      <section class="detail-block">
        <h3>采集截止点（按来源独立推进）</h3>
        <table class="data-table">
          <thead><tr><th>归属来源</th><th>入湖采集截止点</th></tr></thead>
          <tbody>
            <tr v-for="(value, source) in detail.cutoffs.by_source" :key="source">
              <td>{{ source }}</td>
              <td>{{ value }}</td>
            </tr>
            <tr v-if="!Object.keys(detail.cutoffs.by_source).length">
              <td colspan="2" class="empty-state">暂无来源截止点</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="detail-block">
        <h3>窗口归属段（原批可按事件时间拆开）</h3>
        <table class="data-table">
          <thead>
            <tr><th>窗口段</th><th>原批号</th><th>原批归属</th><th>窗口归属</th><th>区间</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="segment in detail.segments" :key="segment.segment_id" :data-testid="`segment-${segment.segment_id}`">
              <td>{{ segment.segment_id }}</td>
              <td>{{ segment.batch_id }}</td>
              <td>{{ segment.original_source }}</td>
              <td>{{ segment.window_owner }}</td>
              <td>{{ segment.window_start }} ~ {{ segment.window_end }}</td>
              <td>{{ segment.state }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="detail-block">
        <h3>待代送窗口与补证待办</h3>
        <table class="data-table">
          <thead>
            <tr><th>批次</th><th>区间</th><th>窗口归属</th><th>需补证来源</th><th>原因</th></tr>
          </thead>
          <tbody>
            <tr
              v-for="item in detail.pending_proxy_windows"
              :key="`${item.batch_id}-${item.window_start}`"
              data-testid="pending-window"
            >
              <td>{{ item.batch_id }}</td>
              <td>{{ item.window_start }} ~ {{ item.window_end }}</td>
              <td>{{ item.window_owner }}</td>
              <td>{{ item.required_source }}</td>
              <td>{{ item.reason }}</td>
            </tr>
            <tr v-if="!detail.pending_proxy_windows.length">
              <td colspan="5" class="empty-state">没有待代送窗口</td>
            </tr>
          </tbody>
        </table>
        <p v-if="detail.proxy_permission.conflict_intervals.length" class="error-text" data-testid="conflict-intervals">
          代送许可冲突区间：
          <span v-for="item in detail.proxy_permission.conflict_intervals" :key="item.window_start">
            {{ item.window_start }} ~ {{ item.window_end }}（{{ item.window_owner }}）
          </span>
        </p>
      </section>

      <section class="detail-block">
        <h3>关联批次确认</h3>
        <table class="data-table">
          <thead>
            <tr>
              <th>确认编号</th><th>批次</th><th>区间</th><th>提交来源</th>
              <th>窗口归属</th><th>状态</th><th>核对来源</th><th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in detail.confirmations"
              :key="item.confirmation_id"
              :data-testid="`confirmation-${item.confirmation_id}`"
              :data-status="item.status"
            >
              <td>{{ item.confirmation_id }}</td>
              <td>{{ item.batch_id }}</td>
              <td>{{ item.window_start }} ~ {{ item.window_end }}</td>
              <td>{{ item.submitted_source }}</td>
              <td>{{ item.window_owner }}</td>
              <td>{{ item.status }}</td>
              <td>{{ item.verification_source }}{{ item.pre_handover_window ? '（交接前）' : '' }}</td>
              <td>{{ item.reason }}{{ item.superseded_by ? ` → ${item.superseded_by}` : '' }}</td>
            </tr>
            <tr v-if="!detail.confirmations.length">
              <td colspan="8" class="empty-state">暂无批次确认</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="detail-block">
        <h3>代送 8:30 至 9:00（值班员使用新源接续凭据）</h3>
        <form class="filter-bar" @submit.prevent="submitProxy">
          <label class="filter-item">
            <span>提交来源</span>
            <input v-model="proxyForm.submitted_source" data-testid="proxy-source" />
          </label>
          <label class="filter-item">
            <span>原批号</span>
            <input v-model="proxyForm.batch_id" data-testid="proxy-batch" />
          </label>
          <label class="filter-item">
            <span>窗口起始</span>
            <input v-model="proxyForm.window_start" data-testid="proxy-start" />
          </label>
          <label class="filter-item">
            <span>窗口结束</span>
            <input v-model="proxyForm.window_end" data-testid="proxy-end" />
          </label>
          <label class="filter-item">
            <span>接续凭据</span>
            <input v-model="proxyForm.credential" data-testid="proxy-credential" />
          </label>
          <button class="btn primary" data-testid="submit-proxy" type="submit">提交代送</button>
        </form>
        <p v-if="lastResult" class="hint" data-testid="receive-result">{{ resultSummary }}</p>
        <ul v-if="lastResult?.intervals?.length" class="interval-list">
          <li v-for="item in lastResult.intervals" :key="`${item.batch_id}-${item.window_start}`">
            {{ item.batch_id }} · {{ item.window_start }} ~ {{ item.window_end }} · 归属 {{ item.window_owner }}
          </li>
        </ul>
      </section>

      <section class="detail-block">
        <h3>窗口核对</h3>
        <form class="filter-bar" @submit.prevent="submitVerify">
          <label class="filter-item">
            <span>核对起始</span>
            <input v-model="verifyForm.window_start" data-testid="verify-start" />
          </label>
          <label class="filter-item">
            <span>核对结束</span>
            <input v-model="verifyForm.window_end" data-testid="verify-end" />
          </label>
          <button class="btn" data-testid="submit-verify" type="submit">按窗口核对</button>
        </form>
        <p v-if="verifyResult" class="hint" data-testid="verify-result">{{ verifySummary }}</p>
      </section>

      <section class="detail-block">
        <h3>交接事件</h3>
        <ul class="event-list">
          <li v-for="(event, index) in detail.events" :key="index">
            <span class="event-kind">{{ event.kind }}</span>
            {{ event.message }}
          </li>
        </ul>
      </section>
    </template>
    <p v-else-if="!errorMessage" class="empty-state">加载中…</p>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  loadHandoverDetail,
  receiveHandoverBatch,
  verifyHandoverWindow,
} from '@/api/backend-service'
import type { HandoverDetail, ReceiveResult } from '@/api/backend-service'

const props = defineProps<{ handoverId: string }>()

const detail = ref<HandoverDetail | null>(null)
const errorMessage = ref('')
const lastResult = ref<ReceiveResult | null>(null)
const verifyResult = ref<Record<string, unknown> | null>(null)

const proxyForm = ref({
  submitted_source: '',
  batch_id: '',
  window_start: '',
  window_end: '',
  credential: '',
})
const verifyForm = ref({ window_start: '', window_end: '' })

const resultSummary = computed(() => {
  const result = lastResult.value
  if (!result) return ''
  if (!result.ok) {
    const intervals = (result.intervals ?? [])
      .map((item) => `${item.window_start} ~ ${item.window_end}`)
      .join('、')
    return `未接收：${result.message ?? result.error ?? '处理失败'}${intervals ? `（${intervals}）` : ''}`
  }
  const confirmed = (result.confirmations ?? []).length
  const duplicates = (result.duplicates ?? []).length
  const rejected = (result.rejected ?? []).length
  const extra = result.restart_required ? '；遇到旧源迟到半批，作业需重启' : ''
  return `已处理：新确认 ${confirmed} 条，重复 ${duplicates} 条，拒绝 ${rejected} 条${extra}`
})

const verifySummary = computed(() => {
  const result = verifyResult.value
  if (!result) return ''
  if (!result.found) return '该窗口没有已确认记录'
  return `已确认，核对来源：${result.verified_by}；原批归属：${result.original_source}`
})

function fillDefaults() {
  const handover = detail.value?.handover
  if (!handover) return
  proxyForm.value = {
    submitted_source: handover.new_source,
    batch_id: handover.old_batch_id,
    window_start: handover.old_window_start,
    window_end: handover.handover_at,
    credential: handover.continuation_token,
  }
  verifyForm.value = {
    window_start: handover.old_window_start,
    window_end: handover.handover_at,
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    detail.value = await loadHandoverDetail(props.handoverId)
    if (!proxyForm.value.batch_id) fillDefaults()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '来源交接详情读取失败'
  }
}

async function submitProxy() {
  errorMessage.value = ''
  try {
    lastResult.value = await receiveHandoverBatch(props.handoverId, {
      ...proxyForm.value,
      operator: '值班员',
    })
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '代送失败'
  }
}

async function submitVerify() {
  errorMessage.value = ''
  try {
    verifyResult.value = await verifyHandoverWindow(
      props.handoverId,
      verifyForm.value.window_start,
      verifyForm.value.window_end,
    )
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '窗口核对失败'
  }
}

watch(() => props.handoverId, async () => {
  detail.value = null
  lastResult.value = null
  verifyResult.value = null
  await reload()
})

onMounted(reload)
</script>

<style scoped>
.detail-block { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; margin-bottom: 14px; }
.detail-block h3 { margin: 0 0 10px; font-size: 14px; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px 16px; margin: 0 0 8px; }
.detail-grid dt { font-size: 12px; color: var(--muted); }
.detail-grid dd { margin: 2px 0 0; font-size: 13px; }
.hint { font-size: 13px; color: var(--muted); }
.interval-list, .event-list { margin: 6px 0 0; padding-left: 18px; font-size: 13px; }
.event-kind { display: inline-block; min-width: 132px; color: var(--brand); font-family: monospace; }
</style>
