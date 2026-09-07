import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import federation from '@originjs/vite-plugin-federation'

export default defineConfig({
  plugins: [
    vue(),
    federation({
      name: 'XhsMovieAssistant',
      filename: 'remoteEntry.js',
      exposes: {
        './Config': './src/components/Config.vue',
        './Page': './src/components/Page.vue',
      },
      shared: {
        vue: { requiredVersion: false, generate: false },
      },
      format: 'esm',
    }),
  ],
  build: {
    target: 'esnext',
    minify: false,
    cssCodeSplit: true,
    rollupOptions: {
      input: './src/main.js',
    },
  },
  css: {
    postcss: {
      plugins: [
        {
          postcssPlugin: 'internal:charset-removal',
          AtRule: {
            charset: atRule => atRule.remove(),
          },
        },
        {
          postcssPlugin: 'vuetify-filter',
          Root(root) {
            root.walkRules(rule => {
              if (
                rule.selector
                && !rule.selector.includes('.xhs-movie-')
                && (rule.selector.includes('.v-') || rule.selector.includes('.mdi-'))
              ) {
                rule.remove()
              }
            })
          },
        },
      ],
    },
  },
  test: {
    css: true,
    environment: 'jsdom',
  },
})
