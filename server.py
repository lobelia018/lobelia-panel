#!/usr/bin/env python3
"""Panel local para gang.fun: servidor que ejecuta el CLI `gang` en segundo plano.
Uso:  python server.py   ->   abre http://127.0.0.1:8788
Solo usa la libreria estandar. Escucha unicamente en tu PC (127.0.0.1)."""
import base64, json, os, re, secrets, shutil, subprocess, threading, time, urllib.request, uuid, webbrowser
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

VERSION = "2.6"
HOST, PORT = "127.0.0.1", 8788
HOME = str(Path.home())


def _npm_bin(name):
    """En Windows usa el .cmd de npm; si no, puede ejecutarse por error un baw.js suelto de la carpeta actual."""
    p = Path(os.environ.get("APPDATA", "")) / "npm" / (name + ".cmd")
    return str(p) if p.exists() else (shutil.which(name + ".cmd") or shutil.which(name) or name)


HERE = Path(__file__).parent

# ---------- compatibilidad Windows con `baw` (sin tocar los archivos de gang, sobrevive a cualquier actualizacion) ----------
# El CLI de gang ejecuta `baw` con execFileSync('baw') SIN shell: en Windows baw es un baw.cmd y Node no lo encuentra (ENOENT),
# de ahi el error «baw ... is not installed or not on PATH». Este precargador (NODE_OPTIONS=--require) corrige esa llamada
# en memoria para cualquier version de gang, asi que ya no hay que reemplazar baw.js despues de cada actualizacion.
BAW_SHIM_JS = r"""'use strict'
// LobeliaPanel: hace que gang pueda ejecutar `baw` en Windows (baw.cmd). No hace nada en otros sistemas.
if (process.platform === 'win32') {
  const cp = require('node:child_process'), fs = require('node:fs'), path = require('node:path')
  let cached
  function resolveBaw() {
    if (cached !== undefined) return cached
    cached = null
    const dirs = (process.env.PATH || process.env.Path || '').split(';').filter(Boolean)
    if (process.env.APPDATA) dirs.push(path.join(process.env.APPDATA, 'npm'))
    for (const d of dirs) {
      if (!fs.existsSync(path.join(d, 'baw.cmd'))) continue
      for (const root of [path.join(d, 'node_modules'), path.join(d, '..', 'node_modules')]) {
        try {
          const pj = path.join(root, '@binance', 'agentic-wallet', 'package.json')
          const meta = JSON.parse(fs.readFileSync(pj, 'utf8'))
          const rel = typeof meta.bin === 'string' ? meta.bin : (meta.bin && (meta.bin.baw || Object.values(meta.bin)[0]))
          const js = rel && path.join(path.dirname(pj), rel)
          if (js && /\.(c|m)?js$/i.test(js) && fs.existsSync(js)) { cached = { js }; return cached }
        } catch (e) {}
      }
      cached = { cmd: path.join(d, 'baw.cmd') }; return cached
    }
    return cached
  }
  const q = (a) => /[\s"&|<>^%()]/.test(a) || a === '' ? '"' + String(a).replace(/"/g, '\\"') + '"' : String(a)
  function fix(file, args, opts) {
    if (typeof file !== 'string' || !/^baw(\.cmd)?$/i.test(file) || !Array.isArray(args)) return null
    const r = resolveBaw()
    if (!r) return null
    if (r.js) return [process.execPath, [r.js, ...args], opts]
    return [process.env.ComSpec || 'cmd.exe', ['/d', '/s', '/c', '"' + [r.cmd, ...args].map(q).join(' ') + '"'], { ...(opts || {}), windowsVerbatimArguments: true }]
  }
  for (const name of ['execFileSync', 'spawnSync', 'spawn', 'execFile']) {
    const orig = cp[name]
    cp[name] = function (file, args, ...rest) {
      let opts, tail = rest
      if (args && !Array.isArray(args)) { opts = args; args = undefined } else if (rest.length && rest[0] && typeof rest[0] === 'object') { opts = rest[0]; tail = rest.slice(1) }
      const f = fix(file, args, opts)
      if (!f) return orig.apply(this, arguments)
      return orig.call(this, f[0], f[1], f[2], ...tail)
    }
  }
}
"""
BAW_SHIM_FILE = HERE / "baw-shim.cjs"


def _node_env():
    """Entorno de los CLI: igual que el tuyo + el precargador que arregla `baw` en Windows."""
    env = os.environ.copy()
    if os.name == "nt":
        try:
            if not BAW_SHIM_FILE.exists() or BAW_SHIM_FILE.read_text(encoding="utf-8") != BAW_SHIM_JS:
                BAW_SHIM_FILE.write_text(BAW_SHIM_JS, encoding="utf-8")
            req = '--require "' + str(BAW_SHIM_FILE).replace("\\", "/") + '"'
            if req not in env.get("NODE_OPTIONS", ""):
                env["NODE_OPTIONS"] = (env.get("NODE_OPTIONS", "") + " " + req).strip()
        except Exception:
            pass
    return env


def _sp(fn, *a, **k):
    k.setdefault("cwd", HOME)   # los CLI se ejecutan desde tu carpeta de usuario, no desde la del servidor
    k.setdefault("env", _node_env())
    return fn(*a, **k)
SESSION = secrets.token_urlsafe(24)                      # evita que otras webs llamen a tu API
GANG = _npm_bin("gang")
ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
NUM = re.compile(r"^\d{1,12}(\.\d{1,18})?$")
HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
BAW = _npm_bin("baw")
JOBS, EVENTS, BUSY = {}, {}, threading.Lock()
CREATE_REQUESTS = {}
PF_CACHE = {"r": None}
HERE = Path(__file__).parent


NATIVE = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"   # BNB nativo para baw
ZERO = "0x" + "0" * 40
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
USDT = "0x55d398326f99059ff775485246999027b3197955"
USDC = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"
STABLES = (USDT, USDC)                                    # baw (Ondo) solo cotiza pares donde un lado es stablecoin
NOT_A_TARGET = (NATIVE.lower(), ZERO, WBNB)               # convertir BNB -> BNB no tiene sentido
OUT_KEYS = ("toTokenQty", "toCoinAmount", "toTokenAmount", "toAmount", "estimatedToQty", "expectedToQty",
            "receiveQty", "toTokenActualQty", "amountOut", "outAmount")
