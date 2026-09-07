import { defineComponent } from 'vue'

const passthrough = (name, template = '<div><slot /></div>') => defineComponent({
  name,
  inheritAttrs: false,
  template,
})

export const VAlertStub = defineComponent({
  name: 'VAlert',
  props: { type: { type: String, default: 'info' } },
  template: '<section :data-alert-type="type"><slot /></section>',
})

export const VBtnStub = defineComponent({
  name: 'VBtn',
  inheritAttrs: false,
  props: { loading: Boolean },
  emits: ['click'],
  template: '<button class="v-btn" v-bind="$attrs" :disabled="loading" @click="$emit(\'click\')"><slot /></button>',
})

export const VSelectStub = defineComponent({
  name: 'VSelect',
  inheritAttrs: false,
  props: {
    items: { type: Array, default: () => [] },
    label: { type: String, default: '' },
    modelValue: { type: [String, Number], default: '' },
  },
  emits: ['update:modelValue'],
  template: `
    <label>
      <span>{{ label }}</span>
      <select v-bind="$attrs" :aria-label="label" :value="modelValue" @change="$emit('update:modelValue', $event.target.value)">
        <option v-for="item in items" :key="item.value" :value="item.value">{{ item.title }}</option>
      </select>
    </label>
  `,
})

export const VTextFieldStub = defineComponent({
  name: 'VTextField',
  inheritAttrs: false,
  props: {
    label: { type: String, default: '' },
    modelValue: { type: [String, Number], default: '' },
  },
  emits: ['update:modelValue'],
  template: '<label><span>{{ label }}</span><input v-bind="$attrs" :aria-label="label" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" /></label>',
})

export const vuetifyStubs = {
  VAlert: VAlertStub,
  VBtn: VBtnStub,
  VChip: passthrough('VChip'),
  VCol: passthrough('VCol'),
  VDivider: passthrough('VDivider'),
  VForm: passthrough('VForm', '<form v-bind="$attrs"><slot /></form>'),
  VImg: passthrough('VImg'),
  VProgressLinear: passthrough('VProgressLinear'),
  VRow: passthrough('VRow'),
  VSelect: VSelectStub,
  VSpacer: passthrough('VSpacer'),
  VSwitch: passthrough('VSwitch'),
  VTextField: VTextFieldStub,
  VTextarea: VTextFieldStub,
  VToolbar: passthrough('VToolbar'),
}
