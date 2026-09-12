Here's a complete guide to setting up and using **GoNB** on Ubuntu Linux with VSCode, Windows 11 with VSCode.

---

Yes. I checked your setup notes. They are specifically for **Go + GoNB + Jupyter \+ VS Code on Ubuntu**.  GitHub

 The cleanest setup is:

 **Ubuntu → Go → GoNB → Jupyter kernel → VS Code Jupyter extension → `.ipynb`**

 ## 1\. Install Go

```
sudo apt update
sudo apt install -y golang-go

go version
```

 If you already have Go, skip this.

 ## 2\. Install Python + Jupyter

 I'd recommend using a virtual environment rather than installing Jupyter globally:

```
sudo apt install -y python3 python3-pip python3-venv
```

 Create a Jupyter environment:

```
python3 -m venv ~/jupyter-venv
source ~/jupyter-venv/bin/activate
```

 Then:

```
pip install --upgrade pip
pip install jupyterlab notebook
```

 Verify:

```
jupyter --version
```

 ## 3\. Install GoNB

 GoNB is what allows a Jupyter notebook to execute Go code.

```
go install github.com/janpfeifer/gonb@latest
go install golang.org/x/tools/cmd/goimports@latest
go install golang.org/x/tools/gopls@latest
```

 Your binaries will normally be in:

```
~/go/bin
```

 Add that to your PATH:

```
echo 'export PATH=$PATH:$(go env GOPATH)/bin' >> ~/.bashrc
source ~/.bashrc
```

 Check:

```
which gonb
which goimports
which gopls
```

 You should get paths similar to:

```
/home/your-user/go/bin/gonb
/home/your-user/go/bin/goimports
/home/your-user/go/bin/gopls
```

 ## 4\. Register Go as a Jupyter kernel

 This is the important step:

```
gonb --install
```

 Check that Jupyter sees it:

```
jupyter kernelspec list
```

 You should see something containing:

```
gonb
```

 Your notes also use `gonb --install` to register the kernel.  GitHub

 ## 5\. Install VS Code extensions

 In VS Code:

 **Ctrl + Shift + X**

 Install:

 - **Python** — Microsoft
- **Jupyter** — Microsoft

 The Python and Jupyter extensions work together for `.ipynb` notebooks in VS Code.  Visual Studio Code+1

 You don't need to install a separate "Go Jupyter" VS Code extension.

 ## 6\. Open/create a notebook

 Create something like:

```
mkdir -p ~/go-notebooks
cd ~/go-notebooks
code .
```

 In VS Code:

 **Ctrl + Shift + P → `Create: New Jupyter Notebook`**

 Or create:

```
test.ipynb
```

 ## 7\. Select the GoNB kernel

 Open `test.ipynb`.

 At the **top-right**, click:

```
Select Kernel
```

 Then:

```
Select Another Kernel...
    ↓
Jupyter Kernels
    ↓
Go (gonb)
```

 VS Code's current notebook UI uses the kernel picker in the upper-right.  Visual Studio Code

 ## 8\. Test Go

 Put this in a notebook cell:

```
import "fmt"

%%
fmt.Println("Hello from Go + Jupyter!")
```

 Run the cell.

 You should get:

```
Hello from Go + Jupyter!
```

 The `%%` is GoNB-specific; it allows the cell to be executed as the body of a `func main()`.  GitHub

---

 # Important Ubuntu + VS Code PATH issue

 This is the annoying part from your notes.

 You may have:

```
which goimports
```

 working perfectly in your terminal, but GoNB inside VS Code may complain:

```
goimports: command not found
```

 Even though:

```
~/go/bin/goimports
```

 exists.

 If that happens, first try the simpler solution: **fully restart VS Code** after adding `~/go/bin` to your PATH.

 If it still doesn't work, your notes suggest exposing the Go binaries through `/usr/local/bin`:

```
sudo ln -s $(go env GOPATH)/bin/goimports /usr/local/bin/goimports
sudo ln -s $(go env GOPATH)/bin/gopls /usr/local/bin/gopls
sudo ln -s $(go env GOPATH)/bin/gonb /usr/local/bin/gonb
```

 Then:

