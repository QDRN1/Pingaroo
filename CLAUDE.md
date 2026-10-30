# Pingaroo: Rules for Any AI Assistant Working in This Repo

1. **No massive changes without asking.** Before any of the following, stop and ask
   Collin, then wait for a yes: new features, rewrites or redesigns of a screen or
   module, renames, changes to app signing, package id, permissions, or anything
   that would require uninstalling the app or reinstalling the Pi service, deleting
   or moving logs or data, and changes to the update mechanism.
2. **A bug report means fix that bug.** Scope the change to the reported problem.
   Do not bundle unrelated improvements into the same build.
3. **Never change the Android signature or package id.** Builds must install over
   the existing app. Debug signing is the accepted state.
4. **Secrets stay out of git.** No passwords, keystores, tokens, IMEI, IMSI or ICCID
   in files or commit messages. Redact them in sample data and docs.
5. **Commands for the Pi or the phone are complete.** Absolute paths, no `~` inside
   `sudo`, no placeholders unless unavoidable, and say when a command drops SSH.
6. **One build per round.** Fix, build, publish `android/releases/version.json`
   with the APK, send the file, then stop and wait for feedback.
7. **Say what was verified on hardware and what was not.** Nothing built here runs
   on a device; state that plainly instead of implying it works.
