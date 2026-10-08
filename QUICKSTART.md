# ZeoCore Quickstart

**Goal:** in about 10 minutes, go from an empty folder to your own working
capability printing `Hello, World!`.

You do not need to know ZeoCore, Pydantic, or virtual environments before
you start. Every command below can be copied and pasted as-is. Pick the tab
that matches your machine — **macOS / Linux** or **Windows** — and follow it
top to bottom.

This guide uses [`uv`](https://docs.astral.sh/uv/), a fast Python package
and version manager, for every install and run step. If something goes
wrong, jump to [Common errors](#common-errors) at the bottom. Most first-run
problems are on that list.

**Contents**

1. [Step 0: Install uv](#step-0-install-uv)
2. [Step 1: Check your Python version](#step-1-check-your-python-version)
3. [Step 2: Install Python 3.14 (only if you need it)](#step-2-install-python-314-only-if-you-need-it)
4. [Step 3: Make a project folder and a virtual environment](#step-3-make-a-project-folder-and-a-virtual-environment)
5. [Step 4: Install ZeoCore](#step-4-install-zeocore)
6. [Step 5: Write your first capability](#step-5-write-your-first-capability)
7. [Step 6: Run it](#step-6-run-it)
8. [What each part of that file does](#what-each-part-of-that-file-does)
9. [Try changing something](#try-changing-something)
10. [Common errors](#common-errors)
11. [Where to go next](#where-to-go-next)

---

## Step 0: Install uv

**macOS / Linux**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

macOS with [Homebrew](https://brew.sh) works too: `brew install uv`.

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close and reopen your terminal, then confirm it installed:

```bash
uv --version
```

## Step 1: Check your Python version

ZeoCore requires **Python 3.14 or newer**. Older versions will refuse to
install it. Check what you have:

**macOS / Linux**

```bash
python3 --version
```

**Windows (PowerShell)**

```powershell
py --version
```

If you see `Python 3.14.x` (or higher, like `3.15.x`), skip ahead to
[Step 3](#step-3-make-a-project-folder-and-a-virtual-environment).

If you see an older version such as `Python 3.11.9`, or a
"command not found" error, do Step 2 first.

## Step 2: Install Python 3.14 (only if you need it)

**Any platform, recommended** — since you already have `uv` from
[Step 0](#step-0-install-uv), let it fetch an interpreter for you without
touching your system Python:

```bash
uv python install 3.14
```

Re-run the check from Step 1. You should see `Python 3.14.x`.

**Alternative: install Python 3.14 yourself**, if you'd rather manage the
interpreter outside uv. Choose **one** of the options for your platform.

**macOS**

```bash
# Option A: Homebrew (https://brew.sh)
brew install python@3.14

# Option B: download the official installer from python.org
open https://www.python.org/downloads/
```

**Linux (Debian / Ubuntu)**

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.14 python3.14-venv
```

On Fedora: `sudo dnf install python3.14`. On Arch: `sudo pacman -S python`.

**Windows (PowerShell)**

```powershell
winget install Python.Python.3.14
```

Or download the installer from
[python.org/downloads](https://www.python.org/downloads/) and — this part
matters — tick **"Add python.exe to PATH"** on the first screen of the
installer.

Now close and reopen your terminal, then re-run the check from Step 1. You
should see `Python 3.14.x`.

## Step 3: Make a project folder and a virtual environment

A **virtual environment** is a private folder of Python packages that belongs
to one project. It keeps ZeoCore and its dependencies out of your system
Python, so nothing you install here can break another project. You create one
per project and "activate" it in each new terminal session.

**macOS / Linux**

```bash
mkdir zeocore-quickstart
cd zeocore-quickstart
uv venv --python 3.14
source .venv/bin/activate
```

**Windows (PowerShell)**

```powershell
mkdir zeocore-quickstart
cd zeocore-quickstart
uv venv --python 3.14
.venv\Scripts\Activate.ps1
```

**Windows (Command Prompt)**

```cmd
mkdir zeocore-quickstart
cd zeocore-quickstart
uv venv --python 3.14
.venv\Scripts\activate.bat
```

If you installed Python 3.14 yourself in Step 2 instead of letting uv fetch
it, the system interpreter also works:

```bash
python3.14 -m venv .venv          # Windows: py -3.14 -m venv .venv
source .venv/bin/activate         # Windows PowerShell: .venv\Scripts\Activate.ps1
```

Your prompt should now start with `(.venv)`. That prefix is how you know the
environment is active. Confirm the right interpreter is in charge:

```bash
python --version
```

Expected output:

```
Python 3.14.x
```

> **PowerShell blocked the activation script?** Run
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in the same
> window, then run the activate command again. That relaxes the policy for
> this one terminal session only.

## Step 4: Install ZeoCore

With the environment active (you still see `(.venv)`), install the package.
The same command works on every platform:

```bash
uv pip install zeocore
```

Note the two spellings, because they are easy to mix up:

- The **package** you install is `zeocore` (no underscore).
- The **module** you import in Python is `zeo_core` (with an underscore).

Verify the install:

```bash
python -c "import zeo_core; print(zeo_core.__version__)"
```

Expected output — a version number such as:

```
0.5.0
```

Integrations like Notion, Google Drive, or the HTTP and MCP adapters are
optional extras you install only when you need them, e.g.
`uv pip install "zeocore[notion]"`. You need none of them for this
quickstart. The full list is in the
[README's integrations table](README.md#optional-integrations).

## Step 5: Write your first capability

A **capability** is a single, typed, named unit of work: it takes a request,
does something, and returns a structured result.

Create a file called `hello_capability.py` in your project folder and paste
this in exactly as-is:

```python
"""My first ZeoCore capability."""

import logging
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from zeo_core.contracts import CapabilityExample, CapabilityResult, EffectKind
from zeo_core.core.fs import get_service as get_fs_service
from zeo_core.tools import ToolContext, bound_capability_of, capability, invoke_sync


class GreetRequest(BaseModel):
    """What the capability accepts."""

    name: str


class GreetResponse(BaseModel):
    """What the capability returns on success."""

    message: str


@capability(
    id="demo.greet@1.0.0",
    description="Greet a person by name.",
    effects={EffectKind.READ},
    examples=(
        CapabilityExample(
            request={"name": "World"},
            response={"message": "Hello, World!"},
        ),
    ),
)
def greet(request: GreetRequest, ctx: ToolContext) -> CapabilityResult[GreetResponse]:
    return CapabilityResult.ok(data=GreetResponse(message=f"Hello, {request.name}!"))


def main() -> None:
    with TemporaryDirectory() as tmp:
        ctx = ToolContext(
            run_id="quickstart-001",
            tool_name="greet",
            tool_version="1.0.0",
            logger=logging.getLogger("greet"),
            fs=get_fs_service(),
            work_dir=tmp,
            output_dir=tmp,
        )
        result = invoke_sync(bound_capability_of(greet), GreetRequest(name="World"), ctx)
        print("status:", result.status.value)
        print("message:", result.data.message)


if __name__ == "__main__":
    main()
```

## Step 6: Run it

```bash
python hello_capability.py
```

Expected output:

```
status: success
message: Hello, World!
```

That is a complete, typed, self-describing capability. The same object can
later be invoked by a runner, served over HTTP, exposed to an MCP-native
coding agent, or offered to an LLM as a callable tool — without changing the
function you just wrote.

## What each part of that file does

Read this section next to your file; each heading matches a piece of it.

### `GreetRequest` and `GreetResponse`

These are [Pydantic](https://docs.pydantic.dev/) models, and they are the
capability's contract. `GreetRequest` says "this capability needs a `name`
string." Pydantic enforces that at runtime: pass the wrong shape and you get
a clear validation error instead of a mysterious failure deep inside your
code. ZeoCore also generates JSON Schema from these models, which is how the
HTTP, MCP, and LLM adapters know how to call your capability without you
writing schema by hand.

### The `@capability(...)` decorator

The decorator attaches the metadata that makes the function discoverable and
callable by machines:

| Argument | What it means |
|---|---|
| `id` | The capability's identity, always `namespace.name@semver` — here, namespace `demo`, name `greet`, version `1.0.0`. |
| `description` | Human- and LLM-readable summary of what it does. |
| `effects` | A **declaration** of what kinds of side effects it has — `READ`, `WRITE`, `DELETE`, `EXTERNAL_COMMUNICATION`, `FINANCIAL`, `SECURITY_SENSITIVE`. It documents intent; it does not grant permission. |
| `examples` | At least one request/response pair. Required — an undocumented capability is not a finished capability. |

### The handler signature

```python
def greet(request: GreetRequest, ctx: ToolContext) -> CapabilityResult[GreetResponse]:
```

Every capability has this shape: a typed request in, a `ToolContext`, and a
`CapabilityResult` out. ZeoCore checks this signature when the decorator
runs, so mistakes surface at import time rather than in production.

### `ToolContext`

`ToolContext` is everything your capability is *allowed* to use from the
outside world: a run id, a logger, a filesystem service, working and output
directories, and any other services the caller wired in. Your capability
never reaches for ambient global state — it asks the context. In real
deployments a runner builds this object for you; here you build a small one
by hand so the script is self-contained. `TemporaryDirectory()` gives it
scratch directories that clean themselves up when the script exits.

### `CapabilityResult`

`CapabilityResult.ok(data=...)` returns a *structured* success. There are
matching constructors for the other things that really happen:

| Constructor | Use it when | Status |
|---|---|---|
| `.ok()` | The work succeeded. | success |
| `.skip()` | Policy said don't run this. | skipped |
| `.unavailable()` | A needed integration isn't configured. | skipped |
| `.fail()` / `.fail_from_exc()` | The work was attempted and failed. | error |

This is why you don't raise exceptions for expected failures: a caller
orchestrating fifty capabilities gets one shape to check every time
(`result.status`, plus the finer-grained `result.outcome`) instead of a
`try`/`except` matrix. Exceptions stay reserved for the genuinely
unexpected, and ZeoCore gives those typed classes (`ZeoError` and friends)
too.

### `bound_capability_of` and `invoke_sync`

`bound_capability_of(greet)` takes the decorated function and hands you the
`BoundCapability` — the function plus its definition — which is the object
registries and adapters pass around. `invoke_sync(cap, request, ctx)` runs
it through the full pipeline: validate the request, apply any guards, call
your handler, validate what comes back. Calling `greet(...)` directly would
skip all of that, so route invocations through `invoke_sync` (or
`invoke_async` for `async def` handlers).

## Try changing something

Small experiments, in increasing order of interest:

1. Change `GreetRequest(name="World")` to your own name and re-run.
2. Add a field: put `excited: bool = False` on `GreetRequest` and use it in
   the message. Notice the default keeps old callers working.
3. Delete the `examples=(...)` argument and re-run. Read the error — ZeoCore
   refuses to build an undocumented capability.
4. Make it fail on purpose. Return
   `CapabilityResult.fail(msg="name must not be empty", code="ZEO_VALIDATION_EMPTY_NAME")`
   when `request.name` is blank, and print `result.outcome.value` next to
   `result.status.value` to see the difference between the coarse status
   (`error`) and the fine-grained outcome (`integration_failure`). Two
   things to notice: error codes must start with `ZEO_`, and `result.data`
   is `None` on failure — so the last `print` needs a guard, such as
   `print(result.data.message if result.data else result.error.message)`.

## Common errors

| What you see | What it means | Fix |
|---|---|---|
| `ERROR: Package 'zeocore' requires a different Python` | Your interpreter is older than 3.14. | Redo [Step 2](#step-2-install-python-314-only-if-you-need-it), then rebuild the venv with the 3.14 interpreter. |
| `ModuleNotFoundError: No module named 'zeo_core'` | Either the venv isn't active, or you installed into a different Python. | Re-activate the venv ([Step 3](#step-3-make-a-project-folder-and-a-virtual-environment)) and re-run `uv pip install zeocore`. |
| `ModuleNotFoundError: No module named 'zeocore'` | You imported the package name instead of the module name. | Import `zeo_core`, with the underscore. |
| `.venv\Scripts\Activate.ps1 cannot be loaded because running scripts is disabled` | PowerShell's execution policy. | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then activate again. |
| `capability id must look like 'namespace.name@1.0.0', got 'greet'` | The `id` is missing its namespace or version. | Use the full three-part form, e.g. `demo.greet@1.0.0`. |
| `capability() missing 1 required keyword-only argument: 'examples'` | `examples=` was left off. | Add at least one `CapabilityExample`. |
| `Value error, at least one example is required` | `examples=()` was empty. | Put a real request/response pair in it. |
| `request parameter must be annotated with a Pydantic BaseModel` | The first parameter has no type annotation, or isn't a Pydantic model. | Annotate it: `def greet(request: GreetRequest, ...)`. |
| `return annotation must be CapabilityResult[ResponseModel]` | The return type is bare `CapabilityResult` or something else. | Parameterize it: `-> CapabilityResult[GreetResponse]`. |
| `1 validation error for GreetRequest / name: Field required` | You built the request without a required field. | Pass every required field, e.g. `GreetRequest(name="World")`. |
| `Value error, Error code must start with one of ('ZEO_', 'ZC_', 'QC_')` | `CapabilityResult.fail()` got an unnamespaced error code. | Use the `ZEO_<AREA>_<DETAIL>` form, e.g. `ZEO_IO_NOT_FOUND`. |
| `AttributeError: 'NoneType' object has no attribute 'message'` | You read `result.data` on a result that wasn't a success. | Check `result.status` (or `if result.data`) before touching `result.data`. |
| `5 validation errors for ToolContext` | `ToolContext` was built without its required fields. | It needs `run_id`, `tool_name`, `tool_version`, `logger`, `fs`, `work_dir`, and `output_dir`. |

Still stuck? Open an
[issue](https://github.com/zeroemployeeorg/zeocore/issues) with the command
you ran and the full error text.

## Where to go next

- [GET-STARTED.md](GET-STARTED.md) — the full manual: configuration, paths,
  filesystem, plugins, integrations, adapters, troubleshooting.
- [docs/README.md](docs/README.md) — the learning hub, with tutorials and a
  guided path through the examples.
- [docs/tutorials/capability-authoring.md](docs/tutorials/capability-authoring.md)
  — the natural next tutorial after this page: registries, guards, manifests,
  and adapter binding.
- [`examples/`](examples/) — every example is a real script you can run with
  `uv run examples/<name>.py`.
- [CONTRIBUTING.md](CONTRIBUTING.md) — set up a dev environment and send a
  change back.
