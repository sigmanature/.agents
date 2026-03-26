# Skill Governance Entry Rule

This rule is always active.

## Trigger conditions

Enter skill-governance mode immediately when any of the following is true:

1. The session starts.
2. Any user or agent asks to create, edit, fix, refactor, validate, package, install, uninstall, migrate, or adapt a skill.
3. A task executed through an existing skill reveals reusable trial-and-error, including:
   - exceptions
   - error feedback
   - environment pitfalls
   - permissions problems
   - path issues
   - missing tools
   - command combinations that had to be discovered by iteration
   - successful workaround sequences
4. The model generates multiple commands or repeated repair steps that can be abstracted into a stable procedure.
5. The model detects that an existing skill, script, reference, asset, or tool entry is insufficient for the real task or user need and should be extended.

## Core policy

1. Prefer durable accumulation over one-off completion.
2. Prefer improving an existing skill over creating a parallel replacement.
3. Rules govern behavior; skills encapsulate reusable execution.
4. Reusable trial-and-error must not remain only in the current conversation.

## Mandatory behavior

### A. When skill-related work appears

First determine:
- whether this should modify an existing skill or create a new one
- whether the right landing place is:
  - rule
  - skill
  - script
  - reference
  - asset

Make the smallest correct complete change set.

### B. Trial-and-error must be evaluated for promotion

The following must be considered promotable knowledge:
- actual errors and failure modes
- commands, tools, parameters, and fallback methods tried during the task
- the successful resolution path
- gaps between the current skill and the real task
- new trigger scenarios exposed by real user demand
- temporary multi-step command sequences created during the task

If the knowledge is likely to be useful again, persist it.

### C. New skills and structured skill changes must use the skill-creator template/process

When creating a new skill, or making a structured modification to an existing skill, use the skill-creator template/process so that the skill clearly states:
- trigger conditions
- task background
- intended scope
- inputs and outputs
- tools/connectors/scripts usage
- supporting references, assets, and scripts

Do not create ambiguous or under-specified skills.

### D. If an existing skill exposed the gap, improve that skill first

If the current task is already using an existing skill and the task reveals reusable fixes, missing capabilities, or repeated troubleshooting:
- default to upgrading that existing skill
- create a new skill only when the responsibility is clearly separate

Apply the same rules as for new skills:
- improve trigger conditions
- improve background/context
- add troubleshooting knowledge
- add or refine script entrypoints
- add validation steps
- update references and usage instructions

### E. Repeated command sequences must be promoted into scripts

When multiple commands or repeated file/repair steps are reusable:
- move them into `scripts/`
- make the script deterministic and single-purpose
- document the script in the owning skill
- avoid relying on future agents to manually reconstruct the same command sequence

### F. Communicate the abstraction when appropriate

When useful, briefly explain to the user:
- why this was promoted to a script or skill
- what repeated trial-and-error it avoids
- which trigger scenarios are now covered
- which additional real-world scenarios would help further refine it

Keep the explanation short and task-relevant.

### G. Installation is mandatory for new skills

If a new skill is created:
- install it through the repository’s `install_skills.py` workflow
- 
- ensure it lands in `~/.agents/skills`
- rely on the existing installer to distribute or link it to supported agents

If an existing skill is substantially upgraded and distribution needs refreshing, re-run the installer.

## Preferred landing zones

### Put in `SKILL.md`
- trigger conditions
- when to use the skill
- scope and constraints
- tool/script entrypoints
- high-level operating instructions

### Put in `scripts/`
- deterministic command sequences
- validations
- install/package/fix utilities
- conversions and repeatable repair flows

### Put in `references/`
- known errors and fixes
- path and environment caveats
- parameter notes
- successful and failed cases worth remembering

### Create a new skill only when
- the responsibility is clearly distinct from existing skills
- the capability cannot be cleanly merged into an existing skill
- the trigger/use case has independent reuse value

## Prohibitions

Do not:
- solve only the current instance and discard reusable knowledge
- create a near-duplicate skill when an existing one should be improved
- keep retyping reusable multi-command procedures
- modify scripts or references without updating the owning skill entrypoint
- create a new skill without using the skill-creator template/process
- create a new skill and forget to install it into `~/.agents/skills`
- package one-off noise as a permanent skill

## Decision order

1. Finish the current task.
2. Extract reusable knowledge from the task.
3. Prefer enhancing an existing skill.
4. Promote fixed multi-step procedures into scripts.
5. Promote cross-task reusable capabilities into skills.
6. Install every new skill through the unified `~/.agents/skills` entry.

## Output preference

When skill governance causes a change, summarize:
- what reusable knowledge was captured
- which skill was modified or created
- which scripts/references were added or changed
- why this abstraction is justified
- what future trigger scenarios would improve it further
