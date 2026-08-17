import { spawn } from "node:child_process"
import { fileURLToPath } from "node:url"

const root = fileURLToPath(new URL("..", import.meta.url))
const vite = fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url))

const children = [
  spawn(process.execPath, ["mock/server.mjs"], { cwd: root, stdio: "inherit" }),
  spawn(process.execPath, [vite], { cwd: root, stdio: "inherit" }),
]

let closing = false
function shutdown(code = 0) {
  if (closing) return
  closing = true
  for (const child of children) child.kill("SIGTERM")
  process.exitCode = code
}

for (const child of children) {
  child.on("exit", (code, signal) => {
    if (!closing && (code || signal)) shutdown(code ?? 1)
  })
}

process.on("SIGINT", () => shutdown())
process.on("SIGTERM", () => shutdown())
