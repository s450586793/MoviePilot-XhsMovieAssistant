import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import Page from './Page.vue'
import { vuetifyStubs } from '../test/vuetify-stubs'

function request(overrides = {}) {
  return {
    id: 7,
    status: 'NEED_CONFIRMATION',
    title: 'Arrival',
    original_title: '-',
    media_type: 'movie',
    year: 2016,
    season: '-',
    match: 'tmdb:329865 (0.93)',
    subscription_id: '-',
    reply: 'PENDING',
    error: '-',
    updated_at: '2026-09-07T12:00:00+00:00',
    ...overrides,
  }
}

function snapshot(row, status = {}) {
  return {
    success: true,
    data: {
      status: {
        activity: 'IDLE',
        browser: 'READY',
        chromium: 'AVAILABLE',
        chromium_code: null,
        login: 'LOGGED_IN',
        ...status,
      },
      requests: [row],
    },
  }
}

async function mountPage({ row = request(), postResponse, status, get } = {}) {
  const api = {
    get: get || vi.fn().mockResolvedValue(snapshot(row, status)),
    post: vi.fn().mockResolvedValue(postResponse || { success: true, data: { status: 'SUBSCRIBED' } }),
  }
  const wrapper = mount(Page, {
    props: { api },
    attachTo: document.body,
    global: { stubs: vuetifyStubs },
  })
  await flushPromises()
  return { api, wrapper }
}

function manualButton(wrapper) {
  return wrapper.findAll('button').find(button => button.text() === '人工确认')
}

describe('Page manual resolution', () => {
  it('clears a stale state-load error after a successful refresh', async () => {
    const get = vi.fn()
      .mockRejectedValueOnce(new Error('缓存读取失败'))
      .mockResolvedValueOnce(snapshot(request()))
    const { wrapper } = await mountPage({ get })

    expect(wrapper.get('[data-alert-type="error"]').text()).toContain('缓存读取失败')

    await wrapper.get('button[aria-label="刷新状态"]').trigger('click')
    await flushPromises()

    expect(get).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-alert-type="error"]').exists()).toBe(false)
  })

  it('renders Chromium availability separately from browser session state', async () => {
    const { wrapper } = await mountPage({
      status: {
        browser: 'READY',
        chromium: 'UNAVAILABLE',
        chromium_code: 'BROWSER_UNAVAILABLE',
      },
    })

    const statusText = wrapper.get('.xhs-movie-page__status').text()
    expect(statusText).toContain('浏览器READY')
    expect(statusText).toContain('ChromiumUNAVAILABLE')
    expect(statusText).toContain('BROWSER_UNAVAILABLE')
  })

  it('hides the private Chromium installer for an external CDP browser', async () => {
    const { wrapper } = await mountPage({
      status: {
        browser_mode: 'CDP',
        chromium: 'EXTERNAL',
      },
    })

    expect(wrapper.text()).toContain('浏览器模式CDP')
    expect(wrapper.findAll('button').some(button => button.text() === '安装 Chromium')).toBe(false)
  })

  it('rejects an unknown media type until the operator explicitly selects movie or tv', async () => {
    const { api, wrapper } = await mountPage({ row: request({ media_type: 'unknown' }) })

    await manualButton(wrapper).trigger('click')
    await flushPromises()

    expect(api.post).not.toHaveBeenCalled()
    expect(wrapper.get('[data-alert-type="error"]').text()).toContain('影视类型必须是电影或电视剧')
  })

  it('submits the operator-edited draft rather than the cached request values', async () => {
    const { api, wrapper } = await mountPage({ row: request({ media_type: 'unknown' }) })

    await wrapper.get('input[aria-label="影视标题"]').setValue('编辑后的降临')
    await wrapper.get('input[aria-label="原始标题（可选）"]').setValue('Arrival edited')
    await wrapper.get('select[aria-label="影视类型"]').setValue('tv')
    await wrapper.get('input[aria-label="年份（可选）"]').setValue('2020')
    await wrapper.get('input[aria-label="季（可选）"]').setValue('2')
    await manualButton(wrapper).trigger('click')
    await flushPromises()

    expect(api.post).toHaveBeenCalledWith(
      'plugin/XhsMovieAssistant/requests/7/manual',
      {
        title: '编辑后的降临',
        original_title: 'Arrival edited',
        media_type: 'tv',
        year: 2020,
        season: 2,
      },
    )
  })

  it('does not post an empty edited title', async () => {
    const { api, wrapper } = await mountPage()

    await wrapper.get('input[aria-label="影视标题"]').setValue('   ')
    await manualButton(wrapper).trigger('click')
    await flushPromises()

    expect(api.post).not.toHaveBeenCalled()
    expect(wrapper.get('[data-alert-type="error"]').text()).toContain('标题不能为空')
  })

  it.each([
    ['FAILED', '匹配失败', 'error', '人工确认失败：匹配失败'],
    ['NEED_CONFIRMATION', '候选不唯一', 'warning', '人工确认仍需确认：候选不唯一'],
  ])('shows a clear %s outcome and refreshes state after a manual action', async (status, detail, alertType, feedback) => {
    const { api, wrapper } = await mountPage({
      postResponse: { success: true, data: { status, message: detail } },
    })

    await manualButton(wrapper).trigger('click')
    await flushPromises()

    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledTimes(2)
    expect(wrapper.get(`[data-alert-type="${alertType}"]`).text()).toContain(feedback)
  })

  it.each([
    ['SUBSCRIBED', 'success', '人工确认完成：已创建订阅'],
    ['ALREADY_SUBSCRIBED', 'info', '人工确认已存在，无需重复订阅'],
  ])('keeps %s outcomes distinct from failed actions', async (status, alertType, feedback) => {
    const { wrapper } = await mountPage({
      postResponse: { success: true, data: { status } },
    })

    await manualButton(wrapper).trigger('click')
    await flushPromises()

    expect(wrapper.get(`[data-alert-type="${alertType}"]`).text()).toContain(feedback)
  })

  it('gives the refresh and request actions a 44px minimum touch target', async () => {
    const { wrapper } = await mountPage()

    for (const button of wrapper.findAll('button')) {
      const style = window.getComputedStyle(button.element)
      expect(style.minWidth).toBe('44px')
      expect(style.minHeight).toBe('44px')
    }
  })
})
