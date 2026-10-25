## Never break updates for deployed units (top rule)

A deployed unit can only update with the updater it already runs. If a change breaks that path,
no later release can reach the unit again without SSH or an SD card re-flash. Nothing may
break it, whatever else the change is for.

- The update path is a fixed contract with every deployed version: `raceflag/ota.py`, the
  `/api/update/*` routes and the page's update button, the GitHub release format (repo name,
  numeric `vX.Y.Z` tags, the `.tar.gz` asset and its top-level layout, `version.txt`),
  `.github/workflows/release.yml`, `raceflag.service`, and `config.json` compatibility.
- New Python dependencies may not get installed by an update (pip has no network inside
  `overlayroot-chroot` and its errors are ignored). New code must still start without them.
- New code must start with any older `config.json`; older code must tolerate a newer one.
- Any change that touches the update path must be called out to the owner.
- **Release gate: no release is ever tagged or published until an OTA update to it has been
  verified on a unit with overlayroot active**, every release, whatever it changes. The test
  unit runs the release it would update from (the current latest), overlayroot is confirmed
  active (`grep overlayroot /proc/mounts`), the update is installed through the page's Update
  button, and after a reboot the unit reports the new version and runs the new code. If any
  step fails, the release is not published. Report the result to the owner before tagging.
  One-time exception (owner, 2026-10-25): **v0.2.35** can't pass the Update-button step from
  v0.2.34, whose broken updater is what it fixes. Its gate is instead: the Windows SSH script
  updating a protected unit from v0.2.34 (done via raceflag-beta), the Update button from
  0.2.35 to a later release (done, 0.2.35 → 0.2.36 on raceflag-beta), and one Windows-script run
  against the main repo's real v0.2.35 release before customers are told. Every later release
  uses the normal gate.
- Never delete releases, rename the repo, or publish a non-numeric or pre-release tag as latest.

## Shared specs (../raceflag_specs)

RaceFlag also runs on an ESP32 (separate repo, separate Claude session). Behaviour is kept
in sync through the shared specs repo at ../raceflag_specs.

- Any user-visible change, including a bug fix that changes behaviour, must update in the
  same piece of work: the feature page in features/, contracts/api.md if any /api route
  changes, conformance/ cases if flag decisions change, and the Pi column in PARITY.md.
  Set the ESP32 columns of a changed feature to ❌ (or 🚧) so the ESP32 session sees it.
- When porting a feature from the ESP32, work from its spec and conformance cases, not
  from the ESP32 code.
- Never modify the ESP32 repo.
- Commit spec changes in raceflag_specs as their own commit, with a message naming the feature.

## Beta branch

- New features are committed to the `beta` branch, never directly to `main`. Spec,
  conformance, and documentation-only changes may go on `main`.
- A feature on `beta` is marked beta in the specs: 🧪 in the Pi column of PARITY.md, plus a
  `**Status:** beta on Pi` line (new feature) or a `> **Beta (Pi):**` callout (change to an
  existing feature). See "Beta features" in ../raceflag_specs/README.md.
- Promoting beta → main includes, in the same piece of work, flipping 🧪 → ✅ and removing
  the beta markers from the spec.
