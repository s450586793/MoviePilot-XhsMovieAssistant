<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

const props = defineProps({
  api: { type: Object, default: () => ({}) },
})

const loading = ref(false)
const actionKey = ref('')
const feedback = ref({ type: 'info', text: '', source: '' })
const snapshot = ref({ status: {}, requests: [] })
const drafts = reactive({})

const actionableStatuses = new Set(['NEW', 'FAILED', 'NEED_CONFIRMATION', 'DRY_RUN_MATCHED'])
const status = computed(() => snapshot.value.status || {})
const requests = computed(() => Array.isArray(snapshot.value.requests) ? snapshot.value.requests : [])
const qrSource = computed(() => status.value.qrcode || '')

function unwrap(response) {
  const body = response && Object.prototype.hasOwnProperty.call(response, 'success')
    ? response
    : (response?.data ?? response)
  if (body?.success === false) throw new Error(body.message || '请求失败')
  return body?.data ?? body ?? {}
}

function message(error, fallback) {
  return error?.message || fallback
}

function normalizeText(value) {
  return value === '-' || value == null ? '' : String(value)
}

function draftFor(row) {
  if (!drafts[row.id]) {
    drafts[row.id] = {
      title: normalizeText(row.title),
      original_title: normalizeText(row.original_title),
      media_type: normalizeText(row.media_type),
      year: normalizeText(row.year),
      season: normalizeText(row.season),
    }
  }
  return drafts[row.id]
}

function optionalInteger(value) {
  const normalized = String(value ?? '').trim()
  if (!normalized) return null
  const parsed = Number(normalized)
  return Number.isInteger(parsed) ? parsed : Number.NaN
}

function isActionable(row) {
  return actionableStatuses.has(row.status)
}

function showFeedback(type, text, source = 'action') {
  feedback.value = { type, text, source }
}

function actionFeedback(data, label) {
  const status = String(data?.status || '')
  const detail = String(data?.message || '').trim()
  const withDetail = fallback => detail ? `：${detail}` : fallback

  if (status === 'FAILED') {
    return { type: 'error', text: `${label}失败${withDetail('。')}` }
  }
  if (status === 'NEED_CONFIRMATION') {
    return { type: 'warning', text: `${label}仍需确认${withDetail('。')}` }
  }
  if (status === 'SUBSCRIBED') {
    return { type: 'success', text: `${label}完成${withDetail('：已创建订阅')}` }
  }
  if (status === 'ALREADY_SUBSCRIBED' || status === 'ALREADY_IN_LIBRARY') {
    return { type: 'info', text: `${label}已存在，无需重复订阅${detail ? `：${detail}` : '。'}` }
  }
  if (status === 'DRY_RUN_MATCHED') {
    return { type: 'info', text: `${label}已完成 dry-run 匹配${withDetail('。')}` }
  }
  return { type: 'success', text: detail || `${label}已提交。` }
}

async function loadState() {
  if (!props.api?.get) {
    showFeedback('error', 'MoviePilot API 客户端不可用。', 'state')
    return
  }
  loading.value = true
  try {
    const response = await props.api.get('plugin/XhsMovieAssistant/state')
    const data = unwrap(response)
    snapshot.value = {
      status: data?.status || {},
      requests: Array.isArray(data?.requests) ? data.requests : [],
    }
    requests.value.forEach(draftFor)
    if (feedback.value.source === 'state') {
      showFeedback('info', '', 'state')
    }
  } catch (error) {
    showFeedback('error', message(error, '无法读取缓存状态。'), 'state')
  } finally {
    loading.value = false
  }
}

async function runAction(path, label, body) {
  if (!props.api?.post || actionKey.value) return
  actionKey.value = path
  try {
    const response = body === undefined
      ? await props.api.post(path)
      : await props.api.post(path, body)
    const data = unwrap(response)
    const outcome = actionFeedback(data, label)
    showFeedback(outcome.type, outcome.text)
    await loadState()
  } catch (error) {
    showFeedback('error', message(error, `${label}失败，请检查当前状态后重试。`))
  } finally {
    actionKey.value = ''
  }
}