```
which goimports
```

 should show:

```
/usr/local/bin/goimports
```

 Restart the VS Code Jupyter kernel afterward. Your notes specifically identify this as a common GoNB/VS Code PATH issue.  GitHub

 ## 9\. The setup in one shot

 If you're starting from a relatively clean Ubuntu installation, this is essentially what you need:

```
sudo apt update
sudo apt install -y golang-go python3 python3-pip python3-venv

python3 -m venv ~/jupyter-venv
source ~/jupyter-venv/bin/activate

pip install --upgrade pip
pip install jupyterlab notebook

go install github.com/janpfeifer/gonb@latest
go install golang.org/x/tools/cmd/goimports@latest
go install golang.org/x/tools/gopls@latest

echo 'export PATH=$PATH:$(go env GOPATH)/bin' >> ~/.bashrc
source ~/.bashrc

gonb --install

jupyter kernelspec list
```

 Then open VS Code, install **Python + Jupyter**, open `.ipynb`, and select:

```
Select Kernel
 → Jupyter Kernels
 → Go (gonb)
```

 That's all you need for **Go notebooks in VS Code on Ubuntu**. VS Code officially supports Jupyter notebooks through its Jupyter extension and lets you select installed kernels from the notebook kernel picker.  Visual Studio Code+1

 One caveat from your notes: **GoNB's `gopls`-powered completion/contextual help works better in JupyterLab than VS Code**, so if your primary goal is DSA practice with Go notebooks, VS Code is fine for execution/editing, but JupyterLab may give you a better Go notebook experience.  GitHub

 Yes — that points to a **VS Code environment/PATH issue**, not a GoNB installation issue.

 Since the same notebook works in the browser, your GoNB kernel is basically fine. VS Code's Jupyter process simply can't find `goimports`.

 ### 1\. First check from the VS Code terminal

 In VS Code, open **Terminal → New Terminal** and run:

```
which go
which goimports
go env GOPATH
ls "$(go env GOPATH)/bin/goimports"
```

 If the last command shows something like:

```
/home/yourname/go/bin/goimports
```

 but:

```
which goimports
```

 returns nothing, fix your PATH.

 ### 2. Add Go's bin directory to PATH

 Run:

```
echo 'export PATH="$PATH:$(go env GOPATH)/bin"' >> ~/.bashrc
source ~/.bashrc
```

 Then verify:

```
which goimports
goimports -h
```

 You should see:

```
/home/yourname/go/bin/goimports
```

 ### 3\. Restart VS Code completely

 This is important.

 Close **all VS Code windows**, then start VS Code again:

```
code .
```

 Then reopen your `.ipynb` and restart the kernel.

---

 ### 4\. If it still says `goimports` isn't installed

 Install it explicitly:

```
go install golang.org/x/tools/cmd/goimports@latest
```

 Then:

```
ls "$(go env GOPATH)/bin/goimports"
```

 If it exists but VS Code still cannot see it, make it available system-wide:

```
sudo ln -sf "$(go env GOPATH)/bin/goimports" /usr/local/bin/goimports
```

 Verify:

```
which goimports
```

 Expected:

```
/usr/local/bin/goimports
```

 Then **restart VS Code and restart the Jupyter kernel**.

 ### 5\. One more thing about your code

 Your code:

```
import "fmt"

func main() {
    fmt.Println("Hi")
}
```

 is valid Go, but in a **GoNB notebook cell**, you normally don't need `main()`.

 Try simply:

```
import "fmt"

fmt.Println("Hi")
```

 Or, if you're using GoNB's cell syntax, the notebook can handle the surrounding execution for you.

 The key issue, though, is **`goimports` not being visible to the VS Code-launched kernel**. Since browser Jupyter works, I would **not reinstall Jupyter or GoNB yet**.

 If you paste the output of these four commands from the **VS Code terminal**:

```
which go
which goimports
go env GOPATH
echo $PATH
```

 I can tell you exactly what's wrong.

 Either is fine for **`goimports`**. Your `goimports` is installed correctly:

```
/home/iamdreamer/go/bin/goimports
```

 And this:

```
goimports -h
```

 working confirms it.

 The `(govenv)` at the beginning is just your **Python virtual environment**. It does **not** mean Go is installed inside `govenv`.

 ### For your current problem

 You are currently here:

```
~/Documents/govenv
```

 That's completely fine. The important part is that:

```
which goimports
```

 returns:

```
/home/iamdreamer/go/bin/goimports
```

 ### Now test VS Code specifically

 **Close VS Code completely**, then from the same terminal run:

```
cd ~/Documents/govenv
code .
```

 Open your notebook and restart the kernel:

 **Kernel → Restart Kernel**

 Then run:

```
import "fmt"

fmt.Println("Hi")
```

 If it **still** says:

 > Program goimports is not installed

 then the issue is that the Jupyter kernel launched by VS Code isn't inheriting your shell's PATH.

 In that case, run this:

```
sudo ln -sf /home/iamdreamer/go/bin/goimports /usr/local/bin/goimports
```

 Then verify:

```
which goimports
```

 and restart VS Code again.

 **You don't need to move out of `govenv`.** Your Python `govenv` and Go's `~/go/bin` are independent.

## 🐧 Step 1 — Full Ubuntu Setup

### 1. Install Go (if not already installed)

```bash
sudo apt update && sudo apt install -y golang-go
# Or install the latest version manually:
# https://go.dev/dl/
```

Verify: `go version`

### 2. Install Jupyter

```bash
sudo apt install -y python3-pip
pip3 install jupyterlab notebook
```

### 3. Install GoNB + required tools

Run the following to install GoNB, `goimports`, and `gopls` (needed for auto-complete) into your `GOBIN` directory:

```bash
go install github.com/janpfeifer/gonb@latest && \
  go install golang.org/x/tools/cmd/goimports@latest && \
  go install golang.org/x/tools/gopls@latest
```

### 4. Add GOBIN to your PATH

```bash
# Add this to your ~/.bashrc or ~/.zshrc:
export PATH=$PATH:$(go env GOPATH)/bin

# Then reload:
source ~/.bashrc
```

### 5. Register GoNB as a Jupyter kernel

```bash
gonb --install
```

This registers GoNB for both JupyterLab and classic Jupyter Notebook.

### 6. Start Jupyter

```bash
jupyter lab
# or
jupyter notebook
```

---

## 📓 Step 2 — Running Go Code in a Notebook

Once Jupyter is open, create a new notebook and select the **Go (gonb)** kernel.

The key special syntax is `%%` — it is a special GoNB command that means "insert a `func main() {...}` here." Without it, you're just writing package-level declarations.

**Basic example cell:**

```go
import "fmt"

%%
fmt.Println("Hello from Go in Jupyter!")
```

**Other useful cell magic commands:**

| Command | What it does |
|---|---|
| `%%` | Wraps code in `func main()` and runs it |
| `%help` | Shows all available GoNB commands |
| `!<cmd>` | Run a shell command (e.g. `!go get ...`) |
| `%test` | Runs `Test*` / `Benchmark*` functions with `go test` |

---

## 🖥️ Step 3 — Selecting the GoNB Kernel in VSCode

### Prerequisites
Install the **Jupyter extension** in VSCode (by Microsoft).

### How to select the kernel:

VSCode can open GoNB notebooks as a normal tab. When opening a `.ipynb` file it will ask which kernel to use, and if GoNB is installed it will offer it as an option.

1. Open (or create) a `.ipynb` file in VSCode
2. Click the **kernel selector** in the top-right corner of the notebook (it may say "Select Kernel" or show a Python version)
3. Choose **"Jupyter Kernel..."** → then pick **Go (gonb)** from the list
4. If it doesn't appear, make sure `gonb --install` was run and restart VSCode

### ⚠️ VSCode Caveats to Know

VSCode doesn't use Jupyter's completion protocol, so it won't offer GoNB's auto-complete or contextual help (which is powered by `gopls`). For that, use JupyterLab in the browser instead.

VSCode also does not render the `text/javascript` mimetype properly — it just displays it as text — so widget-based UI and some Plotly versions won't work in VSCode.

---

## 🐳 Alternative: Docker (quickest way to try it)

