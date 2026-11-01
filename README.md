# 🌺 LobeliaPanel

**LobeliaPanel** is a lightweight local web panel for trading, creating tokens and interacting with **[gang.fun](https://gang.fun)** from a visual interface instead of typing CLI commands. It runs entirely on `127.0.0.1` (your own computer) and uses no browser extension.

🌐 **Official site:** [lobeliapanel.xyz](https://lobeliapanel.xyz)

> ⚠️ **Experimental software. Trading and launching tokens is high risk and you can lose everything you put in. This is not financial advice. Read the [Security & disclaimer](#-security--disclaimer) section before using it with real funds.**

---

## ✨ Features

- 🛒 **Buy, sell and convert** tokens from a clean interface. Each step (wrap, approve, trade) is previewed and confirmed before the next, and the panel stops if Binance flags a risk.
- 🔎 **Scanner:** set curve progress, 24 h volume and volume-to-market-cap filters. Matches are highlighted, with a sound alert when a new one appears.
- 📝 **Optional public note** on a buy or sell. It is published on gang.fun with the trade, so it is public and permanent. Not available for GangLaunchpad (V3) tokens.
- 🪙 **Token creation:** preview first, then confirm. Supports the launch types offered by gang.fun and an optional **initial buy** executed right after your token is created.
- 💰 **Creator fee claim:** check and claim the creator fee of a token you created.
- 📋 **Live token lists** from gang.fun, with balances for the tokens you hold.
- 🚦 **Risk warnings:** if a risk is detected, the panel stops and asks for explicit acknowledgment.
- 🌍 **Languages:** Spanish, English and Chinese (simplified and traditional).
- 🔒 **Local-only server:** written with the Python standard library, listens only on `127.0.0.1` and rejects requests whose `Host` header is not localhost.
- 🎨 **Dark glassmorphic UI.**

---

## 🧩 How it works

LobeliaPanel does **not** hold or ask for private keys. The panel is a thin local layer over two official command-line tools:

1. **`gang`** (gang.fun CLI) prepares each operation (quote, buy, sell, create...).
2. **`baw`** (Binance Agentic Wallet CLI) signs and sends it. Its keys are managed by Binance (MPC keyless), and the daily limits, token scope and approvals you configure **in the Binance app** apply to everything the panel does.

Because every step goes through those CLIs, speed depends on them (several seconds per step is normal). LobeliaPanel is **not** designed for sniping or racing bots.

---

## 📦 Requirements

- **Python 3.8+** (no extra packages needed).
- **Node.js**, to install the CLIs with `npm`.
- **Both CLIs and a wallet session:**
  ```bash
  npm i -g @binance/agentic-wallet
  npm i -g @gangfun/cli@latest
  baw auth signin
  ```
  See also the [official Agentic Wallet guide](https://developers.binance.com/en/docs/products/agentic-wallet/quickstart/install-agentic-wallet).
- A Binance Agentic Wallet with a **.ai Agent ID**: gang.fun only lets Agentic Wallets with an Agent ID buy on the bonding curve.
- In the Binance app, **Developer Mode** and **"Trade all tokens"** enabled for the agent wallet (the panel needs them to run contract calls and trade any token).
- A little **BNB** in that wallet for gas.

---

## 🚀 Quick start

### Option A: download the ZIP (no Git needed)

1. On this page click **Code → Download ZIP** (or use the download link on [lobeliapanel.xyz](https://lobeliapanel.xyz)).
2. Extract it into any folder.
3. Start the panel:
   - **Windows:** double-click `Start_LobeliaPanel.bat` if it is in your download, or run `python server.py`.
   - **Any system:** run `python server.py`.

### Option B: clone with Git

```bash
git clone https://github.com/lobelia018/lobelia-panel.git
cd lobelia-panel
python server.py
```

Your browser opens automatically at **http://127.0.0.1:8788**. If it doesn't, open that address manually, then press **Sign in / verify session**.

On Windows, if `python` is not recognized, try `py server.py`. The panel handles the `.cmd` wrappers that npm creates for `gang` and `baw` on Windows.

### Project files

| File | What it is |
|---|---|
| `server.py` | Local server: runs the CLIs in the background and serves the page |
| `LobeliaPanelApp.html` | The panel interface |

---

## 🛠️ Troubleshooting

- **"Your gang CLI is outdated..."**: run `npm i -g @gangfun/cli@latest` and try again. The panel refuses to send operations that an old CLI would execute incorrectly.
- **`gang` or `baw` not found**: make sure both are installed globally (`npm i -g ...`) and open a new terminal window.
- **Wallet not connected**: sign in again with `baw` and check the Binance app.
- **Port 8788 already in use**: close the other instance of the panel.
- **Operation in progress**: the panel runs one order at a time; wait until the current one ends.

---

## 🛡️ Security & disclaimer

- Review **every preview** (destination, amount, risks) before confirming. Never approve something you don't understand.
- In the Binance app, set a **low daily limit** for the agent wallet and keep only the balance you are willing to risk. Since the panel needs "Trade all tokens", that limit and the previews you confirm are your main protection.
- The panel only listens on `127.0.0.1`. **Don't expose it to your network or the internet.**
- Only download the panel from this repository or from [lobeliapanel.xyz](https://lobeliapanel.xyz). Be wary of copies, "pro" versions or anyone asking you for keys, seed phrases or remote access.
- This software has **not been audited** and is provided "as is", without warranty. You are solely responsible for what you do with it. Nothing here is financial advice.
- LobeliaPanel is an independent project and is **not affiliated with** gang.fun or Binance.

---

## 🐛 Issues and feedback

Found a bug or have an idea? Open an [issue](https://github.com/lobelia018/lobelia-panel/issues). Please do **not** include private data, wallet session information or full transaction details in public reports.