async function submitManual(row) {
  const draft = draftFor(row)
  if (!draft.title.trim()) {
    showFeedback('error', '标题不能为空。')
    return
  }
  if (!['movie', 'tv'].includes(draft.media_type)) {
    showFeedback('error', '影视类型必须是电影或电视剧。')
    return
  }
  const year = optionalInteger(draft.year)
  const season = optionalInteger(draft.season)
  if (Number.isNaN(year) || Number.isNaN(season)) {
    showFeedback('error', '年份和季数必须是整数。')
    return
  }
  const payload = {
    title: draft.title.trim(),
    original_title: draft.original_title.trim(),
    media_type: draft.media_type,
    year: optionalInteger(draft.year),
    season: optionalInteger(draft.season),
  }
  const path = `plugin/XhsMovieAssistant/requests/${row.id}/manual`
  await runAction(path, `请求 #${row.id} 的人工确认`, payload)
}

onMounted(loadState)
</script>

<template>
  <main class="xhs-movie-page" aria-label="小红书影视助手运行面板">
    <header class="xhs-movie-page__header">
      <div>
        <p class="xhs-movie-page__eyebrow">运行面板</p>
        <h1>小红书影视助手</h1>
      </div>
      <VBtn icon="mdi-refresh" variant="text" :loading="loading" title="刷新状态" aria-label="刷新状态" @click="loadState" />
    </header>

    <VAlert v-if="feedback.text" :type="feedback.type" variant="tonal" closable class="xhs-movie-page__feedback" @click:close="showFeedback('info', '')">
      {{ feedback.text }}
    </VAlert>

    <section class="xhs-movie-page__status" aria-label="缓存服务状态">
      <dl>
        <div><dt>插件</dt><dd>{{ status.activity || 'IDLE' }}</dd></div>
        <div><dt>浏览器模式</dt><dd>{{ status.browser_mode || 'EMBEDDED' }}</dd></div>
        <div><dt>浏览器</dt><dd>{{ status.browser || 'UNKNOWN' }}</dd></div>
        <div>
          <dt>Chromium</dt>
          <dd>
            {{ status.chromium || 'UNKNOWN' }}<span v-if="status.chromium_code"> ({{ status.chromium_code }})</span>
          </dd>
        </div>
        <div><dt>登录</dt><dd>{{ status.login || 'UNKNOWN' }}</dd></div>
        <div><dt>暂停原因</dt><dd>{{ status.pause_code || '无' }}</dd></div>
      </dl>
      <VImg v-if="qrSource" :src="qrSource" width="180" height="180" contain class="xhs-movie-page__qr" alt="小红书登录二维码" />
    </section>

    <section class="xhs-movie-page__section" aria-labelledby="management-actions">
      <div class="xhs-movie-page__section-title">
        <h2 id="management-actions">管理与诊断</h2>
      </div>
      <div class="xhs-movie-page__tool-grid">
        <VBtn v-if="status.browser_mode !== 'CDP'" prepend-icon="mdi-download" variant="outlined" :loading="actionKey === 'plugin/XhsMovieAssistant/chromium/install'" @click="runAction('plugin/XhsMovieAssistant/chromium/install', 'Chromium 安装')">安装 Chromium</VBtn>
        <VBtn prepend-icon="mdi-qrcode-scan" variant="outlined" :loading="actionKey === 'plugin/XhsMovieAssistant/login/start'" @click="runAction('plugin/XhsMovieAssistant/login/start', '登录二维码生成')">生成登录二维码</VBtn>
        <VBtn prepend-icon="mdi-refresh" variant="outlined" :loading="actionKey === 'plugin/XhsMovieAssistant/poll'" @click="runAction('plugin/XhsMovieAssistant/poll', '立即轮询')">立即轮询</VBtn>
        <VBtn prepend-icon="mdi-play" variant="outlined" :loading="actionKey === 'plugin/XhsMovieAssistant/resume'" @click="runAction('plugin/XhsMovieAssistant/resume', '恢复轮询')">恢复轮询</VBtn>
        <VBtn prepend-icon="mdi-logout" variant="outlined" :loading="actionKey === 'plugin/XhsMovieAssistant/logout'" @click="runAction('plugin/XhsMovieAssistant/logout', '退出登录')">退出登录</VBtn>
        <VBtn prepend-icon="mdi-robot-outline" variant="outlined" :loading="actionKey === 'plugin/XhsMovieAssistant/test/ai'" @click="runAction('plugin/XhsMovieAssistant/test/ai', 'AI 测试', { title: '星际穿越' })">测试 AI</VBtn>
        <VBtn prepend-icon="mdi-movie-search-outline" variant="outlined" :loading="actionKey === 'plugin/XhsMovieAssistant/test/moviepilot'" @click="runAction('plugin/XhsMovieAssistant/test/moviepilot', 'MoviePilot 测试', { title: '星际穿越', media_type: 'movie' })">测试 MoviePilot</VBtn>
        <VBtn prepend-icon="mdi-bell-check-outline" variant="outlined" :loading="actionKey === 'plugin/XhsMovieAssistant/test/notification'" @click="runAction('plugin/XhsMovieAssistant/test/notification', '通知测试')">测试通知</VBtn>
      </div>
    </section>

    <section class="xhs-movie-page__section" aria-labelledby="recent-requests">
      <div class="xhs-movie-page__section-title">
        <h2 id="recent-requests">最近请求</h2>
        <span>{{ requests.length }} 项</span>
      </div>
      <VProgressLinear v-if="loading" indeterminate color="primary" class="mb-3" />
      <p v-if="!loading && !requests.length" class="xhs-movie-page__empty">没有可显示的持久化请求。</p>

      <article v-for="row in requests" :key="row.id" class="xhs-movie-request">
        <header class="xhs-movie-request__header">
          <div>
            <span class="xhs-movie-request__id">#{{ row.id }}</span>
            <h3>{{ row.title }}</h3>
          </div>
          <VChip size="small" :color="row.status === 'FAILED' ? 'error' : row.status === 'NEED_CONFIRMATION' ? 'warning' : 'primary'" variant="tonal">{{ row.status }}</VChip>
        </header>

        <dl class="xhs-movie-request__summary">
          <div><dt>匹配</dt><dd>{{ row.match }}</dd></div>
          <div><dt>回复</dt><dd>{{ row.reply }}</dd></div>
          <div><dt>错误</dt><dd>{{ row.error }}</dd></div>
          <div><dt>更新</dt><dd>{{ row.updated_at }}</dd></div>
        </dl>

        <div v-if="isActionable(row)" class="xhs-movie-request__editor">
          <VTextField v-model="draftFor(row).title" label="影视标题" density="comfortable" />
          <VTextField v-model="draftFor(row).original_title" label="原始标题（可选）" density="comfortable" />
          <VSelect v-model="draftFor(row).media_type" label="影视类型" density="comfortable" :items="[{ title: '电影', value: 'movie' }, { title: '电视剧', value: 'tv' }]" />
          <VTextField v-model="draftFor(row).year" label="年份（可选）" density="comfortable" inputmode="numeric" />
          <VTextField v-model="draftFor(row).season" label="季（可选）" density="comfortable" inputmode="numeric" />
        </div>

        <footer v-if="isActionable(row)" class="xhs-movie-request__actions">
          <VBtn icon="mdi-replay" variant="text" title="重新处理" :loading="actionKey === `plugin/XhsMovieAssistant/requests/${row.id}/reprocess`" :aria-label="`重新处理请求 #${row.id}`" @click="runAction(`plugin/XhsMovieAssistant/requests/${row.id}/reprocess`, `请求 #${row.id} 重新处理`)" />
          <VBtn icon="mdi-eye-off-outline" variant="text" title="忽略请求" :loading="actionKey === `plugin/XhsMovieAssistant/requests/${row.id}/ignore`" :aria-label="`忽略请求 #${row.id}`" @click="runAction(`plugin/XhsMovieAssistant/requests/${row.id}/ignore`, `请求 #${row.id} 已忽略`)" />
          <VBtn color="primary" prepend-icon="mdi-check-decagram-outline" :loading="actionKey === `plugin/XhsMovieAssistant/requests/${row.id}/manual`" @click="submitManual(row)">人工确认</VBtn>
        </footer>
      </article>
    </section>
  </main>
