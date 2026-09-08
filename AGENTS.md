# Project Maintenance

- Communicate with the user in Chinese.
- After each coherent code or behavior change, update README.md and CHANGELOG.md with usage, rationale, compatibility, and verification results.
- Run the Blender regression suite before committing. Record failures or unavailable checks honestly.
- Commit each completed change to Git with a descriptive message. Stage only task-related files; preserve unrelated user work.
- Report the commit id and relevant documentation to the user.
- Keep the Blender window open after generating or demonstrating the model. Preserve manual scene edits when reconnecting controls.
- Default design mode regenerates ray-aligned points after projector pose changes. Fixed-point mode models already-manufactured glass.
- Do not create a Git commit for every interactive Blender parameter edit. Save experiment scenes separately and record the source commit for reproducibility.
