import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { _ as _export_sfc } from './_plugin-vue_export-helper-pcqpp-6-.js';

const {createElementVNode:_createElementVNode,resolveComponent:_resolveComponent,createVNode:_createVNode,toDisplayString:_toDisplayString,createTextVNode:_createTextVNode,withCtx:_withCtx,openBlock:_openBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode,createElementBlock:_createElementBlock,withKeys:_withKeys,renderList:_renderList,Fragment:_Fragment,createStaticVNode:_createStaticVNode} = await importShared('vue');


const _hoisted_1 = {
  class: "xhs-movie-page",
  "aria-label": "小红书影视助手运行面板"
};
const _hoisted_2 = { class: "xhs-movie-page__header" };
const _hoisted_3 = {
  class: "xhs-movie-page__status",
  "aria-label": "缓存服务状态"
};
const _hoisted_4 = { key: 0 };
const _hoisted_5 = {
  class: "xhs-movie-page__section",
  "aria-labelledby": "session-import"
};
const _hoisted_6 = { class: "xhs-movie-page__section-title" };
const _hoisted_7 = { class: "xhs-movie-page__credential-state" };
const _hoisted_8 = { class: "xhs-movie-page__session-import" };
const _hoisted_9 = {
  class: "xhs-movie-page__section",
  "aria-labelledby": "management-actions"
};
const _hoisted_10 = { class: "xhs-movie-page__tool-grid" };
const _hoisted_11 = {
  class: "xhs-movie-page__section",
  "aria-labelledby": "recent-requests"
};
const _hoisted_12 = { class: "xhs-movie-page__section-title" };
const _hoisted_13 = {
  key: 1,
  class: "xhs-movie-page__empty"
};
const _hoisted_14 = { class: "xhs-movie-request__header" };
const _hoisted_15 = { class: "xhs-movie-request__id" };
const _hoisted_16 = { class: "xhs-movie-request__summary" };
const _hoisted_17 = {
  key: 0,
  class: "xhs-movie-request__editor"
};
const _hoisted_18 = {
  key: 1,
  class: "xhs-movie-request__actions"
};

const {computed,onMounted,reactive,ref} = await importShared('vue');



const _sfc_main = {
  __name: 'Page',
  props: {
  api: { type: Object, default: () => ({}) },
},
  setup(__props) {

const props = __props;

const loading = ref(false);
const actionKey = ref('');
const feedback = ref({ type: 'info', text: '', source: '' });
const snapshot = ref({ status: {}, requests: [] });
const cookieDraft = ref('');
const storageFileInput = ref(null);
const drafts = reactive({});

const actionableStatuses = new Set(['NEW', 'FAILED', 'NEED_CONFIRMATION', 'DRY_RUN_MATCHED']);
const replyableStatuses = new Set(['SUBSCRIBED', 'ALREADY_SUBSCRIBED', 'ALREADY_IN_LIBRARY', 'NEED_CONFIRMATION', 'FAILED']);
const status = computed(() => snapshot.value.status || {});
const requests = computed(() => Array.isArray(snapshot.value.requests) ? snapshot.value.requests : []);
const credentialStatus = computed(() => {
  const sessionState = status.value.session_state;
  const loginState = status.value.login;
  if (sessionState === 'MISSING') return { label: 'Cookie：未保存', color: 'default' }
  if (sessionState === 'PRESENT' && loginState === 'LOGGED_IN') return { label: 'Cookie：有效', color: 'success' }
  if (sessionState === 'PRESENT' && loginState === 'LOGGED_OUT') return { label: 'Cookie：已失效', color: 'error' }
  if (sessionState === 'PRESENT') return { label: 'Cookie：待验证', color: 'warning' }
  return { label: 'Cookie：状态未知', color: 'default' }
});

function unwrap(response) {
  const body = response && Object.prototype.hasOwnProperty.call(response, 'success')
    ? response
    : (response?.data ?? response);
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
    };
  }
  return drafts[row.id]
}

function optionalInteger(value) {
  const normalized = String(value ?? '').trim();
  if (!normalized) return null
  const parsed = Number(normalized);
  return Number.isInteger(parsed) ? parsed : Number.NaN
}

function isActionable(row) {
  return actionableStatuses.has(row.status)
}