</template>

<style scoped>
.xhs-movie-page {
  --xhs-surface: #ffffff;
  --xhs-background: #f4f5f5;
  --xhs-text: #202124;
  --xhs-muted: #61666c;
  --xhs-rule: #d8dcdf;
  --xhs-focus: #146c94;
  background: var(--xhs-background);
  color: var(--xhs-text);
  margin: 0 auto;
  max-width: 1120px;
  min-width: 0;
  padding: 16px;
}

.xhs-movie-page__header,
.xhs-movie-page__section-title,
.xhs-movie-request__header,
.xhs-movie-request__actions {
  align-items: center;
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.xhs-movie-page__header {
  border-bottom: 1px solid var(--xhs-rule);
  padding-bottom: 12px;
}

.xhs-movie-page__eyebrow,
.xhs-movie-request__id,
dt {
  color: var(--xhs-muted);
  font-size: 12px;
  margin: 0;
}

h1,
h2,
h3 {
  color: var(--xhs-text);
  letter-spacing: 0;
  margin: 0;
}

h1 { font-size: 22px; line-height: 1.3; }
h2 { font-size: 16px; line-height: 1.4; }
h3 { font-size: 18px; line-height: 1.35; }

.xhs-movie-page__feedback,
.xhs-movie-page__status,
.xhs-movie-page__section {
  margin-top: 16px;
}

.xhs-movie-page__status,
.xhs-movie-request {
  background: var(--xhs-surface);
  border: 1px solid var(--xhs-rule);
  border-radius: 6px;
}

.xhs-movie-page__status {
  display: grid;
  gap: 12px;
  padding: 14px;
}

.xhs-movie-page__status dl,
.xhs-movie-request__summary {
  display: grid;
  gap: 12px;
  margin: 0;
}

dd { font-size: 14px; margin: 3px 0 0; overflow-wrap: anywhere; }

.xhs-movie-page__qr { justify-self: center; }

.xhs-movie-page__section-title { border-bottom: 1px solid var(--xhs-rule); padding-bottom: 8px; }
.xhs-movie-page__section-title span { color: var(--xhs-muted); font-size: 14px; }

.xhs-movie-page__tool-grid,
.xhs-movie-request__editor {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.xhs-movie-page__header :deep(.v-btn),
.xhs-movie-page__tool-grid :deep(.v-btn),
.xhs-movie-request__actions :deep(.v-btn) {
  min-height: 44px;
  min-width: 44px;
}

.xhs-movie-page__empty { color: var(--xhs-muted); font-size: 16px; margin: 16px 0; }

.xhs-movie-request { margin-top: 12px; padding: 14px; }
.xhs-movie-request__header { align-items: flex-start; }
.xhs-movie-request__summary { grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 14px; }
.xhs-movie-request__editor { margin-top: 16px; }
.xhs-movie-request__actions { justify-content: flex-end; margin-top: 8px; }

.xhs-movie-page :deep(input) { font-size: 16px; }

@media (min-width: 600px) {
  .xhs-movie-page { padding: 24px; }
  .xhs-movie-page__status { grid-template-columns: minmax(0, 1fr) auto; }
  .xhs-movie-page__status dl { grid-template-columns: repeat(2, minmax(140px, 1fr)); }
  .xhs-movie-page__tool-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .xhs-movie-request__editor { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (min-width: 960px) {
  .xhs-movie-page__tool-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .xhs-movie-request__editor { grid-template-columns: repeat(5, minmax(0, 1fr)); }
}

@media (prefers-color-scheme: dark) {
  .xhs-movie-page {
    --xhs-surface: #202427;
    --xhs-background: #16191b;
    --xhs-text: #e6e9ea;
    --xhs-muted: #adb5ba;
    --xhs-rule: #3d454a;
    --xhs-focus: #77c2df;
  }
}

:global([data-theme='dark']) .xhs-movie-page {
  --xhs-surface: #202427;
  --xhs-background: #16191b;
  --xhs-text: #e6e9ea;
  --xhs-muted: #adb5ba;
  --xhs-rule: #3d454a;
  --xhs-focus: #77c2df;
}

@media (prefers-reduced-motion: reduce) {
  .xhs-movie-page *,
  .xhs-movie-page *::before,
  .xhs-movie-page *::after {
    animation-duration: 0.01ms !important;
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
</style>
