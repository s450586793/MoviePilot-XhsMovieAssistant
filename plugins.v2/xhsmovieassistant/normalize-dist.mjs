import { readdir, readFile, writeFile } from 'node:fs/promises'
import { extname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const assetDirectory = fileURLToPath(new URL('./dist/assets/', import.meta.url))
const textExtensions = new Set(['.css', '.html', '.js', '.map'])
const entries = await readdir(assetDirectory, { withFileTypes: true })

await Promise.all(entries.map(async (entry) => {
  if (!entry.isFile() || !textExtensions.has(extname(entry.name))) return

  const path = join(assetDirectory, entry.name)
  const content = await readFile(path, 'utf8')
  const normalized = content.replace(/[ \t]+(?=\r?$)/gm, '')
  if (normalized !== content) await writeFile(path, normalized, 'utf8')
}))
