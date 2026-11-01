# Using the Agent Dev Loop with GitHub Copilot CLI

> **Prototype.** Tested with a stand-in agent, not yet against live Copilot CLI
> sessions. The guard is a guardrail, not a security boundary. Expect rough edges,
> and read [What to look out for](#what-to-look-out-for) before the first run.

This guide is for running the loop on a machine where Copilot CLI (`copilot` in a
terminal) is the agent tool. It assumes nothing else about the machine.

- [1. What you need](#1-what-you-need)
- [2. Install the `loop` command](#2-install-the-loop-command)
- [3. Check the guard before anything else](#3-check-the-guard-before-anything-else)
- [4. Set up a repo](#4-set-up-a-repo)
- [5. Run a demand, command by command](#5-run-a-demand-command-by-command)
- [What to look out for](#what-to-look-out-for)
- [Troubleshooting](#troubleshooting)
- [A first test session (about an hour)](#a-first-test-session-about-an-hour)

---

## 1. What you need

| | Why | Check |
|---|---|---|
| Python 3.11 or newer | `loop` is a Python program | `python3 --version` (Windows: `python --version`) |
| git | demands live in branches and worktrees | `git --version` |
| Copilot CLI, signed in | runs the agents | `copilot --version` |
| PyYAML | the only package `loop` needs | installed with the wheel, see below |
| pytest and pytest-bdd **in the project** | the default way scenarios are tested | `python -m pytest --version` in the project |

**Windows:** WSL is the smoothest place to run this. On native Windows it can work,
but application control (AppLocker or WDAC) may block the `loop.exe` launcher that
installing creates. See [the Windows notes](#on-native-windows).

---

## 2. Install the `loop` command

No `git clone` and no GitHub login are needed. Download the wheel from the
repository's **Releases** page: `devloop-<version>-py3-none-any.whl`. It's a zip of
plain `.py` and text files, with nothing compiled and nothing executable inside.

```bash
# WSL or Linux
python3 -m pip install --user devloop-0.0.1-py3-none-any.whl
# or, if you use uv:
uv tool install ./devloop-0.0.1-py3-none-any.whl
```

```powershell
# Native Windows
python -m pip install --user devloop-0.0.1-py3-none-any.whl
```

If PyPI is blocked, install PyYAML first from its own wheel. It also works in pure
Python: `pip install --no-binary pyyaml pyyaml`.

Check it: `loop --help`. If `loop` isn't found or is blocked, every command also works
as `python -m devloop ...` (`python3` on Linux). The [hook](#hook-command-blocked-or-not-found)
needs the same treatment.

`loop` makes no network calls. The report pages it writes load their diagram library
from `cdn.jsdelivr.net` when you open them in a browser. If that host is blocked, the
diagrams don't draw, but the `.mmd` files next to the report still hold them.

---

## 3. Check the guard before anything else

Copilot runs `loop guard` before **every** tool call. If that command fails, Copilot
denies the call. So if the guard can't run, nothing can run. Test it once by hand,
in any git repo where you've run `loop install`:

```bash
echo '{"toolName":"bash","toolArgs":"{\"command\":\"git status\"}","cwd":"'"$PWD"'"}' | loop guard; echo $?
# expect: 0

echo '{"toolName":"bash","toolArgs":"{\"command\":\"git push --force\"}","cwd":"'"$PWD"'"}' | loop guard; echo $?
# expect: Blocked by the loop guard: 'git push --force' is denied by loop/tools.toml.
#         2

echo '{"toolName":"create","toolArgs":"{\"path\":\"tests/acceptance/x.py\"}","cwd":"'"$PWD"'"}' \
  | LOOP_ROLE=builder LOOP_ROOT="$PWD" LOOP_DEMAND=0001-x loop guard; echo $?
# expect: Blocked ... The builder may not write tests/acceptance/x.py ...
#         2
```

Each call adds a line to `.loop/commands.log`. That log is how you'll confirm, later,
that the guard really sees Copilot's tool calls.

---

## 4. Set up a repo

Start on the truth branch with nothing uncommitted.

```bash
cd your-repo
git switch main && git status
loop install --harness copilot
```

Install **only** the Copilot harness. Copilot CLI also reads Claude Code's hook files,
so installing both makes the guard run twice per call. That's harmless but noisy.

`loop install` writes:

| File | What it is |
|---|---|
| `loop/principles.md` | P1–P9, read by every agent first |
| `loop/conventions.yaml` | the repo's settings (edit this next) |
| `loop/tools.toml` | which shell commands agents may run |
| `loop/worktree.toml` | files to copy and commands to run in new worktrees |
| `loop/context.yaml` | lasting project facts, added only with your approval |
| `.github/agents/loop-*.agent.md` | the five agents, selected with `copilot --agent loop-<role>` |
| `.github/hooks/loop-guard.json` | the hook that runs `loop guard` |
| `AGENTS.md`, `.gitignore` | a pointer to the principles; `.loop/` is ignored |

It never overwrites files you wrote. It does rewrite the generated `.github/` files on
each run.

### Edit `loop/conventions.yaml`

```yaml
truth_branch: main
harness: copilot                 # the default is claude: change this
worktree_root: ".loop/worktrees" # recommended for Copilot, see below
include: [src/, config/]         # folders (end with /) and files to scan; leave out to detect
test_command: [python, -m, pytest, -q, -p, "no:warnings", tests/acceptance, "--junitxml={junit}"]
```

- **`harness: copilot`** makes `loop plan`, `build`, `review` and `run` start Copilot.
- **`worktree_root: ".loop/worktrees"`**: Copilot asks you to trust the folder a session
  starts in, and trust covers that folder and everything below it. The default puts
  worktrees next to the repo (`../<repo>-worktrees/`), outside the trusted folder,
  so every new demand could trigger a trust question, which an unattended session
  can't answer. Inside the repo, trusting the repo once covers them.
- **`test_command`**: if the project uses uv, use
  `[uv, run, pytest, -q, tests/acceptance, "--junitxml={junit}"]`. On Windows, `python`
  is usually right; in WSL, use `python3` if `python` doesn't exist.

### Edit `loop/tools.toml` if needed

Agents running on their own can only run commands listed under `allow`. Anything
unlisted, or under `ask`, is blocked for them. If your project's checks use other
commands (`make test`, `npm run lint`, a PowerShell cmdlet), add them to `allow`.

### Prepare the project's tests

Add `pytest-bdd` to the project's dev dependencies and this to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
bdd_features_base_dir = "loop/behaviour"
```

### Trust the repo in Copilot once

Run `copilot` in the repo root and, when asked, trust the folder **for future
sessions**. Then exit (`/exit` or Ctrl+C twice).

### Map the repo and commit

```bash
loop onboard
```

This writes a draft file per component in `loop/components/` and a map at
`.loop/onboard/report.html`. Open it (`explorer.exe .loop/onboard/report.html` from WSL).
For each component file: write the `responsibility`, check `boundary`, tighten
`may_depend_on` (it starts as whatever the code imports today), then delete
`uncertain: true`.

**Commit all of it to the truth branch:**

```bash
git add -A && git commit -m "loop: onboard"
```

This matters: each demand's worktree branches from the truth branch, so the agents,
the hook and the conventions must be committed there, or the worktree won't have them.

---

## 5. Run a demand, command by command

Every command prints what it does with a `[loop]` prefix. All demand references accept
the number (`0001`) or the full ID (`0001-dedupe-orders`).

### `loop new "<title>"`: add a demand

- Creates `loop/demands/0001-<title>/demand.md` and `status.yaml` (state `new`).
- **Commits to the truth branch** ("loop: new demand 0001-…").
- Next: describe the change in `demand.md`, in words, pasted images or both.
  Commit it too.

### `loop status`: see the queue

Read-only. One line per demand: ID, state, who claimed it, base commit.

### `loop plan 0001`: stage 1, live with you

- Claims the demand if it's new: status becomes `claimed` (committed to the truth
  branch), a branch `demand/0001-…` and a worktree at `.loop/worktrees/0001-…` are
  created, files listed in `worktree.toml` are copied, and its setup commands run.
- Writes `.loop/session.yaml` in the worktree (role, demand, base) and starts
  **`copilot --agent loop-planner`** in the worktree, interactively.
- **Copilot doesn't get a first message from `loop`.** Type something like "start".
  The planner's instructions tell it to read `.loop/session.yaml` and begin.
- **What to expect from the planner:**
  - It reads the principles, the demand and the related scenarios, then runs
    `loop views 0001` to see the map.
  - It asks in **rounds of numbered questions, each with a recommended answer**.
    Answering "yes" accepts the recommendation.
  - It writes `grilling.md`, scenarios in `loop/behaviour/*.feature` and `plan.yaml`.
  - It asks before saving any lasting fact to `loop/context.yaml`.
  - If the demand is too vague, it writes `returned.md` and asks you to run
    `loop state 0001 returned`.
- Copilot asks you to approve tool calls as usual. The guard also blocks the planner
  from writing anything outside its demand folder, `loop/behaviour/` and
  `context.yaml`. Blocked calls show "Blocked by the loop guard: …".
- When you exit Copilot, `loop` redraws the stage 1 views:
  `.loop/worktrees/0001-…/.loop/0001-…/report.html`, with the system map, the change
  view, the data view if tables are touched, and the scenarios.

### `loop views 0001`: redraw the views

Re-reads the code and index files and rewrites the report and `.mmd` files. It runs
no agent and changes nothing in git. Use it after editing a plan or scenarios by hand.

### `loop approve 0001`: fix the plan as the contract

- Commits anything uncommitted in the worktree ("loop: 0001 stage 1 plan").
- Rebases the demand branch onto the latest truth branch. **If that conflicts, it
  stops** and tells you to resolve it in the worktree.
- Records the base commit and sets state `approved` (committed to the truth branch).

### `loop build 0001`: stage 2

Runs two **unattended** Copilot sessions in the worktree, one after the other:

1. `copilot -p "…" --agent loop-outside-test-writer --allow-all-tools --no-ask-user -s`
2. the same with `--agent loop-builder`

(plus `loop-inside-test-writer` if `inside_tests: true`).

- `-s` means you see little while they work, only each agent's final message.
- `--allow-all-tools` means Copilot won't ask you anything. The guard is what limits
  them: each may only write in its own folders, and only `allow` commands run.
- After each session, `loop` commits whatever the agent left uncommitted
  ("loop: 0001 builder (round 1)").
- If the builder can't finish without breaking the plan, it writes `build.yaml` with
  `status: blocked` and a question. Stage 3 then stops for you.

### `loop review 0001`: stage 3

1. **Measures, with no AI:** reads the code, compares it with the approved base, runs
   the tests, counts changed lines, and sorts every finding into **back to stage 2**,
   **stop for you** or **carry on, noted**.
2. Runs `copilot -p … --agent loop-reviewer`. It never edits code, and only writes
   captions and real gaps to `.loop/0001-…/review.yaml`.
3. Measures again with the reviewer's notes merged, and writes into the worktree's
   `.loop/0001-…/`: `report.html`, `pr.md`, `findings.yaml` and the `.mmd` views.
4. Prints each finding and the **verdict**: `clean`, `back` or `stop`.

### `loop run 0001`: stages 2 and 3 together

Build, then review, then:

- **clean or stop:** it ends and prints the report's path.
- **back:** it sends the work back to whoever needs to fix it (the test writer for a
  missing test, the builder for the rest), then reviews again, up to `max_back_rounds`
  (3) times. After that it hands over to you.

Expect several Copilot sessions per run: three in the first round (test writer,
builder, reviewer), then two or three per round sent back, up to twelve in all. On a
plan with limited premium requests, `loop build` followed by `loop review` gives you
more control.

### After a clean review: the PR, then close

`loop` doesn't push and doesn't open PRs. `git push` is on the `ask` list, so
unattended agents can't push either. From the worktree:

```bash
cd .loop/worktrees/0001-*/
git push -u origin demand/0001-*
```

Open the PR and paste `.loop/0001-…/pr.md` as its description. It includes the views
as Mermaid, which GitHub draws. After it's merged:

```bash
loop close 0001 --merged <merge-commit-or-PR>   # removes the worktree, keeps the branch
```

### Other lifecycle commands

| Command | Effect |
|---|---|
| `loop state 0001 returned --reason "…"` | the demand waits for whoever raised it |
| `loop state 0001 rejected --reason "…"` | ends the demand |
| `loop reset 0001` | back to `new`: **deletes the worktree and branch**, drafts included |
| `loop claim 0001` | just claim and create the worktree, without starting the planner |

---

## What to look out for

1. **If the hook command can't run, every tool call is denied.** Copilot treats a failing
   hook as "deny". Do the [guard check](#3-check-the-guard-before-anything-else) first,
   in the same kind of terminal Copilot will run in.
2. **A slow hook is allowed through.** Hook timeouts (30 s here) fail open. The guard
   normally answers in well under a second.
3. **Commit the loop files to the truth branch before claiming.** Worktrees only contain
   what's committed there: agents, hook, conventions and component files.
4. **Unattended means the guard is the only gate.** `--allow-all-tools` turns off Copilot's
   own prompts. The guard stops writes outside a role's folders and unlisted commands,
   but **a script an agent writes and then runs isn't covered**, and spotting writes
   inside shell commands is best effort. Give the machine dev-only credentials.
5. **`ask` means "blocked" when no one is there.** If an agent needs a command you didn't
   allow, it should stop as blocked. If it keeps hitting walls, read `commands.log` and
   decide whether to add the command to `allow`.
6. **Your organisation's Copilot policies may switch things off.** Admins can disable
   hooks or custom agents. Symptoms: `--agent loop-…` not found, or nothing ever
   appearing in `commands.log`.
7. **Choosing the model.** Copilot uses its default model. A custom agent can name its
   own model in its `.agent.md` frontmatter, but `loop install` rewrites those files, so
   a hand edit lasts until the next install.
8. **Sessions use premium requests.** One `loop run` can start up to twelve unattended
   sessions: one first round and up to three rounds sent back.
9. **`loop reset` deletes work.** It removes the worktree and branch, including the
   planner's drafts. Copy `grilling.md` somewhere first if you want it.
10. **Where the logs are.** Each worktree has its own `.loop/commands.log`. Calls made
    outside a `loop` session are logged in the main repo's `.loop/commands.log`.

### On native Windows

- Copilot runs the hook's `powershell` line. Constrained Language Mode limits what
  scripts can do, but doesn't stop PowerShell from starting a program. Application
  control decides whether that program may run.
- If `loop.exe` is blocked but `python.exe` isn't, edit `.github/hooks/loop-guard.json`
  so both lines read `python -m devloop guard`, and run every other command as
  `python -m devloop …`. `loop install` would undo that edit, so redo it after
  reinstalling.
- Shell commands arrive as PowerShell. `tools.toml` is written for bash-style commands;
  `git` and `python` commands match the same way, but cmdlets like `Get-ChildItem`
  must be added to `allow` if agents need them.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Every tool call is denied, even reading files | hook command not found or blocked | run the guard check; fix PATH, or use `python -m devloop guard` in the hook file |
| `.loop/commands.log` stays empty during a session | hooks not loaded: org policy, untrusted folder, or loop files not committed | trust the repo, commit `.github/`, ask your admin about hooks |
| `--agent loop-planner` not found | `.github/agents/` not in the worktree, or custom agents disabled | commit after `loop install`, then `loop reset` and claim again |
| An unattended session stops and asks to trust a folder | worktree outside the trusted repo | `worktree_root: ".loop/worktrees"`, then trust the repo for future sessions |
| "The tests didn't run" finding | `test_command` doesn't work in the worktree | run the command by hand in the worktree, fix `test_command` |
| Every scenario says "has no test" | test names don't match pytest-bdd's names for the scenarios | bind scenarios with `scenarios("<file>.feature")` and keep `bdd_features_base_dir` set |
| "… points at no code" | a component file without code, often after moving modules | delete or rename the file in `loop/components/` |
| The map draws one box per group | more components than `view_max_nodes` | raise it, or split boundaries more finely |
| `approve` says the demand conflicts | the truth branch moved and touched the same files | resolve the rebase in the worktree, then approve again |
| Diagrams don't draw in the report | `cdn.jsdelivr.net` blocked | open the `.mmd` files in an editor with a Mermaid preview |

---

## A first test session (about an hour)

Use a small scratch repo or a quiet corner of a real one.

1. Install the wheel and run the [guard check](#3-check-the-guard-before-anything-else).
2. `loop install --harness copilot`, edit `conventions.yaml` as above, trust the repo
   in Copilot, `loop onboard`, fix two or three component files, commit.
3. `loop new "Add a small, testable behaviour"`, describe it in `demand.md`, commit.
4. `loop plan 0001`. Answer a round or two, and let it write one or two scenarios and a
   small plan. Exit and open the report.
5. While the planner is still open, ask it to edit a file in `src/`. **It should be
   blocked**, and the block should appear in the worktree's `.loop/commands.log`.
6. `loop approve 0001`, then `loop build 0001`. Afterwards check:
   - `git log` in the worktree shows one commit per agent;
   - `commands.log` has entries for `outside-test-writer` and `builder`;
   - the builder didn't change anything in `tests/`.
7. `loop review 0001` and open the report: the plan vs actual view, the findings
   and the verdict.
8. Note what surprised you. That list is the input for the next version.
