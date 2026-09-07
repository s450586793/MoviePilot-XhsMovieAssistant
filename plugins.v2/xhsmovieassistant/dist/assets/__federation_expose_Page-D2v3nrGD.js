import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { _ as _export_sfc } from './_plugin-vue_export-helper-pcqpp-6-.js';

const {createElementVNode:_createElementVNode,resolveComponent:_resolveComponent,createVNode:_createVNode,toDisplayString:_toDisplayString,createTextVNode:_createTextVNode,withCtx:_withCtx,openBlock:_openBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode,createElementBlock:_createElementBlock,renderList:_renderList,Fragment:_Fragment} = await importShared('vue');


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
  "aria-labelledby": "management-actions"
};
const _hoisted_6 = { class: "xhs-movie-page__tool-grid" };
const _hoisted_7 = {
  class: "xhs-movie-page__section",
  "aria-labelledby": "recent-requests"
};
const _hoisted_8 = { class: "xhs-movie-page__section-title" };
const _hoisted_9 = {
  key: 1,
  class: "xhs-movie-page__empty"
};
const _hoisted_10 = { class: "xhs-movie-request__header" };
const _hoisted_11 = { class: "xhs-movie-request__id" };
const _hoisted_12 = { class: "xhs-movie-request__summary" };
const _hoisted_13 = {
  key: 0,
  class: "xhs-movie-request__editor"
};
const _hoisted_14 = {
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
const drafts = reactive({});

const actionableStatuses = new Set(['NEW', 'FAILED', 'NEED_CONFIRMATION', 'DRY_RUN_MATCHED']);
const status = computed(() => snapshot.value.status || {});
const requests = computed(() => Array.isArray(snapshot.value.requests) ? snapshot.value.requests : []);
const qrSource = computed(() => status.value.qrcode || '');

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

onMounted(loadState);

return (_ctx, _cache) => {
  const _component_VBtn = _resolveComponent("VBtn");
  const _component_VAlert = _resolveComponent("VAlert");
  const _component_VImg = _resolveComponent("VImg");
  const _component_VProgressLinear = _resolveComponent("VProgressLinear");
  const _component_VChip = _resolveComponent("VChip");
  const _component_VTextField = _resolveComponent("VTextField");
  const _component_VSelect = _resolveComponent("VSelect");

  return (_openBlock(), _createElementBlock("main", _hoisted_1, [
    _createElementVNode("header", _hoisted_2, [
      _cache[9] || (_cache[9] = _createElementVNode("div", null, [
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
          _cache[10] || (_cache[10] = _createElementVNode("dt", null, "插件", -1)),
          _createElementVNode("dd", null, _toDisplayString(status.value.activity || 'IDLE'), 1)
        ]),
        _createElementVNode("div", null, [
          _cache[11] || (_cache[11] = _createElementVNode("dt", null, "浏览器模式", -1)),
          _createElementVNode("dd", null, _toDisplayString(status.value.browser_mode || 'EMBEDDED'), 1)
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
          _cache[15] || (_cache[15] = _createElementVNode("dt", null, "暂停原因", -1)),
          _createElementVNode("dd", null, _toDisplayString(status.value.pause_code || '无'), 1)
        ])
      ]),
      (qrSource.value)
        ? (_openBlock(), _createBlock(_component_VImg, {
            key: 0,
            src: qrSource.value,
            width: "180",
            height: "180",
            contain: "",
            class: "xhs-movie-page__qr",
            alt: "小红书登录二维码"
          }, null, 8, ["src"]))
        : _createCommentVNode("", true)
    ]),
    _createElementVNode("section", _hoisted_5, [
      _cache[24] || (_cache[24] = _createElementVNode("div", { class: "xhs-movie-page__section-title" }, [
        _createElementVNode("h2", { id: "management-actions" }, "管理与诊断")
      ], -1)),
      _createElementVNode("div", _hoisted_6, [
        (status.value.browser_mode !== 'CDP')
          ? (_openBlock(), _createBlock(_component_VBtn, {
              key: 0,
              "prepend-icon": "mdi-download",
              variant: "outlined",
              loading: actionKey.value === 'plugin/XhsMovieAssistant/chromium/install',
              onClick: _cache[1] || (_cache[1] = $event => (runAction('plugin/XhsMovieAssistant/chromium/install', 'Chromium 安装')))
            }, {
              default: _withCtx(() => [...(_cache[16] || (_cache[16] = [
                _createTextVNode("安装 Chromium", -1)
              ]))]),
              _: 1
            }, 8, ["loading"]))
          : _createCommentVNode("", true),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-qrcode-scan",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/login/start',
          onClick: _cache[2] || (_cache[2] = $event => (runAction('plugin/XhsMovieAssistant/login/start', '登录二维码生成')))
        }, {
          default: _withCtx(() => [...(_cache[17] || (_cache[17] = [
            _createTextVNode("生成登录二维码", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-refresh",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/poll',
          onClick: _cache[3] || (_cache[3] = $event => (runAction('plugin/XhsMovieAssistant/poll', '立即轮询')))
        }, {
          default: _withCtx(() => [...(_cache[18] || (_cache[18] = [
            _createTextVNode("立即轮询", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-play",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/resume',
          onClick: _cache[4] || (_cache[4] = $event => (runAction('plugin/XhsMovieAssistant/resume', '恢复轮询')))
        }, {
          default: _withCtx(() => [...(_cache[19] || (_cache[19] = [
            _createTextVNode("恢复轮询", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-logout",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/logout',
          onClick: _cache[5] || (_cache[5] = $event => (runAction('plugin/XhsMovieAssistant/logout', '退出登录')))
        }, {
          default: _withCtx(() => [...(_cache[20] || (_cache[20] = [
            _createTextVNode("退出登录", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-robot-outline",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/test/ai',
          onClick: _cache[6] || (_cache[6] = $event => (runAction('plugin/XhsMovieAssistant/test/ai', 'AI 测试', { title: '星际穿越' })))
        }, {
          default: _withCtx(() => [...(_cache[21] || (_cache[21] = [
            _createTextVNode("测试 AI", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-movie-search-outline",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/test/moviepilot',
          onClick: _cache[7] || (_cache[7] = $event => (runAction('plugin/XhsMovieAssistant/test/moviepilot', 'MoviePilot 测试', { title: '星际穿越', media_type: 'movie' })))
        }, {
          default: _withCtx(() => [...(_cache[22] || (_cache[22] = [
            _createTextVNode("测试 MoviePilot", -1)
          ]))]),
          _: 1
        }, 8, ["loading"]),
        _createVNode(_component_VBtn, {
          "prepend-icon": "mdi-bell-check-outline",
          variant: "outlined",
          loading: actionKey.value === 'plugin/XhsMovieAssistant/test/notification',
          onClick: _cache[8] || (_cache[8] = $event => (runAction('plugin/XhsMovieAssistant/test/notification', '通知测试')))
        }, {
          default: _withCtx(() => [...(_cache[23] || (_cache[23] = [
            _createTextVNode("测试通知", -1)
          ]))]),
          _: 1
        }, 8, ["loading"])
      ])
    ]),
    _createElementVNode("section", _hoisted_7, [
      _createElementVNode("div", _hoisted_8, [
        _cache[25] || (_cache[25] = _createElementVNode("h2", { id: "recent-requests" }, "最近请求", -1)),
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
        ? (_openBlock(), _createElementBlock("p", _hoisted_9, "没有可显示的持久化请求。"))
        : _createCommentVNode("", true),
      (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(requests.value, (row) => {
        return (_openBlock(), _createElementBlock("article", {
          key: row.id,
          class: "xhs-movie-request"
        }, [
          _createElementVNode("header", _hoisted_10, [
            _createElementVNode("div", null, [
              _createElementVNode("span", _hoisted_11, "#" + _toDisplayString(row.id), 1),
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
          _createElementVNode("dl", _hoisted_12, [
            _createElementVNode("div", null, [
              _cache[26] || (_cache[26] = _createElementVNode("dt", null, "匹配", -1)),
              _createElementVNode("dd", null, _toDisplayString(row.match), 1)
            ]),
            _createElementVNode("div", null, [
              _cache[27] || (_cache[27] = _createElementVNode("dt", null, "回复", -1)),
              _createElementVNode("dd", null, _toDisplayString(row.reply), 1)
            ]),
            _createElementVNode("div", null, [
              _cache[28] || (_cache[28] = _createElementVNode("dt", null, "错误", -1)),
              _createElementVNode("dd", null, _toDisplayString(row.error), 1)
            ]),
            _createElementVNode("div", null, [
              _cache[29] || (_cache[29] = _createElementVNode("dt", null, "更新", -1)),
              _createElementVNode("dd", null, _toDisplayString(row.updated_at), 1)
            ])
          ]),
          (isActionable(row))
            ? (_openBlock(), _createElementBlock("div", _hoisted_13, [
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
          (isActionable(row))
            ? (_openBlock(), _createElementBlock("footer", _hoisted_14, [
                _createVNode(_component_VBtn, {
                  icon: "mdi-replay",
                  variant: "text",
                  title: "重新处理",
                  loading: actionKey.value === `plugin/XhsMovieAssistant/requests/${row.id}/reprocess`,
                  "aria-label": `重新处理请求 #${row.id}`,
                  onClick: $event => (runAction(`plugin/XhsMovieAssistant/requests/${row.id}/reprocess`, `请求 #${row.id} 重新处理`))
                }, null, 8, ["loading", "aria-label", "onClick"]),
                _createVNode(_component_VBtn, {
                  icon: "mdi-eye-off-outline",
                  variant: "text",
                  title: "忽略请求",
                  loading: actionKey.value === `plugin/XhsMovieAssistant/requests/${row.id}/ignore`,
                  "aria-label": `忽略请求 #${row.id}`,
                  onClick: $event => (runAction(`plugin/XhsMovieAssistant/requests/${row.id}/ignore`, `请求 #${row.id} 已忽略`))
                }, null, 8, ["loading", "aria-label", "onClick"]),
                _createVNode(_component_VBtn, {
                  color: "primary",
                  "prepend-icon": "mdi-check-decagram-outline",
                  loading: actionKey.value === `plugin/XhsMovieAssistant/requests/${row.id}/manual`,
                  onClick: $event => (submitManual(row))
                }, {
                  default: _withCtx(() => [...(_cache[30] || (_cache[30] = [
                    _createTextVNode("人工确认", -1)
                  ]))]),
                  _: 1
                }, 8, ["loading", "onClick"])
              ]))
            : _createCommentVNode("", true)
        ]))
      }), 128))
    ])
  ]))
}
}

};
const Page = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-81a8d770"]]);

export { Page as default };
