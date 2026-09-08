import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { _ as _export_sfc } from './_plugin-vue_export-helper-pcqpp-6-.js';

const {createElementVNode:_createElementVNode,resolveComponent:_resolveComponent,createVNode:_createVNode,withCtx:_withCtx,createTextVNode:_createTextVNode,withModifiers:_withModifiers,openBlock:_openBlock,createBlock:_createBlock} = await importShared('vue');


const _hoisted_1 = { class: "xhs-movie-config__section" };
const _hoisted_2 = { class: "xhs-movie-config__section" };
const _hoisted_3 = { class: "xhs-movie-config__section" };
const _hoisted_4 = { class: "xhs-movie-config__section" };
const _hoisted_5 = { class: "xhs-movie-config__actions" };

const {ref,watch} = await importShared('vue');



const _sfc_main = {
  __name: 'Config',
  props: {
  initialConfig: { type: Object, default: () => ({}) },
},
  emits: ['save', 'close'],
  setup(__props, { emit: __emit }) {

const props = __props;

const emit = __emit;

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
  authorized_user_ids: '',
  poll_interval_minutes: 2,
  confidence_threshold: 0.85,
  template_SUBSCRIBED: '收到，已安排订阅。',
  template_ALREADY_SUBSCRIBED: '《{title}》{year_text}{season_text}已经订阅，无需重复添加。',
  template_ALREADY_IN_LIBRARY: '《{title}》{year_text}{season_text}已经在媒体库中。',
  template_NEED_CONFIRMATION: '收到，影视不明确，请明示。',
  template_FAILED: '本次订阅处理失败，详情已通过 MoviePilot 通知发送。',
});

const LEGACY_DEFAULT_TEMPLATES = Object.freeze({
  template_SUBSCRIBED: '检测到{media_type}《{title}》{year_text}{season_text}，已推送订阅。',
  template_NEED_CONFIRMATION: '暂时无法确定这篇笔记中的具体影视作品，请人工确认。',
});

const localConfig = ref({ ...DEFAULT_CONFIG });

function clone(value) {
  return JSON.parse(JSON.stringify(value || {}))
}

function normalizedConfig(value) {
  const normalized = { ...DEFAULT_CONFIG, ...clone(value) };
  Object.entries(LEGACY_DEFAULT_TEMPLATES).forEach(([key, legacyValue]) => {
    if (normalized[key] === legacyValue) normalized[key] = DEFAULT_CONFIG[key];
  });
  return normalized
}

function saveConfig() {
  emit('save', clone(localConfig.value));
}

watch(
  () => props.initialConfig,
  value => {
    localConfig.value = normalizedConfig(value);
  },
  { immediate: true, deep: true },
);