GoNB offers a pre-built Docker image with JupyterLab included. Run it like this:

```bash
docker pull janpfeifer/gonb_jupyterlab:latest
docker run -it --rm -p 8888:8888 -v "${PWD}":/notebooks janpfeifer/gonb_jupyterlab:latest
```

Then open the URL it prints in your browser — no installation needed.

---

## Summary Checklist

- [x] Go installed and `GOPATH/bin` in `PATH`
- [x] `gonb`, `goimports`, `gopls` installed via `go install`
- [x] `gonb --install` run to register the kernel
- [x] Jupyter started fresh after install
- [x] Use `%%` in cells to execute code
- [x] In VSCode, select kernel from top-right → "Go (gonb)"

Classic PATH issue. The binary is installed fine — it's just not visible to your shell. Here's the exact fix:

## The Root Cause

`go install` drops binaries into `$(go env GOPATH)/bin` (typically `~/go/bin`), but that directory isn't in your shell's `$PATH` by default.

## Fix

**Step 1 — Install goimports (if not already done)**
```bash
go install golang.org/x/tools/cmd/goimports@latest
```

**Step 2 — Verify the binary exists**
```bash
ls $(go env GOPATH)/bin/goimports
```

**Step 3 — Add GOPATH/bin to PATH permanently**
```bash
echo 'export PATH=$PATH:$(go env GOPATH)/bin' >> ~/.bashrc
source ~/.bashrc
```

If you're on zsh:
```bash
echo 'export PATH=$PATH:$(go env GOPATH)/bin' >> ~/.zshrc
source ~/.zshrc
```

**Step 4 — Verify**
```bash
which goimports
goimports -h
```

---

## For Jupyter/GoNB specifically

GoNB inherits the PATH from the shell that *launched* Jupyter. So if you added the export after Jupyter was already running, kill it and restart:

```bash
# Kill existing jupyter
jupyter notebook stop
# or just Ctrl+C the process

# Restart fresh
jupyter lab
```

The PATH fix needs to be in place **before** Jupyter starts — that's why the `!go install ...` magic in the notebook works (it installs at runtime) but the binary still isn't found on the *next* cell unless GoNB re-scans PATH.

---

Your setup notes already document this correctly under Step 4 — it's just easy to miss that `source ~/.bashrc` only affects the current terminal session, not an already-running Jupyter process.

The problem is clear — you installed `jupyterlab` inside a **virtualenv** (`~/Documents/venv`) but you're running `jupyter` outside of it, so the system can't find the command.

## Fix

**Step 1 — Activate the venv first**
```bash
source ~/Documents/venv/bin/activate
```

Your prompt will change to show `(venv)` prefix.

**Step 2 — Verify jupyter is now found**
```bash
which jupyter
# should show: /home/iamdreamer/Documents/venv/bin/jupyter
```

**Step 3 — Launch**
```bash
jupyter lab
```

---

## Make it permanent (so you don't forget to activate)

Since you always want this venv active for Jupyter work, add an alias to your `~/.bashrc`:

```bash
echo 'alias jlab="source ~/Documents/venv/bin/activate && jupyter lab"' >> ~/.bashrc
source ~/.bashrc
```

Then just type `jlab` to activate + launch in one shot.

---

## Also — reinstall goimports PATH fix with venv active

Once the venv is active and Jupyter is running, the GoNB kernel still needs `goimports` in PATH. Make sure your `~/.bashrc` has:

```bash
export PATH=$PATH:$(go env GOPATH)/bin
```

Then kill and relaunch Jupyter **from the same terminal** so it inherits the updated PATH. The order matters:

```bash
# 1. export PATH (already in .bashrc after sourcing)
source ~/.bashrc

# 2. activate venv
source ~/Documents/venv/bin/activate

# 3. launch
jupyter lab
```

VSCode needs to connect to your already-running Jupyter server manually. Here's the exact steps:

## Connect VSCode to your running Jupyter server

**Step 1 — Get your server URL + token**

In the terminal where Jupyter is running, look for a line like:
```
http://localhost:8889/lab?token=abc123xyz...
```

Or run this in a **new terminal** (with venv active):
```bash
source ~/Documents/venv/bin/activate
jupyter server list
```

