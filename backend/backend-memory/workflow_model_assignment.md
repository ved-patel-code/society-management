---
name: workflow-model-assignment
description: "Model/agent assignment workflow for new module or feature builds — docs check, explore, write, review stages"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: ce1fbb93-39e0-40be-8e54-a89e5cbf76cd
---

For new module/feature build tasks (not small fixes or quick questions), follow this staged workflow:

1. **Check docs first**: read through the whole `docs/` folder (see [[docs-structure]]) before deciding next steps.
2. **Decide on exploration**: only spin up codebase-exploration subagents if the docs review leaves open questions about current implementation.
3. **Explore stage**: use the Agent tool with `model: "sonnet"` explicitly set (Sonnet 5) for codebase exploration subagents.
4. **Write / test-planning stage**: use Opus 4.8 with low reasoning effort for writing code, test plans, and test cases (`model: "opus"`, `effort: "low"`).
5. **Code review stage**: use Opus 4.8 with medium reasoning effort for the code review agent (`model: "opus"`, `effort: "medium"`).

**Why:** user wants cost/quality tuned per stage — cheaper/faster model for exploration, low-effort Opus for generative writing work, more careful medium-effort Opus for review — rather than one model for everything.

**How to apply:** only trigger this staged approach when starting a new module or feature build (e.g., like [[onboarding-module]] or future modules per [[implementation-workflow]]). Skip for small bug fixes, quick questions, or trivial edits — go straight to normal tool use.

**ALWAYS set `model` explicitly on every Agent/subagent call — never rely on inheritance.** Subagents inherit the session model by default; when the session is Opus, unset explore agents silently run on Opus (costlier than the intended Sonnet). User called this out during the Vault (Module 3) build. Every spawn must name its model per the stage table above: explore→sonnet, write→opus/low, review→opus/medium, test-design→opus, test-impl/run→sonnet.
