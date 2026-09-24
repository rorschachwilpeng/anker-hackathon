# Anker / Evo Repository Split Migration Plan

> **For agentic workers:** Execute each task in order. The deletion task is blocked until the user explicitly approves the migration review.

**Goal:** Convert the existing repository into the private Anker Hackathon repository and migrate all EvoTavern / Ghost in the Shell assets into a separate private repository without losing active worktree changes.

**Architecture:** The existing repository keeps its Git history and is renamed remotely to `anker-hackathon`. A new independent repository, `evotavern-agent-hackathon`, receives a curated Evo-only project skeleton plus the committed Ghost research assets. Evo content remains in the Anker repository until the user approves the migration manifest.

**Tech Stack:** Git, GitHub CLI, Markdown, Codex worktrees.

## Global Constraints

- Canonical source repository: `/Users/pengyoutian/Desktop/彭祐天的Github/Wiki_of_Tian/projects/anker-ghost-in-the-shell-hackathon`.
- Do not modify or commit uncommitted files in active Codex worktrees.
- The Anker and Evo GitHub repositories must be private.
- Do not delete Evo content from the Anker repository before explicit user approval.
- Preserve source paths and commit IDs in a migration manifest so every migrated file can be audited.

---

### Task 1: Freeze and inventory the source

**Files:**
- Create: `docs/superpowers/plans/2026-09-19-repo-split-migration.md`
- Review: `AGENTS.md`, `spec/`, `02-赛事调研/`, active worktrees

- [x] Record the clean/dirty state of the main checkout and every active worktree.
- [x] Identify the committed Anker branch and committed Evo snapshot.
- [x] Confirm target GitHub repository names are available.

### Task 2: Rename and privatize the Anker repository

**Remote:** `rorschachwilpeng/anker-hackathon`

- [x] Rename the existing GitHub repository from `anker-ghost-in-the-shell-hackathon` to `anker-hackathon`.
- [x] Set repository visibility to private.
- [x] Update and verify the local `origin` URL.
- [x] Push the current main branch without merging active worktree changes.

### Task 3: Create the Evo repository

**Local path:** `/Users/pengyoutian/Desktop/彭祐天的Github/Wiki_of_Tian/projects/evotavern-agent-hackathon`

**Remote:** `rorschachwilpeng/evotavern-agent-hackathon`

- [x] Create the private GitHub repository and local checkout.
- [x] Initialize the project lifecycle directory structure.
- [x] Create Evo-only `AGENTS.md`, project Spec and repository README.
- [x] Copy the committed Ghost research snapshot from commit `67c9733`.
- [x] Add a migration manifest listing source commit and file mapping.
- [x] Commit and push the initial Evo repository.

### Task 4: Review gate

**Verification:**

- [x] Compare the source Ghost research file list with the Evo repository manifest.
- [x] Confirm every migrated Markdown file is byte-identical or explicitly marked as adapted.
- [x] Confirm the Anker repository still contains all pre-migration Evo content.
- [x] Give the user links to both private repositories and the local review files.
- [ ] Stop and wait for explicit approval.

### Task 5: Clean Anker after approval

**Files:**
- Modify: Anker `AGENTS.md`, shared Specs and repository description
- Delete: Evo-only research, Ghost Agent references and migration-only notes from the Anker repository

- [ ] Re-scan the complete Anker repository and active branches for Evo, EvoTavern, Ghost in the Shell, 深圳, 进化酒馆, GHOST NETWORK and SECTION 9 references.
- [ ] Remove or rewrite every Evo-specific document and Agent definition approved by the user.
- [ ] Rename the local Anker checkout only after active Codex worktrees no longer depend on its current Git metadata path.
- [ ] Run repository-wide content and link checks.
- [ ] Commit and push the Anker-only state.
