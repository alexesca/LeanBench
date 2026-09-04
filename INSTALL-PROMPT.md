# The install prompt

Paste this to a coding agent (Claude Code, Cursor, Codex, aider, …) working in **your**
repository. It installs LeanVFS and switches the agent's search habit over to it.

---

```
Install LeanVFS in this repo and use it for all code search from now on.

1. Install it (use the SSH form while the repo is private):
   pip install "git+ssh://git@github.com/alexesca/LeanBench.git"
   # once public:  pip install "git+https://github.com/alexesca/LeanBench.git"

2. Verify it works:
   leanvfs --repo . search "error handling" --limit 5
   (First run indexes the repo automatically. No separate setup step.)

3. Create or append to AGENTS.md in the repo root:

   ## Code search
   This repo has a LeanVFS semantic index. Use it INSTEAD of grepping or
   reading whole files when you need to find something.

     leanvfs --repo . search "<question in plain English>" --limit 8
     leanvfs --repo . context "<Symbol.name>"
     leanvfs --repo . stats

   `search` returns ranked file+symbol+line-range hits. `context` returns a
   symbol's signature, exceptions, side effects, tests and callers WITHOUT its body.

   Workflow: search -> pick the 1-3 relevant hits -> read ONLY those line
   ranges with Read(offset/limit). Do not read whole files first.

   The index self-updates on every query, so it is never stale. Do not run a
   sync command manually.

   Works on Python (deepest support), TypeScript/JavaScript, Go, Rust, Java,
   Kotlin, C#, Ruby, PHP, Swift, C/C++, Scala, SQL, shell, Markdown and config
   files.

4. Add the index directory to .gitignore if you used --state-dir:
   echo ".leanvfs/" >> .gitignore
   (By default the index lives in the OS cache dir, outside the repo, so this
   step is usually unnecessary.)

Then confirm it works by answering one real question about this codebase using
only leanvfs output plus targeted line-range reads, and tell me how many tokens
of source you had to read.
```

---

## Why this shape

- **One `pip install`, no config file, no daemon.** Anything more and it does not get adopted.
- **The first query indexes the repo.** An agent that has to remember a setup step will
  forget it, then get an empty result that looks like "no matches" rather than "not indexed".
- **The index self-heals on every query.** An agent that edits a file and then searches must
  not be answered from the previous state. An unchanged file costs one hash, so this is
  nearly free.
- **`AGENTS.md` is the durable part.** The install is one-time; the instruction to *prefer*
  the index over grep is what actually saves tokens on every future session.
- **The last line asks the agent to report its own token usage.** That is the whole claim,
  and it should be checked on your code rather than taken on trust.