return (_ctx, _cache) => {
  const _component_VSpacer = _resolveComponent("VSpacer");
  const _component_VBtn = _resolveComponent("VBtn");
  const _component_VToolbar = _resolveComponent("VToolbar");
  const _component_VDivider = _resolveComponent("VDivider");
  const _component_VSwitch = _resolveComponent("VSwitch");
  const _component_VCol = _resolveComponent("VCol");
  const _component_VRow = _resolveComponent("VRow");
  const _component_VSelect = _resolveComponent("VSelect");
  const _component_VTextField = _resolveComponent("VTextField");
  const _component_VTextarea = _resolveComponent("VTextarea");
  const _component_VForm = _resolveComponent("VForm");

  return (_openBlock(), _createBlock(_component_VForm, {
    class: "xhs-movie-config",
    "aria-label": "小红书影视助手配置",
    onSubmit: _withModifiers(saveConfig, ["prevent"])
  }, {
    default: _withCtx(() => [
      _createVNode(_component_VToolbar, {
        density: "comfortable",
        color: "transparent",
        class: "xhs-movie-config__toolbar"
      }, {
        default: _withCtx(() => [
          _cache[19] || (_cache[19] = _createElementVNode("div", { class: "text-h6" }, "小红书影视助手", -1)),
          _createVNode(_component_VSpacer),
          _createVNode(_component_VBtn, {
            icon: "mdi-content-save-outline",
            variant: "text",
            title: "保存配置",
            "aria-label": "保存配置",
            onClick: saveConfig
          }),
          _createVNode(_component_VBtn, {
            icon: "mdi-close",
            variant: "text",
            title: "关闭配置",
            "aria-label": "关闭配置",
            onClick: _cache[0] || (_cache[0] = $event => (emit('close')))
          })
        ]),
        _: 1
      }),
      _createVNode(_component_VDivider),
      _createElementVNode("section", _hoisted_1, [
        _cache[20] || (_cache[20] = _createElementVNode("div", { class: "text-subtitle-2 mb-2" }, "运行策略", -1)),
        _createVNode(_component_VRow, { dense: "" }, {
          default: _withCtx(() => [
            _createVNode(_component_VCol, {
              cols: "12",
              sm: "6"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSwitch, {
                  modelValue: localConfig.value.enabled,
                  "onUpdate:modelValue": _cache[1] || (_cache[1] = $event => ((localConfig.value.enabled) = $event)),
                  color: "primary",
                  label: "启用轮询",
                  "hide-details": ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              sm: "6"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSwitch, {
                  modelValue: localConfig.value.enable_subscription,
                  "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((localConfig.value.enable_subscription) = $event)),
                  color: "warning",
                  label: "允许真实订阅",
                  "hide-details": ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              sm: "6"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSwitch, {
                  modelValue: localConfig.value.notifications_enabled,
                  "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((localConfig.value.notifications_enabled) = $event)),
                  color: "primary",
                  label: "MoviePilot 通知",
                  "hide-details": ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              sm: "6"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSwitch, {
                  modelValue: localConfig.value.reply_enabled,
                  "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((localConfig.value.reply_enabled) = $event)),
                  color: "primary",
                  label: "小红书公开回复",
                  "hide-details": ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            })
          ]),
          _: 1
        })
      ]),
      _createElementVNode("section", _hoisted_2, [
        _cache[21] || (_cache[21] = _createElementVNode("div", { class: "text-subtitle-2 mb-2" }, "回复范围", -1)),
        _createVNode(_component_VRow, { dense: "" }, {
          default: _withCtx(() => [
            _createVNode(_component_VCol, {
              cols: "12",
              sm: "6"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSwitch, {
                  modelValue: localConfig.value.reply_success_enabled,
                  "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((localConfig.value.reply_success_enabled) = $event)),
                  color: "success",
                  label: "回复订阅成功",
                  "hide-details": ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              sm: "6"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSwitch, {
                  modelValue: localConfig.value.reply_existing_enabled,
                  "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((localConfig.value.reply_existing_enabled) = $event)),
                  color: "info",
                  label: "回复已存在",
                  "hide-details": ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              sm: "6"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSwitch, {
                  modelValue: localConfig.value.reply_confirmation_enabled,
                  "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((localConfig.value.reply_confirmation_enabled) = $event)),
                  color: "warning",
                  label: "回复需人工确认",
                  "hide-details": ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              sm: "6"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSwitch, {
                  modelValue: localConfig.value.reply_failure_enabled,
                  "onUpdate:modelValue": _cache[8] || (_cache[8] = $event => ((localConfig.value.reply_failure_enabled) = $event)),
                  color: "error",
                  label: "回复处理失败",
                  "hide-details": ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            })
          ]),
          _: 1
        })
      ]),
      _createElementVNode("section", _hoisted_3, [
        _cache[22] || (_cache[22] = _createElementVNode("div", { class: "text-subtitle-2 mb-2" }, "识别条件", -1)),
        _createVNode(_component_VRow, { dense: "" }, {
          default: _withCtx(() => [
            _createVNode(_component_VCol, {
              cols: "12",
              sm: "6"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSelect, {
                  modelValue: localConfig.value.site,
                  "onUpdate:modelValue": _cache[9] || (_cache[9] = $event => ((localConfig.value.site) = $event)),
                  label: "站点",
                  items: [{ title: '小红书', value: 'xiaohongshu' }, { title: 'RedNote', value: 'rednote' }]
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              sm: "3"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VTextField, {
                  modelValue: localConfig.value.poll_interval_minutes,
                  "onUpdate:modelValue": _cache[10] || (_cache[10] = $event => ((localConfig.value.poll_interval_minutes) = $event)),
                  modelModifiers: { number: true },
                  label: "轮询间隔（分钟）",
                  type: "number",
                  min: "1",
                  max: "10"
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              sm: "3"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VTextField, {
                  modelValue: localConfig.value.confidence_threshold,
                  "onUpdate:modelValue": _cache[11] || (_cache[11] = $event => ((localConfig.value.confidence_threshold) = $event)),
                  modelModifiers: { number: true },
                  label: "AI 置信度阈值",
                  type: "number",
                  min: "0",
                  max: "1",
                  step: "0.05"
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, { cols: "12" }, {
              default: _withCtx(() => [
                _createVNode(_component_VTextarea, {
                  modelValue: localConfig.value.authorized_user_ids,
                  "onUpdate:modelValue": _cache[12] || (_cache[12] = $event => ((localConfig.value.authorized_user_ids) = $event)),
                  label: "授权用户 ID（逗号或换行分隔）",
                  rows: "3",
                  "auto-grow": ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            })
          ]),
          _: 1
        })
      ]),
      _createElementVNode("section", _hoisted_4, [
        _cache[23] || (_cache[23] = _createElementVNode("div", { class: "text-subtitle-2 mb-2" }, "公开回复模板", -1)),
        _createVNode(_component_VTextarea, {
          modelValue: localConfig.value.template_SUBSCRIBED,
          "onUpdate:modelValue": _cache[13] || (_cache[13] = $event => ((localConfig.value.template_SUBSCRIBED) = $event)),
          label: "订阅成功回复模板",
          rows: "2",
          "auto-grow": ""
        }, null, 8, ["modelValue"]),
        _createVNode(_component_VTextarea, {
          modelValue: localConfig.value.template_ALREADY_SUBSCRIBED,
          "onUpdate:modelValue": _cache[14] || (_cache[14] = $event => ((localConfig.value.template_ALREADY_SUBSCRIBED) = $event)),
          label: "已订阅回复模板",
          rows: "2",
          "auto-grow": ""
        }, null, 8, ["modelValue"]),
        _createVNode(_component_VTextarea, {
          modelValue: localConfig.value.template_ALREADY_IN_LIBRARY,
          "onUpdate:modelValue": _cache[15] || (_cache[15] = $event => ((localConfig.value.template_ALREADY_IN_LIBRARY) = $event)),
          label: "媒体库已存在回复模板",
          rows: "2",
          "auto-grow": ""
        }, null, 8, ["modelValue"]),
        _createVNode(_component_VTextarea, {
          modelValue: localConfig.value.template_NEED_CONFIRMATION,
          "onUpdate:modelValue": _cache[16] || (_cache[16] = $event => ((localConfig.value.template_NEED_CONFIRMATION) = $event)),
          label: "需人工确认回复模板",
          rows: "2",
          "auto-grow": ""
        }, null, 8, ["modelValue"]),
        _createVNode(_component_VTextarea, {
          modelValue: localConfig.value.template_FAILED,
          "onUpdate:modelValue": _cache[17] || (_cache[17] = $event => ((localConfig.value.template_FAILED) = $event)),
          label: "处理失败回复模板",
          rows: "2",
          "auto-grow": ""
        }, null, 8, ["modelValue"])
      ]),
      _createElementVNode("div", _hoisted_5, [
        _createVNode(_component_VBtn, {
          variant: "text",
          onClick: _cache[18] || (_cache[18] = $event => (emit('close')))
        }, {
          default: _withCtx(() => [...(_cache[24] || (_cache[24] = [
            _createTextVNode("取消", -1)
          ]))]),
          _: 1
        }),
        _createVNode(_component_VBtn, {
          color: "primary",
          "prepend-icon": "mdi-content-save-outline",
          onClick: saveConfig
        }, {
          default: _withCtx(() => [...(_cache[25] || (_cache[25] = [
            _createTextVNode("保存", -1)
          ]))]),
          _: 1
        })
      ])
    ]),
    _: 1
  }))
}
}

};
const Config = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-4217fd0b"]]);

export { Config as default };
