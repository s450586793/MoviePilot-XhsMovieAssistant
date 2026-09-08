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

function replyButton(wrapper) {
  return wrapper.find('button[aria-label^="补发小红书回复"]')
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

  it('renders imported session state without a browser mode selector', async () => {
    const { wrapper } = await mountPage({
      status: {
        session_state: 'PRESENT',
      },
    })

    expect(wrapper.text()).toContain('登录凭据PRESENT')
    expect(wrapper.text()).not.toContain('浏览器模式')
    expect(wrapper.findAll('button').some(button => button.text() === '安装 Chromium')).toBe(true)
  })

  it('shows complete Cookie acquisition and credential safety guidance', async () => {
    const { wrapper } = await mountPage()
    const help = wrapper.get('[aria-labelledby="cookie-help-title"]')

    expect(help.text()).toContain('Cookie 从哪里获取')
    expect(help.text()).toContain('Network（网络）')
    expect(help.text()).toContain('Request Headers（请求标头）')
    expect(help.text()).toContain('document.cookie')
    expect(help.text()).toContain('RedNote')
    expect(help.get('a[href="https://www.xiaohongshu.com/explore"]').attributes('rel')).toBe('noopener noreferrer')
    expect(help.get('a[href="https://www.rednote.com/explore"]').attributes('rel')).toBe('noopener noreferrer')
  })

  it('imports a masked Cookie and clears the field immediately', async () => {
    const { api, wrapper } = await mountPage()
    const input = wrapper.get('input[aria-label="小红书 Cookie"]')
    expect(input.attributes('type')).toBe('password')
    await input.setValue('a1=secret-a1; web_session=secret-session')

    const button = wrapper.findAll('button').find(item => item.text() === '导入 Cookie')
    await button.trigger('click')
    await flushPromises()

    expect(api.post).toHaveBeenCalledWith(
      'plugin/XhsMovieAssistant/session/import',
      { cookie: 'a1=secret-a1; web_session=secret-session' },
    )
    expect(input.element.value).toBe('')
  })

  it('imports a selected Playwright Storage State JSON file', async () => {
    const { api, wrapper } = await mountPage()
    const storageState = {
      cookies: [{
        name: 'a1',
        value: 'secret',
        domain: '.xiaohongshu.com',
        path: '/',
        expires: -1,
        httpOnly: false,
        secure: true,
        sameSite: 'Lax',
      }],
      origins: [],
    }
    const file = new File(
      [JSON.stringify(storageState)],
      'storageState.json',
      { type: 'application/json' },
    )
    const input = wrapper.get('input[aria-label="Storage State 文件"]')
    Object.defineProperty(input.element, 'files', { value: [file] })

    await input.trigger('change')
    await vi.waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        'plugin/XhsMovieAssistant/session/import',
        { storage_state: storageState },
      )
    })
    expect(input.element.value).toBe('')
  })

  it('rejects malformed Storage State JSON without posting it', async () => {
    const { api, wrapper } = await mountPage()
    const file = new File(['{"cookies":'], 'broken.json', { type: 'application/json' })
    const input = wrapper.get('input[aria-label="Storage State 文件"]')
    Object.defineProperty(input.element, 'files', { value: [file] })

    await input.trigger('change')
    await vi.waitFor(() => {
      expect(wrapper.get('[data-alert-type="error"]').text()).toContain('JSON')
    })

    expect(api.post).not.toHaveBeenCalled()
    expect(input.element.value).toBe('')
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

  it('offers one pending terminal result for an explicit reply retry', async () => {
    const { api, wrapper } = await mountPage({
      row: request({ status: 'SUBSCRIBED', reply: 'PENDING' }),
      postResponse: { success: true, data: { status: 'SUBSCRIBED' } },
    })

    await replyButton(wrapper).trigger('click')
    await flushPromises()

    expect(api.post).toHaveBeenCalledWith('plugin/XhsMovieAssistant/requests/7/reply')
    expect(wrapper.get('[data-alert-type="success"]').text()).toContain('小红书回复完成')
  })

  it.each(['SENT:reply-1', 'FAILED'])('does not offer another reply after delivery is final: %s', async (reply) => {
    const { wrapper } = await mountPage({
      row: request({ status: 'SUBSCRIBED', reply }),
    })

    expect(replyButton(wrapper).exists()).toBe(false)
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
