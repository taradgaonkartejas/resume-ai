import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
  },
  {
    // src/components/ui/** is vendored from the shadcn registry, not authored
    // here. Those files deliberately export a cva `*Variants` object beside
    // the component, which trips react-refresh/only-export-components. We do
    // not hand-edit them, so re-exporting the variants into separate files
    // would be undone by the next `npx shadcn add`. Scoping the rule off for
    // this directory is the honest fix; every file we DO author still has it.
    files: ['src/components/ui/**/*.{ts,tsx}'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
])
