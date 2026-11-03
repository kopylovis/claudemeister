<p align="center"><img src="docs/icon.png" width="128" alt="claudemeister icon"></p>

<h1 align="center">claudemeister</h1>

<p align="center">English · <a href="README.ru.md">Русский</a></p>

A toolkit around [Claude Code](https://code.claude.com) on macOS. It starts Claude in a project and lets you
pick a saved session, restarts or slims the current session from inside Claude, sends single sessions through
your own proxy server, guards secrets and asks before destructive commands, notifies you when Claude is waiting, and adds an MCP server for searching
past sessions, a project backlog, parallel tasks, window screenshots and release notes.

The interface and messages are in Russian.

```
claudemeister                menu: pick a project, sessions, proxy, humanization, setup
claudemeister kcalm          straight into a project (a prefix of its name is enough)
claudemeister root           in the home folder
claudemeister ... -x         through your own VPS, this session only
claudemeister ... -i         nothing written to disk: no transcript, no prompt history
claudemeister sessions       saved sessions: resume, move, delete, clean, search, slim
claudemeister setup          /restart, /forget and other commands, hooks, MCP server, zsh hook, proxy, promptsill
claudemeister doctor         check the install: commands, hooks, MCP server, notifications, promptsill, proxy
claudemeister restart        from inside Claude: quit, claude update, reopen the same session
claudemeister forget         from inside Claude: delete this session without a trace and open a clean one
claudemeister fresh          from inside Claude: new untitled session, this one stays in /resume
claudemeister slim           from inside Claude: slim this session down and reopen it
claudemeister brief          where we left off: git, last session, backlog
claudemeister parallel       a task in its own branch and worktree, with a second Claude in a new window
claudemeister ask            ask a separate, read-only Claude about another project
claudemeister release-notes  material for "What's new" in a release
claudemeister todo           project backlog that survives between sessions (add, done, rm, all)
claudemeister shot [app]     screenshot of an app window; without arguments, a list of windows
claudemeister deps           outdated Gradle dependencies in libs.versions.toml
claudemeister proxy          proxy through your own server for chosen sessions: setup, test, remove
claudemeister guard          ask before reading or deleting keys and before destructive commands; on, off, test, log
claudemeister humanize       writing rules without the usual AI tells: on, off, check <file>, rules
claudemeister notify         notifications when Claude is waiting for you: on, off, after <sec>, test, telegram
```

## Install

```bash
brew install kopylovis/tap/claudemeister
claudemeister setup
```

Homebrew also installs mihomo for `claudemeister -x`. `claudemeister setup` puts `/restart`, `/forget`, `/slim`
and the other commands into `~/.claude/commands`, hooks into `~/.claude/settings.json` and a zsh hook into
`~/.zshrc`, and registers the `claudemeister` MCP server. If the VPS proxy isn't set up yet, setup asks whether
to do it now (`--country KZ` sets the expected exit country). If the
[promptsill](https://github.com/kopylovis/promptsill) status line is missing, it offers to install it with Homebrew.

Claude Code deletes transcripts older than `cleanupPeriodDays`, 30 days by default, and with them goes everything
`session_search` and `/resume` could find. setup sets it to 365 unless you already have your own value.

setup records the steps it has done in `~/.config/mnrh/claude-setup.json`. If you run `claudemeister` before
setup, or after an update that added a setup step, it lists what isn't configured and offers to do it. "Не
предлагать" (don't offer) stops asking about those steps; `claudemeister setup` still configures them.

`claudemeister doctor` checks all of it in one go: Claude Code on PATH, the commands and whether they match this
version, the hooks, transcript retention, the status line, the MCP server answering `tools/list`, the zsh hook,
the notification app and the proxy. It exits with 1 when something is broken and says what fixes it.

Update with `brew upgrade claudemeister`. Open sessions pick up the new version on their own: before each
request the MCP server checks whether its code changed (a `brew upgrade` or a `git pull` in a checkout) and, if
so, replaces itself with the new one on the same connection, without losing the request. `claudemeister setup --remove` takes everything out of Claude Code,
and `brew uninstall claudemeister` removes the program.

The projects folder is `~/Developer` by default. Set another one with a `projects=<path>` line in
`~/.config/mnrh/config` or with `MNRH_PROJECTS=<path>`. setup puts the read-only MCP tools (`session_search`,
`session_read`, `deps_outdated` and others) into `permissions.allow` in `~/.claude/settings.json`, so Claude
doesn't ask before using them.

### From source, for development

```bash
git clone https://github.com/kopylovis/claudemeister ~/Developer/claudemeister
cd ~/Developer/claudemeister
make install
claudemeister setup
```

`make install` puts a symlink to the checkout into `~/.local/bin`, so edits take effect without reinstalling.
`make uninstall` removes it, `make test` runs the checks.

### Releasing

1. Bump `VERSION` in a PR.
2. Put a signed tag on the commit in `main` (`git tag -s v$(cat VERSION) -m v$(cat VERSION)`) and push it.
3. In `kopylovis/homebrew-tap`, update `tag:` and `revision:` in `Formula/claudemeister.rb`
   (`git rev-parse v$(cat VERSION)^{commit}`).

## Requirements

macOS, bash and `/usr/bin/python3` from the Xcode Command Line Tools (`xcode-select --install`). Notifications
need `swiftc` from the same tools. `claudemeister -x` needs mihomo.

## How `claudemeister` works

Without arguments in a terminal, `claudemeister` opens an action menu: pick a project, the home folder, saved
sessions, proxy, humanization and setup. It runs the chosen action and prints the full command so you can type
it directly next time. Afterwards you can go back to the menu. `MNRH_NO_MENU=1` skips the menu and goes straight
to the project list.

`claudemeister open` lists the folders in the projects folder with their current branch and last commit date.
Move with ↑↓ and press Enter. A digit jumps to that item (`0` is the home folder, `1` then `0` is the tenth),
letters filter the list by name, and Esc clears the filter or exits.

If the project has saved sessions, you can then resume one of them or start a new one (Enter means new).
Newest sessions come first, with time, size, number of messages and title. Empty sessions (only `/clear` and
the like) are hidden, and so are sessions open in other windows, so two copies of Claude never write to one
file. This step is skipped with `--new` and when the arguments after `--` already include `--continue`,
`--resume`, `--session-id` or `--print`. `open_claude` passes `--new`.

Then you pick a mode the same way:

- caffeinate (Enter) runs `caffeinate -is`, so the Mac doesn't sleep from inactivity or on AC power while the
  session runs. The screen can still turn off. Use it for long tasks and for Remote Control from your phone,
  since a sleeping Mac drops the session.
- normal just runs `claude`.

`-k` and `-n` choose the mode without asking, and everything after `--` goes to claude:
`claudemeister kcalm -k -- --continue`. caffeinate doesn't help with a closed lid on battery: the Mac sleeps anyway.

### A session through your own server: `claudemeister proxy`

If the proxy is configured, the next question is the network: direct or through your server. Only this session
goes through the server, together with the programs it starts that read `HTTPS_PROXY` (gh, npm, curl, git over
https, MCP servers). git over SSH (`git@github.com:…`) stays direct, because SSH ignores proxy variables. The
browser, messengers and other terminal windows stay direct, and the system proxy and TUN are left alone. `-x`
picks the server without asking, `-d` picks direct. The menu remembers your last choice.

It runs on [mihomo](https://wiki.metacubex.one) (`brew install mihomo`), which speaks VLESS (including XHTTP)
and Hysteria2 and uses the first live server from the subscription. One-time setup:

```bash
claudemeister proxy setup 'https://…/sub/…' --country KZ   # subscription link, the same one Happ uses
claudemeister proxy test                                    # exit IP and country, direct and through the server
```

`--country` protects the account: if the server exits in another country, the proxied session refuses to
start instead of showing up from an unexpected place. `--only <pattern>` keeps only the servers whose names
match. Encrypted `happ://crypt…` links can only be read by Happ, so use the plain `https://` one.

mihomo listens on `127.0.0.1:17890`, starts with the first proxied session and stops after the last. The
session gets `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, and `NO_PROXY` for localhost. `/restart` and the windows
that `open_claude` and `/parallel` open from such a session also go through the server.

mihomo reaches the server over Wi-Fi or Ethernet, bypassing the TUN tunnel of Happ and other VPNs, so you can
turn Happ on and off during a session without the session noticing. This matters when Happ is connected to the
same server, since the server can't reach itself through its own tunnel. claudemeister downloads the
subscription the same way each time mihomo starts and falls back to the previous copy if that fails. If no
server in the subscription is alive, connections are refused instead of going direct. claudemeister resolves the
subscription's server names through the system DNS and writes them into the `hosts` section of the mihomo
config, so mihomo never does DNS itself. Without this, a corporate VPN whose DNS server is only reachable
through the tunnel would leave mihomo unable to find the server, because its query goes out over Wi-Fi.

Settings, the link and the subscription live in `~/.config/mnrh/proxy/`, readable only by you. claudemeister
checks the exit country every time mihomo starts and passes it to the session in `PROMPTSILL_PROXY_LABEL`, which
promptsill shows as `proxy KZ` in the status line. It also asks mihomo over the unix socket
`~/.local/state/mnrh/proxy/mihomo.sock` which server from the subscription is selected and with which protocol,
and passes the protocol and IP in `PROMPTSILL_PROXY_DETAIL`. In macOS Terminal the status line shows
`proxy KZ vless 203.0.113.7`. In iTerm2, Ghostty and other terminals with link support it stays `proxy KZ`, and
Cmd-click opens a page with the details (`exit.html` in the same folder). All of this is captured when mihomo
starts. If the fallback later switches servers, `claudemeister proxy` shows the current one. If mihomo is
missing, `setup` offers to install it with brew; the claudemeister formula installs it anyway.

## How `claudemeister sessions` works

Claude Code keeps sessions in `~/.claude/projects/<folder-path>/<id>.jsonl`, next to an `<id>/` folder with
subagents and large tool outputs. `claude --resume` only sees sessions of the current folder, so after you move a
project its old sessions disappear from the list.

Without arguments you get every session, newest first: time, size, folder and title (your own from `/rename`,
otherwise the one Claude made up, otherwise the last prompt). `● открыта` means the session is open in another
window and won't be touched. Pick one with the arrows, then resume it (caffeinate or normal), move it or delete it.

- `mv <id> <folder>` moves the files into the new folder's storage and rewrites `cwd` inside the session,
  including subagents. Nothing else in the file changes, the date included.
- `rm <id>...` deletes the session together with its `file-history` and its lines in `history.jsonl`.
- `clean` removes broken symlinks, sessions where Claude never answered, and folders of projects that no longer
  exist on disk (with their `memory`). It asks about each group separately.
- `-l` prints a plain list with ids, no menu.
- `search <words>` searches all sessions of all projects (see below), `show <id> [N]` reads a session from
  message `N`, and `-10` shows the last ten.

## Slimming old sessions: `claudemeister sessions slim`

A big session is mostly screenshots and tool outputs. In one 481 MB session about 330 MB were images (each
stored twice, once for the model and once for the screen), while the conversation text was a few megabytes.
After `/compact`, Claude Code sends the model only the summary, the messages `/compact` kept
(`preservedMessages`) and whatever came after the boundary: when resuming it walks back from the last message and
stops at `compact_boundary`. The old part is only there for scrolling up, but `--resume` still parses the whole
file, which makes opening slow and memory hungry.

Before the last two `/compact`s (`--keep N`), `slim` replaces screenshots with a caption
`[скриншот 1,2 МБ удалён: mnrh slim]` (a 1×1 image in the screen copy, so the display doesn't break) and trims
outputs longer than 8000 characters to their beginning and end. It leaves alone everything after that boundary,
the messages kept by `/compact`, the chain up to the last message, Claude's messages (they carry thinking
signatures) and the file history for `/rewind`.

Before writing, the slimmed file is checked against the original: line count, `uuid`, `parentUuid`, types and
timestamps must match, and everything that goes into the model's context must match byte for byte. Otherwise
nothing is written. The original is compressed into `~/.cache/mnrh/slim-backup/` and kept for 14 days.
`slim <id> --undo` restores it, along with any messages added after slimming. The session date doesn't change.
Open sessions are never slimmed.

```
claudemeister sessions slim              how much all closed sessions could free up
claudemeister sessions slim <id> --copy  a slimmed copy with a new id, to open and check
claudemeister sessions slim <id>         slim it (asks first; -y doesn't)
claudemeister sessions slim <id> --undo  restore the original
```

From inside Claude Code use `/slim` (or ask "slim this session", which calls the `slim` MCP tool). An open
session can't be slimmed, so `/slim` works like `/restart`. It first estimates how much it would free, and if
that's under 5 MB it closes nothing and just says so. Otherwise Claude quits, the tab runs
`claudemeister sessions slim <id> -y`, and the session reopens with the same flags and caffeinate. After it
reopens, Claude shows the size before and after, or why slimming failed (the session is then opened as it was).
`/slim list` shows what can be slimmed among closed sessions, and `/slim <id>` slims one of them.

What you lose: old screenshots when scrolling up, and, if you `/rewind` to a point before those two `/compact`s,
the images and full long outputs in that old part of the conversation.

## Searching past sessions

The same search is available to Claude as the MCP tools `session_search` and `session_read`. Ask "how did we
set up RuStore signing in sightra?" and Claude finds it in past sessions and reads the right spot. The index
covers your prompts, Claude's answers, the commands it ran (`$ …`, or the file or pattern for other tools) and
the summaries after `/compact`. Tool outputs and system messages aren't indexed. All words must appear in one
message (if no message has them all, messages with at least half of them are shown). Case and word form don't
matter: words are reduced to their stems (a Snowball stemmer for Russian, the first Porter steps for English),
so `сессия` finds `сессии` and `signing` finds `signed`. Hyphens split words, and a hyphenated word or a phrase in
quotes must appear in that order: `прокси-соединение` finds `прокси-соединения`. A fragment that is already a
stem, 3 letters or longer, also matches longer words (`fastl` finds `fastlane`). Common words such as `как`,
`мы`, `the` are skipped. Results are ranked with BM25 over messages: sessions with the best-matching messages come
first, with a small bonus for recent ones, and within a session the best snippets are shown first. The current
session is excluded.

The index lives in `~/.cache/mnrh/sessions-index/`: `<id>.json` holds only text (about 1 MB for a 481 MB
session) and `<id>.terms.json` the stems of each message (about 40% of the text size). The first pass over
all sessions takes a few seconds (4 s for 700 MB of transcripts). After that only new lines are read, and a query
takes under 0.1 s. The index is refreshed in the background when each session starts (the
`SessionStart` hook). When a session is deleted, including through `/forget` and `sessions rm`, its index goes
with it.

## Outdated dependencies: `claudemeister deps`

Parses `gradle/libs.versions.toml` (versions, libraries, plugins), checks Google Maven, Maven Central and the
Gradle Plugin Portal, and shows what has a newer version, how big the jump is (major, minor, patch) and which
libraries use that version. It also shows the Gradle wrapper version and what has to move together: Kotlin and
KSP, AGP and Gradle, Compose Multiplatform and Kotlin. Variants such as `-jre`/`-android` are only compared with
each other, and alpha/beta/rc only with `--pre`. `claudemeister deps status` (and `claudemeister deps` outside a
terminal) checks the current project, or all projects if the current one has no catalog. Repository answers are
cached for 6 hours. Claude gets the same through the `deps_outdated` tool.

## Commands in Claude Code

`claudemeister setup` puts these into `~/.claude/commands/`:

| Command | What it does |
|---|---|
| `/restart` | restart with `claude update`, the session continues |
| `/forget` | delete this session without a trace and open a clean one |
| `/fresh` | new untitled session with an empty context, this one stays in `/resume` |
| `/slim` | slim this session down and reopen it; `/slim list` for others |
| `/where` | where we left off: git, the last session in this project, backlog, what's next |
| `/todo` | backlog: `/todo` lists, `/todo <text>` adds, `/todo done 2` marks done |
| `/release-notes` | "What's new" in Russian and English for fastlane |
| `/parallel <task>` | the same task in its own branch in a second window; `/parallel list` |
| `/shot <app> [question]` | screenshot of a window that Claude looks at and answers about |
| `/ask <project> <question>` | an answer from another project by a separate Claude |

## Where we left off

When a new session starts, the `SessionStart` hook gives Claude a brief about the project as background (it
isn't repeated back to you): branch, uncommitted and unpushed changes, the last tag, the last five commits, the
previous session in this folder (title, last prompt and Claude's last answer, which usually sums up the work)
and the open backlog items. You see one line, like
"claudemeister: прошлая сессия «…» (вчера) · незакоммичено 3 · в бэклоге 2 · /where — подробнее". This only
happens for new sessions in a terminal, not after `--resume`, `/clear` or `/forget`, and not in `claude -p`.
Turn it off with `claudemeister brief off`.

## Backlog: `claudemeister todo`

Tasks for later that live between sessions, unlike the task list inside a single session. One file per project,
`~/.config/mnrh/todo/<project>.md`, a plain checklist. The project is the git root of the current folder;
`-p <project>` picks another one. Claude sees open items when a session starts and can add ("note this for
later…") and close them itself (`todo_add`, `todo_done`, `todo_list` run without asking for permission).

## Release notes

`/release-notes` gathers material (`release_notes_context`): the version from `libs.versions.toml`
(`appversion`/`versionName`) or `versionName`/`MARKETING_VERSION`, the commits since the last release (counted
from the last tag, the last fastlane run in `fastlane/report.xml` or the previous notes, whichever is newer) and
the Claude sessions from that period. Claude writes "What's new" in ru-RU and en-US in up to 500 characters
(enough for Google Play; the App Store and TestFlight allow 4000, RuStore 5000, Firebase 16000), shows it to
you, and once you agree saves it to `fastlane/release_notes/<version>.<lang>.txt`. If there is
`fastlane/metadata`, it also writes `changelogs/<versionCode>.txt` and `release_notes.txt`. The Fastfile takes
the text from `notes:` or `RELEASE_NOTES`, so the answer ends with a ready command:
`RELEASE_NOTES="$(cat fastlane/release_notes/3.1.2.ru-RU.txt)" bundle exec fastlane …`.

## Parallel tasks

`/parallel <task>` (or "do this in parallel") creates a `parallel/<from the task>` branch and a git worktree from
the current commit in `~/Developer/.worktrees/<project>/`, copies local untracked files into it (see below), and
opens a second Claude with the task and Remote Control in a new Terminal or iTerm2 window. It works only in its own
folder and commits to its own branch without merging or pushing. The permission mode is the usual one: questions
arrive as notifications, and clicking one opens its tab. `--permission-mode auto` skips the questions.
Uncommitted changes in the main folder don't make it into the worktree.

Which files are copied follows Claude Code's own
[`.worktreeinclude`](https://code.claude.com/docs/en/worktrees#copy-gitignored-files-into-worktrees): if the
project has that file in its root, its patterns (`.gitignore` syntax) pick the files, and only files git ignores
are copied, so tracked files are never duplicated. The same file then also works for `claude --worktree` and for
subagent worktrees. A project without `.worktreeinclude` gets the old list: untracked `local.properties`, `.env*`
and `fastlane/.env*`, and the reply says so.

claudemeister keeps creating the worktree itself instead of using `claude --worktree`: that flag puts worktrees
in `<repo>/.claude/worktrees/` on a `worktree-<name>` branch from the default branch of the remote, while
`/parallel` branches from the current commit, records the base branch for `merge` and `pr`, and opens its own
window with caffeinate and Remote Control. `--tmux` needs `--worktree`, so it isn't used either.

```
claudemeister parallel list            branches, commits, whether Claude is working there
claudemeister parallel merge <branch>  merge into the original branch (no push), remove the worktree
claudemeister parallel drop <branch>   throw it away
claudemeister parallel pr <branch>     push, open a PR, wait for CI, merge it, remove the worktree
```

A merge won't start while Claude is open there, while there are uncommitted changes, or while the main folder is
on a different branch than the original one. On a conflict it's aborted.

`parallel pr` takes the change through GitHub instead. It refuses while Claude is open in the worktree or the
worktree has uncommitted changes. It pushes the branch (never tags), opens a PR into the original branch with
`gh pr create --fill`, or reuses the open one, and picks the merge method from that branch's history: `--merge`
if it already has merge commits, `--rebase` otherwise. If the PR has checks, it waits for them with
`gh pr checks --watch`; with no checks it merges right away. After `gh pr merge --delete-branch` it removes the
worktree and the local branch, like `drop`, and fast-forwards the original branch in the main folder when that
branch is checked out there and clean. If CI fails, the PR stays open, the worktree stays, and the failing checks
are listed: fix them in the worktree, commit, and run `parallel pr` again. `--no-merge` only pushes and opens the
PR. In Claude Code the same is `parallel_finish` with `action: "pr"`.

## Window screenshot: `claudemeister shot`

`/shot chrome why did the button move` takes a screenshot of Chrome's front window (or Safari, Simulator,
Android Studio, Figma, VS Code…), and Claude looks at it and answers. Without an app it lists windows, hiding
title fragments that look like tokens. The screenshot is scaled down to 1600 px and kept in
`~/.cache/mnrh/shots` for three days. The terminal needs Screen Recording permission. The `window_screenshot`
tool hands the image straight to Claude and asks for permission every time, since the screen may show other
people's things.

## Asking another project: `/ask`

`/ask backend what's the response format of /api/foods` starts a separate `claude -p` in the backend folder with
only Read, Grep and Glob, no MCP servers and no saved session, and returns a short answer with paths and code
snippets. Reading another project's files doesn't fill up the current session's context. The default model is
Sonnet; a simple question costs about 30k tokens and 5 to 10 seconds.

## How restarting Claude Code works

`claudemeister setup` puts `restart.md`, `forget.md`, `fresh.md` and `slim.md` into `~/.claude/commands/` and
registers the `claudemeister` MCP server with the tools `restart`, `forget`, `fresh`, `slim`, `open_claude`,
`session_search`, `session_read` and others (user scope, in every project). Then, in any session:

- `/restart` restarts right away without involving the model; `/restart --no-update` skips the update.
- Or ask Claude to "restart" or "update itself", and it calls the `restart` tool.
- `/forget` (or "forget this session", the `forget` tool) makes a throwaway session: Claude quits, the
  conversation is deleted from disk together with its lines in `history.jsonl`, `file-history` and
  `session-env`, the screen and terminal scrollback are cleared, and a clean Claude opens in the same folder.
  Unlike the built-in `/clear`, nothing stays behind in `/resume`. To keep a session off the disk from the
  start, launch it with `claudemeister -i`: it sets `CLAUDE_CODE_SKIP_PROMPT_HISTORY=1`, so Claude Code writes
  neither the transcript nor the prompt history. Such a session can't be reopened, so `/restart` and `/slim`
  refuse in it.
- `/fresh` (or "start a new session", the `fresh` tool) does the same as `/forget` but leaves the old session
  alone, so it stays in `/resume`. The built-in `/clear` now does almost the same (it no longer carries the AI
  title over, and `/clear <name>` labels the session you leave). `/fresh` also starts a new Claude Code process,
  so settings, MCP servers, CLAUDE.md and an installed update are loaded again, with caffeinate, the proxy and
  your flags kept.

Claude Code has its own `/restart` since 2.1.288. It applies an installed update and resumes the session, on a
native install with a Pro or Max plan. claudemeister's `/restart` takes the name: it also runs `claude update`,
keeps caffeinate, the proxy and your launch flags, and works in Terminal, iTerm2 and other terminals through the
zsh hook. The built-in one stays available as `/update`.
- "open Claude in kcalm" uses the `open_claude` tool. Without a project it returns the list of projects (branch,
  last commit) and modes to the chat, Claude asks which one, and opens a new window of the same terminal (Terminal
  or iTerm2) with `claudemeister <project> -k|-n`. You can pass a first prompt and `--continue`. Remote Control
  (`/rc`) is on from the start (`--remote-control <project>`; turn it off with `remote_control: false`). The reply
  contains the window, tty, pid and the claude.ai/code link to the session, so you can continue from your phone
  or a browser. If Claude Code doesn't trust the folder yet (no `hasTrustDialogAccepted` for it or a parent in
  `~/.claude.json`), no window opens: the "Do you trust the files in this folder?" prompt can't be answered over
  Remote Control, and the session would wait forever. Claude asks you in the chat and, if you agree, calls again
  with `trust: true`, which sets the flag before launch. Worktrees from `/parallel` inherit trust from their
  project.

What happens under the hood: claudemeister finds its own `claude` in the process tree and its record in
`~/.claude/sessions/<pid>.json` (session id, folder, terminal). A separate detached process sends it SIGTERM,
so Claude quits cleanly with the session already on disk, and the same tab runs
`cd <folder> && { claude update; claude --resume <id>; }`. Launch flags (`--model`,
`--dangerously-skip-permissions`, …) and caffeinate are kept.

How the command gets back into the tab:

- The zsh hook is the main way and works in any terminal (Android Studio, VS Code, Terminal, iTerm2).
  `claudemeister setup` adds a line with `share/claudemeister/restart.zsh` to `~/.zshrc`. Before Claude quits,
  a job is written to `~/.cache/mnrh/restart/<tty>`, and zsh runs it before the next prompt. Jobs older than two
  minutes are ignored. It works in tabs opened after setup.
- AppleScript is the fallback for Terminal and iTerm2 when the hook isn't loaded in that tab. The first time,
  macOS may ask for permission to control the terminal. This is checked before quitting, so without permission
  Claude simply doesn't quit.

After it starts, Claude shows what happened under the input line: "Claude Code перезапущен (обновлён 2.1.278 →
2.1.280). Сессия «…» продолжается" (restarted and updated, the session continues) or that the previous session
was deleted and this is a clean one. That's the `SessionStart` hook in `~/.claude/settings.json`, installed by
`claudemeister setup`: before quitting, a note is written to `~/.cache/mnrh/notice/<session id>.json`, and the
hook shows and deletes it. The model doesn't see it. That's why `/forget` starts the new session with a known
`--session-id`.

### Empty sessions don't pile up

After `/clear` Claude starts a new session, and if you quit right away, `/resume` is left with junk: the
session contains only the `/clear` record and metadata. `claudemeister setup` installs a `SessionEnd` hook that
deletes its own session on exit if it has no answer from Claude and no message from you (service `/commands`
don't count). If Claude was closed with the window's close button and the hook didn't get to run, the next
`SessionStart` in the same project removes such empty sessions older than a minute in the background, as long as
they aren't open in other windows. A session with an actual conversation is never touched, and `!` commands
count as conversation.

### Status line

The status line is a separate open source program,
[promptsill](https://github.com/kopylovis/promptsill). If it's missing, `claudemeister setup` offers to install
it with Homebrew (`brew install kopylovis/tap/promptsill`) and puts it into `statusLine` unless you already have
your own, replacing the older `claudemeister statusline`. Two lines under the input:

```
Opus · context ██▏   42% · 5h ███▏  63% +23%, runs out at 15:10, resets at 17:00 · week 35%
master ↑3 ±5 · vite :5173 · memory 9.9/16 GB · swap 1.5 · Gradle 2 (3.1 GB)
```

It shows the context, subscription limits with the pace of use, a cache timer, git, the project's dev servers
and busy ports, the Node version, memory, Gradle daemons and simulators. Labels are in English, or in Russian
with a Russian locale or `promptsill install --lang ru`. See the promptsill README for details.
`claudemeister statusline …` passes commands on to promptsill.

### Guarding secrets and destructive commands: `claudemeister guard`

The `PreToolUse` hook watches three things:

| Rule | What |
|---|---|
| `secrets-read` | reading or editing `.env*` (except `.example` and similar), `*.p8`, `*.p12`, `*.jks`, `*.keystore`, private SSH keys, `*service-account*.json`, `*credentials*.json`, `keystore.properties`, `.netrc`, `.npmrc`; `security find-*-password -w` |
| `secrets-delete` | `rm`, `rmdir`, `unlink`, `shred`, `trash` on a key file or a folder that holds keys (`~/.ssh`, `~/.gnupg`, `~/.aws`, the keychain, `fastlane/` with `.p8` and JSON…); `security delete-*` |
| `destructive` | `git reset --hard`; `git push --force`, `-f`, `--force-with-lease` or a `+branch` refspec to any remote; `git clean -f` with `-d` or `-x`; `git checkout -- .`, `git checkout -f`, `git restore .` (not `--staged` alone); `git branch -D`; `rm -r` of a path outside the project, of the project root itself or of `~` (temp folders, `~/.cache`, `~/Library/Caches` and DerivedData are fine); `curl`/`wget … \| sh`, `bash`, `zsh` or `python` reading the script from stdin, and `bash -c "$(curl …)"`; `dd of=/dev/disk…`; `diskutil erase*`; `mkfs`; `chmod`/`chown -R` of `/`, `~` or system folders |

The project is the git root above Claude's working folder (or the folder itself outside git). A session started
in `~` has no project, so any `rm -r` outside temp folders asks. Commands are found inside `bash -c`, `sh -c`,
`zsh -c` and `eval` too, and behind `sudo`, `env`, `xargs`, `nohup`, `time`. `git push origin main`, `rm -rf build`
inside the project and `curl … | python3 -m json.tool` go through. Everything else (`rm` on ordinary files in the
project, fastlane uploads, SQL, commands on a server) is left to Claude Code's normal permissions.

It asks rather than refuses. In the default mode Claude Code shows its own "allow?" prompt with an explanation.
In `auto` mode and with `--dangerously-skip-permissions` Claude Code can't do that (its "ask" turns into "deny"
or "allow"), so guard holds the call and Claude asks you in the chat why it needs it. Answer "yes" (or "ok",
"да", "давай") and the same call goes through. The answer is tied to that call and to your message after the
question, so the model can't answer for you. `claudemeister guard skip destructive` turns a rule off,
`guard test '<command>'` shows the decision, and `guard log` shows recent hits.

### Notifications when Claude is waiting for you

`claudemeister setup` installs the `UserPromptSubmit`, `Stop` and `Notification` hooks (asynchronous, Claude
doesn't wait for them). A macOS notification arrives when Claude:

- asks for permission to use a tool or asks a question (including one from an MCP server);
- finishes a turn that took longer than 20 seconds (`claudemeister notify after <sec>`);
- has been waiting for an answer for a minute with no "done" notification.

The title is the project and what happened; below it are the session title and the start of the answer. If you're
already looking at that tab (Terminal or iTerm2 in front with that tab selected) or at the IDE window Claude runs
in, there's no notification. Clicking it opens the right tab in Terminal and iTerm2 (by tty, like `/restart`) and
the app window in Android Studio, VS Code and others. A new notification from the same session replaces the old
one, and after your next prompt it's removed from Notification Center.

They're shown by a small app, `~/Library/Application Support/claudemeister/claudemeister Notify.app`, built from
`share/claudemeister/notify/main.swift`. The first time, macOS asks whether it may show notifications, and on the
first click, whether it may control Terminal or iTerm2. `claudemeister notify test` sends a test notification,
`notify off` and `on` turn them off and on, and `notify sound off` mutes them.

The same notifications can go to Telegram when you're away from the Mac: the screen is locked or nobody has
touched the mouse and keyboard for 2 minutes. `claudemeister notify telegram` connects a bot: create it with
@BotFather, enter the token (or copy it and run the command outside a terminal, and claudemeister takes the token
from the clipboard and clears it), then press Start in the chat with the bot. The token is kept in the Keychain
(`mnrh-telegram`); the config only holds the chat id. `telegram test` sends a test message,
`telegram away <sec>` sets how long without input counts as away, `telegram always` sends every time, and
`telegram off` disconnects and deletes the token. If you're looking at Claude's tab but have walked away, the
notification only goes to Telegram. Send errors are logged to `~/.cache/mnrh/notify/errors.log`.

Permission requests can also be answered from Telegram. `claudemeister notify telegram approve on` turns it on
(off by default) and adds a blocking `PermissionRequest` hook to `~/.claude/settings.json` (`claudemeister setup`
installs it too, with a 600-second timeout). When Claude asks for permission and you're away from the Mac, the bot
sends the project, session title, tool and command or path with "Разрешить" and "Отклонить" buttons, and Claude
gets your answer. If you come back to the Mac, nobody answers within 9.5 minutes, or Telegram can't be reached,
the hook exits without a decision and Claude asks in the terminal as usual; the message then says so and loses its
buttons. While you're at the Mac, the hook exits at once and changes nothing. Questions (`AskUserQuestion`) and
plan approval stay on the Mac. Only presses from the chat that pressed Start during setup count, and each
message's buttons carry a random id that is valid for that one request. When the request is already in Telegram
with buttons, the regular "permission needed" message isn't sent there a second time.
`claudemeister notify telegram approve off` turns it off.

The trade-off: anyone who can open your Telegram (an unlocked phone, a logged-in Telegram Desktop or web session)
can allow a command on your Mac, and so can anyone holding the bot token, which sits in the Keychain. The message
shows at most 600 characters of the command and hides values that look like passwords and tokens, so a long or
masked command can't be fully read from the phone; the message says so, and then it's safer to deny or wait. An
"allow" from Telegram doesn't bypass your `deny` rules in Claude Code settings. A request that guard held because
it touches keys comes with guard's warning, but the button allows it just like the terminal prompt would. Turn it
on only if your Telegram account has a password and two-step verification.

Or to Tossling on your phone through your own ntfy: `claudemeister notify phone away` when you're away from the
Mac, `phone always` every time, `phone off` to turn it off, `phone test` for a test. They go to the `claude`
channel, and questions and permission requests use high priority. On the phone, add the `claude` channel in
Tossling's notification settings.

The claudemeister Notify icon is `share/claudemeister/icon.png`, 1024×1024 (a rounded copy for the README is in
`docs/icon.png`). It fills the whole square with no transparency: macOS 26 rounds it itself and puts an icon with
margins or transparent corners on a gray plate. The build turns the PNG into `AppIcon.icns`, and the helper app
rebuilds itself when the icon changes. Notification Center remembers the old icon until you restart or log out.

Log: `~/Library/Logs/mnrh-claude-restart.log`.