function canReply(row) {
  return replyableStatuses.has(row.status) && row.reply === 'PENDING'
}

function showFeedback(type, text, source = 'action') {
  feedback.value = { type, text, source };
}

function actionFeedback(data, label) {
  const status = String(data?.status || '');
  const detail = String(data?.message || '').trim();
  const withDetail = fallback => detail ? `：${detail}` : fallback;

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
    showFeedback('error', 'MoviePilot API 客户端不可用。', 'state');
    return
  }
  loading.value = true;
  try {
    const response = await props.api.get('plugin/XhsMovieAssistant/state');
    const data = unwrap(response);
    snapshot.value = {
      status: data?.status || {},
      requests: Array.isArray(data?.requests) ? data.requests : [],
    };
    requests.value.forEach(draftFor);
    if (feedback.value.source === 'state') {
      showFeedback('info', '', 'state');
    }
  } catch (error) {
    showFeedback('error', message(error, '无法读取缓存状态。'), 'state');
  } finally {
    loading.value = false;
  }
}

async function runAction(path, label, body) {
  if (!props.api?.post || actionKey.value) return
  actionKey.value = path;
  try {
    const response = body === undefined
      ? await props.api.post(path)
      : await props.api.post(path, body);
    const data = unwrap(response);
    const outcome = actionFeedback(data, label);
    showFeedback(outcome.type, outcome.text);
    await loadState();
  } catch (error) {
    showFeedback('error', message(error, `${label}失败，请检查当前状态后重试。`));
  } finally {
    actionKey.value = '';
  }
}

async function submitManual(row) {
  const draft = draftFor(row);
  if (!draft.title.trim()) {
    showFeedback('error', '标题不能为空。');
    return
  }
  if (!['movie', 'tv'].includes(draft.media_type)) {
    showFeedback('error', '影视类型必须是电影或电视剧。');
    return
  }
  const year = optionalInteger(draft.year);
  const season = optionalInteger(draft.season);
  if (Number.isNaN(year) || Number.isNaN(season)) {
    showFeedback('error', '年份和季数必须是整数。');
    return
  }
  const payload = {
    title: draft.title.trim(),
    original_title: draft.original_title.trim(),
    media_type: draft.media_type,
    year: optionalInteger(draft.year),
    season: optionalInteger(draft.season),
  };
  const path = `plugin/XhsMovieAssistant/requests/${row.id}/manual`;
  await runAction(path, `请求 #${row.id} 的人工确认`, payload);
}

async function importCookie() {
  const cookie = cookieDraft.value.trim();
  if (!cookie) {
    showFeedback('error', 'Cookie 不能为空。');
    return
  }
  cookieDraft.value = '';
  await runAction(
    'plugin/XhsMovieAssistant/session/import',
    'Cookie 导入',
    { cookie },
  );
}

function selectStorageState() {
  storageFileInput.value?.click();
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener('load', () => resolve(String(reader.result || '')));
    reader.addEventListener('error', () => reject(new Error('无法读取 Storage State 文件。')));
    reader.readAsText(file);
  })
}

async function importStorageState(event) {
  const input = event.target;
  const file = input?.files?.[0];
  if (input) input.value = '';
  if (!file) return
  if (file.size > 1024 * 1024) {
    showFeedback('error', 'Storage State 文件不能超过 1 MB。');
    return
  }
  try {
    const storageState = JSON.parse(await readFile(file));
    if (!storageState || Array.isArray(storageState) || typeof storageState !== 'object') {
      throw new Error('Storage State JSON 格式无效。')
    }
    await runAction(
      'plugin/XhsMovieAssistant/session/import',
      'Storage State 导入',
      { storage_state: storageState },
    );
  } catch (error) {
    showFeedback('error', message(error, 'Storage State 导入失败。'));
  }
}

onMounted(loadState);