SYS, SYS_LOCK, SEQ = [], threading.Lock(), [0]            # terminal de fondo (ultimas 1000 lineas)
PRICE = {"t": 0.0, "usd": 0.0, "src": ""}
LANG = ["es"]                                             # idioma de los mensajes del servidor (lo fija la pagina)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SIGN = {"busy": False}
IMG_TYPES = ((".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".webp", "image/webp"), (".gif", "image/gif"), (".svg", "image/svg+xml"))
TR = sorted([
    ("sin salida", "no output"), ("respuesta no JSON: ", "non-JSON response: "), ("Buscando tokens", "Looking for tokens"),
    ("tokens unicos entre populares, nuevos y cerca de graduar", "unique tokens across popular, new and near-graduation"),
    ("no se pudo leer el mercado", "could not read the market"), ("orden invalido", "invalid order"),
    ("Orden creada", "Order created"), ("Si la app de Binance pide aprobar, hazlo (espero hasta 3 min)", "If the Binance app asks you to approve, do it (waiting up to 3 min)"),
    ("Convertido", "Converted"), (": recibiste ", ": you received "), ("Listo", "Done"), ("la orden termino en estado ", "the order ended with status "),
    ("No pude verificarla a tiempo: revisa la app de Binance y tu saldo", "Could not verify it in time: check the Binance app and your balance"),
    ("baw no devolvio orderId: ", "baw returned no orderId: "), ("Confirmada en la blockchain", "Confirmed on the blockchain"),
    ("la transaccion fallo: ", "the transaction failed: "), ("no se pudo confirmar a tiempo: ", "could not confirm in time: "),
    ("Paso: ", "Step: "), ("Binance marco un riesgo. Esperando tu decision (2 min)", "Binance flagged a risk. Waiting for your decision (2 min)"),
    ("operacion cancelada (por ti o por falta de respuesta)", "operation cancelled (by you, or no response)"),
    ("Continuas bajo tu decision: aproba la operacion en la app de Binance", "Continuing on your decision: approve the operation in the Binance app"),
    ("el CLI no devolvio requestId", "the CLI returned no requestId"), ("Enviada: ", "Sent: "),
    ("Aproba la operacion en la app de Binance (espero hasta 2 min)", "Approve the operation in the Binance app (waiting up to 2 min)"),
    ("Confirmada", "Confirmed"), ("No pude verificarla: revisa la app de Binance y tu saldo", "Could not verify it: check the Binance app and your balance"),
    ("no se pudo confirmar este paso; vuelve a intentarlo", "could not confirm this step; try again"),
    ("demasiados pasos seguidos, detenido por seguridad", "too many consecutive steps, stopped for safety"),
    ("host no permitido", "host not allowed"), ("sesion invalida, recarga la pagina", "invalid session, reload the page"),
    ("direccion invalida", "invalid address"), ("ya no existe", "no longer exists"), ("json invalido", "invalid json"), ("orden desconocida", "unknown order"),
    ("monto invalido", "invalid amount"), ("ese destino es BNB: no hace falta convertir", "that target is BNB: no conversion needed"),
    ("ya hay una operacion en curso, espera a que termine", "an operation is already running, wait for it to finish"),
    ("Conversion de ", "Conversion of "), ("Conversion BNB -> ", "BNB -> "), (" iniciada", " started"),
    ("Orden de compra iniciada", "Buy order started"), ("Orden de venta iniciada", "Sell order started"),
    ("Filtros cambiados: ", "Filters changed: "), ("Escaneo: ", "Scan: "), ("candidatos de ", "candidates of "), ("candidato de ", "candidate of "),
    (" revisados", " checked"), (" · NUEVOS: ", " · NEW: "), ("no hay una ruta", "no route"), ("no existe", "not found"),
    ("sin cantidad intermedia de USDT: ", "no intermediate USDT amount: "),
    ("no se pudo leer los USDT recibidos; el segundo paso no se ejecuto, revisa tu saldo de USDT", "could not read the USDT received; step 2 was not run, check your USDT balance"),
    ("Escáner detenido", "Scanner stopped"),
    ("Límite de la billetera: el monto supera 5 USD; prueba con una cantidad menor.", "Wallet limit: the amount is over 5 USD; try a smaller amount."),
    ("el CLI rechazó la nota (los tokens de GangLaunchpad / V3 no admiten notas): borra la nota y vuelve a intentarlo · ", "the CLI rejected the note (GangLaunchpad / V3 tokens do not support notes): clear the note and try again · "),
    ("tu CLI de gang ignoraría la nota (está desactualizado): ejecuta npm i -g @gangfun/cli@latest; no se envió nada", "your gang CLI would ignore the note (it is outdated): run npm i -g @gangfun/cli@latest; nothing was sent"),
    ("Nota adjunta: ", "Note attached: "), (" · con nota (", " · with note ("), (" car.)", " chars)"),
    ("El CLI de gang está desactualizado (", "The gang CLI is outdated ("), ("): ejecuta «", "): run «"),
    ("» para ver todos los tokens de gang.fun", "» to see every gang.fun token"),
    ("La lista de ", "The list of "), (" no respondió (", " did not respond ("), ("sin datos", "no data"),
    ("; se muestra la última versión conocida", "; showing the last known version"),
    ("populares", "popular"), ("nuevos", "new"), ("cerca de graduar", "near graduation"),
    ('no se pudo leer la blockchain (BSC)', 'could not read the blockchain (BSC)'),
    ('no se pudo leer la direccion de tu billetera (usa «Iniciar / verificar sesión»)', 'could not read your wallet address (use “Sign in / verify session”)'),
    ('sin respuesta', 'no response'),
    ('respuesta vacía', 'empty response'),
    ('Nombre y símbolo son obligatorios.', 'Name and symbol are required.'),
    ('Tipo de lanzamiento no válido.', 'Invalid launch type.'),
    ('Logo PNG/JPG/WEBP requerido.', 'A PNG/JPG/WEBP logo is required.'),
    ('Imagen inválida.', 'Invalid image.'),
    ('El logo debe pesar 512 KB o menos.', 'The logo must be 512 KB or smaller.'),
    ('Impuestos: 0–5%; dividendos: 0–100%.', 'Taxes: 0–5%; dividends: 0–100%.'),
    ('Para impuestos, configura compra y venta mayores que cero.', 'For taxes, set both buy and sell above zero.'),
    ('El sitio debe comenzar con http:// o https://.', 'The website must start with http:// or https://.'),
    ('Se requieren contratos válidos para moneda par y pool.', 'Valid contracts are required for the pair token and the pool.'),
    ('Ya hay otra operación en curso.', 'Another operation is already running.'),
    ('El CLI no devolvió requestId; no se ejecutó nada.', 'The CLI returned no requestId; nothing was run.'),
    ('Vista previa vencida; vuelve a prepararla.', 'Preview expired; prepare it again.'),
], key=lambda p: -len(p[0]))
TR_ZH = [
    ('no se pudo leer la blockchain (BSC)', '无法读取区块链 (BSC)'),
    ('no se pudo leer la direccion de tu billetera (usa «Iniciar / verificar sesión»)', '无法读取你的钱包地址（请使用“登录 / 验证会话”）'),
    ('sin respuesta', '无响应'),
    ('respuesta vacía', '响应为空'),
    ('Nombre y símbolo son obligatorios.', '名称和符号为必填项。'),
    ('Tipo de lanzamiento no válido.', '发行类型无效。'),
    ('Logo PNG/JPG/WEBP requerido.', '需要 PNG/JPG/WEBP 徽标。'),
    ('Imagen inválida.', '图片无效。'),
    ('El logo debe pesar 512 KB o menos.', '徽标不得超过 512 KB。'),
    ('Impuestos: 0–5%; dividendos: 0–100%.', '税率：0–5%；分红：0–100%。'),
    ('Para impuestos, configura compra y venta mayores que cero.', '设置税率时，买入和卖出税都必须大于零。'),
    ('El sitio debe comenzar con http:// o https://.', '网站必须以 http:// 或 https:// 开头。'),
    ('Se requieren contratos válidos para moneda par y pool.', '交易对代币和资金池都需要有效的合约地址。'),
    ('Ya hay otra operación en curso.', '已有另一项操作正在进行。'),
    ('El CLI no devolvió requestId; no se ejecutó nada.', 'CLI 未返回 requestId；未执行任何操作。'),
    ('Vista previa vencida; vuelve a prepararla.', '预览已过期；请重新准备。'),
    ('el CLI rechazó la nota (los tokens de GangLaunchpad / V3 no admiten notas): borra la nota y vuelve a intentarlo · ', 'CLI 拒绝了备注（GangLaunchpad / V3 代币不支持备注）：请清除备注后重试 · '),
    ('tu CLI de gang ignoraría la nota (está desactualizado): ejecuta npm i -g @gangfun/cli@latest; no se envió nada', '你的 gang CLI 会忽略备注（版本过旧）：请运行 npm i -g @gangfun/cli@latest；未发送任何内容'),
    ('El CLI de gang está desactualizado (', 'gang CLI 已过期 ('),
    ('» para ver todos los tokens de gang.fun', '» 以查看 gang.fun 的所有代币'),
    ('; se muestra la última versión conocida', '；显示最近一次已知数据'),
    ('): ejecuta «', '): 请运行 «'),
    ('La lista de ', '列表 '),
    (' no respondió (', ' 无响应 ('),
    ('Nota adjunta: ', '已附加备注：'),
    (' · con nota (', ' · 含备注 ('),
    (' car.)', ' 字符)'),
    ('no se pudo leer los USDT recibidos; el segundo paso no se ejecuto, revisa tu saldo de USDT', '无法读取收到的 USDT；第 2 步未执行，请检查你的 USDT 余额'),
    ('Ruta en 2 pasos: BNB → USDT → token (la billetera solo opera pares con stablecoin)', '分两步兑换：BNB → USDT → 代币（钱包只支持带稳定币的交易对）'),
    ('Límite de la billetera: el monto supera 5 USD; prueba con una cantidad menor.', '钱包限制：金额超过 5 USD；请尝试更小的金额。'),
    ('Sesión cerrada. Usa «Iniciar / verificar sesión» para entrar de nuevo.', '已退出登录。请使用“登录 / 验证会话”重新登录。'),
    ('Continuas bajo tu decision: aproba la operacion en la app de Binance', '按你的决定继续：请在 Binance 应用中批准该操作'),
    ('No pude verificarla a tiempo: revisa la app de Binance y tu saldo', '未能及时验证：请检查 Binance 应用和你的余额'),
    ('Si la app de Binance pide aprobar, hazlo (espero hasta 3 min)', '如果 Binance 应用要求批准，请批准（最多等待 3 分钟）'),
    ('Aproba la operacion en la app de Binance (espero hasta 2 min)', '请在 Binance 应用中批准该操作（最多等待 2 分钟）'),
    (' (si hace falta, confirma el código en la app de Binance)', '（如有需要，请在 Binance 应用中确认验证码）'),
    ('tokens unicos entre populares, nuevos y cerca de graduar', '个不重复的代币（热门、最新和即将毕业）'),
    ('No pude verificarla: revisa la app de Binance y tu saldo', '无法验证：请检查 Binance 应用和你的余额'),
    ('Binance marco un riesgo. Esperando tu decision (2 min)', 'Binance 标记了风险。等待你的决定（2 分钟）'),
    ('operacion cancelada (por ti o por falta de respuesta)', '操作已取消（由你取消或无响应）'),
    ('no se pudo confirmar este paso; vuelve a intentarlo', '无法确认此步骤；请重试'),
    ('ya hay una operacion en curso, espera a que termine', '已有操作正在进行，请等待其完成'),
    ('demasiados pasos seguidos, detenido por seguridad', '连续步骤过多，出于安全已停止'),
    ('ese destino es BNB: no hace falta convertir', '目标是 BNB：无需兑换'),
    ('Sesión verificada: todo en orden ✓ ', '会话已验证：一切正常 ✓ '),
    ('ya hay un inicio de sesión en curso', '已有登录正在进行'),
    ('sesion invalida, recarga la pagina', '会话无效，请刷新页面'),
    ('sin cantidad intermedia de USDT: ', '没有中间 USDT 数量：'),
    ('Hay cosas por corregir (arriba).', '有需要修正的问题（见上方）。'),
    ('no se pudo confirmar a tiempo: ', '未能及时确认：'),
    ('baw terminó con error (código ', 'baw 以错误结束 (代码 '),
    ('el CLI no devolvio requestId', 'CLI 未返回 requestId'),
    ('la orden termino en estado ', '订单结束，状态为 '),
    ('Confirmada en la blockchain', '已在区块链上确认'),
    ('no se pudo leer el mercado', '无法读取市场数据'),
    ('baw no devolvio orderId: ', 'baw 未返回 orderId：'),
    ('Orden de compra iniciada', '买入订单已开始'),
    ('Orden de venta iniciada', '卖出订单已开始'),
    ('la transaccion fallo: ', '交易失败：'),
    ('respuesta no JSON: ', '非 JSON 响应：'),
    ('Filtros cambiados: ', '筛选条件已更改：'),
    ('direccion invalida', '无效的地址'),
    ('Conversion BNB -> ', 'BNB -> '),
    ('host no permitido', '不允许的主机'),
    ('orden desconocida', '未知订单'),
    ('Escáner detenido', '扫描器已停止'),
    ('Buscando tokens', '正在查找代币'),
    ('no hay una ruta', '没有可用路径'),
    ('orden invalido', '无效的排序'),
    ('monto invalido', '无效的金额'),
    ('Conversion de ', '兑换 '),
    ('candidatos de ', '个候选，共 '),
    ('json invalido', '无效的 JSON'),
    ('candidato de ', '个候选，共 '),
    ('Orden creada', '订单已创建'),
    (': recibiste ', '：你收到了 '),
    ('ya no existe', '已不存在'),
    ('Ejecutando: ', '正在执行：'),
    (' · NUEVOS: ', ' · 新增：'),
    ('sin salida', '无输出'),
    ('Convertido', '已兑换'),
    ('Confirmada', '已确认'),
    (' revisados', ' 个已检查'),
    ('Enviada: ', '已发送：'),
    (' iniciada', ' 已开始'),
    ('Escaneo: ', '扫描：'),
    ('no existe', '不存在'),
    ('Error: ', '错误：'),
    ('Paso: ', '步骤：'),
    ('Listo', '完成'),
]
TR_ZT = [
    ('no se pudo leer la blockchain (BSC)', '無法讀取區塊鏈 (BSC)'),
    ('no se pudo leer la direccion de tu billetera (usa «Iniciar / verificar sesión»)', '無法讀取你的錢包地址（請使用「登入 / 驗證會話」）'),
    ('sin respuesta', '無回應'),
    ('respuesta vacía', '回應為空'),
    ('Nombre y símbolo son obligatorios.', '名稱和符號為必填項。'),
    ('Tipo de lanzamiento no válido.', '發行類型無效。'),
    ('Logo PNG/JPG/WEBP requerido.', '需要 PNG/JPG/WEBP 標誌。'),
    ('Imagen inválida.', '圖片無效。'),
    ('El logo debe pesar 512 KB o menos.', '標誌不得超過 512 KB。'),
    ('Impuestos: 0–5%; dividendos: 0–100%.', '稅率：0–5%；分紅：0–100%。'),
    ('Para impuestos, configura compra y venta mayores que cero.', '設定稅率時，買入和賣出稅都必須大於零。'),
    ('El sitio debe comenzar con http:// o https://.', '網站必須以 http:// 或 https:// 開頭。'),
    ('Se requieren contratos válidos para moneda par y pool.', '交易對代幣和資金池都需要有效的合約地址。'),
    ('Ya hay otra operación en curso.', '已有另一項操作正在進行。'),
    ('El CLI no devolvió requestId; no se ejecutó nada.', 'CLI 未回傳 requestId；未執行任何操作。'),
    ('Vista previa vencida; vuelve a prepararla.', '預覽已過期；請重新準備。'),
    ('el CLI rechazó la nota (los tokens de GangLaunchpad / V3 no admiten notas): borra la nota y vuelve a intentarlo · ', 'CLI 拒絕了備註（GangLaunchpad / V3 代幣不支援備註）：請清除備註後重試 · '),
    ('tu CLI de gang ignoraría la nota (está desactualizado): ejecuta npm i -g @gangfun/cli@latest; no se envió nada', '你的 gang CLI 會忽略備註（版本過舊）：請執行 npm i -g @gangfun/cli@latest；未傳送任何內容'),
    ('El CLI de gang está desactualizado (', 'gang CLI 已過期 ('),
    ('» para ver todos los tokens de gang.fun', '» 以查看 gang.fun 的所有代幣'),
    ('; se muestra la última versión conocida', '；顯示最近一次已知資料'),
    ('): ejecuta «', '): 請執行 «'),
    ('La lista de ', '列表 '),
    (' no respondió (', ' 無回應 ('),
    ('Nota adjunta: ', '已附加備註：'),
    (' · con nota (', ' · 含備註 ('),
    (' car.)', ' 字元)'),
    ('no se pudo leer los USDT recibidos; el segundo paso no se ejecuto, revisa tu saldo de USDT', '無法讀取收到的 USDT；第 2 步未執行，請檢查你的 USDT 餘額'),
    ('Ruta en 2 pasos: BNB → USDT → token (la billetera solo opera pares con stablecoin)', '分兩步兌換：BNB → USDT → 代幣（錢包只支援帶穩定幣的交易對）'),
    ('Límite de la billetera: el monto supera 5 USD; prueba con una cantidad menor.', '錢包限制：金額超過 5 USD；請嘗試更小的金額。'),
    ('Sesión cerrada. Usa «Iniciar / verificar sesión» para entrar de nuevo.', '已退出登入。請使用“登入 / 驗證會話”重新登入。'),
    ('Continuas bajo tu decision: aproba la operacion en la app de Binance', '按你的決定繼續：請在 Binance 應用中批准該操作'),
    ('No pude verificarla a tiempo: revisa la app de Binance y tu saldo', '未能及時驗證：請檢查 Binance 應用和你的餘額'),
    ('Si la app de Binance pide aprobar, hazlo (espero hasta 3 min)', '如果 Binance 應用要求批准，請批准（最多等待 3 分鐘）'),
    ('Aproba la operacion en la app de Binance (espero hasta 2 min)', '請在 Binance 應用中批准該操作（最多等待 2 分鐘）'),
    (' (si hace falta, confirma el código en la app de Binance)', '（如有需要，請在 Binance 應用中確認驗證碼）'),
    ('tokens unicos entre populares, nuevos y cerca de graduar', '個不重複的代幣（熱門、最新和即將畢業）'),
    ('No pude verificarla: revisa la app de Binance y tu saldo', '無法驗證：請檢查 Binance 應用和你的餘額'),
    ('Binance marco un riesgo. Esperando tu decision (2 min)', 'Binance 標記了風險。等待你的決定（2 分鐘）'),
    ('operacion cancelada (por ti o por falta de respuesta)', '操作已取消（由你取消或無響應）'),
    ('no se pudo confirmar este paso; vuelve a intentarlo', '無法確認此步驟；請重試'),
    ('ya hay una operacion en curso, espera a que termine', '已有操作正在進行，請等待其完成'),
    ('demasiados pasos seguidos, detenido por seguridad', '連續步驟過多，出於安全已停止'),
    ('ese destino es BNB: no hace falta convertir', '目標是 BNB：無需兌換'),
    ('Sesión verificada: todo en orden ✓ ', '會話已驗證：一切正常 ✓ '),
    ('ya hay un inicio de sesión en curso', '已有登入正在進行'),
    ('sesion invalida, recarga la pagina', '會話無效，請重新整理頁面'),
    ('sin cantidad intermedia de USDT: ', '沒有中間 USDT 數量：'),
    ('Hay cosas por corregir (arriba).', '有需要修正的問題（見上方）。'),
    ('no se pudo confirmar a tiempo: ', '未能及時確認：'),
    ('baw terminó con error (código ', 'baw 以錯誤結束 (程式碼 '),
    ('el CLI no devolvio requestId', 'CLI 未返回 requestId'),
    ('la orden termino en estado ', '訂單結束，狀態為 '),
    ('Confirmada en la blockchain', '已在區塊鏈上確認'),
    ('no se pudo leer el mercado', '無法讀取市場資料'),
    ('baw no devolvio orderId: ', 'baw 未返回 orderId：'),
    ('Orden de compra iniciada', '買入訂單已開始'),
    ('Orden de venta iniciada', '賣出訂單已開始'),
    ('la transaccion fallo: ', '交易失敗：'),
    ('respuesta no JSON: ', '非 JSON 響應：'),
    ('Filtros cambiados: ', '篩選條件已更改：'),
    ('direccion invalida', '無效的地址'),
    ('Conversion BNB -> ', 'BNB -> '),
    ('host no permitido', '不允許的主機'),
    ('orden desconocida', '未知訂單'),
    ('Escáner detenido', '掃描器已停止'),
    ('Buscando tokens', '正在查詢代幣'),
    ('no hay una ruta', '沒有可用路徑'),
    ('orden invalido', '無效的排序'),
    ('monto invalido', '無效的金額'),
    ('Conversion de ', '兌換 '),
    ('candidatos de ', '個候選，共 '),
    ('json invalido', '無效的 JSON'),
    ('candidato de ', '個候選，共 '),
    ('Orden creada', '訂單已建立'),
    (': recibiste ', '：你收到了 '),
    ('ya no existe', '已不存在'),
    ('Ejecutando: ', '正在執行：'),
    (' · NUEVOS: ', ' · 新增：'),
    ('sin salida', '無輸出'),
    ('Convertido', '已兌換'),
    ('Confirmada', '已確認'),
    (' revisados', ' 個已檢查'),
    ('Enviada: ', '已傳送：'),
    (' iniciada', ' 已開始'),
    ('Escaneo: ', '掃描：'),
    ('no existe', '不存在'),
    ('Error: ', '錯誤：'),
    ('Paso: ', '步驟：'),
    ('Listo', '完成'),
]
TRS = {"en": TR, "zh": TR_ZH, "zt": TR_ZT}


def tr(m):
    m = str(m)
    for a, b in TRS.get(LANG[0], ()):
        m = m.replace(a, b)
    return m


def TE(es, en):
    """Para mensajes con cifras: español en 'es'; en inglés, chino simplificado y tradicional se muestra el inglés
    (así un mensaje nunca sale mitad en un idioma y mitad en otro)."""
    return es if LANG[0] == "es" else en


def err_text(e, n=300):
    """Texto de un error del CLI: si ya es un texto se deja tal cual (antes salia entre comillas por json.dumps)."""
    return (e if isinstance(e, str) else json.dumps(e, ensure_ascii=False))[:n]


def T(es, en):
    if LANG[0] == "en":
        return en
    return tr(es) if LANG[0] in ("zh", "zt") else es


def slog(tag, msg, lvl="info"):
    """Todo lo que hace el servidor en segundo plano queda aqui; la pagina lo muestra en su terminal."""
    with SYS_LOCK:
        SEQ[0] += 1
        SYS.append({"n": SEQ[0], "t": time.strftime("%H:%M:%S"), "tag": tag, "lvl": lvl, "m": tr(msg)[:400]})
        del SYS[:-1000]


def summ(r):
    if not isinstance(r, dict):
        return type(r).__name__
    if r.get("success") is False:
        return "error: " + json.dumps(r.get("error", r), ensure_ascii=False)[:200]
    for k in ("tokens", "items", "orders", "list"):
        if isinstance(r.get(k), list):
            return f"{len(r[k])} {k}"
    return "ok"


def _cli(tag, exe, args, timeout, extra=(), quiet=False):
    t0, out = time.time(), ""
    if not quiet:
        slog(tag, f"$ {tag} " + " ".join(map(str, args)))
    try:
        p = _sp(subprocess.run, [exe, *map(str, args), *extra], capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        out = p.stdout.strip()
        res = json.loads(out) if out else {"success": False, "error": p.stderr.strip()[:300] or "sin salida"}
    except json.JSONDecodeError:
        res = {"success": False, "error": "respuesta no JSON: " + out[:200]}
    except Exception as e:
        res = {"success": False, "error": str(e)}
    bad = isinstance(res, dict) and res.get("success") is False
    if bad or not quiet:
        slog(tag, f"  <- {args[0] if args else ''} · {time.time() - t0:.1f}s · {summ(res)}", "err" if bad else "info")
    return res


WL = threading.Lock()
WALLET_CMDS = {"preflight", "token", "buy", "sell", "execute", "quote", "tx", "info", "creator-fee"}


CLI_UPDATE = {"info": None}


def _watch_update(r):
    """El CLI de gang añade `update: {current, latest, cmd, skill}` cuando esta desactualizado. Un CLI viejo no ve
    los tokens nuevos de gang.fun (p. ej. los de GangLaunchpad / V3), asi que se avisa en la pagina."""
    u = r.get("update") if isinstance(r, dict) else None
    if isinstance(u, dict) and (u.get("latest") or u.get("cmd")):
        if CLI_UPDATE["info"] != u:
            slog("gang", f"El CLI de gang está desactualizado ({u.get('current', '?')} → {u.get('latest', '?')}): ejecuta «{u.get('cmd') or 'npm i -g @gangfun/cli@latest'}» para ver todos los tokens de gang.fun", "err")
        CLI_UPDATE["info"] = {k: str(u.get(k, ""))[:200] for k in ("current", "latest", "cmd")}
    return r


def gang(*args, timeout=120, quiet=False):
    """Los comandos que usan la billetera (baw) se ejecutan de uno en uno y se reintentan una vez si baw falla."""
    if args and args[0] in WALLET_CMDS:
        with WL:
            r = _cli("gang", GANG, args, timeout, quiet=quiet)
            if isinstance(r, dict) and r.get("success") is False and "baw" in str(r.get("error", "")).lower():
                time.sleep(2)
                r = _cli("gang", GANG, args, timeout, quiet=quiet)
            return _watch_update(r)
    return _watch_update(_cli("gang", GANG, args, timeout, quiet=quiet))


def baw(*args, timeout=90):
    return _cli("baw", BAW, args, timeout, ("--json",))


def find(o, names):
    """Busca a cualquier profundidad la primera clave cuyo nombre este en `names` (por orden de prioridad)."""
    for n in (x.lower() for x in names):
        todo = [o]
        while todo:
            x = todo.pop(0)
            if isinstance(x, dict):
                for k, v in x.items():
                    if str(k).lower() == n and v not in (None, "", [], {}):
                        return v
                todo += [v for v in x.values() if isinstance(v, (dict, list))]
            elif isinstance(x, list):
                todo += [v for v in x if isinstance(v, (dict, list))]
    return None


def bnb_usd():
    """Precio de BNB en USD (cache 30 s): API publica de Binance; si falla, cotizacion BNB->USDT de baw."""
    if not (PRICE["usd"] and time.time() - PRICE["t"] < 30):
        usd, src = 0.0, ""
        try:
            with urllib.request.urlopen("https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT", timeout=4) as r:
                usd, src = float(json.loads(r.read())["price"]), "Binance"
        except Exception:
            q = baw("market-order", "quote", "--fromTokenQty", "1", "--fromToken", NATIVE, "--toToken", USDT, "--binanceChainId", "56")
            try:
                usd, src = float(str(find(q, OUT_KEYS)).replace(",", "")), "baw"
            except Exception:
                pass
        if usd:
            PRICE.update(t=time.time(), usd=usd, src=src)
    return {"success": bool(PRICE["usd"]), "usd": PRICE["usd"], "src": PRICE["src"]}


def quote_of(r):
    """Moneda par de un token segun `gang token`; prueba varios nombres de campo."""
    t = r["token"] if isinstance(r, dict) and isinstance(r.get("token"), dict) else r
    v = find(t, ["quote", "quoteToken", "quoteSymbol", "quoteAddress", "quoteAsset", "pairToken"])
    if isinstance(v, dict):
        v = find(v, ["symbol", "address"])
    return str(v or "")


def _qty(r):
    out = find(r, OUT_KEYS)
    return None if out is None else str(out).replace(",", "")


def _bq(frm, to, amount):
    return baw("market-order", "quote", "--fromTokenQty", amount, "--fromToken", frm, "--toToken", to, "--binanceChainId", "56")


def _need_stable(r):
    """True si baw rechazo el par por no tener una stablecoin en un lado (codigo 103)."""
    return isinstance(r, dict) and r.get("success") is False and re.search(r"stablecoin|\b103\b|Unsupported token pair", json.dumps(r, ensure_ascii=False), re.I) is not None


def route_quote(frm, to, amount):
    """frm -> to. Directo si baw lo acepta; si no (par sin stablecoin), frm -> USDT -> to (2 saltos)."""
    r = _bq(frm, to, amount)
    if not (isinstance(r, dict) and r.get("success") is False):
        return {"success": True, "out": _qty(r), "raw": r, "route": "direct"}
    if frm.lower() in STABLES or to.lower() in STABLES or not _need_stable(r):
        return r
    r1 = _bq(frm, USDT, amount)                             # salto 1: frm -> USDT
    if isinstance(r1, dict) and r1.get("success") is False:
        return r1
    mid = _qty(r1)
    if not mid:
        return {"success": False, "error": "sin cantidad intermedia de USDT: " + json.dumps(r1, ensure_ascii=False)[:200]}
    r2 = _bq(USDT, to, mid)                                 # salto 2: USDT -> to
    if isinstance(r2, dict) and r2.get("success") is False:
        return r2
    return {"success": True, "out": _qty(r2), "raw": r2, "route": "via-usdt", "mid": mid}


def swap_quote(token, amount, out=False):
    """out=False: BNB -> token.  out=True: token -> BNB."""
    return route_quote(token, NATIVE, amount) if out else route_quote(NATIVE, token, amount)


def swap_wait(frm, to, qty, log, tag):
    """Ejecuta un `baw market-order swap` y espera su estado final. Devuelve la cantidad recibida (str) o None si no se pudo verificar."""
    r = baw("market-order", "swap", "--fromTokenQty", qty, "--fromToken", frm, "--toToken", to, "--binanceChainId", "56")
    if isinstance(r, dict) and r.get("success") is False:
        raise RuntimeError(json.dumps(r.get("error", r), ensure_ascii=False)[:300])
    oid = find(r, ["orderId"])
    if not oid:
        raise RuntimeError("baw no devolvio orderId: " + json.dumps(r, ensure_ascii=False)[:200])
    log(f"{tag}Orden creada ({oid}). Si la app de Binance pide aprobar, hazlo (espero hasta 3 min)")
    for _ in range(60):
        time.sleep(3)
        lr = baw("market-order", "list", "--orderId", str(oid))
        st = str(find(lr, ["status"]) or "").upper()
        if st in ("FINISHED", "SUCCESS", "FILLED", "COMPLETED"):
            got, tx = find(lr, ["toTokenActualQty"]), find(lr, ["txHash"])
            log(f"{tag}Convertido" + (f": recibiste {got}" if got else ""))
            if tx:
                log("Tx: " + str(tx))
            return str(got).replace(",", "") if got else ""
        if st in ("FAILED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"):
            raise RuntimeError("la orden termino en estado " + st)
    return None


def run_swap(jid, token, amount, out=False):
    """Convierte BNB -> `token` (out=False) o `token` -> BNB (out=True). Si baw exige stablecoin en un lado, pasa por USDT."""
    job = JOBS[jid]
    frm, to = (token, NATIVE) if out else (NATIVE, token)
    lab = "token → USDT → BNB" if out else "BNB → USDT → token"

    def log(m):
        job["log"].append(f"{time.strftime('%H:%M:%S')}  {tr(m)}")
        slog("orden", m)
    try:
        sq = swap_quote(token, amount, out)
        if sq.get("success") is False:
            raise RuntimeError(json.dumps(sq.get("error", sq), ensure_ascii=False)[:300])
        if sq.get("route") == "via-usdt":
            log(T(f"Ruta en 2 pasos: {lab} (la billetera solo opera pares con stablecoin)", f"2-step route: {lab} (the wallet only trades stablecoin pairs)"))
            mid = swap_wait(frm, USDT, amount, log, "[1/2] ")
            if mid is None:
                job["status"] = "warn"
                log("No pude verificarla a tiempo: revisa la app de Binance y tu saldo")
                return
            if not mid or not float(mid) > 0:
                raise RuntimeError("no se pudo leer los USDT recibidos; el segundo paso no se ejecuto, revisa tu saldo de USDT")
            got = swap_wait(USDT, to, mid, log, "[2/2] ")
        else:
            got = swap_wait(frm, to, amount, log, "")
        if got is None:
            job["status"] = "warn"
            log("No pude verificarla a tiempo: revisa la app de Binance y tu saldo")
            return
        job["status"] = "ok"
        log("Listo")
    except Exception as ex:
        job["status"] = "error"
        log("Error: " + str(ex) + (" · " + T("Límite de la billetera: el monto supera 5 USD; prueba con una cantidad menor.", "Wallet limit: the amount is over 5 USD; try a smaller amount.") if "315008" in str(ex) else ""))
    finally:
        BUSY.release()


def run_unwrap(jid, amount):
    """WBNB -> BNB nativo: llama withdraw(uint256) del contrato WBNB con la billetera de Binance (1:1, solo cuesta el gas)."""
    job = JOBS[jid]

    def log(m):
        job["log"].append(f"{time.strftime('%H:%M:%S')}  {tr(m)}")
        slog("orden", m)
    try:
        w = wallet_addr()
        have = wbnb_balance(w)
        amt = Decimal(amount)
        if amt > have:
            raise RuntimeError(f"saldo de WBNB insuficiente: tienes {fmt_bnb(have)} y pediste {amount}")
        raw = int((amt * (Decimal(10) ** 18)).to_integral_value())
        if raw <= 0:
            raise RuntimeError("monto invalido")
        data = "0x2e1a7d4d" + format(raw, "x").rjust(64, "0")          # withdraw(uint256)
        log(T(f"Desenvolviendo {amount} WBNB → BNB (withdraw en el contrato WBNB)", f"Unwrapping {amount} WBNB → BNB (withdraw on the WBNB contract)"))
        p = baw("contract-call", "preview", "--binanceChainId", "56", "--from", w, "--to", WBNB, "--value", "0", "--inputData", data)
        if not isinstance(p, dict) or p.get("success") is False or not (p.get("data") or {}).get("requestId"):
            raise RuntimeError("baw rechazó la vista previa: " + json.dumps((p or {}).get("error", p), ensure_ascii=False)[:300])
        rid = p["data"]["requestId"]
        log(T("Vista previa aceptada por el motor de riesgo de Binance; ejecutando…", "Preview accepted by Binance's risk engine; executing…"))
        e = baw("contract-call", "execute", "--requestId", rid)
        if not isinstance(e, dict) or e.get("success") is False:
            raise RuntimeError("baw no pudo ejecutar: " + json.dumps((e or {}).get("error", e), ensure_ascii=False)[:300])
        st = str((e.get("data") or {}).get("status", ""))
        if st == "PENDING_CONFIRMATION":
            log(T("Aprueba la operación en la app de Binance…", "Approve the operation in the Binance app…"))
        for _ in range(60):                                              # hasta ~3 min: el WBNB baja cuando se confirma
            time.sleep(3)
            try:
                if wbnb_balance(w) <= have - amt + Decimal("0.000000000000000001"):
                    job["status"] = "ok"
                    log("Listo")
                    return
            except Exception:
                pass
        job["status"] = "warn"
        log("No pude verificarla a tiempo: revisa la app de Binance y tu saldo")
    except Exception as ex:
        job["status"] = "error"
        log("Error: " + str(ex))
    finally:
        BUSY.release()


def find_img(kind):
    """Busca en img/ por palabras del nombre: fondo -> background/fondo/bg; icono -> icon/logo/coin/token."""
    d = HERE / "img"
    if not d.is_dir():
        return None
    types = dict(IMG_TYPES)
    if kind == "icon":      # el logo de Lobelia Coin es siempre lobelia-icon.png (los logos de LGANG y LOBP se sirven aparte)
        for f in d.iterdir():
            if f.is_file() and f.name.lower() == "lobelia-icon.png":
                return f, types[".png"]
    taken = {x[3].lower() for x in LOBELIA_TOKENS[1:]}
    for f in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        stem, ext = f.stem.lower(), f.suffix.lower()
        if not f.is_file() or ext not in types or f.name.lower() in taken:
            continue
        is_bg = any(k in stem for k in ("background", "fondo", "wallpaper", "bg"))
        if (kind == "background" and is_bg) or (kind == "icon" and not is_bg and any(k in stem for k in ("icon", "logo", "coin", "token"))):
            return f, types[ext]
    return None


def run_signin(jid, action="signin"):
    """Ejecuta `baw auth signin` y luego `gang preflight`, mostrando lo que van imprimiendo."""
    job = JOBS[jid]

    def log(m):
        job["log"].append(f"{time.strftime('%H:%M:%S')}  {m}")
        slog("sesion", m)
    try:
        cmd = "signin"
        if action == "signout":  # el nombre exacto se lee de `baw auth --help`
            h = _sp(subprocess.run, [BAW, "auth", "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            m = re.search(r"\b(sign-?out|log-?out|sign-?off)\b", ANSI.sub("", h.stdout + h.stderr), re.I)
            cmd = m.group(1) if m else "signout"
        log(T(f"Ejecutando: baw auth {cmd}", f"Running: baw auth {cmd}") + ("" if action == "signout" else T(" (si hace falta, confirma el código en la app de Binance)", " (if needed, confirm the code in the Binance app)")))
        p = _sp(subprocess.Popen, [BAW, "auth", cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        timer = threading.Timer(240, p.kill)
        timer.start()
        for line in p.stdout:
            line = ANSI.sub("", line).rstrip()
            if line.strip():
                log(line)
        p.wait()
        timer.cancel()
        if p.returncode != 0:
            raise RuntimeError(T(f"baw terminó con error (código {p.returncode})", f"baw ended with an error (code {p.returncode})"))
        if action == "signout":
            job["status"] = "ok"
            log(T("Sesión cerrada. Usa «Iniciar / verificar sesión» para entrar de nuevo.", "Signed out. Use “Sign in / verify session” to sign in again."))
            return
        log(T("Ejecutando: gang preflight", "Running: gang preflight"))
        r = gang("preflight")
        if r.get("success"):
            job["status"] = "ok"
            log(T("Sesión verificada: todo en orden ✓ ", "Session verified: all good ✓ ") + str(r.get("address", "")))
        else:
            job["status"] = "warn"
            for it in r.get("issues") or []:
                log(f"{it.get('code')}: {it.get('fix')}")
            if r.get("error"):
                log(str(r["error"]))
            log(T("Hay cosas por corregir (arriba).", "Some things need fixing (see above)."))
    except Exception as ex:
        job["status"] = "error"
        log("Error: " + str(ex))
    finally:
        SIGN["busy"] = False


def wait_confirm(tx, log, tries=25):
    for _ in range(60):                                   # recibo directo de BSC: ~1 s, sin pasar por el CLI ni la billetera
        rc = _bsc_rpc("eth_getTransactionReceipt", [tx])
        if isinstance(rc, dict) and rc.get("status") is not None:
            if int(str(rc["status"]), 16) == 1:
                log("Confirmada en la blockchain")
                return
            raise RuntimeError("la transaccion fallo: " + tx)
        time.sleep(1)
    for _ in range(tries):                                # plan B (el de siempre)
        r = gang("tx", "--hash", tx)
        st = str(r.get("status", "")).lower()
        if r.get("success") and st == "success":
            log("Confirmada en la blockchain")
            return
        if st in ("failed", "reverted", "fail"):
            raise RuntimeError("la transaccion fallo: " + tx)
        time.sleep(3)
    raise RuntimeError("no se pudo confirmar a tiempo: " + tx)


MK, MK_LOCK = {}, threading.Lock()      # cache corto: varias pestanas/refrescos no lanzan el CLI en paralelo
MK_TTL = 3.0
MK_TIMEOUT = 25                         # un comando colgado no debe congelar la lista (antes esperaba hasta 120 s)
LIST_CMDS = {"hot": ["hot"], "new": ["new", "--max-age", "24h"], "neargrad": ["near-grad", "--min-pct", "50"]}
# El escaner mira un rango de progreso amplio (por defecto 20-80 %), asi que no puede depender solo de los
# «cerca de graduar >= 50 %» ni de los de las ultimas 24 h: se amplian las dos listas.
WIDE_CMDS = {"hot": ["hot"], "new": ["new", "--max-age", "72h"], "neargrad": ["near-grad", "--min-pct", "10"]}
LAST_OK, WARN_T = {}, {}                # ultima lista buena por comando / ultimo aviso (para no repetirlo cada refresco)
LABEL = {"hot": "populares", "new": "nuevos", "neargrad": "cerca de graduar"}


def markets(sort, auto=False):
    if sort not in ("hot", "new", "neargrad", "all"):
        return {"success": False, "error": "orden invalido"}
    with MK_LOCK:
        c = MK.get(sort)
        if c and time.time() - c[0] < MK_TTL:
            return c[1]
        if not auto:
            slog("mercado", f"Buscando tokens ({sort})")
        res = _markets(sort, auto)
        if isinstance(res, dict) and res.get("success"):
            res = {**res, "fetched_at": time.time()}
            MK[sort] = (time.time(), res)
        return res


def _tokens_of(r):
    return r.get("tokens") if isinstance(r, dict) and r.get("success") is not False and isinstance(r.get("tokens"), list) else None


def _markets(sort, quiet):
    if sort in LIST_CMDS:
        return gang(*LIST_CMDS[sort], timeout=MK_TIMEOUT, quiet=quiet)
    out = {}
    ts = [threading.Thread(target=lambda k=k, c=c: out.__setitem__(k, gang(*c, timeout=MK_TIMEOUT, quiet=quiet))) for k, c in WIDE_CMDS.items()]
    [t.start() for t in ts]; [t.join() for t in ts]
    seen, merged, failed, counts = {}, [], [], {}
    for k in WIDE_CMDS:
        toks = _tokens_of(out.get(k))
        if toks is None:                                     # este comando fallo: se usa su ultima lista buena (<= 2 min)
            failed.append(k)
            prev = LAST_OK.get(k)
            toks = prev[1] if prev and time.time() - prev[0] < 120 else []
            if time.time() - WARN_T.get(k, 0) > 60:
                WARN_T[k] = time.time()
                err = (out.get(k) or {}).get("error") if isinstance(out.get(k), dict) else ""
                slog("mercado", f"La lista de {LABEL[k]} no respondió ({str(err or 'sin datos')[:120]})" + ("; se muestra la última versión conocida" if toks else ""), "err")
        else:
            LAST_OK[k] = (time.time(), toks)
        counts[k] = len(toks)
        for t in toks:
            a = str(t.get("address", "")).lower()
            if a in seen:                                    # el mismo token en varias listas: se completan los datos que falten
                for kk, vv in t.items():
                    if seen[a].get(kk) in (None, "") and vv not in (None, ""):
                        seen[a][kk] = vv
            else:
                seen[a] = t; merged.append(t)
    if not quiet:
        slog("mercado", f"{len(merged)} tokens únicos entre populares, nuevos y cerca de graduar (" + " · ".join(f"{LABEL[k]} {n}" for k, n in counts.items()) + ")")
    return {"success": bool(merged), "tokens": merged, "partial": failed, "counts": counts, "error": "no se pudo leer el mercado"}


def run_order(jid, kind, token, amount, use_all, note="", quote="", fresh=False):
    job, ev = JOBS[jid], EVENTS[jid]

    def log(m):
        job["log"].append(f"{time.strftime('%H:%M:%S')}  {tr(m)}")
        slog("orden", m)
    try:
        if kind == "buy":                                  # comprobacion previa con el saldo real de la cadena (no depende de baw)
            try:
                w = wallet_addr()
                nat = bnb_balance(w)
                log(TE(f"Billetera {w[:6]}…{w[-4:]}: {fmt_bnb(nat)} BNB", f"Wallet {w[:6]}…{w[-4:]}: {fmt_bnb(nat)} BNB"))
                need = (Decimal(amount) if is_bnb_quote(quote) else Decimal(0)) + GAS_MIN
                if nat < need:
                    raise LowBnb(TE("BNB insuficiente · ", "Not enough BNB · ") + bnb_report(amount, quote))
                if is_bnb_quote(quote) and nat < Decimal(amount) + GAS_TIP:
                    log(TE("Aviso: te quedará muy poco BNB para el gas; si falla, baja el monto.", "Heads-up: very little BNB will be left for gas; if it fails, lower the amount."))
            except LowBnb:
                raise
            except Exception:
                pass                                       # si la cadena no responde, decide el CLI
        for _ in range(8):  # wrap -> approve -> buy/sell
            if kind == "buy":
                args = ["buy", "--token", token, "--bnb", amount]
            else:
                args = ["sell", "--token", token] + (["--all"] if use_all else ["--amount", amount])
            if note:
                args += ["--note", note]                  # nota publica que viaja con la operacion (gang buy|sell --note)
            r = gang(*args)
            if fresh and not r.get("success"):               # token recien creado: el CLI puede tardar unos segundos en verlo
                for _t in range(8):
                    if re.search(r"not enough BNB|native BNB|insufficient (funds|balance)", err_text(r.get("error", r)), re.I): break
                    time.sleep(1)
                    r = gang(*args)
                    if r.get("success"): break
            if not r.get("success"):
                err = err_text(r.get("error", r))
                if kind == "buy" and re.search(r"not enough BNB|native BNB|insufficient (funds|balance)", err, re.I):
                    rep_txt = bnb_report(amount, quote, err)
                    raise RuntimeError(err + (" · " + rep_txt if rep_txt else ""))
                if note and re.search(r"--note|\bnote\b|GangLaunchpad", err, re.I):
                    raise RuntimeError("el CLI rechazó la nota (los tokens de GangLaunchpad / V3 no admiten notas): borra la nota y vuelve a intentarlo · " + err[:160])
                raise RuntimeError(err)
            step = str(r.get("step", "?"))
            final = not (step.startswith("approve") or step.startswith("wrap"))
            log(f"Paso: {step}")
            if final and note:                            # si el CLI es viejo ignora --note y la orden saldria SIN nota: no se ejecuta
                an = r.get("agentNote")
                if not an or (isinstance(an, dict) and an.get("attached") is False):
                    raise RuntimeError("tu CLI de gang ignoraría la nota (está desactualizado): ejecuta npm i -g @gangfun/cli@latest; no se envió nada")
                log("Nota adjunta: " + note)
            risks = r.get("risks") or []
            if risks:  # no se ignora: se le muestra al usuario y espera su decision
                job["risk"] = " | ".join(f"{x.get('code')}: {x.get('description')}" for x in risks if isinstance(x, dict)) or json.dumps(risks)[:200]
                job["status"] = "needs_ack"
                log("Binance marco un riesgo. Esperando tu decision (2 min)")
                if not ev["set"].wait(120) or not ev["ok"]:
                    raise RuntimeError("operacion cancelada (por ti o por falta de respuesta)")
                job["status"] = "running"
                log("Continuas bajo tu decision: aproba la operacion en la app de Binance")
            for k, v in r.items():
                if str(k).lower().startswith(("expect", "min")) or k == "fee":
                    log(f"  {k}: {v}")
            rid = r.get("requestId")
            if not rid:
                raise RuntimeError("el CLI no devolvio requestId")
            e = gang("execute", "--request-id", rid)
            if not e.get("success"):
                raise RuntimeError(err_text(e.get("error", e)))
            if e.get("txHash") and not e.get("pendingConfirmation"):
                log("Enviada: " + e["txHash"])
                wait_confirm(e["txHash"], log)
            elif e.get("pendingConfirmation") or r.get("requireConfirmation"):
                log("Aproba la operacion en la app de Binance (espero hasta 2 min)")
                if baw_seen(e.get("orderId")):
                    log("Confirmada")
                elif final:
                    job["status"] = "warn"
                    log("No pude verificarla: revisa la app de Binance y tu saldo")
                    return
                else:
                    raise RuntimeError("no se pudo confirmar este paso; vuelve a intentarlo")
            if final:
                job["status"] = "ok"
                log("Listo")
                return
        raise RuntimeError("demasiados pasos seguidos, detenido por seguridad")
    except Exception as ex:
        job["status"] = "error"
        log("Error: " + str(ex))
    finally:
        BUSY.release()


# ---------- crear token + compra inicial automatica ----------
def _bsc_rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for u in RPCS:
        try:
            req = urllib.request.Request(u, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                j = json.loads(r.read())
            if j.get("result") is not None:
                return j["result"]
        except Exception:
            pass
    return None


def _addr_in(o, names):
    """Primera direccion 0x valida bajo alguna de las claves `names` (acepta dicts anidados)."""
    for n in names:
        v = find(o, [n])
        if isinstance(v, dict):
            v = find(v, ["address", "tokenAddress", "contractAddress"])
        if isinstance(v, str) and ADDR.match(v):
            return v
    return None


TOKEN_KEYS = ["tokenAddress", "token_address", "contractAddress", "contract_address", "tokenContract", "createdToken", "newToken", "token"]
TRANSFER_T0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _token_from_tx(txh, symbol):
    """Plan B: la direccion del token nuevo = contrato que emite el primer Transfer desde 0x0 en la transaccion de creacion."""
    rc = _bsc_rpc("eth_getTransactionReceipt", [txh])
    if not isinstance(rc, dict):
        return None
    for lg in rc.get("logs") or []:
        t = lg.get("topics") or []
        if len(t) >= 3 and t[0].lower() == TRANSFER_T0 and int(t[1], 16) == 0 and ADDR.match(str(lg.get("address", ""))):
            try:
                sym = re.sub(r"[^\w .$\-]", "", _abi_str(rpc_call(lg["address"], "0x95d89b41")))
            except Exception:
                sym = ""
            if not sym or sym.upper() == symbol.upper():
                return lg["address"]
    return None


def _token_from_new(symbol, name):
    """Plan C: buscar el token recien creado en la lista de nuevos de gang (por simbolo y nombre)."""
    r = gang("new", "--max-age", "1h", quiet=True)
    best = None
    for t in _tokens_of(r) or []:
        a = t.get("address") or t.get("tokenAddress")
        if isinstance(a, str) and ADDR.match(a) and str(t.get("symbol", "")).upper() == symbol.upper():
            if str(t.get("name", "")).strip().lower() == name.strip().lower():
                return a
            best = best or a
    return best


def creator_fee_view(token):
    """`gang creator-fee --token X` (solo lectura): cuanto hay para reclamar."""
    r = gang("creator-fee", "--token", token, timeout=120)
    if not isinstance(r, dict) or r.get("success") is False:
        return {"error": err_text(r.get("error", r) if isinstance(r, dict) else r, 300)}
    v = find(r, ["claimable", "claimableAmount", "claimable_amount", "unclaimed", "pending"])
    if isinstance(v, dict):
        v = find(v, ["formatted", "amount", "value"])
    unit = find(r, ["quoteSymbol", "symbol", "unit", "currency"])
    return {"claimable": None if v is None else str(v), "unit": str(unit) if isinstance(unit, str) else "", "raw": err_text(r, 600)}


def run_claim_fee(jid, token):
    """`gang creator-fee --token X --claim`: prepara el reclamo, lo ejecuta y espera la confirmacion."""
    job = JOBS[jid]

    def log(m):
        job["log"].append(f"{time.strftime('%H:%M:%S')}  {tr(m)}")
        slog("orden", m)
    try:
        log("Preparando el reclamo de comisión de creador…")
        r = gang("creator-fee", "--token", token, "--claim", timeout=180)
        if not isinstance(r, dict) or r.get("success") is False:
            raise RuntimeError(err_text(r.get("error", r) if isinstance(r, dict) else r))
        rid, txh, e = find(r, ["requestId"]), find(r, ["txHash", "transactionHash"]), r
        if rid and not txh:                                  # el CLI dejo la operacion preparada: se ejecuta
            log("Ejecutando… aprueba en la app de Binance si te lo pide")
            e = gang("execute", "--request-id", str(rid), timeout=240)
            if not isinstance(e, dict) or e.get("success") is False:
                raise RuntimeError(err_text(e.get("error", e) if isinstance(e, dict) else e))
            txh = find(e, ["txHash", "transactionHash"])
        if isinstance(e, dict) and e.get("pendingConfirmation"):
            log("Esperando tu aprobación en la app de Binance (hasta 2 min)")
            if not baw_seen(e.get("orderId")):
                raise RuntimeError("no se pudo confirmar el reclamo; revisa la app de Binance.")
        elif txh:
            log("Enviada: " + str(txh))
            wait_confirm(str(txh), log)
        else:
            log(err_text(e, 400))
        job["status"] = "ok"
        log("Listo: comisión de creador reclamada ✓")
    except Exception as ex:
        job["status"] = "error"
        log("Error: " + str(ex))
    finally:
        BUSY.release()


def preapprove_quote(quote, amount, log):
    """Deja aprobada la moneda par ANTES de crear el token, para que la compra inicial no gaste ~30 s en el paso approve-quote.
    Usa como referencia un token V3 ya existente con la misma moneda par: si `gang buy` pide approve-quote, se ejecuta
    SOLO esa aprobacion; la compra de referencia nunca se ejecuta."""
    toks = (markets("all", True) or {}).get("tokens", []) or []
    ref = None
    for t in toks:
        q = t.get("quote")
        q = find(q, ["address"]) if isinstance(q, dict) else q
        a = t.get("address") or t.get("tokenAddress")
        if isinstance(a, str) and ADDR.match(a) and str(q or "").lower() == quote.lower() and not t.get("graduated"):
            ref = a
            break
    if not ref:
        log("Pre-aprobación: no hay un token de referencia con esa moneda par; se aprobará después de crear.")
        return
    log("Pre-aprobando la moneda par (antes de crear, no afecta la velocidad de compra)…")
    r = gang("buy", "--token", ref, "--bnb", amount)
    if not isinstance(r, dict) or not r.get("success"):
        log("Pre-aprobación: el CLI no la preparó (" + err_text(r.get("error", r) if isinstance(r, dict) else r, 120) + ")")
        return
    step = str(r.get("step", "?"))
    if not step.startswith("approve"):
        log(f"Pre-aprobación: no hace falta (paso: {step}).")
        return
    if r.get("risks"):
        log("Pre-aprobación omitida: Binance marcó un riesgo en la referencia.")
        return
    rid = r.get("requestId")
    e = gang("execute", "--request-id", rid) if rid else None
    if not isinstance(e, dict) or not e.get("success"):
        log("Pre-aprobación: falló la ejecución; se aprobará después de crear.")
        return
    if e.get("txHash") and not e.get("pendingConfirmation"):
        wait_confirm(str(e["txHash"]), log)
    elif e.get("pendingConfirmation"):
        if not baw_seen(e.get("orderId")):
            log("Pre-aprobación: no se pudo confirmar; se aprobará después de crear si hace falta.")
            return
    log("Moneda par aprobada ✓")


def run_create_buy(jid, rid, item):
    """Ejecuta la creacion ya previsualizada y, en cuanto el token existe, compra `item['buy']` de la moneda par."""
    job = JOBS[jid]

    def log(m):
        job["log"].append(f"{time.strftime('%H:%M:%S')}  {tr(m)}")
        slog("orden", m)
    handed_over = False
    try:
        if item.get("buy") and not is_bnb_quote(item.get("quote")):
            try:
                preapprove_quote(item["quote"], item["buy"], log)
            except Exception as ex:                    # nunca bloquea la creacion
                log("Pre-aprobación omitida: " + str(ex)[:200])
        log("Creando el token… aprueba la operación en la app de Binance si te la pide")
        e = gang("execute", "--request-id", rid, timeout=240)
        if not isinstance(e, dict) or e.get("success") is False:
            raise RuntimeError(err_text(e.get("error", e) if isinstance(e, dict) else e))
        txh = find(e, ["txHash", "transactionHash"])
        if e.get("pendingConfirmation"):
            log("Esperando tu aprobación en la app de Binance (hasta 2 min)")
            if not baw_seen(e.get("orderId")):
                raise RuntimeError("no se pudo confirmar la creación; revisa la app de Binance. No se compró nada.")
        elif txh:
            log("Creación enviada: " + str(txh))
            wait_confirm(str(txh), log)
        addr = _addr_in(e, TOKEN_KEYS)
        if not addr and txh:
            addr = _token_from_tx(str(txh), item["symbol"])
        if not addr:
            for _ in range(6):
                addr = _token_from_new(item["symbol"], item["name"])
                if addr: break
                time.sleep(2)
        if not addr:
            raise RuntimeError("el token se creó, pero no pude saber su dirección; la compra inicial NO se hizo (cómpralo desde la lista).")
        log(f"Token creado: {addr}")
        log(f"Compra inicial: {item['buy']} " + ("BNB" if is_bnb_quote(item.get("quote")) else "(moneda par)"))
        handed_over = True                              # run_order suelta BUSY por su cuenta
        run_order(jid, "buy", addr, item["buy"], False, "", item.get("quote", ""), fresh=True)
    except Exception as ex:
        job["status"] = "error"
        log("Error: " + str(ex))
    finally:
        if not handed_over:
            BUSY.release()


# ---------- saldo y datos leidos directamente de BSC (sirve para tokens V2 y V3) ----------
RPCS = ["https://bsc-dataseed.binance.org", "https://bsc-dataseed1.defibit.io", "https://bsc-dataseed1.ninicoin.io",
        "https://bsc-rpc.publicnode.com", "https://binance.llamarpc.com"]
WALLET = {"addr": ""}


def rpc_call(to, data):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": to, "data": data}, "latest"]}).encode()
    last = "sin respuesta"
    for u in RPCS:
        try:
            req = urllib.request.Request(u, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as r:
                j = json.loads(r.read())
            if j.get("result") not in (None, "0x"):
                return j["result"]
            last = str(j.get("error") or "respuesta vacia")[:120]
        except Exception as e:
            last = str(e)[:120]
    raise RuntimeError("no se pudo leer la blockchain (BSC): " + last)


def wallet_addr():
    if WALLET["addr"]:
        return WALLET["addr"]
    r = gang("preflight")
    a = r.get("address") if isinstance(r, dict) else None
    if not (isinstance(a, str) and ADDR.match(a)):
        raise RuntimeError("no se pudo leer la direccion de tu billetera (usa «Iniciar / verificar sesión»)")
    WALLET["addr"] = a
    return a


class LowBnb(RuntimeError):
    pass


GAS_MIN = Decimal("0.0003")     # por debajo de monto + esto la compra no puede cubrir el gas: se detiene antes de llamar al CLI
GAS_TIP = Decimal("0.002")      # recomendado para wrap + approve + compra


def bnb_balance(addr=None):
    """BNB nativo (Decimal) de la billetera, leido directamente de BSC (no depende de baw)."""
    w = addr or wallet_addr()
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [w, "latest"]}).encode()
    last = "sin respuesta"
    for rpc in RPCS:
        try:
            req = urllib.request.Request(rpc, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as rr:
                jj = json.loads(rr.read())
            if jj.get("result"):
                return Decimal(int(jj["result"], 16)) / (Decimal(10) ** 18)
            last = str(jj.get("error") or "respuesta vacía")[:120]
        except Exception as ex:
            last = str(ex)[:120]
    raise RuntimeError("no se pudo leer la blockchain (BSC): " + last)


def wbnb_balance(addr):
    raw = int(rpc_call(WBNB, "0x70a08231" + addr[2:].lower().rjust(64, "0")), 16)
    return Decimal(raw) / (Decimal(10) ** 18)


def is_bnb_quote(q):
    return str(q or "").strip().lower() in ("", "bnb", "wbnb", "—", "-", ZERO, WBNB, NATIVE.lower())


def fmt_bnb(d):
    return format(d.quantize(Decimal("0.000001")), "f")


def bnb_report(amount, quote, cli_error=""):
    """Explica con cifras reales por que no hay BNB suficiente. Devuelve '' si no se pudo leer la cadena."""
    try:
        w = wallet_addr()
        nat = bnb_balance(w)
    except Exception as ex:
        return TE("No pude leer tu saldo en la cadena: " + str(ex)[:120], "Could not read your on-chain balance: " + str(ex)[:120])
    try:
        wb = wbnb_balance(w)
    except Exception:
        wb = Decimal(0)
    amt = Decimal(amount) if is_bnb_quote(quote) else Decimal(0)
    short = w[:6] + "…" + w[-4:]
    biggest = max(nat - GAS_TIP, Decimal(0))
    wbs_es = f" (y {fmt_bnb(wb)} WBNB)" if wb > 0 else ""
    wbs_en = f" (and {fmt_bnb(wb)} WBNB)" if wb > 0 else ""
    if nat < amt + GAS_MIN:
        es = (f"La billetera {short} tiene {fmt_bnb(nat)} BNB{wbs_es} y esta compra necesita {fmt_bnb(amt)} BNB más gas. " +
              (f"Prueba con {fmt_bnb(biggest)} BNB o menos, o deposita" if biggest > 0 else "Deposita") + f" BNB en {w}. "
              "El saldo que ves dentro de la app de Binance (Spot) no cuenta: tiene que estar en la billetera Agentic.")
        en = (f"Wallet {short} holds {fmt_bnb(nat)} BNB{wbs_en} and this buy needs {fmt_bnb(amt)} BNB plus gas. " +
              (f"Try {fmt_bnb(biggest)} BNB or less, or deposit" if biggest > 0 else "Deposit") + f" BNB to {w}. "
              "The balance inside the Binance app (Spot) does not count: it must be in the Agentic wallet.")
        return TE(es, en)
    if nat < amt + GAS_TIP:
        es = (f"La billetera {short} tiene {fmt_bnb(nat)} BNB: tras gastar {fmt_bnb(amt)} BNB quedan {fmt_bnb(nat - amt)} BNB y el CLI exige además margen para el gas. "
              f"Prueba con {fmt_bnb(biggest)} BNB o menos.")
        en = (f"Wallet {short} holds {fmt_bnb(nat)} BNB: after spending {fmt_bnb(amt)} BNB only {fmt_bnb(nat - amt)} BNB would be left and the CLI also requires a gas margin. "
              f"Try {fmt_bnb(biggest)} BNB or less.")
        return TE(es, en)
    es = (f"La cadena muestra {fmt_bnb(nat)} BNB en la billetera {short}, suficiente para este monto, pero el CLI no lo ve: "
          "probablemente la sesión de baw es de otra cuenta. Pulsa «Iniciar / verificar sesión» y comprueba que la dirección sea la tuya.")
    en = (f"The chain shows {fmt_bnb(nat)} BNB in wallet {short}, enough for this amount, but the CLI does not see it: "
          "the baw session is probably on another account. Press “Sign in / verify session” and check the address is yours.")
    if not is_bnb_quote(quote):
        es += " Además, este token se paga con otra moneda y el panel envía el monto al CLI como BNB (--bnb)."
        en += " Also, this token is paid with another coin and the panel sends the amount to the CLI as BNB (--bnb)."
    return TE(es, en)


def _abi_str(h):
    b = bytes.fromhex(h[2:])
    if len(b) >= 64:
        return b[64:64 + int.from_bytes(b[32:64], "big")].decode("utf-8", "replace")
    return b.rstrip(b"\0").decode("utf-8", "replace")


def onchain_balance(token):
    w = wallet_addr()
    raw = int(rpc_call(token, "0x70a08231" + w[2:].lower().rjust(64, "0")), 16)
    try:
        dec = int(rpc_call(token, "0x313ce567"), 16)
    except Exception:
        dec = 18
    try:
        sym = re.sub(r"[^\w .$\-]", "", _abi_str(rpc_call(token, "0x95d89b41")))[:20]
    except Exception:
        sym = ""
    return {"success": True, "balance": format(Decimal(raw) / (Decimal(10) ** dec), "f"), "decimals": dec, "symbol": sym, "wallet": w}


def _dec(x):
    try:
        return Decimal(str(x).replace(",", ""))
    except Exception:
        return None


def known_add(addr):
    try:
        c = cfg_load(); L = [a for a in c.get("known_tokens", []) if a.lower() != addr.lower()]
        L.insert(0, addr); c["known_tokens"] = L[:300]; cfg_save(c)
    except Exception:
        pass


def rpc_batch(calls):
    """Varias eth_call en una sola peticion JSON-RPC. Devuelve lista de resultados (None si fallo)."""
    body = json.dumps([{"jsonrpc": "2.0", "id": i, "method": "eth_call", "params": [{"to": t, "data": d}, "latest"]} for i, (t, d) in enumerate(calls)]).encode()
    for u in RPCS:
        try:
            req = urllib.request.Request(u, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                j = json.loads(r.read())
            if isinstance(j, list):
                out = [None] * len(calls)
                for x in j:
                    if isinstance(x.get("id"), int) and x.get("result") not in (None, "0x"):
                        out[x["id"]] = x["result"]
                return out
        except Exception:
            continue
    raise RuntimeError("no se pudo leer la blockchain (BSC)")


LOBELIA_TOKENS = (
    ("0x5ff77b7f56f145c539a04ce24d9337b4231195e8", "Lobelia Coin", "LOBELIA", "lobelia-icon.png"),
    ("0x041e95637ea4ab1bf90a935f3730def0e71cfa11", "LobeliaGANG", "LGANG", "LGANG.jpg"),
    ("0x613e07428692446a62469f8a28eb7cb928518c74", "LobeliaPanel", "LOBP", "LobeliaPanel-logo.jpg"),
)
IMG_FILES = {f: dict(IMG_TYPES).get(Path(f).suffix.lower(), "application/octet-stream") for *_, f in LOBELIA_TOKENS}


def _hex_str(h):
    """Decodifica el resultado de symbol() (string ABI o bytes32)."""
    try:
        b = bytes.fromhex((h or "")[2:])
        if len(b) >= 64 and int.from_bytes(b[:32], "big") == 32:
            n = int.from_bytes(b[32:64], "big")
            return b[64:64 + n].decode("utf-8", "ignore").strip()
        return b[:32].rstrip(b"\0").decode("utf-8", "ignore").strip()
    except Exception:
        return ""


def _baw_wallet_tokens():
    """Todos los tokens con saldo que ve la billetera de Binance (baw wallet balance), sin ocultar los de poco valor."""
    out = {}
    try:
        r = baw("wallet", "balance", "--binanceChainId", "56", "--hideLowValue", "false", timeout=45)
        rows = r.get("data") if isinstance(r, dict) else r
        if isinstance(rows, dict):
            rows = rows.get("tokens") or rows.get("list") or []
        for t in rows or []:
            a = str(t.get("address", ""))
            if ADDR.match(a) and a.lower() != NATIVE.lower():
                out[a.lower()] = (a, str(t.get("symbol", "")), str(t.get("balance", "")))
    except Exception:
        pass
    return out


def my_tokens():
    """Saldo de tu billetera en todos los tokens: lo que reporta baw + listas del mercado + los que abriste/compraste + los de Lobelia."""
    w = wallet_addr()
    addrs, found, seen = {}, [], set()
    for a, name, sym, _ in LOBELIA_TOKENS:
        addrs[a.lower()] = (a, sym)
    for t in (markets("all", True) or {}).get("tokens", []) or []:
        if ADDR.match(str(t.get("address", ""))):
            addrs.setdefault(t["address"].lower(), (t["address"], str(t.get("symbol", ""))))
    for a in cfg_load().get("known_tokens", []):
        if ADDR.match(a):
            addrs.setdefault(a.lower(), (a, ""))
    bw = _baw_wallet_tokens()
    for k, (a, sym, _b) in bw.items():
        if k in addrs and not addrs[k][1]:
            addrs[k] = (addrs[k][0], sym)
        addrs.setdefault(k, (a, sym))
    items, pad = list(addrs.values()), w[2:].lower().rjust(64, "0")
    for i in range(0, len(items), 40):
        chunk = items[i:i + 40]
        try:
            res = rpc_batch([(a, "0x70a08231" + pad) for a, _ in chunk] + [(a, "0x313ce567") for a, _ in chunk] + [(a, "0x95d89b41") for a, _ in chunk])
        except Exception:
            continue
        n = len(chunk)
        for j, (a, sym) in enumerate(chunk):
            try:
                raw = int(res[j], 16) if res[j] else 0
            except Exception:
                raw = 0
            if raw > 0:
                try:
                    dec = int(res[n + j], 16) if res[n + j] else 18
                except Exception:
                    dec = 18
                found.append({"address": a, "symbol": sym or _hex_str(res[2 * n + j]) or "", "balance": format(Decimal(raw) / (Decimal(10) ** dec), "f")})
                seen.add(a.lower())
    for k, (a, sym, b) in bw.items():          # los que baw ve pero la lectura directa no devolvio
        try:
            if k not in seen and Decimal(b) > 0:
                found.append({"address": a, "symbol": sym, "balance": format(Decimal(b), "f")})
        except Exception:
            pass
    found.sort(key=lambda x: (x["symbol"] or "~").lower())
    return {"success": True, "wallet": w, "checked": len(items), "tokens": found}


def enrich_token(r, addr):
    """`gang token` (V3 / GangLaunchpad) no siempre trae myBalance ni symbol: se completan desde la cadena.
    Si el CLI no reconoce el token pero el contrato existe en BSC, se devuelve igualmente (modo degradado)
    para poder ver el saldo y vender."""
    if not isinstance(r, dict):
        return r
    cli_err = None
    if r.get("success") is False:
        cli_err = r.get("error")
        r = {"success": True, "token": {"address": addr, "graduated": False}, "_degraded": True,
             "_cliError": (json.dumps(cli_err, ensure_ascii=False) if not isinstance(cli_err, str) else cli_err)[:300]}
    r["_quote"] = quote_of(r)
    t = r["token"] if isinstance(r.get("token"), dict) else r
    cli_bal = _dec(find(t, ["myBalance", "userBalance", "walletBalance"]))
    chain_bal = None
    try:
        c = onchain_balance(addr)
        chain_bal = _dec(c["balance"])
        r["_symbol"] = c["symbol"]
        r["_wallet"] = c["wallet"]
        if c["symbol"] and not t.get("symbol"):
            t["symbol"] = c["symbol"]
    except Exception as e:
        r["_balanceError"] = str(e)[:200]
    best = max([x for x in (cli_bal, chain_bal) if x is not None], default=None)   # el mayor: evita falsos "saldo 0"
    if best is not None:
        t["myBalance"] = format(best, "f")
        r["_balanceSrc"] = "chain" if chain_bal is not None and best == chain_bal else "cli"
        r.pop("_balanceError", None) if chain_bal is not None or cli_bal is not None else None
    if cli_err is not None and best is None:
        return {"success": False, "error": cli_err}          # ni el CLI ni la cadena conocen ese contrato
    if not t.get("priceBnb") and t.get("priceQuote"):
        t["priceBnb"] = t["priceQuote"]
    return r


# ---------- cotizaciones: V2/Soft/U/S con `gang quote`; V3 (GangLaunchpad) simulando con `gang buy|sell` ----------
V3_HINT = re.compile(r"GangLaunchpad|gang buy simulates|expectTokens|simulat", re.I)
QC, QC_TTL = {}, 15.0
EXP_KEYS = ["expectTokens", "expectedTokens", "expectTokenAmount", "expectTokensOut", "expectOut", "expectedOut",
            "expectBnb", "expectedBnb", "expectAmount", "expectedAmount", "expectQuote", "expectReceive"]


def _simulate(kind, token, amount):
    """`gang buy/sell` solo SIMULA: no se envia nada hasta `gang execute` (que este panel no llama aqui)."""
    args = ["buy", "--token", token, "--bnb", amount] if kind == "buy" else ["sell", "--token", token, "--amount", amount]
    r = gang(*args)
    if not isinstance(r, dict) or r.get("success") is False:
        return r
    exp = find(r, EXP_KEYS)
    if exp is None:
        for k, v in r.items():
            if str(k).lower().startswith("expect") and isinstance(v, (str, int, float)):
                exp = v
                break
    return {"success": True, "simulated": True, "tokensOut": None if exp is None else str(exp).replace(",", ""),
            "fee": r.get("fee"), "refund": r.get("refund"), "step": r.get("step"),
            "min": next((v for k, v in r.items() if str(k).lower().startswith("min") and isinstance(v, (str, int, float))), None)}


def quote_any(kind, token, amount):
    key = (kind, token.lower(), amount)
    c = QC.get(key)
    if c and time.time() - c[0] < QC_TTL:
        return c[1]
    if kind == "buy":
        res = gang("quote", "--token", token, "--bnb", amount)
        if isinstance(res, dict) and res.get("success") is False and V3_HINT.search(json.dumps(res, ensure_ascii=False)):
            res = _simulate("buy", token, amount)
    else:
        res = _simulate("sell", token, amount)
    if isinstance(res, dict) and res.get("success"):
        QC[key] = (time.time(), res)
        if len(QC) > 200:
            QC.clear()
    return res


# ---------- configuracion local (tokens conocidos) y notas ----------
def _int(x):
    try:
        return int(float(str(x or 0).replace(",", ".")))
    except ValueError:
        return -1                                          # fuera de rango: lo rechaza la validacion de impuestos


def purge_create():
    """Borra las vistas previas de «crear token» vencidas (y su logo temporal)."""
    for k, it in list(CREATE_REQUESTS.items()):
        if time.time() - it["created"] > 240:
            Path(it["logo"]).unlink(missing_ok=True); CREATE_REQUESTS.pop(k, None)
    for f in HERE.glob("lobelia_logo_*"):                  # restos de ejecuciones anteriores
        try:
            if time.time() - f.stat().st_mtime > 3600 and str(f) not in {it["logo"] for it in CREATE_REQUESTS.values()}:
                f.unlink()
        except OSError:
            pass


CFG_FILE = HERE / "config.json"
NOTE_MAX = 280
NOTE_BAD = re.compile(r'[\x00-\x1f\x7f%^&|<>`\\]')


def cfg_load():
    try:
        return json.loads(CFG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def cfg_save(c):
    CFG_FILE.write_text(json.dumps(c, indent=1), encoding="utf-8")
    try:
        os.chmod(CFG_FILE, 0o600)
    except Exception:
        pass


def clean_note(x):
    """Texto de la nota listo para pasarlo al CLI. En Windows `gang` es un .cmd y cmd.exe interpreta % ^ & | < > ",
    asi que esos caracteres (y saltos de linea) se sustituyen; un guion inicial se quita para que no se lea como opcion."""
    x = str(x or "").replace('"', "'")
    x = NOTE_BAD.sub(" ", x)
    x = re.sub(r"\s+", " ", x).strip().lstrip("-–— ").strip()
    return x[:NOTE_MAX]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, dict) and isinstance(body.get("error"), str):
            body = {**body, "error": tr(body["error"])}
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def guard(self):
        if self.headers.get("Host") not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            self.send(403, {"error": "host no permitido"}); return False
        if self.headers.get("X-Session") != SESSION:
            self.send(403, {"error": "sesion invalida, recarga la pagina"}); return False
        return True

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/":
            html = (HERE / "LobeliaPanelApp.html").read_text(encoding="utf-8").replace("__SESSION__", SESSION)
            return self.send(200, html.encode(), "text/html")
        if u.path.startswith("/img/t/"):
            fn = u.path[7:]
            if fn in IMG_FILES and (HERE / "img" / fn).is_file() and self.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
                return self.send(200, (HERE / "img" / fn).read_bytes(), IMG_FILES[fn])
            return self.send(404, b"", "text/plain")
        if u.path in ("/img/icon", "/img/background"):
            if self.headers.get("Host") not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
                return self.send(403, b"", "text/plain")
            hit = find_img(u.path.split("/")[-1])
            if hit:
                return self.send(200, hit[0].read_bytes(), hit[1])
            return self.send(404, b"", "text/plain")
        if not self.guard(): return
        if u.path == "/api/mytokens":
            try:
                return self.send(200, my_tokens())
            except Exception as ex:
                return self.send(200, {"success": False, "error": str(ex)[:200]})
        if u.path == "/api/version":
            return self.send(200, {"version": VERSION})
        if u.path == "/api/markets":
            res = markets(q.get("sort", ["hot"])[0], q.get("auto", ["0"])[0] == "1")
            return self.send(200, {**res, "cli_update": CLI_UPDATE["info"]} if isinstance(res, dict) else res)
        if u.path == "/api/token":
            a = q.get("address", [""])[0]
            if not ADDR.match(a):
                return self.send(200, {"success": False, "error": "direccion invalida"})
            res = enrich_token(gang("token", "--token", a), a)
            if isinstance(res, dict) and res.get("success") is not False:
                known_add(a)
            return self.send(200, res)
        if u.path == "/api/bnbbalance":
            try:
                w = wallet_addr()
                return self.send(200, {"success": True, "wallet": w, "balance": format(bnb_balance(w), "f")})
            except Exception as ex:
                return self.send(200, {"success": False, "error": str(ex)[:200]})
        if u.path == "/api/balance":
            a = q.get("address", [""])[0]
            if not ADDR.match(a):
                return self.send(200, {"success": False, "error": "direccion invalida"})
            try:
                return self.send(200, onchain_balance(a))
            except Exception as ex:
                return self.send(200, {"success": False, "error": str(ex)[:200]})
        if u.path == "/api/holdings":
            return self.send(200, {"success": False, "error": "ya no existe"})
        if u.path == "/api/syslog":
            since = int(q.get("since", ["0"])[0] or 0)
            with SYS_LOCK:
                last = SEQ[0]
                items = [x for x in SYS if x["n"] > since] if since <= last else list(SYS)
            return self.send(200, {"last": last, "items": items})
        if u.path == "/api/bnbprice":
            return self.send(200, bnb_usd())
        if u.path == "/api/preflight":
            if BUSY.locked() and PF_CACHE["r"] is not None:   # orden en curso: se responde con la ultima comprobacion buena
                return self.send(200, PF_CACHE["r"])
            r = gang("preflight")
            if isinstance(r, dict) and (r.get("success") or r.get("status") == "CONNECTED"):
                PF_CACHE["r"] = r
            if isinstance(r, dict) and isinstance(r.get("address"), str) and ADDR.match(r["address"]):
                WALLET["addr"] = r["address"]          # por si cambiaste de cuenta
            return self.send(200, r)
        if u.path == "/api/job":
            return self.send(200, JOBS.get(q.get("id", [""])[0], {"status": "unknown", "log": []}))
        self.send(404, {"error": "no existe"})

    POST_PATHS = {"/api/lang", "/api/signin", "/api/signout", "/api/log", "/api/ack",
                  "/api/quote", "/api/swapquote", "/api/swap", "/api/buy", "/api/sell", "/api/create-token", "/api/create-execute", "/api/creator-fee"}

    def do_POST(self):
        if not self.guard(): return
        if self.path not in self.POST_PATHS:        # antes caia en la validacion de token y respondia «direccion invalida»
            return self.send(404, {"error": "no existe: " + self.path})
        try:
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except Exception:
            return self.send(400, {"error": "json invalido"})
        if self.path == "/api/lang":
            LANG[0] = b.get("lang") if b.get("lang") in ("en", "zh", "zt") else "es"
            return self.send(200, {"ok": True})
        if self.path in ("/api/signin", "/api/signout"):
            if SIGN["busy"]:
                return self.send(409, {"error": T("ya hay un inicio de sesión en curso", "a sign-in is already running")})
            SIGN["busy"] = True
            jid = uuid.uuid4().hex[:8]
            JOBS[jid] = {"status": "running", "log": []}
            threading.Thread(target=run_signin, args=(jid, self.path[5:]), daemon=True).start()
            return self.send(200, {"job": jid})
        if self.path == "/api/log":      # la pagina anota aqui lo que hace el escaner
            tag = str(b.get("tag", "panel"))
            slog(tag if tag in ("escáner", "panel") else "panel", str(b.get("msg", ""))[:300])
            return self.send(200, {"ok": True})
        if self.path == "/api/ack":
            ev = EVENTS.get(str(b.get("job")))
            if not ev:
                return self.send(404, {"error": "orden desconocida"})
            ev["ok"] = bool(b.get("accept")); ev["set"].set()
            return self.send(200, {"ok": True})
        if self.path == "/api/create-token":
            name = re.sub(r"[\x00-\x1f\x7f]", "", str(b.get("name", ""))).strip()[:64]
            symbol = re.sub(r"[^A-Za-z0-9]", "", str(b.get("symbol", ""))).upper()[:12]
            note = clean_note(b.get("note", ""))
            if not name or not symbol: return self.send(400, {"success": False, "error": "Nombre y símbolo son obligatorios."})
            if b.get("kind") not in ("v3", "fair"): return self.send(400, {"success": False, "error": "Tipo de lanzamiento no válido."})
            ext = {"image/png":".png", "image/jpeg":".jpg", "image/webp":".webp"}.get(str(b.get("mime", "")))
            if not ext: return self.send(400, {"success": False, "error": "Logo PNG/JPG/WEBP requerido."})
            try: raw = base64.b64decode(str(b.get("logoData", "")), validate=True)
            except Exception: return self.send(400, {"success": False, "error": "Imagen inválida."})
            if not raw or len(raw) > 512*1024: return self.send(400, {"success": False, "error": "El logo debe pesar 512 KB o menos."})
            temp = HERE / ("lobelia_logo_" + uuid.uuid4().hex + ext); temp.write_bytes(raw)
            args = ["create", "--name", name, "--symbol", symbol, "--logo", str(temp)]
            if b.get("kind") == "fair":
                args += ["--fair", "--raise", str(b.get("raise", "10")), "--cap", str(b.get("cap", "1")), "--hold", str(b.get("hold", "70")), "--mint-fee", str(b.get("fee", "1"))]
            else:
                bt, st, dv = _int(b.get("buyTax")), _int(b.get("sellTax")), _int(b.get("dividend"))
                if not (0 <= bt <= 5 and 0 <= st <= 5 and 0 <= dv <= 100): temp.unlink(missing_ok=True); return self.send(400, {"success":False,"error":"Impuestos: 0–5%; dividendos: 0–100%."})
                if bt or st or dv:
                    if not (bt and st): temp.unlink(missing_ok=True); return self.send(400, {"success":False,"error":"Para impuestos, configura compra y venta mayores que cero."})
                    args += ["--buy-tax", str(bt), "--sell-tax", str(st), "--dividend", str(dv)]
            website, twitter = str(b.get("website", "")).strip(), str(b.get("twitter", "")).strip()
            if website:
                if not re.match(r"^https?://[^\s]+$", website, re.I): temp.unlink(missing_ok=True); return self.send(400, {"success":False,"error":"El sitio debe comenzar con http:// o https://."})
                args += ["--website", website]
            if twitter: args += ["--twitter", twitter]
            if note: args += ["--note", note]
            if b.get("pair") == "CUSTOM":
                quote, pool = str(b.get("quote", "")), str(b.get("pool", ""))
                if not ADDR.match(quote) or not ADDR.match(pool): temp.unlink(missing_ok=True); return self.send(400, {"success":False,"error":"Se requieren contratos válidos para moneda par y pool."})
                args += ["--quote", quote, "--ref-pool", pool]
            buy = str(b.get("initialBuy", "")).strip().replace(",", ".")
            if buy:
                if b.get("kind") != "v3": temp.unlink(missing_ok=True); return self.send(400, {"success":False,"error":"La compra inicial solo está disponible en GangLaunchpad V3."})
                if not NUM.match(buy) or not Decimal(buy) > 0: temp.unlink(missing_ok=True); return self.send(400, {"success":False,"error":"Compra inicial no válida: usa un número mayor que cero, por ejemplo 0.01."})
            purge_create()
            if not BUSY.acquire(blocking=False): temp.unlink(missing_ok=True); return self.send(409, {"success":False,"error":"Ya hay otra operación en curso."})
            try:
                result = gang(*args, timeout=180)             # solo SIMULA: se suelta BUSY enseguida (antes quedaba tomado hasta ejecutar y bloqueaba todas las compras)
            except Exception as ex:
                result = {"success": False, "error": str(ex)[:500]}
            finally:
                BUSY.release()
            if not isinstance(result, dict) or result.get("success") is False:
                temp.unlink(missing_ok=True)
                return self.send(200, {"success": False, "error": str(result.get("error", result) if isinstance(result, dict) else result)[:1000]})
            rid = find(result, ["requestId", "request_id"])
            if not rid:
                temp.unlink(missing_ok=True)
                return self.send(200, {"success": False, "error": "El CLI no devolvió requestId; no se ejecutó nada.", "preview": json.dumps(result, ensure_ascii=False, indent=2)})
            CREATE_REQUESTS[str(rid)] = {"logo": str(temp), "created": time.time(), "buy": buy,
                                         "quote": str(b.get("quote", "")) if b.get("pair") == "CUSTOM" else "",
                                         "name": name, "symbol": symbol}
            return self.send(200, {"success": True, "requestId": str(rid), "preview": json.dumps(result, ensure_ascii=False, indent=2)})
        if self.path == "/api/create-execute":
            rid = str(b.get("requestId", "")); item = CREATE_REQUESTS.get(rid)
            if not item or time.time() - item["created"] > 240:
                if item: Path(item["logo"]).unlink(missing_ok=True); CREATE_REQUESTS.pop(rid, None)
                return self.send(400, {"success": False, "error": "Vista previa vencida; vuelve a prepararla."})
            if not BUSY.acquire(blocking=False): return self.send(409, {"success": False, "error": "Ya hay otra operación en curso."})
            if item.get("buy"):                           # crear + comprar: un solo trabajo; BUSY se suelta al terminar la compra
                CREATE_REQUESTS.pop(rid, None)
                jid = uuid.uuid4().hex[:8]
                JOBS[jid] = {"status": "running", "log": []}
                EVENTS[jid] = {"set": threading.Event(), "ok": False}
                threading.Thread(target=run_create_buy, args=(jid, rid, item), daemon=True).start()
                return self.send(200, {"success": True, "job": jid})
            try:
                result = gang("execute", "--request-id", rid, timeout=240)
            except Exception as ex:
                result = {"success": False, "error": str(ex)[:500]}
            finally:
                BUSY.release()
            CREATE_REQUESTS.pop(rid, None); Path(item["logo"]).unlink(missing_ok=True)
            return self.send(200, {"success": not (isinstance(result, dict) and result.get("success") is False), "message": json.dumps(result, ensure_ascii=False, indent=2)})
        token, amount = str(b.get("token", "")), str(b.get("amount", ""))
        if not ADDR.match(token):
            return self.send(400, {"error": "direccion invalida"})
        path = self.path
        if path == "/api/quote":
            if not NUM.match(amount) or not float(amount) > 0: return self.send(400, {"error": "monto invalido"})
            return self.send(200, quote_any("sell" if b.get("side") == "sell" else "buy", token, amount))
        if path == "/api/swapquote":
            if not NUM.match(amount): return self.send(400, {"error": "monto invalido"})
            return self.send(200, swap_quote(token, amount, b.get("dir") == "out"))
        if path == "/api/swap":
            unwrap = token.lower() == WBNB and b.get("dir") == "out"
            if token.lower() in NOT_A_TARGET and not unwrap:
                return self.send(400, {"error": "ese destino es BNB: no hace falta convertir"})
            if not NUM.match(amount) or not float(amount) > 0:
                return self.send(400, {"error": "monto invalido"})
            if not BUSY.acquire(blocking=False):
                return self.send(409, {"error": "ya hay una operacion en curso, espera a que termine"})
            jid, out = uuid.uuid4().hex[:8], b.get("dir") == "out"
            JOBS[jid] = {"status": "running", "log": []}
            slog("orden", f"Conversion de {amount} {token} -> BNB iniciada" if out else f"Conversion de {amount} BNB -> {token} iniciada")
            JOBS[jid]["log"].append(tr(f"{time.strftime('%H:%M:%S')}  " + (f"Conversion {token[:8]}… -> BNB iniciada" if out else f"Conversion BNB -> {token[:8]}… iniciada")))
            threading.Thread(target=run_unwrap if unwrap else run_swap, args=((jid, amount) if unwrap else (jid, token, amount, out)), daemon=True).start()
            return self.send(200, {"job": jid})
        if path == "/api/creator-fee":
            if not b.get("claim"):
                return self.send(200, creator_fee_view(token))
            if not BUSY.acquire(blocking=False):
                return self.send(409, {"error": "ya hay una operacion en curso, espera a que termine"})
            jid = uuid.uuid4().hex[:8]
            JOBS[jid] = {"status": "running", "log": []}
            threading.Thread(target=run_claim_fee, args=(jid, token), daemon=True).start()
            return self.send(200, {"job": jid})
        if path in ("/api/buy", "/api/sell"):
            kind, use_all = path[5:], bool(b.get("all"))
            if kind == "buy":
                if not NUM.match(amount) or not float(amount) > 0:
                    return self.send(400, {"error": "monto invalido"})
            if kind == "sell" and not use_all and not NUM.match(amount):
                return self.send(400, {"error": "monto invalido"})
            if not BUSY.acquire(blocking=False):
                return self.send(409, {"error": "ya hay una operacion en curso, espera a que termine"})
            note = clean_note(b.get("note"))
            jid = uuid.uuid4().hex[:8]
            JOBS[jid] = {"status": "running", "log": [tr(f"{time.strftime('%H:%M:%S')}  Orden de {'compra' if kind == 'buy' else 'venta'} iniciada")]}
            slog("orden", f"Orden de {'compra' if kind == 'buy' else 'venta'} iniciada: {token} ({'todo' if use_all else amount})" + (f" · con nota ({len(note)} car.)" if note else ""))
            EVENTS[jid] = {"set": threading.Event(), "ok": False}
            threading.Thread(target=run_order, args=(jid, kind, token, amount, use_all, note, str(b.get("quote", ""))[:64]), daemon=True).start()
            return self.send(200, {"job": jid})
        self.send(404, {"error": "no existe"})


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}"
    print(f"LobeliaPanel: {url}  (Ctrl+C to stop / para salir)")
    if not os.environ.get("LOB_NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        srv = ThreadingHTTPServer((HOST, PORT), H)
    except OSError:
        print(f"\nNo se pudo abrir el puerto {PORT}: probablemente YA hay otro server.py corriendo (version vieja).")
        print("Cierra esa ventana / proceso de Python y vuelve a ejecutar este archivo.")
        raise SystemExit(1)
    srv.serve_forever()
