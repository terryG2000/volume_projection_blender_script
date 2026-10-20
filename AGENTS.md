# Project Maintenance

- Communicate with the user in Chinese.
- After each coherent code or behavior change, update README.md and CHANGELOG.md with usage, rationale, compatibility, and verification results.
- Run the Blender regression suite before committing. Record failures or unavailable checks honestly.
- After every test run (including failures, timeouts, and cancellations), verify that all processes started for that test have exited. Track their process/session IDs and terminate any remaining test processes and their test-owned children before reporting completion. Never terminate processes by name alone or close unrelated user processes, manually edited Blender windows, or demonstration windows explicitly requested to remain open.
- Commit each completed change to Git with a descriptive message. Stage only task-related files; preserve unrelated user work.
- Report the commit id and relevant documentation to the user.
- Keep the Blender window open after generating or demonstrating the model. Preserve manual scene edits when reconnecting controls.
- Default design mode regenerates ray-aligned points after projector pose changes. Fixed-point mode models already-manufactured glass.
- Do not create a Git commit for every interactive Blender parameter edit. Save experiment scenes separately and record the source commit for reproducibility.
