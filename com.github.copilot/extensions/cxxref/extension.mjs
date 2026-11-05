// cxxref の Copilot CLI 拡張。モデルは呼ばない(スクリプトを裏で動かし、結果をセッションに出すだけ)。
//
// /cxxref-index [ルート]  作業中のリポジトリの索引を作る。ltidx.exe が無ければ、先に scripts\get-ltidx.ps1 で取ってくる。
//                         索引がすでにあれば、変わった TU だけを作り直す(-Update)。裏で動かし、終わったらそう出す。
//                         ルートを省くと、作業フォルダから親へさかのぼって .index のあるフォルダ、無ければ作業フォルダ。
//
// 拡張は Copilot CLI の実験的機能で、機能フラグ EXTENSIONS が有効なときだけ読み込まれる。
import { joinSession } from "@github/copilot-sdk/extension";
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, openSync, closeSync, statSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// プラグインのルート(plugin.json のあるフォルダ)。拡張は com.github.copilot\extensions\cxxref\ にある
function pluginRoot() {
  let d = dirname(fileURLToPath(import.meta.url));
  for (let i = 0; i < 6; i++) {
    if (existsSync(join(d, "plugin.json")) && existsSync(join(d, "scripts", "index-project.ps1"))) return d;
    d = dirname(d);
  }
  throw new Error("cxxref のプラグインのフォルダが見つかりません");
}

// MCP サーバー(mcp\autoupdate.py の find_ltidx)と同じ順に探す
function findLtidx(root) {
  if (process.env.LTIDX) return existsSync(process.env.LTIDX) ? process.env.LTIDX : null;
  for (const p of [join(root, "indexer", "build", "ltidx.exe"), join(process.env.LOCALAPPDATA ?? "", "cxxref", "ltidx", "ltidx.exe")]) {
    if (existsSync(p)) return p;
  }
  return null;
}

function lastCwd(events) {
  let cwd = "";
  for (const e of events) {
    const c = e.data?.context?.cwd ?? (e.type === "session.context_changed" ? e.data?.cwd : undefined);
    if (typeof c === "string" && c) cwd = c;
  }
  return cwd;
}

// 作業フォルダから親へさかのぼって、索引(.index)のあるフォルダ。無ければ作業フォルダ(ltq と同じ探し方)
function indexRoot(start) {
  for (let d = resolve(start); ; d = dirname(d)) {
    if (existsSync(join(d, ".index"))) return d;
    if (dirname(d) === d) return resolve(start);
  }
}

function run(script, args, log) {
  return new Promise((done) => {
    const fd = openSync(log, "a");
    const p = spawn("pwsh", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, ...args], {
      stdio: ["ignore", fd, fd],
      windowsHide: true,
    });
    p.on("error", (err) => { closeSync(fd); done({ code: -1, error: err }); });
    p.on("exit", (code) => { closeSync(fd); done({ code }); });
  });
}

let running = null; // 走っている索引づくり(同時に 1 つだけ)

const tell = (message, level) => session.log(message, { ephemeral: true, ...(level ? { level } : {}) });

async function indexCommand(ctx) {
  if (running) {
    await tell(`cxxref: 索引を作っている途中です(${running})。終わるまで待ってください`, "warning");
    return;
  }
  let plugin;
  try {
    plugin = pluginRoot();
  } catch (err) {
    await tell(`cxxref: ${err.message}`, "error");
    return;
  }
  const arg = (ctx.args ?? "").trim().replace(/^"(.*)"$/, "$1");
  const cwd = lastCwd(await session.getEvents()) || process.cwd();
  const root = arg ? (isAbsolute(arg) ? arg : resolve(cwd, arg)) : indexRoot(cwd);
  if (!existsSync(root) || !statSync(root).isDirectory()) {
    await tell(`cxxref: フォルダがありません: ${root}`, "error");
    return;
  }
  const dir = join(root, ".index");
  mkdirSync(dir, { recursive: true });
  const log = join(dir, "cxxref-index.log");
  running = root;
  const t0 = Date.now();
  try {
    if (!findLtidx(plugin)) {
      await tell("cxxref: ltidx.exe が無いので、先にリリースから取ってきます(scripts\\get-ltidx.ps1)");
      const r = await run(join(plugin, "scripts", "get-ltidx.ps1"), [], log);
      if (r.code !== 0 || !findLtidx(plugin)) {
        await tell(`cxxref: ltidx を取ってこられませんでした。ログ: ${log}`, "error");
        return;
      }
    }
    const update = existsSync(join(dir, "index.tsv.deps.tsv"));
    await tell(`cxxref: 索引を${update ? "作り直し" : "作り"}始めました(${root})。裏で動かすので、作業を続けてかまいません。ログ: ${log}`);
    const r = await run(join(plugin, "scripts", "index-project.ps1"), ["-Root", root, ...(update ? ["-Update"] : [])], log);
    const sec = Math.round((Date.now() - t0) / 1000);
    if (r.code === 0) {
      await tell(`cxxref: 索引ができました(${sec} 秒、${dir})。以後は MCP サーバーの refs / check がこの索引を使い、コードを変えても自動で作り直します`);
    } else {
      await tell(`cxxref: 索引づくりに失敗しました(終了コード ${r.code ?? r.error?.message}、${sec} 秒)。ログ: ${log}`, "error");
    }
  } finally {
    running = null;
  }
}

const session = await joinSession({
  commands: [
    {
      // 拡張のコマンドには引数のヒントを付けられないので、説明文の先頭に引数を書く
      name: "cxxref-index",
      description: "[ルート] — C++ の参照索引(cxxref)を作る・作り直す。裏で動かす(モデルは呼ばない)",
      handler: (ctx) => {
        indexCommand(ctx).catch((err) => tell(`cxxref: ${err?.message ?? err}`, "error"));
      },
    },
  ],
});
