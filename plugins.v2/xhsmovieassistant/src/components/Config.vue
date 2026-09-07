<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  initialConfig: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['save', 'close'])

const DEFAULT_CONFIG = Object.freeze({
  enabled: false,
  enable_subscription: false,
  notifications_enabled: true,
  reply_enabled: false,
  reply_success_enabled: false,
  reply_existing_enabled: false,
  reply_confirmation_enabled: false,
  reply_failure_enabled: false,
  site: 'xiaohongshu',
  browser_mode: 'embedded',
  cdp_url: '',
  cdp_token: '',
  authorized_user_ids: '',
  poll_interval_minutes: 2,
  confidence_threshold: 0.85,
  template_SUBSCRIBED: '检测到{media_type}《{title}》{year_text}{season_text}，已推送订阅。',
  template_ALREADY_SUBSCRIBED: '《{title}》{year_text}{season_text}已经订阅，无需重复添加。',
  template_ALREADY_IN_LIBRARY: '《{title}》{year_text}{season_text}已经在媒体库中。',
  template_NEED_CONFIRMATION: '暂时无法确定这篇笔记中的具体影视作品，请人工确认。',
  template_FAILED: '本次订阅处理失败，详情已通过 MoviePilot 通知发送。',
})

const localConfig = ref({ ...DEFAULT_CONFIG })

function clone(value) {
  return JSON.parse(JSON.stringify(value || {}))
}

function normalizedConfig(value) {
  return { ...DEFAULT_CONFIG, ...clone(value) }
}

function saveConfig() {
  emit('save', clone(localConfig.value))
}

watch(
  () => props.initialConfig,
  value => {
    localConfig.value = normalizedConfig(value)
  },
  { immediate: true, deep: true },
)
</script>

<template>
  <VForm class="xhs-movie-config" aria-label="小红书影视助手配置" @submit.prevent="saveConfig">
    <VToolbar density="comfortable" color="transparent" class="xhs-movie-config__toolbar">
      <div class="text-h6">小红书影视助手</div>
      <VSpacer />
      <VBtn icon="mdi-content-save-outline" variant="text" title="保存配置" aria-label="保存配置" @click="saveConfig" />
      <VBtn icon="mdi-close" variant="text" title="关闭配置" aria-label="关闭配置" @click="emit('close')" />
    </VToolbar>
    <VDivider />

    <section class="xhs-movie-config__section">
      <div class="text-subtitle-2 mb-2">运行策略</div>
      <VRow dense>
        <VCol cols="12" sm="6"><VSwitch v-model="localConfig.enabled" color="primary" label="启用轮询" hide-details /></VCol>
        <VCol cols="12" sm="6"><VSwitch v-model="localConfig.enable_subscription" color="warning" label="允许真实订阅" hide-details /></VCol>
        <VCol cols="12" sm="6"><VSwitch v-model="localConfig.notifications_enabled" color="primary" label="MoviePilot 通知" hide-details /></VCol>
        <VCol cols="12" sm="6"><VSwitch v-model="localConfig.reply_enabled" color="primary" label="小红书公开回复" hide-details /></VCol>
      </VRow>
    </section>

    <section class="xhs-movie-config__section">
      <div class="text-subtitle-2 mb-2">回复范围</div>
      <VRow dense>
        <VCol cols="12" sm="6"><VSwitch v-model="localConfig.reply_success_enabled" color="success" label="回复订阅成功" hide-details /></VCol>
        <VCol cols="12" sm="6"><VSwitch v-model="localConfig.reply_existing_enabled" color="info" label="回复已存在" hide-details /></VCol>
        <VCol cols="12" sm="6"><VSwitch v-model="localConfig.reply_confirmation_enabled" color="warning" label="回复需人工确认" hide-details /></VCol>
        <VCol cols="12" sm="6"><VSwitch v-model="localConfig.reply_failure_enabled" color="error" label="回复处理失败" hide-details /></VCol>
      </VRow>
    </section>

    <section class="xhs-movie-config__section">
      <div class="text-subtitle-2 mb-2">识别条件</div>
      <VRow dense>
        <VCol cols="12" sm="6">
          <VSelect v-model="localConfig.site" label="站点" :items="[{ title: '小红书', value: 'xiaohongshu' }, { title: 'RedNote', value: 'rednote' }]" />
        </VCol>
        <VCol cols="12" sm="6">
          <VSelect v-model="localConfig.browser_mode" label="浏览器模式" :items="[{ title: '内置 Chromium', value: 'embedded' }, { title: 'CloakBrowser / CDP', value: 'cdp' }]" />
        </VCol>
        <VCol v-if="localConfig.browser_mode === 'cdp'" cols="12" sm="8">
          <VTextField v-model="localConfig.cdp_url" label="CloakBrowser CDP URL" placeholder="http://NAS-IP:9050/api/profiles/PROFILE-ID/cdp" autocomplete="off" />
        </VCol>
        <VCol v-if="localConfig.browser_mode === 'cdp'" cols="12" sm="4">
          <VTextField v-model="localConfig.cdp_token" label="CDP Access Token" type="password" autocomplete="new-password" />
        </VCol>
        <VCol cols="12" sm="3"><VTextField v-model.number="localConfig.poll_interval_minutes" label="轮询间隔（分钟）" type="number" min="1" max="10" /></VCol>
        <VCol cols="12" sm="3"><VTextField v-model.number="localConfig.confidence_threshold" label="AI 置信度阈值" type="number" min="0" max="1" step="0.05" /></VCol>
        <VCol cols="12"><VTextarea v-model="localConfig.authorized_user_ids" label="授权用户 ID（逗号或换行分隔）" rows="3" auto-grow /></VCol>
      </VRow>
    </section>

    <section class="xhs-movie-config__section">
      <div class="text-subtitle-2 mb-2">公开回复模板</div>
      <VTextarea v-model="localConfig.template_SUBSCRIBED" label="订阅成功回复模板" rows="2" auto-grow />
      <VTextarea v-model="localConfig.template_ALREADY_SUBSCRIBED" label="已订阅回复模板" rows="2" auto-grow />
      <VTextarea v-model="localConfig.template_ALREADY_IN_LIBRARY" label="媒体库已存在回复模板" rows="2" auto-grow />
      <VTextarea v-model="localConfig.template_NEED_CONFIRMATION" label="需人工确认回复模板" rows="2" auto-grow />
      <VTextarea v-model="localConfig.template_FAILED" label="处理失败回复模板" rows="2" auto-grow />
    </section>

    <div class="xhs-movie-config__actions">
      <VBtn variant="text" @click="emit('close')">取消</VBtn>
      <VBtn color="primary" prepend-icon="mdi-content-save-outline" @click="saveConfig">保存</VBtn>
    </div>
  </VForm>
</template>

<style scoped>
.xhs-movie-config {
  min-width: 0;
  padding-bottom: 16px;
}

.xhs-movie-config__toolbar,
.xhs-movie-config__section,
.xhs-movie-config__actions {
  padding-inline: 16px;
}

.xhs-movie-config__section {
  padding-block: 16px 0;
}

.xhs-movie-config__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 16px;
}

.xhs-movie-config :deep(input),
.xhs-movie-config :deep(textarea) {
  font-size: 16px;
}

.xhs-movie-config :deep(.v-btn) {
  min-height: 44px;
  min-width: 44px;
}
</style>
