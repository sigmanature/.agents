import type { Plugin } from "@opencode-ai/plugin"
import { readFileSync } from "fs"

const C_EXT = new Set([".c", ".h", ".cpp", ".hpp"])

export const BraceCheckPlugin: Plugin = async (_ctx) => {
  return {
    "file.edited": async (input) => {
      const file: string = (input as any).file ?? (input as any).path ?? ""
      if (!file || !C_EXT.has(file.slice(file.lastIndexOf(".")))) return

      let text: string
      try { text = readFileSync(file, "utf-8") } catch { return }

      let depth = 0
      const lines = text.split("\n")
      for (let i = 0; i < lines.length; i++) {
        for (const ch of lines[i]) {
          if (ch === "{") depth++
          else if (ch === "}") depth--
        }
        if (depth < 0) {
          console.warn(`\x1b[33m[BRACE] Extra } at ${file}:${i + 1}\x1b[0m`)
          return
        }
      }
      if (depth > 0) {
        console.warn(`\x1b[33m[BRACE] Unclosed { at ${file} (depth=${depth})\x1b[0m`)
      }
    }
  }
}
