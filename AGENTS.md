# Repository notes

This worktree is shared: two agents may edit it at the same time, both on
`main`, in this one directory. So a careless commit or a whole-file rewrite can
silently destroy the other agent's uncommitted work.

## Before editing a file

- Run `git status --short` first. A file listed as `M` that you did not modify
  belongs to the other agent's in-flight change — stay out of it.
- `stat -c %y <file>` shows when it last changed.
- Edit in small, additive hunks. Never replace a whole `<script>` or module
  body, and never reformat an entire file: the other side cannot review that,
  and the merge conflict becomes unavoidable.

## Who owns what (current split)

- `web/templates/index.html` — the UI agent (layout, styles, browser behaviour).
- `web/app.py`, `pipeline/`, `server.py`, `cli.py` — the backend agent.
- `README.md` is the one shared file: re-read it immediately before editing,
  otherwise the last writer silently drops the other's paragraph.

## Commits

- Always `git commit -- <paths>` with explicit paths. Never `git add -A`,
  `git add .` or `git commit -a` — they would sweep the other agent's
  uncommitted work into your commit message.
- Commit your own work promptly and in small batches. A change that sits
  uncommitted for long can land in someone else's commit or vanish under their
  `git stash` / `git checkout`.

## Runtime state is shared too

- Port 8765 belongs to whoever started it first. Before killing a listener,
  check `netstat -ano | grep :8765` and that PID's command line; start your own
  test server on 8766+.
- `web/outputs/<job-id>/` is written by every server instance. Delete only the
  job ids your own run printed.
