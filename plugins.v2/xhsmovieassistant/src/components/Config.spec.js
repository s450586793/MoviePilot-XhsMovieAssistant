import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Config from './Config.vue'
import { vuetifyStubs } from '../test/vuetify-stubs'

const expectedDefaults = {
  enabled: false,
  enable_subscription: false,
  notifications_enabled: true,
  reply_enabled: false,
  reply_success_enabled: false,
  reply_existing_enabled: false,
  reply_confirmation_enabled: false,
  reply_failure_enabled: false,
  site: 'xiaohongshu',
  authorized_user_ids: '',
  poll_interval_minutes: 2,
  confidence_threshold: 0.85,
  template_SUBSCRIBED: '收到，已安排订阅。',
  template_ALREADY_SUBSCRIBED: '《{title}》{year_text}{season_text}已经订阅，无需重复添加。',
  template_ALREADY_IN_LIBRARY: '《{title}》{year_text}{season_text}已经在媒体库中。',
  template_NEED_CONFIRMATION: '收到，影视不明确，请明示。',
  template_FAILED: '本次订阅处理失败，详情已通过 MoviePilot 通知发送。',
}

describe('Config', () => {
  it('emits every safe backend default when saved without an initial configuration', async () => {
    const wrapper = mount(Config, {
      props: { initialConfig: {} },
      global: { stubs: vuetifyStubs },
    })

    await wrapper.findAll('button').at(-1).trigger('click')

    expect(wrapper.emitted('save')).toEqual([[expectedDefaults]])
  })

  it('migrates only legacy default reply text when opening saved configuration', async () => {
    const wrapper = mount(Config, {
      props: {
        initialConfig: {
          template_SUBSCRIBED: '检测到{media_type}《{title}》{year_text}{season_text}，已推送订阅。',
          template_NEED_CONFIRMATION: '暂时无法确定这篇笔记中的具体影视作品，请人工确认。',
          template_FAILED: '我自己的失败文案',
        },
      },
      global: { stubs: vuetifyStubs },
    })

    await wrapper.findAll('button').at(-1).trigger('click')

    const saved = wrapper.emitted('save')[0][0]
    expect(saved.template_SUBSCRIBED).toBe('收到，已安排订阅。')
    expect(saved.template_NEED_CONFIRMATION).toBe('收到，影视不明确，请明示。')
    expect(saved.template_FAILED).toBe('我自己的失败文案')
  })

  it('gives toolbar icon controls a 44px minimum touch target', () => {
    const wrapper = mount(Config, {
      props: { initialConfig: {} },
      attachTo: document.body,
      global: { stubs: vuetifyStubs },
    })

    for (const button of wrapper.findAll('button')) {
      const style = window.getComputedStyle(button.element)
      expect(style.minWidth).toBe('44px')
      expect(style.minHeight).toBe('44px')
    }
  })

  it('keeps browser sessions out of persisted plugin settings', () => {
    const wrapper = mount(Config, {
      props: { initialConfig: {} },
      global: { stubs: vuetifyStubs },
    })

    expect(wrapper.find('select[aria-label="浏览器模式"]').exists()).toBe(false)
    expect(wrapper.find('input[aria-label="CloakBrowser CDP URL"]').exists()).toBe(false)
    expect(wrapper.find('input[aria-label="CDP Access Token"]').exists()).toBe(false)
  })
})