return (_ctx, _cache) => {
  const _component_VBtn = _resolveComponent("VBtn");
  const _component_VAlert = _resolveComponent("VAlert");
  const _component_VChip = _resolveComponent("VChip");
  const _component_VTextField = _resolveComponent("VTextField");
  const _component_VProgressLinear = _resolveComponent("VProgressLinear");
  const _component_VSelect = _resolveComponent("VSelect");

  return (_openBlock(), _createElementBlock("main", _hoisted_1, [
    _createElementVNode("header", _hoisted_2, [
      _cache[10] || (_cache[10] = _createElementVNode("div", null, [
        _createElementVNode("p", { class: "xhs-movie-page__eyebrow" }, "运行面板"),
        _createElementVNode("h1", null, "小红书影视助手")
      ], -1)),
      _createVNode(_component_VBtn, {
        icon: "mdi-refresh",
        variant: "text",
        loading: loading.value,
        title: "刷新状态",
        "aria-label": "刷新状态",
        onClick: loadState
      }, null, 8, ["loading"])
    ]),
    (feedback.value.text)
      ? (_openBlock(), _createBlock(_component_VAlert, {
          key: 0,
          type: feedback.value.type,
          variant: "tonal",
          closable: "",
          class: "xhs-movie-page__feedback",
          "onClick:close": _cache[0] || (_cache[0] = $event => (showFeedback('info', '')))
        }, {
          default: _withCtx(() => [
            _createTextVNode(_toDisplayString(feedback.value.text), 1)
          ]),
          _: 1
        }, 8, ["type"]))
      : _createCommentVNode("", true),
    _createElementVNode("section", _hoisted_3, [
      _createElementVNode("dl", null, [
        _createElementVNode("div", null, [
          _cache[11] || (_cache[11] = _createElementVNode("dt", null, "插件", -1)),
          _createElementVNode("dd", null, _toDisplayString(status.value.activity || 'IDLE'), 1)
        ]),
        _createElementVNode("div", null, [
          _cache[12] || (_cache[12] = _createElementVNode("dt", null, "浏览器", -1)),
          _createElementVNode("dd", null, _toDisplayString(status.value.browser || 'UNKNOWN'), 1)
        ]),
        _createElementVNode("div", null, [
          _cache[13] || (_cache[13] = _createElementVNode("dt", null, "Chromium", -1)),
          _createElementVNode("dd", null, [
            _createTextVNode(_toDisplayString(status.value.chromium || 'UNKNOWN'), 1),
            (status.value.chromium_code)
              ? (_openBlock(), _createElementBlock("span", _hoisted_4, " (" + _toDisplayString(status.value.chromium_code) + ")", 1))
              : _createCommentVNode("", true)
          ])
        ]),
        _createElementVNode("div", null, [
          _cache[14] || (_cache[14] = _createElementVNode("dt", null, "登录", -1)),
          _createElementVNode("dd", null, _toDisplayString(status.value.login || 'UNKNOWN'), 1)
        ]),
        _createElementVNode("div", null, [
          _cache[15] || (_cache[15] = _createElementVNode("dt", null, "登录凭据", -1)),
          _createElementVNode("dd", null, _toDisplayString(status.value.session_state || 'MISSING'), 1)
        ]),
        _createElementVNode("div", null, [
          _cache[16] || (_cache[16] = _createElementVNode("dt", null, "暂停原因", -1)),
          _createElementVNode("dd", null, _toDisplayString(status.value.pause_code || '无'), 1)
        ])
      ])
    ]),
    _createElementVNode("section", _hoisted_5, [
      _createElementVNode("div", _hoisted_6, [
        _cache[17] || (_cache[17] = _createElementVNode("h2", { id: "session-import" }, "登录凭据", -1)),
        _createVNode(_component_VChip, {
          size: "small",
          color: credentialStatus.value.color,
          variant: "tonal"
        }, {
          default: _withCtx(() => [
            _createElementVNode("span", _hoisted_7, _toDisplayString(credentialStatus.value.label), 1)
          ]),
          _: 1
        }, 8, ["color"])
      ]),
      _cache[20] || (_cache[20] = _createStaticVNode("<aside class=\"xhs-movie-page__credential-help\" aria-labelledby=\"cookie-help-title\" data-v-57250e7f><div class=\"xhs-movie-page__credential-help-title\" data-v-57250e7f><h3 id=\"cookie-help-title\" data-v-57250e7f>Cookie 从哪里获取</h3><a href=\"https://www.xiaohongshu.com/explore\" target=\"_blank\" rel=\"noopener noreferrer\" data-v-57250e7f>打开小红书网页版</a></div><ol data-v-57250e7f><li data-v-57250e7f>使用电脑 Chrome 或 Edge 登录“小红书影视助手”小号。</li><li data-v-57250e7f>按 F12 打开开发者工具，选择 Network（网络），然后刷新页面。</li><li data-v-57250e7f>点开任意发往 xiaohongshu.com 的请求，在 Headers（标头）→ Request Headers（请求标头）中找到 Cookie。</li><li data-v-57250e7f>只复制 <code data-v-57250e7f>Cookie:</code> 后面的完整内容，粘贴到下方并点击“保存并验证 Cookie”。</li></ol><p data-v-57250e7f>插件设置选择 RedNote 时，请改在 <a href=\"https://www.rednote.com/explore\" target=\"_blank\" rel=\"noopener noreferrer\" data-v-57250e7f>RedNote 网页版</a>执行相同步骤，凭据必须与所选站点一致。</p><p class=\"xhs-movie-page__credential-warning\" data-v-57250e7f>Cookie 等同于账号登录凭据。不要使用 <code data-v-57250e7f>document.cookie</code>，也不要把 Cookie 发给他人、上传 GitHub 或放进截图。</p></aside>", 1)),
      _createElementVNode("div", _hoisted_8, [
        _createVNode(_component_VTextField, {
          modelValue: cookieDraft.value,
          "onUpdate:modelValue": _cache[1] || (_cache[1] = $event => ((cookieDraft).value = $event)),
          label: "小红书 Cookie",
          type: "password",
          autocomplete: "new-password",
          density: "comfortable",
          onKeyup: _withKeys(importCookie, ["enter"])
        }, null, 8, ["modelValue"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-cookie-check-outline",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/session/import',
          onClick: importCookie
        }, {
          default: _withCtx(() => [...(_cache[18] || (_cache[18] = [
            _createTextVNode("保存并验证 Cookie", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createElementVNode("input", {
          ref_key: "storageFileInput",
          ref: storageFileInput,
          type: "file",
          accept: "application/json,.json",
          "aria-label": "Storage State 文件",
          hidden: "",
          onChange: importStorageState
        }, null, 544),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-file-upload-outline",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/session/import',
          onClick: selectStorageState
        }, {
          default: _withCtx(() => [...(_cache[19] || (_cache[19] = [
            _createTextVNode("保存并验证 Storage State", -1)
          ]))]),
          _: 1
        }, 8, ["loading"])
      ])
    ]),
    _createElementVNode("section", _hoisted_9, [
      _cache[29] || (_cache[29] = _createElementVNode("div", { class: "xhs-movie-page__section-title" }, [
        _createElementVNode("h2", { id: "management-actions" }, "管理与诊断")
      ], -1)),
      _createElementVNode("div", _hoisted_10, [
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-download",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/chromium/install',
          onClick: _cache[2] || (_cache[2] = $event => (runAction('plugin/XhsMovieAssistant/chromium/install', 'Chromium 安装')))
        }, {
          default: _withCtx(() => [...(_cache[21] || (_cache[21] = [
            _createTextVNode("安装 Chromium", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-shield-check-outline",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/session/validate',
          onClick: _cache[3] || (_cache[3] = $event => (runAction('plugin/XhsMovieAssistant/session/validate', '登录验证')))
        }, {
          default: _withCtx(() => [...(_cache[22] || (_cache[22] = [
            _createTextVNode("验证登录", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-logout",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/session/clear',
          onClick: _cache[4] || (_cache[4] = $event => (runAction('plugin/XhsMovieAssistant/session/clear', '清除登录')))
        }, {
          default: _withCtx(() => [...(_cache[23] || (_cache[23] = [
            _createTextVNode("清除登录", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-refresh",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/poll',
          onClick: _cache[5] || (_cache[5] = $event => (runAction('plugin/XhsMovieAssistant/poll', '立即轮询')))
        }, {
          default: _withCtx(() => [...(_cache[24] || (_cache[24] = [
            _createTextVNode("立即轮询", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-play",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/resume',
          onClick: _cache[6] || (_cache[6] = $event => (runAction('plugin/XhsMovieAssistant/resume', '恢复轮询')))
        }, {
          default: _withCtx(() => [...(_cache[25] || (_cache[25] = [
            _createTextVNode("恢复轮询", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-robot-outline",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/test/ai',
          onClick: _cache[7] || (_cache[7] = $event => (runAction('plugin/XhsMovieAssistant/test/ai', 'AI 测试', { title: '星际穿越' })))
        }, {
          default: _withCtx(() => [...(_cache[26] || (_cache[26] = [
            _createTextVNode("测试 AI", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-movie-search-outline",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/test/moviepilot',
          onClick: _cache[8] || (_cache[8] = $event => (runAction('plugin/XhsMovieAssistant/test/moviepilot', 'MoviePilot 测试', { title: '星际穿越', media_type: 'movie' })))
        }, {
          default: _withCtx(() => [...(_cache[27] || (_cache[27] = [
            _createTextVNode("测试 MoviePilot", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-bell-check-outline",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/test/notification',
          onClick: _cache[9] || (_cache[9] = $event => (runAction('plugin/XhsMovieAssistant/test/notification', '通知测试')))
        }, {
          default: _withCtx(() => [...(_cache[28] || (_cache[28] = [
            _createTextVNode("测试通知", -1)
          ]))]),
          _: 1
        }, 8, ["loading"])
      ])
    ]),
    _createElementVNode("section", _hoisted_11, [
      _createElementVNode("div", _hoisted_12, [
        _cache[30] || (_cache[30] = _createElementVNode("h2", { id: "recent-requests" }, "最近请求", -1)),
        _createElementVNode("span", null, _toDisplayString(requests.value.length) + " 项", 1)
      ]),
      (loading.value)
        ? (_openBlock(), _createBlock(_component_VProgressLinear, {
            key: 0,
            indeterminate: "",
            color: "primary",
            class: "mb-3"
          }))
        : _createCommentVNode("", true),
      (!loading.value && !requests.value.length)
        ? (_openBlock(), _createElementBlock("p", _hoisted_13, "没有可显示的持久化请求。"))
        : _createCommentVNode("", true),
      (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(requests.value, (row) => {
        return (_openBlock(), _createElementBlock("article", {
          key: row.id,
          class: "xhs-movie-request"
        }, [
          _createElementVNode("header", _hoisted_14, [
            _createElementVNode("div", null, [
              _createElementVNode("span", _hoisted_15, "#" + _toDisplayString(row.id), 1),
              _createElementVNode("h3", null, _toDisplayString(row.title), 1)
            ]),
            _createVNode(_component_VChip, {
              size: "small",
              color: row.status === 'FAILED' ? 'error' : row.status === 'NEED_CONFIRMATION' ? 'warning' : 'primary',
              variant: "tonal"
            }, {
              default: _withCtx(() => [
                _createTextVNode(_toDisplayString(row.status), 1)
              ]),
              _: 2
            }, 1032, ["color"])
          ]),
          _createElementVNode("dl", _hoisted_16, [
            _createElementVNode("div", null, [
              _cache[31] || (_cache[31] = _createElementVNode("dt", null, "匹配", -1)),
              _createElementVNode("dd", null, _toDisplayString(row.match), 1)
            ]),
            _createElementVNode("div", null, [
              _cache[32] || (_cache[32] = _createElementVNode("dt", null, "回复", -1)),
              _createElementVNode("dd", null, _toDisplayString(row.reply), 1)
            ]),
            _createElementVNode("div", null, [
              _cache[33] || (_cache[33] = _createElementVNode("dt", null, "错误", -1)),
              _createElementVNode("dd", null, _toDisplayString(row.error), 1)
            ]),
            _createElementVNode("div", null, [
              _cache[34] || (_cache[34] = _createElementVNode("dt", null, "更新", -1)),
              _createElementVNode("dd", null, _toDisplayString(row.updated_at), 1)
            ])
          ]),
          (isActionable(row))
            ? (_openBlock(), _createElementBlock("div", _hoisted_17, [
                _createVNode(_component_VTextField, {
                  modelValue: draftFor(row).title,
                  "onUpdate:modelValue": $event => ((draftFor(row).title) = $event),
                  label: "影视标题",
                  density: "comfortable"
                }, null, 8, ["modelValue", "onUpdate:modelValue"]),
                _createVNode(_component_VTextField, {
                  modelValue: draftFor(row).original_title,
                  "onUpdate:modelValue": $event => ((draftFor(row).original_title) = $event),
                  label: "原始标题（可选）",
                  density: "comfortable"
                }, null, 8, ["modelValue", "onUpdate:modelValue"]),
                _createVNode(_component_VSelect, {
                  modelValue: draftFor(row).media_type,
                  "onUpdate:modelValue": $event => ((draftFor(row).media_type) = $event),
                  label: "影视类型",
                  density: "comfortable",
                  items: [{ title: '电影', value: 'movie' }, { title: '电视剧', value: 'tv' }]
                }, null, 8, ["modelValue", "onUpdate:modelValue"]),
                _createVNode(_component_VTextField, {
                  modelValue: draftFor(row).year,
                  "onUpdate:modelValue": $event => ((draftFor(row).year) = $event),
                  label: "年份（可选）",
                  density: "comfortable",
                  inputmode: "numeric"
                }, null, 8, ["modelValue", "onUpdate:modelValue"]),
                _createVNode(_component_VTextField, {
                  modelValue: draftFor(row).season,
                  "onUpdate:modelValue": $event => ((draftFor(row).season) = $event),
                  label: "季（可选）",
                  density: "comfortable",
                  inputmode: "numeric"
                }, null, 8, ["modelValue", "onUpdate:modelValue"])
              ]))
            : _createCommentVNode("", true),
          (isActionable(row) || canReply(row))
            ? (_openBlock(), _createElementBlock("footer", _hoisted_18, [
                (isActionable(row))
                  ? (_openBlock(), _createBlock(_component_VBtn, {
                      key: 0,
                      icon: "mdi-replay",
                      variant: "text",
                      title: "重新处理",
                      loading: actionKey.value === `plugin/XhsMovieAssistant/requests/${row.id}/reprocess`,
                      "aria-label": `重新处理请求 #${row.id}`,
                      onClick: $event => (runAction(`plugin/XhsMovieAssistant/requests/${row.id}/reprocess`, `请求 #${row.id} 重新处理`))
                    }, null, 8, ["loading", "aria-label", "onClick"]))
                  : _createCommentVNode("", true),
                (isActionable(row))
                  ? (_openBlock(), _createBlock(_component_VBtn, {
                      key: 1,
                      icon: "mdi-eye-off-outline",
                      variant: "text",
                      title: "忽略请求",
                      loading: actionKey.value === `plugin/XhsMovieAssistant/requests/${row.id}/ignore`,
                      "aria-label": `忽略请求 #${row.id}`,
                      onClick: $event => (runAction(`plugin/XhsMovieAssistant/requests/${row.id}/ignore`, `请求 #${row.id} 已忽略`))
                    }, null, 8, ["loading", "aria-label", "onClick"]))
                  : _createCommentVNode("", true),
                (isActionable(row))
                  ? (_openBlock(), _createBlock(_component_VBtn, {
                      key: 2,
                      color: "primary",
                      "prepend-icon": "mdi-check-decagram-outline",
                      loading: actionKey.value === `plugin/XhsMovieAssistant/requests/${row.id}/manual`,
                      onClick: $event => (submitManual(row))
                    }, {
                      default: _withCtx(() => [...(_cache[35] || (_cache[35] = [
                        _createTextVNode("人工确认", -1)
                      ]))]),
                      _: 1
                    }, 8, ["loading", "onClick"]))
                  : _createCommentVNode("", true),
                (canReply(row))
                  ? (_openBlock(), _createBlock(_component_VBtn, {
                      key: 3,
                      icon: "mdi-reply",
                      variant: "text",
                      title: "补发小红书回复",
                      loading: actionKey.value === `plugin/XhsMovieAssistant/requests/${row.id}/reply`,
                      "aria-label": `补发小红书回复 #${row.id}`,
                      onClick: $event => (runAction(`plugin/XhsMovieAssistant/requests/${row.id}/reply`, `请求 #${row.id} 的小红书回复`))
                    }, null, 8, ["loading", "aria-label", "onClick"]))
                  : _createCommentVNode("", true)
              ]))
            : _createCommentVNode("", true)
        ]))
      }), 128))
    ])
  ]))
}
}

};
const Page = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-57250e7f"]]);

export { Page as default };
