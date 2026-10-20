# Project Maintenance

- Communicate with the user in Chinese.
- After each coherent code or behavior change, update README.md and CHANGELOG.md with usage, rationale, compatibility, and verification results.
- Record every development and optimization task in a Markdown development log under docs/development/. Include the request, baseline and environment, investigation and measured bottlenecks, decisions and implementation steps, before/after evidence, compatibility and limitations, test results and process cleanup, reproduction commands, and source commit references. Distinguish measurements from hypotheses and offline timings from observed GUI FPS; document failed or unavailable checks honestly. Update the log as work progresses, alongside README.md and CHANGELOG.md.
- Run the Blender regression suite before committing. Record failures or unavailable checks honestly.
- After every test run (including failures, timeouts, and cancellations), verify that all processes started for that test have exited. Track their process/session IDs and terminate any remaining test processes and their test-owned children before reporting completion. Never terminate processes by name alone or close unrelated user processes, manually edited Blender windows, or demonstration windows explicitly requested to remain open.
- Commit each completed change to Git with a descriptive message. Stage only task-related files; preserve unrelated user work.
- Report the commit id and relevant documentation to the user.
- Keep the Blender window open after generating or demonstrating the model. Preserve manual scene edits when reconnecting controls.
- Default design mode regenerates ray-aligned points after projector pose changes. Fixed-point mode models already-manufactured glass.
- Do not create a Git commit for every interactive Blender parameter edit. Save experiment scenes separately and record the source commit for reproducibility.
