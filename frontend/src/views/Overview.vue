<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = [{"name": "湖表目录", "created": 0, "pending": 0, "abnormal": 0}, {"name": "结构演进", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分区规划", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分区演化", "created": 0, "pending": 0, "abnormal": 0}, {"name": "小文件归并", "created": 0, "pending": 0, "abnormal": 0}, {"name": "快照管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "时间旅行", "created": 0, "pending": 0, "abnormal": 0}, {"name": "入湖水位", "created": 0, "pending": 0, "abnormal": 0}, {"name": "入湖批次", "created": 0, "pending": 0, "abnormal": 0}, {"name": "写入作业", "created": 0, "pending": 0, "abnormal": 0}, {"name": "压实作业", "created": 0, "pending": 0, "abnormal": 0}, {"name": "过期策略", "created": 0, "pending": 0, "abnormal": 0}, {"name": "统计画像", "created": 0, "pending": 0, "abnormal": 0}, {"name": "列统计", "created": 0, "pending": 0, "abnormal": 0}, {"name": "入湖质量规则", "created": 0, "pending": 0, "abnormal": 0}, {"name": "隔离区", "created": 0, "pending": 0, "abnormal": 0}, {"name": "重建补数", "created": 0, "pending": 0, "abnormal": 0}, {"name": "存储分层", "created": 0, "pending": 0, "abnormal": 0}, {"name": "保留窗口", "created": 0, "pending": 0, "abnormal": 0}, {"name": "消费视图", "created": 0, "pending": 0, "abnormal": 0}, {"name": "资源额度计量", "created": 0, "pending": 0, "abnormal": 0}, {"name": "湖表审计", "created": 0, "pending": 0, "abnormal": 0}, {"name": "来源交接", "created": 0, "pending": 0, "abnormal": 0}]
  }
})
</script>