Copy the full URL including the token.

---

**Step 2 — Open your `.ipynb` in VSCode, then select kernel**

1. Open the `.ipynb` file in VSCode
2. Click the **kernel selector** (top-right corner — says "Select Kernel" or shows a kernel name)
3. Choose **"Existing Jupyter Server..."**
4. Paste the full URL with token:
   ```
   http://localhost:8889/?token=your_token_here
   ```
5. Press Enter → give it a name (e.g. `local-gonb`) → select **Go (gonb)** from the kernel list

---

## If "Existing Jupyter Server" option doesn't appear

You're missing the **Jupyter extension**. Install it:

1. In VSCode → Extensions (`Ctrl+Shift+X`)
2. Search: `Jupyter` → install the one by **Microsoft**
3. Also install: `Jupyter Keymap` and `Jupyter Notebook Renderers`

Then repeat Step 2.

---

## Verify it's working

Once connected, run a test cell:
```go
import "fmt"
%%
fmt.Println("VSCode + GoNB working!")
```

If goimports error still appears inside VSCode's kernel, run this once in a notebook cell:
```bash
!export PATH=$PATH:$(go env GOPATH)/bin && which goimports
```

The root cause: VSCode spawns the GoNB kernel with its **own isolated environment** — it doesn't source `~/.bashrc`, so your `GOPATH/bin` export is never applied.

## The Permanent Fix — Symlink to system PATH

```bash
sudo ln -s $(go env GOPATH)/bin/goimports /usr/local/bin/goimports
sudo ln -s $(go env GOPATH)/bin/gopls /usr/local/bin/gopls
sudo ln -s $(go env GOPATH)/bin/gonb /usr/local/bin/gonb
```

`/usr/local/bin` is always in PATH — even in VSCode's isolated kernel environment. Verify:

```bash
which goimports
# /usr/local/bin/goimports
```

Then **restart the kernel** in VSCode (`Ctrl+Shift+P` → "Jupyter: Restart Kernel") and run a cell. The error will be gone.

---

## Why this works over other approaches

| Approach | Why it fails in VSCode |
|---|---|
| `export PATH=...` in `~/.bashrc` | VSCode doesn't source bashrc for kernel env |
| `!export PATH=...` in notebook cell | Only affects that one shell subprocess |
| `go install` in notebook cell | Installs binary but PATH still not updated |
| **Symlink to `/usr/local/bin`** | ✅ Always in PATH, no env needed |

---

## Optional — Also fix VSCode's env directly

If you want a VSCode-native solution alongside, add this to your VSCode `settings.json` (`Ctrl+Shift+P` → "Open User Settings JSON"):

```json
{
  "terminal.integrated.env.linux": {
    "PATH": "${env:PATH}:/home/iamdreamer/go/bin"
  },
  "jupyter.kernels.trusted": ["gonb"]
}
```

But the symlink approach above is simpler and more reliable.

Clean. The symlink approach would have worked too, but the VSCode env injection is more explicit and easier to maintain — you can see exactly what's happening.

**Quick summary of what fixed it:**

VSCode's Jupyter kernel spawns in a sandboxed environment that skips `~/.bashrc`. The `terminal.integrated.env.linux` setting injects your `~/go/bin` directly into that environment at launch, so GoNB finds `goimports` when the kernel starts.

---

Update your setup notes with this — it's a non-obvious gotcha that'll save you time on any future machine setup:

```markdown
## VSCode + GoNB PATH fix

VSCode does not source ~/.bashrc for Jupyter kernels.
Add to settings.json (Ctrl+Shift+P → "Open User Settings JSON"):

{
  "terminal.integrated.env.linux": {
    "PATH": "${env:PATH}:/home/YOUR_USER/go/bin"
  }
}

Without this, goimports/gopls will not be found even if installed.
```

You're good to go now — GoNB + VSCode fully operational. Back to DSA.

Here's an audit of everything done — what's needed vs what's not:

## Can be removed

