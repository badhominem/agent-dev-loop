# Principles

Every agent reads this file first. Each principle has a fixed ID. Changing a
principle changes how agents behave, so change it on purpose and note why.

## P1 The approved picture is the contract
What is visible on the approved after-state is fixed. What is hidden inside a
component is the agent's call.
- Fixed, so stop and ask: scenarios, interfaces, module boundaries, table schemas, data flow.
- Free, so decide and log: anything inside a component that doesn't change how it looks from outside.

## P2 The behaviour index is the current truth
Each demand adds, changes or retires scenarios in `loop/behaviour/`. Before
approval, check new scenarios against the existing ones and ask when one
contradicts another: "This contradicts scenario X. Which one wins?"

## P3 Data first, pictures drawn from it
Indexes are stored as data. Every visual is rendered from them by `loop views`.
An edited visual is read back as input, never stored as the source.

## P4 Only uncertainty reaches a human
Decide the small things and flag assumptions. People review the flagged items.

## P5 A repeated question becomes a convention
When a decision has been made before, offer to record it so no agent asks again.

## P6 A demand's work stays isolated until it closes
The truth branch holds only what is true now, plus the intake queue. Each
claimed demand works in its own worktree and branch. Nothing reaches the truth
branch before close.

## P7 The builder hands over only what can't be measured
Hand over: the code, the opinion files for components you changed, and
`build.yaml` with your decisions, assumptions and status (done, or blocked with
one question). Everything measurable is measured by stage 3, never reported.

## P8 Review never edits code
Stage 3 checks, explains and reports. Problems go back to stage 2.

## P9 Tests and code come from different agents
The outside-test writer writes tests from the approved scenarios before any
code. The builder never writes or edits a test. Inside tests are optional (off
by default) and written after the build by a separate agent.