**1. `notebook` package (classic Jupyter — you don't use it)**
```bash
source ~/Documents/venv/bin/activate
pip uninstall notebook -y
```

**2. `"jupyter.kernels.trusted": ["gonb"]` in VSCode settings.json**

Not a real/standard VSCode setting. Remove it — only keep:
```json
{
  "terminal.integrated.env.linux": {
    "PATH": "${env:PATH}:/home/iamdreamer/go/bin"
  }
}
```

**3. `gopls`** (optional — remove if you don't need Go autocomplete)

VSCode doesn't use GoNB's gopls at all (your own notes say this). Only useful in JupyterLab browser.
```bash
rm $(go env GOPATH)/bin/gopls
```

---

## What you actually need (minimal working set)

| Component | Why |
|---|---|
| Go | obvious |
| `gonb` | the Go kernel itself |
| `goimports` | required by gonb to auto-import packages |
| `gonb --install` | registers kernel with Jupyter |
| Python + `jupyterlab` (in venv) | backend server VSCode connects to |
| VSCode Jupyter extension | renders notebooks in VSCode |
| VSCode `settings.json` PATH fix | makes goimports visible to kernel |

---

## The venv — keep it

It's not unwanted. Installing `jupyterlab` globally with `pip3` on Ubuntu can conflict with system Python packages. The venv isolates it cleanly. Just remember to always activate before starting Jupyter:

```bash
source ~/Documents/venv/bin/activate && jupyter lab
```

Or use the alias from earlier: `jlab`

---

Here's the Windows equivalent of that GoNB + Jupyter + VSCode setup:

## Step 1 — Install Go

Download and run the installer from [go.dev/dl](https://go.dev/dl/) (pick the `.msi`), or via `winget`:

```powershell
winget install GoLang.Go
```

Verify (open a **new** PowerShell so PATH refreshes):
```powershell
go version
```

## Step 2 — Install Python + Jupyter

Install Python from [python.org](https://python.org) or `winget install Python.Python.3.12`, then:

```powershell
python -m venv $HOME\venv
$HOME\venv\Scripts\activate
pip install jupyterlab notebook
```

(Same venv-isolation reasoning as the Linux guide — keeps it out of your system Python.)

## Step 3 — Install GoNB + tools

```powershell
go install github.com/janpfeifer/gonb@latest
go install golang.org/x/tools/cmd/goimports@latest
go install golang.org/x/tools/gopls@latest
```

## Step 4 — Add GOBIN to PATH

`go install` puts binaries in `%USERPROFILE%\go\bin` on Windows. Add it permanently:

```powershell
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";$env:USERPROFILE\go\bin", "User")
```

Close and reopen your terminal, then verify:
```powershell
where goimports
```

## Step 5 — Register the GoNB kernel

```powershell
gonb --install
```

## Step 6 — VSCode setup

1. Install the **Jupyter** extension (Microsoft) in VSCode.
2. Open/create a `.ipynb` file → click kernel selector (top-right) → **Jupyter Kernel...** → **Go (gonb)**.

## The Windows-specific gotcha

Same root cause as Linux: **VSCode's Jupyter kernel doesn't inherit your interactive shell's PATH updates** in some launch contexts (especially if VSCode was already running when you set the PATH). Fix it explicitly in `settings.json`:

```json
{
  "terminal.integrated.env.windows": {
    "PATH": "${env:PATH};${env:USERPROFILE}\\go\\bin"
  }
}
```

Then fully restart VSCode (not just reload window) so it picks up the new PATH, and restart the kernel (`Ctrl+Shift+P` → "Jupyter: Restart Kernel").

## Key differences from the Ubuntu version

| Linux | Windows |
|---|---|
| `~/go/bin` | `%USERPROFILE%\go\bin` |
| `~/.bashrc` export | `[Environment]::SetEnvironmentVariable(...)` or System Properties → Environment Variables |
| `sudo ln -s ... /usr/local/bin` | No real equivalent — just make sure PATH is set at the **User** or **System** level, not just inside one terminal session |
| `terminal.integrated.env.linux` | `terminal.integrated.env.windows` |
| `source venv/bin/activate` | `venv\Scripts\activate` |

One more Windows-only wrinkle: if you're running PowerShell with an execution policy restriction, `venv\Scripts\Activate.ps1` may get blocked. If so:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

